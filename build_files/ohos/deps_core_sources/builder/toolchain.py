# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit caller SDK/compiler/TBB prerequisites and native signing toolchain."""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile
import tempfile
from io_utils import HERE, REPO, repo_file, sha, vendor, write_json


def compiler_config(args):
    sdk = args.sdk_root.resolve()
    common = ['--target=aarch64-unknown-linux-ohos','--sysroot=' + str(sdk / 'sysroot'),
              '--ld-path=' + str(args.lld),'-resource-dir=' + str(args.resource_dir.resolve()),
              '-L' + str(sdk / 'llvm/lib/aarch64-linux-ohos')]
    cxx = common + ['--driver-mode=g++','-nostdinc++','-isystem',str(sdk / 'llvm/include/libcxx-ohos/include/c++/v1'),
                    '-fexperimental-library','-static-libstdc++','-lc++experimental']
    return {'compilers':{'c':str(args.cc),'cxx':str(args.cxx)},
            'flags':{'c':common,'cxx':cxx},'signer':str(args.signer.resolve()),
            'readelf':str(sdk / 'llvm/bin/llvm-readelf'),'tmp_dir':str(args.tmp_dir)}


def preflight(args, runner):
    if os.uname().sysname != 'HarmonyOS' or os.uname().machine != 'aarch64':
        raise ValueError('Actual native HarmonyOS/aarch64 host required')
    required = [args.cc,args.cxx,args.lld,args.signer,args.sdk_root / 'llvm/bin/llvm-ar',
                args.sdk_root / 'llvm/bin/llvm-ranlib',args.sdk_root / 'llvm/bin/llvm-readelf',
                args.sdk_root / 'llvm/bin/llvm-nm',args.sdk_root / 'llvm/lib/aarch64-linux-ohos/libc++.a',
                args.sdk_root / 'llvm/lib/aarch64-linux-ohos/libc++abi.a',
                args.sdk_root / 'llvm/lib/aarch64-linux-ohos/libc++experimental.a']
    required.append(args.resource_dir / 'include/arm_neon.h')
    for name in ['libclang_rt.builtins.a','clang_rt.crtbegin.o','clang_rt.crtend.o']:
        overlay = args.resource_dir / 'lib/aarch64-linux-ohos' / name
        original = args.sdk_root / 'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos' / name
        if not overlay.is_file() or not original.is_file() or sha(overlay) != sha(original):
            raise ValueError('Declared Clang20 resource needs exact SDK15 compiler-rt overlay: ' + name)
        required.extend([overlay,original])
    for file in required:
        if not file.is_file():
            raise ValueError('Missing declared native prerequisite: ' + str(file))
    for file in [args.cc,args.cxx]:
        version = runner.run([file,'--version'],'compiler-version-' + file.name)
        if not re.search(r'clang version 20\.',version):
            raise ValueError('Real Clang20 compiler required, without version overrides')
    linker = runner.run([args.lld,'--version'],'linker-version')
    if not re.search(r'LLD 20\.',linker):
        raise ValueError('Declared lld20 required')
    cfg = compiler_config(args)
    flags = [f for f in cfg['flags']['cxx'] if not f.startswith(('--ld-path=','-L')) and
             f not in ('-static-libstdc++','-lc++experimental')]
    macros = runner.run([args.cxx] + flags + ['-std=c++20','-E','-dM','-x','c++','-'],
                        'actual-sdk-preprocessor',input_text='#include <__config>\n#include <arm_neon.h>\n')
    values = dict(re.findall(r'^#define (\w+)[ \t]+([^\n]+)$',macros,re.M))
    if values.get('_LIBCPP_ABI_NAMESPACE') != '__n1' or values.get('_LIBCPP_VERSION') != '15004' or \
       values.get('__clang_major__') != '20' or '__OHOS__' not in values or '__aarch64__' not in values:
        raise ValueError('Actual SDK15 __n1/Clang20/OHOS/AArch64 macro verification failed')
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    tbb = lock['tbb_source']
    with tempfile.TemporaryDirectory(prefix='core-tbb-prerequisite-',dir=runner.tmp) as td:
        archive = Path(td) / tbb['archive_filename']
        vendor().materialize(repo_file(tbb['registry_manifest']).parent,archive)
        with tarfile.open(archive) as tar:
            member = tar.getmember(tbb['archive_root'] + '/include/oneapi/tbb/version.h')
            original = tar.extractfile(member).read()
        installed = args.tbb_prefix / 'include/oneapi/tbb/version.h'
        if not installed.is_file() or installed.read_bytes() != original:
            raise ValueError('Caller TBB prefix header differs from pinned complete original source')
    metadata = args.tbb_prefix / 'lib/cmake/TBB'
    texts = {p.name:p.read_text() for p in metadata.glob('*.cmake')}
    exports = '\n'.join(texts.values())
    if not texts or any(not re.search(r'add_library\(TBB::' + name + r'\s+STATIC\s+IMPORTED\)',exports)
                        for name in ['tbb','tbbmalloc']):
        raise ValueError('Real caller-selected static TBB components tbb and tbbmalloc required')
    if 'Threads::Threads' not in exports or 'dl' not in exports:
        raise ValueError('TBB native thread/dl dependency closure absent')
    if str(args.tbb_prefix) in exports or re.search(r'"(/[^"\n]+)"',exports):
        raise ValueError('Caller TBB CMake metadata contains an absolute prefix/library route')
    archives = []
    for name in ['tbb','tbbmalloc']:
        file = args.tbb_prefix / 'lib' / ('lib' + name + '.a')
        headers = runner.run([args.sdk_root / 'llvm/bin/llvm-readelf','-h',file],'tbb-header-' + name)
        machines = re.findall(r'Machine:\s+([^\n]+)',headers)
        if not machines or any(m.strip() != 'AArch64' for m in machines):
            raise ValueError('Caller TBB archive is not actual AArch64')
        symbols = runner.run([args.sdk_root / 'llvm/bin/llvm-nm','--undefined-only','--demangle',file],
                             'tbb-symbols-' + name)
        if 'std::__h::' in symbols or re.search(r'\bpthread_cancel\b',symbols):
            raise ValueError('TBB wrong SDK ABI or unsupported cancellation symbol')
        archives.append({'path':str(file),'sha256':sha(file),'members':len(machines)})
    return {'scope':'Actual existing compiler/version/preprocessor plus read-only caller TBB input audit; no configure/link/run proof yet',
            'selected_sdk_macros':{k:values.get(k) for k in ['_LIBCPP_ABI_NAMESPACE','_LIBCPP_VERSION','__clang_major__','__cplusplus','__OHOS__','__aarch64__']},
            'tool_inputs':[{'path':str(p),'sha256':sha(p)} for p in required],
            'tbb_prefix_explicit':str(args.tbb_prefix),'tbb_archives':archives,
            'tbb_metadata':[{'path':str(metadata / name),'sha256':sha(metadata / name)} for name in sorted(texts)],
            'tbb_pic_runtime_revalidation':'Required real shared bridge/consumers during future full stage',
            'tbb_source_build':'Separate prerequisite builder; this core candidate does not compile TBB'}


