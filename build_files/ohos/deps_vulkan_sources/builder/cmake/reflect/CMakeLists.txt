# SPDX-License-Identifier: GPL-2.0-or-later
cmake_minimum_required(VERSION 3.28)
project(PortablePinnedReflect LANGUAGES C CXX)
if(NOT IS_DIRECTORY "${REFLECT_SOURCE}" OR NOT IS_DIRECTORY "${SPIRV_PUBLIC_INCLUDE}")
  message(FATAL_ERROR "Complete caller-owned Reflect and installed SPIRV-Headers required")
endif()
add_subdirectory("${REFLECT_SOURCE}" "${CMAKE_CURRENT_BINARY_DIR}/upstream")
target_compile_definitions(spirv-reflect-static PUBLIC SPIRV_REFLECT_USE_SYSTEM_SPIRV_H)
target_include_directories(spirv-reflect-static PRIVATE "${SPIRV_PUBLIC_INCLUDE}")
