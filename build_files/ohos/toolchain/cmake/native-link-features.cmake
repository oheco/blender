# SPDX-License-Identifier: GPL-2.0-or-later
# Actual native lld archive-retention checks; never preset successful probes.
include_guard(GLOBAL)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "HarmonyOS" OR CMAKE_CROSSCOMPILING)
  message(FATAL_ERROR "Toolkit link features require actual native HarmonyOS")
endif()
include(CheckLinkerFlag)
check_linker_flag(CXX "LINKER:--push-state,--whole-archive,--pop-state"
  TOOLKIT_WHOLE_ARCHIVE_LINKER_SUPPORTED)
if(NOT TOOLKIT_WHOLE_ARCHIVE_LINKER_SUPPORTED)
  message(FATAL_ERROR "Native linker does not accept whole-archive state flags")
endif()
set(_toolkit_probe_sources "${CMAKE_CURRENT_LIST_DIR}/../probes")
set(_toolkit_marker "${CMAKE_BINARY_DIR}/toolkit-link-probe/libwhole-marker.a")
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/toolkit-link-probe")
set(_toolkit_saved_try_type "${CMAKE_TRY_COMPILE_TARGET_TYPE}")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
try_compile(TOOLKIT_MARKER_COMPILED SOURCES "${_toolkit_probe_sources}/whole_archive_marker.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20" "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"
  COPY_FILE "${_toolkit_marker}" OUTPUT_VARIABLE TOOLKIT_MARKER_OUTPUT)
if(_toolkit_saved_try_type STREQUAL "")
  unset(CMAKE_TRY_COMPILE_TARGET_TYPE)
else()
  set(CMAKE_TRY_COMPILE_TARGET_TYPE "${_toolkit_saved_try_type}")
endif()
if(NOT TOOLKIT_MARKER_COMPILED)
  message(FATAL_ERROR "Real archive marker compilation failed: ${TOOLKIT_MARKER_OUTPUT}")
endif()
try_run(TOOLKIT_PLAIN_RUN TOOLKIT_PLAIN_COMPILED
  SOURCES "${_toolkit_probe_sources}/whole_archive_probe.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20"
  LINK_LIBRARIES "${_toolkit_marker}"
  COMPILE_OUTPUT_VARIABLE TOOLKIT_PLAIN_COMPILE_OUTPUT RUN_OUTPUT_VARIABLE TOOLKIT_PLAIN_OUTPUT)
try_run(TOOLKIT_WHOLE_RUN TOOLKIT_WHOLE_COMPILED
  SOURCES "${_toolkit_probe_sources}/whole_archive_probe.cc"
  CMAKE_FLAGS "-DCMAKE_CXX_STANDARD=20"
  LINK_OPTIONS "LINKER:--push-state,--whole-archive" "${_toolkit_marker}" "LINKER:--pop-state"
  COMPILE_OUTPUT_VARIABLE TOOLKIT_WHOLE_COMPILE_OUTPUT RUN_OUTPUT_VARIABLE TOOLKIT_WHOLE_OUTPUT)
file(WRITE "${CMAKE_BINARY_DIR}/toolkit-link-probe/result.txt"
  "plain_compile=${TOOLKIT_PLAIN_COMPILED}\nplain_run=${TOOLKIT_PLAIN_RUN}\n${TOOLKIT_PLAIN_OUTPUT}\nwhole_compile=${TOOLKIT_WHOLE_COMPILED}\nwhole_run=${TOOLKIT_WHOLE_RUN}\n${TOOLKIT_WHOLE_OUTPUT}\n${TOOLKIT_PLAIN_COMPILE_OUTPUT}\n${TOOLKIT_WHOLE_COMPILE_OUTPUT}")
if(NOT TOOLKIT_PLAIN_COMPILED OR NOT TOOLKIT_PLAIN_RUN EQUAL 1 OR
   NOT TOOLKIT_PLAIN_OUTPUT MATCHES "marker=0" OR
   NOT TOOLKIT_WHOLE_COMPILED OR NOT TOOLKIT_WHOLE_RUN EQUAL 0 OR
   NOT TOOLKIT_WHOLE_OUTPUT MATCHES "marker=73")
  message(FATAL_ERROR "Actual signed whole-archive semantics failed; inspect toolkit-link-probe/result.txt")
endif()
set(CMAKE_LINK_LIBRARY_USING_WHOLE_ARCHIVE
  "LINKER:--push-state,--whole-archive" "<LINK_ITEM>" "LINKER:--pop-state")
set(CMAKE_LINK_LIBRARY_USING_WHOLE_ARCHIVE_SUPPORTED TRUE)
