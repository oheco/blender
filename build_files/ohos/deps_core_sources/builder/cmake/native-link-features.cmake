# SPDX-License-Identifier: GPL-2.0-or-later
# Actual native lld archive-retention checks; never preset successful probes.
include_guard(GLOBAL)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "HarmonyOS" OR CMAKE_CROSSCOMPILING)
  message(FATAL_ERROR "Core link features require actual native HarmonyOS")
endif()
include(CheckLinkerFlag)
check_linker_flag(CXX "LINKER:--push-state,--whole-archive,--pop-state"
  CORE_WHOLE_ARCHIVE_LINKER_SUPPORTED)
if(NOT CORE_WHOLE_ARCHIVE_LINKER_SUPPORTED)
  message(FATAL_ERROR "Native linker does not accept whole-archive state flags")
endif()
set(_core_probe_sources "${CMAKE_CURRENT_LIST_DIR}/../consumers")
set(_core_marker "${CMAKE_BINARY_DIR}/core-link-probe/libwhole-marker.a")
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/core-link-probe")
set(_core_saved_try_type "${CMAKE_TRY_COMPILE_TARGET_TYPE}")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
try_compile(CORE_MARKER_COMPILED SOURCES "${_core_probe_sources}/whole_archive_marker.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20" "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"
  COPY_FILE "${_core_marker}" OUTPUT_VARIABLE CORE_MARKER_OUTPUT)
if(_core_saved_try_type STREQUAL "")
  unset(CMAKE_TRY_COMPILE_TARGET_TYPE)
else()
  set(CMAKE_TRY_COMPILE_TARGET_TYPE "${_core_saved_try_type}")
endif()
if(NOT CORE_MARKER_COMPILED)
  message(FATAL_ERROR "Real archive marker compilation failed: ${CORE_MARKER_OUTPUT}")
endif()
try_run(CORE_PLAIN_RUN CORE_PLAIN_COMPILED
  SOURCES "${_core_probe_sources}/whole_archive_probe.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20"
  LINK_LIBRARIES "${_core_marker}"
  COMPILE_OUTPUT_VARIABLE CORE_PLAIN_COMPILE_OUTPUT RUN_OUTPUT_VARIABLE CORE_PLAIN_OUTPUT)
try_run(CORE_WHOLE_RUN CORE_WHOLE_COMPILED
  SOURCES "${_core_probe_sources}/whole_archive_probe.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20"
  LINK_OPTIONS "LINKER:--push-state,--whole-archive" "${_core_marker}" "LINKER:--pop-state"
  COMPILE_OUTPUT_VARIABLE CORE_WHOLE_COMPILE_OUTPUT RUN_OUTPUT_VARIABLE CORE_WHOLE_OUTPUT)
file(WRITE "${CMAKE_BINARY_DIR}/core-link-probe/result.txt"
  "plain_compile=${CORE_PLAIN_COMPILED}\nplain_run=${CORE_PLAIN_RUN}\n${CORE_PLAIN_OUTPUT}\nwhole_compile=${CORE_WHOLE_COMPILED}\nwhole_run=${CORE_WHOLE_RUN}\n${CORE_WHOLE_OUTPUT}\n${CORE_PLAIN_COMPILE_OUTPUT}\n${CORE_WHOLE_COMPILE_OUTPUT}")
if(NOT CORE_PLAIN_COMPILED OR NOT CORE_PLAIN_RUN EQUAL 1 OR
   NOT CORE_PLAIN_OUTPUT MATCHES "marker=0" OR
   NOT CORE_WHOLE_COMPILED OR NOT CORE_WHOLE_RUN EQUAL 0 OR
   NOT CORE_WHOLE_OUTPUT MATCHES "marker=73")
  message(FATAL_ERROR "Actual signed whole-archive semantics failed; inspect core-link-probe/result.txt")
endif()
set(CMAKE_LINK_LIBRARY_USING_WHOLE_ARCHIVE
  "LINKER:--push-state,--whole-archive" "<LINK_ITEM>" "LINKER:--pop-state")
set(CMAKE_LINK_LIBRARY_USING_WHOLE_ARCHIVE_SUPPORTED TRUE)
