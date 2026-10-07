# SPDX-License-Identifier: GPL-2.0-or-later
"""Future real native compiler/SDK/link/owner acceptance, never invoked by plan."""
import json,re
from pathlib import Path
from toolkit_io import HERE,sha,file_row,json_write


def elf(a,runner,p,label):
    headers=runner.run([a.readelf,'-h','-S','-d',p],'native-elf-'+label)
    if 'AArch64' not in headers or '.codesign' not in headers or not re.search(r'Type:\s+(EXEC|DYN)',headers):raise ValueError('Unsigned/non-native execution artifact')
    if any(s in headers for s in ['RPATH','RUNPATH','TEXTREL']):raise ValueError('Unportable ELF dynamic tags')
    needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',headers)
    if any(n!='libc.so' for n in needed):raise ValueError('Unexpected native application dependency '+repr(needed))
    symbols=runner.run([a.nm,'-C',p],'native-symbols-'+label)
    if any(s in symbols for s in ['std::__h::','std::__1::','std::__blender20::']):raise ValueError('Foreign application C++ ABI')
    signed=None
    for file in (a.root/'signatures').glob('*.json'):
        row=json.loads(file.read_text())
        if row['signed_sha256']==sha(p):signed=row;break
    if not signed:raise ValueError('No byte-exact signed compiler origin for ELF (including actual ABI copy)')
    return dict(NEEDED=needed,signature_lineage=signed,**file_row(p))


def full(a,runner,manifest):
    paths=manifest['paths'];build=a.root/'native-build'
    if build.exists():raise ValueError('Fresh native probe build required, no resume')
    before={row['path']:row['sha256'] for row in manifest['owned_generated']}
    runner.run([a.cmake,'-S',a.root/'profile/probes','-B',build,'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(a.ninja),'-DCMAKE_TOOLCHAIN_FILE='+paths['toolchain_file'],
                '-DCMAKE_PROJECT_INCLUDE='+str(a.root/'profile/cmake/native-host.cmake'),'-DCMAKE_SKIP_RPATH=ON','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON'],'native-configure',timeout=1800)
    runner.run([a.cmake,'--build',build,'--parallel',str(a.jobs)],'native-build',timeout=3600)
    artifacts=[]
    # Genuine CMake CompilerId/ABI/check/try_run outputs also must be signed.
    for p in sorted(build.rglob('*')):
        if not p.is_file():continue
        with p.open('rb') as f:header=f.read(20)
        if header[:4]==b'\x7fELF' and int.from_bytes(header[16:18],'little') in (2,3):artifacts.append(elf(a,runner,p,str(len(artifacts))))
    runs=[]
    for name in ['native_probe','affinity_probe','exceptions_rtti_format_error','cpp17','c17']:
        p=build/name;value=sha(p);out=runner.run([p],'native-actual-'+name,timeout=a.runtime_timeout)
        if sha(p)!=value:raise ValueError('Execution changed signed bytes')
        runs.append({'name':name,'exit':0,'output':out,'signed_sha256':value})
    owner=build/'module_owner';module=build/'libowner_module.so';hashes=[sha(owner),sha(module)]
    out=runner.run([owner,module],'native-actual-module-owner',timeout=a.runtime_timeout)
    if hashes!=[sha(owner),sha(module)]:raise ValueError('Module loading changed signed bytes')
    runs.append({'name':'module_owner','exit':0,'output':out,'signed_sha256':hashes})
    for path,value in before.items():
        if sha(a.root/path)!=value:raise ValueError('Prepared input modified during native probing')
    source={row['path']:row['sha256'] for row in json.loads((HERE/'inputs.lock.json').read_text())['sealed_files']}
    for path,value in source.items():
        from toolkit_io import REPO
        if sha(REPO/path)!=value:raise ValueError('Recipe/source modified during native probing')
    receipt={'status':'PASS','manifest_before_native_sha256':manifest['_loaded_manifest_sha256'],'current_recipe_inputs_sha256':manifest['current_recipe_inputs_sha256'],
             'artifacts':artifacts,'real_native_runs':runs,'genuine_try_run':file_row(build/'genuine-native-try-run.txt'),
             'real_registration_retention':file_row(build/'toolkit-link-probe/result.txt'),'input_guards':'PASS','LLVM_from_source':'NOT_RUN',
             'scope':'Fresh native C17/C++17+C++20 SDK15004 __n1, genuine compiler/ABI/try_run, NEON/pthread/std::thread/exception/RTTI/format, worker-affinity, static registration/plain-vs-whole semantics, opaque MODULE three dlopen-owner-dlclose cycles; no full C++20/all-LLVM/upstream suite/application claim'}
    path=a.root/'native-probe-receipt.json';json_write(path,receipt)
    return {'status':'PASS','receipt':dict(path=str(path),sha256=sha(path))}
