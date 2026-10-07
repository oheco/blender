# SPDX-License-Identifier: GPL-2.0-or-later
"""Consume the separate provided-native-ohos-toolkit schema exactly, read-only."""
import copy
import hashlib
import json
import os
from pathlib import Path
from common import bound, load, sha, relative, absolute

KIND = 'provided-native-ohos-toolkit'
PROFILE = 'clang20-sdk15-native'
ROLES = {'cc', 'cxx', 'lld', 'ar', 'ranlib', 'readelf', 'nm', 'signer', 'python', 'cmake', 'ninja', 'git', 'pkgconf', 'ctest'}


def verify_inventory(group):
    root = absolute(group['root']); names = set()
    for row in group['records']:
        p = root / str(relative(row['path']))
        if p.stat().st_size != row['size'] or sha(p) != row['sha256']:
            raise ValueError('Toolkit provided header/link byte drift')
        if p.is_symlink() and row.get('link_target') != os.readlink(p):
            raise ValueError('Toolkit provided link target drift')
        if row['path'] in names:
            raise ValueError('Repeated provided toolkit inventory row')
        names.add(row['path'])
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    expected = hashlib.sha256(json.dumps(group['records'], sort_keys=True).encode()).hexdigest()
    if names != actual or group['inventory_sha256'] != expected:
        raise ValueError('Toolkit complete provided file-set/inventory binding drift')


def native_probe(data, root, seal):
    state = data['native_probe']
    if state['status'] == 'NOT_RUN' and state['receipt'] is None:
        return ['TOOLKIT_SIGNED_NATIVE_THREAD_EXCEPTION_CPP_PROBES_NOT_RUN']
    if state['status'] != 'PASS':
        raise ValueError('Unknown toolkit native probe state')
    p = absolute(state['receipt']['path'])
    if p != root / 'native-probe-receipt.json' or sha(p) != state['receipt']['sha256']:
        raise ValueError('Toolkit native probe receipt drift')
    receipt = load(p)
    before = copy.deepcopy(data); before['native_probe'] = {'status': 'NOT_RUN', 'receipt': None}
    expected = hashlib.sha256((json.dumps(before, indent=2, ensure_ascii=False) + '\n').encode()).hexdigest()
    if receipt['status'] != 'PASS' or receipt['current_recipe_inputs_sha256'] != seal or receipt['manifest_before_native_sha256'] != expected:
        raise ValueError('Toolkit native probe does not bind its actual prepared profile')
    required = {'native_probe', 'affinity_probe', 'exceptions_rtti_format_error', 'cpp17', 'c17', 'module_owner'}
    runs = receipt['real_native_runs']
    if {r['name'] for r in runs} != required or len(runs) != len(required) or any(r['exit'] != 0 for r in runs):
        raise ValueError('Toolkit actual native execution closure incomplete')
    artifacts = {}
    origins = [load(p) for p in (root / 'signatures').glob('*.json')]
    for row in receipt['artifacts']:
        p = bound(row)
        if not p.is_relative_to(root / 'native-build') or row['signature_lineage'] not in origins or row['signature_lineage']['signed_sha256'] != row['sha256'] or row['signature_lineage']['signature_checked'] is not True:
            raise ValueError('Toolkit native artifact/signature/source lineage drift')
        artifacts[str(p)] = row['sha256']
    for run in runs:
        wanted = [artifacts[str(root / 'native-build/module_owner')], artifacts[str(root / 'native-build/libowner_module.so')]] if run['name'] == 'module_owner' else artifacts[str(root / 'native-build' / run['name'])]
        if run['signed_sha256'] != wanted:
            raise ValueError('Toolkit actual run used different signed bytes')
        log = root / 'logs' / ('native-actual-' + ('module-owner' if run['name'] == 'module_owner' else run['name']) + '.log')
        command = load(log.with_suffix('.json'))
        if command['exit'] != 0 or command['log_sha256'] != sha(log) or not command['argv'] or command['argv'][0] != str(root / 'native-build' / run['name']):
            raise ValueError('Toolkit actual native run log/command mismatch')
    for key in ('genuine_try_run', 'real_registration_retention'):
        bound(receipt[key])
    if receipt['input_guards'] != 'PASS':
        raise ValueError('Toolkit native probe input guard failed')
    return []


