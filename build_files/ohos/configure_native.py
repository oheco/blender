#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Configure the in-progress native OHOS Blender editor with fixed local inputs.

This is an integration diagnostic, not a finished DevEco builder or acceptance.
No download or platform probe result is substituted by this script.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT.parent / 'blender-ohos-work'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--python-prefix', type=Path,
                        help='Accepted shared Python 3.13 runtime prefix for this build')
    parser.add_argument('--define', action='append', default=[], metavar='NAME=VALUE',
                        help='Explicit diagnostic CMake override, recorded in the configure log')
    args = parser.parse_args()
    overrides = {}
    for definition in args.define:
        name, separator, value = definition.partition('=')
        if not separator or not name or not all(c.isalnum() or c == '_' for c in name):
            parser.error('--define requires a CMake variable NAME=VALUE')
        overrides[name] = value
    if os.uname().sysname not in ('HarmonyOS', 'OHOS', 'OpenHarmony') or os.uname().machine != 'aarch64':
        raise SystemExit('A real native ARM64 OHOS environment is required')
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve()
    build = (args.build_dir or cache / 'blender-ohos-editor-build').resolve()
    if cache not in build.parents:
        raise SystemExit('Build directory must be owned under private XDG_CACHE_HOME')
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    base = cache / 'blender-ohos-deps-base/prefix'
    geometry = cache / 'blender-ohos-geometry/prefix'
    color = cache / 'blender-ohos-deps-color/staged-prefix'
    vulkan = cache / 'blender-ohos-deps-vulkan/install'
    python = (args.python_prefix or cache / 'blender-ohos-python-3.13.13-build/runtime').resolve()
    core = cache / 'blender-ohos-deps-core/prefix'
    # An assembled/HAP runtime may contain its own relinked NumPy modules.
    numpy_site = python / 'lib/python3.13/site-packages'
    if not (numpy_site / 'numpy/_core/include/numpy/arrayobject.h').is_file():
        if args.python_prefix:
            raise SystemExit('An explicit Python prefix must include its compatible accepted NumPy runtime')
        numpy_site = cache / 'blender-ohos-numpy-2.3.4-build/install/lib/python3.13/site-packages'
    for path in (base, geometry, color, vulkan, python, core, numpy_site):
        if not path.is_dir():
            raise SystemExit(f'Fixed dependency prefix is not prepared: {path}')
    cmake = shutil.which('cmake')
    ninja = shutil.which('ninja')
    if not cmake or not ninja:
        raise SystemExit('Native CMake and Ninja are required')
    # Package discovery caches must follow the selected runtime on incremental
    # reconfiguration instead of retaining a previously accepted prefix.
    command = [cmake, *(['--fresh'] if args.fresh else []),
               '-UPYTHON_SSL_CERT_FILE', '-UPYTHON_REQUESTS_PATH', '-UPYTHON_ZSTANDARD_PATH',
               '-S', str(ROOT), '-B', str(build), '-G', 'Ninja']
    options = {
        'CMAKE_TOOLCHAIN_FILE': WORK / 'toolchain/blender-native-llvm20.cmake',
        'CMAKE_PROJECT_INCLUDE': WORK / 'deps-core/cmake/native-link-features.cmake',
        'CMAKE_MAKE_PROGRAM': ninja,
        'CMAKE_BUILD_TYPE': 'Release',
        'CMAKE_CXX_SCAN_FOR_MODULES': 'OFF',
        'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON',
        'CMAKE_PREFIX_PATH': ';'.join(map(str, (core, color, geometry, base, vulkan, python))),
        'CMAKE_JOB_POOLS': 'compile=2;link=1',
        'CMAKE_JOB_POOL_COMPILE': 'compile',
        'CMAKE_JOB_POOL_LINK': 'link',
        # The SDK CMake cannot parse this device's memory report reliably.
        'NINJA_MAX_NUM_PARALLEL_COMPILE_JOBS': '2',
        'NINJA_MAX_NUM_PARALLEL_COMPILE_HEAVY_JOBS': '1',
        'NINJA_MAX_NUM_PARALLEL_LINK_JOBS': '1',
        'WITH_LIBS_PRECOMPILED': 'OFF',
        'WITH_STATIC_LIBS': 'ON',
        'SSE2NEON_INCLUDE_DIR': cache / 'blender-ohos-sse2neon/source',
        'WITH_GHOST_OHOS': 'ON',
        'WITH_GHOST_X11': 'OFF',
        'WITH_GHOST_WAYLAND': 'OFF',
        'WITH_GHOST_SDL': 'OFF',
        # Allocation interposition is unavailable with the verified static TBB.
        # Standard malloc remains active; TBB parallelism is preserved.
        'WITH_TBB_MALLOC_PROXY': 'OFF',
        'WITH_HARFBUZZ': 'ON',
        'WITH_FRIBIDI': 'ON',
        'WITH_OPENGL_BACKEND': 'OFF',
        'WITH_VULKAN_BACKEND': 'ON',
        'WITH_PYTHON': 'ON',
        'WITH_PYTHON_INSTALL': 'ON',
        'WITH_PYTHON_MODULE': 'OFF',
        'WITH_INSTALL_PORTABLE': 'ON',
        'WITH_COMPILER_CCACHE': 'OFF',
        'FETCHCONTENT_FULLY_DISCONNECTED': 'ON',
        'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON',
        'PYTHON_VERSION': '3.13',
        'PYTHON_ROOT_DIR': python,
        'PYTHON_EXECUTABLE': python / 'bin/python3.13',
        'PYTHON_INCLUDE_DIR': python / 'include/python3.13',
        'PYTHON_INCLUDE_CONFIG_DIR': python / 'include/python3.13',
        'PYTHON_LIBRARY': python / 'lib/libpython3.13.so',
        'PYTHON_LIBPATH': python / 'lib',
        'PYTHON_SITE_PACKAGES': python / 'lib/python3.13/site-packages',
        'WITH_PYTHON_NUMPY': 'ON',
        'WITH_PYTHON_INSTALL_NUMPY': 'ON',
        'PYTHON_NUMPY_PATH': numpy_site,
        'PYTHON_NUMPY_INCLUDE_DIRS': numpy_site / 'numpy/_core/include',
        'SHADERC_ROOT_DIR': vulkan,
        'SHADERC_INCLUDE_DIR': vulkan / 'include',
        'SHADERC_LIBRARY': vulkan / 'lib/libshaderc_combined.a',
        'VULKAN_ROOT_DIR': vulkan,
        'VULKAN_INCLUDE_DIR': vulkan / 'include',
        'VULKAN_LIBRARY': '/system/lib64/libvulkan.so',
        'OpenImageIO_DIR': color / 'lib/cmake/OpenImageIO',
        'OpenColorIO_DIR': color / 'lib/cmake/OpenColorIO',
        'OpenEXR_DIR': color / 'lib/cmake/OpenEXR',
        'Imath_DIR': color / 'lib/cmake/Imath',
        'fmt_DIR': color / 'lib/cmake/fmt',
        'OPENSUBDIV_ROOT_DIR': geometry,
        'GMP_ROOT_DIR': geometry,
        'TBB_ROOT_DIR': geometry,
        'EMBREE_ROOT_DIR': core,
        'Eigen3_DIR': core / 'share/eigen3/cmake',
        'Ceres_DIR': core / 'lib/cmake/Ceres',
        'absl_DIR': core / 'lib/cmake/absl',
        # libmv consumes native generated headers, not desktop vendor configs.
        'WITH_SYSTEM_GFLAGS': 'ON',
        'WITH_SYSTEM_GLOG': 'ON',
        'GFLAGS_ROOT_DIR': core,
        'GLOG_ROOT_DIR': core,
        'PKG_CONFIG_EXECUTABLE': cache / 'blender-ohos-deps-base/tools/bin/pkgconf',
    }
    options.update(overrides)
    command += [f'-D{key}={value}' for key, value in options.items()]
    build.mkdir(parents=True, exist_ok=True)
    logs = WORK / 'state/configure-logs'
    logs.mkdir(parents=True, exist_ok=True)
    index = 0
    logfile = logs / 'native-editor-configure.log'
    while logfile.exists():
        index += 1
        logfile = logs / f'native-editor-configure.{index}.log'
    print(f'Configure native editor; log={logfile}', flush=True)
    with logfile.open('w') as log:
        log.write('Arguments: ' + repr(command) + '\n')
        log.flush()
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        log.write(f'\nexit_code={result.returncode}\n')
    print(f'Configure exit={result.returncode}; inspect {logfile}', flush=True)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
