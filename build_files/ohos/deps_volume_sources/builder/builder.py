#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repository-owned portable offline volume builder; full is a parent-owned action."""
import sys
sys.dont_write_bytecode = True
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile
import re
from io_utils import HERE, Runner, check_case_sensitive, private_path, sha, verify_inputs, write_json
import source_tree
import toolchain
import metadata
import audit

ORDER = ['zlib', 'imath', 'tbb', 'blosc', 'fftw-double', 'fftw-float', 'openvdb']
STAGES = ['verify-inputs', 'materialize', 'prepare', 'plan', 'full', 'acceptance', 'audit', 'migrate']
TOOLS = ['sdk_root', 'cc', 'cxx', 'lld', 'resource_dir', 'signer', 'python', 'cmake', 'ninja', 'git', 'pkgconf', 'ctest']


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    for name in ['root', 'prefix', 'tmp_dir', *TOOLS]:
        parser.add_argument('--' + name.replace('_', '-'), type=Path)
    parser.add_argument('--resume', action='store_true', help='Only exact owned immutable root may resume')
    parser.add_argument('--jobs', type=int, choices=[1, 2], default=2)
    parser.add_argument('--runtime-timeout', type=int, default=180, help='Seconds for each actual consumer; never fake a timeout PASS')
    args = parser.parse_args()
    if args.runtime_timeout < 1:
        parser.error('Runtime timeout must be positive')
    if args.stage == 'verify-inputs':
        return args
    if args.root is None or args.tmp_dir is None:
        parser.error('Output stages require explicit --root and --tmp-dir')
    args.root = private_path(args.root)
    args.prefix = private_path(args.prefix or args.root / 'prefix')
    args.tmp_dir = private_path(args.tmp_dir, temporary=True)
    if args.root == args.prefix or args.root.is_relative_to(args.prefix):
        parser.error('Install prefix must not contain workspace root')
    required = ['git'] if args.stage == 'prepare' else (TOOLS if args.stage not in ['materialize'] else [])
    for name in required:
        value = getattr(args, name)
        if value is None:
            parser.error('Stage requires explicit --' + name.replace('_', '-'))
        # Preserve ld.lld/clang++ multicall invocation names.
        value = value.resolve() if name in ['sdk_root', 'resource_dir'] else value.absolute()
        if any(c in str(value) for c in ('\n', '\r', ';', '$', '"')):
            parser.error('Unsupported path interpolation characters')
        setattr(args, name, value)
    return args


def own_workspace(args):
    marker = args.root / '.volume-builder-owned.json'
    expected = {'schema_version': 1, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'),
                'prefix': str(args.prefix), 'tmp_dir': str(args.tmp_dir)}
    if args.root.exists():
        if not args.resume or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse existing/unowned/mismatched root; new root or exact --resume required')
    else:
        if args.resume or args.prefix.exists():
            raise ValueError('New root and install prefix must be absent')
        args.root.mkdir(parents=True)
        write_json(marker, expected)
    if args.root.stat().st_dev != args.tmp_dir.stat().st_dev:
        raise ValueError('Cache output and TMPDIR must share native private filesystem')
    guard = (args.root / '.volume-builder-lock').open('a+')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return guard


def common_flags(args, preset, prefix=None):
    prefix = prefix or args.prefix
    values = {'CMAKE_TOOLCHAIN_FILE': str(preset), 'CMAKE_PROJECT_INCLUDE': str(HERE / 'cmake/native-host.cmake'),
              'CMAKE_MAKE_PROGRAM': str(args.ninja), 'PKG_CONFIG_EXECUTABLE': str(args.pkgconf),
              'Python3_EXECUTABLE': str(args.python), 'CMAKE_INSTALL_PREFIX': str(prefix), 'CMAKE_PREFIX_PATH': str(prefix),
              'CMAKE_INSTALL_LIBDIR': 'lib', 'CMAKE_BUILD_TYPE': 'Release', 'CMAKE_C_STANDARD': '17',
              'CMAKE_CXX_STANDARD': '20', 'CMAKE_CXX_STANDARD_REQUIRED': 'ON', 'CMAKE_POSITION_INDEPENDENT_CODE': 'ON',
              'CMAKE_CXX_SCAN_FOR_MODULES': 'OFF', 'BUILD_SHARED_LIBS': 'OFF', 'CMAKE_SKIP_RPATH': 'ON',
              'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON', 'CMAKE_JOB_POOLS': 'compile=' + str(args.jobs) + ';link=1',
              'CMAKE_JOB_POOL_COMPILE': 'compile', 'CMAKE_JOB_POOL_LINK': 'link',
              'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF', 'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF',
              'CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH': 'OFF', 'CMAKE_FIND_USE_CMAKE_SYSTEM_PATH': 'OFF',
              'FETCHCONTENT_FULLY_DISCONNECTED': 'ON', 'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON'}
    return ['-G', 'Ninja'] + ['-D' + k + '=' + v for k, v in values.items()]


