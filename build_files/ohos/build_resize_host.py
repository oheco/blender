#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the real five-source resize host with the original native SDK and sign its ELF.

Uses a new private-cache build directory and explicit signed Core. No download,
stripping, credential access, or implicit HAP source staging occurs here.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from build_resize_hap import codesign_record, file_record


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, env, log):
    with log.open('x') as stream:
        result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
    require(result.returncode == 0, f'Native command failed ({result.returncode}); inspect {log}')
    return {'command': command, 'exit_code': result.returncode, 'log': {'path': str(log), **file_record(log)}}


def text(command, env):
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpp-dir', type=Path, required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    require(os.uname().sysname == 'HarmonyOS' and os.uname().machine == 'aarch64', 'Native HarmonyOS ARM64 is required')
    repository = Path(__file__).resolve().parents[2]
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve(strict=True)
    temporary = Path(os.environ['TMPDIR']).resolve(strict=True)
    source = args.cpp_dir.resolve(strict=True)
    core = args.core.resolve(strict=True)
    sdk = args.sdk.resolve(strict=True)
    build = args.build_dir.resolve()
    receipt = args.receipt.absolute()
    require(cache in build.parents and not build.exists(), 'Build must be a new private cache directory')
    require(not receipt.exists() and receipt.parent.is_dir(), 'Receipt must be new and outside the build directory')
    require(build not in receipt.parents, 'Receipt must survive independently of the build')
    codesign_record(core)
    source_files = [p for p in sorted(source.iterdir()) if p.is_file() and
                    (p.suffix in ('.cc', '.h') or p.name in ('CMakeLists.txt', 'host.exports.map'))]
    source_files += [repository / 'intern/ghost/GHOST_OHOSHost.h', repository / 'intern/ghost/GHOST_OHOSNative.h',
                     repository / 'source/creator/creator_ohos.h', repository / 'source/creator/creator_ohos_files.h']
    inputs = [{'path': str(p), **file_record(p)} for p in source_files]
    expected_cpp = {'napi_init.cc', 'host_bridge.cc', 'reliable_input.cc', 'xcomponent_input.cc', 'ime_input.cc'}
    require({p.name for p in source_files if p.suffix == '.cc'} == expected_cpp, 'Unexpected native host source set')
    core_input = {'path': str(core), **file_record(core)}
    env = dict(os.environ)
    for name in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LD_LIBRARY_PATH', 'LD_PRELOAD',
                 'CMAKE_C_COMPILER_LAUNCHER', 'CMAKE_CXX_COMPILER_LAUNCHER', 'CMAKE_C_LINKER_LAUNCHER',
                 'CMAKE_CXX_LINKER_LAUNCHER', 'PYTHONHOME', 'PYTHONPATH'):
        env.pop(name, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    cmake = sdk / 'build-tools/cmake/bin/cmake'
    ninja = sdk / 'build-tools/cmake/bin/ninja'
    readelf = sdk / 'llvm/bin/llvm-readelf'
    signer = shutil.which('binary-sign-tool')
    require(cmake.is_file() and ninja.is_file() and readelf.is_file() and signer, 'Native build and signer tools are required')
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    build.mkdir(mode=0o700)
    options = {
        'CMAKE_MAKE_PROGRAM': ninja, 'CMAKE_TOOLCHAIN_FILE': sdk / 'build/cmake/ohos.toolchain.cmake',
        'OHOS_ARCH': 'arm64-v8a', 'OHOS_STL': 'c++_static', 'OHOS_ALLOW_UNDEFINED_SYMBOLS': 'OFF',
        'CMAKE_BUILD_TYPE': 'Debug', 'CMAKE_SKIP_RPATH': 'ON', 'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON',
        'BLENDER_SOURCE': repository, 'BLENDER_CORE_LIBRARY': core, 'BLENDER_OHOS_MALEOON_TILE_EXPERIMENT': 'ON',
    }
    configure = run([str(cmake), '-S', str(source), '-B', str(build), '-G', 'Ninja',
                     *[f'-D{k}={v}' for k, v in options.items()]], env, build / 'configure.log')
    commands_path = build / 'compile_commands.json'
    commands = json.loads(commands_path.read_text())
    require({Path(row['file']).name for row in commands} == expected_cpp and len(commands) == 5,
            'The generated graph must compile all five real host sources')
    compiled = run([str(cmake), '--build', str(build), '--target', 'blender_host', '--parallel', '1', '--verbose'],
                   env, build / 'build.log')
    unsigned = build / 'libblender_host.so'
    header = text([str(readelf), '-h', str(unsigned)], env)
    dynamic = text([str(readelf), '-d', str(unsigned)], env)
    require('AArch64' in header and 'ELF64' in header, 'Host must be AArch64 ELF64')
    require(not any(item in dynamic for item in ('RPATH', 'RUNPATH', 'TEXTREL')), 'Host must have no embedded loader paths or text relocations')
    host_symbols = text([str(readelf), '--dyn-syms', '--wide', str(unsigned)], env)
    core_symbols = text([str(readelf), '--dyn-syms', '--wide', str(core)], env)
    imports = {line.split()[-1] for line in host_symbols.splitlines()
               if ' UND ' in line and line.split()[-1].startswith(('ghost_ohos_', 'blender_ohos_'))}
    exports = {line.split()[-1].replace('@@', '@') for line in core_symbols.splitlines()
               if ' UND ' not in line and line.split()}
    require(imports and imports <= exports and
            any(name.partition('@')[0] == 'ghost_ohos_native_resize_observe' for name in imports),
            'Rebuilt host must bind every Core import including the new resize observation API')
    signed = build / 'libblender_host-signed.so'
    unsigned_record = file_record(unsigned)
    with tempfile.TemporaryDirectory(prefix='blender-host-sign-', dir=temporary) as owned:
        owner = Path(owned).resolve(strict=True)
        require(owner.parent == temporary, 'Unexpected signer temporary directory')
        candidate = owner / 'host-signed.so'
        result = subprocess.run([signer, 'sign', '-inFile', str(unsigned), '-outFile', str(candidate), '-selfSign', '1'],
                                env=env, capture_output=True)
        require(result.returncode == 0, 'Native host signing failed; signer output suppressed')
        codesign_record(candidate)
        with candidate.open('rb') as src, signed.open('xb') as dst:
            shutil.copyfileobj(src, dst)
        require(Path(owned).resolve(strict=True) == owner and owner.parent == temporary, 'Signer cleanup target changed')
    require(file_record(core) == {k: core_input[k] for k in ('sha256', 'size')}, 'Signed Core changed during host build')
    require(all(file_record(Path(item['path'])) == {k: item[k] for k in ('sha256', 'size')} for item in inputs),
            'Host sources changed during native build')
    record = {'kind': 'resize-host-native-build', 'status': 'success', 'native_system': list(os.uname()),
              'source_inputs': inputs, 'core': core_input, 'host': {'path': str(signed), **file_record(signed)},
              'host_codesign': codesign_record(signed), 'unsigned_host': unsigned_record,
              'configure': configure, 'build': compiled, 'compile_commands': {'path': str(commands_path), **file_record(commands_path)},
              'core_imports': sorted(imports), 'all_core_imports_bound': True,
              'needed': re.findall(r'Shared library: \[(.*?)\]', dynamic),
              'scope': 'Native build, ABI and signing; installed HAP runtime remains a separate gate'}
    with receipt.open('x') as stream:
        json.dump(record, stream, indent=2); stream.write('\n')
    print(json.dumps({'status': record['status'], 'host': record['host'], 'receipt': str(receipt)}, indent=2))


if __name__ == '__main__':
    main()
