# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact offline compiler replay planning and byte-bound fixed source patch."""
import json,shutil,difflib
from pathlib import Path
from toolkit_io import HERE,REPO,sha,file_row,json_write


def patch_check(a,runner):
    meta=json.loads((HERE/'patches/manifest.json').read_text());tree=a.root/'source-patch-check';tree.mkdir()
    target=tree/meta['target'];target.parent.mkdir(parents=True);shutil.copyfile(HERE/'source-originals'/meta['target'],target)
    before=target.read_bytes()
    if sha(target)!=meta['upstream_sha256']:raise ValueError('Original archive source target drift')
    diff=HERE/'patches'/meta['patch_file']
    runner.run([a.git,'-C',str(tree),'apply','--check',str(diff)],'patch-forward-check')
    runner.run([a.git,'-C',str(tree),'apply',str(diff)],'patch-forward')
    if sha(target)!=meta['patched_sha256']:raise ValueError('Exact source patched target drift')
    runner.run([a.git,'-C',str(tree),'apply','--reverse','--check',str(diff)],'patch-reverse-check')
    runner.run([a.git,'-C',str(tree),'apply','--reverse',str(diff)],'patch-reverse')
    if target.read_bytes()!=before:raise ValueError('Source patch failed exact reverse roundtrip')
    return {'state':'PASS actual exact original target forward/reverse byte roundtrip','before_sha256':meta['upstream_sha256'],'after_sha256':meta['patched_sha256'],'patch':file_row(diff)}


def plan(a,paths):
    source=a.root/'compiler-replay/source/llvm-project-20.1.8.src';build=a.root/'compiler-replay/build';prefix=a.root/'compiler-replay/install'
    flags={'CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(prefix),'CMAKE_C_COMPILER':paths['cc_launcher'],'CMAKE_CXX_COMPILER':paths['cxx_launcher'],'CMAKE_MODULE_PATH':str(a.root/'profile/cmake'),'CMAKE_AR':str(a.ar),'CMAKE_RANLIB':str(a.ranlib),'CMAKE_MAKE_PROGRAM':str(a.ninja),'CMAKE_SKIP_RPATH':'ON','CMAKE_EXE_LINKER_FLAGS':'-Wl,--threads=1','CMAKE_SHARED_LINKER_FLAGS':'-Wl,--threads=1',
           'LLVM_ENABLE_PROJECTS':'clang;lld','LLVM_ENABLE_RUNTIMES':'','LLVM_TARGETS_TO_BUILD':'AArch64','LLVM_HOST_TRIPLE':'aarch64-unknown-linux-ohos','LLVM_DEFAULT_TARGET_TRIPLE':'aarch64-unknown-linux-ohos','LLVM_NATIVE_ARCH':'AArch64','LLVM_ENABLE_LTO':'OFF','LLVM_BUILD_LLVM_DYLIB':'OFF','LLVM_LINK_LLVM_DYLIB':'OFF','LLVM_ENABLE_RTTI':'ON','LLVM_ENABLE_THREADS':'ON','LLVM_ENABLE_ZLIB':'OFF','LLVM_ENABLE_ZSTD':'OFF','LLVM_ENABLE_LIBXML2':'OFF','LLVM_ENABLE_LIBEDIT':'OFF','LLVM_INCLUDE_TESTS':'OFF','LLVM_INCLUDE_BENCHMARKS':'OFF','LLVM_INCLUDE_EXAMPLES':'OFF','LLVM_INCLUDE_UTILS':'ON','LLVM_BUILD_UTILS':'ON','LLVM_INSTALL_UTILS':'ON','LLVM_PARALLEL_COMPILE_JOBS':str(a.jobs),'LLVM_PARALLEL_LINK_JOBS':'1','LLVM_OPTIMIZED_TABLEGEN':'OFF','CLANG_BUILD_EXAMPLES':'OFF','CLANG_ENABLE_STATIC_ANALYZER':'OFF','CLANG_ENABLE_ARCMT':'OFF','Python3_EXECUTABLE':str(a.python)}
    return {'state':'COMPLETE_SOURCE_REBUILD_NOT_RUN','bootstrap_role':'explicit caller provided Clang20 self-host replay; historical old recipe used SDK15 Clang15 bootstrap',
            'complete_archive':json.loads((HERE/'sources.lock.json').read_text())['LLVM'],'historical_recipe':file_row(HERE/'historical/native_llvm.py'),
            'source_preparation_required':'Before executing configure: materialize exact5parts original archive; safe complete extraction with full inventory/guard; apply fixed before/after-hashed patch in NEW owned tree. This profile candidate does not execute/extract full compiler tree.',
            'commands':{'configure':[str(a.cmake),'-S',str(source/'llvm'),'-B',str(build),'-G','Ninja',*[f'-D{k}={v}' for k,v in flags.items()]],
                       'build':[str(a.cmake),'--build',str(build),'--target','clang','lld','llvm-tblgen','clang-tblgen','--parallel',str(a.jobs)],
                       'install':[[str(a.cmake),'--install',str(build),'--component',c] for c in ['clang','lld','clang-resource-headers','llvm-tblgen','clang-tblgen']]},
            'scope':'clang/lld/AArch64 only; no compiler-rt20/libc++20/all-projects/upstream-tests/signed-bit reproducibility; no source tree re-extraction during short profile stages'}