def generate(args, root):
    directory = root / 'toolchain'
    directory.mkdir(exist_ok=True)
    config = compiler_config(args)
    write_json(directory / 'compiler.json',config)
    for lang in ['c','cxx']:
        launcher = directory / ('clang20-' + lang)
        command = [sys.executable,str(HERE / 'launcher.py'),str(directory / 'compiler.json'),lang,'--']
        launcher.write_text('#!/usr/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
        launcher.chmod(0o755)
    def quote(path):
        return str(path).replace('\\','/').replace('"','\\"')
    sdk = args.sdk_root
    lines = ['# Caller-owned native toolchain; no compiler/platform/probe identity overrides.',
             'include_guard(GLOBAL)',
             'set(CMAKE_C_COMPILER "' + quote(directory / 'clang20-c') + '" CACHE FILEPATH "Actual native compiler signing driver")',
             'set(CMAKE_CXX_COMPILER "' + quote(directory / 'clang20-cxx') + '" CACHE FILEPATH "Actual native SDK15 __n1 signing driver")',
             'set(CMAKE_AR "' + quote(sdk / 'llvm/bin/llvm-ar') + '" CACHE FILEPATH "Declared native SDK archiver")',
             'set(CMAKE_RANLIB "' + quote(sdk / 'llvm/bin/llvm-ranlib') + '" CACHE FILEPATH "Declared native SDK indexer")',
             'set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "Ordinary TUs; C++ modules remain unverified")',
             'list(PREPEND CMAKE_MODULE_PATH "' + quote(HERE / 'cmake') + '")']
    for kind in ['EXE','SHARED','MODULE']:
        lines.append('set(CMAKE_' + kind + '_LINKER_FLAGS_INIT "${CMAKE_' + kind + '_LINKER_FLAGS_INIT} -Wl,--threads=2")')
    preset = directory / 'native.cmake'
    preset.write_text('\n'.join(lines) + '\n')
    return preset
