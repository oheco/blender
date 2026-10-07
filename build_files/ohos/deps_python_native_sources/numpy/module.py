# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent offline native NumPy replay; common parent owns sourceprep/root/toolchain.
plan reads inputs and returns argv only. build executes only when explicitly selected.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

HERE=Path(__file__).resolve().parent


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _load(name):return json.loads((HERE/name).read_text())


def verify_inputs(expected_lock_sha256=None):
    """Independent module seal; the parent must anchor this lock hash externally."""
    lock=HERE/'files.lock.json';digest=_sha(lock)
    if expected_lock_sha256 is not None and digest!=expected_lock_sha256:raise ValueError('NumPy module seal changed')
    rows=_load('files.lock.json')['files'];expected=set()
    for row in rows:
        p=Path(row['path'])
        if p.is_absolute() or '..' in p.parts or str(p) in expected:raise ValueError('Noncanonical module seal path')
        expected.add(str(p));file=HERE/p
        if file.is_symlink() or not file.is_file() or file.stat().st_size!=row['size'] or _sha(file)!=row['sha256']:
            raise ValueError('Sealed NumPy module input changed: '+str(p))
    actual=set()
    for file in HERE.rglob('*'):
        if file.is_symlink():raise ValueError('Source module links rejected')
        if file.is_file() and file.relative_to(HERE).as_posix() not in ('files.lock.json','frozen-candidate.json','SHA256SUMS'):
            actual.add(file.relative_to(HERE).as_posix())
        elif not file.is_file() and not file.is_dir():raise ValueError('Source module special object rejected')
    if actual!=expected:raise ValueError('NumPy source module file set changed')
    return {'files':len(rows),'files_lock_sha256':digest,'gate':'source inputs only'}


def _path(value):
    p=Path(value)
    if not p.is_absolute() or '..' in p.parts or any(c in str(p) for c in '\x00\n\r'):
        raise ValueError('Require explicit absolute path without parent traversal/control characters')
    return p


def _layout(args,root,runtime_prefix):
    root=_path(root);runtime_prefix=_path(runtime_prefix);work=root/'numpy'
    realroot=root.resolve();realruntime=runtime_prefix.resolve();realwork=work.resolve()
    if not realwork.is_relative_to(realroot) or not realruntime.is_relative_to(realroot) or realruntime==realroot or realwork.is_relative_to(realruntime) or realruntime.is_relative_to(realwork):
        raise ValueError('NumPy must consume a distinct NEW Python prefix inside this owned fresh parent root')
    lock=_load('sources.lock.json');sources={}
    if set(args.numpy_sources)!={e['registry_id'] for e in lock['sources']}:raise ValueError('Exactly the 12 sealed complete source sdists required')
    for e in lock['sources']:
        p=_path(args.numpy_sources[e['registry_id']]);real=p.resolve()
        if not real.is_relative_to(realroot) or real.is_relative_to(realwork) or real.is_relative_to(realruntime):
            raise ValueError('Use common new prepared sources, never old source/build/runtime cache trees')
        sources[e['registry_id']]=p
    if args.jobs not in (1,2):raise ValueError('Native NumPy jobs must be 1 or 2; LLD threads remains 1')
    tools={name:_path(getattr(args,name)) for name in ('sdk_root','cc','cxx','lld','ar','strip','ninja','signer','readelf','tmp_dir')}
    if tools['lld'].name!='ld.lld':raise ValueError('Preserve actual ld.lld invocation basename')
    expected={'cc':'clang','cxx':'clang++','lld':'ld.lld','ar':'llvm-ar','strip':'llvm-strip','readelf':'llvm-readelf'}
    for role,filename in expected.items():
        if tools[role].name!=filename or tools[role].resolve()!=(tools['sdk_root']/'llvm/bin'/filename).resolve():
            raise ValueError('Use the explicit accepted SDK15 '+filename+', not an overlay/LLVM20 compiler')
    return root,runtime_prefix,work,sources,tools


