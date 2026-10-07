# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.20)
project(blender_ohos_host LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17) # Bridge only; the separate Blender core still requires Clang >=17/C++20.
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set_property(GLOBAL PROPERTY JOB_POOLS blender_hap_native=1)
set(CMAKE_JOB_POOL_COMPILE blender_hap_native)
set(CMAKE_JOB_POOL_LINK blender_hap_native)
# Resolve this project after migration; never use a developer HOME path in the HAP.
get_filename_component(HOST_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../../.." ABSOLUTE)
set(BLENDER_SOURCE "" CACHE PATH "Parent's actual patched Blender source tree")
set(BLENDER_CORE_LIBRARY "" CACHE FILEPATH "Signed/PIC native shared Blender core exporting creator_ohos + GHOST ABI")
option(BLENDER_OHOS_MALEOON_TILE_EXPERIMENT
  "Request the LIMITED Maleoon tile-blend startup trial; HAP/WSI unverified" OFF)
if(NOT EXISTS "${BLENDER_SOURCE}/intern/ghost/GHOST_OHOSHost.h")
  message(FATAL_ERROR "Pass -DBLENDER_SOURCE with the reviewed formal GHOST/entry integration")
endif()
if(NOT EXISTS "${BLENDER_CORE_LIBRARY}")
  message(FATAL_ERROR "Pass -DBLENDER_CORE_LIBRARY; a host-only stub cannot produce a Blender HAP")
endif()
add_library(blender_core SHARED IMPORTED)
set_target_properties(blender_core PROPERTIES IMPORTED_LOCATION "${BLENDER_CORE_LIBRARY}")
add_library(blender_host SHARED napi_init.cc host_bridge.cc reliable_input.cc xcomponent_input.cc ime_input.cc)
if(BLENDER_OHOS_MALEOON_TILE_EXPERIMENT)
  target_compile_definitions(blender_host PRIVATE BLENDER_OHOS_MALEOON_TILE_EXPERIMENT=1)
endif()
if(NOT EXISTS "${BLENDER_SOURCE}/source/creator/creator_ohos_files.h")
  message(FATAL_ERROR "Apply reviewed 0004 file API plus creator lifecycle rev2 to the real core before building this host")
endif()
# Consume the SAME rev2 ABI header as the linked core. A stale overlay/header with
# the same teardown symbol name would silently pass linking with the wrong ABI.
target_include_directories(blender_host PRIVATE "${BLENDER_SOURCE}/intern/ghost" "${BLENDER_SOURCE}/source/creator")
target_compile_options(blender_host PRIVATE -Wall -Wextra -fvisibility=hidden)
target_link_options(blender_host PRIVATE "LINKER:--no-undefined" "LINKER:--threads=1" "LINKER:--version-script=${CMAKE_CURRENT_LIST_DIR}/host.exports.map")
target_link_libraries(blender_host PRIVATE blender_core ace_ndk.z ace_napi.z hilog_ndk.z ohinputmethod)
# Core already owns ONE GHOST queue/launcher and ONE shared Python runtime.
# Do not compile private duplicate GHOST/Python into this DSO.
