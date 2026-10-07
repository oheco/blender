# SPDX-License-Identifier: GPL-2.0-or-later
"""Read real final ELF inputs. Never create ELF fixtures or rewrite signed bytes."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
from common import beneath, digest, load, require

CORE_EXPORTS = {'blender_ohos_initialize', 'blender_ohos_pump', 'blender_ohos_stop',
                'blender_ohos_teardown', 'blender_ohos_file_command',
                'ghost_ohos_host_create', 'ghost_ohos_engine_start',
                'ghost_ohos_native_retain', 'ghost_ohos_native_release'}
FORBIDDEN_SYSTEM = {'libblender_core.so', 'libblender_host.so', 'libpython3.13.so',
                    'libpython3.so', 'libc++_shared.so', 'libc++.so'}

def parse_readelf(text: str):
    require(re.search(r'Class:\s+ELF64', text), 'ELF64 required')
    require(re.search(r'Machine:\s+AArch64', text), 'AArch64 required')
    require(re.search(r'Type:\s+DYN', text), 'Shared ELF required')
    require(re.search(r'\[\s*\d+\]\s+\.codesign\b', text), 'Final signed ELF .codesign section required; trust is still an installed-HAP gate')
    require(not re.search(r'\(TEXTREL\)|\bFLAGS\b[^\n]*TEXTREL', text), 'TEXTREL rejected')
    sonames = re.findall(r'\(SONAME\).*?\[([^\]]+)\]', text)
    require(len(sonames) <= 1, 'Ambiguous ELF SONAME')
    for name in sonames:
        require(re.fullmatch(r'lib[A-Za-z0-9_.-]+\.so', name), 'Real unversioned SONAME required')
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
    require(all('/' not in name and name.endswith('.so') for name in needed), 'Versioned/path NEEDED rejected')
    paths = []
    for value in re.findall(r'\((?:RPATH|RUNPATH)\).*?\[([^\]]*)\]', text):
        for path in value.split(':'):
            require(path and not path.startswith('/') and re.fullmatch(r'\$(?:ORIGIN|\{ORIGIN\})(?:/[A-Za-z0-9_.+ -]+)*', path),
                    'RPATH must use explicit relative ORIGIN without environment/current-directory entries')
            depth = 3  # Final co-located native dir is entry/libs/arm64-v8a inside project.
            for piece in path.split('/')[1:]:
                depth += -1 if piece == '..' else (0 if piece == '.' else 1)
                require(depth >= 0, 'RPATH escapes exported project')
            paths.append(path)
    exported = set()
    data_exports = set()
    version_nodes = set()
    function_exports = set()
    default_global_functions = set()
    undefined = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 8 or not fields[0].rstrip(':').isdigit() or fields[4] not in ('GLOBAL', 'WEAK'):
            continue
        name = fields[7].split('@')[0]
        if fields[6] == 'UND':
            undefined.add(name)
        elif fields[5] in ('DEFAULT', 'PROTECTED'):
            if fields[3] in ('FUNC', 'IFUNC'):
                exported.add(name)
                if fields[3] == 'FUNC':
                    function_exports.add(name)
                    if fields[4:6] == ['GLOBAL', 'DEFAULT']: default_global_functions.add(name)
            else:
                data_exports.add(name)
                if fields[6] == 'ABS' and int(fields[1], 16) == 0 and fields[2] == '0' and name.startswith('BLENDER_OHOS_'):
                    version_nodes.add(name)
    abi = sorted(set(re.findall(r'St\d+(__n1|__h|__1|__blender20)', text)))
    require(not set(abi) - {'__n1'}, 'C++ runtime namespace other than the reviewed SDK __n1 found')
    return {'soname': sonames[0] if sonames else None, 'needed': needed, 'runpaths': paths,
            'exports': sorted(exported), 'visible_exports': sorted(exported | data_exports),
            'function_exports': sorted(function_exports), 'default_global_functions': sorted(default_global_functions), 'noncallable_data_exports': sorted(data_exports - version_nodes),
            'undefined': sorted(undefined), 'cpp_namespaces': abi, 'codesign_section': True,
            'signature_trust': 'NOT_RUN_REQUIRES_ACTUAL_INSTALLED_HAP'}

def audit(path: Path, readelf: Path, role: str):
    require(path.is_file() and not path.is_symlink(), 'Regular final native input required: ' + str(path))
    require(path.stat().st_mode & 0o111, 'Signed input executable bits must be preserved: ' + str(path))
    with path.open('rb') as stream:
        require(stream.read(4) == b'\x7fELF', 'Actual ELF input required')
    before = digest(path)
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    result = subprocess.run([str(readelf), '-W', '-h', '-d', '-S', '--dyn-syms', str(path)],
                            env=env, capture_output=True, text=True, check=True, timeout=60)
    observed = parse_readelf(result.stdout)
    require(digest(path) == before, 'ELF changed during audit')
    observed.update({'sha256': before, 'size': path.stat().st_size, 'mode': path.stat().st_mode & 0o777,
                     'role': role})
    if role in ('core', 'host'):
        require(not observed['noncallable_data_exports'], 'Private/unreviewed public data exports')
    if role == 'core':
        require(observed['soname'] == 'libblender_core.so', 'Core SONAME must be libblender_core.so')
        require(CORE_EXPORTS.issubset(observed['default_global_functions']), 'Real core lifecycle/file/GHOST ABI exports missing')
        require('libpython3.13.so' in observed['needed'], 'Core must actually depend on the selected shared Python')
        require('libc++_shared.so' not in observed['needed'], 'Reviewed core profile requires static SDK C++')
        require(all(name.startswith(('blender_ohos_', 'ghost_ohos_', 'BLENDER_OHOS_')) for name in observed['visible_exports']),
                'Core exports private/unreviewed symbols')
    elif role == 'host':
        require(observed['soname'] == 'libblender_host.so', 'Host SONAME must be libblender_host.so')
        require('blender_host_register' in observed['default_global_functions'], 'Real host registration export missing')
        require(all(name == 'blender_host_register' or name.startswith('BLENDER_OHOS_') for name in observed['visible_exports']),
                'Host exports private/unreviewed symbols')
        require('libblender_core.so' in observed['needed'], 'Actual host must depend on the selected shared core')
    return observed

def accepted_rows(runtime: Path, development: Path, python_audit: Path, numpy_audit: Path):
    dev = load(development)
    require(dev.get('accepted') is True and dev.get('native_exit_code') == 0 and
            dev.get('exact_signed_input_bytes_preserved') is True and dev.get('native_elf_count') == 93,
            'Accepted native93 development receipt required')
    require(dev['python_audit_sha256'] == digest(python_audit) and dev['numpy_audit_sha256'] == digest(numpy_audit),
            'Native93 audit receipt hashes differ')
    py = load(python_audit)
    numpy = load(numpy_audit)
    require(len(py) == 74 and numpy['numpy'] == '2.3.4' and numpy['platform'] == 'ohos' and
            len(numpy['elf_audit']) == 19, 'Complete accepted Python74+NumPy19 audit required')
    rows = py + numpy['elf_audit']
    names = set()
    for row in rows:
        require(row['path'] not in names, 'Duplicate accepted native path')
        names.add(row['path'])
        path = beneath(runtime, row['path'])
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == row['size'] and
                digest(path) == row['sha256'] and row.get('codesign') is True and path.stat().st_mode & 0o111,
                'Accepted signed native input changed: ' + row['path'])
        require('libpython3.13.so.1.0' not in row.get('needed', []), 'Versioned PyRuntime rejected')
    require('bin/python3.13' in names and 'lib/libpython3.13.so' in names, 'Native93 inventory missing interpreter/runtime')
    return rows, dev

def module_name(relative: str):
    if '/lib-dynload/' in relative:
        return Path(relative).name.split('.')[0]
    require('/site-packages/numpy/' in relative, 'Unrecognized dynamic import mapping')
    inside = relative.split('/site-packages/', 1)[1]
    return '.'.join([*Path(inside).parts[:-1], Path(inside).name.split('.')[0]])

def packaged_python(rows, runtime: Path, readelf: Path):
    libraries = {}
    bindings = []
    imports = {}
    for row in rows:
        if row['path'] == 'bin/python3.13':
            continue
        path = beneath(runtime, row['path'])
        info = audit(path, readelf, 'python')
        require(info['sha256'] == row['sha256'], 'Native93 hash changed during final packaging audit')
        name = info['soname'] or ('libblender_py_' + info['sha256'][:24] + '.so')
        require(name not in libraries, 'Duplicate Python library content/name; explicit aliases required')
        libraries[name] = {'source': path, 'audit': info}
        bindings.append({'path': '5.2/python/' + row['path'], 'library': name})
        if '/lib-dynload/' in row['path'] or '/site-packages/numpy/' in row['path']:
            module = module_name(row['path'])
            require(module not in imports and 'PyInit_' + module.rsplit('.', 1)[-1] in info['default_global_functions'],
                    'Dynamic import lacks its real PyInit export: ' + module)
            require('libpython3.13.so' in info['needed'], 'Extension is not linked to selected shared PyRuntime')
            imports[module] = {'path': '5.2/python/' + row['path'], 'library': name, 'sha256': info['sha256']}
    require(len(libraries) == 92 and len(imports) == 90, 'Exact Python92 packaged DSOs/90 import mappings required')
    require(libraries['libpython3.13.so']['audit']['soname'] == 'libpython3.13.so', 'One genuine PyRuntime required')
    return libraries, bindings, imports

def close_dependencies(libraries: dict, system: set):
    require(not system & FORBIDDEN_SYSTEM, 'Application/SDK C++ libraries cannot be assumed to be system libraries')
    require('libblender_core.so' in libraries and 'libpython3.13.so' in libraries, 'Core and Python must be co-located')
    dependencies = {}
    for name, row in libraries.items():
        require(re.fullmatch(r'lib[A-Za-z0-9_.-]+\.so', name), 'Unsafe native package name')
        needed = row['audit']['needed']
        require(not set(needed) - (set(libraries) | system), 'Unprovided NEEDED for ' + name + ': ' + str(set(needed) - (set(libraries) | system)))
        dependencies[name] = needed
    return dependencies
