# SPDX-License-Identifier: GPL-2.0-or-later
"""Construct concrete argv for formal CMake/Ninja from verified caller inputs."""
from pathlib import Path
from common import HERE, dump
from profiles import profile


def quote(value):
    text = str(value)
    if any(c in text for c in (';', '$', '\n', '\r', '\x00')):
        raise ValueError('Unsupported CMake path interpolation')
    return '"' + text.replace('\\', '/').replace('"', '\\"') + '"'


def project_hook(root, toolkit, selected):
    p = root / 'configuration/editor-project.cmake'
    p.parent.mkdir(parents=True, exist_ok=True)
    # The separate toolkit's actual check/marker source stays immutable and sealed.
    contents = 'include(' + quote(toolkit['paths']['native_link_features_include']) + ')\n'
    contents += 'set(OHOS_EDITOR_REQUIRED_IMPORTED_TARGETS ' + ' '.join(quote(x) for x in selected['imported_targets']) + ')\n'
    contents += 'include(' + quote(HERE / 'cmake/graph.cmake') + ')\n'
    if p.exists() and p.read_text() != contents:
        raise ValueError('Refuse changed generated editor project hook')
    if not p.exists():
        p.write_text(contents)
    return p


def plan(repo, root, prefixes, toolkit, host, name, jobs, sse_directory):
    selected = profile(name, host.get('imported_targets', {}))
    t = toolkit['tools']; python = Path(prefixes['python_native']); pure = Path(prefixes['pure_resources'])
    p = {k: Path(v) for k, v in prefixes.items()}
    build = root / 'build'; install = root / 'install'
    options = dict(selected['required_options'])
    options.update({
        'CMAKE_TOOLCHAIN_FILE': toolkit['paths']['toolchain_file'],
        'CMAKE_PROJECT_INCLUDE': root / 'configuration/editor-project.cmake',
        'CMAKE_MAKE_PROGRAM': t['ninja'], 'CMAKE_BUILD_TYPE': 'Release',
        'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON', 'CMAKE_CXX_SCAN_FOR_MODULES': 'OFF',
        'CMAKE_INSTALL_PREFIX': install, 'CMAKE_INSTALL_DO_STRIP': 'OFF',
        # Build with the final intended RPATH, then install/relink and sign final bytes.
        'CMAKE_BUILD_WITH_INSTALL_RPATH': 'ON', 'CMAKE_INSTALL_RPATH': '$ORIGIN',
        'CMAKE_INSTALL_RPATH_USE_LINK_PATH': 'OFF',
        'CMAKE_PREFIX_PATH': ';'.join(str(p[x]) for x in ('volume', 'core', 'color', 'geometry', 'base', 'vulkan', 'gltf', 'python_native')),
        'CMAKE_JOB_POOLS': 'compile=' + str(jobs) + ';link=1', 'CMAKE_JOB_POOL_COMPILE': 'compile', 'CMAKE_JOB_POOL_LINK': 'link',
        'NINJA_MAX_NUM_PARALLEL_COMPILE_JOBS': str(jobs), 'NINJA_MAX_NUM_PARALLEL_COMPILE_HEAVY_JOBS': '1', 'NINJA_MAX_NUM_PARALLEL_LINK_JOBS': '1',
        'FETCHCONTENT_FULLY_DISCONNECTED': 'ON', 'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON',
        'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF', 'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF', 'CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH': 'OFF',
        'GIT_EXECUTABLE': t['git'], 'PKG_CONFIG_EXECUTABLE': t['pkgconf'],
        'SSE2NEON_INCLUDE_DIR': sse_directory,
        'OHOS_VOLUME_PREFIX': p['volume'], 'TBB_ROOT_DIR': p['volume'],
        'TBB_DIR': p['volume'] / 'lib/cmake/TBB', 'Imath_DIR': p['volume'] / 'lib/cmake/Imath', 'ZLIB_DIR': p['volume'] / 'lib/cmake/ZLIB',
        'PYTHON_VERSION': '3.13', 'PYTHON_ROOT_DIR': python, 'PYTHON_EXECUTABLE': python / 'bin/python3.13',
        'PYTHON_INCLUDE_DIR': python / 'include/python3.13', 'PYTHON_INCLUDE_CONFIG_DIR': python / 'include/python3.13',
        'PYTHON_LIBRARY': python / 'lib/libpython3.13.so', 'PYTHON_LIBPATH': python / 'lib',
        'PYTHON_SITE_PACKAGES': python / 'lib/python3.13/site-packages',
        'PYTHON_NUMPY_PATH': python / 'lib/python3.13/site-packages',
        'PYTHON_NUMPY_INCLUDE_DIRS': python / 'lib/python3.13/site-packages/numpy/_core/include',
        'PYTHON_REQUESTS_PATH': pure / 'site-packages',
        'PYTHON_SSL_CERT_FILE': pure / 'site-packages/certifi/cacert.pem',
        'SHADERC_ROOT_DIR': p['vulkan'], 'SHADERC_INCLUDE_DIR': p['vulkan'] / 'include', 'SHADERC_LIBRARY': p['vulkan'] / 'lib/libshaderc_combined.a',
        'VULKAN_ROOT_DIR': p['vulkan'], 'VULKAN_INCLUDE_DIR': p['vulkan'] / 'include', 'VULKAN_LIBRARY': host['vulkan_library']['path'],
        'OpenImageIO_DIR': p['color'] / 'lib/cmake/OpenImageIO', 'OpenColorIO_DIR': p['color'] / 'lib/cmake/OpenColorIO',
        'OpenEXR_DIR': p['color'] / 'lib/cmake/OpenEXR', 'fmt_DIR': p['color'] / 'lib/cmake/fmt',
        'OPENSUBDIV_ROOT_DIR': p['geometry'], 'GMP_ROOT_DIR': p['geometry'],
        'EMBREE_ROOT_DIR': p['core'], 'Eigen3_DIR': p['core'] / 'share/eigen3/cmake', 'Ceres_DIR': p['core'] / 'lib/cmake/Ceres',
        'absl_DIR': p['core'] / 'lib/cmake/absl', 'gflags_DIR': p['core'] / 'lib/cmake/gflags', 'glog_DIR': p['core'] / 'lib/cmake/glog',
        'GFLAGS_ROOT_DIR': p['core'], 'GLOG_ROOT_DIR': p['core'], 'GFLAGS_PREFER_EXPORTED_GFLAGS_CMAKE_CONFIGURATION': 'ON',
        'draco_DIR': p['gltf'] / 'lib/cmake/draco', 'meshoptimizer_DIR': p['gltf'] / 'lib/cmake/meshoptimizer',
    })
    configure = [t['cmake'], '-S', str(repo), '-B', str(build), '-G', 'Ninja'] + [f'-D{k}={v}' for k, v in sorted(options.items())]
    return {'schema': 1, 'status': 'PREPARED_NOT_RUN', 'profile': selected,
            'commands': {'configure': configure, 'build': [t['ninja'], '-C', str(build), '-j', str(jobs), '-k', '1', *selected['build_targets']],
                         'install': [t['cmake'], '--install', str(build), '--prefix', str(install)]},
            'build': str(build), 'install': str(install), 'source': str(repo), 'options': {k: str(v) for k, v in options.items()},
            'final_signing': 'After install/relink; strip disabled; then immutable final ELF SHA/SONAME/NEEDED/RPATH/source-object audit',
            'native_full': 'NOT_RUN', 'core_dlopen': 'NOT_RUN', 'bpy': 'NOT_RUN', 'SDL_window': 'NOT_RUN', 'HAP': 'NOT_RUN'}


def create_query(build):
    path = build / '.cmake/api/v1/query/client-editor-builder/query.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'requests': [{'kind': k, 'version': v} for k, v in [('codemodel', 2), ('cache', 2), ('toolchains', 1), ('cmakeFiles', 1)]]}
    if path.exists():
        raise ValueError('Refuse to overwrite prior File API query; configure a fresh owned build')
    dump(path, value)
