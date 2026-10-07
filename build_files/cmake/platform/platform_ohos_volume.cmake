# SPDX-License-Identifier: GPL-2.0-or-later
# Optional native OHOS volume CONFIG integration. The caller selects a prefix
# whose complete native builder acceptance has already passed. This file never
# changes compiler identity, cross status, native probes, or feature options.

if(NOT WITH_GHOST_OHOS OR NOT OHOS_VOLUME_PREFIX)
  message(FATAL_ERROR "OHOS volume CONFIG integration needs an explicit OHOS_VOLUME_PREFIX")
endif()
if(WITH_LIBS_PRECOMPILED)
  message(FATAL_ERROR "OHOS volume CONFIG integration cannot use desktop precompiled LIBDIR")
endif()
if(WITH_TBB_MALLOC_PROXY)
  message(FATAL_ERROR "The selected OHOS volume closure does not build TBB malloc proxy")
endif()

get_filename_component(_OHOS_VOLUME_PREFIX "${OHOS_VOLUME_PREFIX}" REALPATH)
if(NOT IS_DIRECTORY "${_OHOS_VOLUME_PREFIX}/include")
  message(FATAL_ERROR "OHOS volume prefix has no installed headers: ${OHOS_VOLUME_PREFIX}")
endif()

# Packages use if(NOT TARGET ...) guards. Reject a previous import from another
# prefix rather than silently accepting the same target name with different bytes.
function(ohos_volume_check_archive target archive)
  if(NOT TARGET "${target}")
    return()
  endif()
  get_target_property(_type "${target}" TYPE)
  if(NOT _type STREQUAL "STATIC_LIBRARY")
    message(FATAL_ERROR "OHOS volume target ${target} is not a static archive")
  endif()
  set(_found FALSE)
  foreach(_property IMPORTED_LOCATION IMPORTED_LOCATION_RELEASE IMPORTED_LOCATION_DEBUG
                    IMPORTED_LOCATION_RELWITHDEBINFO IMPORTED_LOCATION_MINSIZEREL
                    IMPORTED_LOCATION_NOCONFIG)
    get_target_property(_location "${target}" "${_property}")
    if(_location)
      get_filename_component(_actual "${_location}" REALPATH)
      get_filename_component(_expected "${_OHOS_VOLUME_PREFIX}/lib/${archive}" REALPATH)
      if(NOT EXISTS "${_location}" OR NOT _actual STREQUAL _expected)
        message(FATAL_ERROR
          "OHOS volume target ${target} already selects ${_location}; expected ${_expected}. "
          "Choose the volume CONFIG packages before other dependency imports.")
      endif()
      set(_found TRUE)
    endif()
  endforeach()
  if(NOT _found)
    message(FATAL_ERROR "OHOS volume target ${target} has no imported archive location")
  endif()
endfunction()

