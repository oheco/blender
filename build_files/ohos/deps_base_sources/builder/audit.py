# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual ELF/native archive audits and build/install signed byte lineage."""
import re
from pathlib import Path
from io_utils import sha, write_json
from metadata import ARCHIVES


def elf(file,a,runner,label):
    header=runner.run([a.readelf,'-h',file],label+'-header');sections=runner.run([a.readelf,'-S',file],label+'-sections');dynamic=runner.run([a.readelf,'-d',file],label+'-dynamic')
    if 'AArch64' not in header or '.codesign' not in sections or any(value in dynamic for value in ['(TEXTREL)','(RPATH)','(RUNPATH)']):raise ValueError('Signed native ELF invariants failed: '+str(file))
    needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',dynamic)
    if any(item!='libc.so' for item in needed):raise ValueError('Unexpected runtime dynamic dependency: '+str(needed))
    symbols=runner.run([a.nm,'-C',file],label+'-symbols')
    if re.search(r'std::__(?:h|1|blender20)(?:::|\b)',symbols):raise ValueError('Foreign C++ ABI in native ELF')
    return {'file':str(file),'sha256':sha(file),'machine':'AArch64','signature_section':'.codesign','NEEDED':needed,'TEXTREL':False,'RPATH_RUNPATH':False,'runtime_ownership':'C++ remains within module for bridge opaque ABI'}


def run(a,runner):
    rows=[]
    for index,name in enumerate(ARCHIVES):
        file=a.prefix/'lib'/name
        if not file.is_file() or file.open('rb').read(8)!=b'!<arch>\n':raise ValueError('Real static archive required')
        headers=runner.run([a.readelf,'-h',file],'archive-'+str(index)+'-members');machines=re.findall(r'Machine:\s+([^\n]+)',headers);kinds=re.findall(r'Type:\s+([^\n]+)',headers)
        if not machines or any('AArch64' not in m for m in machines) or len(machines)!=len(kinds) or any(not kind.startswith('REL') for kind in kinds):raise ValueError('Each native archive member must be AArch64 ET_REL')
        symbols=runner.run([a.nm,'-C',file],'archive-'+str(index)+'-ABI')
        if re.search(r'std::__(?:h|1|blender20)(?:::|\b)',symbols):raise ValueError('Foreign C++ archive ABI')
        candidate_names=[name]
        if name.startswith('libbrotli'):candidate_names.append(name[:-2]+'-static.a')
        matches=[p for p in (a.root/'build').rglob('*.a') if p.name in candidate_names and sha(p)==sha(file)]
        if not matches:raise ValueError('Installed archive lacks actual build byte origin: '+name)
        rows.append({'file':str(file),'sha256':sha(file),'archive_members':len(machines),'machine':'AArch64','type':'ET_REL','signature':'Not applicable to static archives','NEEDED':'Not applicable','actual_build_matches':[str(p) for p in matches]})
    tools=[a.root/'tools/bin/pkgconf',a.prefix/'bin/brotli']
    tools += [p for p in (a.root/'build/fribidi/gen.tab').glob('*') if p.is_file() and p.open('rb').read(4)==b'\x7fELF']
    if len(tools)<9:raise ValueError('All seven actual FriBidi native Unicode generators required')
    elfs=[elf(p,a,runner,'tool-'+str(i)) for i,p in enumerate(tools)]
    build_elf_index={}
    for p in (a.root/'build').rglob('*'):
        if p.is_file() and not p.is_symlink() and p.open('rb').read(4)==b'\x7fELF':build_elf_index.setdefault(sha(p),[]).append(str(p))
    installed=[]
    for p in a.prefix.rglob('*'):
        if p.is_symlink():
            if a.prefix.resolve() not in p.resolve().parents:raise ValueError('Installed link escapes owned prefix')
            continue
        if not p.is_file():continue
        if p.open('rb').read(4)==b'\x7fELF':
            row=elf(p,a,runner,'installed-'+str(len(installed)));origins=build_elf_index.get(sha(p),[])
            if not origins:raise ValueError('Signed installed ELF changed after actual build/sign')
            row['signed_build_byte_origins']=[str(q) for q in origins];installed.append(row)
        if p.suffix in ('.pc','.cmake') and any(path in p.read_text() for path in [str(a.root),str(a.prefix)]):raise ValueError('Nonportable installed metadata')
    result={'native_archive_audit':rows,'source_built_native_tool_ELFs':elfs,'all_installed_ELFs':installed,'new_prefix_inventory':[
            {'path':p.relative_to(a.prefix).as_posix(),'sha256':sha(p),'size':p.stat().st_size} for p in sorted(a.prefix.rglob('*')) if p.is_file() and not p.is_symlink()],
            'scope':'Real member/native ABI, actual build/install signed bytes; PIC/dlopen/public interfaces separately required by acceptance','BlenderTextImage':'NOT_TESTED','HAP':'NOT_TESTED'}
    write_json(a.root/'native-artifact-audit.json',result);return result
