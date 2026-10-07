#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent sealed offline Draco+meshoptimizer+real Blender native bridge builder."""
import sys
sys.dont_write_bytecode=True
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import re
from source_guard import HERE, REPO, sha, verify_inputs, inventory, repo_file
from source_tree import prepare, sources, verify_bridges
from io_utils import Runner, ownership, private_path, write_json
import toolchain
import metadata
import audit

STAGES=['verify-inputs','prepare','plan','full','acceptance','audit','migrate','copy-inputs']
TOOLS=['sdk_root','cc','cxx','lld','resource_dir','signer','python','cmake','ninja','pkgconf','git']


def arguments():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=STAGES)
    for n in ['root','prefix','tmp_dir','destination',*TOOLS]:p.add_argument('--'+n.replace('_','-'),type=Path)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--jobs',type=int,choices=[1,2],default=1)
    p.add_argument('--runtime-timeout',type=int,default=180)
    a=p.parse_args()
    if a.runtime_timeout<1:p.error('Positive runtime timeout required')
    if a.stage=='verify-inputs':return a
    if a.stage=='copy-inputs':
        if a.destination is None:p.error('copy-inputs requires absent --destination in private cache')
        a.destination=private_path(a.destination);return a
    if a.root is None or a.tmp_dir is None:p.error('Explicit --root and --tmp-dir required')
    a.root=private_path(a.root);a.prefix=private_path(a.prefix or a.root/'prefix');a.tmp_dir=private_path(a.tmp_dir,True)
    if not a.prefix.is_relative_to(a.root) or a.prefix==a.root:p.error('Prefix must be strictly within owned root')
    required=['git'] if a.stage=='prepare' else TOOLS
    for n in required:
        value=getattr(a,n)
        if value is None:p.error('Explicit --'+n.replace('_','-')+' required')
        value=value.resolve() if n in ['sdk_root','resource_dir'] else value.absolute()
        if any(c in str(value) for c in '\n\r;"$'):p.error('Unsafe tool path')
        setattr(a,n,value)
    return a


def common(args,preset,prefix=None):
    prefix=prefix or args.prefix
    values={'CMAKE_TOOLCHAIN_FILE':str(preset),'CMAKE_PROJECT_INCLUDE':str(HERE/'cmake/native-host.cmake'),
            'CMAKE_MAKE_PROGRAM':str(args.ninja),'PKG_CONFIG_EXECUTABLE':str(args.pkgconf),
            'PYTHON_EXECUTABLE':str(args.python),'Python3_EXECUTABLE':str(args.python),
            'CMAKE_INSTALL_PREFIX':str(prefix),'CMAKE_INSTALL_LIBDIR':'lib','CMAKE_BUILD_TYPE':'Release',
            'CMAKE_CXX_STANDARD':'20','CMAKE_CXX_STANDARD_REQUIRED':'ON','CMAKE_POSITION_INDEPENDENT_CODE':'ON',
            'CMAKE_CXX_SCAN_FOR_MODULES':'OFF','CMAKE_SKIP_RPATH':'ON','CMAKE_EXPORT_COMPILE_COMMANDS':'ON',
            'CMAKE_JOB_POOLS':'compile='+str(args.jobs)+';link=1','CMAKE_JOB_POOL_COMPILE':'compile','CMAKE_JOB_POOL_LINK':'link',
            'CMAKE_FIND_USE_PACKAGE_REGISTRY':'OFF','CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY':'OFF',
            'CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH':'OFF','CMAKE_FIND_USE_CMAKE_SYSTEM_PATH':'OFF',
            'FETCHCONTENT_FULLY_DISCONNECTED':'ON','FETCHCONTENT_UPDATES_DISCONNECTED':'ON','CMAKE_POLICY_VERSION_MINIMUM':'3.5'}
    return ['-G','Ninja']+['-D'+k+'='+v for k,v in values.items()]


