# SPDX-License-Identifier: GPL-2.0-or-later
"""Merge only own fresh stdlib/NumPy plus independently verified selected pure8."""
import sys
sys.dont_write_bytecode=True
import importlib.util
import json
from pathlib import Path
import shutil
from source_guard import HERE,REPO,sha,inventory
from io_utils import dump,owned

PURE=REPO/'build_files/ohos/deps_python_resources_sources/resources.py'
PURE_SHA='27aeac5e6fd312c672d09d31c6d937d3fa0bb9b77080017617ff75d4e444536d'


def pure_module():
    spec=importlib.util.spec_from_file_location('native_selected_pure_resources',PURE)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def prepare_selected(args,root,runner):
    root=owned(root)
    if sha(PURE.parent/'sources.lock.json')!=PURE_SHA or sha(HERE/'resources/selected-pure8.lock.json')!=PURE_SHA:raise ValueError('Selected pure8 source lock differs')
    cache=root/'blender-ohos-python-resources-source-cache';pure=root/'blender-ohos-python-resources-pure'
    if cache.exists() or pure.exists():raise ValueError('Pure resources replay requires wholly fresh own subroots')
    for action in ('materialize','prepare','assemble'):
        command=[args.python,PURE,action,'--repo',REPO,'--cache',cache,'--tmp',args.tmp_dir]
        if action=='assemble':command+=['--root',pure]
        runner.run(command,'selected-pure8-'+action)
    result=pure_module().verify_resources(REPO,pure)
    if result['pure_file_count']!=193:raise ValueError('Selected source-complete pure8 file profile differs')
    dump(root/'receipts/selected-pure8-source-assembly.json',{'schema':1,'source_lock_sha256':PURE_SHA,'actual_source_assembly':result,
              'native_full93_with_this_pure8':'NOTRUN until new actual gate','independent_historical_pure8_not_combined_gate':True})
    return pure


def contract(root):
    root=owned(root)
    return {'schema':1,'kind':'python-native-source-resource-contract','native_source_namespace':'build_files/ohos/deps_python_native_sources',
        'source_lock_sha256':sha(HERE/'sources.lock.json'),'inputs_lock_sha256':sha(HERE/'inputs.lock.json'),
        'stdlib_input':'own prepared complete CPython Lib + own fresh generated target config (never old copied headers/config)',
        'numpy_input':'own complete NumPy source + own fresh Meson installed pure files/config and exactly19 signed outputs',
        'pure8_input_lock_sha256':PURE_SHA,'pure8_manifest':'blender-ohos-python-resources-pure/resources.json',
        'stdlib_site_relative':'resources/rawfile/python/lib/python3.13/site-packages','raw_python_home_relative':'resources/rawfile/python',
        'native_relative':'hap-native/libs/arm64-v8a','module_map_relative':'hap-native/python-modules.json',
        'counts':{'runtime_ELF':93,'HAP_DSO':92,'native_map':90,'source_selected_pure':8},
        'genuine_unversioned_SONAME':'libpython3.13.so','single_shared_PyRuntime':True,'HAP_runtime':'embedding/dlopen; no subprocess',
        'fresh_combined_native93_and_pure8':'NOTRUN until actual new native/flat acceptance receipts','installed_HAP':'NOTRUN',
        'historical_93_oldpure5_and_independent_pure8':'provenance only, never substitute new combined actual gate'}


def assemble(root,pure_root=None):
    root=owned(root);pure_root=Path(pure_root) if pure_root else root/'blender-ohos-python-resources-pure'
    verified=pure_module().verify_resources(REPO,pure_root)
    if verified['pure_file_count']!=193:raise ValueError('Pure8 actual assembled profile differs')
    runtime=root/'runtime';stdlib=runtime/'lib/python3.13';site=stdlib/'site-packages'
    for p in (pure_root/'site-packages').iterdir():
        target=site/p.name
        if target.exists() or target.is_symlink():raise ValueError('Pure8 collides with current runtime payload')
        if p.is_dir():shutil.copytree(p,target,symlinks=False)
        else:shutil.copyfile(p,target)
    hap=root/'hap-resources';raw=hap/'resources/rawfile/python/lib/python3.13'
    if hap.exists():raise ValueError('Raw resources require wholly absent own subtree')
    raw.mkdir(parents=True)
    for p in sorted(stdlib.rglob('*')):
        if p.is_symlink():
            # Config archive alias is native/build metadata and never raw data.
            if p.name.endswith('.a'):continue
            raise ValueError('Unexpected installed stdlib resource symlink')
        if not p.is_file():continue
        parts=p.relative_to(stdlib).parts
        if '__pycache__' in parts or p.suffix in ('.so','.a','.pyc','.pyo','.pyd','.dll','.dylib'):continue
        with p.open('rb') as f:
            if f.read(4)==b'\x7fELF':raise ValueError('Unmapped ELF in pure raw resources')
        target=raw/p.relative_to(stdlib);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    notice=hap/'resources/rawfile/python/share/licenses';shutil.copytree(runtime/'share/licenses',notice,symlinks=False)
    payload=inventory(hap)
    if any(Path(row['path']).suffix in ('.so','.a','.pyc','.pyo','.pyd','.dll') for row in payload):raise ValueError('Native/bytecode payload leaked into resources')
    manifest=contract(root);manifest.update(actual_raw_resource_assembly='PASS source bytes staged only',pure8_resources_manifest_sha256=sha(pure_root/'resources.json'),
          inventory=payload,pure_source_files=193,raw_resources_tree_sha256=sha_inventory(payload))
    dump(hap/'resource-manifest.json',manifest);dump(root/'native-source-resource-contract.json',contract(root))
    return manifest


def sha_inventory(records):
    import hashlib
    return hashlib.sha256(json.dumps(records,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