def component_flags(name, args):
    extra = {'zlib': {'VOLUME_ZLIB_SOURCE': str(args.root / 'sources/zlib')},
             'imath': {'BUILD_TESTING': 'OFF', 'IMATH_INSTALL': 'ON', 'IMATH_INSTALL_PKG_CONFIG': 'ON',
                       'PYTHON': 'OFF', 'PYBIND11': 'OFF', 'BUILD_WEBSITE': 'OFF'},
             'tbb': {'TBB_TEST': 'OFF', 'TBB_EXAMPLES': 'OFF', 'TBBMALLOC_BUILD': 'ON', 'TBBMALLOC_PROXY_BUILD': 'OFF',
                     'TBB_DISABLE_HWLOC_AUTOMATIC_SEARCH': 'ON', 'TBB_STRICT': 'OFF', 'TBB_ENABLE_IPO': 'OFF', 'TBB_INSTALL': 'ON'}}
    recipe = json.loads((HERE / 'recipe.json').read_text())['candidate_component_cmake']
    settings = dict(extra.get(name, recipe.get(name, {})))
    for key, value in settings.items():
        settings[key] = value.replace('${PRIVATE_PREFIX}', str(args.prefix))
        if '${' in settings[key]:
            raise ValueError('Unresolved component option')
    return ['-D' + k + '=' + v for k, v in settings.items()]


def command_plan(args, preset):
    common = common_flags(args, preset)
    rows = []
    for name in ORDER:
        source = HERE / 'cmake/zlib' if name == 'zlib' else args.root / 'sources' / ('fftw' if name.startswith('fftw') else name)
        build = args.root / 'build' / name
        rows.append({'name': name, 'configure': [str(args.cmake), '-S', str(source), '-B', str(build), *common, *component_flags(name, args)],
                     'build': [str(args.cmake), '--build', str(build), '--parallel', str(args.jobs)],
                     'install': [str(args.cmake), '--install', str(build)]})
    return rows


def guard_configured_dependencies(args, build, prefix):
    """Reject accidental prebuilt third-party library selection in real caches."""
    cache = (build / 'CMakeCache.txt').read_text()
    allowed = [args.root.resolve(), prefix.resolve(), args.sdk_root.resolve()]
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
                    raise ValueError('Configured library outside own source closure/declared SDK: ' + line)


def consumers(args, runner, preset, build, prefix, mode, label, forbidden=None):
    runner.env['PKG_CONFIG_LIBDIR'] = str(prefix / 'lib/pkgconfig')
    flags = common_flags(args, preset, prefix) + ['-DVOLUME_CONSUMER=' + mode,
            '-DZLIB_LIBRARY=' + str(prefix / 'lib/libz.a'), '-DZLIB_INCLUDE_DIR=' + str(prefix / 'include')]
    runner.run([args.cmake, '-S', HERE / 'consumers', '-B', build, *flags], label + '-configure')
    guard_configured_dependencies(args, build, prefix)
    plan = runner.run([args.ninja, '-C', build, '-t', 'commands'], label + '-actual-link-plan')
    for old in forbidden or []:
        if old in plan:
            raise ValueError('Moved consumer still uses original prefix/source/build route')
    for line in (build / 'build.ninja').read_text().splitlines():
        if line.lstrip().startswith(('LINK_LIBRARIES =', 'INCLUDES =')):
            if '/usr/local/' in line or ' -I/usr/include' in line or ' -L/usr/lib' in line:
                raise ValueError('Accidentally selected system dependency')
    runner.run([args.cmake, '--build', build, '--parallel', str(args.jobs)], label + '-build')
    artifacts = [audit.elf(args, runner, build / leaf, label + '-' + leaf)
                 for leaf in ['volume_acceptance', 'fftw_acceptance', 'tbb_acceptance', 'nanovdb_stream_regression', 'libvolume_pic.so']]
    with tempfile.TemporaryDirectory(prefix='volume-native-fixtures-', dir=runner.tmp) as td:
        fixtures = Path(td) / '卷 volume fixtures with spaces'
        fixtures.mkdir()
        output = runner.run([build / 'volume_acceptance', fixtures], label + '-volume-run', timeout=args.runtime_timeout)
        if not re.search(r'^ALL PASS volume checks=\d+$', output, re.M):
            raise ValueError('Actual volume sentinel missing')
        files = audit.file_inventory(fixtures)
        fft = runner.run([build / 'fftw_acceptance'], label + '-fftw-run', timeout=args.runtime_timeout)
        if not re.search(r'^ALL PASS fftw checks=\d+$', fft, re.M):
            raise ValueError('Actual FFTW sentinel missing')
        tbb = runner.run([build / 'tbb_acceptance'], label + '-tbb-run', timeout=args.runtime_timeout)
        if 'PASS oneTBB real worker' not in tbb:
            raise ValueError('Actual source-built TBB native consumer sentinel missing')
        stream = runner.run([build / 'nanovdb_stream_regression'], label + '-nanovdb-stream-run', timeout=args.runtime_timeout)
        if not re.search(r'^ALL PASS NanoVDB stream regression checks=\d+$', stream, re.M):
            raise ValueError('Actual NanoVDB short-stream regression sentinel missing')
    return {'mode': mode, 'artifacts': artifacts, 'volume_output': output, 'fftw_output': fft, 'tbb_output': tbb, 'nanovdb_stream_output': stream,
            'fixtures': files, 'temporary_tree_cleaned': True, 'pic_module_dlopened': False}