# Normal variables intentionally mask any old cached *_DIR values. Import shared
# prerequisites before OpenEXR/OCIO/OIIO/Embree. They must all reuse these targets;
# changing an existing imported target's archive location is deliberately avoided.
set(TBB_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/TBB")
set(Imath_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/Imath")
set(ZLIB_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/ZLIB")
set(Blosc_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/Blosc")
set(OpenVDB_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/OpenVDB")
set(FFTW3_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/fftw3")
set(FFTW3f_DIR "${_OHOS_VOLUME_PREFIX}/lib/cmake/fftw3f")

foreach(_row "TBB::tbb|libtbb.a" "TBB::tbbmalloc|libtbbmalloc.a"
             "Imath::Imath|libImath-3_2.a" "ZLIB::ZLIB|libz.a"
             "Blosc::blosc|libblosc.a" "OpenVDB::openvdb|libopenvdb.a"
             "FFTW3::fftw3|libfftw3.a" "FFTW3::fftw3_threads|libfftw3_threads.a"
             "FFTW3::fftw3f|libfftw3f.a" "FFTW3::fftw3f_threads|libfftw3f_threads.a")
  string(REPLACE "|" ";" _parts "${_row}")
  list(GET _parts 0 _target)
  list(GET _parts 1 _archive)
  ohos_volume_check_archive("${_target}" "${_archive}")
endforeach()

find_package(TBB 2022.3.0 EXACT CONFIG REQUIRED PATHS "${TBB_DIR}" NO_DEFAULT_PATH)
find_package(Imath 3.2.2 EXACT CONFIG REQUIRED PATHS "${Imath_DIR}" NO_DEFAULT_PATH)
find_package(ZLIB 1.3.1 EXACT CONFIG REQUIRED PATHS "${ZLIB_DIR}" NO_DEFAULT_PATH)

# Preserve the variables used by Blender's module-style Zlib lookup, PNG and
# FreeType. Their later lookup must not reintroduce the previous prefix's archive.
set(ZLIB_INCLUDE_DIR "${_OHOS_VOLUME_PREFIX}/include")
set(ZLIB_INCLUDE_DIRS "${ZLIB_INCLUDE_DIR}")
set(ZLIB_LIBRARY "${_OHOS_VOLUME_PREFIX}/lib/libz.a")
set(ZLIB_LIBRARY_RELEASE "${ZLIB_LIBRARY}")
set(ZLIB_LIBRARY_DEBUG "${ZLIB_LIBRARY}")
set(ZLIB_LIBRARIES ZLIB::ZLIB)

if(WITH_OPENVDB)
  find_package(OpenVDB 13.0.0 EXACT CONFIG REQUIRED PATHS "${OpenVDB_DIR}" NO_DEFAULT_PATH)
  if(NOT TARGET OpenVDB::openvdb)
    message(FATAL_ERROR "OHOS OpenVDB CONFIG does not provide OpenVDB::openvdb")
  endif()
  set(OPENVDB_FOUND TRUE)
  set(OPENVDB_INCLUDE_DIR "${_OHOS_VOLUME_PREFIX}/include")
  set(OPENVDB_INCLUDE_DIRS "${OPENVDB_INCLUDE_DIR}")
  set(OPENVDB_LIBRARY "${_OHOS_VOLUME_PREFIX}/lib/libopenvdb.a")
  set(OPENVDB_LIBRARIES OpenVDB::openvdb)
  set(OPENVDB_DEFINITIONS "")
endif()
if(WITH_NANOVDB)
  if(NOT WITH_OPENVDB OR NOT TARGET OpenVDB::nanovdb)
    message(FATAL_ERROR "OHOS NanoVDB needs WITH_OPENVDB and the accepted CONFIG target")
  endif()
  set(NANOVDB_FOUND TRUE)
  set(NANOVDB_INCLUDE_DIR "${_OHOS_VOLUME_PREFIX}/include")
  set(NANOVDB_INCLUDE_DIRS "${NANOVDB_INCLUDE_DIR}")
endif()
if(WITH_FFTW3)
  find_package(FFTW3 3.3.10 EXACT CONFIG REQUIRED PATHS "${FFTW3_DIR}" NO_DEFAULT_PATH)
  find_package(FFTW3f 3.3.10 EXACT CONFIG REQUIRED PATHS "${FFTW3f_DIR}" NO_DEFAULT_PATH)
  foreach(_target FFTW3::fftw3_threads FFTW3::fftw3f_threads)
    if(NOT TARGET "${_target}")
      message(FATAL_ERROR "OHOS FFTW CONFIG does not provide ${_target}")
    endif()
  endforeach()
  set(FFTW3_FOUND TRUE)
  set(FFTW3_INCLUDE_DIR "${_OHOS_VOLUME_PREFIX}/include")
  set(FFTW3_INCLUDE_DIRS "${FFTW3_INCLUDE_DIR}")
  set(FFTW3_LIBRARY_D "${_OHOS_VOLUME_PREFIX}/lib/libfftw3.a")
  set(FFTW3_LIBRARY_F "${_OHOS_VOLUME_PREFIX}/lib/libfftw3f.a")
  set(FFTW3_LIBRARY_THREADS_F "${_OHOS_VOLUME_PREFIX}/lib/libfftw3f_threads.a")
  set(FFTW3_LIBRARIES FFTW3::fftw3_threads FFTW3::fftw3f_threads)
endif()

foreach(_row "TBB::tbb|libtbb.a" "TBB::tbbmalloc|libtbbmalloc.a"
             "Imath::Imath|libImath-3_2.a" "ZLIB::ZLIB|libz.a"
             "Blosc::blosc|libblosc.a" "OpenVDB::openvdb|libopenvdb.a"
             "FFTW3::fftw3|libfftw3.a" "FFTW3::fftw3_threads|libfftw3_threads.a"
             "FFTW3::fftw3f|libfftw3f.a" "FFTW3::fftw3f_threads|libfftw3f_threads.a")
  string(REPLACE "|" ";" _parts "${_row}")
  list(GET _parts 0 _target)
  list(GET _parts 1 _archive)
  ohos_volume_check_archive("${_target}" "${_archive}")
endforeach()
unset(_row)
unset(_parts)
unset(_target)
unset(_archive)
set(_OHOS_VOLUME_CONFIG_ACTIVE TRUE)
