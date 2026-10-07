# SDK CMake 3.28 reports CMAKE_HOST_SYSTEM_PROCESSOR=unknown for HarmonyOS,
# despite real uname -m and clang target being aarch64. Query the native host;
# preserve HarmonyOS identity and native CMAKE_CROSSCOMPILING=FALSE.
execute_process(COMMAND /usr/bin/uname -m
  RESULT_VARIABLE _ohos_uname_status OUTPUT_VARIABLE _ohos_native_arch
  OUTPUT_STRIP_TRAILING_WHITESPACE)
if(NOT _ohos_uname_status EQUAL 0 OR NOT _ohos_native_arch STREQUAL "aarch64")
  message(FATAL_ERROR "Expected real native HarmonyOS aarch64 uname result")
endif()
if(NOT CMAKE_SYSTEM_NAME STREQUAL "HarmonyOS" OR CMAKE_CROSSCOMPILING)
  message(FATAL_ERROR "This recipe requires actual native HarmonyOS, not a Linux alias")
endif()
set(CMAKE_SYSTEM_PROCESSOR "${_ohos_native_arch}")
set(CMAKE_HOST_SYSTEM_PROCESSOR "${_ohos_native_arch}")
