#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete offline Vulkan/GPU libraries; explicit native full is parent-owned."""
import sys
sys.dont_write_bytecode = True
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from io_utils import HERE, Runner, check_case_sensitive, private_path, sha, verify_inputs, write_json
import audit
import metadata
import source_tree
import toolchain

STAGES = ['verify-inputs','materialize','prepare','plan','full','acceptance','audit','migrate']
TOOLS = ['sdk_root','cc','cxx','lld','resource_dir','signer','python','cmake','ninja','git','pkgconf','ctest','loader']


def arguments():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=STAGES)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--prefix',type=Path)
    parser.add_argument('--tmp-dir',type=Path,default=Path(os.environ['TMPDIR']))
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--jobs',type=int,default=1)
    parser.add_argument('--runtime-timeout',type=int,default=180)
    for name in TOOLS+['ar','ranlib','readelf','nm']:
        parser.add_argument('--'+name.replace('_','-'),type=Path)
    args=parser.parse_args()
    if args.stage!='verify-inputs' and args.root is None:
        parser.error('Explicit --root required')
    if args.jobs<1 or args.runtime_timeout<1:
        parser.error('Positive jobs/runtime timeout required')
    if args.stage in ['plan','full','acceptance','audit','migrate']:
        missing=[name for name in TOOLS if getattr(args,name) is None]
        if missing:parser.error('Explicit native tools required: '+','.join(missing))
        for name in ['ar','ranlib','readelf','nm']:
            if getattr(args,name) is None:
                setattr(args,name,args.sdk_root/'llvm/bin'/('llvm-'+name))
    elif args.stage=='prepare' and args.git is None:
        parser.error('Explicit --git required')
    for name in TOOLS+['ar','ranlib','readelf','nm']:
        value=getattr(args,name)
        if value is not None:setattr(args,name,value.absolute())
    return args


