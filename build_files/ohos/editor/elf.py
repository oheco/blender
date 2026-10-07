# SPDX-License-Identifier: GPL-2.0-or-later
"""Final real ELF audit after install/relink/sign; no byte rewriting in audit."""
from pathlib import Path
import os
import re
import shutil
import tempfile
from common import sha, record, inventory, bound

CORE_EXPORTS = {'blender_ohos_initialize', 'blender_ohos_pump', 'blender_ohos_stop',
                'blender_ohos_teardown', 'blender_ohos_file_command',
                'ghost_ohos_host_create', 'ghost_ohos_engine_start',
                'ghost_ohos_native_retain', 'ghost_ohos_native_release'}


def parse(text, path, root, shared=True):
    if not re.search(r'Class:\s+ELF64', text) or not re.search(r'Machine:\s+AArch64', text):
        raise ValueError('Actual ELF64/AArch64 required: ' + str(path))
    if shared and not re.search(r'Type:\s+DYN', text):
        raise ValueError('Actual shared DYN required')
    if not re.search(r'\[\s*\d+\]\s+\.codesign\b', text) or re.search(r'\(TEXTREL\)|\bFLAGS\b[^\n]*TEXTREL', text):
        raise ValueError('Final codesign required; TEXTREL rejected')
    sonames = re.findall(r'\(SONAME\).*?\[([^\]]+)\]', text)
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
    if len(sonames) > 1 or any(not re.fullmatch(r'lib[A-Za-z0-9_.-]+\.so', n) for n in sonames + needed):
        raise ValueError('Versioned/path/ambiguous SONAME or NEEDED')
    rpaths = []
    for value in re.findall(r'\((?:RPATH|RUNPATH)\).*?\[([^\]]*)\]', text):
        for item in value.split(':'):
            if not re.fullmatch(r'\$(?:ORIGIN|\{ORIGIN\})(?:/[A-Za-z0-9_.+ -]+)*', item):
                raise ValueError('Empty/absolute/non-ORIGIN final RPATH')
            expanded = item.replace('${ORIGIN}', str(path.parent)).replace('$ORIGIN', str(path.parent))
            if not Path(expanded).resolve().is_relative_to(root):
                raise ValueError('Final RPATH escapes owned installation')
            rpaths.append(item)
    functions = set()
    data = set()
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 8 and f[0].rstrip(':').isdigit() and f[4:6] == ['GLOBAL', 'DEFAULT'] and f[6] != 'UND':
            name = f[7].split('@')[0]
            if f[3] == 'FUNC':
                functions.add(name)
            elif f[6] != 'ABS':
                data.add(name)
    namespaces = set(re.findall(r'St\d+(__n1|__h|__1|__blender20)', text))
    if namespaces - {'__n1'}:
        raise ValueError('Foreign C++ runtime namespace in final ELF')
    return {'soname': sonames[0] if sonames else None, 'needed': needed, 'rpaths': rpaths,
            'default_global_functions': sorted(functions), 'data_exports': sorted(data),
            'cpp_namespaces': sorted(namespaces), 'codesign_section': True}


def sign_installed(install, toolkit, tmp, run):
    """Install/relink has finished. Sign final bytes without strip/RPATH patching."""
    result = []
    for row in inventory(install):
        p = install / row['path']
        if p.is_symlink():
            continue
        with p.open('rb') as stream:
            is_elf = stream.read(4) == b'\x7fELF'
        if not is_elf:
            continue
        before = record(p)
        previous_mode = p.stat().st_mode & 0o777
        with tempfile.TemporaryDirectory(prefix='editor-final-sign-', dir=tmp) as scratch:
            final = Path(scratch) / p.name
            argv = [toolkit['tools']['signer'], 'sign', '-inFile', str(p), '-outFile', str(final), '-selfSign', '1']
            run(argv, 'final-sign-' + row['path'].replace('/', '-'))
            if not final.is_file():
                raise ValueError('Signer produced no final ELF')
            text = run([toolkit['tools']['readelf'], '-W', '-S', str(final)], 'final-sign-sections-' + row['path'].replace('/', '-'))
            if '.codesign' not in text:
                raise ValueError('Final signer output lacks actual signature section')
            final.chmod(previous_mode | 0o100)
            # Public receipt records both sides and actual signer command/log; never edit afterwards.
            p.unlink()
            shutil.move(str(final), str(p))
        result.append({'input': before, 'signed_output': record(p), 'sign_argv': argv,
                       'original_mode': previous_mode, 'final_mode': p.stat().st_mode & 0o777})
    return result