def pipeline(args, runner):
    prerequisite = toolchain.preflight(args, runner)
    receipt = args.root / 'prerequisites.json'
    if receipt.exists() and json.loads(receipt.read_text()) != prerequisite:
        raise ValueError('Explicit SDK/tool input bytes changed since owned plan')
    write_json(receipt, prerequisite)
    preset = toolchain.generate(args, args.root)
    rows = command_plan(args, preset)
    plan = {'status': 'Portable candidate; full native run remains unaccepted', 'commands': rows,
            'input_lock_sha256': sha(HERE / 'inputs.lock.json'), 'source_closure': ORDER,
            'zstd': 'Complete pinned archive/inventory available but no enabled consumer/codec; excluded from built prefix',
            'tools': prerequisite['tool_inputs'], 'new_full_native_acceptance': False,
            'scheduling': {'nice': 10, 'jobs': args.jobs, 'link_pool': 1, 'lld_threads': 2}}
    write_json(args.root / 'plan.json', plan)
    if args.stage == 'plan':
        return plan
    if args.stage == 'full':
        # Preserve prior own receipts while preventing stale PASS pointers after
        # a new failed attempt in the same owned root.
        history = args.root / 'receipt-history' / runner.logs.name
        for leaf in ['full-native-acceptance.json', 'acceptance.json', 'artifacts.json', 'migration.json']:
            file = args.root / leaf
            if file.exists():
                history.mkdir(parents=True, exist_ok=True)
                file.rename(history / leaf)
        source_tree.prepare(args.root, runner, args.git)
        probe = args.root / 'build/prerequisites'
        runner.run([args.cmake, '-S', HERE / 'preflight', '-B', probe, *common_flags(args, preset)], 'prerequisite-configure')
        runner.run([args.cmake, '--build', probe, '--parallel', str(args.jobs)], 'prerequisite-build')
        runner.run([args.ctest, '--test-dir', probe, '--output-on-failure', '-V'], 'prerequisite-native-tests')
        for entry in rows:
            name = entry['name']
            source_tree.verify_tree(args.root / 'sources' / ('fftw' if name.startswith('fftw') else name),
                                    'fftw' if name.startswith('fftw') else name, patched=True)
            runner.env['PKG_CONFIG_LIBDIR'] = str(args.prefix / 'lib/pkgconfig')
            for stage in ['configure', 'build', 'install']:
                runner.run(entry[stage], name + '-' + stage)
                if stage == 'configure':
                    guard_configured_dependencies(args, args.root / 'build' / name, args.prefix)
            metadata.install(name, args, args.root)
        audit.freeze_prefix(args, args.root)
    consume = lambda build, prefix, mode, label, forbidden=None: consumers(args, runner, preset, build, prefix, mode, label, forbidden)
    if args.stage in ['full', 'acceptance']:
        audit.verify_prefix(args, args.root)
        tests = [consume(args.root / 'build' / ('acceptance-' + mode), args.prefix, mode, 'original-' + mode) for mode in ['CMAKE', 'PC']]
        write_json(args.root / 'acceptance.json', {'result': 'PASS actual new CMake/PC volume/FFT/TBB runtime consumers',
                   'consumers': tests, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'), 'pic_module_dlopened': False})
    if args.stage in ['full', 'audit']:
        if not (args.root / 'acceptance.json').is_file():
            raise ValueError('Actual new acceptance required before audit')
        audit.artifacts(args, args.root, runner)
    if args.stage in ['full', 'migrate']:
        if not (args.root / 'artifacts.json').is_file():
            raise ValueError('Actual new artifact audit required before migration')
        audit.migrate(args, args.root, runner, consume)
    if args.stage == 'full':
        result = {'result': 'PASS actual independent complete selected-source native pipeline and migrated CMake/PC consumers',
                  'input_lock_sha256': sha(HERE / 'inputs.lock.json'), 'new_full_native_acceptance': True,
                  'scope': 'Seven source build groups; OpenVDB/NanoVDB CPU+ZIP/BLOSC+mesh-volume; FFTW double/float pthreads; TBB/Imath/Zlib own source closure',
                  'excluded': ['Zstd codec', 'Delayed loading', 'GPU/AX/Python bindings', 'PIC module dlopen/static C++ runtime ownership', 'Blender/HAP/release']}
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
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        guard.close()


if __name__ == '__main__':
    main()
