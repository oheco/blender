#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source-only authoring checks. No NumPy/old/new runtime/compiler/tool executions."""
import sys
sys.dont_write_bytecode=True
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tarfile
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent


def load(name):
    spec=importlib.util.spec_from_file_location('_numpy_source_check_'+name,HERE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(original_archive=None):
    results=[]
    def check(name,fn):
        fn();results.append({'check':name,'result':'PASS','gate':'source/Python plan only'})
    def reject(name,fn):
        try:fn()
        except ValueError:results.append({'check':name,'result':'PASS','gate':'source/Python plan negative only'})
        else:raise AssertionError('Required rejection absent: '+name)
    module=load('module');elf=load('elf')
    def syntax():
        for file in HERE.glob('*.py'):compile(ast.parse(file.read_text(),filename=str(file)),str(file),'exec')
    check('all authored Python AST+compile (no script execution)',syntax)
    original=json.loads((HERE/'evidence/accepted-sources.lock.json').read_text());locked=json.loads((HERE/'sources.lock.json').read_text())
    check('12 original formal source entries exact accepted lock',lambda:require(len(locked['sources'])==12 and locked['sources']==original['sources']))
    check('original patch bytes exact accepted SHA',lambda:require(sha(HERE/'patches/numpy-vendored-meson-ohos.patch')=='22d6830e3151fa567e5a9cea88e914592b20c810abb88a8c3c39704800cb94ca'))
    sdk=Path('/storage/Users/currentUser/.oheco/packages/ohos-sdk-native/26.0.0.35-Beta');root=Path('/data/storage/el2/base/haps/entry/cache/blender-ohos-python-native-numpy-plan-only-1')
    moved=Path('/data/storage/el2/base/haps/entry/cache/blender-ohos-python-native-迁移 NumPy source plan with spaces-1')
    args=SimpleNamespace(sdk_root=sdk,cc=sdk/'llvm/bin/clang',cxx=sdk/'llvm/bin/clang++',lld=sdk/'llvm/bin/ld.lld',
                         ar=sdk/'llvm/bin/llvm-ar',strip=sdk/'llvm/bin/llvm-strip',readelf=sdk/'llvm/bin/llvm-readelf',
                         ninja=Path('/storage/Users/currentUser/.oheco/bin/ninja'),signer=Path('/storage/Users/currentUser/.oheco/bin/binary-sign-tool'),
                         tmp_dir=Path('/data/storage/el2/base/haps/entry/temp'),jobs=1,
                         python=Path('/storage/Users/currentUser/.oheco/packages/python3/3.14.7-ohos.1/bin/python3'))
    args.numpy_sources={e['registry_id']:root/'sources'/e['directory'] for e in locked['sources']}
    before=(root.exists(),moved.exists());require(before==(False,False),'Pure plan fixture roots must be absent')
    p=module.plan(args,root,root/'runtime')
    check('native configure/build/acceptance argv is only a NOTRUN plan',lambda:require(p['execution'].startswith('NOTRUN')))
    check('all runtime/Meson/Cython/acceptance launches consume NEW Python',lambda:require(all(s['argv'][0]==str(root/'runtime/bin/python3.13') for s in p['steps'] if s['label']!='numpy-build')))
    check('Ninja explicit and SDK15 ELF tools native.ini',lambda:require(p['steps'][4]['argv'][0]==str(args.ninja) and str(sdk/'llvm/bin/llvm-ar') in p['native_ini']))
    setup=p['steps'][3]['argv']
    check('exact source-complete native no-BLAS/LAPACK-lite profile',lambda:require(all(x in setup for x in ['-Dblas=none','-Dlapack=none','-Dallow-noblas=true','-Dstrip=false','-Dpython.bytecompile=0'])))
    check('vendored Meson used; Meson1.9 formal sdist still sealed',lambda:require('/vendored-meson/meson/meson.py' in setup[1] and 'meson-1.9.0' in p['source_paths']))
    env=p['steps'][3]['extra_env']
    check('offline source tool imports and real probes retained',lambda:require(env['PIP_NO_INDEX']=='1' and 'nodownload' in setup and '[host_machine]' not in p['native_ini'] and 'exe_wrapper' not in p['native_ini']))
    check('full source-tool src/root bootstrap excludes NumPy/standalone Meson',lambda:require(all(str(args.numpy_sources[e['registry_id']]) in env['PYTHONPATH'] for e in locked['sources'] if e['name'] not in ('numpy','meson')) and str(args.numpy_sources['numpy-2.3.4']) not in env['PYTHONPATH'] and str(args.numpy_sources['meson-1.9.0']) not in env['PYTHONPATH']))
    move_args=copy.copy(args);move_args.numpy_sources={e['registry_id']:moved/'sources'/e['directory'] for e in locked['sources']};mp=module.plan(move_args,moved,moved/'runtime')
    check('Unicode/space relocated plan owns only new source/runtime routes',lambda:require(str(moved) in mp['native_ini'] and str(root) not in json.dumps(mp,ensure_ascii=False)))
    check('planning creates no roots, source trees, launchers or receipts',lambda:require((root.exists(),moved.exists())==before))
    reject('reject parent path traversal',lambda:module.plan(args,root/'../escape',root/'runtime'))
    reject('reject old external accepted runtime',lambda:module.plan(args,root,Path('/data/storage/el2/base/haps/entry/cache/blender-ohos-python-hap/runtime')))
    reject('reject NumPy/runtime output overlap',lambda:module.plan(args,root,root/'numpy/runtime'))
    missing=copy.copy(args);missing.numpy_sources=dict(args.numpy_sources);missing.numpy_sources.pop('wheel-0.45.1')
    reject('reject partial source tool graph',lambda:module.plan(missing,root,root/'runtime'))
    old=copy.copy(args);old.numpy_sources=dict(args.numpy_sources);old.numpy_sources['numpy-2.3.4']=Path('/data/storage/el2/base/haps/entry/cache/blender-ohos-python-hap/numpy/sources/numpy-2.3.4')
    reject('reject old accepted source borrowing',lambda:module.plan(old,root,root/'runtime'))
    overlay=copy.copy(args);overlay.cc=Path('/data/storage/el2/base/haps/entry/cache/blender-ohos-toolchain/install-llvm20/bin/clang')
    reject('reject LLVM20 substitution',lambda:module.plan(overlay,root,root/'runtime'))
    wrong=copy.copy(args);wrong.lld=sdk/'llvm/bin/lld'
    reject('preserve real ld.lld invocation basename',lambda:module.plan(wrong,root,root/'runtime'))
    busy=copy.copy(args);busy.jobs=8
    reject('reject jobs above accepted bounded budget',lambda:module.plan(busy,root,root/'runtime'))
    reject('reject truncated/non-ELF bytes without loader execution',lambda:elf.inspect_bytes(b'not native ELF'))
    fake=b'\x7fELF\x02\x01\x01'+bytes(9)+struct.pack('<HHIQQQIHHHHHH',3,62,1,0,0,64,0,64,0,0,64,1,0)+bytes(64)
    reject('reject non-AArch64 ELF parser bytes',lambda:elf.inspect_bytes(fake))
    bad=bytearray(fake);bad[18:20]=struct.pack('<H',183);bad[40:48]=struct.pack('<Q',2**63)
    reject('reject ELF table range escape',lambda:elf.inspect_bytes(bytes(bad)))
    tree=ast.parse((HERE/'acceptance.py').read_text())
    check('HAP acceptance source has no subprocess/os-system imports or calls',lambda:require(not any(isinstance(n,ast.Import) and any(a.name in ('subprocess','os') for a in n.names) or isinstance(n,ast.ImportFrom) and n.module in ('subprocess','os') for n in ast.walk(tree))))
    # Pure control-flow regression: native execution, SDK/source validation and receipt I/O
    # are replaced with in-memory functions. This proves callback ordering only.
    saved={name:getattr(module,name) for name in ('verify_inputs','verify_sdk','_build_owned','_write')}
    try:
        module.verify_inputs=lambda:{};module.verify_sdk=lambda a:{}
        calls=[];writes=[];guard_args=copy.copy(args)
        guard_args.numpy_verify_sources=lambda:calls.append('guard')
        module._write=lambda path,value:writes.append((path,value))
        primary=RuntimeError('synthetic Python control-flow failure, no native execution')
        def failed(*a):raise primary
        module._build_owned=failed
        try:module.build(guard_args,None,root,root/'runtime')
        except RuntimeError as error:require(error is primary)
        else:raise AssertionError('Expected synthetic failure')
        check('after guard on failed full control flow; no receipt published',lambda:require(calls==['guard','guard'] and not writes))
        calls.clear();primary=RuntimeError('synthetic primary')
        def guard_cofailure():
            calls.append('guard')
            if len(calls)==2:raise ValueError('synthetic after guard failure')
        guard_args.numpy_verify_sources=guard_cofailure
        try:module.build(guard_args,None,root,root/'runtime')
        except RuntimeError as error:require(error is primary and len(error.__notes__)==1)
        else:raise AssertionError('Expected preserved synthetic primary failure')
        check('after-guard cofailure preserves original failure',lambda:require(calls==['guard','guard'] and not writes))
        calls.clear();guard_args.numpy_verify_sources=lambda:calls.append('guard')
        module._build_owned=lambda *a:{'numpy_prefix':str(root/'numpy/install')}
        module.build(guard_args,None,root,root/'runtime')
        check('success receipt only after completed after-guard',lambda:require(calls==['guard','guard'] and len(writes)==1))
        calls.clear();writes.clear();guard_args.numpy_verify_sources=guard_cofailure
        try:module.build(guard_args,None,root,root/'runtime')
        except ValueError:pass
        else:raise AssertionError('Expected failed synthetic final guard')
        check('failed final source guard prevents native PASS receipt',lambda:require(calls==['guard','guard'] and not writes))
    finally:
        for name,value in saved.items():setattr(module,name,value)
    if original_archive is not None:
        original_archive=Path(original_archive)
        check('original complete NumPy tar SHA',lambda:require(sha(original_archive)==locked['sources'][0]['sha256']))
        patches=[('vendored-meson/meson/mesonbuild/environment.py',"def detect_system() -> str:\n    if sys.platform == 'cygwin':\n", "def detect_system() -> str:\n    # CPython and compiler identify the native target honestly as OHOS.\n    if sys.platform == 'ohos' or platform.system() in ('HarmonyOS', 'OpenHarmony', 'OHOS'):\n        return 'ohos'\n    if sys.platform == 'cygwin':\n"),
                 ('vendored-meson/meson/mesonbuild/dependencies/python.py','            libdirs = []\n\n        largs =','            libdirs = []\n            if self.platform.startswith(\'ohos-\'):\n                # Relocatable OHOS Python is not installed in the SDK/system prefix.\n                libdirs = [self.variables[\'LIBDIR\']]\n\n        largs =')]
        record=json.loads((HERE/'patches/patch-record.json').read_text())
        with tarfile.open(original_archive,'r:gz') as tar:
            fallback=json.loads((HERE/'profile.json').read_text())['source_fallback_evidence']['original_file_inventory']
            def complete_fallback():
                require(len(fallback)==9)
                for row in fallback:
                    data=tar.extractfile('numpy-2.3.4/'+row['path']).read()
                    require(len(data)==row['size'] and hashlib.sha256(data).hexdigest()==row['sha256'])
            check('complete eight bundled BLAS/LAPACK-lite source hashes and actual selecting meson.build',complete_fallback)
            for rel,before,after in patches:
                data=tar.extractfile('numpy-2.3.4/'+rel).read();row=next(r for r in record['files'] if r['path']==rel)
                def contract(data=data,row=row,before=before,after=after):
                    require(hashlib.sha256(data).hexdigest()==row['before_sha256']);text=data.decode();require(text.count(before)==1)
                    patched=text.replace(before,after,1).encode();require(hashlib.sha256(patched).hexdigest()==row['after_sha256'])
                    require(patched.decode().replace(after,before,1).encode()==data)
                check('exact forward/reverse pure-source patch contract '+rel,contract)
    return {'schema_version':1,'classification':'SOURCE/PYTHON/READ-ONLY ARGV PLAN ONLY','result':'PASS','checks':results,
            'authoring_python':sys.version,'source_roots_created':False,'temp_files_created':False,
            'actual_sourceprep_full_inventories':'Parent common sourceguard responsibility; not a shortcut to source PASS',
            'new_native_configure_compile_probe':'NOTRUN','old_or_new_NumPy_runtime_tests':'NOTRUN','real_HAP_install_execution':'NOTRUN'},p,mp


def require(condition,message='source contract assertion failed'):
    if not condition:raise AssertionError(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--original-archive');parser.add_argument('--output-dir')
    args=parser.parse_args();out=Path(args.output_dir).resolve() if args.output_dir else None
    names=['short-checks.json','original-plan.json','unicode-space-plan.json']
    if out is not None:
        if out!=HERE:raise ValueError('This delegated check may write only new files in its NumPy namespace')
        for name in names:
            if (out/name).exists() or (out/name).is_symlink():raise ValueError('Do not overwrite source receipts')
    result,p,mp=run(args.original_archive)
    if out is not None:
        for name,value in zip(names,[result,p,mp]):(out/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'result':result['result'],'source_only_checks':len(result['checks']),'native_compile_configure_probe':'NOTRUN','NumPy_runtime':'NOTRUN','HAP':'NOTRUN'},indent=2))


if __name__=='__main__':main()
