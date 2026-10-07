#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable offline geometry pipeline. Full native actions belong to the parent."""
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
import source_tree
import toolchain
import metadata
import audit
import link_plan

ORDER = ['tbb', 'gmp', 'opensubdiv', 'manifold']
STAGES = ['verify-inputs', 'materialize', 'prepare', 'plan', 'full', 'acceptance', 'audit', 'migrate']
TOOLS = ['sdk_root', 'cc', 'cxx', 'lld', 'resource_dir', 'signer', 'python', 'cmake', 'ninja',
         'git', 'pkgconf', 'ctest', 'make', 'shell', 'shader_prefix', 'shader_receipt']


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    for name in ['root', 'prefix', 'tmp_dir', *TOOLS]:
        parser.add_argument('--' + name.replace('_', '-'), type=Path)
    for name in ['make_sha256', 'shader_receipt_sha256']:
        parser.add_argument('--' + name.replace('_', '-'))
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--jobs', type=int, choices=[1, 2], default=2)
    parser.add_argument('--runtime-timeout', type=int, default=180)
    args = parser.parse_args()
    if args.runtime_timeout < 1:
        parser.error('Runtime timeout must be positive')
    if args.stage == 'verify-inputs':
        return args
    if args.root is None or args.tmp_dir is None:
        parser.error('Output stages require --root and --tmp-dir')
    args.root = private_path(args.root)
    args.prefix = private_path(args.prefix or args.root / 'prefix')
    args.tmp_dir = private_path(args.tmp_dir, temporary=True)
    if args.root == args.prefix or args.root.is_relative_to(args.prefix):
        parser.error('Install prefix must not contain the workspace root')
    required = ['git'] if args.stage == 'prepare' else (TOOLS if args.stage != 'materialize' else [])
    for name in required:
        value = getattr(args, name)
        if value is None:
            parser.error('Stage requires --' + name.replace('_', '-'))
        value = value.resolve() if name in ['sdk_root', 'resource_dir', 'shader_prefix'] else value.absolute()
        if any(c in str(value) for c in ('\n', '\r', ';', '$', '"', '\\')):
            parser.error('Unsupported path interpolation character')
        setattr(args, name, value)
    if args.stage not in ['materialize', 'prepare']:
        for name in ['make_sha256', 'shader_receipt_sha256']:
            if not re.fullmatch('[0-9a-f]{64}', getattr(args, name) or ''):
                parser.error('Explicit fixed SHA-256 required: --' + name.replace('_', '-'))
        if args.shader_prefix == args.prefix or args.shader_prefix.is_relative_to(args.root) or args.root.is_relative_to(args.shader_prefix):
            parser.error('Accepted shader prerequisite must be separate from new owned geometry root/prefix')
    return args


def own_workspace(args):
    marker = args.root / '.geometry-builder-owned.json'
    expected = {'schema_version': 1, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'),
                'prefix': str(args.prefix), 'tmp_dir': str(args.tmp_dir)}
    if args.root.exists():
        if not args.resume or not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse existing/unowned/mismatched root; exact owned --resume required')
    else:
        if args.resume or args.prefix.exists():
            raise ValueError('New root and install prefix must be absent')
        args.root.mkdir(parents=True)
        write_json(marker, expected)
    if args.root.stat().st_dev != args.tmp_dir.stat().st_dev:
        raise ValueError('Owned root and TMPDIR must share native private filesystem')
    lock = args.root / '.geometry-builder-lock'
    if lock.is_symlink():
        raise ValueError('Unsafe ownership lock symlink')
    guard = lock.open('a+')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return guard