def verify_sdk(args):
    """Byte inspection only; common parent observes real versions/macros in native full."""
    sdk=_path(args.sdk_root);seen={}
    for row in _load('sdk15.lock.json')['files']:
        file=sdk/row['sdk_relative'];key=str(file.resolve())
        if key not in seen:seen[key]=_sha(file)
        if file.stat().st_size!=row['size'] or seen[key]!=row['sha256']:
            raise ValueError('Accepted SDK15 input changed: '+row['sdk_relative'])
    return {'profile':'accepted SDK Clang15.0.4 bytes','pins':len(_load('sdk15.lock.json')['files']),'native_probe':'NOTRUN'}


def _quote(value):return "'"+str(value).replace('\\','\\\\').replace("'","\\'")+"'"


def plan(args,root,runtime_prefix):
    root,runtime_prefix,work,sources,t=_layout(args,root,runtime_prefix)
    python=runtime_prefix/'bin/python3.13';source=work/'work/numpy-2.3.4';meson=source/'vendored-meson/meson/meson.py';cython=sources['cython-3.0.11']/'cython.py'
    config=work/'compiler.json';launch=work/'launchers';ini=work/'native.ini';prefix=work/'install'
    base=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(t['sdk_root']/'sysroot'),'--ld-path='+str(t['lld']),'-Wl,--threads=1']
    compiler={'compilers':{'c':str(t['cc']),'cpp':str(t['cxx'])},'flags':{'c':base,'cpp':[*base,'-static-libstdc++']},
              'runtime_prefix':str(runtime_prefix),'signer':str(t['signer']),'readelf':str(t['readelf']),'tmp_dir':str(t['tmp_dir'])}
    launchers={}
    for name,language in [('cc','c'),('cxx','cpp')]:
        launchers[name]='#!/usr/bin/sh\nexec '+shlex.join([str(python),str(HERE/'sign_compiler.py'),str(config),language,'--'])+' "$@"\n'
    launchers['cython']='#!/usr/bin/sh\nexec '+shlex.join([str(python),str(cython)])+' "$@"\n'
    # Explicit Ninja is also selected by Meson via this own launcher PATH, never an inherited tool.
    launchers['ninja']='#!/usr/bin/sh\nexec '+shlex.quote(str(t['ninja']))+' "$@"\n'
    ini_text='[binaries]\n'+''.join(name+' = '+_quote(path)+'\n' for name,path in [
        ('c',launch/'cc'),('cpp',launch/'cxx'),('cython',launch/'cython'),('python',python),('ar',t['ar']),('strip',t['strip'])])
    boot=[]
    for e in _load('sources.lock.json')['sources']:
        if e['name'] not in ('numpy','meson'):boot.extend([str(sources[e['registry_id']]/'src'),str(sources[e['registry_id']])])
    env={'PYTHONPATH':os.pathsep.join(boot),'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
         'NUMPY_PYTHON_PREFIX':str(runtime_prefix),'PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1',
         'TMPDIR':str(t['tmp_dir']),'MESON_NUM_PROCESSES':str(args.jobs),'CMAKE_BUILD_PARALLEL_LEVEL':str(args.jobs),
         'LC_ALL':'C','PATH':os.pathsep.join([str(launch),str(t['sdk_root']/'llvm/bin'),'/usr/bin'])}
    identity='import sys,sysconfig,json; v=[list(sys.version_info[:3]),sys.platform,sysconfig.get_config_var("SOABI"),sysconfig.get_config_var("INSTSONAME"),bool(sys._is_gil_enabled())]; assert v==[[3,13,13],"ohos","cpython-313-aarch64-linux-ohos","libpython3.13.so",True],v; print(json.dumps(v))'
    def step(label,argv):return {'label':label,'argv':list(map(str,argv)),'cwd':str(work),'extra_env':env}
    steps=[step('numpy-runtime-identity',[python,'-I','-c',identity]),step('numpy-cython-version',[python,cython,'--version']),
           step('numpy-vendored-meson-version',[python,meson,'--version']),
           step('numpy-configure',[python,meson,'setup',work/'build',source,'--native-file',ini,'--prefix',prefix,'--libdir','lib',
                                  '--buildtype','release','--wrap-mode','nodownload','-Dstrip=false','-Ddebug=false','-Dblas=none','-Dlapack=none',
                                  '-Dallow-noblas=true','-Dpython.bytecompile=0','-Dpython.platlibdir=lib/python3.13/site-packages',
                                  '-Dpython.purelibdir=lib/python3.13/site-packages']),
           step('numpy-build',[t['ninja'],'-C',work/'build','-j'+str(args.jobs)]),
           step('numpy-install-staged',[python,meson,'install','-C',work/'build','--no-rebuild','--destdir','<unique private TMPDIR stage>']),
           step('numpy-acceptance',[python,'-I',HERE/'acceptance.py','--runtime-prefix',runtime_prefix,'--numpy-prefix',prefix,
                                    '--libpython',runtime_prefix/'lib/libpython3.13.so','--expected-executable',python,'--report',work/'validation.json'])]
    return {'schema_version':1,'execution':'NOTRUN, pure source/argv plan','root':str(root),'numpy_root':str(work),
            'runtime_prefix':str(runtime_prefix),'source_paths':{k:str(v) for k,v in sources.items()},'source_copy':{'from':str(sources['numpy-2.3.4']),'to':str(source)},
            'compiler_config':compiler,'native_ini':ini_text,'launchers':launchers,'steps':steps,'extension_count':19,
            'source_lock_sha256':_sha(HERE/'sources.lock.json'),'patch_record_sha256':_sha(HERE/'patches/patch-record.json'),
            'profile_sha256':_sha(HERE/'profile.json'),'offline':'All 12 complete formal sdists guarded by common sourceprep; pure source imports, no wheels/fetch',
            'publication':'Meson depfixer only new never-executed TMPDIR copies, final signatures then same-filesystem atomic install publish'}


