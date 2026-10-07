# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite selected C/CXX configure recording prototype; no consumer admission."""
from pathlib import Path
import json
import re
from capture_io import absolute, read_original, require, verify_reference
from capture_store import Store, Clock
from toolkit_io import HERE, REPO, file_row, sha

NAMES = {'base', 'core', 'geometry', 'volume', 'color', 'vulkan', 'python_native', 'gltf', 'pure_resources'}


def parent_context(reference, toolkit_root, recipe):
    data = json.loads(verify_reference(reference))
    required = {'schema_version', 'kind', 'producer', 'consumer', 'source_revision', 'root_registry',
                'all_nine_physical_refs', 'all_nine_native', 'parent_clock_context'}
    require(set(data) == required and type(data['schema_version']) is int and data['schema_version'] == 1 and
            data['kind'] == 'parent-provided-toolkit-native-fact-context-prototype', 'Independent parent context schema differs')
    require(data['producer'] == {'toolkit_root': str(toolkit_root), 'producer_source_root': str(REPO),
                               'recipe': recipe}, 'Actual selected toolkit/source/recipe parent context differs')
    require(data['all_nine_native'] == 'NOT_READY', 'Toolkit prototype cannot authorize all-nine native acceptance')
    require(set(data['all_nine_physical_refs']) == NAMES, 'Independent physical all-nine references missing')
    for name in ('source_revision', 'root_registry', 'parent_clock_context'): verify_reference(data[name])
    require(set(data['consumer']) == {'source_root', 'input_lock', 'output_root'} and
            absolute(data['consumer']['source_root']) != absolute(toolkit_root), 'Independent consumer/source context required')
    verify_reference(data['consumer']['input_lock'])
    for name, refs in data['all_nine_physical_refs'].items():
        require(set(refs) == {'entry', 'inputs_lock', 'source_locks', 'source_review'}, 'Exact physical producer context missing: ' + name)
        for field in ('entry', 'inputs_lock', 'source_review'): verify_reference(refs[field])
        require(refs['source_locks'], 'Physical original source locks missing')
        for item in refs['source_locks']: verify_reference(item)
    return data


def protocol(recipe):
    raw_lock, original_lock = read_original(HERE / 'inputs.lock.json')
    require(original_lock['sha256'] == recipe['inputs_lock']['sha256'], 'Selected source recipe differs from original protocol lock read')
    lock = json.loads(raw_lock); rows = {Path(row['path']).name: row for row in lock['sealed_files']}
    names = ('builder.py', 'profile.py', 'toolkit_io.py', 'capture_io.py', 'capture_store.py', 'capture_launcher.py', 'capture_cmake.py', 'launcher.py', 'abi_capture.py', 'env_policy.py', 'capture-before-project.cmake')
    return {'kind': 'producer-only-native-fact-capture-prototype-NO_RSP', 'source_recipe': recipe,
            'recipe_input_lock': original_lock, 'modules': {name: rows[name] for name in names},
            'consumer_protocol': 'NOT_ADMITTED', 'source_bytes_attest_native_origin': False}


