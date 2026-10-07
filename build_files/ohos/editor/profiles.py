# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit editor feature policies, preserving formal Blender/CMake behavior."""

FINAL_ON = '''WITH_STRICT_BUILD_OPTIONS WITH_BLENDER WITH_GHOST_OHOS WITH_GHOST_OHOS_EMBEDDED WITH_GHOST_OHOS_DIAGNOSTIC WITH_INSTALL_PORTABLE WITH_STATIC_LIBS WITH_PYTHON WITH_PYTHON_INSTALL WITH_PYTHON_NUMPY WITH_PYTHON_INSTALL_REQUESTS WITH_CYCLES WITH_CYCLES_EMBREE WITH_TBB WITH_LIBMV WITH_LIBMV_SCHUR_SPECIALIZATIONS WITH_SYSTEM_GFLAGS WITH_SYSTEM_GLOG WITH_OPENVDB WITH_OPENVDB_BLOSC WITH_NANOVDB WITH_FFTW3 WITH_MOD_OCEANSIM WITH_DRACO WITH_MESHOPTIMIZER WITH_VULKAN_BACKEND WITH_HARFBUZZ WITH_FRIBIDI WITH_OPENSUBDIV WITH_GMP WITH_MANIFOLD WITH_IO_WAVEFRONT_OBJ WITH_MOD_FLUID WITH_MOD_REMESH WITH_QUADRIFLOW WITH_UV_SLIM WITH_PUGIXML WITH_IMAGE_OPENJPEG WITH_IMAGE_WEBP WITH_IMAGE_CINEON WITH_INTERNATIONAL WITH_BULLET WITH_FREESTYLE WITH_IK_ITASC WITH_IK_SOLVER WITH_AUDASPACE WITH_LINKER_LLD'''.split()
REQUIRED_OFF = '''WITH_LIBS_PRECOMPILED WITH_PYTHON_MODULE WITH_PYTHON_INSTALL_NUMPY WITH_HEADLESS WITH_CPU_CHECK WITH_BLENDER_THUMBNAILER WITH_BINRELOC WITH_TBB_MALLOC_PROXY WITH_OPENGL_BACKEND WITH_GHOST_X11 WITH_GHOST_WAYLAND WITH_GHOST_SDL WITH_COMPILER_CCACHE WITH_OPENVDB_3_ABI_COMPATIBLE WITH_LINKER_MOLD'''.split()
NOT_ENABLED = '''WITH_OPENAL WITH_CODEC_SNDFILE WITH_CODEC_FFMPEG WITH_INPUT_NDOF WITH_CYCLES_OSL WITH_ALEMBIC WITH_USD WITH_HYDRA WITH_MATERIALX WITH_OPENIMAGEDENOISE WITH_XR_OPENXR WITH_POTRACE WITH_HARU WITH_RUBBERBAND WITH_CYCLES_PATH_GUIDING WITH_JACK WITH_PULSEAUDIO WITH_PIPEWIRE WITH_CYCLES_DEVICE_OPTIX WITH_PYTHON_INSTALL_ZSTANDARD WITH_CYCLES_DEVICE_CUDA WITH_CYCLES_DEVICE_HIP WITH_CYCLES_DEVICE_HIPRT WITH_CYCLES_DEVICE_ONEAPI WITH_CYCLES_DEVICE_METAL WITH_CYCLES_CUDA_BINARIES WITH_CYCLES_HIP_BINARIES WITH_CYCLES_ONEAPI_BINARIES WITH_CYCLES_STANDALONE WITH_CYCLES_STANDALONE_GUI WITH_CYCLES_HYDRA_RENDER_DELEGATE WITH_GTESTS'''.split()
BUILD_TARGETS = ['blender', 'blender-ohos-diagnostic', 'bf_intern_draco_bridge', 'bf_intern_meshopt_bridge']
TARGETS = BUILD_TARGETS + '''bf_intern_ghost bf_python bf_windowmanager bf_rna bf_blenkernel bf_intern_libmv bf_intern_cycles cycles_device cycles_scene cycles_kernel_cpu bf_intern_openvdb bf_modifiers bf_nodes_geometry bf_io_wavefront_obj bf_intern_opensubdiv'''.split()
BINDINGS = {
    'source/creator/creator.cc': ['source/creator/creator_ohos.h', 'source/creator/creator_ohos_files.h', 'source/creator/creator_ohos_files_impl.hh', 'source/creator/creator_ohos_files_path.hh'],
    'intern/ghost/intern/GHOST_OHOSHost.cc': ['intern/ghost/GHOST_OHOSHost.h'],
    'intern/ghost/intern/GHOST_OHOSEngine.cc': ['intern/ghost/GHOST_OHOSEngine.h', 'intern/ghost/GHOST_OHOSHost.h'],
    'intern/ghost/intern/GHOST_OHOSNative.cc': ['intern/ghost/GHOST_OHOSNative.h', 'intern/ghost/GHOST_OHOSHost.h'],
}
COMPILE = {
    'source/creator/creator.cc': ['WITH_GHOST_OHOS', 'WITH_GHOST_OHOS_EMBEDDED', 'WITH_PYTHON', 'WITH_LIBMV'],
    'source/blender/python/intern/bpy_interface.cc': ['WITH_PYTHON'],
    'intern/ghost/intern/GHOST_SystemOHOS.cc': ['WITH_GHOST_OHOS'],
    'source/blender/gpu/vulkan/vk_backend.cc': ['WITH_VULKAN_BACKEND'],
    'intern/cycles/device/cpu/device.cpp': ['WITH_EMBREE', 'WITH_TBB'],
    'intern/cycles/scene/volume.cpp': ['WITH_NANOVDB', 'NANOVDB_USE_OPENVDB', 'NANOVDB_USE_TBB', 'NANOVDB_USE_BLOSC', 'NANOVDB_USE_ZIP'],
    'intern/openvdb/openvdb_capi.cc': ['WITH_OPENVDB', 'WITH_OPENVDB_BLOSC', 'OPENVDB_STATICLIB', 'OPENVDB_OPENEXR_STATICLIB', '__TBB_NO_IMPLICIT_LINKAGE'],
    'source/blender/blenkernel/intern/ocean.cc': ['WITH_FFTW3', 'WITH_OCEANSIM'],
    'source/blender/blenkernel/intern/tracking.cc': ['WITH_LIBMV'],
    'source/blender/modifiers/intern/MOD_nodes.cc': [],
    'source/blender/editors/sculpt_paint/mesh/sculpt.cc': [],
    'source/blender/editors/uvedit/uvedit_ops.cc': [],
    'source/blender/blenkernel/intern/material.cc': [],
    'intern/draco_bridge/intern/common.cpp': ['WITH_DRACO'],
    'intern/meshoptimizer_bridge/intern/common.cpp': ['WITH_MESHOPTIMIZER'],
}
VOLUME = {'TBB::tbb': 'libtbb.a', 'TBB::tbbmalloc': 'libtbbmalloc.a', 'Imath::Imath': 'libImath-3_2.a', 'ZLIB::ZLIB': 'libz.a', 'Blosc::blosc': 'libblosc.a', 'OpenVDB::openvdb': 'libopenvdb.a', 'FFTW3::fftw3': 'libfftw3.a', 'FFTW3::fftw3_threads': 'libfftw3_threads.a', 'FFTW3::fftw3f': 'libfftw3f.a', 'FFTW3::fftw3f_threads': 'libfftw3f_threads.a'}


