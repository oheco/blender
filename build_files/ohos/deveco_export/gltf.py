# SPDX-License-Identifier: GPL-2.0-or-later
"""Require actual feature/object and frozen upstream native receipts for ctypes bridges."""
from pathlib import Path
from common import beneath, digest, explicit_path, load, require
import evidence

BRIDGES = {'WITH_DRACO': ('bf_intern_draco_bridge', 'libbf_intern_draco_bridge.so'),
           'WITH_MESHOPTIMIZER': ('bf_intern_meshopt_bridge', 'libbf_intern_meshopt_bridge.so')}
API = {'libbf_intern_draco_bridge.so': {'encoderCreate','encoderRelease','encoderSetAttribute','encoderEncode','decoderCreate','decoderRelease','decoderDecode','decoderCopyAttribute'},
       'libbf_intern_meshopt_bridge.so': {'encodeVertexBuffer','decodeVertexBuffer','encodeIndexBuffer','decodeIndexBuffer','encodeFilterOct','decodeFilterOct'}}

def bound_file(record, label):
    file = explicit_path(record['path'], label)
    require(digest(file) == record['sha256'], label + ' bytes changed')
    return file

def verify_features(core, source, profile):
    flags = core.get('features')
    require(isinstance(flags, dict) and all(isinstance(flags.get(flag), bool) for flag in BRIDGES), 'Actual compressed glTF core feature receipt is missing/unknown')
    require(profile in ('full-required', 'prepared-diagnostic'), 'Explicit full-required/prepared-diagnostic feature profile required')
    flags = {flag: flags[flag] for flag in BRIDGES}
    if profile == 'full-required':
        require(all(flags.values()), 'Full-required profile cannot disable compressed glTF')
    proof = core['feature_evidence']
    cache = bound_file(proof['cache'], 'actual core feature cache').read_text()
    targets = bound_file(proof['targets'], 'actual generated core targets').read_text()
    commands = load(bound_file(proof['compile_commands'], 'actual generated core compile commands'))
    seen = set()
    for flag, enabled in flags.items():
        if flag not in BRIDGES: continue
        require((flag + ':BOOL=' + ('ON' if enabled else 'OFF')) in cache.splitlines(), 'Actual core cache feature differs')
        if not enabled: continue
        target, _ = BRIDGES[flag]
        require(target in targets, 'Enabled actual generated bridge target missing')
        tuples = [row for row in proof['tuples'] if row['feature'] == flag]
        require(tuples, 'Enabled feature lacks actual source/object/definition tuple')
        for row in tuples:
            require(row['target'] == target, 'Feature tuple names wrong actual bridge target')
            file = beneath(source, row['source'])
            require(digest(file) == row['source_sha256'], 'Actual feature TU source differs from selected source snapshot')
            compiled_source = file.resolve()
            if row.get('compiled_source'):
                patched = bound_file(row['compiled_source'], 'actual compiled patched bridge source')
                changes = {item['path']: item for item in load(source / 'build_files/ohos/deps_gltf_sources/patches.lock.json')['files']}
                change = changes.get(row['source'])
                require(change and row['source_sha256'] == change['before_sha256'] and digest(patched) == change['after_sha256'],
                        'Actual compiled patched source lacks selected before/after source provenance')
                compiled_source = patched
            obj = bound_file(row['object'], 'actual generated feature object')
            command = commands[row['command_index']]
            cwd = explicit_path(command['directory'], 'actual feature compiler cwd')
            argv = evidence.expanded(command.get('arguments') or __import__('shlex').split(command['command']), cwd,
                                     proof.get('response_files', []))
            require(evidence.resolve(cwd, command['file']) == compiled_source and evidence.resolve(cwd, evidence.argument(argv, '-c')) == compiled_source, 'Feature tuple source differs from actual compiler argv')
            require(evidence.resolve(cwd, evidence.argument(argv, '-o')) == obj and any(target in token for token in argv), 'Feature tuple object/target differs from generated command')
            evidence.abi_macros(argv)
        seen.add(flag)
    require(seen == {flag for flag in BRIDGES if flags[flag]}, 'Missing enabled feature source/object tuple')
    return flags