def own_root(args):
    args.root=private_path(args.root)
    args.prefix=private_path(args.prefix or args.root/'prefix')
    args.tmp_dir=private_path(args.tmp_dir,temporary=True)
    if args.prefix==args.root or args.prefix in args.root.parents or args.root in args.tmp_dir.parents or args.tmp_dir in args.root.parents:
        raise ValueError('Separate owned root/prefix and TMP staging required; prefix cannot contain work root')
    for leaf in ['sources','archives','build','toolchain','logs','receipt-history','upstream-metadata-originals']:
        reserved=args.root/leaf
        if args.prefix==reserved or reserved in args.prefix.parents or args.prefix in reserved.parents:
            raise ValueError('Prefix overlaps reserved source/build/tool/receipt area: '+leaf)
    protected=[getattr(args,name,None) for name in ['sdk_root','resource_dir']]
    protected.extend(value.parent for name in TOOLS+['ar','ranlib','readelf','nm'] if (value:=getattr(args,name,None)) is not None and name not in ['sdk_root','resource_dir','loader'])
    for output in [args.root,args.prefix]:
        for input_path in [p.resolve() for p in protected if p is not None]:
            if output==input_path or output in input_path.parents or input_path in output.parents:
                raise ValueError('Owned output overlaps protected SDK/tool/resource input tree')
    marker=args.root/'.vulkan-builder-owned.json'
    expected={'kind':'portable-vulkan-builder-private-root','input_lock_sha256':sha(HERE/'inputs.lock.json'),
              'prefix':str(args.prefix),'tmp_dir':str(args.tmp_dir),'sources_root':str(args.root/'sources'),'archives_root':str(args.root/'archives')}
    if args.root.exists():
        if not args.resume or not marker.is_file() or json.loads(marker.read_text())!=expected:
            raise ValueError('Existing root requires exact owned same-lock --resume')
    else:
        if args.resume or args.prefix.exists():raise ValueError('Fresh root/prefix must not already exist')
        args.root.mkdir(parents=True,mode=0o700)
        write_json(marker,expected)
    check_case_sensitive(args.tmp_dir)
    lock=(args.root/'.operation.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return lock


def common_flags(args,preset,prefix=None):
    prefix=prefix or args.prefix
    settings={'CMAKE_TOOLCHAIN_FILE':preset,'CMAKE_PROJECT_TOP_LEVEL_INCLUDES':HERE/'cmake/native-host.cmake',
              'CMAKE_MAKE_PROGRAM':args.ninja,'CMAKE_INSTALL_PREFIX':prefix,'CMAKE_PREFIX_PATH':prefix,
              'CMAKE_BUILD_TYPE':'Release','BUILD_SHARED_LIBS':'OFF','CMAKE_POSITION_INDEPENDENT_CODE':'ON',
              'CMAKE_SKIP_RPATH':'ON','CMAKE_INSTALL_DO_STRIP':'OFF','CMAKE_EXPORT_COMPILE_COMMANDS':'ON',
              'CMAKE_C_STANDARD':17,'CMAKE_CXX_STANDARD':17,'CMAKE_CXX_STANDARD_REQUIRED':'ON','CMAKE_CXX_EXTENSIONS':'OFF',
              'CMAKE_CXX_SCAN_FOR_MODULES':'OFF','CMAKE_INSTALL_LIBDIR':'lib','CMAKE_INSTALL_INCLUDEDIR':'include',
              'CMAKE_INSTALL_DATADIR':'share','CMAKE_INSTALL_BINDIR':'bin','CMAKE_JOB_POOLS':f'compile={args.jobs};link=1',
              'CMAKE_JOB_POOL_COMPILE':'compile','CMAKE_JOB_POOL_LINK':'link','FETCHCONTENT_FULLY_DISCONNECTED':'ON',
              'FETCHCONTENT_UPDATES_DISCONNECTED':'ON','CMAKE_FIND_USE_PACKAGE_REGISTRY':'OFF',
              'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY':'OFF','CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH':'OFF',
              'CMAKE_FIND_USE_CMAKE_SYSTEM_PATH':'OFF','CMAKE_FIND_USE_INSTALL_PREFIX':'OFF',
              'PKG_CONFIG_EXECUTABLE':args.pkgconf,'PKG_CONFIG_ARGN':'--static','Python_EXECUTABLE':args.python,
              'Python3_EXECUTABLE':args.python,'PYTHON_EXECUTABLE':args.python,'GIT_EXECUTABLE':args.git}
    return ['-G','Ninja',*[f'-D{k}={v}' for k,v in settings.items()]]


def component_plan(args,preset):
    recipe=json.loads((HERE/'recipe.json').read_text())
    plans=[]
    for name in recipe['build_groups']:
        options=dict(recipe['options'][name])
        source=args.root/'sources'/name
        if name=='shaderc':
            options.update(SHADERC_SPIRV_TOOLS_DIR=args.root/'sources/spirv-tools',SHADERC_SPIRV_HEADERS_DIR=args.root/'sources/spirv-headers',SHADERC_GLSLANG_DIR=args.root/'sources/glslang')
        if name=='vulkan-utility':
            options.update(VulkanHeaders_DIR=args.prefix/'share/cmake/VulkanHeaders',VULKAN_HEADERS_INSTALL_DIR=args.prefix,CMAKE_CXX_FLAGS='-DVK_USE_PLATFORM_OHOS')
        if name=='spirv-reflect':
            source=HERE/'cmake/reflect'
            options.update(REFLECT_SOURCE=args.root/'sources/spirv-reflect',SPIRV_PUBLIC_INCLUDE=args.prefix/'include')
        build=args.root/'build'/name
        plans.append({'name':name,'configure':[args.cmake,'-S',source,'-B',build,*common_flags(args,preset),*[f'-D{k}={v}' for k,v in options.items()]],
                      'build':[args.cmake,'--build',build,'--parallel',str(args.jobs)],'install':[args.cmake,'--install',build]})
    return plans


def guard_dependencies(args,build,prefix):
    cache=build/'CMakeCache.txt'
    if not cache.is_file():raise ValueError('Real configured CMake cache absent')
    for line in cache.read_text().splitlines():
        if line.startswith(('//','#')) or '=' not in line or 'LIBRAR' not in line.split('=',1)[0].upper():continue
        for value in line.split('=',1)[1].split(';'):
            if not value.startswith('/') or not re.search(r'\.(?:a|so)(?:\.\d+)*$',value):continue
            path=Path(value).resolve()
            if path==args.loader.resolve():continue
            if not any(path==allowed.resolve() or allowed.resolve() in path.parents for allowed in [args.root,prefix,args.sdk_root]):
                raise ValueError('Configured dependency escaped new source/prefix/SDK closure: '+value)


def configured_run(args,runner,command,label,build=None,prefix=None):
    audit.source_guard(args.root)
    try:
        output=runner.run(command,label)
    finally:
        audit.source_guard(args.root)
    if build is not None:guard_dependencies(args,build,prefix or args.prefix)
    return output


def native_preflight(args,runner,preset):
    build=args.root/'build/native-preflight'
    configured_run(args,runner,[args.cmake,'-S',HERE/'preflight','-B',build,*common_flags(args,preset)],'native-preflight-configure',build)
    configured_run(args,runner,[args.cmake,'--build',build,'--parallel',str(args.jobs)],'native-preflight-build')
    configured_run(args,runner,[args.ctest,'--test-dir',build,'--output-on-failure'],'native-preflight-real-runtime')


def consumers(args,runner,preset,build,prefix,mode,label,forbidden=()):
    flags=common_flags(args,preset,prefix)+[f'-DPINNED_PREFIX={prefix}',f'-DSYSTEM_VULKAN_LOADER={args.loader}',f'-DVULKAN_CONSUMER={mode}',
           f'-DSPIRV-Headers_DIR={prefix}/share/cmake/SPIRV-Headers',f'-DVulkanHeaders_DIR={prefix}/share/cmake/VulkanHeaders']
    runner.env['PKG_CONFIG_LIBDIR']=str(prefix/'lib/pkgconfig')+':'+str(prefix/'share/pkgconfig')
    configured_run(args,runner,[args.cmake,'-S',HERE/'consumers','-B',build,*flags],label+'-configure',build,prefix)
    commands=runner.run([args.ninja,'-C',build,'-t','commands'],label+'-actual-link-plan')
    if any(path in commands for path in forbidden):raise ValueError('Fresh moved consumer retains old source/prefix route')
    for line in commands.splitlines():
        if 'library-acceptance' in line and ' -o ' in line and re.search(r'lib(?:glslang|SPIRV-Tools).*\.a',line):
            raise ValueError('Combined consumer used a second standalone compiler/optimizer library')
    configured_run(args,runner,[args.cmake,'--build',build,'--parallel',str(args.jobs)],label+'-build')
    executables=['library-acceptance','public-dependency-acceptance','utility-acceptance','borrowed-handle-acceptance','public-package-acceptance','glslang-package-acceptance','tools-package-acceptance','blender-find-acceptance','system-loader-acceptance']
    files=executables+['libpic-'+group+'.so' for group in ['combined','reflect','utility','standalone']]
    artifacts=[audit.elf(args,runner,build/leaf,label+'-'+leaf,loader=leaf=='system-loader-acceptance',require_n1=leaf=='library-acceptance') for leaf in files]
    outputs={}
    with tempfile.TemporaryDirectory(prefix='vulkan-native-fixtures-',dir=runner.tmp) as td:
        fixture=Path(td)/'shader SPIRV 图形 fixtures with spaces';fixture.mkdir()
        for leaf in executables:
            command=[build/leaf]
            if leaf=='library-acceptance':command.append(fixture)
            if leaf=='system-loader-acceptance':command.append(args.loader)
            audit.source_guard(args.root)
            # Layer settings read only this isolated private configuration directory.
            isolation={'XDG_CONFIG_HOME':runner.env.get('XDG_CONFIG_HOME'),'VK_LAYER_SETTINGS_PATH':runner.env.get('VK_LAYER_SETTINGS_PATH')}
            runner.env.update(XDG_CONFIG_HOME=str(fixture),VK_LAYER_SETTINGS_PATH=str(fixture))
            try:output=runner.run(command,label+'-'+leaf+'-real-runtime',cwd=fixture,timeout=args.runtime_timeout)
            finally:
                for key,value in isolation.items():
                    if value is None:runner.env.pop(key,None)
                    else:runner.env[key]=value
                audit.source_guard(args.root)
            parsed=json.loads(output.strip().splitlines()[-1]);outputs[leaf]=parsed
        if outputs['system-loader-acceptance']['runtime_loader']!=str(args.loader):raise ValueError('Actual native system loader substituted')
        spv=[{'path':str(p.relative_to(fixture)),'sha256':sha(p),'size':p.stat().st_size} for p in sorted(fixture.glob('*.spv'))]
        if len(spv)!=5:raise ValueError('Real shader compiler SPIRV fixture set incomplete')
    return {'mode':mode,'artifacts':artifacts,'native_results':outputs,'actual_spirv_outputs':spv,'temporary_fixtures_cleaned':True,
            'pic_modules_dlopened':False,'gpu_device_dispatch_render_pixels_wsi':'NOT_TESTED'}


def run(args):
    verified=verify_inputs()
    if args.stage=='verify-inputs':print(json.dumps(verified,indent=2));return
    guard=own_root(args)
    try:
        runner=Runner(args.root,args.tmp_dir)
        if args.stage=='materialize':print(json.dumps(source_tree.materialize(args.root,runner),indent=2));return
        if args.stage=='prepare':print(json.dumps(source_tree.prepare(args.root,runner,args.git),indent=2));return
        write_json(args.root/'prerequisites.json',toolchain.preflight(args,runner))
        preset=toolchain.generate(args,args.root)
        # Upstream generators also spawn bare git; bind that name to caller's
        # selected tool and prevent an ancestor development repo from discovery.
        shim=args.root/'toolchain/git'
        import shlex
        shim.write_text('#!/usr/bin/sh\nexec '+shlex.quote(str(args.git))+' "$@"\n')
        shim.chmod(0o755)
        runner.env['PATH']=str(shim.parent)+os.pathsep+runner.env.get('PATH','')
        runner.env['GIT_CEILING_DIRECTORIES']=str(args.root/'sources')
        plans=component_plan(args,preset)
        write_json(args.root/'plan.json',{'commands':[{k:[str(v) for v in values] if isinstance(values,list) else values for k,values in row.items()} for row in plans],
                   'input_lock_sha256':sha(HERE/'inputs.lock.json'),'new_full_native_acceptance':False,'gpu_pixels_wsi':'NOT_TESTED'})
        if args.stage=='plan':print('PASS actual explicit native prerequisite/link plan; no native objects/runtime PASS');return
        consumer=lambda build,prefix,mode,label,forbidden=():consumers(args,runner,preset,build,prefix,mode,label,forbidden)
        if args.stage=='full':
            history=args.root/'receipt-history'/runner.logs.name
            for leaf in ['full-native-acceptance.json','acceptance.json','artifacts.json','migration.json']:
                file=args.root/leaf
                if file.exists():history.mkdir(parents=True,exist_ok=True);file.rename(history/leaf)
            source_tree.prepare(args.root,runner,args.git)
            native_preflight(args,runner,preset)
            for group in plans:
                for stage in ['configure','build','install']:
                    configured_run(args,runner,group[stage],group['name']+'-'+stage,args.root/'build'/group['name'] if stage=='configure' else None)
                install=args.root/'build'/group['name']/'install_manifest.txt'
                if any(not Path(path).is_relative_to(args.prefix) for path in install.read_text().splitlines()):raise ValueError('Install escaped private prefix')
            metadata.normalize(args,args.root)
            audit.freeze_prefix(args,args.root)
        if args.stage in ['full','acceptance']:
            audit.verify_prefix(args,args.root)
            results=[consumer(args.root/'build'/('acceptance-'+mode),args.prefix,mode,'original-'+mode) for mode in ['CMAKE','PC']]
            write_json(args.root/'acceptance.json',{'consumers':results,'scope':'Real native library APIs and loader facts; no GPU pixels/WSI'})
        if args.stage in ['full','audit']:audit.artifacts(args,args.root,runner)
        if args.stage in ['full','migrate']:audit.migrate(args,args.root,runner,consumer)
        if args.stage=='full':
            write_json(args.root/'full-native-acceptance.json',{'result':'PASS complete new source-built offline native Vulkan libraries and moved CMAKE/PC consumers',
                       'input_lock_sha256':sha(HERE/'inputs.lock.json'),'gpu_pixels_wsi_vma_device_allocation_blender_hap':'NOT_TESTED','pic_modules_dlopened':False})
        print('PASS actual requested stage:',args.stage)
    finally:guard.close()


if __name__=='__main__':
    os.nice(10)
    run(arguments())
