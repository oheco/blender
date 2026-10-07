# SPDX-License-Identifier: GPL-2.0-or-later
"""Load CPython/NumPy extensions directly from signed HAP native libraries.

Application data contains Python source and the validated path registry only.
The OS installed native directory remains the owner of every executable ELF.
"""
import sys
from importlib.machinery import ExtensionFileLoader, ModuleSpec


def _module_name(path):
    prefix = '5.2/python/lib/python3.13/'
    if not path.startswith(prefix):
        if path in ('5.2/python/lib/libpython3.13.so', '5.2/python/lib/libpython3.so'):
            return None
        raise ImportError('Unknown Blender HAP native path: ' + path)
    tail = path[len(prefix):]
    if tail.startswith('lib-dynload/') and '/' not in tail[len('lib-dynload/'):]:
        return tail.rsplit('/', 1)[-1].split('.', 1)[0]
    if tail.startswith('site-packages/numpy/'):
        parts = tail[len('site-packages/'):].split('/')
        parts[-1] = parts[-1].split('.', 1)[0]
        return '.'.join(parts)
    raise ImportError('Unknown Blender HAP extension path: ' + path)


class _HapNativeFinder:
    def __init__(self, mapping):
        self.mapping = mapping

    def find_spec(self, fullname, path=None, target=None):
        library = self.mapping.get(fullname)
        if library is None:
            return None
        # Full Python module name selects the genuine PyInit_<leaf> symbol;
        # the HAP filename can remain its original SONAME/hash-based basename.
        loader = ExtensionFileLoader(fullname, library)
        spec = ModuleSpec(fullname, loader, origin=library)
        spec.has_location = True
        return spec


def install():
    registry = __file__.rsplit('/', 1)[0] + '/blender_hap_native_paths.tsv'
    with open(registry, encoding='utf-8') as stream:
        lines = stream.read().splitlines()
    if len(lines) != 94 or lines[0] != 'BLENDER_HAP_NATIVE_V1':
        raise ImportError('Invalid Blender HAP native registry')
    root = lines[1]
    if not root.startswith('/') or any(ord(c) < 32 for c in root):
        raise ImportError('Invalid Blender HAP native root')
    mapping = {}
    paths = set()
    for line in lines[2:]:
        fields = line.split('\t')
        if len(fields) != 2:
            raise ImportError('Invalid Blender HAP native binding')
        relative, library = fields
        if (relative.startswith('/') or any(p in ('', '.', '..') for p in relative.split('/')) or
                not library.startswith('lib') or not library.endswith('.so') or
                '..' in library or any(not (c.isascii() and (c.isalnum() or c in '_.-')) for c in library)):
            raise ImportError('Unsafe Blender HAP native binding')
        if relative in paths:
            raise ImportError('Duplicate Blender HAP native path')
        paths.add(relative)
        module = _module_name(relative)
        if module is not None:
            if module in mapping:
                raise ImportError('Duplicate Blender HAP extension name')
            mapping[module] = root.rstrip('/') + '/' + library
    if len(mapping) != 90:
        raise ImportError('Incomplete Blender HAP extension registry')
    previous = [f for f in sys.meta_path if isinstance(f, _HapNativeFinder)]
    if previous:
        if len(previous) != 1 or previous[0].mapping != mapping:
            raise ImportError('Conflicting Blender HAP native registry')
        return
    sys.meta_path.insert(0, _HapNativeFinder(mapping))
