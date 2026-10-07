# SPDX-License-Identifier: GPL-2.0-or-later
# CMAKE_ROOT and CMAKE_BINARY_DIR are the actual consuming CMake invocation.
# Capture its selected module source before project starts compiler detection.
foreach(_capture_input IN ITEMS OHOS_ABI_CAPTURE_PYTHON OHOS_ABI_CAPTURE_CONFIG OHOS_ABI_CAPTURE_STORE)
  if(NOT DEFINED ${_capture_input})
    message(FATAL_ERROR "Original capture input missing: ${_capture_input}")
  endif()
endforeach()
execute_process(COMMAND "${OHOS_ABI_CAPTURE_PYTHON}" "${OHOS_ABI_CAPTURE_STORE}"
  --config "${OHOS_ABI_CAPTURE_CONFIG}" --cmake-root "${CMAKE_ROOT}"
  --binary-root "${CMAKE_BINARY_DIR}" RESULT_VARIABLE _capture_exit)
if(NOT _capture_exit EQUAL 0)
  message(FATAL_ERROR "Original CMake module capture failed; native facts remain NOTREADY")
endif()
