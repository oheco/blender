#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise the signed C ABI from a native Python host (explicitly NOT a HAP test)."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('build_info', type=Path, nargs='?', default=HERE / 'results-verified/build-info.json')
    args = ap.parse_args()
    if sys.platform != 'ohos':
        raise SystemExit('Run on native OHOS')
    info = args.build_info.resolve()
    if HERE not in info.parents:
        raise SystemExit('Build metadata must be in probes')
    meta = json.loads(info.read_text())
    artifact = meta['artifacts']['libblender_vulkan_probe.so']
    library_path = Path(artifact['path'])
    with library_path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != artifact['sha256']:
            raise SystemExit('Signed shared-library SHA-256 changed')
    output = info.parent / 'vulkan-shared-host.json'
    if output.exists():
        raise SystemExit('Refusing to overwrite existing shared-host report')
    library = ctypes.CDLL(str(library_path))
    probe = library.blender_ohos_vulkan_report
    probe.argtypes = (ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))
    probe.restype = ctypes.c_void_p
    libc = ctypes.CDLL(None)
    libc.free.argtypes = (ctypes.c_void_p,)
    libc.free.restype = None
    status = ctypes.c_int(2)
    pointer = probe(0, b'python_terminal_host_not_hap', ctypes.byref(status))
    if not pointer:
        raise SystemExit('Probe returned a null JSON allocation')
    try:
        report = json.loads(ctypes.string_at(pointer).decode('utf-8'))
    finally:
        libc.free(pointer)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(output)
    print(f'enumeration_status={status.value}; this is not a HAP execution test')
    raise SystemExit(status.value)

if __name__ == '__main__':
    main()
