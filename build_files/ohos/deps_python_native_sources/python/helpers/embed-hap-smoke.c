/* Basic native embedding gate before Blender/NumPy integration. */
#include <Python.h>
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: embed-smoke /runtime/prefix\n"); return 2; }
    /* Own isolated startup decodes prefix bytes as UTF-8 before any PyConfig setter. */
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    PyStatus status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) { Py_ExitStatusException(status); }
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.site_import = 0;
    config.write_bytecode = 0;
    status = PyConfig_SetBytesString(&config, &config.home, argv[1]);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) { Py_ExitStatusException(status); }
    PyGILState_STATE gil = PyGILState_Ensure();
    int result = PyRun_SimpleString(
        "import sys, sysconfig, ssl, hashlib, ctypes, sqlite3, zlib, bz2, lzma\n"
        "assert sys.version_info[:3] == (3,13,13)\n"
        "assert sys.platform == 'ohos'\n"
        "assert sysconfig.get_config_var('SOABI') == 'cpython-313-aarch64-linux-ohos'\n"
        "assert not sysconfig.get_config_var('Py_GIL_DISABLED')\n"
        "assert zlib.decompress(zlib.compress(b'embedding')) == b'embedding'\n"
        "callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda x: x + 11)\n"
        "assert callback(31) == 42\n"
        "assert sqlite3.connect(':memory:').execute('select 42').fetchone() == (42,)\n"
        "print('OHOS native embedding: CPython3.13.13/GIL/SSL/ffi/sqlite/zlib PASS')\n");
    PyGILState_Release(gil);
    if (Py_FinalizeEx() < 0) return 120;
    return result ? 1 : 0;
}
