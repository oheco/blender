# SPDX-License-Identifier: GPL-2.0-or-later
"""Future actual loader/core9 probe. Run only with the selected genuine Python13."""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True

NAMES = ('blender_ohos_initialize', 'blender_ohos_pump', 'blender_ohos_stop',
         'blender_ohos_teardown', 'blender_ohos_file_command', 'ghost_ohos_host_create',
         'ghost_ohos_engine_start', 'ghost_ohos_native_retain', 'ghost_ohos_native_release')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core', required=True, type=Path)
    p.add_argument('--core-sha256', required=True)
    p.add_argument('--python-provider', required=True, type=Path)
    p.add_argument('--python-provider-sha256', required=True)
    args = p.parse_args()
    if sys.version_info[:3] != (3, 13, 13) or not sys.flags.isolated:
        raise ValueError('Explicit isolated actual CPython3.13.13 required')
    for path, expected in ((args.core, args.core_sha256), (args.python_provider, args.python_provider_sha256)):
        if path.is_symlink() or not path.is_file() or sha(path) != expected:
            raise ValueError('Audited native input changed before loader probe')
    core = C.CDLL(str(args.core), mode=C.RTLD_GLOBAL)
    functions = {n: C.cast(getattr(core, n), C.c_void_p).value for n in NAMES}
    if not all(functions.values()):
        raise ValueError('Core9 actual function address missing')
    # The ABI2 function's null-slot branch is bounded and cannot own/start a lifetime.
    fn = core.blender_ohos_teardown
    fn.argtypes = [C.POINTER(C.c_void_p), C.POINTER(C.c_int32)]
    fn.restype = C.c_int32
    code = C.c_int32(0x123456)
    status = fn(None, C.byref(code))
    if status >= 0 or code.value != 0x123456:
        raise ValueError('Actual ABI2 null-slot teardown changed status/independent exit-code contract')
    provider = C.CDLL(str(args.python_provider), mode=C.RTLD_GLOBAL)
    get_version = provider.Py_GetVersion
    get_version.restype = C.c_char_p
    if not get_version().decode().startswith('3.13.13') or C.cast(get_version, C.c_void_p).value != C.cast(C.pythonapi.Py_GetVersion, C.c_void_p).value:
        raise ValueError('Core probe uses a different Python C API provider')
    runtime = C.c_byte.in_dll(provider, '_PyRuntime')
    if C.addressof(runtime) != C.addressof(C.c_byte.in_dll(C.pythonapi, '_PyRuntime')):
        raise ValueError('Multiple Python runtime state providers')
    class Info(C.Structure):
        _fields_ = [('addr', C.c_void_p), ('name', C.c_char_p), ('phdr', C.c_void_p), ('phnum', C.c_ushort)]
    names = []
    callback_type = C.CFUNCTYPE(C.c_int, C.POINTER(Info), C.c_size_t, C.c_void_p)
    @callback_type
    def callback(info, size, unused):
        names.append((info.contents.name or b'').decode())
        return 0
    iterator = C.CDLL(None).dl_iterate_phdr
    iterator.argtypes = [callback_type, C.c_void_p]
    iterator.restype = C.c_int
    if iterator(callback, None) != 0:
        raise ValueError('Actual native library enumeration failed')
    providers = [Path(n).resolve() for n in names if n and Path(n).name.startswith('libpython')]
    if providers != [args.python_provider.resolve()]:
        raise ValueError('Expected exactly one genuine unversioned selected Python mapping: ' + repr(providers))
    print(json.dumps({'status': 'PASS_ACTUAL_CORE_DLOPEN_ABI2_CORE9', 'functions': functions,
                      'teardown_null_status': status, 'teardown_independent_exit_code_unchanged': True,
                      'core_sha256': args.core_sha256, 'python_provider_sha256': args.python_provider_sha256,
                      'python_provider': str(providers[0]), 'actual_native_libraries': names,
                      'Blender_initialize': 'NOT_RUN', 'bpy': 'NOT_RUN', 'SDL_window': 'NOT_RUN', 'HAP': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
