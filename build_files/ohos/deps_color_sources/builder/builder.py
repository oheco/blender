#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repository-owned portable offline color builder; full is a parent-owned action."""
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

ORDER = source_tree.ACTIVE
STAGES = ['verify-inputs', 'materialize', 'prepare', 'plan', 'full', 'acceptance', 'audit', 'migrate']
TOOLS = ['sdk_root', 'cc', 'cxx', 'lld', 'resource_dir', 'signer', 'python', 'cmake', 'ninja', 'git', 'pkgconf', 'ctest']


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    for name in ['root', 'prefix', 'tmp_dir', *TOOLS]:
        parser.add_argument('--' + name.replace('_', '-'), type=Path)
    parser.add_argument('--resume', action='store_true', help='Only exact owned immutable root may resume')
    parser.add_argument('--source', action='append', choices=source_tree.ACTIVE, help='Prepare only this complete sealed source group; repeat as needed')
    parser.add_argument('--jobs', type=int, choices=[1, 2], default=2)
    parser.add_argument('--runtime-timeout', type=int, default=180, help='Seconds for each actual consumer; never fake a timeout PASS')
    args = parser.parse_args()
    if args.runtime_timeout < 1:
        parser.error('Runtime timeout must be positive')
    if args.source and args.stage != 'prepare':
        parser.error('--source is available only for short prepare groups')
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
    marker = args.root / '.color-builder-owned.json'
    expected = {'schema_version': 1, 'input_lock_sha256': sha(HERE / 'inputs.lock.json'),
                'prefix': str(args.prefix), 'tmp_dir': str(args.tmp_dir)}
    if args.root.exists():
        if not args.resume or marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse existing/unowned/mismatched root; new root or exact --resume required')
    else:
        if args.resume or args.prefix.exists():
            raise ValueError('New root and install prefix must be absent')
        args.root.mkdir(parents=True)
        write_json(marker, expected)
    if args.root.stat().st_dev != args.tmp_dir.stat().st_dev:
        raise ValueError('Cache output and TMPDIR must share native private filesystem')
    lockfile = args.root / '.color-builder-lock'
    if lockfile.is_symlink():
        raise ValueError('Refuse symbolic-link ownership lock')
    guard = lockfile.open('a+')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return guard


def common_flags(args, preset, prefix=None):
    prefix = prefix or args.prefix
    values = {'CMAKE_TOOLCHAIN_FILE': str(preset), 'CMAKE_PROJECT_INCLUDE': str(HERE / 'cmake/native-host.cmake'),
              'CMAKE_MAKE_PROGRAM': str(args.ninja), 'PKG_CONFIG_EXECUTABLE': str(args.pkgconf),
              'Python3_EXECUTABLE': str(args.python), 'Python_EXECUTABLE': str(args.python),
              'GIT_EXECUTABLE': str(args.git), 'CMAKE_INSTALL_PREFIX': str(prefix), 'CMAKE_PREFIX_PATH': str(prefix),
              'CMAKE_INSTALL_LIBDIR:STRING': 'lib', 'CMAKE_BUILD_TYPE': 'Release', 'CMAKE_C_STANDARD': '17',
              'CMAKE_CXX_STANDARD': '20', 'CMAKE_CXX_STANDARD_REQUIRED': 'ON', 'CMAKE_POSITION_INDEPENDENT_CODE': 'ON',
              'CMAKE_CXX_SCAN_FOR_MODULES': 'OFF', 'BUILD_SHARED_LIBS': 'OFF', 'CMAKE_SKIP_RPATH': 'ON',
              'CMAKE_EXPORT_COMPILE_COMMANDS': 'ON', 'CMAKE_JOB_POOLS': 'compile=' + str(args.jobs) + ';link=1',
              'CMAKE_JOB_POOL_COMPILE': 'compile', 'CMAKE_JOB_POOL_LINK': 'link',
              'CMAKE_FIND_USE_PACKAGE_REGISTRY': 'OFF', 'CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY': 'OFF',
              'CMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH': 'OFF', 'CMAKE_FIND_USE_CMAKE_SYSTEM_PATH': 'OFF',
              'FETCHCONTENT_FULLY_DISCONNECTED': 'ON', 'FETCHCONTENT_UPDATES_DISCONNECTED': 'ON'}
    return ['-G', 'Ninja'] + ['-D' + k + '=' + v for k, v in values.items()]


