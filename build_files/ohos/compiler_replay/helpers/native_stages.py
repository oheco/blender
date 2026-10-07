# SPDX-License-Identifier: GPL-2.0-or-later
"""Future parent-only genuine LLVM native stages with explicit acceptance gates."""
import json,re,struct,os
from pathlib import Path
from common import HERE,row,sha,write_json
import profile,source_tree

def command_plan(a):
    lock=json.loads((HERE/'sources.lock.json').read_text());source=a.root/'sources'/lock['archive_root'];build=a.root/'build';prefix=a.root/'install';p=a.root/'bootstrap-profile'
    flags={'CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(prefix),'CMAKE_C_COMPILER':str(p/'compiler-c'),'CMAKE_CXX_COMPILER':str(p/'compiler-cxx'),'CMAKE_MODULE_PATH':str(p/'cmake'),'CMAKE_PROJECT_INCLUDE':str(p/'cmake/native-host.cmake'),'CMAKE_MAKE_PROGRAM':str(a.ninja),'CMAKE_AR':str(a.ar),'CMAKE_RANLIB':str(a.ranlib),'CMAKE_SKIP_RPATH':'ON','CMAKE_POSITION_INDEPENDENT_CODE':'ON','CMAKE_EXE_LINKER_FLAGS':'-Wl,--threads=1','CMAKE_SHARED_LINKER_FLAGS':'-Wl,--threads=1',
           'LLVM_ENABLE_PROJECTS':'clang;lld','LLVM_ENABLE_RUNTIMES':'','LLVM_TARGETS_TO_BUILD':'AArch64','LLVM_HOST_TRIPLE':'aarch64-unknown-linux-ohos','LLVM_DEFAULT_TARGET_TRIPLE':'aarch64-unknown-linux-ohos','LLVM_NATIVE_ARCH':'AArch64','LLVM_ENABLE_LTO':'OFF','LLVM_BUILD_LLVM_DYLIB':'OFF','LLVM_LINK_LLVM_DYLIB':'OFF','LLVM_ENABLE_RTTI':'ON','LLVM_ENABLE_THREADS':'ON','LLVM_ENABLE_ZLIB':'OFF','LLVM_ENABLE_ZSTD':'OFF','LLVM_ENABLE_LIBXML2':'OFF','LLVM_ENABLE_LIBEDIT':'OFF','LLVM_INCLUDE_TESTS':'OFF','LLVM_INCLUDE_BENCHMARKS':'OFF','LLVM_INCLUDE_EXAMPLES':'OFF','LLVM_INCLUDE_UTILS':'ON','LLVM_BUILD_UTILS':'ON','LLVM_INSTALL_UTILS':'ON','LLVM_PARALLEL_COMPILE_JOBS':str(a.jobs),'LLVM_PARALLEL_LINK_JOBS':'1','LLVM_OPTIMIZED_TABLEGEN':'OFF','LLVM_INSTALL_TOOLCHAIN_ONLY':'OFF','CLANG_BUILD_EXAMPLES':'OFF','CLANG_ENABLE_STATIC_ANALYZER':'OFF','CLANG_ENABLE_ARCMT':'OFF','Python3_EXECUTABLE':str(a.python)}
    return {'configure':[a.cmake,'-S',source/'llvm','-B',build,'-G','Ninja',*[f'-D{k}={v}' for k,v in flags.items()]],'build':[a.cmake,'--build',build,'--target','clang','lld','llvm-tblgen','clang-tblgen','--parallel',str(a.jobs)],
            'install':[[a.cmake,'--install',build,'--component',name] for name in ['clang','lld','clang-resource-headers','llvm-tblgen','clang-tblgen']]}