def accepted_bridges(root, source, libraries, enabled, run_records):
    root = explicit_path(root, 'actual frozen glTF native full receipt root')
    full = load(root / 'full-native-acceptance.json')
    lock_sha = digest(source / 'build_files/ohos/deps_gltf_sources/inputs.lock.json')
    require(full.get('result') == 'PASS actual independent source-built native full and moved pipeline' and full['input_lock_sha256'] == lock_sha,
            'Actual source-selected glTF native full receipt missing/stale')
    require(full['prefix_seal_sha256'] == digest(root / 'prefix-seal.json'), 'glTF native prefix seal changed')
    receipts = {}
    for name in ('acceptance.json', 'artifacts.json', 'migration.json'):
        require(full['receipts'][name] == digest(root / name), 'glTF actual native receipt changed')
        receipts[name] = load(root / name)
        require(receipts[name]['result'].startswith('PASS'), 'glTF actual native receipt not PASS')
    acceptance = receipts['acceptance.json']
    artifacts = receipts['artifacts.json']
    require(artifacts['input_lock_sha256'] == lock_sha and artifacts['acceptance_sha256'] == digest(root / 'acceptance.json'), 'glTF artifacts not source/acceptance bound')
    require(acceptance['bridge']['result'] == 'PASS' and len(acceptance['consumers']) == 2, 'Actual codec and bridge calls missing')
    records = []
    for row in run_records:
        file = bound_file(row, 'actual glTF native process record')
        value = load(file)
        log = file.with_suffix('.log')
        require(value.get('exit_code') == 0 and value.get('expected_exit') == 0 and digest(log) == value['log_sha256'],
                'Actual glTF native process/log not successful/bound')
        records.append(value)
    require(len(records) >= 3, 'Explicit actual CMAKE/PC/bridge native run records required')
    for consumer in acceptance['consumers']:
        artifact = consumer['artifact']
        executable = explicit_path(artifact['path'], 'actual accepted glTF native consumer')
        require(digest(executable) == artifact['sha256'] and 'ALL PASS gltf native checks=' in consumer['stdout'] and artifact['codesign'] is True,
                'Actual native codec consumer sentinel/final executable missing')
        require(any(Path(value['command'][0]).resolve() == executable for value in records), 'Accepted codec executable has no actual exit0/log record')
    helper = source / 'build_files/ohos/deps_gltf_sources/acceptance/bridge_abi_acceptance.py'
    require(any(len(value['command']) > 1 and Path(value['command'][1]).is_file() and
                digest(Path(value['command'][1])) == digest(helper) for value in records), 'Actual selected bridge ABI helper exit0/log missing')
    selected_lock = load(source / 'build_files/ohos/deps_gltf_sources/inputs.lock.json')
    changes = {row['path']: row for row in load(source / 'build_files/ohos/deps_gltf_sources/patches.lock.json')['files']}
    compiled_sources = []
    for row in selected_lock['sealed_files']:
        if not row['path'].startswith(('intern/draco_bridge/', 'intern/meshoptimizer_bridge/')): continue
        require(digest(beneath(source, row['path'])) == row['sha256'], 'Original formal bridge source differs from selected native input lock')
        change = changes.get(row['path'])
        expected = row['sha256']
        if change:
            require(change['before_sha256'] == expected, 'Bridge patch preimage differs from formal source')
            expected = change['after_sha256']
        file = beneath(root / 'blender-bridge-source', row['path'])
        require(digest(file) == expected, 'Actual compiled patched native bridge source differs')
        compiled_sources.append({'path': row['path'], 'sha256': expected, 'size': file.stat().st_size})
    require(compiled_sources and artifacts['bridges']['actual_Blender_sources'] is True and artifacts['bridges']['patched'] is True,
            'Actual complete patched bridge source provenance missing')
    binaries = {Path(row['path']).name: row for row in artifacts['binaries']}
    bindings = []
    for flag in BRIDGES:
        if not enabled[flag]: continue
        _, name = BRIDGES[flag]
        require(name in libraries and name in binaries, 'Required ctypes bridge absent: ' + name)
        info = libraries[name]['audit']
        require(info['sha256'] == binaries[name]['sha256'] and binaries[name]['codesign'] is True and API[name] <= set(info['exports']),
                'Final signed bridge differs from actual upstream/native C ABI acceptance')
        bindings.append({'path': 'lib/' + name, 'library': name})
        # Additional owned alias covers a SYSTEM_LIBS branch if a future platform
        # adapter provides it. Actual current OHOS LOCAL parent/lib needs postlink bpy.
        bindings.append({'path': '5.2/scripts/addons_core/io_scene_gltf2/' + name, 'library': name})
    return {'full_native_receipt_sha256': digest(root / 'full-native-acceptance.json'), 'input_lock_sha256': lock_sha,
            'features': enabled, 'runtime_loader': 'PREPARED_OWNED_PATH_BINDINGS_POSTLINK_BPY_AND_INSTALLED_HAP_NOT_RUN',
            'native_source_receipts': full['receipts'], 'compiled_bridge_sources': compiled_sources,
            'source_patches_lock_sha256': digest(source / 'build_files/ohos/deps_gltf_sources/patches.lock.json')}, bindings
