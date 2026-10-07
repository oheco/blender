#!/usr/bin/env python3
"""Offline native LLVM20 compiler bootstrap, gated before resource-heavy actions.
Only this toolchain directory and XDG_CACHE_HOME/TMPDIR are written.
Run `plan` first and obtain the coordinating parent's resource allocation.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ['XDG_CACHE_HOME']) / 'blender-ohos-toolchain'
SDK_DEFAULT = '/storage/Users/currentUser/.oheco/packages/ohos-sdk-native/26.0.0.35-Beta'
TRIPLE = 'aarch64-unknown-linux-ohos'


def run(command, label, environment):
    logs = ROOT / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    print('+ ' + shlex.join(map(str, command)), flush=True)
    result = {'command':list(map(str,command)),'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'runner_pid':os.getpid(),'log':str(logs/(label+'.log')),'status':'running'}
    state = logs/(label+'.result.json')
    state.write_text(json.dumps(result,indent=2)+'\n')
    with (logs / (label + '.log')).open('w') as log:
        log.write('+ ' + shlex.join(map(str, command)) + '\n')
        log.flush()
        process = subprocess.run(list(map(str,command)),env=environment,stdout=log,stderr=subprocess.STDOUT)
    result.update(status='completed' if process.returncode == 0 else 'failed',returncode=process.returncode,
                  finished=datetime.datetime.now(datetime.timezone.utc).isoformat())
    state.write_text(json.dumps(result,indent=2)+'\n')
    if process.returncode:
        raise SystemExit(f'{label} failed ({process.returncode}); inspect {logs / (label + ".log")} before retry')


def launcher(path, compiler, flags):
    path.parent.mkdir(parents=True, exist_ok=True)
    words = [sys.executable, str(HERE / 'sign_compiler.py'), str(compiler), *map(str,flags),'--']
    content = '#!/usr/bin/sh\nexec ' + shlex.join(words) + ' "$@"\n'
    if not path.exists() or path.read_text() != content:
        path.write_text(content)
    path.chmod(0o755)  # Private cache filesystem, never hmdfs.
    return path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['plan','configure','build','install','configure-libcxx20','build-libcxx20','install-libcxx20'])
    p.add_argument('--sdk',default=SDK_DEFAULT)
    p.add_argument('--jobs',type=int,default=2)
    p.add_argument('--ack-resource-plan',action='store_true',help='Set only after parent allocates compilation resources')
    p.add_argument('--fresh',action='store_true',help='Reset CMake configuration probes before configuring; no effect on build/install actions')
    a = p.parse_args()
    if platform.machine() != 'aarch64' or platform.system() not in ('HarmonyOS','OpenHarmony','OHOS'):
        raise SystemExit('Native ARM64 OHOS host required; Linux prebuilts/cross-host generators forbidden')
    if not 1 <= a.jobs <= 4:
        raise SystemExit('Conservative job limit is 1..4, default 2; link pool is 1')
    sdk = Path(a.sdk)
    source = ROOT / 'sources/llvm-project-20.1.8.src'
    build = ROOT / 'build-llvm20-sdk15'
    prefix = ROOT / 'install-llvm20'
    rtbuild = ROOT / 'build-libcxx20'
    rtprefix = ROOT / 'install-libcxx20'
    plan = {'source':str(source),'build':str(build),'install':str(prefix),'libcxx20_build':str(rtbuild),
            'libcxx20_install':str(rtprefix),'bootstrap':str(sdk/'llvm/bin/clang++'),'native_generators':True,
            'compile_jobs':a.jobs,'link_jobs':1,'lld_threads':2,'build_type':'Release','lto':False,
            'projects':['clang','lld'],'targets':['AArch64'],
            'disk_estimate':'source measured 2.0GiB; reserve 35GiB for Release build/install (estimate, not measured)',
            'memory_policy':'Require MemAvailable >= 8GiB at each action; default -j2; no full-CPU build; parent coordinates Godot',
            'stdlib_policy':'LLVM compiler/generators bootstrap against static SDK libc++15; Blender SDK15 experimental subset needs fresh clang20 probes; own libc++20 is optional and uses ABI namespace __blender20',
            'network':'No network in any build action; only prepare_source.py fetches pinned source through required SOCKS5 proxy',
            'runtime_overlay':'LLVM20 builtin headers + copied SDK15 compiler-rt builtins/crt .a/.o; record SDK provenance, no SDK edits',
            'signing':'Compiler wrapper signs every linked ELF before execution, including llvm-min-tblgen/llvm-tblgen/clang-tblgen/CMake try_run'}
    if a.action == 'plan':
        print(json.dumps(plan,indent=2))
        return
    if not a.ack_resource_plan:
        raise SystemExit('Resource-heavy action not started. Send plan/source/probe results to parent, then pass --ack-resource-plan only after allocation.')
    # Hold one private-filesystem lock for this whole operation. Never start a
    # second build/configure/install while an existing native action is active.
    guard = (ROOT/'active-operation.lock').open('a+')
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('An existing native toolchain operation holds the lock; do not duplicate it')
    guard.seek(0)
    guard.truncate()
    guard.write(json.dumps({'pid':os.getpid(),'action':a.action})+'\n')
    guard.flush()
    marker = source / '.ohos-source-verified.json'
    if not marker.is_file():
        raise SystemExit('Verified source missing; run prepare_source.py --offline first')
    lock = json.loads((HERE/'llvm-source.lock.json').read_text())
    verified = json.loads(marker.read_text())
    if verified['lock'] != lock:
        raise SystemExit('Source marker does not match pinned lock')
    from apply_cache_patch import apply_patch
    plan['source_patch'] = apply_patch()
    if not (sdk/'llvm/bin/clang++').is_file():
        raise SystemExit('Native SDK compiler missing')
    mem = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(mem['MemAvailable'].split()[0]) * 1024
    if available < 8 * 1024**3:
        raise SystemExit(f'MemAvailable only {available / 1024**3:.1f}GiB; do not contend with user Godot')
    if shutil.disk_usage(ROOT).free < 35 * 1024**3:
        raise SystemExit('Less than 35GiB free in private cache; parent must budget before starting')
    # Native nice command is absent. Lower only this build process priority; never global config.
    try:
        os.setpriority(os.PRIO_PROCESS,0,10)
    except OSError as error:
        raise SystemExit(f'Cannot lower own build priority: {error}; parent should schedule this job explicitly')
    env = os.environ.copy()
    env['PATH'] = str(sdk/'llvm/bin') + ':' + env['PATH']
    env['CMAKE_BUILD_PARALLEL_LEVEL'] = str(a.jobs)
    # Do not put SDK linker-script libc++.so in host LD_LIBRARY_PATH: the signer
    # uses HarmonyOS system std::__h, unlike SDK application std::__n1.
    # LLVM bootstrap/generators link SDK libc++ statically and need no SDK LD path.
    bindir = ROOT/'launchers'
    base = ['--target='+TRIPLE,'--sysroot='+str(sdk/'sysroot'),'-fuse-ld=lld']
    cc = launcher(bindir/'bootstrap-cc',sdk/'llvm/bin/clang',base)
    cxx = launcher(bindir/'bootstrap-cxx',sdk/'llvm/bin/clang++',base+['-static-libstdc++'])
    cmake = shutil.which('cmake')
    if not cmake:
        raise SystemExit('Native cmake missing')
    if a.action == 'configure':
        # Do NOT set CMAKE_SYSTEM_NAME/CMAKE_CROSSCOMPILING or probe results.
        # CMake detects the genuine native host, while compilation triple is OHOS.
        flags = {
            'CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(prefix),'CMAKE_C_COMPILER':str(cc),
            'CMAKE_CXX_COMPILER':str(cxx),'CMAKE_MODULE_PATH':str(HERE/'cmake'),'CMAKE_AR':str(sdk/'llvm/bin/llvm-ar'),
            'CMAKE_RANLIB':str(sdk/'llvm/bin/llvm-ranlib'),'CMAKE_SKIP_RPATH':'ON',
            'CMAKE_EXE_LINKER_FLAGS':'-Wl,--threads=2','CMAKE_SHARED_LINKER_FLAGS':'-Wl,--threads=2',
            'LLVM_ENABLE_PROJECTS':'clang;lld','LLVM_ENABLE_RUNTIMES':'','LLVM_TARGETS_TO_BUILD':'AArch64',
            'LLVM_HOST_TRIPLE':TRIPLE,'LLVM_DEFAULT_TARGET_TRIPLE':TRIPLE,'LLVM_NATIVE_ARCH':'AArch64',
            'LLVM_ENABLE_LTO':'OFF','LLVM_BUILD_LLVM_DYLIB':'OFF','LLVM_LINK_LLVM_DYLIB':'OFF',
            'LLVM_ENABLE_RTTI':'ON','LLVM_ENABLE_THREADS':'ON',
            'LLVM_ENABLE_ZLIB':'OFF','LLVM_ENABLE_ZSTD':'OFF','LLVM_ENABLE_LIBXML2':'OFF','LLVM_ENABLE_LIBEDIT':'OFF',
            'LLVM_INCLUDE_TESTS':'OFF','LLVM_INCLUDE_BENCHMARKS':'OFF','LLVM_INCLUDE_EXAMPLES':'OFF',
            'LLVM_INCLUDE_UTILS':'ON','LLVM_BUILD_UTILS':'ON','LLVM_INSTALL_UTILS':'ON',
            'LLVM_PARALLEL_COMPILE_JOBS':str(a.jobs),'LLVM_PARALLEL_LINK_JOBS':'1',
            'LLVM_OPTIMIZED_TABLEGEN':'OFF','CLANG_BUILD_EXAMPLES':'OFF','CLANG_ENABLE_STATIC_ANALYZER':'OFF',
            'CLANG_ENABLE_ARCMT':'OFF','Python3_EXECUTABLE':sys.executable,
        }
        run([cmake,*(['--fresh'] if a.fresh else []),'-S',source/'llvm','-B',build,'-G','Ninja',*[f'-D{k}={v}' for k,v in flags.items()]],'llvm-configure',env)
    elif a.action == 'build':
        run([cmake,'--build',build,'--target','clang','lld','llvm-tblgen','clang-tblgen','--parallel',a.jobs],'llvm-build',env)
    elif a.action == 'install':
        for component in ('clang','lld','clang-resource-headers','llvm-tblgen','clang-tblgen'):
            run([cmake,'--install',build,'--component',component],'install-'+component,env)
        if not (prefix/'bin/clang').is_file():
            raise SystemExit('Compiler component did not install clang')
        resource = prefix/'lib/clang/20'
        if not (resource/'include/stddef.h').is_file():
            raise SystemExit('LLVM20 builtin headers not installed; inspect clang-resource-headers component')
        overlay = resource/'lib/aarch64-linux-ohos'
        overlay.mkdir(parents=True,exist_ok=True)
        sdkrt = sdk/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'
        provenance = {'sdk':str(sdk),'bootstrap_sdk_version':'26.0.0.35-Beta','compiler_rt_version':'15.0.4',
                      'note':'LLVM20 compiler, LLVM20 builtin headers, genuine SDK15 runtime objects/archives; not represented as compiler-rt20','files':[]}
        for item in sdkrt.iterdir():
            if item.is_file() and item.suffix in ('.a','.o'):
                shutil.copy2(item,overlay/item.name)
                provenance['files'].append({'name':item.name,'sha256':hashlib.sha256(item.read_bytes()).hexdigest()})
        shutil.copy2(sdk/'NOTICE.txt',prefix/'bootstrap-sdk-NOTICE.txt')
        (resource/'ohos-sdk15-runtime-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
        # Upstream20 OHOS.cpp does not understand vendor libcxx-ohos header layout,
        # nor does it auto-add libc++experimental for -fexperimental-library.
        stagebase = base+['-resource-dir='+str(resource),'-L'+str(sdk/'llvm/lib/aarch64-linux-ohos')]
        launcher(bindir/'clang20-cc',prefix/'bin/clang',stagebase)
        launcher(bindir/'clang20-sdk15-cxx',prefix/'bin/clang++',stagebase+
                 ['-nostdinc++','-isystem',sdk/'llvm/include/libcxx-ohos/include/c++/v1',
                  '-fexperimental-library','-static-libstdc++','-lc++experimental'])
        launcher(bindir/'clang20-runtime-cxx',prefix/'bin/clang++',stagebase)
        run([prefix/'bin/clang','--version'],'installed-clang-version',env)
        for name in ('llvm-min-tblgen','llvm-tblgen','clang-tblgen'):
            tool = build/'bin'/name
            if not tool.is_file():
                raise SystemExit('Native generated tool missing: '+str(tool))
            run([tool,'--version'],'native-'+name,env)
    elif a.action.startswith(('configure-libcxx20','build-libcxx20','install-libcxx20')):
        if not (bindir/'clang20-runtime-cxx').is_file():
            raise SystemExit('Build/install real native clang20 first; no libcxx20 with fake compiler identity')
        if a.action == 'configure-libcxx20':
            flags = {
                'CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(rtprefix),
                'CMAKE_C_COMPILER':str(bindir/'clang20-cc'),'CMAKE_CXX_COMPILER':str(bindir/'clang20-runtime-cxx'),
                'CMAKE_MODULE_PATH':str(HERE/'cmake'),
                'CMAKE_AR':str(sdk/'llvm/bin/llvm-ar'),'CMAKE_RANLIB':str(sdk/'llvm/bin/llvm-ranlib'),
                'CMAKE_EXE_LINKER_FLAGS':'-Wl,--threads=2','CMAKE_SKIP_RPATH':'ON',
                'LLVM_ENABLE_RUNTIMES':'libunwind;libcxxabi;libcxx','LIBCXX_ABI_NAMESPACE':'__blender20',
                'LIBCXX_ENABLE_SHARED':'OFF','LIBCXX_ENABLE_STATIC':'ON','LIBCXX_INCLUDE_TESTS':'OFF',
                'LIBCXXABI_ENABLE_SHARED':'OFF','LIBCXXABI_ENABLE_STATIC':'ON','LIBCXXABI_INCLUDE_TESTS':'OFF',
                'LIBCXXABI_USE_LLVM_UNWINDER':'ON','LIBUNWIND_ENABLE_SHARED':'OFF','LIBUNWIND_ENABLE_STATIC':'ON',
                'LIBUNWIND_INCLUDE_TESTS':'OFF','LIBCXX_USE_COMPILER_RT':'ON','LIBCXXABI_USE_COMPILER_RT':'ON',
                'LIBUNWIND_USE_COMPILER_RT':'ON','CMAKE_POSITION_INDEPENDENT_CODE':'ON',
            }
            run([cmake,'-S',source/'runtimes','-B',rtbuild,'-G','Ninja',*[f'-D{k}={v}' for k,v in flags.items()]],'libcxx20-configure',env)
        elif a.action == 'build-libcxx20':
            run([cmake,'--build',rtbuild,'--parallel',a.jobs],'libcxx20-build',env)
        else:
            run([cmake,'--install',rtbuild],'libcxx20-install',env)
            inc = rtprefix/'include/c++/v1'
            if not (inc/'__config_site').is_file():
                raise SystemExit('Check actual upstream libcxx20 installed header layout before creating consumer launcher')
            rtbase = base+['-resource-dir='+str(prefix/'lib/clang/20'),'-L'+str(rtprefix/'lib'),'-L'+str(sdk/'llvm/lib/aarch64-linux-ohos')]
            launcher(bindir/'clang20-libcxx20-cxx',prefix/'bin/clang++',rtbase+
                     ['-nostdinc++','-isystem',inc,'-static-libstdc++'])
            # All Blender C++ deps need this SAME ABI namespace and runtime; cannot mix __n1/__1/__blender20 objects.
    (HERE/'results/native-build-plan.json').write_text(json.dumps(plan,indent=2)+'\n')


if __name__ == '__main__':
    main()
