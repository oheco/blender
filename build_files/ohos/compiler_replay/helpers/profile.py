# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh own bootstrap/rebuilt profiles; never reuse accepted toolkit JSON."""
import json,re,shlex,shutil,os
from pathlib import Path
from common import HERE,TOOLS,row,sha,write_json
RT=['libclang_rt.builtins.a','clang_rt.crtbegin.o','clang_rt.crtend.o'];LIBS=['libc++.a','libc++abi.a','libc++experimental.a']

def inventory(p):return [row(f,p) for f in sorted(p.rglob('*')) if f.is_file()]

def config(a,root,cc,cxx,lld,resource):
    base=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(a.sdk_root/'sysroot'),'-resource-dir='+str(resource),'--ld-path='+str(lld),'-Wl,--threads=1','-L'+str(a.sdk_root/'llvm/lib/aarch64-linux-ohos')]
    return {'compilers':{'c':str(cc),'cxx':str(cxx)},'flags':{'c':base,'cxx':['--driver-mode=g++',*base,'-nostdinc++','-isystem',str(a.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),'-fexperimental-library','-static-libstdc++','-lc++experimental']},
            'signer':str(a.signer),'readelf':str(a.readelf),'tmp_dir':str(a.tmp_dir),'private_cache':os.environ['XDG_CACHE_HOME'],'signature_receipts_dir':str(a.root/'signatures'),
            'allowed_output_roots':[str(a.root/'build'),str(a.root/'bootstrap-probes'),str(a.root/'rebuilt-probes')],
            'protected_inputs':[str(a.sdk_root),str(a.resource_dir),str(resource),str(HERE),str(a.root/'sources'),str(root),str(a.root/'owner.json'),*[str(getattr(a,k)) for k in TOOLS]]}

def query(a,runner,cc,cxx,lld,resource,label,major):
    versions={}
    for key,p in [('cc',cc),('cxx',cxx),('lld',lld)]:versions[key]=runner.run([p,'--version'],label+'-version-'+key)
    expected='15.0.4' if major==15 else '20.1.8'
    if any('clang version '+expected not in versions[k] for k in ['cc','cxx']) or 'LLD '+expected not in versions['lld']:raise ValueError('Actual compiler/linker bootstrap mode mismatch')
    default=Path(runner.run([cc,'-print-resource-dir'],label+'-default-resource').strip())
    if inventory(default/'include')!=inventory(resource/'include'):raise ValueError('Selected resource headers not actual compiler defaults')
    cfg=config(a,a.root/'bootstrap-profile',cc,cxx,lld,resource);flags=[f for f in cfg['flags']['cxx'] if not f.startswith(('--ld-path=','-Wl,','-L')) and f not in ['-static-libstdc++','-lc++experimental']]
    macros=runner.run([cxx,*flags,'-std=c++20','-E','-dM','-x','c++','-'],label+'-macros',input='#include <__config>\n#include <arm_neon.h>\n')
    values=dict(re.findall(r'^#define (\w+)[ \t]+([^\n]+)$',macros,re.M))
    if values.get('_LIBCPP_VERSION')!='15004' or values.get('_LIBCPP_ABI_NAMESPACE')!='__n1' or values.get('__clang_major__')!=str(major) or '__OHOS__' not in values or '__aarch64__' not in values:raise ValueError('Real bootstrap SDK15/machine/macro mismatch')
    for key,p in [('cc',cc),('cxx',cxx),('lld',lld)]:
        elf=runner.run([a.readelf,'-h','-S',p],label+'-signed-elf-'+key)
        if 'AArch64' not in elf or '.codesign' not in elf:raise ValueError('Provided/rebuilt native tool missing signature/machine')
    return {'mode':a.bootstrap_mode if label=='bootstrap' else 'new-rebuilt20','major':major,'versions':versions,'selected_sdk_macros':{k:values[k] for k in ['_LIBCPP_VERSION','_LIBCPP_ABI_NAMESPACE','__clang_major__','__OHOS__','__aarch64__']},
            'tools':[dict(role=role,**row(p)) for role,p in [('cc',cc),('cxx',cxx),('lld',lld)]],'resource_headers':inventory(resource/'include'),'resource_origin':str(resource),
            'sdk_cxx_headers':inventory(a.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),'sdk_sysroot_headers':inventory(a.sdk_root/'sysroot/usr/include'),'sdk_sysroot_link_files':inventory(a.sdk_root/'sysroot/usr/lib'),
            'SDKruntime':[row(a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/n) for n in RT]+[row(a.sdk_root/'llvm/lib/aarch64-linux-ohos'/n) for n in LIBS]+[row(a.sdk_root/'NOTICE.txt')],'readonly_scope':'version/ELF/preprocessor only; no new executable compiled'}