def profile(name, imports=None):
    if name not in ('final', 'diagnostic'):
        raise ValueError('Unknown editor profile')
    options = {x: 'ON' for x in FINAL_ON}
    options.update({x: 'OFF' for x in REQUIRED_OFF + NOT_ENABLED})
    compile_defs = {k: list(v) for k, v in COMPILE.items()}
    if name == 'diagnostic':
        options.update(WITH_STRICT_BUILD_OPTIONS='OFF', WITH_LIBMV='OFF')
        for values in compile_defs.values():
            if 'WITH_LIBMV' in values:
                values.remove('WITH_LIBMV')
    targets = {k: {'prefix': 'volume', 'path': 'lib/' + v} for k, v in VOLUME.items()}
    targets.update(imports or {})
    consumed = []
    prefix_headers = {}
    if name == 'final':
        for target, archive in {'Ceres::ceres': 'libceres.a', 'gflags_static': 'libgflags.a', 'glog::glog': 'libglog.a'}.items():
            expected = {'prefix': 'core', 'path': 'lib/' + archive}
            if target in targets and targets[target] != expected:
                raise ValueError('Canonical Libmv core provider cannot be overridden')
            targets[target] = expected
            consumed.append(expected)
        for source in ('intern/libmv/libmv/simple_pipeline/bundle.cc', 'intern/libmv/intern/logging.cc'):
            compile_defs[source] = ['WITH_CERES', 'EIGEN_HAS_TBB', 'LIBMV_USE_TBB_THREADS', 'LIBMV_GFLAGS_NAMESPACE']
        prefix_headers = {
            'intern/libmv/libmv/simple_pipeline/bundle.cc': [{'prefix': 'core', 'path': 'include/ceres/ceres.h'}, {'prefix': 'core', 'path': 'include/eigen3/Eigen/Core'}],
            'intern/libmv/intern/logging.cc': [{'prefix': 'core', 'path': 'include/gflags/gflags.h'}, {'prefix': 'core', 'path': 'include/glog/logging.h'}],
        }
    return {'name': name, 'required_options': options, 'required_compile': compile_defs,
            'required_targets': TARGETS, 'build_targets': BUILD_TARGETS,
            'implementation_bindings': BINDINGS, 'imported_targets': targets,
            'not_enabled': {x: 'NOT_ENABLED explicit profile declaration; enablement needs a new reviewed profile and source closure' for x in NOT_ENABLED},
            'libmv': 'REQUIRED real Ceres/Eigen/gflags/glog/static interfaces' if name == 'final' else 'DIAGNOSTIC_ONLY explicitly disabled; cannot satisfy final gate',
            'required_consumed_archives': consumed, 'prefix_header_bindings': prefix_headers,
            'numpy_install_strategy': 'WITH_PYTHON_NUMPY ON; exact accepted native runtime composition before final sign',
            'inspect_link_commands': True}