def command_plan(args,preset):
    rows=[]
    settings={'draco':{'BUILD_SHARED_LIBS':'OFF','DRACO_MESH_COMPRESSION':'ON','DRACO_POINT_CLOUD_COMPRESSION':'ON',
                      'DRACO_PREDICTIVE_EDGEBREAKER':'ON','DRACO_STANDARD_EDGEBREAKER':'ON','DRACO_BACKWARDS_COMPATIBILITY':'ON',
                      'DRACO_GLTF_BITSTREAM':'OFF','DRACO_TRANSCODER_SUPPORTED':'OFF','DRACO_TESTS':'OFF','DRACO_INSTALL':'ON'},
              'meshoptimizer':{'MESHOPT_BUILD_SHARED_LIBS':'OFF','MESHOPT_BUILD_GLTFPACK':'OFF','MESHOPT_BUILD_DEMO':'OFF','MESHOPT_INSTALL':'ON'},
              'bridges':{'GLTF_PREFIX':str(args.prefix),'GLTF_BLENDER_SOURCE':str(args.root/'blender-bridge-source')}}
    for name in ['draco','meshoptimizer','bridges']:
        src=HERE/'cmake/bridges' if name=='bridges' else args.root/'sources'/name
        build=args.root/'build'/name
        flags=common(args,preset)+['-D'+k+'='+v for k,v in settings[name].items()]
        rows.append({'name':name,'configure':[str(args.cmake),'-S',str(src),'-B',str(build),*flags],
                     'build':[str(args.cmake),'--build',str(build),'--parallel',str(args.jobs)],
                     'install':[str(args.cmake),'--install',str(build)]})
    return rows


def guard_cache(args,build,prefix):
    cache=(build/'CMakeCache.txt').read_text()
    selected=re.search(r'^CMAKE_CXX_COMPILER:(?:STRING|FILEPATH)=(.+)$',cache,re.M)
    if selected is None or Path(selected.group(1)).resolve()!=(args.root/'toolchain/clang20-cxx').resolve():
        raise ValueError('Actual exact selected compiler launcher missing')
    for line in cache.splitlines():
        if line.startswith(('#','//')) or '=' not in line:continue
        key,value=line.split('=',1)
        if 'LIBRAR' not in key.upper():continue
        for token in value.split(';'):
            if token.startswith('/') and re.search(r'\.(a|so(?:\.\d+)*)$',token):
                path=Path(token).resolve()
                if not any(path.is_relative_to(p.resolve()) for p in [args.root,prefix,args.sdk_root]):
                    raise ValueError('Configured dependency outside selected source/SDK: '+line)


def consume(args,runner,preset,build,prefix,mode,label,consumer_source=None,forbidden=None):
    runner.env['PKG_CONFIG_LIBDIR']=str(prefix/'lib/pkgconfig')
    source=consumer_source or HERE/'consumers'
    flags=common(args,preset,prefix)+['-DGLTF_PREFIX='+str(prefix),'-DGLTF_CONSUMER='+mode]
    runner.run([args.cmake,'-S',source,'-B',build,*flags],label+'-configure')
    guard_cache(args,build,prefix)
    commands=runner.run([args.ninja,'-C',build,'-t','commands'],label+'-link-plan')
    if any(p in commands for p in forbidden or []):raise ValueError('Moved consumer still routes original prefix')
    runner.run([args.cmake,'--build',build,'--parallel',str(args.jobs)],label+'-build',timeout=None)
    artifact=audit.elf(args,runner,build/'gltf_native_acceptance',label+'-consumer')
    with tempfile.TemporaryDirectory(prefix='gltf-real-geometry-',dir=args.tmp_dir) as td:
        fixtures=Path(td)/'几何 geometry input with spaces';fixtures.mkdir()
        out=runner.run([build/'gltf_native_acceptance',fixtures,*[prefix/'lib'/n for n in metadata.BRIDGES]],label+'-native-run',timeout=args.runtime_timeout,
                       extra_env={'LD_LIBRARY_PATH':str(prefix/'lib')})
        if not re.search(r'^ALL PASS gltf native checks=\d+ libcxx=15004 namespace=__n1$',out,re.M):
            raise ValueError('Native geometry/ABI sentinel absent')
        files=inventory(fixtures)
    return {'mode':mode,'artifact':artifact,'stdout':out,'fixtures':files,'temporary_tree_cleaned':True}


