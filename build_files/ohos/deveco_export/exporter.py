#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two-phase portable DevEco candidate. No compile/sign/install/network operations."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
sys.dont_write_bytecode = True
from common import (Rejected, beneath, copy_exact, digest, explicit_path, ensure_fresh_output,
                    load, metadata_output, object_digest, record_file, relative, require, verify_record,
                    verify_self_lock, write_new)
import native
import evidence
import gltf
import rebuild
import resources
import source_guard
import offline_hvigor
import offline_cache_links

HERE = Path(__file__).resolve().parent
REQUIRED_PATHS = ('source_root', 'source_manifest', 'host_root', 'host_inventory', 'runtime_root',
                  'development_receipt', 'python_audit', 'numpy_audit', 'pure_root',
                  'core_library', 'core_receipt', 'sdk_root', 'sdk_ets_root', 'readelf')
BUILD_TOOLS = {'python', 'node', 'hvigor', 'ohpm', 'cmake', 'ninja', 'signer', 'git', 'ctest', 'pkgconf',
               'native_llvm20_cc', 'native_llvm20_cxx', 'native_lld20'}
NOTICE_GROUPS = {'base', 'geometry', 'core', 'volume', 'color', 'vulkan', 'sdk-runtime'}
GATES = {'source_rebuild': 'NOT_RUN', 'CompileArkTS': 'NOT_RUN', 'actual_HAP_sign_install': 'NOT_RUN',
         'Ark_callbacks': 'NOT_RUN', 'installed_native_namespace': 'NOT_RUN',
         'unique_PyRuntime_in_installed_HAP': 'NOT_RUN', 'picker_six_actions': 'NOT_RUN',
         'ABI2_physical_drain': 'NOT_RUN', 'XComponent_Vulkan_WSI_GPU': 'NOT_RUN',
         'installed_no_spawn_workflow': 'NOT_RUN', 'runtime_hash_patch': 'PREPARED_NOT_APPLIED'}

def spec_paths(spec):
    require(spec.get('schema') == 1 and spec.get('bridge_stl') in ('c++_shared', 'c++_static'), 'Explicit SDK bridge STL profile required')
    paths = {}
    for key in REQUIRED_PATHS:
        paths[key] = explicit_path(spec.get(key), key)
    require(paths['core_library'].name == 'libblender_core.so', 'Real core input filename required')
    require(spec.get('feature_profile') in ('full-required', 'prepared-diagnostic'), 'Explicit core feature profile required')
    require(spec.get('system_libraries') and isinstance(spec['system_libraries'], list), 'Explicit SDK system library declarations required')
    require(not set(spec['system_libraries']) & native.FORBIDDEN_SYSTEM, 'SDK C++ cannot be declared a system runtime')
    require({item['label'] for item in spec.get('notice_inputs', [])} >= NOTICE_GROUPS, 'Complete explicit dependency/SDK notice groups required')
    require({item['label'] for item in spec.get('offline_inputs', [])} >= {'hvigor', 'ohpm'}, 'Pinned offline Hvigor/OHPM cache inventories required')
    require(BUILD_TOOLS <= set(spec.get('tool_prerequisites', {})), 'All native/DevEco build tools must be explicitly declared')
    for name, row in spec['tool_prerequisites'].items():
        if name == 'hvigor':
            offline_cache_links.verify_selected_tool(row)
        else:
            file = explicit_path(row['path'], 'tool prerequisite ' + name)
            require(file.is_file() and digest(file) == row['sha256'], 'Declared build tool bytes differ: ' + name)
    return paths

