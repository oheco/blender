# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind structured actual operations to source/object/output bytes without executing them."""
from pathlib import Path
import hashlib
import re
import shlex
from common import beneath, digest, explicit_path, load, require

# Actual CMake translation units; file-command code is included in creator.cc.
# Keep this contract independent of receipt-provided headers or filename suffixes.
CORE_IMPLEMENTATION_HEADERS = {
    'source/creator/creator.cc': (
        'source/creator/creator_intern.h',
        'source/creator/creator_ohos.h',
        'source/creator/creator_ohos_files.h',
        'source/creator/creator_ohos_files_impl.hh',
        'source/creator/creator_ohos_files_path.hh',
        'intern/ghost/GHOST_OHOSHost.h',
    ),
    'intern/ghost/intern/GHOST_OHOSHost.cc': ('intern/ghost/GHOST_OHOSHost.h',),
    'intern/ghost/intern/GHOST_OHOSNative.cc': (
        'intern/ghost/GHOST_OHOSNative.h', 'intern/ghost/GHOST_OHOSHost.h'),
    'intern/ghost/intern/GHOST_OHOSEngine.cc': (
        'intern/ghost/GHOST_OHOSEngine.h', 'intern/ghost/GHOST_OHOSHost.h'),
}
CORE_SOURCE_BINDINGS = (set(CORE_IMPLEMENTATION_HEADERS) |
                        {header for headers in CORE_IMPLEMENTATION_HEADERS.values() for header in headers} |
                        {'source/creator/ohos/core.exports.map', 'source/creator/CMakeLists.txt', 'intern/ghost/CMakeLists.txt'})

def implementation_headers(name):
    require(name in CORE_IMPLEMENTATION_HEADERS, 'Unknown/phantom core translation unit: ' + name)
    return CORE_IMPLEMENTATION_HEADERS[name]

def resolve(cwd, value):
    require(isinstance(value, str) and value and not any(ord(c) < 32 for c in value), 'Invalid operation path')
    return (cwd / value).resolve()

def argument(argv, option):
    values = []
    for index, token in enumerate(argv):
        if token == option:
            require(index + 1 < len(argv), 'Missing operation option value')
            values.append(argv[index + 1])
        elif option == '-o' and token.startswith('-o') and len(token) > 2:
            values.append(token[2:])
    require(len(values) == 1, 'Exactly one actual ' + option + ' argument required')
    return values[0]

def expanded(argv, cwd, records=()):
    bound = {}
    for row in records:
        path = explicit_path(row['path'], 'actual response file')
        require(digest(path) == row['sha256'], 'Response file changed')
        bound[path] = path.read_text()
    result = []
    for token in argv:
        require(isinstance(token, str) and token and '\n' not in token and '\x00' not in token, 'Invalid structured argv')
        if token.startswith('@'):
            path = resolve(cwd, token[1:])
            require(path in bound, 'Opaque/unbound command response file')
            parsed = shlex.split(bound[path])
            require(not any(value.startswith('@') for value in parsed), 'Nested opaque response file rejected')
            result.extend(parsed)
        else:
            result.append(token)
    return result

def operation(row, tool, output):
    file = explicit_path(row['path'], 'actual structured operation')
    require(digest(file) == row['sha256'], 'Structured operation bytes changed')
    data = load(file)
    cwd = explicit_path(data['cwd'], 'actual operation cwd')
    require(cwd.is_dir() and isinstance(data['argv'], list) and data['argv'], 'Actual structured cwd/argv required')
    argv = expanded(data['argv'], cwd, data.get('response_files', []))
    require(resolve(cwd, argv[0]) == tool.resolve(), 'Actual operation tool differs from selected evidence')
    if output is not None:
        require(resolve(cwd, data['output']) == output.resolve(), 'Operation belongs to a different output')
    return data, cwd, argv

def signing_roles(data, cwd, argv, artifact):
    # Actual frozen native launcher uses binary-sign-tool sign -inFile/-outFile.
    require(len(argv) > 1 and argv[1] == 'sign', 'Unsupported native signer command form')
    actual_input = resolve(cwd, argument(argv, '-inFile'))
    actual_output = resolve(cwd, argument(argv, '-outFile'))
    require(actual_input == resolve(cwd, data['input']) and actual_output == resolve(cwd, data['output']),
            'Native signer input/output roles differ from structured operation')
    if actual_input != artifact.resolve():
        require(actual_input.is_file() and digest(actual_input) == data['input_sha256'], 'Native signer original input bytes not bound')
    if actual_output != artifact.resolve():
        publication = data['publication']
        require(publication.get('operation') in ('rename', 'copy_exact') and resolve(cwd, publication['source']) == actual_output and
                resolve(cwd, publication['target']) == artifact.resolve() and publication['sha256'] == digest(artifact),
                'Final signer publication/clone bytes not bound')
        if actual_output.exists():
            require(digest(actual_output) == publication['sha256'], 'Signer intermediate final bytes changed')
    return {'input_role_bound': True, 'output_role_bound': True}