def signed_elf(a,runner,p,label):
    p=Path(p);header=p.open('rb').read(20)
    if header[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<HH',header,16)[0] not in [2,3] or struct.unpack_from('<HH',header,16)[1]!=183:raise ValueError('Expected native AArch64 execution artifact')
    out=runner.run([a.readelf,'-h','-S','-d','-l',p],label+'-ELF')
    if '.codesign' not in out or any(s in out for s in ['RPATH','RUNPATH','TEXTREL']):raise ValueError('Unsigned/unportable compiler execution artifact')
    if p.suffix!='.so' and '/lib/ld-musl-aarch64.so.1' not in out:raise ValueError('Native executable requires actual OHOS musl PT_INTERP')
    needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',out)
    if any(s!='libc.so' for s in needed):raise ValueError('Unexpected compiler/application ELF dependencies')
    symbols=runner.run([a.nm,'-C',p],label+'-symbols')
    if any(s in symbols for s in ['std::__h::','std::__1::','std::__blender20::']):raise ValueError('Foreign compiler application C++ ABI')
    origins=[json.loads(q.read_text()) for q in (a.root/'signatures').glob('*.json') if json.loads(q.read_text())['signed_sha256']==sha(p)]
    if not origins:raise ValueError('Signed artifact lacks actual compiler-link provenance')
    return dict(NEEDED=needed,signature_origin=origins[0],**row(p))

def probe(a,runner,label,major):
    profile_dir=a.root/(label+'-profile');build=a.root/('bootstrap-probes' if label=='bootstrap' else 'rebuilt-probes')
    if build.exists():raise ValueError('Fresh genuine probe build required')
    runner.run([a.cmake,'-S',HERE/'probes','-B',build,'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(a.ninja),'-DCMAKE_TOOLCHAIN_FILE='+str(profile_dir/'native.cmake'),'-DCMAKE_PROJECT_INCLUDE='+str(profile_dir/'cmake/native-host.cmake'),'-DCMAKE_SKIP_RPATH=ON','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-DREPLAY_EXPECTED_CLANG_MAJOR='+str(major)],label+'-probes-configure')
    runner.run([a.cmake,'--build',build,'--parallel',str(a.jobs)],label+'-probes-build')
    artifacts=[]
    for p in sorted(build.rglob('*')):
        if not p.is_file():continue
        with p.open('rb') as stream:head=stream.read(20)
        if head[:4]==b'\x7fELF' and len(head)>=20 and int.from_bytes(head[16:18],'little') in [2,3]:artifacts.append(signed_elf(a,runner,p,label+'-probe-'+str(len(artifacts))))
    runs=[]
    for name in ['native_probe','affinity_probe','exceptions_rtti_format_error','cpp17','c17']:
        p=build/name;before=sha(p);out=runner.run([p],label+'-actual-run-'+name,timeout=a.runtime_timeout)
        if sha(p)!=before:raise ValueError('Signed executable changed while running')
        runs.append({'name':name,'exit':0,'signed_sha256':before,'output':out})
    owner=build/'module_owner';module=build/'libowner_module.so';before=[sha(owner),sha(module)];out=runner.run([owner,module],label+'-actual-module-owner',timeout=a.runtime_timeout)
    if [sha(owner),sha(module)]!=before:raise ValueError('Module ownership run changed signed bytes')
    runs.append({'name':'module_owner','exit':0,'signed_sha256':before,'output':out})
    result={'status':'PASS','label':label,'root':str(a.root),'profile_manifest_sha256':sha(a.root/(label+'-profile.json')),'prepared_source_sha256':sha(a.root/'prepared-source.json'),'major':major,'artifacts':artifacts,'real_native_runs':runs,'genuine_try_run':row(build/'genuine-native-try-run.txt'),'real_registration_retention':row(build/'toolkit-link-probe/result.txt'),'recipe_inputs_sha256':sha(HERE/'inputs.lock.json')}
    write_json(a.root/(label+'-native-receipt.json'),result);return result

def validate_native_receipt(a,label,major):
    path=a.root/(label+'-native-receipt.json');data=json.loads(path.read_text())
    build=a.root/('bootstrap-probes' if label=='bootstrap' else 'rebuilt-probes');snapshot=a.root/(label+'-profile.json')
    if data['status']!='PASS' or data['major']!=major or data['label']!=label or data['root']!=str(a.root) or data['recipe_inputs_sha256']!=sha(HERE/'inputs.lock.json'):raise ValueError('Native receipt role/root/recipe mismatch')
    if data['profile_manifest_sha256']!=sha(snapshot) or data['prepared_source_sha256']!=sha(a.root/'prepared-source.json'):raise ValueError('Native receipt source/profile mismatch')
    required={'native_probe','affinity_probe','exceptions_rtti_format_error','cpp17','c17','module_owner'}
    runs=data['real_native_runs'];artifacts=data['artifacts']
    if len(runs)!=6 or {r['name'] for r in runs}!=required or any(r['exit']!=0 for r in runs):raise ValueError('Missing actual required native runs')
    expected_paths={str(build/name) for name in required}|{str(build/'libowner_module.so')}
    if not expected_paths<={r['path'] for r in artifacts}:raise ValueError('Missing native artifact graph')
    allowed_compilers=set(json.loads((a.root/(label+'-profile')/'compiler.json').read_text())['compilers'].values())
    origins=[json.loads(p.read_text()) for p in (a.root/'signatures').glob('*.json')];hashes={r['path']:r['sha256'] for r in artifacts}
    if len(hashes)!=len(artifacts):raise ValueError('Duplicate native artifact path')
    for item in artifacts:
        p=Path(item['path']);origin=item['signature_origin']
        if build not in p.parents or sha(p)!=item['sha256'] or origin['signed_sha256']!=item['sha256'] or origin not in origins or origin['argv'][0] not in allowed_compilers or not origin['signature_checked'] or origin['signer']!=str(a.signer):raise ValueError('Unbound native artifact/compiler/signature bytes')
    for run in runs:
        expected=[hashes[str(build/'module_owner')],hashes[str(build/'libowner_module.so')]] if run['name']=='module_owner' else hashes[str(build/run['name'])]
        if run['signed_sha256']!=expected:raise ValueError('Run bytes not actual current artifacts')
        receipt=a.root/'logs'/((label+'-actual-module-owner' if run['name']=='module_owner' else label+'-actual-run-'+run['name'])+'.json');log=receipt.with_suffix('.log');actual=json.loads(receipt.read_text())
        expected_argv=[str(build/'module_owner'),str(build/'libowner_module.so')] if run['name']=='module_owner' else [str(build/run['name'])]
        if actual['argv']!=expected_argv or actual['exit']!=0 or actual['log_sha256']!=sha(log) or log.read_text()!=run['output']:raise ValueError('Missing actual runtime command receipt')
    for key,relative in [('genuine_try_run','genuine-native-try-run.txt'),('real_registration_retention','toolkit-link-probe/result.txt')]:
        item=data[key];p=build/relative
        if item['path']!=str(p) or item['sha256']!=sha(p):raise ValueError('Missing genuine signed configure/registration evidence')
    text=(build/'toolkit-link-probe/result.txt').read_text()
    if not (build/'genuine-native-try-run.txt').read_text().startswith('TRUE;0') or not all(s in text for s in ['plain_compile=TRUE','plain_run=1','marker=0','whole_compile=TRUE','whole_run=0','marker=73']):raise ValueError('Actual try-run/registration result failed')
    if 'three dlopen/create/check/destroy/dlclose cycles' not in next(r['output'] for r in runs if r['name']=='module_owner'):raise ValueError('Opaque module native cycles not proven')
    return data

def configure(a,runner):
    if (a.root/'build').exists():raise ValueError('Fresh native LLVM configure required')
    major=15 if a.bootstrap_mode=='sdk15-bootstrap' else 20
    if not (a.root/'bootstrap-native-receipt.json').exists():probe(a,runner,'bootstrap',major)
    validate_native_receipt(a,'bootstrap',major)
    runner.run(command_plan(a)['configure'],'LLVM-native-configure')
    cache=(a.root/'build/CMakeCache.txt').read_text()
    if 'CMAKE_CROSSCOMPILING:BOOL=TRUE' in cache or 'CMAKE_C_COMPILER_WORKS:INTERNAL=FALSE' in cache:raise ValueError('Genuine native compiler checks failed')

def cache_guard(a):
    path=a.root/'build/CMakeCache.txt';text=path.read_text();values=dict(re.findall(r'^([^/#\n][^:=\n]*):[^=\n]+=(.*)$',text,re.M))
    for flag in command_plan(a)['configure']:
        if str(flag).startswith('-D'):
            key,value=str(flag)[2:].split('=',1)
            if values.get(key)!=value:raise ValueError('Effective native cache input drift '+key)
    for key in ['LLVM_NATIVE_TOOL_DIR','LLVM_TABLEGEN','CLANG_TABLEGEN']:
        value=values.get(key,'')
        if value and str(a.root/'build/bin') not in value and value not in ['llvm-tblgen','clang-tblgen']:raise ValueError('External cached TableGen override '+key)
    return row(path)

def build(a,runner):
    cache_guard(a);runner.run(command_plan(a)['build'],'LLVM-native-build')
    generators=[]
    for name in ['llvm-min-tblgen','llvm-tblgen','clang-tblgen']:
        p=a.root/'build/bin'/name;record=signed_elf(a,runner,p,'built-generator-'+name);before=sha(p);out=runner.run([p,'--version'],'built-generator-version-'+name)
        if '20.1.8' not in out or sha(p)!=before:raise ValueError('Native generated TableGen identity/bytes mismatch')
        generators.append(record)
    # Ninja records the actual producer commands, while emitted .inc headers
    # prove this build's generators ran as dependencies before compilation.
    commands=runner.run([a.ninja,'-C',a.root/'build','-t','commands'],'actual-generation-commands')
    generator_commands=[line for line in commands.splitlines() if any(str(a.root/'build/bin'/n) in line for n in ['llvm-min-tblgen','llvm-tblgen','clang-tblgen'])]
    generated=[row(p,a.root/'build') for p in sorted((a.root/'build').rglob('*.inc')) if p.is_file()]
    if not generator_commands or not generated:raise ValueError('Actual native TableGen generation graph missing')
    write_json(a.root/'built-generators.json',{'status':'PASS actual newly built signed TableGen execution and generation graph','artifacts':generators,'actual_commands':generator_commands,'generated_headers':generated,'ninja_execution_log':row(a.root/'build/.ninja_log')})

def install(a,runner):
    if (a.root/'install').exists():raise ValueError('Fresh install required')
    for i,command in enumerate(command_plan(a)['install']):runner.run(command,'LLVM-component-install-'+str(i))
    for name in ['clang','clang++','ld.lld','llvm-tblgen','clang-tblgen']:
        if not (a.root/'install/bin'/name).is_file():raise ValueError('Required native install component absent '+name)
    notice=a.root/'install/bootstrap-sdk-NOTICE.txt';notice.write_bytes((a.sdk_root/'NOTICE.txt').read_bytes())
    resource=a.root/'install/lib/clang/20';target=resource/'lib/aarch64-linux-ohos'
    if not (resource/'include/stddef.h').is_file():raise ValueError('New Clang20 resource headers absent')
    if target.exists():raise ValueError('Unexpected compiler-rt runtime install')
    target.mkdir(parents=True)
    for name in profile.RT:(target/name).write_bytes((a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name).read_bytes())
    write_json(resource/'SDK15-overlay-provenance.json',{'compiler':'NEW source built Clang20','runtime':'Provided SDK15.0.4 only3CRT/builtins, not compiler-rt20','files':[row(target/name) for name in profile.RT]})

def sign_install(a,runner):
    # Every producer link was signed before use. Install must preserve these exact
    # signed bytes; silently re-signing a mutated install would hide copy damage.
    tools=[]
    for name in ['clang','clang++','ld.lld','llvm-tblgen','clang-tblgen']:
        p=a.root/'install/bin'/name;record=signed_elf(a,runner,p,'installed-'+name);out=runner.run([p,'--version'],'installed-version-'+name)
        if '20.1.8' not in out:raise ValueError('New source compiler version mismatch')
        record['actual_version']=out;tools.append(record)
    all_elf=[]
    for p in sorted((a.root/'install').rglob('*')):
        if not p.is_file():continue
        with p.open('rb') as stream:head=stream.read(20)
        if head[:4]==b'\x7fELF' and len(head)>=20 and int.from_bytes(head[16:18],'little') in [2,3]:
            record=signed_elf(a,runner,p,'installed-complete-ELF-'+str(len(all_elf)));record['alias_target']=os.readlink(p) if p.is_symlink() else None;all_elf.append(record)
    if not all_elf:raise ValueError('Empty native install ELF graph')
    profile.generate(a,runner,a.root/'rebuilt-profile',a.root/'install/bin/clang',a.root/'install/bin/clang++',a.root/'install/bin/ld.lld',a.root/'install/lib/clang/20',20,'rebuilt')
    write_json(a.root/'installed-tools.json',{'status':'PASS exact signed build-to-install bytes and native20 self versions','tools':tools,'complete_installed_ELF_graph':all_elf})

def audit(a,runner):
    installed=json.loads((a.root/'installed-tools.json').read_text())
    if not (a.root/'rebuilt-native-receipt.json').exists():probe(a,runner,'rebuilt',20)
    receipt=validate_native_receipt(a,'rebuilt',20)
    validate_native_receipt(a,'bootstrap',15 if a.bootstrap_mode=='sdk15-bootstrap' else 20)
    for record in installed['tools']:
        if sha(record['path'])!=record['sha256']:raise ValueError('Installed compiler bytes changed')
    if receipt['status']!='PASS' or receipt['recipe_inputs_sha256']!=sha(HERE/'inputs.lock.json'):raise ValueError('Rebuilt native acceptance not bound')
    profile.guard_profile(a,'rebuilt');source_tree.guard(a)
    inventory=[row(p,a.root/'install') for p in sorted((a.root/'install').rglob('*')) if p.is_file()]
    result={'compiler_output_REBUILD':'PASS actual NEW complete-source clang/lld/AArch64 build, native generators,5 install components, immutable signed-copy bytes and newly produced native compiler consumption','recipe_inputs_sha256':sha(HERE/'inputs.lock.json'),'installed_tools':installed['tools'],'installed_inventory':inventory,'bootstrap_native':row(a.root/'bootstrap-native-receipt.json'),'rebuilt_native':row(a.root/'rebuilt-native-receipt.json'),'prepared_source':row(a.root/'prepared-source.json'),
            'scope':'Native compiler+SDK15 __n1 C17/C++17/C++20 named subset only. No allLLVM projects/compiler-rt20/libc++20/sanitizers/all upstream suite/signed-bit reproducibility/editor/HAP claim'}
    write_json(a.root/'compiler-acceptance.json',result);return result