def component_flags(name, args):
    recipe = json.loads((HERE / 'recipe.json').read_text())['candidate_component_cmake']
    settings = dict(recipe[name])
    for key, value in settings.items():
        settings[key] = value.replace('${PRIVATE_PREFIX}', str(args.prefix)).replace('${SOURCE_ROOT}', str(args.root / 'sources'))
        if '${' in settings[key]:
            raise ValueError('Unresolved component option')
    return ['-D' + k + '=' + v for k, v in settings.items()]


def command_plan(args, preset):
    common = common_flags(args, preset)
    rows = []
    for name in ORDER:
        source = HERE / 'cmake/zlib' if name == 'zlib' else args.root / 'sources' / name
        if name == 'expat':
            source /= 'expat'
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
    pc_tokens = metadata.query_static(args, runner, prefix, label)
    flags = common_flags(args, preset, prefix) + ['-DCOLOR_CONSUMER=' + mode,
            '-DZLIB_LIBRARY=' + str(prefix / 'lib/libz.a'), '-DZLIB_INCLUDE_DIR=' + str(prefix / 'include')]
    runner.run([args.cmake, '-S', HERE / 'consumers', '-B', build, *flags], label + '-configure')
    guard_configured_dependencies(args, build, prefix)
    plan = runner.run([args.ninja, '-C', build, '-t', 'commands'], label + '-actual-link-plan')
    for old in forbidden or []:
        if old in plan or old in ' '.join(metadata.pkgconf_tokens(plan.encode())) or any(old in token for token in pc_tokens):
            raise ValueError('Moved consumer still uses original prefix/source/build route')
    for line in (build / 'build.ninja').read_text().splitlines():
        if line.lstrip().startswith(('LINK_LIBRARIES =', 'INCLUDES =')):
            if '/usr/local/' in line or ' -I/usr/include' in line or ' -L/usr/lib' in line:
                raise ValueError('Accidentally selected system dependency')
    runner.run([args.cmake, '--build', build, '--parallel', str(args.jobs)], label + '-build')
    artifacts = [audit.elf(args, runner, build / leaf, label + '-' + leaf)
                 for leaf in ['color_acceptance', 'half_texture_acceptance', 'tbb_acceptance', 'libcolor_pic.so']]
    with tempfile.TemporaryDirectory(prefix='color-native-fixtures-', dir=runner.tmp) as td:
        fixtures = Path(td) / '卷 color fixtures with spaces'
        fixtures.mkdir()
        output = runner.run([build / 'color_acceptance', fixtures], label + '-color-run', timeout=args.runtime_timeout)
        if not re.search(r'^ALL PASS checks=45184$', output, re.M):
            raise ValueError('Actual color sentinel missing')
        extended = runner.run([build / 'half_texture_acceptance', fixtures], label + '-half-texture-run', timeout=args.runtime_timeout)
        if not re.search(r'^ALL PASS half-texture checks=\d+$', extended, re.M):
            raise ValueError('Actual Half/texture workflow sentinel missing')
        tbb = runner.run([build / 'tbb_acceptance'], label + '-tbb-run', timeout=args.runtime_timeout)
        if 'PASS oneTBB real worker' not in tbb:
            raise ValueError('Actual source-built TBB native consumer sentinel missing')
        files = audit.file_inventory(fixtures)
    return {'mode': mode, 'artifacts': artifacts, 'pkgconf_static_tokens': pc_tokens, 'color_output': output, 'half_texture_output': extended, 'tbb_output': tbb,
            'fixtures': files, 'temporary_tree_cleaned': True, 'pic_module_dlopened': False}