def signed_extra(receipt, artifact, selected_signer_sha256):
    tool = explicit_path(receipt['signer']['path'], 'actual extra signer')
    require(digest(tool) == receipt['signer']['sha256'] == selected_signer_sha256, 'Extra signer differs from selected prerequisite')
    data, cwd, argv = operation(receipt['sign_command'], tool, None)
    signing_roles(data, cwd, argv, artifact)
    return {'sign_command_sha256': receipt['sign_command']['sha256'], 'signer_sha256': selected_signer_sha256}

def linked_signer_input(sign, cwd, linker_output):
    require(resolve(cwd, sign['input']) == linker_output.resolve(), 'Core/host signer input is not the actual recorded linker output')

def signed_operations(receipt, artifact):
    compiler = explicit_path(receipt['compiler']['path'], 'actual compiler')
    signer = explicit_path(receipt['signer']['path'], 'actual signer')
    link, cwd, argv = operation(receipt['link_command'], compiler, artifact)
    require(resolve(cwd, argument(argv, '-o')) == artifact.resolve(), 'Actual link -o is not final selected ELF')
    records = link.get('objects')
    require(isinstance(records, list) and records, 'Actual linked object records required')
    linked = {resolve(cwd, token) for token in argv if token.endswith(('.o', '.obj', '.a')) and not token.startswith('-')}
    recorded = set()
    for row in records:
        file = explicit_path(row['path'], 'actual linked object/archive')
        require(file in linked and digest(file) == row['sha256'] and file not in recorded, 'Linked object/archive bytes differ or unbound')
        recorded.add(file)
    require(recorded == linked, 'Unrecorded actual linked objects/archives')
    require(resolve(cwd, link['output']) == artifact.resolve(), 'Actual linked output mismatch')
    sign, sign_cwd, sign_argv = operation(receipt['sign_command'], signer, None)
    linked_signer_input(sign, sign_cwd, resolve(cwd, link['output']))
    signing_roles(sign, sign_cwd, sign_argv, artifact)
    return {'link_command_sha256': receipt['link_command']['sha256'], 'sign_command_sha256': receipt['sign_command']['sha256'],
            'linked_objects': [{'name': Path(row['path']).name, 'sha256': row['sha256']} for row in records],
            'final_output_sha256': receipt['signed_sha256']}, link, cwd, argv

def archive_contains(archive, expected_sha):
    # Ordinary ar only; thin/opaque archives fail closed. Read, never extract.
    with archive.open('rb') as stream:
        require(stream.read(8) == b'!<arch>\n', 'Opaque/thin archive needs explicit real member audit')
        while True:
            header = stream.read(60)
            if not header: return False
            require(len(header) == 60 and header[58:] == b'`\n', 'Malformed linked archive')
            size = int(header[48:58].decode('ascii').strip())
            payload = size
            if header[:16].decode('ascii').strip().startswith('#1/'):
                name_size = int(header[:16].decode('ascii').strip()[3:])
                require(name_size <= size, 'Malformed BSD ar member name')
                stream.read(name_size); payload -= name_size
            hash_value = hashlib.sha256()
            while payload:
                part = stream.read(min(payload, 1024 * 1024))
                require(part, 'Truncated linked archive member')
                hash_value.update(part); payload -= len(part)
            if size & 1: stream.read(1)
            if hash_value.hexdigest() == expected_sha: return True

def make_dependency_rule(line):
    # Make depfile escaping differs from shell quoting: apostrophes are literal,
    # spaces/#/:/backslashes can be backslash escaped and $$ means literal $.
    targets, dependencies, word = [], [], []
    output, separator, index = targets, False, 0
    def finish():
        if word:
            output.append(''.join(word)); word.clear()
    while index < len(line):
        char = line[index]; index += 1
        if char == '\\':
            require(index < len(line), 'Truncated Make dependency escape')
            word.append(line[index]); index += 1
        elif char == '$':
            require(index < len(line) and line[index] == '$', 'Unbound Make variable in dependency evidence')
            word.append('$'); index += 1
        elif char == '#':
            break
        elif char == ':':
            require(not separator, 'Multiple unescaped dependency-rule separators')
            finish(); output = dependencies; separator = True
        elif char.isspace():
            finish()
        else:
            word.append(char)
    finish()
    require(separator and targets, 'Actual compiler dependency rule required')
    return targets, dependencies

