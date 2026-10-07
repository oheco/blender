# Native HarmonyOS platform information, not a Linux platform alias.
# SDK probes verify POSIX pthread/dlopen/mmap/stat/futimens/atomic APIs and ELF;
# clang emits real aarch64-unknown-linux-ohos and lld is the native SDK linker.
# Do not set compiler identity, CMAKE_SYSTEM_NAME, cross status, or probe results.
set(UNIX 1)
set(CMAKE_SHARED_LIBRARY_C_FLAGS "-fPIC")
set(CMAKE_SHARED_LIBRARY_CXX_FLAGS "-fPIC")
set(CMAKE_SHARED_LIBRARY_CREATE_C_FLAGS "-shared")
set(CMAKE_SHARED_LIBRARY_CREATE_CXX_FLAGS "-shared")
set(CMAKE_SHARED_LIBRARY_RUNTIME_C_FLAG "-Wl,-rpath,")
set(CMAKE_SHARED_LIBRARY_RUNTIME_C_FLAG_SEP ":")
set(CMAKE_SHARED_LIBRARY_RPATH_LINK_C_FLAG "-Wl,-rpath-link,")
set(CMAKE_SHARED_LIBRARY_SONAME_C_FLAG "-Wl,-soname,")
set(CMAKE_PLATFORM_USES_PATH_WHEN_NO_SONAME 1)
set(CMAKE_DL_LIBS "dl")
