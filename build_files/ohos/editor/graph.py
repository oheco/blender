# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit actual CMake File API/compile/Ninja evidence; never execute tools directly.

Caller creates query/client-editor-builder/query.json before real configure, asking
for codemodel-v2, cache-v2, toolchains-v1 and cmakeFiles-v1. Profile keys are
required_options {cache_key: ON/OFF}, required_compile {source_relative: [defines]},
required_targets [names], imported_targets {name: {prefix: label, path: relative}},
and implementation_bindings {TU_relative: [header_relative]}. Prefixes maps labels
to actual caller-owned prefixes. Optional compiler_paths {C: path, CXX: path} binds
wrappers; inspect_link_commands enables the narrowly parsed Ninja link rule audit.
required_consumed_archives=[{prefix, path}] binds real static files to the actual
core link, including legacy FindGlog/Gflags paths. prefix_header_bindings maps
TU-relative paths to [{prefix, path}] public headers: plan checks include roots
and hashes, compiled audit checks exact Ninja dependencies and rejects other
providers of the same public include. LIBMV_GFLAGS_NAMESPACE may be requested by
name; its discovered value is retained in each compile record's definitions.
require_compiled_objects defaults to True; False audits a prebuild plan and returns
PASS_GENERATED_PLAN with object/dependency checks NOT_RUN. The profile must request
WITH_STRICT_BUILD_OPTIONS explicitly; OFF is diagnostic only and cannot yield the
strict compiled-graph status or final/native acceptance. Its compiled result is
PASS_DIAGNOSTIC_GRAPH. Absolute -Wl,-rpath-link paths must stay inside the build
or the declared prefixes, including an explicitly supplied SDK prefix.
The generated editor-imports.json has schema=1, targets=[{name, type,
imported_locations, include_dirs, link_libraries, link_options,
compile_definitions}]. Paths/arrays must contain resolved installed metadata.

run(argv, label) returns successful captured stdout or raises on failure. Only this
callback may run Ninja; this module has no subprocess capability. PASS_GENERATED_GRAPH
requires retained actual implementation objects and Ninja header dependencies. It
never means ELF/signature, SDK ABI, Python-provider, bpy, GUI or HAP acceptance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex


class GraphError(ValueError):
    """Missing, ambiguous or unsupported actual graph evidence."""


def require(ok, message):
    if not ok:
        raise GraphError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path, evidence):
    require(path.is_file() and not path.is_symlink(), 'Missing/nonregular graph evidence: ' + str(path))
    before = digest(path)
    value = json.loads(path.read_text(encoding='utf-8'))
    require(digest(path) == before, 'Graph evidence changed while reading: ' + str(path))
    evidence[str(path)] = before
    return value


def relative(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(), 'Relative graph path required')
    path = (root / name).resolve()
    require(path.is_relative_to(root), 'Graph path escapes declared root: ' + name)
    return path


def resolve(cwd, name):
    require(isinstance(name, str) and name and '\x00' not in name, 'Invalid graph path')
    return (cwd / name).resolve()


def tokens(text):
    require(isinstance(text, str) and not any(mark in text for mark in ('`', '$(', '\x00', '\n', '\r')),
            'Unsupported command substitution/multiline syntax')
    argv = shlex.split(text, posix=True)
    require(not any(item in (';', '|', '||', '&&', '>', '>>', '<', '&') for item in argv),
            'Unsupported shell command syntax')
    return argv


def expand(argv, cwd, build, evidence):
    require(isinstance(argv, list) and argv and all(isinstance(x, str) and x and '\x00' not in x for x in argv),
            'Actual structured argv required')
    result = []
    for item in argv:
        if item.startswith('@'):
            path = resolve(cwd, item[1:])
            require(path.is_relative_to(build) and path.is_file() and not path.is_symlink(),
                    'Missing/unowned command response file: ' + str(path))
            before = digest(path)
            values = tokens(path.read_text(encoding='utf-8'))
            require(not any(x.startswith('@') for x in values), 'Nested response syntax is unsupported')
            require(digest(path) == before, 'Response file changed: ' + str(path))
            evidence[str(path)] = before
            result.extend(values)
        else:
            result.append(item)
    return result