def audit_install(install, python, toolkit, host, run):
    rows = inventory(install)
    elf_info = {}
    tools = toolkit['tools']
    provider = python / 'lib/libpython3.13.so'
    providers = list((python / 'lib').glob('libpython3.13.so*'))
    if providers != [provider] or provider.is_symlink():
        raise ValueError('Require one genuine regular unversioned Python provider from accepted source runtime')
    with provider.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            raise ValueError('Python provider is not an actual ELF')
    provider_text = run([tools['readelf'], '-W', '-h', '-d', '-S', '--dyn-syms', str(provider)], 'actual-python-provider-elf')
    provider_info = parse(provider_text, provider, python)
    if provider_info['soname'] != 'libpython3.13.so' or not {'Py_GetVersion', 'Py_IsInitialized', 'Py_InitializeFromConfig', 'PyGILState_Ensure'} <= set(provider_info['default_global_functions']):
        raise ValueError('Genuine unversioned Python SONAME/C API provider required')
    system = {row['name']: bound(row['file']) for row in host['system_libraries']}
    if any(name.startswith(('libpython', 'libblender')) or name in ('libc++.so', 'libc++_shared.so') for name in system):
        raise ValueError('Owned Python/core/static C++ cannot be disguised as a system library')
    for row in rows:
        p = install / row['path']
        if p.is_symlink():
            continue
        with p.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        before = sha(p)
        text = run([tools['readelf'], '-W', '-h', '-d', '-S', '--dyn-syms', str(p)], 'installed-elf-' + row['path'].replace('/', '-'))
        info = parse(text, p, install, p.name != 'blender-ohos-diagnostic')
        if sha(p) != before or not os.access(p, os.X_OK):
            raise ValueError('Final signed bytes/mode changed during audit')
        info.update(sha256=before, size=p.stat().st_size)
        if p.name == 'libblender_core.so':
            if info['soname'] != p.name or not CORE_EXPORTS <= set(info['default_global_functions']) or info['data_exports'] or any(not x.startswith(('blender_ohos_', 'ghost_ohos_')) for x in info['default_global_functions']):
                raise ValueError('Core must export the nine real ABI functions, with no unrelated default exports')
            if info['needed'].count('libpython3.13.so') != 1 or any(x in info['needed'] for x in ('libpython3.so', 'libc++.so', 'libc++_shared.so')):
                raise ValueError('Core lost its one genuine Python/static SDK C++ ownership')
            symbols = run([tools['nm'], '--defined-only', '--demangle', str(p)], 'actual-core-sdk-cpp-namespace')
            if 'std::__n1::' not in symbols or re.search(r'std::(?:__h|__1|__blender20)::', symbols):
                raise ValueError('Final actual core does not exclusively demonstrate SDK __n1 C++ namespace')
        elf_info[row['path']] = info
    if not {'lib/libblender_core.so', 'bin/blender-ohos-diagnostic'} <= elf_info.keys():
        raise ValueError('Installed core/diagnostic actual files missing')
    sonames = {i['soname'] for i in elf_info.values() if i['soname']}
    allowed = sonames | {'libpython3.13.so'} | set(system)
    for rel, info in elf_info.items():
        if not set(info['needed']) <= allowed:
            raise ValueError('Unresolved actual NEEDED closure: ' + rel + ' ' + repr(set(info['needed']) - allowed))
    return {'schema': 1, 'status': 'PASS_FINAL_ELF_BYTES_ONLY', 'elf': elf_info,
            'python_provider': {'file': record(provider), 'elf': provider_info}, 'inventory': rows,
            'signature_trust': 'Real signed core probe/diagnostic and installed HAP are separate actual gates',
            'core_dlopen': 'NOT_RUN', 'bpy': 'NOT_RUN', 'SDL_window': 'NOT_RUN', 'HAP': 'NOT_RUN'}