def _write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def _elf_module():
    import importlib.util
    spec=importlib.util.spec_from_file_location('_numpy_replay_elf',HERE/'elf.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def build(args,runner,root,runtime_prefix):
    """Future parent full only. No accepted source/cache/binary/runtime reads are fallbacks."""
    if not callable(getattr(args,'numpy_verify_sources',None)):raise ValueError('Common complete source guard callback required')
    module_seal=verify_inputs()
    p=plan(args,root,runtime_prefix);verify_sdk(args);args.numpy_verify_sources()
    try:
        result=_build_owned(args,runner,root,runtime_prefix,p,module_seal)
    except BaseException as primary:
        try:args.numpy_verify_sources()
        except BaseException as guard_error:
            primary.add_note('Common NumPy after-failure source guard also failed: '+repr(guard_error))
        raise
    args.numpy_verify_sources()
    _write(Path(result['numpy_prefix']).parent/'build-receipt.json',result)
    return result


def _build_owned(args,runner,root,runtime_prefix,p,module_seal):
    root,runtime_prefix,work,sources,t=_layout(args,root,runtime_prefix);elf=_elf_module()
    if work.exists() or work.is_symlink():raise ValueError('NumPy replay stage requires an absent own fresh subtree; no partial-cache resume')
    if not (runtime_prefix/'bin/python3.13').is_file() or (runtime_prefix/'lib/libpython3.13.so').is_symlink() or any((runtime_prefix/'lib').glob('libpython3.13.so.*')):
        raise ValueError('Genuine fresh native unversioned Python required; no renamed/aliased old versioned library')
    runtime_elf=elf.python_library(runtime_prefix/'lib/libpython3.13.so')
    for row in _load('patches/patch-record.json')['files']:
        if _sha(sources['numpy-2.3.4']/row['path'])!=row['after_sha256']:raise ValueError('Common NumPy Meson patch state is not exact')
    work.mkdir();_write(work/'.numpy-replay-owned.json',{'module_sha256':_sha(HERE/'module.py'),'plan':p,'runtime_elf':runtime_elf})
    (work/'work').mkdir();shutil.copytree(sources['numpy-2.3.4'],work/'work/numpy-2.3.4',symlinks=True)
    (work/'launchers').mkdir();_write(work/'compiler.json',p['compiler_config']);(work/'native.ini').write_text(p['native_ini'])
    for name,text in p['launchers'].items():
        file=work/'launchers'/name;file.write_text(text);file.chmod(0o755)
    # Runner must remove inherited Python/compiler/loader variables before merging explicit env; common parent owns this.
    for step in p['steps'][:5]:runner.run(step['argv'],step['label'],cwd=Path(step['cwd']),extra_env=step['extra_env'])
    options=json.loads((work/'build/meson-info/intro-buildoptions.json').read_text());actual={row['name']:row['value'] for row in options}
    for key,value in _load('profile.json')['accepted_profile'].items():
        if actual.get(key)!=value:raise ValueError('Actual NumPy feature profile differs: '+key)
    prefix=work/'install';publish=work/'ready-runtime'
    with tempfile.TemporaryDirectory(prefix='numpy-source-replay-install-',dir=t['tmp_dir']) as td:
        stage=Path(td);step=p['steps'][5];command=step['argv'][:-1]+[str(stage)]
        runner.run(command,step['label'],cwd=work,extra_env=step['extra_env'])
        staged=stage/prefix.relative_to('/')
        if not staged.is_dir():raise ValueError('Actual complete NumPy Meson DESTDIR missing')
        libraries=sorted(staged.rglob('*.so'))
        if len(libraries)!=19:raise ValueError('Actual source build must install all 19 NumPy extensions')
        audit=[]
        for index,file in enumerate(libraries):
            # Signed build ELF may be altered by Meson depfixer; final sign follows every such edit.
            signed=stage/('signed-'+str(index))
            runner.run([str(t['signer']),'sign','-inFile',str(file),'-outFile',str(signed),'-selfSign','1'],
                       'numpy-final-sign-'+str(index),cwd=work,extra_env={'TMPDIR':str(t['tmp_dir'])})
            if not signed.is_file():raise ValueError('Final signer returned no output')
            signed.chmod(0o755);file.unlink();shutil.move(str(signed),str(file));record=elf.extension(file)
            record['path']=file.relative_to(staged).as_posix();audit.append(record)
        notices=staged/'share/licenses/numpy';notices.mkdir(parents=True)
        for e in _load('sources.lock.json')['sources']:
            target=notices/e['registry_id'];target.mkdir()
            for rel in e['license_files']:
                file=target/rel;file.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(sources[e['registry_id']]/rel,file)
        for name in ('sources.lock.json','sdk15.lock.json','profile.json'):shutil.copyfile(HERE/name,notices/name)
        shutil.copyfile(t['sdk_root']/'NOTICE.txt',notices/'NOTICE.sdk-static-libcxx15.txt')
        if prefix.exists() or publish.exists():raise ValueError('Refuse existing NumPy publication prefix')
        shutil.copytree(staged,publish,symlinks=True)
    os.replace(publish,prefix)
    step=p['steps'][6];runner.run(step['argv'],step['label'],cwd=work,extra_env=step['extra_env'])
    if _sha(runtime_prefix/'lib/libpython3.13.so')!=runtime_elf['sha256']:
        raise ValueError('NEW Python library bytes changed during NumPy replay')
    result={'numpy':'2.3.4','new_runtime_prefix':str(runtime_prefix),'numpy_prefix':str(prefix),'actual_extensions':audit,'module_seal':module_seal,
            'source_guard_before_after':'PASS','native_acceptance_sha256':_sha(work/'validation.json'),
            'source_lock_sha256':p['source_lock_sha256'],'patch_record_sha256':p['patch_record_sha256'],'SDK15':'exact byte pins',
            'HAP_install_execution':'NOTRUN separate installed-app gate','claim':'Own fresh native stage only; no full NumPy suite/Blender/optimized BLAS claim'}
    return result