def option(argv, name):
    values = []
    for index, token in enumerate(argv):
        if token == name:
            require(index + 1 < len(argv), 'Missing command option value: ' + name)
            values.append(argv[index + 1])
        elif name == '-o' and token.startswith('-o') and len(token) > 2:
            values.append(token[2:])
    require(len(values) == 1, 'Exactly one actual command option required: ' + name)
    return values[0]


def definitions(argv):
    found = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ('-D', '-U'):
            index += 1
            require(index < len(argv), 'Missing macro value')
            token += argv[index]
        if token.startswith(('-D', '-U')) and len(token) > 2:
            value = token[2:]
            name = value.split('=', 1)[0]
            require('_LIBCPP_ABI_' not in name, 'Unreviewed C++ ABI macro override')
            if token.startswith('-U'):
                found.pop(name, None)
            else:
                found[name] = value
        index += 1
    return set(found.values())


def include_dirs(argv, cwd):
    """Read only the generated Clang include-option forms, preserving their order."""
    result, index = [], 0
    while index < len(argv):
        token = argv[index]
        value = None
        if token in ('-I', '-isystem', '-iquote', '-idirafter'):
            index += 1
            require(index < len(argv), 'Missing actual include directory')
            value = argv[index]
        else:
            for prefix in ('-isystem', '-iquote', '-idirafter', '-I'):
                if token.startswith(prefix) and len(token) > len(prefix):
                    value = token[len(prefix):]
                    break
        if value is not None:
            result.append(resolve(cwd, value))
        index += 1
    return result


def prefix_file(prefixes, row, suffix=None):
    require(isinstance(row, dict) and set(row) == {'prefix', 'path'} and row['prefix'] in prefixes,
            'Explicit declared prefix/file binding required')
    root = prefixes[row['prefix']]
    path = relative(root, row['path'])
    require(path.is_file() and not (root / row['path']).is_symlink() and
            (suffix is None or path.suffix == suffix), 'Missing/aliased declared prefix file: ' + str(path))
    return path


def file_api(build, evidence):
    folder = build / '.cmake/api/v1/reply'
    indexes = sorted(folder.glob('index-*.json'))
    require(indexes, 'Actual editor-builder File API reply missing; query must precede configure')
    index = load(indexes[-1], evidence)
    query = load(build / '.cmake/api/v1/query/client-editor-builder/query.json', evidence)
    expected = {'codemodel': 2, 'cache': 2, 'toolchains': 1, 'cmakeFiles': 1}
    requests = query.get('requests', [])
    require(len(requests) == 4, 'Exact four File API requests required')
    selected = {}
    for row in requests:
        version = row.get('version')
        major = version.get('major') if isinstance(version, dict) else version
        require(row.get('kind') in expected and major == expected[row['kind']], 'Unknown File API query version')
        require(row['kind'] not in selected, 'Duplicate File API request')
        selected[row['kind']] = major
    require(selected == expected, 'Incomplete File API query')
    client = index.get('reply', {}).get('client-editor-builder', {}).get('query.json', {})
    require(client.get('requests') == requests, 'File API reply belongs to another query')
    responses = client.get('responses', [])
    require(len(responses) == 4, 'Exact four successful File API responses required')
    objects = {}
    for row in responses:
        kind, version = row.get('kind'), row.get('version', {})
        minor = version.get('minor')
        require(kind in expected and kind not in objects and version.get('major') == expected[kind],
                'Failed/unknown File API response version')
        require(isinstance(minor, int) and 0 <= minor <= (6 if kind == 'codemodel' else 0),
                'Unsupported File API minor version: ' + str(kind))
        value = load(relative(folder, row['jsonFile']), evidence)
        require(value.get('kind') == kind and value.get('version') == version, 'File API object/version mismatch')
        objects[kind] = value
    return index, objects, folder


