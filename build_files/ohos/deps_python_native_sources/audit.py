# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact accepted module identities, fresh signed ELF closure and portable HAP mapping."""
import sys
sys.dont_write_bytecode=True
import importlib.util
import json
from pathlib import Path
import shutil
from source_guard import HERE,sha,inventory
from io_utils import dump,owned


def elf_module():
    spec=importlib.util.spec_from_file_location('native_python_elf',HERE/'numpy/elf.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def expected():
    old=json.loads((HERE/'provenance/accepted/combined-elf-audit.json').read_text())
    return old['module_map'],{r['path'] for r in old['runtime_ELF']}


def prefix(root,stage_hap=False):
    root=owned(root);runtime=root/'runtime';elf=elf_module();modules,paths=expected();records=[]
    actual={}
    for p in sorted(runtime.rglob('*')):
        if p.is_symlink() or not p.is_file():continue
        with p.open('rb') as f:magic=f.read(4)
        if magic==b'\x7fELF':actual[p.relative_to(runtime).as_posix()]=p
    if set(actual)!=paths or len(actual)!=93:raise ValueError('Fresh native exact 74+19 file profile differs')
    for name,p in actual.items():
        record=elf.inspect(p)
        if not record['codesign'] or record['TEXTREL'] or set(record['needed'])-{'libc.so','libpython3.13.so'}:raise ValueError('Final native architecture/signature/dependency closure differs')
        if any(v not in ('$ORIGIN','$ORIGIN/../lib','$ORIGIN/../..') for v in record['runpaths']):raise ValueError('Unexpected/absolute native RPATH')
        if name.startswith('lib/python3.13/site-packages/numpy/') and record['runpaths']:raise ValueError('NumPy extension must have no RPATH')
        if name=='lib/libpython3.13.so':elf.python_library(p)
        elif 'libpython3.13.so' not in record['needed']:raise ValueError('Interpreter/shim/extensions must use the same genuine shared Python')
        record['path']=name;records.append(record)
    if any((runtime/'lib').glob('libpython3.13.so.*')) or (runtime/'lib/libpython3.13.so').is_symlink():raise ValueError('Renamed/versioned runtime forbidden')
    files_by_basename={p.name:p for p in actual.values() if p.suffix=='.so'}
    if len(files_by_basename)!=92 or set(modules.values())-set(files_by_basename):raise ValueError('Exact physical native mapping differs')
    if len(modules)!=90 or len(set(modules.values()))!=90:raise ValueError('Complete 71 stdlib +19 NumPy mapping required')
    hap=root/'hap-native';native=hap/'libs/arm64-v8a'
    if stage_hap:
        if hap.exists():raise ValueError('HAP native copy requires absent subtree')
        native.mkdir(parents=True)
        for name,p in files_by_basename.items():
            shutil.copy2(p,native/name)
            if sha(native/name)!=sha(p):raise ValueError('Signed HAP copy bytes differ')
        dump(hap/'python-modules.json',{'schema':1,'modules':modules,'native_directory':'libs/arm64-v8a','native_extensions':90,'DSOs':92,
                  'actual_installed_HAP':'NOTRUN','interpreter_executable_copied':False})
    if not native.is_dir() or {p.name for p in native.iterdir()}!=set(files_by_basename):raise ValueError('Current HAP native layout differs')
    for name,p in files_by_basename.items():
        if (native/name).is_symlink() or sha(native/name)!=sha(p):raise ValueError('HAP namespace signed byte binding differs')
    mapping=json.loads((hap/'python-modules.json').read_text())
    if mapping['modules']!=modules:raise ValueError('Fresh physical module map differs')
    result={'schema':1,'result':'PASS final fresh artifact ELF/header/signature section/closure audit only','runtime_ELFs':records,
        'native_runtime_ELF_count':93,'hap_native_DSO_count':92,'native_module_map_count':90,
        'source_lock_sha256':sha(HERE/'sources.lock.json'),'inputs_lock_sha256':sha(HERE/'inputs.lock.json'),'tool_lock_sha256':sha(HERE/'tools.lock.json'),
        'module_map_sha256':sha(hap/'python-modules.json'),'prefix_inventory':inventory(runtime),'HAP_installed_execution':'NOTRUN',
        'certificate_chain_signature_verification':'not claimed by section checks'}
    return result
