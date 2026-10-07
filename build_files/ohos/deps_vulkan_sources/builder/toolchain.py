# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit native tools; copied core signing contracts with no TBB prerequisite."""
import os
from pathlib import Path
import re
import shlex
from io_utils import HERE, sha, write_json


def compiler_config(args):
    sdk = args.sdk_root
    common = ['--target=aarch64-unknown-linux-ohos', '--sysroot=' + str(sdk / 'sysroot'),
              '--ld-path=' + str(args.lld), '-resource-dir=' + str(args.resource_dir),
              '-L' + str(sdk / 'llvm/lib/aarch64-linux-ohos')]
    cxx = common + ['--driver-mode=g++', '-nostdinc++', '-isystem',
                    str(sdk / 'llvm/include/libcxx-ohos/include/c++/v1'), '-fexperimental-library',
                    '-static-libstdc++', '-lc++experimental']
    return {'compilers': {'c': str(args.cc), 'cxx': str(args.cxx)}, 'flags': {'c': common, 'cxx': cxx},
            'signer': str(args.signer), 'readelf': str(args.readelf), 'tmp_dir': str(args.tmp_dir)}


def inventory(directory):
    return [{'path': p.relative_to(directory).as_posix(), 'sha256': sha(p), 'size': p.stat().st_size}
            for p in sorted(directory.rglob('*')) if p.is_file()]


def preflight(args, runner):
    if (os.uname().sysname, os.uname().machine) != ('HarmonyOS', 'aarch64'):
        raise ValueError('Actual native HarmonyOS/aarch64 host required')
    if args.lld.name != 'ld.lld':
        raise ValueError('Preserve lld multicall invocation basename ld.lld; do not resolve to generic lld')
    required = [args.cc, args.cxx, args.lld, args.signer, args.python, args.cmake, args.ninja, args.git, args.pkgconf, args.ctest]
    sdk = args.sdk_root
    if args.loader != Path('/system/lib64/libvulkan.so'):
        raise ValueError('Only the genuine declared HarmonyOS system Vulkan loader is supported')
    required += [args.ar, args.ranlib, args.readelf, args.nm, args.loader, sdk / 'NOTICE.txt']
    required += [sdk / 'llvm/lib/aarch64-linux-ohos' / n for n in ['libc++.a', 'libc++abi.a', 'libc++experimental.a']]
    required += [args.resource_dir / 'include/arm_neon.h', sdk / 'llvm/include/libcxx-ohos/include/c++/v1/__config']
    for name in ['libclang_rt.builtins.a', 'clang_rt.crtbegin.o', 'clang_rt.crtend.o']:
        overlay = args.resource_dir / 'lib/aarch64-linux-ohos' / name
        original = sdk / 'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos' / name
        if not overlay.is_file() or not original.is_file() or sha(overlay) != sha(original):
            raise ValueError('Declared resource needs exact accepted SDK15 runtime overlay: ' + name)
        required += [overlay, original]
    for file in required:
        if not file.is_file():
            raise ValueError('Missing explicit native prerequisite: ' + str(file))
    versions = {}
    for name in ['cc', 'cxx', 'lld', 'python', 'cmake', 'ninja', 'git', 'pkgconf', 'ctest']:
        output = runner.run([getattr(args, name), '--version'], 'version-' + name)
        versions[name] = output
    if any(not re.search(r'clang version 20\.', versions[n]) for n in ['cc', 'cxx']) or not re.search(r'LLD 20\.', versions['lld']):
        raise ValueError('Actual native Clang20/lld20 required')
    cmake = re.search(r'cmake version (\d+)\.(\d+)', versions['cmake'])
    if not cmake or tuple(map(int, cmake.groups())) < (3, 28):
        raise ValueError('CMake>=3.28 required')
    actual_resource = Path(runner.run([args.cc, '-print-resource-dir'], 'actual-clang20-default-resource').strip())
    if inventory(args.resource_dir / 'include') != inventory(actual_resource / 'include'):
        raise ValueError('Caller resource headers differ from genuine selected Clang20 default resource')
    cfg = compiler_config(args)
    flags = [f for f in cfg['flags']['cxx'] if not f.startswith(('--ld-path=', '-L')) and f not in ('-static-libstdc++', '-lc++experimental')]
    macros = runner.run([args.cxx, *flags, '-std=c++20', '-E', '-dM', '-x', 'c++', '-'],
                        'actual-sdk-preprocessor', input_text='#include <__config>\n#include <arm_neon.h>\n')
    values = dict(re.findall(r'^#define (\w+)[ \t]+([^\n]+)$', macros, re.M))
    if values.get('_LIBCPP_ABI_NAMESPACE') != '__n1' or values.get('_LIBCPP_VERSION') != '15004' or values.get('__clang_major__') != '20' or '__OHOS__' not in values or '__aarch64__' not in values:
        raise ValueError('Actual SDK15 __n1/Clang20/OHOS/AArch64 macro verification failed')
    # -### constructs the actual linker argv without producing objects or executables.
    planned = runner.run([args.cxx, *cfg['flags']['cxx'], '-std=c++20', '-###', '-x', 'c++', '-', '-o', str(args.root / 'unused-plan-output')],
                         'native-driver-link-plan', input_text='int main(){return 0;}\n')
    if str(args.lld) not in planned or '-lc++experimental' not in planned:
        raise ValueError('Actual driver did not retain explicit lld/experimental archive')
    return {'scope': 'Existing native versions/preprocessor/-### only; no compile/link/runtime PASS',
            'host': list(os.uname()), 'versions': versions, 'selected_sdk_macros': {k: values[k] for k in ['_LIBCPP_ABI_NAMESPACE', '_LIBCPP_VERSION', '__clang_major__', '__OHOS__', '__aarch64__']},
            'tool_inputs': [{'path': str(p), 'sha256': sha(p), 'size': p.stat().st_size} for p in required],
            'header_inventories': {'sdk_cxx': inventory(sdk / 'llvm/include/libcxx-ohos/include/c++/v1'),
                                   'clang20_resource': inventory(args.resource_dir / 'include'),
                                   'sdk_sysroot': inventory(sdk / 'sysroot/usr/include')},
            'source_graph': 'Pinned Shaderc/glslang/SPIRV graph plus VUL/Reflect/VMA; no accepted library prefix', 'native_runtime_probe': 'Required by full'}