def plan(spec):
    missing = []
    for key in REQUIRED_PATHS:
        value = spec.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).exists():
            missing.append(key)
    for name in sorted(BUILD_TOOLS):
        row = spec.get('tool_prerequisites', {}).get(name)
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not Path(row['path']).is_file():
            missing.append('tool:' + name)
    for name, items in (('offline', spec.get('offline_inputs', [])), ('notices', spec.get('notice_inputs', []))):
        if not items:
            missing.append(name + ':explicit_sealed_inputs')
    source = Path(spec['source_root']) if isinstance(spec.get('source_root'), str) else None
    contract = rebuild.detect(source, spec.get('rebuild_commands')) if source and source.is_dir() else None
    return {'schema': 1, 'status': 'BLOCKED_MISSING_REAL_INPUTS' if missing else 'PREPARED_METADATA_ONLY',
            'missing_inputs': missing, 'rebuild_contract': contract, 'gates': GATES,
            'heavy_source_or_ELF_payloads_read': False, 'native_assembly': 'NOT_RUN',
            'note': 'Read-only presence/recipe detection only; not an ELF, SDK, signature, complete rebuild or project acceptance.'}

def sealed_tree(origin: Path, destination: Path, manifest):
    require(manifest.get('files'), 'Explicit sealed tree required')
    rows = manifest['files']
    require(all(row.get('kind', 'file') == 'file' for row in rows), 'Support/offline/host inventories contain regular files only')
    require({name for name, _ in source_guard.source_paths(origin)} == {row['path'] for row in rows}, 'Sealed tree file set differs')
    for row in rows:
        copy_exact(verify_record(origin, row), destination / relative(row['path']), row['sha256'])

def final_sign_receipt(path: Path, receipt, source_root: Path):
    require(receipt.get('link_exit_code') == 0 and receipt.get('sign_exit_code') == 0,
            'Actual successful final link/sign receipt required')
    require(receipt.get('signed_sha256') == digest(path), 'Final signing receipt belongs to other bytes')
    for field in ('link_log', 'link_command', 'sign_log', 'sign_command', 'compiler', 'signer'):
        entry = receipt.get(field)
        require(isinstance(entry, dict), 'Explicit actual ' + field + ' evidence required')
        file = explicit_path(entry.get('path'), field)
        require(digest(file) == entry['sha256'], 'Evidence/tool bytes differ: ' + field)
    bindings = receipt.get('source_bindings', [])
    require(bindings, 'Actual link source/header bindings required')
    for row in bindings:
        file = beneath(source_root, row['path'])
        require(digest(file) == row['sha256'], 'Actual built source/header differs: ' + row['path'])
    operations, _, _, _ = evidence.signed_operations(receipt, path)
    return {'receipt_sha256': object_digest(receipt), 'signed_sha256': receipt['signed_sha256'],
            'actual_operations': operations,
            'link_log_sha256': receipt['link_log']['sha256'], 'link_command_sha256': receipt['link_command']['sha256'],
            'sign_log_sha256': receipt['sign_log']['sha256'],
            'compiler_sha256': receipt['compiler']['sha256'], 'signer_sha256': receipt['signer']['sha256'],
            'source_bindings': bindings, 'cryptographic_trust': 'NOT_RUN_INSTALLED_HAP_REQUIRED'}

def sdk_check(paths, spec):
    sdk = paths['sdk_root']
    for name in ('oh-uni-package.json', 'sysroot/usr/include/napi/native_api.h', 'build/cmake/ohos.toolchain.cmake',
                 'llvm/include/libcxx-ohos/include/c++/v1/__config_site'):
        require((sdk / name).is_file(), 'Explicit installed SDK prerequisite missing: ' + name)
    ets = paths['sdk_ets_root']
    require((ets / 'oh-uni-package.json').is_file() and (ets / 'build-tools/ets-loader').is_dir(), 'Explicit installed ETS SDK prerequisite missing')
    ets_metadata = load(ets / 'oh-uni-package.json')
    require(str(ets_metadata.get('version', '')).startswith('26.0.0'), 'Explicit reviewed ETS SDK26 required')
    metadata = load(sdk / 'oh-uni-package.json')
    require(str(metadata.get('version', '')).startswith('26.0.0'), 'Explicit reviewed SDK26 required')
    require('_LIBCPP_ABI_NAMESPACE __n1' in (sdk / 'llvm/include/libcxx-ohos/include/c++/v1/__config_site').read_text(),
            'Actual SDK C++ ABI configuration differs from reviewed __n1')
    for name in spec['system_libraries']:
        relative(name)
        require('/' not in name and (sdk / 'sysroot/usr/lib/aarch64-linux-ohos' / name).is_file(), 'SDK system library declaration absent: ' + name)
    return {'version': metadata['version'], 'manifest_sha256': digest(sdk / 'oh-uni-package.json'),
            'ETS_version': ets_metadata['version'], 'ETS_manifest_sha256': digest(ets / 'oh-uni-package.json'),
            'SDK_redistributed': False, 'bridge_stl': spec['bridge_stl'],
            'cpp_namespace': 'SDK15 __n1; actual final host still requires audit',
            'readelf_sha256': digest(paths['readelf'])}