def bridge_acceptance(args,runner,prefix,label):
    with tempfile.TemporaryDirectory(prefix='gltf-real-bridge-',dir=args.tmp_dir) as td:
        fixtures=Path(td)/'压缩 glTF fixtures with spaces';fixtures.mkdir()
        report=Path(td)/'report.json'
        runner.run([args.python,HERE/'acceptance/bridge_abi_acceptance.py','--source-root',REPO,'--lib-dir',prefix/'lib',
                    '--fixtures',fixtures,'--report',report],label+'-ABI-dlopen-geometry',timeout=args.runtime_timeout)
        value=json.loads(report.read_text())
        if value.get('result')!='PASS':raise ValueError('Actual bridge ABI report not PASS')
        # Preserve genuine encoded extension fixture bytes for separate parent bpy stage.
        destination=args.root/('fixtures-'+label)
        if destination.exists():raise ValueError('Refuse overwrite of recorded fixtures; use new acceptance label')
        shutil.copytree(fixtures,destination)
        value['fixtures_saved']=str(destination);value['fixture_inventory']=inventory(destination)
    value['temporary_tree_cleaned']=True
    return value


def acceptance(args,runner,preset,label='original'):
    metadata.verify(args)
    tests=[consume(args,runner,preset,args.root/'build'/('acceptance-'+label+'-'+mode),args.prefix,mode,label+'-'+mode) for mode in ['CMAKE','PC']]
    bridge=bridge_acceptance(args,runner,args.prefix,label)
    result={'result':'PASS actual native CMAKE/PC static geometry and actual Blender DLL ABI/dlopen/geometry ownership',
            'consumers':tests,'bridge':bridge,'prefix_seal_sha256':sha(args.root/'prefix-seal.json'),
            'input_lock_sha256':sha(HERE/'inputs.lock.json'),'bpy':'NOTRUN separate parent postlink stage','HAP':'NOTRUN'}
    write_json(args.root/'acceptance.json',result);return result


def validate_artifact_binding(args):
    value=json.loads((args.root/'artifacts.json').read_text())
    if not value.get('result','').startswith('PASS') or value['input_lock_sha256']!=sha(HERE/'inputs.lock.json') or \
       value['prefix_seal_sha256']!=sha(args.root/'prefix-seal.json') or value['acceptance_sha256']!=sha(args.root/'acceptance.json'):
        raise ValueError('Current artifact/acceptance/input/prefix receipt binding required')
    return value


def invalidate(args,runner,leaves):
    history=args.root/'receipt-history'/runner.logs.name
    for leaf in leaves:
        p=args.root/leaf
        if p.exists():history.mkdir(parents=True,exist_ok=True);p.rename(history/leaf)


def migrate(args,runner,preset):
    metadata.verify(args)
    validate_artifact_binding(args)
    with tempfile.TemporaryDirectory(prefix='gltf-native-migrate-',dir=args.tmp_dir) as td:
        moved=Path(td)/'移行 portable source with spaces';moved.mkdir()
        p=moved/'native prefix';shutil.copytree(args.prefix,p,symlinks=True)
        if metadata.prefix_inventory(p)!=metadata.prefix_inventory(args.prefix):raise ValueError('Signed migrated prefix bytes/controlled links differ')
        consumer=moved/'new consumer source';shutil.copytree(HERE/'consumers',consumer)
        tests=[consume(args,runner,preset,moved/('fresh '+mode+' build'),p,mode,'moved-'+mode,consumer_source=consumer,
                       forbidden=[str(args.prefix),str(args.root/'sources'),str(args.root/'build')]) for mode in ['CMAKE','PC']]
        bridge=bridge_acceptance(args,runner,p,'moved-'+runner.logs.name)
        dlls=[audit.elf(args,runner,p/'lib'/n,'moved-'+n) for n in metadata.BRIDGES]
    result={'result':'PASS actual Unicode/space moved fresh CMAKE/PC source consumers and bridge DLL gates',
            'consumers':tests,'bridge':bridge,'DLLs':dlls,'temporary_tree_cleaned':True,
            'prefix_seal_sha256':sha(args.root/'prefix-seal.json'),'bpy':'NOTRUN','HAP':'NOTRUN'}
    write_json(args.root/'migration.json',result);return result


