# SPDX-License-Identifier: GPL-2.0-or-later
"""Read generated ownership and actual archive bytes; never execute build tools.

The supported grammar is the sealed projects' CMake Ninja Release output and
GNU Make/libtool verbose output. Unsupported evidence fails closed. A PIE
executable is outside the PIC gate only when it owns the object exclusively;
all other compile rows, including additional object libraries, require PIC.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
from io_utils import HERE, sha, write_json
from metadata import ARCHIVES
from source_tree import verify_tree


def digest(data):
    return hashlib.sha256(data).hexdigest()


def owned(value, base, boundary, must_exist=True):
    if not isinstance(value, str) or not value or any(c in value for c in '\x00\n\r'):
        raise ValueError('Invalid closure path')
    file = Path(value)
    if not file.is_absolute():
        file = base / file
    # Resolve existing symlinks, but never allow an output to escape its owner.
    resolved = file.resolve(strict=must_exist)
    if not resolved.is_relative_to(boundary.resolve()) or (must_exist and not resolved.is_file()):
        raise ValueError('Escaped/missing closure path: ' + value)
    return resolved


def file_row(file, boundary):
    return {'path': file.relative_to(boundary.resolve()).as_posix(),
            'sha256': sha(file), 'size': file.stat().st_size}


def archive_members(file):
    data = file.read_bytes()
    if data[:8] != b'!<arch>\n':
        raise ValueError('Regular static archive required (no thin archive)')
    at, names, rows = 8, None, []
    while at < len(data):
        header = data[at:at + 60]
        if len(header) != 60 or header[58:] != b'`\n' or not header[48:58].strip().isdigit():
            raise ValueError('Malformed archive header')
        size = int(header[48:58]); at += 60
        payload = data[at:at + size]
        if len(payload) != size:
            raise ValueError('Truncated archive member')
        at += size
        if size % 2:
            if data[at:at + 1] != b'\n':
                raise ValueError('Invalid archive padding')
            at += 1
        name = header[:16].decode('utf-8').rstrip()
        if name in ('//', '/', '/SYM64/') and payload.startswith(b'\x7fELF'):
            raise ValueError('ELF object hidden in archive metadata')
        if name == '//':
            if names is not None:
                raise ValueError('Duplicate archive name table')
            names = payload
            continue
        if name in ('/', '/SYM64/'):
            continue
        if name.startswith('#1/'):
            length = int(name[3:])
            if length > size:
                raise ValueError('Invalid BSD member name')
            name = payload[:length].rstrip(b'\0').decode('utf-8'); payload = payload[length:]
        elif name.startswith('/'):
            if names is None or not name[1:].isdigit():
                raise ValueError('Unknown archive name reference')
            offset = int(name[1:]); end = names.find(b'/\n', offset)
            if offset >= len(names) or end < 0 or (offset and names[offset - 1:offset] != b'\n'):
                raise ValueError('Invalid archive name offset')
            name = names[offset:end].decode('utf-8')
        else:
            name = name.removesuffix('/')
        if name in ('__.SYMDEF', '__.SYMDEF SORTED'):
            if payload.startswith(b'\x7fELF'):
                raise ValueError('ELF object hidden in archive metadata')
            continue
        if not name or Path(name).name != name or not name.endswith('.o'):
            raise ValueError('Unknown static archive member: ' + name)
        rows.append({'member': name, 'sha256': digest(payload), 'size': len(payload)})
    if not rows:
        raise ValueError('Empty archive closure')
    return rows


def pair(built, installed, objects):
    if sha(built) != sha(installed):
        raise ValueError('Built/installed archive bytes differ')
    expected = [{'member': name, 'sha256': sha(file), 'size': file.stat().st_size}
                for name, file in objects]
    if archive_members(built) != expected:
        raise ValueError('Actual archive members differ from complete object closure')
    return {'built': str(built), 'installed': str(installed), 'sha256': sha(built),
            'members': expected}


def argv(text):
    words = shlex.split(text)
    if not words or any(w.startswith('@') or w in (';', '&&', '||', '|', '>', '<', '#', '--', '-Xclang', '-Xpreprocessor', '-Xlinker', '-Xassembler')
                        or any(c in w for c in '$`;&|<>#\n\r') for w in words):
        raise ValueError('Unsupported compile command indirection')
    return words


def require_pic(words):
    operands = {'-o', '-c', '-I', '-isystem', '-iquote', '-include', '-imacros', '-D', '-U',
                '-MF', '-MT', '-MQ', '-x', '-isysroot', '--sysroot', '-resource-dir', '-target'}
    options, consume = [], False
    for word in words[1:]:
        if consume:
            consume = False
        elif word in operands:
            consume = True
        else:
            options.append(word)
    if '-fPIC' not in options or any(w in ('-fpic', '-fPIE', '-fpie', '-fno-PIC', '-fno-pic', '-fno-PIE', '-fno-pie')
                                   or w.startswith(('-Wp,', '-Wa,', '-Xclang=', '-Xpreprocessor=', '--config', '-fplugin'))
                                   for w in options):
        raise ValueError('Library object lacks unambiguous actual -fPIC')


def option(words, flag):
    if words.count(flag) != 1 or words.index(flag) + 1 == len(words):
        raise ValueError('Missing/duplicate compile operand: ' + flag)
    return words[words.index(flag) + 1]


def expand(text, variables, missing=()):
    def replace(match):
        key = match.group(1) or match.group(2)
        if key in variables:
            return variables[key]
        if key in missing:
            return ''
        raise ValueError('Unknown Ninja variable: ' + key)
    # Literal dollar is protected before variable substitution.
    text = text.replace('$$', '\x01')
    text = re.sub(r'\$\{([A-Za-z0-9_]+)\}|\$([A-Za-z0-9_]+)', replace, text)
    return text.replace('\x01', '$').replace('$ ', ' ').replace('$:', ':')


def ninja_tokens(text, variables):
    # Preserve Ninja's escaped whitespace/colon before separating grammar tokens.
    text = text.replace('$$', '\x01').replace('$ ', '\x02').replace('$:', '\x03')
    text = re.sub(r'(\|\||[:|])', r' \1 ', text)
    return [expand(w, variables).replace('\x01', '$').replace('\x02', ' ').replace('\x03', ':')
            for w in text.split()]


def ninja(build):
    variables, rules, edges, graph_files = {}, {}, [], []
    def parse(file, rule_file=False):
        graph_files.append(file_row(file, build))
        block = None
        text = re.sub(r'\$\n[ \t]*', '', file.read_text())
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if line.startswith((' ', '\t')):
                if block is not None and '=' in line:
                    key, value = line.strip().split('=', 1)
                    block[key.strip()] = value.strip()
                continue
            block = None
            if line.startswith('include '):
                included = owned(expand(line[8:], variables), build, build)
                if included != build / 'CMakeFiles/rules.ninja' or rule_file:
                    raise ValueError('Unsupported Ninja include')
                parse(included, True)
            elif line.startswith('subninja '):
                raise ValueError('Unsupported Ninja subgraph')
            elif line.startswith('rule '):
                name = line[5:]
                if name in rules:
                    raise ValueError('Duplicate Ninja rule')
                block = {}; rules[name] = block
            elif line.startswith('build '):
                words = ninja_tokens(line[6:], variables)
                if words.count(':') != 1:
                    raise ValueError('Unsupported Ninja build statement')
                sep = words.index(':'); outputs = words[:sep]
                implicit_outputs = []
                if '|' in outputs:
                    implicit_outputs = outputs[outputs.index('|') + 1:]
                    outputs = outputs[:outputs.index('|')]
                inputs = words[sep + 2:]
                end = next((i for i, w in enumerate(inputs) if w in ('|', '||')), len(inputs))
                edge = {'outputs': outputs, 'implicit_outputs': implicit_outputs, 'rule': words[sep + 1], 'inputs': inputs[:end],
                        'dependencies': [w for w in inputs[end:] if w not in ('|', '||')], 'bindings': {}}
                edges.append(edge); block = edge['bindings']
            elif '=' in line:
                key, value = line.split('=', 1); variables[key.strip()] = expand(value.strip(), variables)
        return
    parse(build / 'build.ninja')
    return edges, rules, variables, graph_files


def cmake_closure(args, root, name):
    build = (root / 'build' / name).resolve(); source = (root / 'sources' / name).resolve()
    database = build / 'compile_commands.json'
    entries = json.loads(database.read_text())
    if not entries:
        raise ValueError('Empty compile database')
    edges, rules, globals_, graph_files = ninja(build)
    compilers, libraries, executables, object_libraries, generated_headers = {}, [], [], {}, []
    for edge in edges:
        rule = edge['rule']
        if edge['implicit_outputs']:
            explicit = [owned(p, build, build, must_exist=False) for p in edge['outputs']]
            implicit = [owned(p, build, build, must_exist=False) for p in edge['implicit_outputs']]
            names = edge['outputs'] + edge['implicit_outputs']
            canonical = all(Path(p).as_posix() == p and '..' not in Path(p).parts for p in names)
            if rule != 'CUSTOM_COMMAND' or len(explicit) != 1 or len(implicit) != 1 or Path(edge['outputs'][0]).is_absolute() or not Path(edge['implicit_outputs'][0]).is_absolute() or not canonical or implicit[0] != explicit[0]:
                raise ValueError('Unsupported implicit target output')
        known = re.fullmatch(r'(C|CXX)_(COMPILER__.+_unscanned|STATIC_LIBRARY_LINKER__.+|EXECUTABLE_LINKER__.+)_Release', rule)
        if rule != 'phony':
            for output in edge['outputs']:
                owned(output, build, build, must_exist=False)
                if output.endswith(('.a', '.o')) and not known:
                    raise ValueError('Unknown archive/object producing target')
                if rule == 'CUSTOM_COMMAND' and output.endswith('.gen.h'):
                    generated_headers.append(file_row(owned(output, build, build), root))
        if re.fullmatch(r'(C|CXX)_COMPILER__.+_unscanned_Release', rule):
            if len(edge['outputs']) != 1 or len(edge['inputs']) != 1:
                raise ValueError('Unsupported compiler ownership')
            out = owned(edge['outputs'][0], build, build)
            if out in compilers:
                raise ValueError('Duplicate object graph output')
            compilers[out] = edge
        elif re.fullmatch(r'(C|CXX)_STATIC_LIBRARY_LINKER__.+_Release', rule):
            libraries.append(edge)
        elif re.fullmatch(r'(C|CXX)_EXECUTABLE_LINKER__.+_Release', rule):
            executables.append(edge)
        elif rule == 'phony' and edge['inputs'] and all(p.endswith('.o') for p in edge['inputs']):
            for out in edge['outputs']:
                if Path(out).name.endswith('_obj'):
                    for p in edge['inputs']:
                        obj = owned(p, build, build)
                        object_libraries.setdefault(obj, []).append(out)
    library_owners, executable_owners, archive_rows = {}, {}, []
    if len(libraries) != len(ARCHIVES[name]):
        raise ValueError('Unexpected static library target set')
    for edge in libraries + executables:
        if len(edge['outputs']) != 1 or not edge['inputs'] or len(set(edge['inputs'])) != len(edge['inputs']):
            raise ValueError('Missing/duplicate link ownership')
        out = owned(edge['outputs'][0], build, build)
        bindings = edge['bindings']; command = rules.get(edge['rule'], {}).get('command', '')
        if owned(bindings.get('TARGET_FILE', ''), build, build) != out or (bindings.get('LINK_FLAGS', '') and edge in libraries):
            raise ValueError('Misidentified target output or archive flags')
        if bindings.get('PRE_LINK') != ':' or any(bindings.get(k, '') for k in ('LINK_PATH', 'LINK_LIBRARIES')):
            raise ValueError('Unsupported hidden link action/input')
        post = shlex.split(expand(bindings.get('POST_BUILD', ''), globals_))
        if post != [':']:
            expected_post = ['cd', str(build / 'src/tbb'), '&&', str(args.sdk_root / 'build-tools/cmake/bin/cmake'),
                             '-DBINARY_DIR=' + str(build), '-DSOURCE_DIR=' + str(source), '-DBIN_PATH=' + str(out.parent),
                             '-DVARS_TEMPLATE=linux/env/vars.sh.in', '-DVARS_NAME=vars.sh', '-DTBB_INSTALL_VARS=OFF',
                             '-DTBB_CMAKE_INSTALL_LIBDIR=lib', '-P', str(source / 'integration/cmake/generate_vars.cmake')]
            if name != 'tbb' or out.name != 'libtbb.a' or post != expected_post:
                raise ValueError('Unsupported hidden post-link action')
        if any(p.endswith(('.o', '.a')) for p in edge['dependencies']):
            raise ValueError('Hidden implicit library/object input')
        objects = [owned(p, build, build) for p in edge['inputs']]
        if any(p not in compilers for p in objects):
            raise ValueError('Uncompiled/unknown link object')
        if edge in libraries:
            archiver = re.fullmatch(r'\$PRE_LINK && (.+) -E rm -f \$TARGET_FILE && (.+) qc \$TARGET_FILE \$LINK_FLAGS \$in && (.+) \$TARGET_FILE && \$POST_BUILD', command)
            if out.name not in ARCHIVES[name] or not archiver:
                raise ValueError('Misidentified static archive rule')
            for text, expected in zip(archiver.groups(), [args.sdk_root / 'build-tools/cmake/bin/cmake',
                                                       args.sdk_root / 'llvm/bin/llvm-ar', args.sdk_root / 'llvm/bin/llvm-ranlib']):
                program = argv(expand(text, globals_))
                if len(program) != 1 or Path(program[0]).resolve() != expected.resolve():
                    raise ValueError('Misidentified static archive tool ownership')
            if any(r['installed'] == str(args.prefix / 'lib' / out.name) for r in archive_rows):
                raise ValueError('Duplicate installed archive owner')
            archive_rows.append(pair(out, args.prefix / 'lib' / out.name, [(p.name, p) for p in objects]))
            owners = library_owners
        else:
            if argv(expand(bindings.get('FLAGS', ''), globals_)) != ['-D__MUSL__', '-O3', '-DNDEBUG'] or argv(expand(bindings.get('LINK_FLAGS', ''), globals_)) != ['-Wl,--threads=2']:
                raise ValueError('Unsupported executable link flags/input')
            linker = re.fullmatch(r'\$PRE_LINK && (.+) \$FLAGS \$LINK_FLAGS \$in -o \$TARGET_FILE \$LINK_PATH \$LINK_LIBRARIES && \$POST_BUILD', command)
            if not linker or bindings.get('PRE_LINK') != ':' or bindings.get('POST_BUILD') != ':':
                raise ValueError('Misidentified executable rule')
            driver = argv(expand(linker[1], globals_))
            if len(driver) != 1 or Path(driver[0]).resolve() != (root / 'toolchain/clang20-cxx').resolve():
                raise ValueError('Misidentified executable linker driver')
            owners = executable_owners
        for p in objects:
            owners.setdefault(p, []).append({'target': str(out), 'rule': edge['rule']})
    rows, seen = [], set()
    for entry in entries:
        if Path(entry['directory']).resolve() != build:
            raise ValueError('Compile directory outside generated build owner')
        words = entry.get('arguments')
        if words is None:
            words = argv(entry['command'])
        else:
            words = argv(shlex.join(words))
            if 'command' in entry and words != argv(entry['command']):
                raise ValueError('Conflicting command/arguments evidence')
        out = owned(option(words, '-o'), build, build)
        if out in seen or out not in compilers:
            raise ValueError('Duplicate/unknown compile output')
        seen.add(out)
        if 'output' in entry and owned(entry['output'], build, build) != out:
            raise ValueError('Compile output field mismatch')
        src = owned(option(words, '-c'), build, source)
        if owned(entry['file'], build, source) != src:
            raise ValueError('Compile source field mismatch')
        edge = compilers[out]
        if owned(edge['inputs'][0], build, source) != src:
            raise ValueError('Graph/database source mismatch')
        variables = dict(globals_); variables.update(edge['bindings'])
        variables.update(out=shlex.quote(edge['outputs'][0]), in_=shlex.quote(edge['inputs'][0]))
        variables['in'] = variables.pop('in_')
        template = rules.get(edge['rule'], {}).get('command', '')
        if not re.fullmatch(r'\$\{LAUNCHER\}\$\{CODE_CHECK\}.+ \$DEFINES \$INCLUDES \$FLAGS -MD -MT \$out -MF \$DEP_FILE -o \$out -c \$in', template) or any(variables.get(k, '') for k in ('LAUNCHER', 'CODE_CHECK')):
            raise ValueError('Unsupported compiler rule/action')
        generated = argv(expand(template, variables, missing=('LAUNCHER', 'CODE_CHECK', 'INCLUDES')))
        for flag in ('-MD', '-MT', '-MF'):
            if flag in generated:
                i = generated.index(flag); del generated[i:i + (1 if flag == '-MD' else 2)]
        driver = root / 'toolchain' / ('clang20-cxx' if edge['rule'].startswith('CXX_') else 'clang20-c')
        if generated != words or Path(words[0]).resolve() != driver.resolve():
            raise ValueError('Graph/database compile command or driver mismatch')
        libraries_ = library_owners.get(out, [])
        exes = executable_owners.get(out, [])
        target = edge['rule'].split('_COMPILER__', 1)[1].removesuffix('_unscanned_Release')
        object_owners = object_libraries.get(out, [])
        if any(Path(p).name != target for p in object_owners):
            raise ValueError('Misidentified/conflicting object library owner')
        if libraries_ and exes:
            raise ValueError('Executable object injected into static library')
        if libraries_:
            require_pic(words); role = 'static-library-object'
        elif exes:
            if len(exes) != 1 or object_owners or not exes[0]['rule'].endswith('__' + target + '_Release'):
                raise ValueError('Ambiguous executable object owner')
            role = 'exclusive-executable-object'
        elif object_owners:
            require_pic(words); role = 'additional-PIC-object-library'
        else:
            raise ValueError('Unowned compile row')
        rows.append({'object': file_row(out, root), 'source': file_row(src, root),
                     'command_sha256': digest(shlex.join(words).encode()), 'role': role,
                     'library_owners': libraries_, 'executable_owners': exes, 'object_library_owners': object_owners})
    if seen != set(compilers):
        raise ValueError('Missing library/graph compile entry')
    return {'name': name, 'units': len(rows), 'sha256': sha(database), 'graph': graph_files,
            'archives': archive_rows, 'objects': rows, 'generated_headers': generated_headers}


def gmp_closure(args, root, log):
    build = (root / 'build/gmp').resolve()
    text = log.read_text(); header = json.loads(text.splitlines()[0])
    if Path(header['cwd']).resolve() != build or not text.rstrip().endswith('exit=0'):
        raise ValueError('Missing successful GMP build provenance')
    directories, compiled, aliases, links = {}, {}, {}, {}
    cwd = build
    for line in text.splitlines()[1:]:
        match = re.fullmatch(r"make\[(\d+)\]: (Entering|Leaving) directory ['`](.*)'", line)
        if match:
            depth = int(match[1]); directory = Path(match[3]).resolve()
            if not directory.is_relative_to(build):
                raise ValueError('Escaped GNU Make directory')
            if match[2] == 'Entering':
                directories[depth] = directory
            else:
                if directories.pop(depth, None) != directory:
                    raise ValueError('Ambiguous GNU Make directory')
            cwd = directories[max(directories)] if directories else build
        elif line.startswith('libtool: compile:'):
            words = argv(line.split('libtool: compile:', 1)[1]); require_pic(words)
            if Path(words[0]).resolve() not in [(root / 'toolchain/clang20-c').resolve(), (root / 'toolchain/clang20-cxx').resolve()]:
                raise ValueError('Misidentified GMP compile driver')
            out = owned(option(words, '-o'), cwd, build)
            src = owned(option(words, '-c'), cwd, root)
            if out in compiled:
                raise ValueError('Duplicate GMP compile entry')
            compiled[out] = {'object': file_row(out, root), 'source': file_row(src, root),
                             'command_sha256': digest(shlex.join(words).encode())}
        elif line.startswith('libtool: link: ln '):
            words = shlex.split(line[len('libtool: link: '):])
            if len(words) != 7 or words[0] != 'ln' or words[3:5] != ['||', 'cp'] or words[1:3] != words[5:7]:
                raise ValueError('Unknown libtool object copy')
            src = owned(words[1], cwd, build)
            dest = owned(words[2], cwd, build, must_exist=False)
            if dest in aliases or src not in compiled:
                raise ValueError('Uncompiled/duplicate libtool renamed object')
            aliases[dest] = src
        elif line.startswith('libtool: link:') and '/llvm-ar ' in line:
            words = argv(line.split('libtool: link:', 1)[1])
            if len(words) < 4 or Path(words[0]).resolve() != (args.sdk_root / 'llvm/bin/llvm-ar').resolve() or words[1] != 'cq':
                raise ValueError('Unknown GMP archive ownership')
            out = owned(words[2], cwd, build)
            if out in links:
                raise ValueError('Duplicate GMP archive output')
            objects = []
            for token in words[3:]:
                obj = owned(token, cwd, build, must_exist=False)
                original = aliases.get(obj, obj)
                if original not in compiled:
                    raise ValueError('Missing GMP library compile entry')
                objects.append((obj.name, original))
            if len({p for _, p in objects}) != len(objects):
                raise ValueError('Duplicate GMP archive object')
            links[out] = objects
    if not compiled or directories:
        raise ValueError('Incomplete GMP build output')
    archives = []
    for leaf in ARCHIVES['gmp']:
        built = build / '.libs' / leaf
        if built not in links:
            raise ValueError('Missing GMP installed archive ownership')
        archives.append(pair(built, args.prefix / 'lib' / leaf, links[built]))
    return {'name': 'gmp', 'commands': len(compiled), 'log': file_row(log, root),
            'log_sha256': sha(log), 'archives': archives,
            'objects': [compiled[p] for p in sorted(compiled)]}


def inspect(args, root, name, log=None):
    verify_tree(root / 'sources' / name, name, patched=True)
    result = gmp_closure(args, root, log) if name == 'gmp' else cmake_closure(args, root, name)
    result['input_lock_sha256'] = sha(HERE / 'inputs.lock.json')
    # Existing source inventory plus verify_tree binds every upstream source byte.
    result['sources_lock_sha256'] = sha(HERE / 'sources.lock.json')
    result['build_headers'] = [file_row(owned(str(p), root, root), root)
                               for p in sorted((root / 'build' / name).rglob('*.h')) if p.is_file()]
    return result


def capture(args, root, name, logs):
    log = None
    if name == 'gmp':
        matches = list(logs.glob('gmp-build-*.log'))
        if len(matches) != 1:
            raise ValueError('Ambiguous GMP build log')
        log = matches[0]
    result = inspect(args, root, name, log)
    write_json(root / 'compile-closure' / (name + '.json'), result)


def verify(args, root, name):
    file = root / 'compile-closure' / (name + '.json')
    saved = json.loads(file.read_text())
    log = owned(saved['log']['path'], root, root) if name == 'gmp' else None
    actual = inspect(args, root, name, log)
    if actual != saved:
        raise ValueError('Stale compile closure graph/archive/object/source/input bytes: ' + name)
    actual['closure_receipt_sha256'] = sha(file)
    return actual