def add_extras(libraries, paths, spec):
    for extra in spec.get('native_extras', []):
        name = extra['library']
        require(name != 'libblender_host.so', 'Host name is reserved for actual CMake output; no packaged duplicate')
        require(not name.startswith('libpython'), 'Only the accepted native93 Python closure may supply PyRuntime libraries')
        path = explicit_path(extra['path'], 'signed SDK/application extra')
        info = native.audit(path, paths['readelf'], 'extra')
        require(info['sha256'] == extra['signed_sha256'] and info['soname'] == name and name not in libraries,
                'Extra final signed bytes/name collide or differ')
        if extra.get('sdk_original'):
            original = beneath(paths['sdk_root'], extra['sdk_original'])
            require(digest(original) == extra['sdk_original_sha256'], 'SDK extra provenance differs')
        sign = load(explicit_path(extra['sign_receipt'], 'extra sign receipt'))
        require(sign.get('sign_exit_code') == 0 and sign.get('signed_sha256') == info['sha256'], 'Extra final-sign evidence missing')
        log = explicit_path(sign['sign_log']['path'], 'extra signing log')
        require(digest(log) == sign['sign_log']['sha256'], 'Extra signing log differs')
        info['final_sign_evidence'] = evidence.signed_extra(sign, path, spec['tool_prerequisites']['signer']['sha256'])
        libraries[name] = {'source': path, 'audit': info}
    if spec['bridge_stl'] == 'c++_shared':
        require('libc++_shared.so' in libraries, 'SDK shared bridge profile requires explicit signed libc++_shared.so payload, never system assumption')

def derive_profiles(project: Path, spec):
    cpp = project / 'entry/src/main/cpp/CMakeLists.txt'
    text = cpp.read_text()
    old_source = 'set(BLENDER_SOURCE "" CACHE PATH'
    old_core = 'set(BLENDER_CORE_LIBRARY "" CACHE FILEPATH'
    require(text.count(old_source) == 1 and text.count(old_core) == 1, 'Frozen CMake template contexts differ')
    text = text.replace(old_source, 'set(BLENDER_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../../../source/blender" CACHE PATH')
    text = text.replace(old_core, 'set(BLENDER_CORE_LIBRARY "${CMAKE_CURRENT_LIST_DIR}/../../../libs/arm64-v8a/libblender_core.so" CACHE FILEPATH')
    before = digest(cpp)
    cpp.write_text(text)
    profile = project / 'entry/build-profile.json5'
    value = json.loads(profile.read_text())
    before_profile = digest(profile)
    value['buildOption']['externalNativeOptions']['arguments'] = '-DOHOS_STL=' + spec['bridge_stl']
    profile.write_text(json.dumps(value, indent=2) + '\n')
    return [{'path': 'entry/src/main/cpp/CMakeLists.txt', 'before_sha256': before, 'after_sha256': digest(cpp)},
            {'path': 'entry/build-profile.json5', 'before_sha256': before_profile, 'after_sha256': digest(profile)}]