def native_system(build, index, evidence):
    version = index['cmake']['version']['string']
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version), 'Unsupported CMake version directory')
    path = build / 'CMakeFiles' / version / 'CMakeSystem.cmake'
    require(path.is_file(), 'Actual native CMakeSystem evidence missing')
    evidence[str(path)] = digest(path)
    text = path.read_text(encoding='utf-8')
    for key, allowed in (('CMAKE_HOST_SYSTEM_NAME', {'HarmonyOS', 'OHOS', 'OpenHarmony'}),
                         ('CMAKE_SYSTEM_NAME', {'HarmonyOS', 'OHOS', 'OpenHarmony'}),
                         ('CMAKE_CROSSCOMPILING', {'FALSE'})):
        values = re.findall(r'^set\(' + key + r'\s+"([^"\n]*)"\)\s*$', text, re.MULTILINE)
        require(len(values) == 1 and values[0] in allowed, 'Genuine native platform evidence required: ' + key)
    require(digest(path) == evidence[str(path)], 'Native CMakeSystem evidence changed')


def imported_graph(build, prefixes, declarations, evidence):
    data = load(build / 'editor-imports.json', evidence)
    require(data.get('schema') == 1 and isinstance(data.get('targets'), list), 'Unknown imported-target schema')
    rows = {}
    for row in data['targets']:
        require(isinstance(row, dict) and isinstance(row.get('name'), str) and row['name'] not in rows,
                'Duplicate/invalid imported-target metadata')
        rows[row['name']] = row
    archives, visited = {}, set()
    system = {'c', 'm', 'dl', 'pthread', 'util'}

    def links(value):
        require(isinstance(value, str), 'Unstructured imported link interface')
        if value.startswith('$<LINK_ONLY:') and value.endswith('>'):
            return links(value[12:-1])
        if value.startswith('$<LINK_LIBRARY:WHOLE_ARCHIVE,') and value.endswith('>'):
            return links(value[len('$<LINK_LIBRARY:WHOLE_ARCHIVE,'):-1])
        # Exact installed Ceres archive-retention expression; do not evaluate arbitrary genex.
        match = re.fullmatch(r'\$<IF:\$<STREQUAL:\$<TARGET_PROPERTY:([^,<>]+),TYPE>,STATIC_LIBRARY>,\$<LINK_LIBRARY:WHOLE_ARCHIVE,([^,<>]+)>,([^,<>]+)>', value)
        if match:
            require(len(set(match.groups())) == 1, 'Mismatched whole-archive interface target')
            return [match.group(1)]
        require('$<' not in value and ';' not in value, 'Unsupported imported generator expression/interface')
        return [value] if value else []

    def visit(name):
        if name in visited:
            return
        require(name in rows, 'Imported interface target is not recorded: ' + name)
        visited.add(name)
        row = rows[name]
        require(row.get('type') in ('STATIC_LIBRARY', 'INTERFACE_LIBRARY'), 'Nonstatic imported closure: ' + name)
        locations = row.get('imported_locations', [])
        locations = list(locations.values()) if isinstance(locations, dict) else locations
        require(isinstance(locations, list), 'Unstructured imported locations: ' + name)
        if name == 'Threads::Threads':
            require(row['type'] == 'INTERFACE_LIBRARY' and not locations,
                    'Threads::Threads must be the recorded canonical interface target')
        declaration = declarations.get(name)
        expected = None
        if declaration is not None:
            if isinstance(declaration, str):
                label, slash, tail = declaration.partition('/')
                require(slash, 'Imported declaration needs prefix label/relative path')
            else:
                label, tail = declaration['prefix'], declaration['path']
            require(label in prefixes, 'Undeclared imported prefix: ' + str(label))
            expected = relative(prefixes[label], tail)
            require(expected.suffix == '.a' and expected.is_file(), 'Canonical actual static archive missing: ' + str(expected))
            require(row['type'] == 'STATIC_LIBRARY' and locations, 'Required static imported target has no archive: ' + name)
        for value in locations:
            path = Path(value).resolve()
            require(Path(value).is_absolute() and path.is_file() and path.suffix == '.a' and
                    any(path.is_relative_to(prefix) for prefix in prefixes.values()), 'Foreign/absent imported archive: ' + str(value))
            require(expected is None or path == expected, 'Canonical imported target selects another archive: ' + name)
            archives[str(path)] = {'target': name, 'sha256': digest(path)}
        for field in ('include_dirs', 'link_libraries', 'link_options', 'compile_definitions'):
            require(isinstance(row.get(field), list), 'Imported metadata array missing: ' + name + ':' + field)
        includes = []
        for value in row['include_dirs']:
            require('$<' not in value and Path(value).is_absolute(), 'Unresolved imported include interface')
            path = Path(value).resolve()
            require(path.is_dir() and any(path.is_relative_to(prefix) for prefix in prefixes.values()),
                    'Foreign/absent imported include: ' + value)
            includes.append(path)
        if expected is not None:
            require(prefixes[label] / 'include' in includes, 'Canonical installed include interface missing: ' + name)
        for value in row['compile_definitions']:
            require(isinstance(value, str) and '$<' not in value and '_LIBCPP_ABI_' not in value,
                    'Unresolved/unreviewed imported definition')
        for value in row['link_options']:
            require(isinstance(value, str) and '$<' not in value and '\x00' not in value,
                    'Unresolved imported link option')
        for value in row['link_libraries']:
            for target in links(value):
                if target in system or target in ('-pthread', '-lpthread', '-ldl', '-lm'):
                    continue
                require(not target.startswith('-') and not Path(target).is_absolute(),
                        'Bare archive/library path cannot replace an imported target interface: ' + target)
                visit(target)
    for name in declarations:
        visit(name)
    return {'targets': sorted(visited), 'archives': archives}