def dependency_paths(text, cwd, obj, format_name):
    if format_name == 'make-depfile':
        lines = [line for line in text.replace('\\\n', ' ').splitlines() if line.strip() and not line.lstrip().startswith('#')]
        require(lines, 'Empty actual compiler depfile')
        rules = [make_dependency_rule(line) for line in lines]
        targets, values = rules[0]
        require(obj.resolve() in {resolve(cwd, value) for value in targets}, 'Depfile belongs to another object')
        require(all(not dependencies for _, dependencies in rules[1:]), 'Other nonempty object rules cannot supply this object dependencies')
        return {resolve(cwd, value) for value in values}
    require(format_name == 'ninja-deps', 'Unknown core dependency evidence format')
    lines = text.splitlines()
    require(lines, 'Empty Ninja dependency output')
    match = re.fullmatch(r'(.+): #deps ([0-9]+), deps mtime [0-9]+ \(VALID\)', lines[0])
    require(match and resolve(cwd, match.group(1)) == obj.resolve(), 'Ninja deps stale or belongs to another object')
    values = [line[4:] for line in lines[1:] if line]
    require(all(line.startswith('    ') for line in lines[1:] if line) and len(values) == int(match.group(2)),
            'Ninja deps truncated/multiple object output')
    return {resolve(cwd, value) for value in values}

def required_dependency_paths(name, source, dependencies):
    required = {beneath(source, value).resolve(strict=True) for value in (name, *implementation_headers(name))}
    missing = required - dependencies
    require(not missing, 'Core object missing required actual API/implementation dependencies: ' + ', '.join(sorted(map(str, missing))))
    return required

def linked_implementation_object(obj, expected_sha, linked, archive_value=None):
    if obj in linked:
        require(linked[obj] == expected_sha, 'Core linked implementation object hash differs')
        return
    require(archive_value, 'Core implementation output is not an actual linked object/archive member')
    archive = explicit_path(archive_value, 'actual linked core implementation archive')
    require(archive in linked and digest(archive) == linked[archive] and archive_contains(archive, expected_sha),
            'Core implementation object is absent from actual hashed linked archive')

def core_implementations(receipt, source):
    required = CORE_IMPLEMENTATION_HEADERS
    proof = receipt['implementation_compile']
    database = explicit_path(proof['compile_commands']['path'], 'actual core implementation compile database')
    require(digest(database) == proof['compile_commands']['sha256'], 'Core implementation compile database changed')
    commands = load(database)
    link_file = explicit_path(receipt['link_command']['path'], 'actual core link operation')
    require(digest(link_file) == receipt['link_command']['sha256'], 'Core linked operation changed')
    link = load(link_file)
    linked = {explicit_path(row['path'], 'actual linked core input'): row['sha256'] for row in link['objects']}
    found = set()
    for row in proof['tuples']:
        name = row['source']
        require(name in required and name not in found, 'Wrong/duplicate core implementation tuple')
        file = (source / name).resolve(strict=True)
        require(file.is_relative_to(source) and digest(file) == row['source_sha256'], 'Actual core implementation source changed')
        obj = explicit_path(row['object']['path'], 'actual core implementation object')
        require(digest(obj) == row['object']['sha256'], 'Actual core implementation object changed')
        command = commands[row['command_index']]
        cwd = explicit_path(command['directory'], 'actual core compiler cwd')
        argv = expanded(command.get('arguments') or shlex.split(command['command']), cwd, proof.get('response_files', []))
        require(resolve(cwd, argv[0]) == Path(receipt['compiler']['path']).resolve() and
                resolve(cwd, command['file']) == file and resolve(cwd, argument(argv, '-c')) == file and
                resolve(cwd, argument(argv, '-o')) == obj, 'Core source/object is disconnected from actual compiler argv')
        dependency = explicit_path(row['dependency_file']['path'], 'actual retained compiler dependency file')
        require(digest(dependency) == row['dependency_file']['sha256'], 'Actual core header dependency record changed')
        format_name = row['dependency_file'].get('format', 'make-depfile')
        dependency_cwd = cwd
        if format_name == 'ninja-deps':
            ninja = explicit_path(receipt['ninja']['path'], 'actual dependency-query Ninja')
            require(digest(ninja) == receipt['ninja']['sha256'], 'Ninja dependency-query tool changed')
            query, query_cwd, query_argv = operation(row['dependency_file']['command'], ninja, dependency)
            require(query.get('exit_code') == 0, 'Actual Ninja dependency query failed')
            dependency_cwd = query_cwd
            if '-C' in query_argv:
                dependency_cwd = resolve(query_cwd, argument(query_argv, '-C'))
            require(query_argv[-3:-1] == ['-t', 'deps'] and resolve(dependency_cwd, query_argv[-1]) == obj,
                    'Ninja dependency command does not query this actual object')
        deps = dependency_paths(dependency.read_text(), dependency_cwd, obj, format_name)
        required_dependency_paths(name, source, deps)
        linked_implementation_object(obj, row['object']['sha256'], linked, row.get('linked_archive'))
        found.add(name)
    require(found == set(required), 'Missing actual source/object/core-link implementation bindings')
    return {'compile_commands_sha256': proof['compile_commands']['sha256'], 'implementation_sources': sorted(found),
            'required_headers_by_translation_unit': {name: list(CORE_IMPLEMENTATION_HEADERS[name]) for name in sorted(found)},
            'source_layout_contract': 'actual-creator-single-tu-and-ghost-api-headers-v2'}