def generate(args, root):
    directory = root / 'toolchain'
    directory.mkdir(exist_ok=True)
    write_json(directory / 'compiler.json', compiler_config(args))
    for language in ['c', 'cxx']:
        launcher = directory / ('clang20-' + language)
        command = [str(args.python), str(HERE / 'launcher.py'), str(directory / 'compiler.json'), language, '--']
        launcher.write_text('#!/usr/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
        launcher.chmod(0o755)
    def quote(path):
        text = str(path)
        if any(c in text for c in (';', '\n', '\r', '$')):
            raise ValueError('Unsupported CMake path interpolation')
        return text.replace('\\', '/').replace('"', '\\"')
    lines = ['# Actual native host; no compiler/cross/probe identity overrides.', 'include_guard(GLOBAL)']
    for name, value in {'C_COMPILER': directory / 'clang20-c', 'CXX_COMPILER': directory / 'clang20-cxx',
                        'AR': args.ar, 'RANLIB': args.ranlib}.items():
        lines.append('set(CMAKE_' + name + ' "' + quote(value) + '" CACHE FILEPATH "Explicit native input")')
    lines += ['set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "Ordinary TUs")',
              'list(PREPEND CMAKE_MODULE_PATH "' + quote(HERE / 'cmake') + '")']
    for kind in ['EXE', 'SHARED', 'MODULE']:
        lines.append('set(CMAKE_' + kind + '_LINKER_FLAGS_INIT "${CMAKE_' + kind + '_LINKER_FLAGS_INIT} -Wl,--threads=1")')
    preset = directory / 'native.cmake'
    preset.write_text('\n'.join(lines) + '\n')
    return preset