def link_fragments(target, build, evidence, lookup_roots=()):
    argv = []
    for row in target.get('link', {}).get('commandFragments', []):
        require(row.get('role') in ('flags', 'libraries', 'libraryPath', 'frameworkPath'), 'Unknown link fragment role')
        argv.extend(tokens(row['fragment']))
    argv = expand(argv, build, build, evidence) if argv else []
    for index, value in enumerate(argv):
        paths = []
        if value.startswith('-Wl,-rpath-link,'):
            for name in value[len('-Wl,-rpath-link,'):].split(':'):
                require(name and Path(name).is_absolute() and ',' not in name,
                        'Unsupported linker lookup path grammar: ' + value)
                path = Path(name).resolve()
                require(any(path.is_relative_to(root) for root in (build, *lookup_roots)),
                        'Linker lookup path outside caller build/prefix/SDK roots: ' + name)
        elif value.startswith('-Wl,-rpath,'):
            paths = value[len('-Wl,-rpath,'):].split(':')
        elif value in ('-rpath', '-Wl,-rpath'):
            offset = 2 if index + 1 < len(argv) and argv[index + 1] == '-Xlinker' else 1
            require(index + offset < len(argv), 'Missing actual RPATH fragment')
            paths = argv[index + offset].split(':')
        elif 'rpath' in value.lower():
            raise GraphError('Unsupported RPATH option grammar: ' + value)
        for path in paths:
            require(path and path.startswith(('$ORIGIN', '${ORIGIN}')), 'Reserved/empty/absolute generated RPATH: ' + target['name'])
        require(not re.search(r'libpython3\.13\.so\.', value), 'Versioned Python in actual generated link')
    return argv