def scalar(data, language):
    """Bounded source decoding only; unsupported generated forms stay unknown."""
    from capture_cmake import commands
    text = data.decode('utf-8'); values = []; scopes = []; wanted = 'CMAKE_' + language + '_SIZEOF_DATA_PTR'; alias = 'CMAKE_SIZEOF_VOID_P'
    for name, arguments, line in commands(text):
        args = [value for value, quoted in arguments]
        require(name in {'set', 'if', 'endif', 'else', 'elseif', 'function', 'macro', 'foreach', 'while', 'block',
                         'endfunction', 'endmacro', 'endforeach', 'endwhile', 'endblock'}, 'Unsupported generated command; retain raw scalar unknown')
        if name == 'set': require(args and not any(token in args[0] for token in ('$', ';', '\\')), 'Dynamic generated target refused')
        if name in {'if', 'function', 'macro', 'foreach', 'while', 'block'}: scopes.append(name)
        if name in {'endif', 'endfunction', 'endmacro', 'endforeach', 'endwhile', 'endblock'}:
            require(scopes and scopes[-1] == name[3:], 'Generated scope mismatch'); scopes.pop()
        if name == 'set' and args and args[0] == wanted:
            require(not scopes and len(args) == 2 and arguments[1][1] != 'bracket' and re.fullmatch(r'[1-9][0-9]*', args[1]),
                    'Unsupported/conditional generated scalar source')
            values.append(args[1])
        elif name == 'set' and args and args[0] == alias:
            require(len(args) == 2 and args[1] == '${' + wanted + '}' and arguments[1][1] != 'bracket', 'Forced global pointer literal refused')
        elif name not in {'if', 'endif'} and any(wanted in value or alias in value for value in args):
            raise ValueError('Generated scalar mutation or noncommand text refused')
    require(not scopes and len(values) == 1, 'Unique top-level original generated scalar required')
    return values[0]


def capture_generated(store, build):
    result = {}; gaps = []
    for language in ('C', 'CXX'):
        matches = list(build.rglob('CMake' + language + 'Compiler.cmake'))
        if len(matches) != 1:
            gaps.append('Actual generated ' + language + ' source missing/ambiguous'); continue
        original, data = store.snapshot(matches[0], 'actual-generated-' + language + '-compiler-source')
        value = None
        try: value = scalar(data, language)
        except (ValueError, UnicodeError) as error: gaps.append(str(error))
        result[language] = {'source': original, 'CMAKE_' + language + '_SIZEOF_DATA_PTR': value,
                            'origin': 'ACTUAL_GENERATED_SOURCE_BYTES_ONLY', 'native_acceptance': 'NOT_READY'}
    if set(result) == {'C', 'CXX'}:
        c = result['C']['CMAKE_C_SIZEOF_DATA_PTR']; cxx = result['CXX']['CMAKE_CXX_SIZEOF_DATA_PTR']
        if c is None or cxx is None or c != cxx: gaps.append('Original generated C/CXX scalar unknown/different')
    return result, gaps


