#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent portable sealed Base builder. Source/plan stages never compile."""
import sys
sys.dont_write_bytecode=True
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
from io_utils import HERE, REPO, Runner, check_case_sensitive, private_path, sha, verify_inputs, write_json
import source_tree
import toolchain
import metadata

RECIPE=json.loads((HERE/'recipe.json').read_text())


def args_parse():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['verify-inputs','materialize','prepare','plan','full','acceptance','audit','migrate'])
    for key in ['root','prefix','tmp-dir','sdk-root','cc','cxx','lld','resource-dir','ar','ranlib','readelf','nm','signer','python','cmake','ninja','git','pkgconf','ctest']:
        parser.add_argument('--'+key,type=Path,required=key not in ['root','prefix','tmp-dir'])
    parser.add_argument('--jobs',type=int,default=1);parser.add_argument('--link-jobs',type=int,default=1)
    parser.add_argument('--runtime-timeout',type=int,default=180);parser.add_argument('--resume',action='store_true')
    parser.add_argument('--output',type=Path)
    a=parser.parse_args()
    if not a.root or not a.prefix:parser.error('Explicit new --root and --prefix required')
    a.root=private_path(a.root);a.prefix=private_path(a.prefix)
    a.tmp_dir=private_path(a.tmp_dir or os.environ['TMPDIR'],temporary=True)
    if a.root==a.prefix or a.prefix in a.root.parents:raise ValueError('Prefix may not equal/contain owned root')
    if a.jobs<1 or a.link_jobs!=1 or a.jobs>4 or a.runtime_timeout<1:raise ValueError('Explicit limited native jobs, one link job and positive timeout required')
    protected=[REPO,a.sdk_root,a.resource_dir,*[getattr(a,k).parent for k in ['cc','cxx','lld','ar','ranlib','readelf','nm','signer','python','cmake','ninja','git','pkgconf','ctest']]]
    reserved=[Path(os.environ['XDG_CACHE_HOME'])/name for name in ['blender-ohos-deps-base','blender-ohos-deps-vulkan','blender-ohos-volume-formal-fresh-1','blender-ohos-vulkan-formal-fresh-1','blender-ohos-toolchain','blender-ohos-python-3.13.13-build']]
    for name in ['sources','build','archives','toolchain','tools','logs','migration','tool-bin','source-guards','consumers','upstream-metadata-originals']:
        boundary=(a.root/name).resolve();prefix=a.prefix.resolve()
        if prefix==boundary or prefix in boundary.parents or boundary in prefix.parents:raise ValueError('Prefix overlaps owned reserved subtree: '+name)
    for output in [a.root,a.prefix]:
        for boundary in protected+reserved:
            x,y=output.resolve(),boundary.resolve()
            if x==y or x in y.parents or y in x.parents:raise ValueError('Owned output overlaps explicit tool/SDK/input or protected existing root')
    for key in ['cc','cxx','lld','resource_dir','ar','ranlib','readelf','nm','signer','python','cmake','ninja','git','pkgconf','ctest','sdk_root']:
        p=getattr(a,key)
        if not p.is_absolute() or not p.exists():raise ValueError('Explicit existing absolute prerequisite required: '+key)
    return a


def source_guard(root):
    if not (root/'sources').exists():return []
    return [source_tree.verify_tree(root/'sources'/name,name,patched=True) for name in source_tree.ACTIVE]


def guarded(a,runner,label,operation):
    before=source_guard(a.root)
    try:return operation()
    finally:
        after=source_guard(a.root)
        write_json(a.root/'source-guards'/(label+'.json'),{'before':before,'after_including_failure':after,'bytecode_disabled':True})


def common(a,preset):
    return {'CMAKE_BUILD_TYPE':'Release','CMAKE_INSTALL_PREFIX':str(a.prefix),'CMAKE_INSTALL_LIBDIR:STRING':'lib','CMAKE_INSTALL_INCLUDEDIR:STRING':'include',
            'CMAKE_POSITION_INDEPENDENT_CODE':'ON','BUILD_SHARED_LIBS':'OFF','CMAKE_TOOLCHAIN_FILE':str(preset),
            'CMAKE_PROJECT_INCLUDE':str(HERE/'cmake/native-host.cmake'),'CMAKE_PREFIX_PATH':str(a.prefix),
            'CMAKE_FIND_PACKAGE_PREFER_CONFIG':'ON','CMAKE_FIND_USE_PACKAGE_REGISTRY':'OFF','CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY':'OFF',
            'CMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH':'OFF','CMAKE_CXX_STANDARD':'17','CMAKE_CXX_STANDARD_REQUIRED':'ON',
            'CMAKE_JOB_POOLS':'compile='+str(a.jobs)+';link=1','CMAKE_JOB_POOL_COMPILE':'compile','CMAKE_JOB_POOL_LINK':'link',
            'CMAKE_EXPORT_COMPILE_COMMANDS':'ON','PKG_CONFIG_EXECUTABLE':str(a.root/'tools/bin/pkgconf')}