def audit_graph(source: Path, build: Path, profile: dict, prefixes: dict, run: callable) -> dict:
    """Return schema1 audit; FAIL is a closed gate with an explicit concrete gap."""
    result = {'schema': 1, 'kind': 'editor-generated-graph-audit', 'status': 'FAIL', 'gaps': [],
              'runtime': 'NOT_RUN', 'compiled_objects': 'NOT_RUN', 'ninja_header_deps': 'NOT_RUN',
              'final_native_acceptance': 'NOT_RUN', 'evidence': {},
              'sdk_cpp_abi': 'CALLER_AND_FINAL_ELF_GATE_REQUIRED'}
    try:
        require_objects = profile.get('require_compiled_objects', True)
        require(isinstance(require_objects, bool), 'require_compiled_objects must be a boolean')
        strict = profile['required_options'].get('WITH_STRICT_BUILD_OPTIONS')
        require(strict in ('ON', 'OFF'), 'Profile must explicitly request strict ON or diagnostic OFF')
        result.update(diagnostic_only=strict == 'OFF', strict_profile=strict == 'ON')
        source, build = source.resolve(strict=True), build.resolve(strict=True)
        prefixes = {name: Path(path).resolve(strict=True) for name, path in prefixes.items()}
        require(source.is_dir() and build.is_dir() and source != build, 'Distinct actual source/build roots required')
        index, api, folder = file_api(build, result['evidence'])
        require(api['codemodel']['paths'] == {'source': str(source), 'build': str(build)}, 'File API source/build identity mismatch')
        require(api['cmakeFiles']['paths'] == {'source': str(source), 'build': str(build)}, 'CMake input graph belongs to other roots')
        require(index['cmake']['generator']['name'] == 'Ninja', 'Actual single-config Ninja generator required')
        native_system(build, index, result['evidence'])
        entries = api['cache']['entries']
        cache = {row['name']: row['value'] for row in entries}
        require(len(cache) == len(entries), 'Duplicate File API cache entries')
        for key, value in profile['required_options'].items():
            require(value in ('ON', 'OFF') and cache.get(key) == value, 'Actual cache option mismatch: ' + key)
        tools = {}
        for row in api['toolchains']['toolchains']:
            lang, compiler = row.get('language'), row.get('compiler', {})
            if lang in ('C', 'CXX'):
                require(lang not in tools and compiler.get('id') == 'Clang' and
                        re.fullmatch(r'20\.[0-9]+\.[0-9]+', compiler.get('version', '')), 'Actual native Clang20 identity required')
                path = Path(compiler['path']).resolve(strict=True)
                require(Path(cache.get('CMAKE_' + lang + '_COMPILER', '')).resolve() == path, 'Cached compiler differs from File API')
                declared = profile.get('compiler_paths', {}).get(lang)
                require(declared is None or Path(declared).resolve(strict=True) == path, 'Compiler differs from caller wrapper identity')
                tools[lang] = path
        require(set(tools) == {'C', 'CXX'}, 'Actual C and CXX toolchains required')
        configurations = api['codemodel']['configurations']
        require(len(configurations) == 1 and configurations[0]['name'] == cache.get('CMAKE_BUILD_TYPE'), 'Ambiguous actual build configuration')
        targets, owners = {}, {}
        for row in configurations[0]['targets']:
            target = load(relative(folder, row['jsonFile']), result['evidence'])
            require(target['name'] == row['name'] and target['id'] == row['id'] and row['name'] not in targets, 'Target identity mismatch')
            targets[row['name']] = target
            for item in target.get('sources', []):
                if 'compileGroupIndex' in item:
                    owners.setdefault(resolve(source, item['path']), []).append(target)
        for name in profile['required_targets']:
            require(name in targets, 'Required actual target missing: ' + name)
        require(targets.get('blender', {}).get('type') == 'SHARED_LIBRARY', 'Actual blender shared-core target required')
        require(targets.get('blender-ohos-diagnostic', {}).get('type') == 'EXECUTABLE', 'Actual diagnostic executable required')
        actual_links = {}
        for name in profile['required_targets']:
            target = targets[name]
            artifacts = target.get('artifacts', [])
            require(artifacts or (target['type'] == 'OBJECT_LIBRARY' and
                    any('compileGroupIndex' in item for item in target.get('sources', []))),
                    'Required target lacks actual artifacts/compiled object sources: ' + name)
            for item in artifacts:
                relative(build, item['path'])
            if name == 'blender':
                require([Path(x['path']).name for x in artifacts] == ['libblender_core.so'], 'Core artifact/SONAME profile mismatch')
            actual_links[name] = link_fragments(target, build, result['evidence'], tuple(prefixes.values()))
        imported = imported_graph(build, prefixes, profile['imported_targets'], result['evidence'])
        metadata = set()
        for item in api['cmakeFiles']['inputs']:
            path = resolve(source, item['path'])
            if path.suffix == '.cmake' and path.is_file() and any(path.is_relative_to(prefix) for prefix in prefixes.values()):
                result['evidence'][str(path)] = digest(path)
                metadata.add(path)
        for declaration in profile['imported_targets'].values():
            label = declaration.split('/', 1)[0] if isinstance(declaration, str) else declaration['prefix']
            require(any(path.is_relative_to(prefixes[label]) for path in metadata),
                    'Imported archive prefix has no actual consumed CMake metadata: ' + label)
        imported['consumed_metadata'] = sorted(str(path) for path in metadata)
        canonical = {Path(path).name: Path(path) for path in imported['archives']}
        require(len(canonical) == len(imported['archives']), 'Imported static archive basenames are ambiguous')
        required_archives = profile.get('required_consumed_archives', [])
        require(isinstance(required_archives, list), 'required_consumed_archives must be a list')
        archive_records = []
        for row in required_archives:
            path = prefix_file(prefixes, row, '.a')
            with path.open('rb') as stream:
                require(stream.read(8) == b'!<arch>\n', 'Required core input is not a regular static archive')
            require(path.name not in canonical or canonical[path.name] == path,
                    'Required core archive conflicts with canonical imported input: ' + str(path))
            canonical[path.name] = path
            archive_records.append({**row, 'file': str(path), 'sha256': digest(path), 'consumer': 'blender'})
        core_archives = {resolve(build, token) for token in actual_links['blender']
                         if token.endswith('.a') and not token.startswith('-')}
        require(all(Path(row['file']) in core_archives for row in archive_records),
                'Actual core link omits required canonical static archives: ' +
                ', '.join(row['file'] for row in archive_records if Path(row['file']) not in core_archives))
        imported['required_consumed_archives'] = archive_records
        consumed = set()
        for argv in actual_links.values():
            for token in argv:
                if token.endswith('.a') and not token.startswith('-'):
                    path = resolve(build, token)
                    if path.name in canonical:
                        require(path == canonical[path.name], 'Actual link selects noncanonical imported archive: ' + token)
                        consumed.add(str(path))
        imported['consumed_archives'] = sorted(consumed)
        python_tokens = [token for token in actual_links['blender'] if Path(token).name == 'libpython3.13.so']
        python_links = [resolve(build, token) for token in python_tokens]
        require(len(set(python_links)) == 1 and python_links[0].is_file() and
                all(not (build / token).is_symlink() for token in python_tokens) and
                any(python_links[0].is_relative_to(prefix) for prefix in prefixes.values()),
                'Actual core link must select one declared unversioned Python file without aliases')
        ids = {target['id']: target for target in targets.values()}
        reachable, pending = set(), [targets['blender']['id']]
        while pending:
            identifier = pending.pop()
            if identifier in reachable:
                continue
            require(identifier in ids, 'Unrecorded actual target dependency: ' + identifier)
            reachable.add(identifier)
            pending.extend(row['id'] for row in ids[identifier].get('dependencies', []))
        database = load(build / 'compile_commands.json', result['evidence'])
        require(isinstance(database, list), 'Actual compilation database required')
        compiled = {}
        header_bindings = profile.get('prefix_header_bindings', {})
        require(isinstance(header_bindings, dict), 'prefix_header_bindings must be a TU map')
        selected = set(profile['required_compile']) | set(profile['implementation_bindings']) | set(header_bindings)
        libmv_required = profile['required_options'].get('WITH_LIBMV') == 'ON'
        stub = relative(source, 'intern/libmv/intern/stub.cc')
        require(not libmv_required or stub not in owners, 'Real libmv profile cannot own the diagnostic stub TU')
        for row in database:
            cwd = Path(row['directory']).resolve(strict=True)
            file = resolve(cwd, row['file'])
            require(not libmv_required or file != stub, 'Real libmv compilation database contains the diagnostic stub')
            if not file.is_relative_to(source) or file.relative_to(source).as_posix() not in selected:
                continue
            name = file.relative_to(source).as_posix()
            require(name not in compiled and cwd.is_relative_to(build), 'Ambiguous/foreign required compile record: ' + name)
            argv = expand(row.get('arguments') or tokens(row['command']), cwd, build, result['evidence'])
            require(resolve(cwd, argv[0]) in tools.values() and resolve(cwd, option(argv, '-c')) == file, 'Required TU/compiler routing mismatch')
            obj = resolve(cwd, option(argv, '-o'))
            require(obj.is_relative_to(build) and obj.suffix == '.o', 'Required object outside actual build')
            standard = [x for x in argv if x.startswith('-std=')]
            if file.suffix in ('.cc', '.cpp', '.cxx'):
                require(resolve(cwd, argv[0]) == tools['CXX'] and standard and standard[-1] in ('-std=c++20', '-std=gnu++20'), 'Actual C++20 wrapper/flags required')
            owning = owners.get(file, [])
            require(len(owning) == 1 and owning[0]['type'] in ('STATIC_LIBRARY', 'SHARED_LIBRARY', 'OBJECT_LIBRARY', 'EXECUTABLE'), 'Required TU lacks unique actual target ownership')
            require('CMakeFiles/' + owning[0]['name'] + '.dir/' in obj.relative_to(build).as_posix(),
                    'Actual object is disconnected from its File API target owner')
            if name in profile['implementation_bindings'] or name in header_bindings:
                require(owning[0]['id'] in reachable, 'Implementation target is outside actual core dependency graph')
            if owning[0]['type'] != 'EXECUTABLE':
                require('-fPIC' in argv and not any(x in argv for x in ('-fno-pic', '-fno-PIC', '-fno-pie')), 'Actual library PIC flag missing/overridden')
            actual_defines = definitions(argv)
            required_defines = set(profile['required_compile'].get(name, []))
            if 'LIBMV_GFLAGS_NAMESPACE' in required_defines:
                require(any(re.fullmatch(r'LIBMV_GFLAGS_NAMESPACE=[A-Za-z_][A-Za-z0-9_]*', value)
                            for value in actual_defines), 'Actual discovered libmv gflags namespace missing: ' + name)
                required_defines.remove('LIBMV_GFLAGS_NAMESPACE')
            require(required_defines <= actual_defines, 'Required actual TU definitions missing: ' + name)
            compiled[name] = {'target': owning[0]['name'], 'object': str(obj), 'source_sha256': digest(file),
                              'argv': argv, 'definitions': sorted(actual_defines)}
            if name in header_bindings:
                require(isinstance(header_bindings[name], list) and header_bindings[name],
                        'Prefix header binding list cannot be empty: ' + name)
                search = include_dirs(argv, cwd)
                records = []
                for binding in header_bindings[name]:
                    header = prefix_file(prefixes, binding)
                    require(not name.startswith('intern/libmv/') or binding['prefix'] == 'core',
                            'Real libmv headers must bind the selected core prefix')
                    parts = Path(binding['path']).parts
                    require(parts[0] == 'include', 'Prefix binding must name a public installed header')
                    include = prefixes[binding['prefix']] / 'include'
                    if len(parts) > 1 and parts[1] == 'eigen3':
                        include /= 'eigen3'
                    require(include in search, 'Actual TU include options omit the selected header root: ' + str(include))
                    records.append({**binding, 'file': str(header), 'sha256': digest(header),
                                    'include_dir': str(include), 'include_name': header.relative_to(include).as_posix(),
                                    'actual_dependency': 'NOT_RUN'})
                compiled[name]['prefix_headers'] = records
        require(set(compiled) == selected, 'Required compilation tuples missing: ' + ', '.join(sorted(selected - set(compiled))))
        ninja = Path(cache['CMAKE_MAKE_PROGRAM']).resolve(strict=True)
        if require_objects or profile.get('inspect_link_commands', False):
            require(callable(run), 'Caller Ninja evidence callback required')
        for name in sorted(set(profile['implementation_bindings']) | set(header_bindings)):
            headers = profile['implementation_bindings'].get(name, [])
            compiled[name]['headers'] = {header: digest(relative(source, header)) for header in headers}
            if not require_objects:
                continue
            obj = Path(compiled[name]['object'])
            require(obj.is_file() and not obj.is_symlink(), 'Real implementation object not built: ' + str(obj))
            output = run([str(ninja), '-C', str(build), '-t', 'deps', str(obj.relative_to(build))], 'editor-header-deps-' + name.replace('/', '-'))
            require(isinstance(output, str), 'Ninja callback must return captured actual stdout')
            lines = output.splitlines()
            require(lines and re.fullmatch(re.escape(str(obj.relative_to(build))) + r': #deps [0-9]+, deps mtime [0-9]+ \(VALID\)', lines[0]), 'Missing/stale/unknown Ninja deps record: ' + name)
            deps = {resolve(build, line.strip()) for line in lines[1:] if line.strip()}
            require(relative(source, name) in deps and all(relative(source, header) in deps for header in headers), 'Actual implementation header bindings absent: ' + name)
            for binding in compiled[name].get('prefix_headers', []):
                header = Path(binding['file'])
                require(header in deps and digest(header) == binding['sha256'],
                        'Actual compiled dependency omits/changes selected prefix header: ' + str(header))
                suffix = Path(binding['include_name']).parts
                require(not any(path != header and path.parts[-len(suffix):] == suffix for path in deps),
                        'Actual compiled dependency selects another public header provider: ' + binding['include_name'])
                binding['actual_dependency'] = 'VERIFIED'
            compiled[name]['object_sha256'] = digest(obj)
            compiled[name]['ninja_deps_sha256'] = hashlib.sha256(output.encode()).hexdigest()
        if profile.get('inspect_link_commands', False):
            for name in ('blender', 'blender-ohos-diagnostic'):
                artifact = targets[name]['artifacts'][0]['path']
                output = run([str(ninja), '-C', str(build), '-t', 'commands', '-s', artifact], 'editor-link-command-' + name)
                require(isinstance(output, str) and len(output.strip().splitlines()) == 1, 'Exactly one actual link rule required')
                argv = shlex.split(output.strip())
                if argv[:2] == [':', '&&']:
                    argv = argv[2:]
                if '&&' in argv:
                    pos = argv.index('&&')
                    tail, argv = argv[pos + 1:], argv[:pos]
                    require(tail == [':'] or (len(tail) >= 7 and tail[0] == 'cd' and
                            resolve(build, tail[1]).is_relative_to(build) and tail[2] == '&&' and
                            Path(tail[3]).name == 'cmake' and tail[4:6] == ['-E', 'echo'] and
                            not any(x in tail[6:] for x in ('&&', ';', '|'))), 'Unsupported link-rule epilogue')
                require(not any(x in argv for x in ('&&', ';', '|', '||')) and not any(x in output for x in ('`', '$(')), 'Unsupported link rule grammar')
                argv = expand(argv, build, build, result['evidence'])
                require(resolve(build, argv[0]) == tools['CXX'] and resolve(build, option(argv, '-o')) == relative(build, artifact), 'Actual link compiler/output mismatch')
                require(('-shared' in argv) == (name == 'blender') and '-static' not in argv,
                        'Actual link does not implement required shared-core/executable ownership')
                result.setdefault('link_commands', {})[name] = {'argv': argv, 'sha256': hashlib.sha256(output.encode()).hexdigest()}
        status = 'PASS_GENERATED_PLAN' if not require_objects else (
            'PASS_GENERATED_GRAPH' if strict == 'ON' else 'PASS_DIAGNOSTIC_GRAPH')
        result.update(status=status, source=str(source), build=str(build),
                      compiled_objects='VERIFIED' if require_objects else 'NOT_RUN',
                      ninja_header_deps='VERIFIED' if require_objects else 'NOT_RUN',
                      targets={name: {'type': targets[name]['type'], 'artifacts': targets[name].get('artifacts', [])} for name in profile['required_targets']},
                      compiler_paths={lang: str(path) for lang, path in tools.items()}, imported=imported, compiled=compiled,
                      prefix_header_bindings={name: compiled[name]['prefix_headers'] for name in header_bindings})
    except (GraphError, OSError, ValueError, KeyError, TypeError, IndexError) as error:
        result['gaps'].append(str(error))
    return result