def invalidate_receipts(args, runner):
    leaves = {'full': ['full-native-acceptance.json', 'acceptance.json', 'artifacts.json', 'migration.json'],
              'acceptance': ['full-native-acceptance.json', 'acceptance.json', 'artifacts.json', 'migration.json'],
              'audit': ['full-native-acceptance.json', 'artifacts.json', 'migration.json'],
              'migrate': ['full-native-acceptance.json', 'migration.json']}.get(args.stage, [])
    history = args.root / 'receipt-history' / runner.logs.name
    for leaf in leaves:
        file = args.root / leaf
        if file.is_symlink():
            raise ValueError('Unsafe current acceptance receipt type')
        if file.exists():
            history.mkdir(parents=True, exist_ok=True)
            file.rename(history / leaf)


def pipeline(args, runner):
    invalidate_receipts(args, runner)
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
        source_tree.prepare(args.root, runner, args.git)
        probe = args.root / 'build/prerequisites'
        runner.run([args.cmake, '-S', HERE / 'preflight', '-B', probe, *common_flags(args, preset)], 'prerequisite-configure')
        runner.run([args.cmake, '--build', probe, '--parallel', str(args.jobs)], 'prerequisite-build')
        runner.run([args.ctest, '--test-dir', probe, '--output-on-failure', '--no-tests=error', '-V'], 'prerequisite-native-tests')
        for entry in rows:
            name = entry['name']
            source_tree.verify_tree(args.root / 'sources' / name, name, patched=True)
            runner.env['PKG_CONFIG_LIBDIR'] = str(args.prefix / 'lib/pkgconfig')
            for stage in ['configure', 'build', 'install']:
                runner.run(entry[stage], name + '-' + stage)
                if stage == 'configure':
                    guard_configured_dependencies(args, args.root / 'build' / name, args.prefix)
                    metadata.guard_install_layout(args.root / 'build' / name, args.prefix)
            metadata.install(name, args, args.root)
            source_tree.verify_tree(args.root / 'sources' / name, name, patched=True)
            if name == 'png':
                write_json(args.root / 'png-test-profile.json', {'upstream_ctest_not_available_for_static_profile': True,
                           'reason': 'Pinned libpng registers its tests only for PNG_TESTS AND PNG_SHARED',
                           'actual_png_gate': 'Original color consumer PNG roundtrip and IOProxy, in all four CMAKE/PC/moved executions',
                           'no_zero_test_pass_claim': True})
        audit.freeze_prefix(args, args.root)
    consume = lambda build, prefix, mode, label, forbidden=None: consumers(args, runner, preset, build, prefix, mode, label, forbidden)
    if args.stage in ['full', 'acceptance']:
        audit.verify_prefix(args, args.root)
        tests = [consume(args.root / 'build' / ('acceptance-' + mode), args.prefix, mode, 'original-' + mode) for mode in ['CMAKE', 'PC']]
        write_json(args.root / 'acceptance.json', {'result': 'PASS actual new CMake/PC color/Half/texture/TBB runtime consumers',
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
                  'scope': 'Twenty selected complete source groups; OpenEXR FLOAT/HALF/HTJ2K/ZIP/PIZ; OCIO matrix/inverse/look/LUT; OIIO formats/IOProxy/DDS/color conversion/tiled MIP texture; own TBB/Imath/Zlib/PNG/JPEG/fmt',
                  'excluded': ['Zstd/TIFF optional codecs per baseline', 'OCIO/OIIO Python bindings and CLI tools', 'HEIF codec source closure', 'PIC module dlopen/static C++ runtime ownership', 'Blender/HAP/release']}
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
            result = source_tree.prepare(args.root, runner, args.git, args.source)
        else:
            result = pipeline(args, runner)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        guard.close()


if __name__ == '__main__':
    main()