def run(a, runner, manifest, context_reference):
    root = absolute(a.root); require(not (root / 'capture-abi-build').exists() and not (root / 'native-fact-captures').exists(),
                                    'New absent finite configure/capture subroots required; no resume')
    profile = manifest['profile']
    require(profile['id'] == 'clang20-sdk15-native' and profile['actual_clang20'] == '20.1.8' and
            profile['libcxx_version'] == 15004 and profile['libcxx_abi_namespace'] == '__n1' and
            profile['target'] == 'aarch64-unknown-linux-ohos', 'Actual selected compiler20/SDK15 profile differs')
    tool_records = {item['role']: item for item in manifest['tool_records']}
    require(all('clang version 20.1.8' in tool_records[role]['actual_version'] for role in ('cc', 'cxx')), 'Selected actual C/CXX version provenance differs')
    recipe = manifest['recipe']; context = parent_context(context_reference, root, recipe)
    selected = {'parent_original_context': context_reference, 'parent_context': context,
                'toolkit_manifest_original': file_row(root / 'toolkit-manifest.json'), 'recipe': recipe,
                'profile': manifest['profile'], 'sdk': manifest['sdk'], 'tool_records': manifest['tool_records'],
                'readonly_facts': manifest['readonly_facts'], 'owned_generated': manifest['owned_generated'],
                'resource_inputs': manifest['resources'], 'all_nine_native': 'NOT_READY'}
    config = json.loads(read_original(manifest['paths']['compiler_config'])[0])
    expected_owner = config['capture_owner']['owner_reference']
    clock = Clock(); parent_clock = json.loads(verify_reference(context['parent_clock_context']))
    require(parent_clock.get('kind') == 'parent-original-clock-observation-prototype' and
            parent_clock.get('host') == clock.domain['host'] and parent_clock.get('boot_id') == clock.domain['boot_id'] and
            parent_clock.get('clock') == 'monotonic_ns' and type(parent_clock.get('observed_monotonic_ns')) is int and
            0 < parent_clock['observed_monotonic_ns'] <= clock.now() and bool(parent_clock.get('parent_session_id')),
            'Independently original parent clock host/boot/session/domain differs')
    store = Store(root, expected_owner, create=True, context=selected, protocol=protocol(recipe), clock=clock)
    runner.capture = store
    generated = {}; gaps = ['Producer prototype not admitted by native consumer', 'Actual compiler-to-linker descendant exec not observed',
                             'CMake ABI COPY_FILE/removal internal lifecycle not observed; matching SHA is no copy proof',
                             'Implicit Clang default config-file reads are not observed',
                             'Kernel exec object and descendant actual argv are not independently traced',
                             'Parent clock context and original all-nine native evidence require independent parent review']
    try:
        raw_parent, _ = store.snapshot(context_reference['path'], 'independent-parent-context-original')
        store.event({'kind': 'independent-parent-selection', 'context_original': raw_parent, 'all_nine_native': 'NOT_READY'})
        for name in ('compiler_config', 'toolchain_file', 'cc_launcher', 'cxx_launcher'):
            store.snapshot(manifest['paths'][name], 'selected-profile-' + name)
        version = runner.run([a.cmake, '--version'], 'native-fact-original-cmake-version', cwd=root)
        selected_version = next(row['actual_version'] for row in manifest['tool_records'] if row['role'] == 'cmake')
        require(version == selected_version, 'Original capture CMake version differs from prepared selected actual version')
        build = root / 'capture-abi-build'; source = root / 'profile/abi-fact-probe'
        argv = [a.cmake, '-S', source, '-B', build, '-G', 'Ninja', '-DCMAKE_MAKE_PROGRAM=' + str(a.ninja),
                '-DCMAKE_TOOLCHAIN_FILE=' + str(root / 'profile/capture-native.cmake'),
                '-DCMAKE_PROJECT_INCLUDE_BEFORE=' + str(source / 'capture-before-project.cmake'),
                '-DOHOS_ABI_CAPTURE_PYTHON=' + str(a.python), '-DOHOS_ABI_CAPTURE_CONFIG=' + str(root / 'profile/compiler.json'),
                '-DOHOS_ABI_CAPTURE_STORE=' + str(root / 'profile/capture_store.py'),
                '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', '-DCMAKE_SKIP_RPATH=ON', '--debug-trycompile']
        for path in [source / 'CMakeLists.txt', source / 'capture-before-project.cmake', root / 'profile/capture-native.cmake']:
            store.snapshot(path, 'selected-finite-configure-source')
        try: runner.run(argv, 'native-fact-original-configure', cwd=root, timeout=1800)
        finally:
            if build.exists():
                for path in sorted(build.rglob('*')):
                    if path.is_file(): store.snapshot(path, 'post-configure-original-output-raw')
                generated, scalar_gaps = capture_generated(store, build); gaps.extend(scalar_gaps)
        if store.clock.domain.get('boot_id') is None: gaps.append('Original clock boot source unavailable')
        result = store.finish('RECORDED_PRODUCER_PROTOTYPE_NOT_READY', gaps, generated)
        return {'status': 'RECORDED_PRODUCER_PROTOTYPE_NOT_READY', 'receipt': result, 'native_fact_acceptance': 'NOT_READY',
                'all_nine_native': 'NOT_READY'}
    except BaseException as error:
        gaps.append(type(error).__name__ + ': ' + str(error)); store.finish('FAILED_OR_PARTIAL_PRODUCER_PROTOTYPE_NOT_READY', gaps, generated); raise
    finally: runner.capture = None; store.close()
