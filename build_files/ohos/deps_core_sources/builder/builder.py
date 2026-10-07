#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parameterized repository-owned offline core builder candidate.

Short stages never configure/compile. Full stage is parent-owned and remains
unaccepted until its new-root signed native consumers and migration succeed.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
from io_utils import HERE, BASE, REPO, Runner, check_case_sensitive, private_path, sha, verify_inputs, write_json
from source_tree import prepare_sources
import toolchain
import audit

ORDER = ['eigen','abseil','gflags','glog','embree','ceres']
STAGES = ['verify-inputs','prepare-sources','plan','full','acceptance','audit','migrate']


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',nargs='?',choices=STAGES)
    parser.add_argument('--verify-inputs',action='store_true')
    parser.add_argument('--root',type=Path,help='Caller-owned new private source/build/log/toolchain workspace')
    parser.add_argument('--prefix',type=Path,help='Caller-owned new private install prefix; default ROOT/prefix')
    parser.add_argument('--tmp-dir',type=Path,help='Caller-selected native directory under TMPDIR')
    parser.add_argument('--sdk-root',type=Path)
    parser.add_argument('--cc',type=Path,help='Real native Clang20 C driver, not a developer signing wrapper')
    parser.add_argument('--cxx',type=Path,help='Real native Clang20 C++ driver; SDK15 __n1 flags supplied explicitly')
    parser.add_argument('--lld',type=Path)
    parser.add_argument('--resource-dir',type=Path,help='Declared Clang20 builtin headers/resource directory')
    parser.add_argument('--signer',type=Path,help='Declared executable binary-sign-tool')
    parser.add_argument('--tbb-prefix',type=Path,help='Explicit accepted static native TBB2022.3 prefix, read-only')
    parser.add_argument('--cmake',type=Path,default=shutil.which('cmake'))
    parser.add_argument('--ninja',type=Path,default=shutil.which('ninja'))
    parser.add_argument('--resume',action='store_true',help='Reuse only this builder owned root with identical sealed/caller inputs')
    args = parser.parse_args()
    if args.verify_inputs and args.stage not in (None,'verify-inputs'):
        parser.error('Use verify-inputs or a stage')
    args.stage = 'verify-inputs' if args.verify_inputs else (args.stage or 'verify-inputs')
    if args.stage == 'verify-inputs':
        return args
    if args.root is None or args.tmp_dir is None:
        parser.error('All output stages require explicit --root and --tmp-dir')
    args.root = private_path(args.root)
    args.tmp_dir = private_path(args.tmp_dir,temporary=True)
    args.prefix = private_path(args.prefix or args.root / 'prefix')
    if args.stage != 'prepare-sources':
        for name in ['sdk_root','cc','cxx','lld','resource_dir','signer','tbb_prefix','cmake','ninja']:
            value = getattr(args,name)
            if value is None:
                parser.error('Native plan/full/consumer stages require explicit --' + name.replace('_','-'))
            # Executable basename can select a real multicall driver (ld.lld/clang++).
            path = Path(value).absolute() if name in ['cc','cxx','lld','signer','cmake','ninja'] else Path(value).resolve()
            setattr(args,name,path)
        if args.tbb_prefix == args.prefix or args.tbb_prefix in args.prefix.parents or args.prefix in args.tbb_prefix.parents:
            parser.error('Caller-owned output prefix must be disjoint from read-only TBB prerequisite')
    return args