def verify_toolkit(repo, path, expected, allow_pending=False):
    if sha(path) != expected:
        raise ValueError('Toolkit manifest SHA drift')
    data = load(path)
    if data.get('schema_version') != 1 or data.get('kind') != KIND:
        raise ValueError('Unsupported provided toolkit kind/schema')
    p = data['profile']
    values = {'id': PROFILE, 'actual_clang20': '20.1.8', 'sdk_version': '26.0.0.35-Beta', 'libcxx_version': 15004,
              'libcxx_abi_namespace': '__n1', 'target': 'aarch64-unknown-linux-ohos', 'experimental_static_cpp': True, 'lld_invocation': 'ld.lld'}
    if any(p.get(k) != value for k, value in values.items()) or data['host'] != {'system': 'HarmonyOS', 'machine': 'aarch64'}:
        raise ValueError('Unsupported actual native toolkit profile/host')
    recipe = data['recipe']
    lock = repo / str(relative(recipe['inputs_lock']['path']))
    if recipe['entry'] != 'build_files/ohos/toolchain/builder.py' or recipe['inputs_lock']['path'] != 'build_files/ohos/toolchain/inputs.lock.json' or sha(lock) != recipe['inputs_lock']['sha256'] or data['current_recipe_inputs_sha256'] != sha(lock):
        raise ValueError('Toolkit must bind current independent source recipe')
    for row in load(lock)['sealed_files']:
        f = repo / str(relative(row['path']))
        if f.is_symlink() or f.stat().st_size != row['size'] or sha(f) != row['sha256']:
            raise ValueError('Frozen toolkit source/recipe input changed')
    facts = data['readonly_facts']; tools = {}; versions = {}
    if data['tool_records'] != facts['tool_records'] or data['header_inventories'] != facts['header_inventories'] or data['selected_sdk_macros'] != facts['selected_sdk_macros']:
        raise ValueError('Toolkit readonly facts/profile crosslink mismatch')
    for row in data['tool_records']:
        bound(row)
        if row['role'] in tools or row['state'] != 'PROVIDED_PREREQUISITE' or not os.access(row['path'], os.X_OK):
            raise ValueError('Repeated/nonexecutable/nonprovided native tool')
        tools[row['role']] = row['path']; versions[row['role']] = row['actual_version']
    if set(tools) != ROLES or Path(tools['lld']).name != 'ld.lld' or any('clang version 20.1.8' not in versions[x] for x in ('cc', 'cxx')) or 'LLD 20.1.8' not in versions['lld']:
        raise ValueError('Pinned actual tool roles/version/invocation mismatch')
    macros = data['selected_sdk_macros']
    if macros['_LIBCPP_VERSION'] != '15004' or macros['_LIBCPP_ABI_NAMESPACE'] != '__n1' or macros['__clang_major__'] != '20' or not macros.get('__OHOS__') or not macros.get('__aarch64__'):
        raise ValueError('Actual SDK15 __n1/Clang20/OHOS/AArch64 macro proof failed')
    for group in data['header_inventories'].values():
        verify_inventory(group)
    verify_inventory(facts['sdk_link_inventory'])
    for row in facts['external_runtime_inputs']:
        bound(row)
    root = absolute(data['paths']['root']); derived = {}
    for row in data['owned_generated']:
        f = root / str(relative(row['path']))
        if f.is_symlink() or f.stat().st_size != row['size'] or sha(f) != row['sha256'] or str(f) in derived:
            raise ValueError('Generated toolkit byte/file-set drift')
        derived[str(f)] = row
    actual = {str(f) for f in (root / 'profile').rglob('*') if f.is_file()}
    if actual != set(derived):
        raise ValueError('Toolkit generated extra/missing file')
    for role in ('toolchain_file', 'cc_launcher', 'cxx_launcher', 'compiler_config', 'native_link_features_include'):
        if str(absolute(data['paths'][role])) not in derived:
            raise ValueError('Unsealed generated toolkit path: ' + role)
    for role in ('cc_launcher', 'cxx_launcher'):
        if not os.access(data['paths'][role], os.X_OK):
            raise ValueError('Toolkit launcher lacks real execute permission')
    compiler = load(data['paths']['compiler_config'])
    if compiler['compilers'] != {'c': tools['cc'], 'cxx': tools['cxx']} or compiler['signer'] != tools['signer'] or compiler['readelf'] != tools['readelf']:
        raise ValueError('Toolkit compiler/sign input mismatch')
    flags = compiler['flags']['cxx']
    required = {'--target=aarch64-unknown-linux-ohos', '--ld-path=' + tools['lld'], '-fexperimental-library', '-static-libstdc++', '-lc++experimental', '-nostdinc++'}
    if not required <= set(flags) or '-resource-dir=' + data['paths']['resource_dir'] not in flags:
        raise ValueError('Toolkit lost native SDK static experimental/resource ownership flags')
    resource_proof = load(root / 'profile/resource-provenance.json')
    overlays = resource_proof['SDK15_overlay']
    if {Path(x['path']).name for x in overlays} != {'libclang_rt.builtins.a', 'clang_rt.crtbegin.o', 'clang_rt.crtend.o'} or len(overlays) != 3:
        raise ValueError('Exactly three SDK15 runtime overlay files required')
    for row in overlays:
        f = root / str(relative(row['path']))
        if str(f) not in derived or sha(f) != row['sha256'] or sha(Path(row['source'])) != row['sha256']:
            raise ValueError('Genuine SDK15 runtime overlay source/derived byte mismatch')
    gaps = native_probe(data, root, sha(lock))
    if gaps and not allow_pending:
        raise ValueError('; '.join(gaps))
    return {'manifest_sha256': expected, 'recipe_sha256': sha(lock), 'tools': tools, 'paths': data['paths'],
            'profile': PROFILE, 'gaps': gaps, 'manifest': data, 'compiler_config': compiler,
            'LLVM_compiler_rebuild': 'NOT_RUN; this is the explicitly provided compiler profile'}
