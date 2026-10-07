# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare a private byte-bound Clang20/SDK15 provided native profile."""
import json,os,re,shlex,shutil
from pathlib import Path
from toolkit_io import HERE,TOOL_ROLES,file_row,inventory,sha,json_write

RUNTIME=['libclang_rt.builtins.a','clang_rt.crtbegin.o','clang_rt.crtend.o']
CXXLIBS=['libc++.a','libc++abi.a','libc++experimental.a']

def config(a,resource):
    sdk=a.sdk_root
    common=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(sdk/'sysroot'),'-resource-dir='+str(resource),'--ld-path='+str(a.lld),'-Wl,--threads=1','-L'+str(sdk/'llvm/lib/aarch64-linux-ohos')]
    return {'compilers':{'c':str(a.cc),'cxx':str(a.cxx)},'flags':{'c':common,'cxx':['--driver-mode=g++',*common,'-nostdinc++','-isystem',str(sdk/'llvm/include/libcxx-ohos/include/c++/v1'),'-fexperimental-library','-static-libstdc++','-lc++experimental']},'signer':str(a.signer),'readelf':str(a.readelf),'tmp_dir':str(a.tmp_dir),'private_cache':os.environ['XDG_CACHE_HOME'],'signature_receipts_dir':str(a.root/'signatures'),
            'allowed_output_roots':[str(a.root/'native-build'),str(a.root/'compiler-replay/build'),*[str(p) for p in a.allow_output_root]],
            'protected_inputs':[str(a.sdk_root),str(a.resource_dir),str(resource.parent),str(HERE),str(a.root/'source-patch-check'),str(a.root/'owner.json'),str(a.root/'toolkit-manifest.json'),*[str(getattr(a,k)) for k in TOOL_ROLES]]}

