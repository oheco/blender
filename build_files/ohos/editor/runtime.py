# SPDX-License-Identifier: GPL-2.0-or-later
"""Compose already accepted native13/pure8 bytes before final installed signing."""
from pathlib import Path
import os
import shutil
from common import inventory, load, sha, record, dump


def compose_runtime(install, python, pure, records):
    target = install / '5.2/python'
    native = inventory(python)
    selected = [row for row in native if row['path'] == 'bin/python3.13' or row['path'] == 'lib/libpython3.13.so' or
                row['path'].startswith(('lib/python3.13/', 'include/python3.13/'))]
    required = {'bin/python3.13', 'lib/libpython3.13.so', 'lib/python3.13/encodings/__init__.py', 'lib/python3.13/site-packages/numpy/__init__.py'}
    if not required <= {row['path'] for row in selected}:
        raise ValueError('Current accepted native runtime payload is incomplete')
    copied = []
    for row in selected:
        source = python / row['path']; dest = target / row['path']
        if source.is_symlink():
            raise ValueError('Runtime composition requires genuine regular unversioned source payload files')
        if dest.is_symlink():
            raise ValueError('Unexpected installation symlink in runtime composition')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        with source.open('rb') as stream:
            is_elf = stream.read(4) == b'\x7fELF'
        if is_elf:
            dest.chmod((source.stat().st_mode & 0o777) | 0o100)
        if sha(dest) != row['sha256']:
            raise ValueError('Runtime copy changed actual source bytes')
        copied.append({'source': record(source), 'installed_before_final_sign': record(dest)})
    resource = load(pure / 'resources.json')
    for row in resource['site_inventory']:
        source = pure / 'site-packages' / row['path']
        dest = target / 'lib/python3.13/site-packages' / row['path']
        if source.is_symlink() or source.stat().st_size != row['size'] or sha(source) != row['sha256']:
            raise ValueError('Separately accepted pure8 resource bytes changed')
        if dest.exists() and sha(dest) != row['sha256']:
            raise ValueError('Native runtime has a different pure8 composition')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        copied.append({'source': record(source), 'installed_before_final_sign': record(dest)})
    provider = target / 'lib/libpython3.13.so'
    if list((target / 'lib').glob('libpython*.so*')) != [provider] or provider.is_symlink():
        raise ValueError('Final runtime composition introduced multiple/versioned Python providers')
    # Root lib is the core/bridge resolution directory; its relative provider route
    # points to the one genuine regular payload and is not a SONAME/name rewrite.
    link = install / 'lib/libpython3.13.so'
    literal = '../5.2/python/lib/libpython3.13.so'
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or os.readlink(link) != literal:
            raise ValueError('Refuse a second adopted core-visible Python provider')
    else:
        link.symlink_to(literal)
    result = {'schema': 1, 'status': 'PASS_ACCEPTED_RUNTIME_BYTE_COMPOSITION_ONLY',
              'native_input_lock_sha256': records['python_native']['input_lock_sha256'],
              'native_receipt_sha256': records['python_native']['native_receipt_sha256'],
              'pure_input_lock_sha256': records['pure_resources']['input_lock_sha256'],
              'pure_native_receipt_sha256': records['pure_resources']['native_receipt_sha256'],
              'producer_metadata_scopes': records['python_native'].get('producer_metadata', []),
              'target': str(target), 'copies': copied, 'provider_relative_route': literal,
              'Cython': 'SOURCE_BUILD_TOOL_ONLY; WITH_PYTHON_INSTALL_NUMPY OFF, native NumPy runtime preserved',
              'final_signing': 'NOT_RUN; must follow all composition/install/relink', 'bpy': 'NOT_RUN', 'HAP': 'NOT_RUN'}
    dump(install.parent / 'runtime-composition.json', result)
    return result