def assemble(spec, output: Path, allow_prepared_rebuild=False):
    # Missing real core/snapshot rejects before heavy walks or payload copying.
    paths = spec_paths(spec)
    input_roots = [path for path in paths.values() if path.is_dir()]
    input_roots += [explicit_path(item['root'], 'sealed support input') for item in spec.get('support_inputs', []) + spec['offline_inputs'] + spec['notice_inputs']]
    if spec.get('gltf_receipt_root'):
        input_roots.append(explicit_path(spec['gltf_receipt_root'], 'glTF native receipt/source root'))
    output = ensure_fresh_output(output, input_roots)
    for extra in ('core_library', 'source_manifest', 'host_inventory', 'development_receipt', 'python_audit', 'numpy_audit', 'core_receipt'):
        require(paths[extra].is_file(), 'Regular explicit input required: ' + extra)
    pins = load(HERE / 'accepted-inputs.json')['fingerprints']
    for role, key in (('host151_inventory', 'host_inventory'), ('development2_receipt', 'development_receipt'),
                      ('python74_audit', 'python_audit'), ('numpy19_audit', 'numpy_audit')):
        require(digest(paths[key]) == pins[role], 'Explicit accepted input fingerprint differs: ' + role)
    source_manifest = load(paths['source_manifest'])
    source_guard.validate_manifest(source_manifest)
    contract = rebuild.detect(paths['source_root'], spec.get('rebuild_commands'), source_manifest)
    require(allow_prepared_rebuild or not contract['gaps'], 'Portable source rebuild gaps: ' + ', '.join(contract['gaps']))
    sdk = sdk_check(paths, spec)
    core_receipt = load(paths['core_receipt'])
    require(core_receipt.get('cpp_profile') == 'clang20-sdk15-static-__n1', 'Actual reviewed core compiler/runtime profile required')
    require(core_receipt['compiler']['sha256'] == spec['tool_prerequisites']['native_llvm20_cxx']['sha256'] and
            core_receipt['signer']['sha256'] == spec['tool_prerequisites']['signer']['sha256'], 'Actual core compiler/signer not selected prerequisites')
    core_sign = final_sign_receipt(paths['core_library'], core_receipt, paths['source_root'])
    require(evidence.CORE_SOURCE_BINDINGS <= {row['path'] for row in core_receipt['source_bindings']}, 'Core actual TU/API/implementation/export/CMake source bindings missing')
    if any(row['dependency_file'].get('format') == 'ninja-deps' for row in core_receipt['implementation_compile']['tuples']):
        require(core_receipt['ninja']['sha256'] == spec['tool_prerequisites']['ninja']['sha256'], 'Actual core dependency-query Ninja differs from selected prerequisite')
    core_sign['implementation_binding'] = evidence.core_implementations(core_receipt, paths['source_root'])
    core_features = gltf.verify_features(core_receipt, paths['source_root'], spec['feature_profile'])
    core = native.audit(paths['core_library'], paths['readelf'], 'core')
    rows, dev = native.accepted_rows(paths['runtime_root'], paths['development_receipt'], paths['python_audit'], paths['numpy_audit'])
    pure = resources.verify_pure(paths['source_root'], paths['runtime_root'], paths['pure_root'], dev)
    libraries, bindings, imports = native.packaged_python(rows, paths['runtime_root'], paths['readelf'])
    libraries['libblender_core.so'] = {'source': paths['core_library'], 'audit': core}
    add_extras(libraries, paths, spec)
    compressed_gltf = {'features': core_features, 'runtime_loader': 'NOT_ENABLED_PREPARED_DIAGNOSTIC'}
    if any(core_features.values()):
        compressed_gltf, codec_bindings = gltf.accepted_bridges(spec['gltf_receipt_root'], paths['source_root'], libraries,
                                                              core_features, spec['gltf_run_records'])
        bindings.extend(codec_bindings)
    dependencies = native.close_dependencies(libraries, set(spec['system_libraries']))
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(os.environ['TMPDIR']).resolve(strict=True)
    require(temp.stat().st_dev == output.parent.stat().st_dev, 'Publication requires same private filesystem as TMPDIR')
    with tempfile.TemporaryDirectory(prefix='deveco-export-', dir=temp) as private:
        project = Path(private) / 'project'
        project.mkdir()
        host_manifest = load(paths['host_inventory'])
        # Inventory is whole host151; only Deveco subtree is copied, historical overlays are never production source.
        host_rows = host_manifest['files']
        require(len(host_rows) == 151, 'Require frozen whole-host151 inventory')
        require({name for name, _ in source_guard.source_paths(paths['host_root'])} == {row['path'] for row in host_rows}, 'Frozen host inventory changed')
        for row in host_rows:
            verify_record(paths['host_root'], row)
        for row in host_rows:
            if row['path'].startswith('deveco/'):
                copy_exact(paths['host_root'] / row['path'], project / row['path'].removeprefix('deveco/'), row['sha256'])
        source_receipt = source_guard.copy_source(paths['source_root'], project / 'source/blender', source_manifest)
        for row in compressed_gltf.get('compiled_bridge_sources', []):
            file = beneath(Path(spec['gltf_receipt_root']).resolve() / 'blender-bridge-source', row['path'])
            copy_exact(file, project / 'support/gltf-native-bridge-source' / row['path'], row['sha256'])
        diagnostic = None
        if spec.get('diagnostic'):
            item = spec['diagnostic']
            file = explicit_path(item['path'], 'optional actual diagnostic')
            diagnostic = native.audit(file, paths['readelf'], 'diagnostic')
            final_sign_receipt(file, load(explicit_path(item['receipt'], 'diagnostic receipt')), paths['source_root'])
            require('libblender_core.so' in diagnostic['needed'], 'Diagnostic must use the same actual core')
            require(not set(diagnostic['needed']) - (set(libraries) | set(spec['system_libraries'])), 'Diagnostic NEEDED closure missing')
            copy_exact(file, project / 'diagnostic/blender-ohos-diagnostic', diagnostic['sha256'])
        copied_inputs = []
        hvigor_identity = None
        hvigor_cache_copy = None
        for group, items in (('support', spec.get('support_inputs', [])), ('offline', spec['offline_inputs'])):
            for item in items:
                label = relative(item['label'])
                require('/' not in label, 'Single-component support label required')
                manifest = load(explicit_path(item['inventory'], 'sealed support inventory'))
                origin = explicit_path(item['root'], 'sealed support root')
                opted = item.get('cache_profile')
                require(opted is None or (group == 'offline' and label == 'hvigor' and
                        opted == offline_cache_links.PROFILE), 'Cache filelink profile is Hvigor-offline-only')
                if opted == offline_cache_links.PROFILE:
                    require(hvigor_identity is None, 'Duplicate selected Hvigor cache')
                    # Keep the raw caller root for no-symlink-directory FD traversal;
                    # explicit_path's resolved value is not permission to use aliases.
                    with offline_cache_links.VerifiedCache(item['root'], manifest) as cache:
                        cache.package_metadata()
                        hvigor_identity = offline_hvigor.validate_payload(cache.root, manifest,
                                                spec['tool_prerequisites']['hvigor'], cache=cache)
                        hvigor_cache_copy = cache.copy_to(project / group / label, owned_root=project)
                    copied_inputs.append({'group': group, 'label': label, 'inventory_sha256': object_digest(manifest),
                                          'files': len(manifest['files']), 'preserved_filelinks': len(manifest['symlinks']),
                                          'cache_profile': offline_cache_links.PROFILE})
                    continue
                if group == 'offline':
                    require(manifest.get('kind') == 'frozen-offline-cache' and manifest.get('network_at_build_time') is False and
                            manifest.get('resolution_lock') and manifest.get('packages'), 'Pinned offline cache/resolution manifest required')
                    require(manifest['resolution_lock'] in {row['path'] for row in manifest['files']}, 'Offline resolution lock not sealed')
                    package_versions = {}
                    for package in manifest['packages']:
                        file = beneath(origin, package['manifest'])
                        value = load(file)
                        require(value.get('name') == package['name'] and value.get('version') == package['version'], 'Offline package metadata differs')
                        require(package['manifest'] in {row['path'] for row in manifest['files']}, 'Offline package metadata not sealed')
                        package_versions[package['name']] = package['version']
                    if label == 'hvigor':
                        require(hvigor_identity is None, 'Duplicate selected Hvigor cache')
                        hvigor_identity = offline_hvigor.validate_payload(origin, manifest, spec['tool_prerequisites']['hvigor'])
                sealed_tree(origin, project / group / label, manifest)
                copied_inputs.append({'group': group, 'label': label, 'inventory_sha256': object_digest(manifest), 'files': len(manifest['files'])})
        require(hvigor_identity is not None, 'Validated selected native Hvigor cache required')
        derived = derive_profiles(project, spec)
        derived.append(offline_hvigor.derive_project_package(project, hvigor_identity))
        library_rows = []
        for name, row in sorted(libraries.items()):
            copy_exact(row['source'], project / 'entry/libs/arm64-v8a' / name, row['audit']['sha256'])
            library_rows.append({'library': name, 'sha256_before_hap_sign': row['audit']['sha256'],
                                 'size_before_hap_sign': row['audit']['size'], 'mode': row['audit']['mode'], 'soname': row['audit']['soname']})
        manifest = resources.stage_resources(project, project / 'source/blender', paths['runtime_root'], rows,
                                             spec['notice_inputs'], bindings, library_rows, dependencies)
        write_new(project / 'entry/src/main/resources/rawfile/runtime-manifest.json', manifest)
        write_new(project / 'evidence/source-inventory.json', source_manifest)
        write_new(project / 'evidence/rebuild-contract.json', contract)
        write_new(project / 'evidence/python-import-map.json', imports)
        write_new(project / 'evidence/accepted-native93.json', rows)
        write_new(project / 'evidence/pure8.json', pure)
        copied = [record_file(project, path) for _, path in source_guard.source_paths(project) if path.is_file() and not path.is_symlink()]
        result = {'schema': 1, 'status': 'PREPARED_HOST_LINK_PENDING', 'complete_portable_project': False,
                  'source': source_receipt, 'core_sign': core_sign, 'core_audit': core,
                  'optional_diagnostic': diagnostic, 'diagnostic_execution': 'NOT_RUN_TERMINAL_ONLY_NEVER_HAP_SPAWN',
                  'compressed_gltf': compressed_gltf, 'feature_profile': spec['feature_profile'],
                  'tool_fingerprints': {name: row['sha256'] for name, row in spec['tool_prerequisites'].items()},
                  'exporter_lock_sha256': digest(HERE / 'inputs.lock.json'), 'sdk_prerequisite': sdk,
                  'host_template_inventory_sha256': digest(paths['host_inventory']), 'derived_template_files': derived,
                  'copied_support_offline': copied_inputs, 'offline_hvigor_identity': hvigor_identity,
                  'offline_hvigor_cache_copy': hvigor_cache_copy,
                  'native_python_input_count': 93,
                  'packaged_python_DSO_count': 92, 'dynamic_import_mapping_count': 90, 'pure8': pure,
                  'native_libraries': {name: row['audit'] for name, row in libraries.items()},
                  'system_libraries': spec['system_libraries'], 'nativeDependencies': dependencies,
                  'runtime_manifest_id': manifest['id'], 'rebuild_gaps': contract['gaps'],
                  'gates': GATES, 'files': copied,
                  'source_links': [{'path': 'source/blender/' + row['path'], 'target': row['target']}
                                   for row in source_manifest['files'] if row['kind'] == 'symlink'],
                  'native_bytes_modified': False, 'source_SDK_redistributed': False}
        result['seal'] = object_digest(result)
        write_new(project / 'export-manifest.json', result)
        # mkdir is the atomic absent-output reservation. Never rename over a caller's
        # empty directory. Publish the completed manifest last; moves share one FS.
        output.mkdir()
        try:
            children = sorted(project.iterdir(), key=lambda item: (item.name == 'export-manifest.json', item.name))
            for child in children:
                child.rename(output / child.name)
        except BaseException:
            shutil.rmtree(output)
            raise
    return {'output': str(output), 'status': result['status'], 'seal': result['seal'], 'gates': GATES}

