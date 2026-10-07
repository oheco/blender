#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short text/parser/negative tests only. No successful ELF or project fixture."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.dont_write_bytecode = True
from common import Rejected, copy_exact, digest, ensure_fresh_output, load, object_digest, relative, write_new
import exporter
import evidence
import gltf
import resources
import native
import rebuild
import source_guard

class Guards(unittest.TestCase):
    root = None
    def fresh(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    def test_relative_unicode_space(self):
        self.assertEqual(relative('source/中文 path/file.py'), 'source/中文 path/file.py')
    def test_noncanonical_paths_rejected(self):
        for value in ('', '/a', '../a', 'a/../b', 'a//b', 'a/./b', 'a\\b', 'a\x00b', 'a\nb'):
            with self.subTest(value=value), self.assertRaises(Rejected): relative(value)
    def test_duplicate_json_rejected(self):
        path = self.fresh('duplicate.json'); path.write_text('{"a":1,"a":2}')
        with self.assertRaises(Rejected): load(path)
    def test_output_reuse_rejected(self):
        path = self.fresh('already-output'); path.mkdir()
        with self.assertRaises(Rejected): ensure_fresh_output(path)
    def test_output_outside_cache_rejected(self):
        with self.assertRaises(Rejected): ensure_fresh_output(self.root / 'outside' / 'new')
    def test_copy_exact_bytes_modes_unicode(self):
        source = self.fresh('ordinary source 中文.txt'); source.write_text('ordinary text, no ELF fixture\n'); source.chmod(0o755)
        target = self.fresh('copy path/中文 copied.txt')
        row = copy_exact(source, target, digest(source))
        self.assertEqual(row['sha256'], digest(source)); self.assertEqual(target.stat().st_mode & 0o111, 0o111)
    def test_copy_changed_hash_rejected(self):
        source = self.fresh('changed-source'); source.write_text('changed')
        with self.assertRaises(Rejected): copy_exact(source, self.fresh('rejected-copy'), '0' * 64)
    def test_unresolved_lfs_rejected(self):
        path = self.fresh('lfs-pointer.blend'); path.write_bytes(source_guard.LFS_HEADER + b'\noid sha256:000\nsize 100\n')
        with self.assertRaises(Rejected): source_guard.reject_pointer(path)
    def test_missing_source_layout_rejected(self):
        path = self.fresh('incomplete-source'); path.mkdir()
        with self.assertRaises(Rejected): source_guard.check_layout(path)
    def test_source_manifest_wrong_kind_rejected(self):
        with self.assertRaises(Rejected): source_guard.validate_manifest({'schema': 1, 'kind': 'partial'})
    def test_incomplete_registry_rejected(self):
        path = self.fresh('registry/tpr/sources/tiny/manifest.json')
        path.write_text(json.dumps({'format':'original-archive-parts','size':1,'parts':[{'filename':'source.part0000','size':1,'sha256':'0'*64}]}))
        with self.assertRaises(Rejected): source_guard.verify_registry_records(self.root / 'registry', [{'path':'tpr/sources/tiny/manifest.json','kind':'file'}])
    def test_case_collisions_rejected(self):
        root = self.fresh('case-source'); root.mkdir(); (root / 'FILE').write_text('a'); (root / 'file').write_text('b')
        with self.assertRaises(Rejected): list(source_guard.source_paths(root))
    def test_bare_namespace_system_assumption_rejected(self):
        with self.assertRaises(Rejected): native.close_dependencies({}, {'libc++_shared.so'})
    def test_missing_core_closure_rejected(self):
        with self.assertRaises(Rejected): native.close_dependencies({'libpython3.13.so': {'audit': {'needed': []}}}, {'libc.so'})
    def test_unprovided_dynamic_needed_rejected(self):
        libraries = {'libblender_core.so':{'audit':{'needed':['libmissing.so']}}, 'libpython3.13.so':{'audit':{'needed':[]}}}
        with self.assertRaises(Rejected): native.close_dependencies(libraries, {'libc.so'})
    def test_missing_real_core_rejected_before_copy(self):
        spec = {'schema':1,'bridge_stl':'c++_static'}
        for key in exporter.REQUIRED_PATHS:
            spec[key] = str(self.root)
        spec['core_library'] = str(self.root / 'absent/libblender_core.so')
        output = self.root / 'must-not-exist-project'
        with self.assertRaises(OSError): exporter.assemble(spec, output)
        self.assertFalse(output.exists())
    def test_sdk_profile_missing_rejected(self):
        with self.assertRaises(Rejected): exporter.spec_paths({'schema':1})
    def test_nonelf_rejected_without_readelf_call(self):
        path = self.fresh('plain-not-native.so'); path.write_text('ordinary non-ELF'); path.chmod(0o755)
        with self.assertRaises(Rejected): native.audit(path, self.root / 'not-an-executable', 'core')
    def test_unsigned_native_input_mode_rejected(self):
        path = self.fresh('non-executable.so'); path.write_text('ordinary non-ELF'); path.chmod(0o644)
        with self.assertRaises(Rejected): native.audit(path, self.root / 'not-an-executable', 'core')
    def test_failed_final_sign_receipt_rejected(self):
        with self.assertRaises(Rejected): exporter.final_sign_receipt(self.root / 'absent', {'link_exit_code':0,'sign_exit_code':1}, self.root)
    def test_native_parser_rejection_tokens_only(self):
        # Deliberate text rejection inputs, never an ELF file or native success.
        text = 'Class: ELF64\nMachine: AArch64\nType: DYN\n[ 5] .codesign PROGBITS\n'
        cases = [text.replace('ELF64','ELF32'), text.replace('AArch64','X86-64'), text.replace('DYN','EXEC'),
                 text.replace('[ 5] .codesign PROGBITS',''), text + '(TEXTREL) 0\n',
                 text + '(SONAME) [libpython3.13.so.1.0]\n', text + '(NEEDED) [libpython3.13.so.1.0]\n',
                 text + '(RUNPATH) [/developer/cache]\n', text + '(RUNPATH) [$ORIGIN:]\n',
                 text + '(RUNPATH) [$ORIGIN/../../../../outside]\n', text + '_ZNSt3__h6mutex4lockEv\n']
        for value in cases:
            with self.subTest(value=value), self.assertRaises(Rejected): native.parse_readelf(value)
    def test_rebuild_missing_is_blocked(self):
        result = rebuild.detect(self.root)
        self.assertEqual(result['status'], 'BLOCKED_MISSING_SOURCE_REBUILD_STAGES')
        self.assertFalse(result['complete_portable_rebuild'])
    def test_plan_is_metadata_only(self):
        result = exporter.plan({'source_root': str(self.root)})
        self.assertEqual(result['status'], 'BLOCKED_MISSING_REAL_INPUTS')
        self.assertFalse(result['heavy_source_or_ELF_payloads_read'])
        self.assertEqual(result['native_assembly'], 'NOT_RUN')
    def test_alias_output_into_protected_input_rejected(self):
        cache = self.fresh('alias-cache'); cache.mkdir()
        protected = cache / 'protected-source-or-SDK-or-runtime'; protected.mkdir()
        alias = cache / 'alias'; alias.symlink_to(protected, target_is_directory=True)
        with patch.dict(os.environ, {'XDG_CACHE_HOME': str(cache)}):
            with self.assertRaises(Rejected): ensure_fresh_output(alias / 'new-project', [protected])
    def test_inventory_output_inside_source_rejected(self):
        with self.assertRaises(Rejected): exporter.metadata_output(self.root / 'source-output.json', [self.root])
    def test_rebuild_embedded_paths_rejected(self):
        for token in ('--cache=/old/accepted/cache', '@CACHE/../../outside', '--prefix=@CACHE/a/../b', '@UNKNOWN/root', '--root=relative/old-cache'):
            with self.subTest(token=token): self.assertFalse(rebuild.safe_argument(token))
        self.assertTrue(rebuild.safe_argument('--root=@CACHE/new owned source'))
    def test_unfrozen_recipe_lock_rejected(self):
        entry = self.fresh('unfrozen/entry.py'); entry.write_text('ordinary source')
        lock = self.fresh('unfrozen/lock.json'); lock.write_text('{}')
        state, frozen = rebuild.lock_freeze(self.root, 'unfrozen/entry.py', 'unfrozen/lock.json')
        self.assertFalse(frozen); self.assertIn('NOT_FROZEN', state)
    def test_reserved_host_extra_rejected_before_payload_access(self):
        with self.assertRaises(Rejected): exporter.add_extras({}, {}, {'native_extras':[{'library':'libblender_host.so'}]})
    def test_stale_resource_blob_rejected(self):
        original = self.fresh('incoming-resource'); original.write_text('actual ordinary text')
        blobs = resources.Blobs(self.root / 'raw-stale')
        (blobs.raw / 'runtime').mkdir()
        (blobs.raw / 'runtime' / digest(original)).write_text('stale')
        with self.assertRaises(Rejected): blobs.add(original, '5.2/data/ordinary.txt')
    def test_opaque_response_file_rejected(self):
        with self.assertRaises(Rejected): evidence.expanded(['tool', '@unrecorded.rsp'], self.root)
    def test_compile_wrong_source_and_effective_standard_rejected(self):
        project = self.fresh('command-parser-project'); cpp = project / 'entry/src/main/cpp'; cpp.mkdir(parents=True)
        sdk = self.fresh('command-parser-SDK'); (sdk / 'bin').mkdir(parents=True)
        tool = sdk / 'bin/ordinary-tool-token.txt'; tool.write_text('ordinary parser token, never executed')
        file = cpp / 'napi_init.cc'; file.write_text('ordinary source parser token')
        receipt = {'compiler':{'path':str(tool)}}
        prefix = [str(tool), '--sysroot=' + str(sdk / 'sysroot')]
        cases = [prefix + ['-std=c++17','-c',str(self.root / 'outside.cc'),'-o','object.o'],
                 prefix + ['-std=c++17','-std=c++14','-c',str(file),'-o','object.o']]
        for argv in cases:
            with self.subTest(argv=argv), self.assertRaises(Rejected):
                evidence.host_compile([{'directory':str(project),'file':str(file),'arguments':argv}], receipt, project, sdk)
    def test_structured_operation_wrong_output_rejected(self):
        tool = self.fresh('ordinary-operation-tool.txt'); tool.write_text('ordinary text')
        command = self.fresh('wrong-output-operation.json')
        command.write_text(json.dumps({'cwd':str(self.root),'argv':[str(tool)],'output':str(self.root/'unrelated')}))
        with self.assertRaises(Rejected): evidence.operation({'path':str(command),'sha256':digest(command)}, tool, self.root/'selected')
    def test_signer_input_output_role_mismatch_rejected(self):
        artifact = self.root / 'selected-final.so'
        data = {'input':str(artifact),'output':str(artifact)}
        argv = ['ordinary-tool','sign','-inFile',str(self.root/'other-input.so'),'-outFile',str(artifact),'-selfSign','1']
        with self.assertRaises(Rejected): evidence.signing_roles(data, self.root, argv, artifact)
    def test_joined_sysroot_split_macro_vfs_rejected(self):
        sdk = self.root / 'declared-sdk-token'
        base = ['ordinary-tool','--sysroot=' + str(sdk / 'sysroot')]
        for suffix in (['-isysroot/old/sysroot'], ['-D','_LIBCPP_ABI_NAMESPACE=__h'], ['-U','_LIBCPP_ABI_VERSION'], ['-ivfsoverlay','overlay.json']):
            with self.subTest(suffix=suffix), self.assertRaises(Rejected): evidence.host_routing(base + suffix, self.root, sdk)
    def test_missing_core_implementation_tuples_rejected(self):
        with self.assertRaises(KeyError): evidence.core_implementations({}, Path(self.source_root))
    def test_actual_source_mapping_resolves(self):
        source = Path(self.source_root).resolve(strict=True)
        self.assertEqual(set(evidence.CORE_IMPLEMENTATION_HEADERS), {
            'source/creator/creator.cc', 'intern/ghost/intern/GHOST_OHOSHost.cc',
            'intern/ghost/intern/GHOST_OHOSNative.cc', 'intern/ghost/intern/GHOST_OHOSEngine.cc'})
        for name in evidence.CORE_SOURCE_BINDINGS:
            self.assertTrue((source / name).is_file(), name)
        self.assertTrue(evidence.CORE_SOURCE_BINDINGS <= set(source_guard.REQUIRED))
        text = (source / 'source/creator/creator.cc').read_text()
        self.assertIn('#  include "creator_ohos.h"', text)
        self.assertIn('#  include "creator_ohos_files_impl.hh"', text)
        self.assertIn('#include "creator_intern.h"', text)
        for name in ('initialize', 'pump', 'stop', 'teardown'):
            self.assertIn('extern "C" int32_t blender_ohos_' + name + '(', text)
        self.assertIn('extern "C" int32_t blender_ohos_file_command(', (source/'source/creator/creator_ohos_files_impl.hh').read_text())
    def test_phantom_translation_units_rejected(self):
        for name in ('source/creator/creator_ohos.cc', 'source/creator/creator_ohos_files.cc', 'source/creator/creator_ohos_files_impl.hh'):
            with self.subTest(name=name), self.assertRaises(Rejected): evidence.implementation_headers(name)
    def test_normal_creator_header_cannot_replace_ohos_contract(self):
        source = Path(self.source_root).resolve(strict=True)
        dependencies = {source/'source/creator/creator.cc', source/'source/creator/creator_intern.h', source/'source/creator/creator.h'}
        with self.assertRaises(Rejected): evidence.required_dependency_paths('source/creator/creator.cc', source, dependencies)
    def test_creator_dependency_missing_impl_rejected(self):
        source = Path(self.source_root).resolve(strict=True)
        name = 'source/creator/creator.cc'
        dependencies = {source / value for value in (name, *evidence.implementation_headers(name)) if not value.endswith('creator_ohos_files_impl.hh')}
        with self.assertRaises(Rejected): evidence.required_dependency_paths(name, source, dependencies)
    def test_creator_dependency_missing_abi_rejected(self):
        source = Path(self.source_root).resolve(strict=True)
        name = 'source/creator/creator.cc'
        dependencies = {source / value for value in (name, *evidence.implementation_headers(name)) if not value.endswith('creator_ohos.h')}
        with self.assertRaises(Rejected): evidence.required_dependency_paths(name, source, dependencies)
    def test_creator_output_disconnected_from_link_rejected(self):
        with self.assertRaises(Rejected): evidence.linked_implementation_object(self.root/'actual-creator-output-token.o', 'a'*64, {self.root/'another-output-token.o':'a'*64})
    def test_dependency_record_wrong_object_rejected(self):
        with self.assertRaises(Rejected): evidence.dependency_paths('other.o: source.cc header.h', self.root, self.root/'selected.o', 'make-depfile')
        with self.assertRaises(Rejected): evidence.dependency_paths('other.o: #deps 1, deps mtime 1 (VALID)\n    source.cc\n', self.root, self.root/'selected.o', 'ninja-deps')
    def test_ninja_dependency_stale_or_incomplete_rejected(self):
        for text in ('selected.o: #deps 1, deps mtime 1 (STALE)\n    source.cc\n', 'selected.o: #deps 2, deps mtime 1 (VALID)\n    source.cc\n'):
            with self.subTest(text=text), self.assertRaises(Rejected): evidence.dependency_paths(text, self.root, self.root/'selected.o', 'ninja-deps')
    def test_headers_from_other_make_rule_rejected(self):
        text = 'selected.o: creator.cc\nother.o: creator_ohos.h creator_ohos_files_impl.hh\n'
        with self.assertRaises(Rejected): evidence.dependency_paths(text, self.root, self.root/'selected.o', 'make-depfile')
    def test_make_paths_escaping_and_phony_rules(self):
        # Ordinary text parser input, no synthetic ELF/source-rebuild acceptance.
        text = "selected.o: literal'quote.cc dollar$$name.h escaped\\ space.h\nescaped\\ space.h:\n"
        paths = evidence.dependency_paths(text, self.root, self.root/'selected.o', 'make-depfile')
        self.assertEqual(paths, {self.root/"literal'quote.cc", self.root/'dollar$name.h', self.root/'escaped space.h'})
    def test_core_signer_cannot_replace_linked_artifact(self):
        with self.assertRaises(Rejected): evidence.linked_signer_input({'input':'unrelated.so'}, self.root, self.root/'actual-link-output.so')
    def test_unknown_core_features_rejected(self):
        with self.assertRaises(Rejected): gltf.verify_features({}, self.root, 'full-required')
    def test_copied_profile_relative_to_project(self):
        project = self.fresh('project 中文 with spaces'); (project / 'entry/src/main/cpp').mkdir(parents=True)
        host = Path(self.host_root)
        copy_exact(host / 'deveco/entry/src/main/cpp/CMakeLists.txt', project / 'entry/src/main/cpp/CMakeLists.txt')
        copy_exact(host / 'deveco/entry/build-profile.json5', project / 'entry/build-profile.json5')
        result = exporter.derive_profiles(project, {'bridge_stl': 'c++_static'})
        text = (project / 'entry/src/main/cpp/CMakeLists.txt').read_text()
        self.assertIn('${CMAKE_CURRENT_LIST_DIR}/../../../../source/blender', text)
        self.assertIn('${CMAKE_CURRENT_LIST_DIR}/../../../libs/arm64-v8a/libblender_core.so', text)
        self.assertEqual(len(result), 2)
        self.assertEqual(load(project / 'entry/build-profile.json5')['buildOption']['externalNativeOptions']['arguments'], '-DOHOS_STL=c++_static')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--host-root', required=True)
    parser.add_argument('--source-root', required=True, help='Actual source filesystem for scoped TU/API/include checks only')
    args = parser.parse_args()
    output = io.StringIO()
    with tempfile.TemporaryDirectory(prefix='deveco-export-guards-', dir=os.environ['TMPDIR']) as directory:
        Guards.root = Path(directory)
        Guards.host_root = args.host_root
        Guards.source_root = str(Path(args.source_root).resolve(strict=True))
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Guards)
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    record = {'schema':1,'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,
              'failures':len(result.failures),'errors':len(result.errors),'log':output.getvalue(),
              'scope':'Original37 ordinary text/path/command rejection guards plus actual source TU/API/include checks and phantom-TU/missing-impl/degraded-header/disconnected-object/dep-rule/signer-chain regressions. No ELF fixture, actual object/archive processing or successful native/project/HAP assertion.',
              'source_root':Guards.source_root, 'source_layout_only':True,
              'temporary_tree_cleaned':not Path(directory).exists(),'native_assembly':'NOT_RUN','HAP':'NOT_RUN'}
    write_new(args.output, record)
    print(output.getvalue()); print(json.dumps({k:v for k,v in record.items() if k != 'log'}, indent=2))
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    raise SystemExit(main())
