# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit Clang20, SDK15 __n1 and exact read-only compiler-rt overlay.
Adapted from deps_core_sources/builder/toolchain.py; no identity/probe overrides.
"""
import json
import os
import re
import shlex
from pathlib import Path
from source_guard import HERE, sha
from io_utils import write_json


def flags(args):
    common=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(args.sdk_root/'sysroot'),
            '--ld-path='+str(args.lld),'-resource-dir='+str(args.resource_dir),
            '-L'+str(args.sdk_root/'llvm/lib/aarch64-linux-ohos')]
    cxx=common+['--driver-mode=g++','-nostdinc++','-isystem',str(args.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),
                '-fexperimental-library','-static-libstdc++','-lc++experimental']
    return {'c':common,'cxx':cxx}


def preflight(args, runner):
    if os.uname().sysname!='HarmonyOS' or os.uname().machine!='aarch64':
        raise ValueError('Actual native HarmonyOS AArch64 host required')
    if args.lld.name!='ld.lld':
        raise ValueError('Multicall LLVM linker must retain actual ld.lld basename')
    required=[args.cc,args.cxx,args.lld,args.signer,args.python,args.cmake,args.ninja,args.pkgconf,args.git,
              args.resource_dir/'include/arm_neon.h',args.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1/__config']
    required += [args.sdk_root/'llvm/bin'/n for n in ['llvm-ar','llvm-ranlib','llvm-readelf','llvm-nm']]
    required += [args.sdk_root/'llvm/lib/aarch64-linux-ohos'/n for n in ['libc++.a','libc++abi.a','libc++experimental.a']]
    for name in ['libclang_rt.builtins.a','clang_rt.crtbegin.o','clang_rt.crtend.o']:
        overlay=args.resource_dir/'lib/aarch64-linux-ohos'/name
        original=args.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name
        if not overlay.is_file() or not original.is_file() or sha(overlay)!=sha(original):
            raise ValueError('Missing/exact SDK15 compiler-rt overlay differs: '+name)
        required.extend([overlay,original])
    if any(not p.is_file() for p in required):
        raise ValueError('Declared actual prerequisite is absent')
    for p in [args.cc,args.cxx]:
        if not re.search(r'clang version 20\.',runner.run([p,'--version'],'clang-version-'+p.name)):
            raise ValueError('Actual Clang20 required')
    if not re.search(r'LLD 20\.',runner.run([args.lld,'--version'],'lld-version')):
        raise ValueError('Actual LLD20 required')
    versions={}
    for name in ['cmake','ninja','pkgconf','python','git']:
        versions[name]=runner.run([getattr(args,name),'--version'],'tool-version-'+name)
    selected=[f for f in flags(args)['cxx'] if not f.startswith(('--ld-path=','-L')) and f not in ('-static-libstdc++','-lc++experimental')]
    macros=runner.run([args.cxx,*selected,'-std=c++20','-E','-dM','-x','c++','-'],'actual-sdk-preprocessor',
                     input_text='#include <__config>\n#include <arm_neon.h>\n')
    values=dict(re.findall(r'^#define (\w+)[ \t]+([^\n]+)$',macros,re.M))
    if any(values.get(k)!=v for k,v in {'_LIBCPP_ABI_NAMESPACE':'__n1','_LIBCPP_VERSION':'15004','__clang_major__':'20'}.items()) or \
       '__OHOS__' not in values or '__aarch64__' not in values:
        raise ValueError('Actual SDK __n1/15004/Clang20/target macro mismatch')
    return {'result':'PASS read-only actual compiler versions/preprocessor',
            'scope':'No configure, native compile, ELF link/run, bridge or bpy acceptance',
            'selected_macros':{k:values.get(k) for k in ['_LIBCPP_ABI_NAMESPACE','_LIBCPP_VERSION','__clang_major__','__OHOS__','__aarch64__','__cplusplus']},
            'tools':[{'path':str(p),'size':p.stat().st_size,'sha256':sha(p)} for p in required], 'versions':versions}


def generate(args):
    d=args.root/'toolchain';d.mkdir(exist_ok=True)
    cfg={'compilers':{'c':str(args.cc),'cxx':str(args.cxx)},'flags':flags(args),'signer':str(args.signer),
         'readelf':str(args.sdk_root/'llvm/bin/llvm-readelf'),'tmp_dir':str(args.tmp_dir)}
    write_json(d/'compiler.json',cfg)
    for lang in ['c','cxx']:
        p=d/('clang20-'+lang)
        p.write_text('#!/usr/bin/sh\nexec '+shlex.join([str(args.python),str(HERE/'launcher.py'),str(d/'compiler.json'),lang,'--'])+' "$@"\n')
        p.chmod(0o755)
    def q(p):
        return str(p).replace('\\','/').replace('"','\\"')
    lines=['# Actual native identity; caller-owned SDK/compiler/signer. No fake HAVE/cross/probe values.',
           'include_guard(GLOBAL)',
           'list(PREPEND CMAKE_MODULE_PATH "'+q(HERE/'cmake')+'")',
           'set(CMAKE_C_COMPILER "'+q(d/'clang20-c')+'" CACHE FILEPATH "Actual compiler signing driver")',
           'set(CMAKE_CXX_COMPILER "'+q(d/'clang20-cxx')+'" CACHE FILEPATH "Actual SDK15 __n1 compiler signing driver")',
           'set(CMAKE_AR "'+q(args.sdk_root/'llvm/bin/llvm-ar')+'" CACHE FILEPATH "SDK archiver")',
           'set(CMAKE_RANLIB "'+q(args.sdk_root/'llvm/bin/llvm-ranlib')+'" CACHE FILEPATH "SDK archive indexer")',
           'set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "Ordinary translation units")']
    for kind in ['EXE','SHARED','MODULE']:
        lines.append('set(CMAKE_'+kind+'_LINKER_FLAGS_INIT "-Wl,--threads=2 -Wl,-z,text")')
    p=d/'native.cmake';p.write_text('\n'.join(lines)+'\n');return p