def common_flags(args, preset, prefix=None):
    prefix = prefix or args.prefix
    values = {'CMAKE_TOOLCHAIN_FILE': str(preset), 'CMAKE_PROJECT_INCLUDE': str(HERE / 'cmake/native-host.cmake'),
              'CMAKE_MAKE_PROGRAM': str(args.ninja), 'PKG_CONFIG_EXECUTABLE': str(args.pkgconf),
              'Python3_EXECUTABLE': str(args.python), 'CMAKE_INSTALL_PREFIX': str(prefix),
              'CMAKE_PREFIX_PATH': str(prefix), 'CMAKE_INSTALL_LIBDIR': 'lib', 'CMAKE_BUILD_TYPE': 'Release',
              'CMAKE_C_STANDARD': '17', 'CMAKE_CXX_STANDARD': '20', 'CMAKE_CXX_STANDARD_REQUIRED': 'ON',
              'CMAKE_POSITION_INDEPENDENT_CODE': 'ON', 'CMAKE_CXX_SCAN_FOR_MODULES': 'OFF',
              'BUILD_SHARED_LIBS': 'OFF', 'CMAKE_SKIP_RPATH': 'ON', 'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON',
              'CMAKE_JOB_POOLS': 'compile=' + str(args.jobs) + ';link=1', 'CMAKE_JOB_POOL_COMPILE': 'compile',
              'CMAKE_JOB_POOL_LINK': 'link', 'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF',
              'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF', 'CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH': 'OFF',
              'CMAKE_FIND_USE_CMAKE_SYSTEM_PATH': 'OFF', 'FETCHCONTENT_FULLY_DISCONNECTED': 'ON',
              'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON', 'CMAKE_C_FLAGS': '-D__MUSL__', 'CMAKE_CXX_FLAGS': '-D__MUSL__'}
    return ['-G', 'Ninja'] + ['-D' + key + '=' + value for key, value in values.items()]


def component_flags(name, args):
    recipe = json.loads((HERE / 'recipe.json').read_text())['component_cmake']
    values = dict(recipe[name])
    for key, value in values.items():
        values[key] = value.replace('${PRIVATE_PREFIX}', str(args.prefix))
        if '${' in values[key]:
            raise ValueError('Unresolved component option')
    return ['-D' + key + '=' + value for key, value in values.items()]


def gmp_environment(args):
    tool = args.sdk_root / 'llvm/bin'
    return {'CONFIG_SHELL': str(args.shell), 'SHELL': str(args.shell), 'MAKESHELL': str(args.shell),
            'MAKE': str(args.make), 'CC': str(args.root / 'toolchain/clang20-c'),
            'CXX': str(args.root / 'toolchain/clang20-cxx'), 'CC_FOR_BUILD': str(args.root / 'toolchain/clang20-c'),
            'CFLAGS': '-O2 -fPIC -D__MUSL__', 'CFLAGS_FOR_BUILD': '-O2 -fPIC -D__MUSL__',
            'CXXFLAGS': '-std=c++20 -O2 -fPIC -D__MUSL__', 'LDFLAGS': '-Wl,--threads=2',
            'LD': str(args.lld), 'AR': str(tool / 'llvm-ar'), 'RANLIB': str(tool / 'llvm-ranlib'),
            'NM': str(tool / 'llvm-nm'), 'PKG_CONFIG': str(args.pkgconf),
            'PKG_CONFIG_LIBDIR': str(args.prefix / 'lib/pkgconfig')}


def command_plan(args, preset):
    rows = []
    for name in ORDER:
        source = args.root / 'sources' / name
        build = args.root / 'build' / name
        if name == 'gmp':
            rows.append({'name': name, 'cwd': str(build), 'environment': gmp_environment(args),
                'configure': [str(args.shell), str(source / 'configure'), '--build=aarch64-unknown-ohos',
                    '--prefix=' + str(args.prefix), '--libdir=' + str(args.prefix / 'lib'),
                    '--enable-static', '--disable-shared', '--enable-cxx', '--with-pic', '--disable-assembly',
                    '--disable-maintainer-mode'],
                'build': [str(args.make), '-j' + str(args.jobs), 'SHELL=' + str(args.shell)],
                'upstream_check': [str(args.make), '-j' + str(args.jobs), 'SHELL=' + str(args.shell), 'check'],
                'install': [str(args.make), '-j1', 'SHELL=' + str(args.shell), 'install']})
        else:
            rows.append({'name': name, 'configure': [str(args.cmake), '-S', str(source), '-B', str(build),
                    *common_flags(args, preset), *component_flags(name, args)],
                'build': [str(args.cmake), '--build', str(build), '--parallel', str(args.jobs)],
                'install': [str(args.cmake), '--install', str(build)]})
    return rows