def own_workspace(args):
    root = args.root
    marker = root / '.core-builder-owned.json'
    expected = {'schema_version':1,'sealed_input_lock_sha256':sha(HERE / 'inputs.lock.json'),
                'prefix':str(args.prefix),'tmp_dir':str(args.tmp_dir)}
    if root.exists():
        if not args.resume or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse existing/unowned/mismatched root; select new private root or identical --resume')
    else:
        if args.resume:
            raise ValueError('--resume requires a previously owned root')
        if args.prefix.exists():
            raise ValueError('Refuse existing caller install prefix for a new root')
        root.mkdir(parents=True)
        write_json(marker,expected)
    if root.stat().st_dev != args.tmp_dir.stat().st_dev:
        raise ValueError('Root and TMPDIR must be on the same native private filesystem')
    lock = (root / '.core-builder-lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock


def common_flags(args, root, preset, prefix=None, tbb=None):
    prefix = prefix or args.prefix
    tbb = tbb or args.tbb_prefix
    values = {
        'CMAKE_TOOLCHAIN_FILE':str(preset),
        'CMAKE_PROJECT_TOP_LEVEL_INCLUDES':str(HERE / 'cmake/native-host.cmake'),
        'CMAKE_PROJECT_INCLUDE':str(HERE / 'cmake/native-link-features.cmake'),
        'CMAKE_MAKE_PROGRAM':str(args.ninja),
        'CMAKE_INSTALL_PREFIX':str(prefix),'CMAKE_INSTALL_LIBDIR':'lib','CMAKE_BUILD_TYPE':'Release',
        'CMAKE_CXX_STANDARD':'20','CMAKE_CXX_STANDARD_REQUIRED':'ON','CMAKE_POSITION_INDEPENDENT_CODE':'ON',
        'CMAKE_EXPORT_COMPILE_COMMANDS':'ON','CMAKE_JOB_POOLS':'compile=2;link=1',
        'CMAKE_JOB_POOL_COMPILE':'compile','CMAKE_JOB_POOL_LINK':'link',
        'CMAKE_FIND_USE_PACKAGE_REGISTRY':'OFF','CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY':'OFF',
        'FETCHCONTENT_FULLY_DISCONNECTED':'ON','CMAKE_PREFIX_PATH':str(prefix) + ';' + str(tbb),
        'TBB_DIR':str(tbb / 'lib/cmake/TBB'),'CORE_DECLARED_TBB_PREFIX':str(tbb)}
    return ['-G','Ninja'] + ['-D' + name + '=' + value for name,value in values.items()]


def component_flags(name, args):
    # Exact relevant original developer choices, now bound only to explicit caller variables.
    recipe = json.loads((BASE / 'recipe.candidate.json').read_text())['candidate_component_cmake'][name]
    variables = {'PRIVATE_PREFIX':str(args.prefix),'DECLARED_TBB_PREFIX':str(args.tbb_prefix)}
    result = []
    for key,value in recipe.items():
        for token,replacement in variables.items():
            value = value.replace('${' + token + '}',replacement)
        if '${' in value:
            raise ValueError('Unresolved component configuration variable')
        result.append('-D' + key + '=' + value)
    return result


def consumer(args, root, runner, preset, build, prefix, tbb, label, forbidden=None):
    flags = common_flags(args,root,preset,prefix,tbb) + [
        '-DEigen3_DIR=' + str(prefix / 'share/eigen3/cmake'),
        '-Dabsl_DIR=' + str(prefix / 'lib/cmake/absl'),
        '-DCeres_DIR=' + str(prefix / 'lib/cmake/Ceres'),
        '-Dgflags_DIR=' + str(prefix / 'lib/cmake/gflags'),
        '-DCORE_WITH_EMBREE=ON','-DCORE_WITH_CERES=ON','-DCORE_WITH_LOGGING=ON']
    runner.run([args.cmake,'-S',HERE / 'consumers','-B',build] + flags,label + '-configure')
    for line in (build / 'build.ninja').read_text().splitlines():
        if line.lstrip().startswith(('LINK_LIBRARIES =','INCLUDES =')) and any(p in line for p in (forbidden or [])):
            raise ValueError('Moved consumer still resolves original prefix inputs')
    runner.run([args.cmake,'--build',build,'--parallel','2'],label + '-build')
    runner.run(['ctest','--test-dir',build,'--output-on-failure','-V'],label + '-tests')


def pipeline(args, root, runner):
    prerequisite = toolchain.preflight(args,runner)
    current = root / 'prerequisites.json'
    if current.exists() and json.loads(current.read_text()) != prerequisite:
        raise ValueError('Declared SDK/compiler/TBB bytes changed since this owned root was prepared')
    write_json(current,prerequisite)
    preset = toolchain.generate(args,root)
    common = common_flags(args,root,preset)
    command_plan = []
    for name in ORDER:
        build = root / 'build' / name
        command_plan.append({'name':name,'configure':[str(args.cmake),'-S',str(root / 'sources' / name),'-B',str(build)] + common + component_flags(name,args),
                             'build':[str(args.cmake),'--build',str(build),'--parallel','2'],
                             'install':[str(args.cmake),'--install',str(build)]})
    plan = {'status':'candidate; no new full-run acceptance yet','commands':command_plan,
            'input_lock_sha256':sha(HERE / 'inputs.lock.json'),'sdk_prerequisite_scope':prerequisite['scope'],
            'scheduling':{'nice':os.getpriority(os.PRIO_PROCESS,0),'compile_parallel':2,'link_pool':1,'lld_threads':2},
            'source_routing':'Repository relative inputs and explicit caller paths; no developer work imports',
            'new_builder_accepted':False}
    write_json(root / 'plan.json',plan)
    if args.stage == 'plan':
        return plan
    if args.stage == 'full':
        prepared = prepare_sources(root,runner)
        probe_build = root / 'build/prerequisite-acceptance'
        runner.run([args.cmake,'-S',HERE / 'preflight','-B',probe_build] + common,'prerequisite-configure')
        runner.run([args.cmake,'--build',probe_build,'--parallel','2'],'prerequisite-build')
        runner.run(['ctest','--test-dir',probe_build,'--output-on-failure','-V'],'prerequisite-native-tests')
        for entry in command_plan:
            runner.run(entry['configure'],entry['name'] + '-configure')
            runner.run(entry['build'],entry['name'] + '-build')
            runner.run(entry['install'],entry['name'] + '-install')
            audit.normalize_metadata(args.prefix,root)
            audit.install_notices(args.prefix)
    consume = lambda build,prefix,tbb,label,forbidden=None: consumer(args,root,runner,preset,build,prefix,tbb,label,forbidden)
    if args.stage in ['full','acceptance']:
        consume(root / 'build/acceptance',args.prefix,args.tbb_prefix,'acceptance')
        write_json(root / 'acceptance.json',{'result':'PASS actual six core native tests; all core consumers enabled',
                                           'logs':str(runner.logs / 'acceptance-tests.log')})
    if args.stage in ['full','audit']:
        if not (root / 'acceptance.json').is_file():
            raise ValueError('Actual core acceptance required before artifact audit')
        audit.artifacts(args,root,runner)
    if args.stage in ['full','migrate']:
        if not (root / 'artifacts.json').is_file():
            raise ValueError('Actual artifact audit required before migration')
        audit.migrate(args,root,runner,consume)
    if args.stage == 'full':
        accepted = {'result':'PASS new-root complete core native dependency pipeline and moved-prefix consumers',
                    'new_builder_accepted_on_this_host':True,'input_lock_sha256':sha(HERE / 'inputs.lock.json'),
                    'scope':'Six core libraries with explicit already-built/pinned TBB prerequisite. Blender render/libmv UI/HAP/release not included.',
                    'receipts':[str(root / name) for name in ['sources.json','prerequisites.json','acceptance.json','artifacts.json','migration.json']]}
        write_json(root / 'full-native-acceptance.json',accepted)
        return accepted
    return {'stage':args.stage,'completed':True,'new_full_builder_acceptance_claim':False}


def main():
    args = arguments()
    verification = verify_inputs()
    if args.stage == 'verify-inputs':
        print(json.dumps(verification,indent=2))
        return
    check_case_sensitive(args.tmp_dir)
    os.environ['TMPDIR'] = str(args.tmp_dir)
    guard = own_workspace(args)
    try:
        os.setpriority(os.PRIO_PROCESS,0,10)
        runner = Runner(args.root,args.tmp_dir)
        if args.stage == 'prepare-sources':
            result = prepare_sources(args.root,runner)
        else:
            result = pipeline(args,args.root,runner)
        print(json.dumps(result,indent=2))
    finally:
        guard.close()


if __name__ == '__main__':
    main()