def facts(a,runner):
    if (os.uname().sysname,os.uname().machine)!=('HarmonyOS','aarch64'):raise ValueError('Actual native HarmonyOS/aarch64 required')
    records=[]
    for role in TOOL_ROLES:
        path=getattr(a,role)
        if role=='signer':
            info=runner.run([path,'--help'],'provided-signer-version-unavailable',expected=1)
            version='UNAVAILABLE: supplied binary-sign-tool has no --version/--help API; actual unsupported --help exit1 recorded'
        else:version=runner.run([path,'--version'],'provided-version-'+role)
        records.append(dict(role=role,state='PROVIDED_PREREQUISITE',actual_version=version,**file_row(path)))
    versions={row['role']:row['actual_version'] for row in records}
    if any('clang version 20.1.8' not in versions[k] for k in ['cc','cxx']) or 'LLD 20.1.8' not in versions['lld']:raise ValueError('Actual pinned Clang20.1.8/lld20.1.8 required')
    match=re.search(r'cmake version (\d+)\.(\d+)',versions['cmake'])
    if not match or tuple(map(int,match.groups()))<(3,28):raise ValueError('CMake>=3.28 required')
    default=Path(runner.run([a.cc,'-print-resource-dir'],'default-resource-dir').strip())
    provided=inventory(a.resource_dir/'include');genuine=inventory(default/'include')
    if provided['records']!=genuine['records']:raise ValueError('Selected resource headers differ from selected native compiler defaults')
    cxx=inventory(a.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1');sysroot=inventory(a.sdk_root/'sysroot/usr/include')
    sdk_link=inventory(a.sdk_root/'sysroot/usr/lib')
    runtime=[a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name for name in RUNTIME]+[a.sdk_root/'llvm/lib/aarch64-linux-ohos'/name for name in CXXLIBS]+[a.sdk_root/'NOTICE.txt']
    libs=[dict(state='PROVIDED_PREREQUISITE',**file_row(p)) for p in runtime]
    flags=config(a,a.resource_dir)['flags']['cxx']
    macroflags=[f for f in flags if not f.startswith(('--ld-path=','-Wl,','-L')) and f not in ('-static-libstdc++','-lc++experimental')]
    macros=runner.run([a.cxx,*macroflags,'-std=c++20','-E','-dM','-x','c++','-'],'actual-sdk-macros',input='#include <__config>\n#include <arm_neon.h>\n')
    values=dict(re.findall(r'^#define (\w+)[ \t]+([^\n]+)$',macros,re.M));selected={k:values.get(k) for k in ['_LIBCPP_ABI_NAMESPACE','_LIBCPP_VERSION','__clang_major__','__clang_minor__','__OHOS__','__aarch64__','__cplusplus']}
    if selected['_LIBCPP_ABI_NAMESPACE']!='__n1' or selected['_LIBCPP_VERSION']!='15004' or selected['__clang_major__']!='20' or '__OHOS__' not in values or '__aarch64__' not in values:raise ValueError('Genuine target/header macro mismatch')
    for role in ['cc','cxx','lld']:
        info=runner.run([a.readelf,'-h','-S',getattr(a,role)],'provided-elf-'+role)
        if 'AArch64' not in info or '.codesign' not in info:raise ValueError('Provided native tool is not signed AArch64')
    return {'tool_records':records,'header_inventories':{'clang20_resource':provided,'sdk_cxx':cxx,'sdk_sysroot':sysroot},'sdk_link_inventory':sdk_link,
            'selected_sdk_macros':selected,'external_runtime_inputs':libs,'scope':'Existing tools version/ELF/header/preprocessor facts; new linked runtime NOT_RUN','cleared_ambient_names':runner.removed}

def quote(path):
    s=str(path)
    if any(c in s for c in [';','\n','\r','$']):raise ValueError('Unsupported CMake path character')
    return s.replace('\\','/').replace('"','\\"')

def generate(a,readonly):
    root=a.root;owned=root/'profile';owned.mkdir()
    resource=owned/'resource';shutil.copytree(a.resource_dir/'include',resource/'include')
    for name in RUNTIME:
        source=a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name;dest=resource/'lib/aarch64-linux-ohos'/name
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    shutil.copytree(HERE/'cmake',owned/'cmake');shutil.copytree(HERE/'probes',owned/'probes')
    for name in ['launcher.py','env_policy.py','capture_io.py','capture_store.py','capture_launcher.py']:shutil.copyfile(HERE/name,owned/name)
    shutil.copytree(HERE/'abi-fact-probe',owned/'abi-fact-probe')
    cfg=config(a,resource);cfg['capture_owner']={'root':str(root),'owner_reference':file_row(root/'owner.json')}
    json_write(owned/'compiler.json',cfg)
    for lang in ['c','cxx']:
        file=owned/('clang20-'+lang);file.write_text('#!/usr/bin/sh\nexec '+shlex.join([str(a.python),str(owned/'launcher.py'),str(owned/'compiler.json'),lang,'--'])+' "$@"\n');file.chmod(0o755)
        capture=owned/('capture-clang20-'+lang);capture.write_text('#!/usr/bin/sh\nexec '+shlex.join([str(a.python),str(owned/'launcher.py'),str(owned/'compiler.json'),lang,'--capture'])+' "$@"\n');capture.chmod(0o755)
    lines=['# Native provided compiler profile; no preset probe/compiler/cross results.','include_guard(GLOBAL)']
    for key,value in {'C_COMPILER':owned/'clang20-c','CXX_COMPILER':owned/'clang20-cxx','AR':a.ar,'RANLIB':a.ranlib}.items():lines.append('set(CMAKE_'+key+' "'+quote(value)+'" CACHE FILEPATH "Provided native input")')
    lines+=['set(CMAKE_CXX_SCAN_FOR_MODULES OFF CACHE BOOL "Ordinary translation units; scanner unverified")','list(PREPEND CMAKE_MODULE_PATH "'+quote(owned/'cmake')+'")']
    for kind in ['EXE','SHARED','MODULE']:lines.append('set(CMAKE_'+kind+'_LINKER_FLAGS_INIT "${CMAKE_'+kind+'_LINKER_FLAGS_INIT} -Wl,--threads=1")')
    (owned/'native.cmake').write_text('\n'.join(lines)+'\n')
    capture_lines=lines[:]
    for lang in ['c','cxx']:
        capture_lines=[line.replace(quote(owned/('clang20-'+lang)),quote(owned/('capture-clang20-'+lang))) for line in capture_lines]
    (owned/'capture-native.cmake').write_text('\n'.join(capture_lines)+'\n')
    json_write(owned/'resource-provenance.json',{'clang20_header_source':readonly['header_inventories']['clang20_resource'],'SDK15_overlay':[dict(source=str(a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name),**file_row(resource/'lib/aarch64-linux-ohos'/name,root)) for name in RUNTIME],
               'compiler_rt20':'NOT_BUILT; SDK15 provided CRT/builtins only','sanitizer_profile':'NOT_ENABLED_NOT_TESTED'})
    return {'root':str(root),'toolchain_file':str(owned/'native.cmake'),'cc_launcher':str(owned/'clang20-c'),'cxx_launcher':str(owned/'clang20-cxx'),
            'compiler_config':str(owned/'compiler.json'),'resource_dir':str(resource),'native_link_features_include':str(owned/'cmake/native-link-features.cmake')}

def verify_records(data):
    for row in data['tool_records']+data['external_runtime_inputs']:
        if sha(Path(row['path']))!=row['sha256'] or Path(row['path']).stat().st_size!=row['size']:raise ValueError('Provided tool/runtime bytes drift')
    for inv in [*data['header_inventories'].values(),data['sdk_link_inventory']]:
        if inventory(Path(inv['root']))!=inv:raise ValueError('Provided headers/link input inventory drift')