def audit_host(project: Path, host: Path, receipt_path: Path, cache: Path, readelf: Path, sdk: Path, output: Path):
    sdk = sdk.resolve(strict=True)
    output = ensure_fresh_output(output, [project, sdk, host.parent, cache.parent])
    assembled = load(project / 'export-manifest.json')
    seal = assembled.pop('seal')
    require(seal == object_digest(assembled), 'Assembly manifest seal changed')
    require(assembled['status'] == 'PREPARED_HOST_LINK_PENDING', 'Wrong assembly phase')
    packaged_dir = project / 'entry/libs/arm64-v8a'
    require('libblender_host.so' not in assembled['native_libraries'] and not (packaged_dir / 'libblender_host.so').exists(),
            'Prebuilt host collides with actual CMake host output')
    require({path.name for path in packaged_dir.iterdir()} == set(assembled['native_libraries']),
            'Appended/missing/unaudited native payload file')
    for row in assembled['files']:
        verify_record(project, row)
    for row in assembled['source_links']:
        file = project / relative(row['path'])
        require(file.is_symlink() and os.readlink(file) == row['target'] and file.resolve(strict=True).is_relative_to(project),
                'Exported source link changed/escaped')
    if assembled.get('offline_hvigor_cache_copy') is not None:
        offline_cache_links.verify_copy_receipt(project, assembled['offline_hvigor_cache_copy'])
    require(digest(sdk / 'oh-uni-package.json') == assembled['sdk_prerequisite']['manifest_sha256'] and
            digest(readelf) == assembled['sdk_prerequisite']['readelf_sha256'], 'Actual SDK/readelf bytes differ from selected prerequisite')
    receipt = load(receipt_path)
    require(receipt['signer']['sha256'] == assembled['tool_fingerprints']['signer'], 'Actual host signer differs from selected prerequisite')
    require(receipt.get('cpp_profile') == 'sdk15-' + assembled['sdk_prerequisite']['bridge_stl'] + '-__n1', 'Actual host STL/compiler profile differs')
    signed = final_sign_receipt(host, receipt, project)
    required = {'entry/src/main/cpp/' + name for name in ('napi_init.cc', 'host_bridge.cc', 'reliable_input.cc', 'xcomponent_input.cc', 'ime_input.cc', 'ime_input.h', 'CMakeLists.txt', 'host.exports.map')}
    required |= {'source/blender/source/creator/creator_ohos.h', 'source/blender/source/creator/creator_ohos_files.h', 'source/blender/intern/ghost/GHOST_OHOSHost.h'}
    require(required <= {row['path'] for row in receipt['source_bindings']}, 'Actual host/source ABI bindings missing')
    values = {}
    for line in cache.read_text().splitlines():
        if line and not line.startswith(('#', '//')) and '=' in line and ':' in line.split('=', 1)[0]:
            key_type, value = line.split('=', 1)
            values[key_type.split(':', 1)[0]] = value
    require(Path(values.get('BLENDER_SOURCE', '')).resolve() == (project / 'source/blender').resolve(), 'Actual CMake cache consumed external source')
    require(Path(values.get('BLENDER_CORE_LIBRARY', '')).resolve() == (project / 'entry/libs/arm64-v8a/libblender_core.so').resolve(), 'Actual CMake cache consumed another core')
    require(values.get('OHOS_STL') == assembled['sdk_prerequisite']['bridge_stl'], 'Actual CMake STL differs')
    compile_record = receipt.get('compile_commands')
    require(isinstance(compile_record, dict), 'Actual host compile_commands evidence required')
    command_file = explicit_path(compile_record['path'], 'actual compile_commands')
    require(digest(command_file) == compile_record['sha256'], 'Actual compile_commands changed')
    commands = load(command_file)
    outputs = evidence.host_compile(commands, receipt, project, sdk)
    _, link, link_cwd, link_argv = evidence.signed_operations(receipt, host)
    require(set(outputs) <= {Path(row['path']).resolve() for row in link['objects']}, 'Actual host link did not consume its recorded four TU objects')
    selected_core = (project / 'entry/libs/arm64-v8a/libblender_core.so').resolve()
    require(any(evidence.resolve(link_cwd, token) == selected_core for token in link_argv if token.endswith('.so') and not token.startswith('-')),
            'Actual host link did not consume selected signed core')
    require(Path(values.get('CMAKE_CXX_COMPILER', '')).resolve() == Path(receipt['compiler']['path']).resolve(), 'Actual CMake compiler differs')
    info = native.audit(host, readelf, 'host')
    if values['OHOS_STL'] == 'c++_shared':
        require('libc++_shared.so' in info['needed'], 'Declared shared STL not observed in actual host ELF')
    else:
        require('libc++_shared.so' not in info['needed'], 'Declared static STL actually depends on shared SDK C++')
    libraries = {name: {'audit': row} for name, row in assembled['native_libraries'].items()}
    libraries['libblender_host.so'] = {'audit': info}
    closure = native.close_dependencies(libraries, set(assembled['system_libraries']))
    result = {'schema': 1, 'status': 'FINAL_NATIVE_INPUTS_AUDITED_HAP_NOT_RUN', 'assembly_seal': seal,
              'host_final_sha256': info['sha256'], 'host_audit': info, 'host_final_sign': signed,
              'actual_CMakeCache_sha256': digest(cache), 'nativeDependencies': closure,
              'no_duplicate_host_copied_to_entry_libs': True,
              'note': 'Host is the actual CMake output; a rebuild/re-sign/change requires a NEW receipt. This does not accept a signed/installed HAP.',
              'complete_portable_project': False, 'gates': assembled['gates']}
    write_new(output, result)
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('plan', 'assemble'):
        command = sub.add_parser(name)
        command.add_argument('--spec', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'assemble':
            command.add_argument('--allow-prepared-rebuild', action='store_true', help='Explicitly export a PREPARED payload with recorded missing rebuild stages; never complete')
    command = sub.add_parser('inventory-source')
    command.add_argument('--source-root', required=True, type=Path)
    command.add_argument('--identity', required=True)
    command.add_argument('--output', required=True, type=Path)
    command = sub.add_parser('audit-host')
    for field in ('project', 'host', 'receipt', 'cmake-cache', 'readelf', 'sdk-root', 'output'):
        command.add_argument('--' + field, required=True, type=Path)
    args = p.parse_args()
    try:
        verify_self_lock(HERE)
        if args.command == 'plan':
            spec = load(args.spec)
            roots = [Path(spec[key]) for key in REQUIRED_PATHS if isinstance(spec.get(key), str) and Path(spec[key]).is_dir()]
            output = metadata_output(args.output, roots)
            result = plan(spec); write_new(output, result)
            print(json.dumps(result, indent=2)); return 2 if result['missing_inputs'] else 0
        if args.command == 'inventory-source':
            source = args.source_root.resolve(strict=True)
            output = ensure_fresh_output(args.output, [source])
            result = source_guard.inventory(source, args.identity)
            write_new(output, result); print(json.dumps({'objects': len(result['files']), 'tree_sha256': result['tree_sha256']})); return 0
        if args.command == 'assemble':
            result = assemble(load(args.spec), args.output, args.allow_prepared_rebuild)
        else:
            result = audit_host(args.project.resolve(strict=True), args.host.resolve(strict=True), args.receipt,
                                args.cmake_cache, args.readelf.resolve(strict=True), args.sdk_root, args.output)
        print(json.dumps(result, indent=2)); return 0
    except (Rejected, OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print('EXPORT REJECTED: ' + str(error), file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())