def configure_command(a,name,preset):
    source=a.root/'sources'/name;options=common(a,preset)
    options.update(RECIPE['cmake_options'].get(name,{}))
    if name=='zlib':options['BASE_ZLIB_SOURCE']=str(source);source=HERE/'cmake/zlib'
    elif name=='zstd':source=source/'build/cmake'
    elif name=='png':options['PNG_LIBCONF_HEADER:FILEPATH']=str(source/'scripts/pnglibconf.h.prebuilt')
    elif name=='freetype':
        options.update(BROTLIDEC_INCLUDE_DIRS=str(a.prefix/'include'),BROTLIDEC_LIBRARIES=str(a.prefix/'lib/libbrotlidec.a')+';'+str(a.prefix/'lib/libbrotlicommon.a'),BROTLIDEC_VERSION='1.0.9',
                       ZLIB_INCLUDE_DIR=str(a.prefix/'include'),ZLIB_LIBRARY=str(a.prefix/'lib/libz.a'),PNG_PNG_INCLUDE_DIR=str(a.prefix/'include'),PNG_LIBRARY_RELEASE=str(a.prefix/'lib/libpng16.a'))
    elif name=='harfbuzz':
        options.update(FREETYPE_LIBRARY_RELEASE=str(a.prefix/'lib/libfreetype.a'),FREETYPE_INCLUDE_DIR_freetype2=str(a.prefix/'include/freetype2'),FREETYPE_INCLUDE_DIR_ft2build=str(a.prefix/'include/freetype2'),
                       CMAKE_REQUIRED_LIBRARIES=';'.join(map(str,[a.prefix/'lib/libpng16.a',a.prefix/'lib/libz.a',a.prefix/'lib/libbrotlidec.a',a.prefix/'lib/libbrotlicommon.a']))+';m')
    return [str(a.cmake),'-S',str(source),'-B',str(a.root/'build'/name),'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(a.ninja),*[f'-D{k}={v}' for k,v in options.items()]]


def meson_native(a):
    file=a.root/'toolchain/meson-native.ini'
    binaries={'c':a.root/'toolchain/clang20-c','cpp':a.root/'toolchain/clang20-cxx','ar':a.ar,'nm':a.nm,'ranlib':a.ranlib,'pkg-config':a.root/'tools/bin/pkgconf','cmake':a.cmake}
    text='[binaries]\n'+'\n'.join(k+' = '+repr(str(v)) for k,v in binaries.items())+'\n[built-in options]\nc_args = [\'-O2\', \'-fPIC\']\ncpp_args = [\'-O2\', \'-fPIC\']\nc_link_args = [\'-Wl,--threads=1\']\ncpp_link_args = [\'-Wl,--threads=1\']\n'
    file.write_text(text);return file


def meson_setup(a,name,native):
    return [str(a.python),str(a.root/'sources/meson/meson.py'),'setup',str(a.root/'build'/name),str(a.root/'sources'/name),
            '--native-file',str(native),'--prefix',str(a.root/'tools' if name=='pkgconf' else a.prefix),'--libdir','lib',
            '--buildtype','release','--default-library','static','--wrap-mode','nodownload',*RECIPE['meson_options'][name]]


def install_brotli(a):
    build=a.root/'build/brotli';rows=[]
    for name in ['common','dec','enc']:
        src=build/('libbrotli'+name+'-static.a');dst=a.prefix/'lib'/('libbrotli'+name+'.a')
        if not src.is_file():raise ValueError('Missing actual selected Brotli static target')
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        rows.append({'build':str(src),'install':str(dst),'sha256':sha(dst),'actual_build_sha256':sha(src)})
    shutil.copytree(a.root/'sources/brotli/c/include/brotli',a.prefix/'include/brotli')
    cli=build/'brotli';dst=a.prefix/'bin/brotli';dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(cli,dst);dst.chmod(0o755)
    if sha(cli)!=sha(dst):raise ValueError('Brotli signed install bytes drift')
    write_json(a.root/'brotli-selected-static-install.json',{'recipe_added_static_install':True,'static_archives':rows,'signed_cli_build_install_sha256':sha(dst),'upstream_unconditional_shared_targets_not_built':True})


def full(a,runner,preset):
    import acceptance
    import audit
    native=meson_native(a)
    runner.env.update(NINJA=str(a.ninja),PKG_CONFIG_LIBDIR=str(a.prefix/'lib/pkgconfig'),PKG_CONFIG=str(a.root/'tools/bin/pkgconf'))
    # Version scripts/tools private PATH binds bare Ninja/CMake/Git names to selected tools.
    for name in ['pkgconf',*RECIPE['runtime_groups']]:
        if name in ['pkgconf','fribidi']:
            command=meson_setup(a,name,native)
        else:command=configure_command(a,name,preset)
        guarded(a,runner,name+'-configure',lambda:runner.run(command,name+'-configure'))
        if name in ['pkgconf','fribidi']:command=[a.ninja,'-C',a.root/'build'/name,'-j'+str(a.jobs)]
        else:
            command=[a.cmake,'--build',a.root/'build'/name,'--parallel',str(a.jobs)]
            if name=='brotli':command+=['--target','brotlicommon-static','brotlidec-static','brotlienc-static','brotli']
        guarded(a,runner,name+'-build',lambda:runner.run(command,name+'-build'))
        if name=='brotli':guarded(a,runner,name+'-install',lambda:install_brotli(a))
        else:
            command=[a.python,a.root/'sources/meson/meson.py','install','-C',a.root/'build'/name,'--no-rebuild'] if name in ['pkgconf','fribidi'] else [a.cmake,'--install',a.root/'build'/name]
            guarded(a,runner,name+'-install',lambda:runner.run(command,name+'-install'))
        if name=='pkgconf':
            runner.run([a.root/'tools/bin/pkgconf','--version'],'new-source-built-pkgconf-version')
            (a.root/'tools/bin/pkg-config').symlink_to('pkgconf')
            write_json(a.root/'new-source-built-pkgconf.json',{'source_sha256':next(d['sha256'] for d in source_tree.sources()['sources'] if d['name']=='pkgconf'),
                       'installed_executable_sha256':sha(a.root/'tools/bin/pkgconf'),'source_built_this_full':True,'bootstrap_not_used_for_native_consumers':True})
        if name=='zlib':
            pcs,configs=metadata.templates();pc=a.prefix/'lib/pkgconfig/zlib.pc';pc.parent.mkdir(parents=True,exist_ok=True);pc.write_text(pcs['zlib'])
    metadata.normalize(a,a.root)
    guarded(a,runner,'native-acceptance',lambda:acceptance.run(a,runner,preset))
    guarded(a,runner,'native-audit',lambda:audit.run(a,runner))
    guarded(a,runner,'native-migration',lambda:acceptance.migrate(a,runner,preset))


def main():
    a=args_parse();inputs=verify_inputs();metadata.verify_fonts();check_case_sensitive(a.tmp_dir)
    owner=a.root/'owner.json'
    if a.stage=='full' and (a.root.exists() or a.prefix.exists()) and not a.resume:raise ValueError('Fresh full requires absent owned root and prefix; no implicit resume')
    a.root.mkdir(parents=True,exist_ok=True)
    with (a.root/'operation.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker={'kind':'independent-source-built-Base','inputs_lock_sha256':sha(HERE/'inputs.lock.json'),'prefix':str(a.prefix)}
        if owner.exists() and json.loads(owner.read_text())!=marker:raise ValueError('Owned root/code/prefix identity drift')
        if not owner.exists() and any(p.name!='operation.lock' for p in a.root.iterdir()):raise ValueError('Unowned nonempty output root')
        write_json(owner,marker);runner=Runner(a.root,a.tmp_dir)
        shims=a.root/'tool-bin';shims.mkdir(exist_ok=True)
        for name,key in [('git','git'),('ninja','ninja'),('cmake','cmake')]:
            file=shims/name;file.write_text('#!/usr/bin/sh\nexec '+shlex.quote(str(getattr(a,key)))+' "$@"\n');file.chmod(0o755)
        runner.env['PATH']=str(shims)+':'+runner.env.get('PATH','');runner.env['GIT_CEILING_DIRECTORIES']=str(a.root/'sources')
        if a.stage=='verify-inputs':result=inputs
        elif a.stage=='materialize':result=source_tree.materialize(a.root,runner)
        else:
            result=guarded(a,runner,'prepare',lambda:source_tree.prepare(a.root,runner,a.git))
            if a.stage!='prepare':
                prereqs=toolchain.preflight(a,runner);write_json(a.root/'prerequisites.json',prereqs)
                preset=toolchain.generate(a,a.root)
                if a.stage=='plan':
                    native=meson_native(a)
                    result={'native_commands_not_executed':True,'source_scope':result,'prerequisites':prereqs,
                            'configure_commands':{name:meson_setup(a,name,native) if name in ['pkgconf','fribidi'] else configure_command(a,name,preset) for name in ['pkgconf',*RECIPE['runtime_groups']]},
                            'gates':RECIPE['gates'],'existing_pkgconf':'Explicit bootstrap prerequisite; full builds own source tool before runtime graph'}
                    write_json(a.root/'plan.json',result)
                elif a.stage=='full':
                    full(a,runner,preset);result={'native_full':'PASS actual new full, including migration','BlenderTextImage':'NOT_TESTED','HAP':'NOT_TESTED'}
                elif a.stage=='acceptance':
                    import acceptance
                    result=guarded(a,runner,'acceptance',lambda:acceptance.run(a,runner,preset))
                elif a.stage=='audit':
                    import audit
                    result=guarded(a,runner,'audit',lambda:audit.run(a,runner))
                else:
                    import acceptance
                    result=guarded(a,runner,'migration',lambda:acceptance.migrate(a,runner,preset))
        if a.output:write_json(a.output,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
