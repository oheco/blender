/* Shared consumer fixture used by terminal dlopen and the NAPI adapter probe. */
#include <Python.h>
#include <stdio.h>

__attribute__((visibility("default"))) void *hap_runtime_address(void) {
    return (void *)Py_IsInitialized;
}
static PyStatus add_search_path(PyConfig *config, const char *path) {
    size_t length;
    wchar_t *wide = Py_DecodeLocale(path, &length);
    if (!wide) return length == (size_t)-2 ? PyStatus_Error("cannot decode module search path") : PyStatus_NoMemory();
    PyStatus status = PyWideStringList_Append(&config->module_search_paths, wide);
    PyMem_RawFree(wide);
    return status;
}
static int run_probe(const char *prefix, const char *native_dir) {
    if (Py_IsInitialized()) return 21;
    /* Set decoding before SetBytesString implicitly preinitializes in locale mode. */
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    PyStatus status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        if (PyStatus_IsExit(status)) fprintf(stderr, "PyPreConfig requested exit: %d\n", status.exitcode);
        else fprintf(stderr, "PyPreConfig failed: %s\n", status.err_msg ? status.err_msg : "unknown");
        return 22;
    }
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.site_import = 0;
    config.write_bytecode = 0;
    status = PyConfig_SetBytesString(&config, &config.home, prefix);
    if (native_dir && !PyStatus_Exception(status)) {
        char standard_library[4096];
        if (snprintf(standard_library, sizeof(standard_library), "%s/lib/python3.13", prefix) >= (int)sizeof(standard_library)) {
            PyConfig_Clear(&config); return 24;
        }
        config.module_search_paths_set = 1;
        status = add_search_path(&config, native_dir);
        if (!PyStatus_Exception(status)) status = add_search_path(&config, standard_library);
    }
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        if (PyStatus_IsExit(status)) fprintf(stderr, "PyConfig requested exit: %d\n", status.exitcode);
        else fprintf(stderr, "PyConfig failed: %s\n", status.err_msg ? status.err_msg : "unknown");
        return 22;
    }
    PyGILState_STATE gil = PyGILState_Ensure();
    int result = PyRun_SimpleString(
        "import sys,sysconfig,ssl,ctypes,sqlite3,zlib,bz2,lzma\n"
        "assert sys.version_info[:3] == (3,13,13)\n"
        "assert sysconfig.get_config_var('INSTSONAME') == 'libpython3.13.so'\n"
        "f=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int)(lambda n:n+12)\n"
        "assert f(30)==42\n"
        "print('Shared dlopen consumer: CPython3.13.13 SSL/ffi/sqlite/compression PASS')\n");
    PyGILState_Release(gil);
    int finished = Py_FinalizeEx();
    return result || finished < 0 ? 23 : 0;
}
__attribute__((visibility("default"))) int hap_python_probe(const char *prefix) {
    return run_probe(prefix, NULL);
}
__attribute__((visibility("default"))) int hap_python_probe_with_native_dir(const char *prefix, const char *native_dir) {
    return run_probe(prefix, native_dir);
}