def abi_macros(argv):
    macros = []
    for index, token in enumerate(argv):
        if token in ('-D', '-U'):
            require(index + 1 < len(argv), 'Missing macro option value')
            macros.append(argv[index + 1])
        elif token.startswith(('-D', '-U')):
            macros.append(token[2:])
    require(not any('_LIBCPP_ABI_' in value for value in macros), 'CPP ABI macro override rejected')

def host_routing(argv, cwd, sdk):
    sysroots = []
    for index, token in enumerate(argv):
        if token in ('--sysroot', '-isysroot'):
            require(index + 1 < len(argv), 'Missing sysroot option')
            sysroots.append(argv[index + 1])
        elif token.startswith('--sysroot='):
            sysroots.append(token.split('=', 1)[1])
        elif token.startswith('-isysroot'):
            sysroots.append(token[len('-isysroot'):].removeprefix('='))
        require(not token.startswith(('-ivfsoverlay', '-vfsoverlay', '-idirafter', '-iprefix', '-iwithprefix',
                                      '-isystem-after', '-iframework', '-F', '-fmodule-map-file', '-fmodules-cache-path')),
                'Unbound compiler header-routing option')
        if token in ('-resource-dir', '--gcc-toolchain') or token.startswith(('-resource-dir=', '--gcc-toolchain=')):
            value = argv[index + 1] if '=' not in token else token.split('=', 1)[1]
            require(resolve(cwd, value).is_relative_to(sdk.resolve()), 'Unbound compiler resource/toolchain root')
    require(sysroots and all(resolve(cwd, value) == (sdk / 'sysroot').resolve() for value in sysroots), 'Actual host sysroot differs from selected SDK')
    abi_macros(argv)

def host_compile(commands, receipt, project, sdk):
    cpp = project / 'entry/src/main/cpp'
    expected = {(cpp / name).resolve() for name in ('napi_init.cc', 'host_bridge.cc', 'reliable_input.cc', 'xcomponent_input.cc', 'ime_input.cc')}
    inc = {(cpp).resolve(), (project / 'source/blender/source/creator').resolve(), (project / 'source/blender/intern/ghost').resolve()}
    compiler = explicit_path(receipt['compiler']['path'], 'host compiler')
    require(compiler.is_relative_to(sdk.resolve()), 'Actual host compiler is outside selected installed SDK')
    found, outputs = set(), {}
    object_rows = {explicit_path(row['path'], 'actual host object'): row for row in receipt.get('host_objects', [])}
    for command in commands:
        cwd = explicit_path(command['directory'], 'actual compiler cwd')
        metadata = resolve(cwd, command['file'])
        if metadata not in expected:
            continue
        argv = expanded(command.get('arguments') or shlex.split(command['command']), cwd, receipt.get('compile_response_files', []))
        require(resolve(cwd, argv[0]) == compiler, 'Actual compiler differs from selected SDK tool')
        require(resolve(cwd, argument(argv, '-c')) == metadata, 'Compile argv source differs from database/copied TU')
        host_routing(argv, cwd, sdk)
        standards = [token.removeprefix('-std=') for token in argv if token.startswith('-std=')]
        require(standards and standards[-1] in ('c++17', 'gnu++17'), 'Effective final host standard must be C++17')
        includes = set()
        for index, token in enumerate(argv[1:], 1):
            flag = next((value for value in ('-isystem', '-iquote', '-include', '-imacros', '-I') if token.startswith(value)), None)
            if flag:
                value = argv[index + 1] if token == flag else token[len(flag):]
                path = resolve(cwd, value)
                require(path in inc or path.is_relative_to(sdk.resolve()) or (flag in ('-include', '-imacros') and path.parent in inc),
                        'Unreviewed external/shadowing include path')
                includes.add(path)
        require({project / 'source/blender/source/creator', project / 'source/blender/intern/ghost'} <= includes,
                'Actual compiler did not include real creator/GHOST source headers')
        output = resolve(cwd, argument(argv, '-o'))
        require(output in object_rows and digest(output) == object_rows[output]['sha256'], 'Actual compiled host object unbound/changed')
        require(output not in outputs and metadata not in found, 'Duplicate host TU/object evidence')
        outputs[output] = metadata; found.add(metadata)
    require(found == expected and set(outputs) == set(object_rows), 'Exact four actual host TU/object bindings required')
    return outputs