def quote(p):
    text=str(p)
    if any(c in text for c in [';','$','\n','\r']):raise ValueError('Unsupported CMake path interpolation')
    return text.replace('\\','/').replace('"','\\"')

def generate(a,runner,root,cc,cxx,lld,raw_resource,major,label):
    if root.exists():raise ValueError('Fresh independent profile required')
    facts=query(a,runner,cc,cxx,lld,raw_resource,label,major);root.mkdir();resource=root/'resource'
    shutil.copytree(raw_resource/'include',resource/'include')
    for name in RT:
        p=resource/'lib/aarch64-linux-ohos'/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name,p)
    for name in ['launcher.py','env_policy.py']:shutil.copyfile(HERE/'helpers'/name,root/name)
    shutil.copytree(HERE/'cmake',root/'cmake');cfg=config(a,root,cc,cxx,lld,resource);write_json(root/'compiler.json',cfg)
    for language in ['c','cxx']:
        p=root/('compiler-'+language);p.write_text('#!/usr/bin/sh\nexec '+shlex.join([str(a.python),str(root/'launcher.py'),str(root/'compiler.json'),language,'--'])+' "$@"\n');p.chmod(0o755)
    lines=['# Native source-compiler replay profile; genuine host/compiler/probes.','include_guard(GLOBAL)']
    for key,p in {'C_COMPILER':root/'compiler-c','CXX_COMPILER':root/'compiler-cxx','AR':a.ar,'RANLIB':a.ranlib}.items():lines.append('set(CMAKE_'+key+' "'+quote(p)+'" CACHE FILEPATH "Explicit native input")')
    lines+=['set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "Ordinary TUs")','list(PREPEND CMAKE_MODULE_PATH "'+quote(root/'cmake')+'")']
    (root/'native.cmake').write_text('\n'.join(lines)+'\n')
    flags=cfg['flags']['cxx'];planned=runner.run([cxx,*flags,'-std=c++20','-###','-x','c++','-','-o',a.root/'plan-never-produced'],label+'-link-plan',input='int main(){return 0;}\n')
    if str(lld) not in planned or '-lc++experimental' not in planned:raise ValueError('Actual driver omitted explicit lld/experimental')
    snapshot={'facts':facts,'config':row(root/'compiler.json'),'owned_inputs':[row(p,a.root) for p in sorted(root.rglob('*')) if p.is_file()],'native_probe':'NOT_RUN','scope':'PROVIDED_PREREQUISITE' if label=='bootstrap' else 'NEW_SOURCE_BUILT_COMPILER_PENDING_NATIVE_ACCEPTANCE'}
    write_json(a.root/(label+'-profile.json'),snapshot);return snapshot

def guard_profile(a,label):
    saved=json.loads((a.root/(label+'-profile.json')).read_text());facts=saved['facts']
    for item in facts['tools']+facts['SDKruntime']:
        if sha(Path(item['path']))!=item['sha256']:raise ValueError('Provided/rebuilt tool/runtime changed')
    raw=Path(facts['resource_origin'])
    if inventory(raw/'include')!=facts['resource_headers']:raise ValueError('Original resource header drift')
    for key,p in [('sdk_cxx_headers',a.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),('sdk_sysroot_headers',a.sdk_root/'sysroot/usr/include'),('sdk_sysroot_link_files',a.sdk_root/'sysroot/usr/lib')]:
        if inventory(p)!=facts[key]:raise ValueError('Provided SDK input tree changed')
    expected={item['path'] for item in saved['owned_inputs']};root=a.root/('bootstrap-profile' if label=='bootstrap' else 'rebuilt-profile')
    if expected!={p.relative_to(a.root).as_posix() for p in root.rglob('*') if p.is_file()}:raise ValueError('Owned profile file set changed')
    for item in saved['owned_inputs']:
        if sha(a.root/item['path'])!=item['sha256']:raise ValueError('Owned profile bytes changed')
    return saved
