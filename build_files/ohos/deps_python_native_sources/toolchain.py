# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual SDK15 native identity, explicit immutable tools and linked-ELF signing."""
import sys
sys.dont_write_bytecode=True
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tempfile
from source_guard import HERE,sha,inventory
from io_utils import clean_env,dump,Runner


def fixed():return json.loads((HERE/'tools.lock.json').read_text())


def byte_check(path,pin):
    p=Path(path)
    if not p.is_absolute() or not p.is_file() or p.stat().st_size!=pin['size'] or sha(p)!=pin['sha256']:
        raise ValueError('Explicit tool/SDK bytes differ: '+str(p))
    # Keep ld.lld/clang++ invocation names even when their bytes are multicall aliases.
    return str(p.absolute())


def validate(args,runner=None):
    if platform.system() not in ('HarmonyOS','OHOS','OpenHarmony') or platform.machine()!='aarch64':raise ValueError('Real native HarmonyOS/aarch64 required')
    pins=fixed();tools={}
    for slot,pin in pins['tools'].items():tools[slot]=byte_check(getattr(args,slot),pin)
    sdk=Path(args.sdk_root)
    if not sdk.is_absolute() or not sdk.is_dir():raise ValueError('Explicit SDK root required')
    expected_resource=sdk/pins['resource_relative']
    if Path(args.resource_dir).resolve()!=expected_resource.resolve():raise ValueError('Use actual SDK15 resource directory')
    for row in pins['sdk_files']:byte_check(sdk/row['path'],row)
    for name,pin in pins['sdk_tree_inventories'].items():
        path=sdk/name
        if inventory(path)!=json.loads((HERE/pin['inventory']).read_text()):raise ValueError('SDK header/resource closure drift: '+name)
    for row in pins['regen_closure']:
        prefix=Path(args.regen_python).parent.parent
        byte_check(prefix/row['path'],row)
    gen=pins['regen_prefix_inventory'];gen_file=HERE/gen['inventory']
    if sha(gen_file)!=gen['sha256'] or inventory(Path(args.regen_python).parent.parent)!=json.loads(gen_file.read_text()):raise ValueError('Complete explicit native generator prefix drift')
    env=clean_env(args.tmp_dir)
    env.update(PATH=str(sdk/'llvm/bin')+':'+str(Path(args.make).parent)+':'+str(Path(args.ninja).parent)+':'+env['PATH'],
               PERL5LIB=str(Path(args.perl).parent.parent/'lib/5.44.0')+':'+str(Path(args.perl).parent.parent/'lib/5.44.0/aarch64-linux'),
               PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
    def run(argv,label):
        if runner:return runner.run(argv,label,extra_env={k:env[k] for k in ('PATH','PERL5LIB','PIP_NO_INDEX','PIP_DISABLE_PIP_VERSION_CHECK')})
        return subprocess.check_output(list(map(str,argv)),env=env,text=True,stderr=subprocess.STDOUT)
    versions={slot:run(argv,'readonly-'+slot) for slot,argv in {
        'cc':[args.cc,'--version'],'lld':[args.lld,'--version'],'make':[args.make,'--version'],
        'ninja':[args.ninja,'--version'],'perl':[args.perl,'-e','print $^V'],
        'python':[args.python,'--version'],'regen_python':[args.regen_python,'--version'],'git':[args.git,'--version']}.items()}
    for slot,text in {'cc':'clang version 15.0.4','lld':'LLD 15.0.4','make':'GNU Make 4.4.1','ninja':'1.13.2',
                       'perl':'v5.44.0','python':'Python 3.14.7','regen_python':'Python 3.13.13'}.items():
        if text not in versions[slot]:raise ValueError('Actual native tool version mismatch: '+slot)
    flags=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(sdk),'-resource-dir='+str(args.resource_dir)]
    flags[1]='--sysroot='+str(sdk/'sysroot')
    macros=run([args.cc,*flags,'-dM','-E','-x','c','/dev/null'],'c-preprocessor')
    required={'__OHOS__':'1','__aarch64__':'1','__clang_major__':'15'}
    for name,value in required.items():
        if f'#define {name} {value}' not in macros:raise ValueError('Actual native compiler macro missing: '+name)
    cpp=run([args.cxx,*flags,'-std=c++17','-dM','-E','-x','c++','-include','__config','/dev/null'],'cpp-preprocessor')
    for line in ('#define _LIBCPP_ABI_NAMESPACE __n1','#define _LIBCPP_VERSION 15004'):
        if line not in cpp:raise ValueError('Actual SDK15 libc++ identity differs')
    return {'schema':1,'result':'PASS readonly tools/bytes/preprocessor only','versions':versions,'tools':tools,
            'native_macros':required,'libcxx_ABI':'__n1','libcxx_version':15004,'native_configure':'NOTRUN',
            'native_probe_executables':'NOTRUN','native_full93':'NOTRUN','HAP':'NOTRUN'},env


def launchers(args,root,env):
    root=Path(root);directory=root/'launchers';directory.mkdir(exist_ok=False)
    env=dict(env)
    env.update(OHOS_BINARY_SIGN_TOOL=str(args.signer),OHOS_LLVM_READELF=str(args.readelf),OHOS_REAL_CC=str(args.cc),OHOS_REAL_CXX=str(args.cxx))
    defaults=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(Path(args.sdk_root)/'sysroot'),
              '-resource-dir='+str(args.resource_dir),'--ld-path='+str(args.lld),'-Wl,--threads=1']
    for name,compiler,extra in [('python-native-cc',args.cc,[]),('python-native-cxx',args.cxx,['-static-libstdc++'])]:
        command=[args.python,HERE/'python/helpers/sign_compiler.py',compiler,*defaults,*extra,'--']
        p=directory/name;p.write_text('#!/usr/bin/sh\nexec '+shlex.join(list(map(str,command)))+' "$@"\n');p.chmod(0o755)
    # Some upstream configure scripts expand CC directly without eval. Unique own
    # basenames on an explicitly controlled PATH avoid both splitting and fallback.
    env['PATH']=str(directory)+':'+env['PATH']
    for name in ('python-native-cc','python-native-cxx'):
        if shutil.which(name,path=env['PATH'])!=str(directory/name):raise ValueError('Own compiler launcher resolution differs')
    env.update(CC='python-native-cc',CXX='python-native-cxx',AR=str(args.ar),RANLIB=str(args.ranlib),
               NM=str(args.nm),LD=str(args.lld),STRIP=str(args.strip),PYTHON_FOR_REGEN=str(args.regen_python),
               CFLAGS='-O2 -fPIC',CXXFLAGS='-O2 -fPIC',CPPFLAGS='-D__MUSL__')
    return env


def sign_file(args,file):
    p=Path(file)
    if p.is_symlink() or not p.is_file():raise ValueError('Only owned regular ELF may be signed')
    env=clean_env(args.tmp_dir)
    with tempfile.TemporaryDirectory(prefix='python-native-final-sign-',dir=args.tmp_dir) as td:
        signed=Path(td)/'signed'
        result=subprocess.run([str(args.signer),'sign','-inFile',str(p),'-outFile',str(signed),'-selfSign','1'],env=env,capture_output=True,text=True)
        if result.returncode or not signed.is_file():raise RuntimeError('Final signer failed: '+result.stdout+result.stderr)
        sections=subprocess.check_output([str(args.readelf),'--sections',str(signed)],env=env,text=True)
        if '.codesign' not in sections:raise ValueError('Final signed ELF lacks codesign section')
        p.unlink();shutil.copyfile(signed,p);p.chmod(0o755)