def guard_dependencies(args, build, prefix, shader=False):
    allowed = [args.root.resolve(), prefix.resolve(), args.sdk_root.resolve()]
    if shader:
        allowed.append(args.shader_prefix.resolve())
    cache = (build / 'CMakeCache.txt').read_text()
    for line in cache.splitlines():
        if line.startswith(('#', '//')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if 'LIBRAR' not in key.upper():
            continue
        for token in value.split(';'):
            if token.startswith('/') and re.search(r'\.(a|so(?:\.\d+)*)$', token):
                file = Path(token).resolve()
                if not any(file.is_relative_to(p) for p in allowed):
                    raise ValueError('Dependency outside own sources/prefix/declared SDK: ' + line)


def gmp_config_receipt(args, root):
    build = root / 'build/gmp'
    log = (build / 'config.log').read_text()
    if not re.search(r'checking whether we are cross compiling[\s\S]{0,2000}?result: no', log):
        raise ValueError('Actual GMP executed native configure probes must show cross=no')
    if not (build / 'config.h').is_file() or not (build / 'gmp.h').is_file():
        raise ValueError('Actual GMP generated public/probe header missing')
    write_json(root / 'gmp-native-config.json', {'native_configure_probes_executed': True,
        'config_log_sha256': sha(build / 'config.log'), 'config_h_sha256': sha(build / 'config.h'),
        'gmp_h_sha256': sha(build / 'gmp.h'), 'build_triplet': 'aarch64-unknown-ohos',
        'cross': 'no from executed configure output', 'assembly': 'disabled existing accepted generic ARM64 profile'})


def gmp_upstream_receipt(args, root):
    summaries = []
    totals = {key: 0 for key in ['TOTAL', 'PASS', 'SKIP', 'XFAIL', 'FAIL', 'XPASS', 'ERROR']}
    for file in sorted((root / 'build/gmp').rglob('test-suite.log')):
        text = file.read_text()
        values = {key: int(value) for key, value in re.findall(r'^#\s*(TOTAL|PASS|SKIP|XFAIL|FAIL|XPASS|ERROR):\s*(\d+)', text, re.M)}
        if values:
            for key, value in values.items():
                totals[key] += value
            summaries.append({'path': str(file), 'sha256': sha(file), 'summary': values})
    if totals['PASS'] < 150 or any(totals[k] for k in ['FAIL', 'XPASS', 'ERROR']) or totals['SKIP'] > 1:
        raise ValueError('Actual complete GMP upstream check summary is missing or failed')
    if totals['SKIP']:
        trs = list((root / 'build/gmp').rglob('*.trs'))
        skipped = [file for file in trs if ':test-result: SKIP' in file.read_text()]
        if len(skipped) != 1 or skipped[0].stem != 't-addaddmul':
            raise ValueError('Unexpected GMP skip beyond generic assembly-disabled source profile')
    result = {'result': 'PASS actual source-built GMP upstream check', 'summary': totals,
              'logs': summaries, 'only_possible_skip': 't-addaddmul native assembly-specific path'}
    write_json(root / 'gmp-upstream-tests.json', result)
    return result


def consumers(args, runner, preset, build, prefix, mode, label, forbidden=None):
    runner.env['PKG_CONFIG_LIBDIR'] = str(prefix / 'lib/pkgconfig')
    flags = common_flags(args, preset, prefix) + ['-DGEOMETRY_PREFIX=' + str(prefix),
            '-DGEOMETRY_CONSUMER=' + mode, '-DGEOMETRY_SHADER_PREFIX=' + str(args.shader_prefix)]
    runner.run([args.cmake, '-S', HERE / 'consumers', '-B', build, *flags], label + '-configure')
    guard_dependencies(args, build, prefix, shader=True)
    runner.run([args.cmake, '--build', build, '--parallel', str(args.jobs)], label + '-build')
    commands = runner.run([args.ninja, '-C', build, '-t', 'commands'], label + '-actual-built-link-plan', raw_bytes=True)
    if any(old.encode('utf-8') in commands for old in forbidden or []):
        raise ValueError('Moved consumer retained an original geometry source/build/prefix route')
    parsed_link = link_plan.inspect(commands, build, [args.sdk_root / 'llvm/lib/aarch64-linux-ohos'])
    linked_archives = {Path(file) for file in parsed_link['archives']}
    expected_archives = {(prefix / 'lib' / leaf).resolve() for leaves in metadata.ARCHIVES.values() for leaf in leaves}
    expected_archives.add((args.shader_prefix / 'lib/libshaderc_combined.a').resolve())
    if not expected_archives.issubset(linked_archives):
        raise ValueError('Actual consumer link misses canonical selected archives: ' + str(expected_archives - linked_archives))
    for file in linked_archives - expected_archives:
        if not file.is_relative_to(args.sdk_root.resolve()) and not file.is_relative_to(args.resource_dir.resolve()):
            raise ValueError('Actual link retained foreign/same-basename archive: ' + str(file))
    leaves = ['tbb_acceptance', 'opensubdiv_acceptance', 'gmp_acceptance', 'manifold_acceptance', 'glsl_acceptance', 'libgeometry_pic.so']
    artifacts = [audit.elf(args, runner, build / leaf, label + '-' + leaf) for leaf in leaves]
    outputs = {}
    with tempfile.TemporaryDirectory(prefix='geometry-native-fixtures-', dir=runner.tmp) as td:
        fixtures = Path(td) / '几何 geometry fixtures with spaces'
        fixtures.mkdir()
        for leaf in leaves[:-1]:
            command = [build / leaf, fixtures] if leaf in ['glsl_acceptance', 'manifold_acceptance'] else [build / leaf]
            output = runner.run(command, label + '-' + leaf + '-run', timeout=args.runtime_timeout)
            if 'PASS' not in output:
                raise ValueError('Actual runtime sentinel absent: ' + leaf)
            outputs[leaf] = output
        if 'ALL PASS GLSL' not in outputs['glsl_acceptance']:
            raise ValueError('Actual GLSL compile/error/validator sentinel absent')
        produced = audit.file_inventory(fixtures)
    return {'mode': mode, 'artifacts': artifacts, 'outputs': outputs, 'fixtures': produced, 'actual_link_bindings': parsed_link,
            'temporary_tree_cleaned': True, 'pic_module_dlopened': False}


def pipeline(args, runner):
    prerequisites = toolchain.preflight(args, runner)
    receipt = args.root / 'prerequisites.json'
    if receipt.exists() and json.loads(receipt.read_text()) != prerequisites:
        raise ValueError('Explicit SDK/tool/accepted-shader bytes changed since owned plan')
    write_json(receipt, prerequisites)
    preset = toolchain.generate(args, args.root)
    rows = command_plan(args, preset)
    plan = {'status': 'Portable complete candidate; actual native fresh pipeline NOTRUN',
            'commands': rows, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'),
            'source_closure': ORDER, 'new_full_native_acceptance': False,
            'declared_shader': prerequisites['shader_prerequisite'],
            'scheduling': {'nice': 10, 'jobs': args.jobs, 'link_pool': 1, 'lld_threads': 2}}
    write_json(args.root / 'plan.json', plan)
    if args.stage == 'plan':
        return plan
    if args.stage == 'full':
        history = args.root / 'receipt-history' / runner.logs.name
        for leaf in ['full-native-acceptance.json', 'acceptance.json', 'artifacts.json', 'migration.json']:
            file = args.root / leaf
            if file.exists():
                history.mkdir(parents=True, exist_ok=True)
                file.rename(history / leaf)
        source_tree.prepare(args.root, runner, args.git)
        probe = args.root / 'build/prerequisites'
        runner.run([args.cmake, '-S', HERE / 'preflight', '-B', probe, *common_flags(args, preset)], 'actual-prerequisite-configure')
        runner.run([args.cmake, '--build', probe, '--parallel', str(args.jobs)], 'actual-prerequisite-build')
        runner.run([args.ctest, '--test-dir', probe, '--output-on-failure', '-V'], 'actual-prerequisite-runtime')
        for row in rows:
            name = row['name']
            source_tree.verify_tree(args.root / 'sources' / name, name, patched=True)
            runner.env['PKG_CONFIG_LIBDIR'] = str(args.prefix / 'lib/pkgconfig')
            build = args.root / 'build' / name
            if name == 'gmp':
                build.mkdir(parents=True, exist_ok=True)
                runner.env.update(row['environment'])
                # Only source timestamp normalization for distributed generated Makefile.in;
                # no generated configuration/header/probe bytes are borrowed.
                src = args.root / 'sources/gmp'
                timestamp_files = ['Makefile.am', 'Makefile.in']
                before_times = {leaf: (src / leaf).stat().st_mtime_ns for leaf in timestamp_files}
                before_shas = {leaf: sha(src / leaf) for leaf in timestamp_files}
                template_sha = sha(src / 'Makefile.in')
                newer = max((src / 'Makefile.am').stat().st_mtime, (src / 'Makefile.in').stat().st_mtime) + 1
                os.utime(src / 'Makefile.in', (newer, newer))
                write_json(args.root / 'gmp-generated-source-timestamp.json', {'before_mtime_ns': before_times,
                    'files': [{'path': leaf, 'original_mtime_ns': before_times[leaf],
                        'derived_mtime_ns': (src / leaf).stat().st_mtime_ns,
                        'original_patched_source_sha256': before_shas[leaf], 'derived_sha256': sha(src / leaf)} for leaf in timestamp_files],
                    'after_Makefile_in_mtime_ns': (src / 'Makefile.in').stat().st_mtime_ns,
                    'original_patched_template_sha256': template_sha, 'derived_sha256': sha(src / 'Makefile.in'),
                    'reason': 'Distributed generated source template newer than patched .am; no unstated automake bootstrap',
                    'ELF_not_mutated': True})
                for stage in ['configure', 'build', 'upstream_check', 'install']:
                    runner.run(row[stage], name + '-' + stage, cwd=build)
                    if stage == 'configure':
                        gmp_config_receipt(args, args.root)
                    elif stage == 'upstream_check':
                        gmp_upstream_receipt(args, args.root)
                for key in row['environment']:
                    runner.env.pop(key, None)
            else:
                for stage in ['configure', 'build', 'install']:
                    runner.run(row[stage], name + '-' + stage)
                    if stage == 'configure':
                        guard_dependencies(args, build, args.prefix)
            metadata.install(name, args, args.root)
            audit.pic_closure.capture(args, args.root, name, runner.logs)
        audit.freeze_prefix(args, args.root)
    consume = lambda build, prefix, mode, label, forbidden=None: consumers(args, runner, preset, build, prefix, mode, label, forbidden)
    if args.stage in ['full', 'acceptance']:
        audit.verify_prefix(args, args.root)
        tests = [consume(args.root / 'build' / ('acceptance-' + mode), args.prefix, mode, 'original-' + mode) for mode in ['CMAKE', 'PC']]
        write_json(args.root / 'acceptance.json', {'result': 'PASS actual source-built geometry native CMAKE/PC consumers',
                   'consumers': tests, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'), 'pic_module_dlopened': False})
    if args.stage in ['full', 'audit']:
        if not (args.root / 'acceptance.json').is_file():
            raise ValueError('Current actual native acceptance required before artifact audit')
        audit.artifacts(args, args.root, runner)
    if args.stage in ['full', 'migrate']:
        if not (args.root / 'artifacts.json').is_file():
            raise ValueError('Current actual artifact audit required before migration')
        audit.migrate(args, args.root, runner, consume)
    if args.stage == 'full':
        result = {'result': 'PASS new complete source-built geometry and moved Unicode/space CMAKE/PC native consumers',
                  'new_full_native_acceptance': True, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'),
                  'scope': 'Own source TBB2022.3; OpenSubdiv CPU/TBB+real GLSL compilation; GMP native multiprecision and upstream tests; manifold Boolean/topology/mesh export',
                  'external_prerequisites': prerequisites,
                  'excluded': ['GPU dispatch', 'CrossSection/Assimp export/bindings', 'GMP ARM assembly performance path',
                               'PIC dlopen/static C++ runtime ownership', 'Blender modifiers', 'installed HAP', 'release']}
        write_json(args.root / 'full-native-acceptance.json', result)
        return result
    return {'stage': args.stage, 'completed': True, 'new_full_native_acceptance': False}


def main():
    args = arguments()
    verified = verify_inputs()
    if args.stage == 'verify-inputs':
        print(json.dumps(verified, indent=2))
        return
    check_case_sensitive(args.tmp_dir)
    os.environ['TMPDIR'] = str(args.tmp_dir)
    guard = own_workspace(args)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 10)
        runner = Runner(args.root, args.tmp_dir)
        if args.stage == 'materialize':
            result = source_tree.materialize(args.root, runner)
        elif args.stage == 'prepare':
            result = source_tree.prepare(args.root, runner, args.git)
        else:
            result = pipeline(args, runner)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        guard.close()


if __name__ == '__main__':
    main()