def copy_inputs(args):
    if args.destination.exists():raise ValueError('Copied repository input root must be absent')
    lock=json.loads((HERE/'inputs.lock.json').read_text())
    rows=lock['sealed_files']+[{'path':(HERE/'inputs.lock.json').relative_to(REPO).as_posix()}]
    args.destination.mkdir(parents=True)
    for row in rows:
        target=args.destination/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(repo_file(row['path']),target)
    value={'result':'PASS exact repository-relative copied inputs','files':len(rows),'destination':str(args.destination),
           'input_lock_sha256':sha(HERE/'inputs.lock.json'),'native_full':'NOTRUN'}
    write_json(args.destination/'.gltf-source-input-copy.json',value);return value


def pipeline(args,runner):
    dependent={'full':['full-native-acceptance.json','acceptance.json','artifacts.json','migration.json'],
               'acceptance':['full-native-acceptance.json','acceptance.json','artifacts.json','migration.json'],
               'audit':['full-native-acceptance.json','artifacts.json','migration.json'],
               'migrate':['full-native-acceptance.json','migration.json']}
    invalidate(args,runner,dependent.get(args.stage,[]))
    preflight=toolchain.preflight(args,runner)
    seal=args.root/'prerequisites.json'
    if seal.exists() and json.loads(seal.read_text())!=preflight:raise ValueError('Explicit prerequisite bytes/version changed')
    write_json(seal,preflight)
    preset=toolchain.generate(args);commands=command_plan(args,preset)
    plan={'status':'CANDIDATE NOTRUN native full/bpy/HAP','commands':commands,'input_lock_sha256':sha(HERE/'inputs.lock.json'),
          'prerequisites':preflight,'feature_matrix':json.loads((HERE/'feature-matrix.json').read_text()),
          'schedule':{'jobs':args.jobs,'link_pool':1,'lld_threads':2},'independent_root':str(args.root)}
    write_json(args.root/'plan.json',plan)
    if args.stage=='plan':return plan
    if args.stage=='full':
        if (args.root/'prefix-seal.json').exists():raise ValueError('Full rebuild must use a new independent root')
        prepare(args,runner)
        for row in commands:
            for step in ['configure','build','install']:
                runner.run(row[step],row['name']+'-'+step,timeout=None)
                if step=='configure':guard_cache(args,args.root/'build'/row['name'],args.prefix)
        metadata.install(args);metadata.freeze(args)
    if args.stage in ['full','acceptance']:acceptance(args,runner,preset,label='original' if args.stage=='full' else runner.logs.name)
    if args.stage in ['full','audit']:audit.artifacts(args,runner)
    if args.stage in ['full','migrate']:
        if not (args.root/'artifacts.json').is_file():raise ValueError('Actual artifact audit required before migration')
        migrate(args,runner,preset)
    if args.stage=='full':
        value={'result':'PASS actual independent source-built native full and moved pipeline',
               'input_lock_sha256':sha(HERE/'inputs.lock.json'),'prefix_seal_sha256':sha(args.root/'prefix-seal.json'),
               'receipts':{n:sha(args.root/n) for n in ['acceptance.json','artifacts.json','migration.json']},
               'bpy':'NOTRUN separate real Blender parent postlink acceptance required','HAP':'NOTRUN'}
        write_json(args.root/'full-native-acceptance.json',value);return value
    return {'result':'Completed actual '+args.stage,'bpy':'NOTRUN','HAP':'NOTRUN'}


def main():
    args=arguments();verified=verify_inputs()
    if args.stage=='verify-inputs':result=verified
    elif args.stage=='copy-inputs':result=copy_inputs(args)
    else:
        with ownership(args):
            runner=Runner(args.root,args.tmp_dir)
            result=prepare(args,runner) if args.stage=='prepare' else pipeline(args,runner)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
