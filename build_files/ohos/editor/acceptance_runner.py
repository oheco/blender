# SPDX-License-Identifier: GPL-2.0-or-later
"""Run existing real bpy/codec scripts through the signed fixed diagnostic CLI."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from common import HERE, load, dump, sha, record, inventory


def driver():
    p = HERE / 'acceptance/diagnostic_driver.py'
    spec = importlib.util.spec_from_file_location('editor_real_bpy_driver', p)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def accept(args, root, deps, toolkit, host, run, compressed=False):
    python = root / 'install/5.2/python'
    pure = Path(deps['prefixes']['pure_resources'])
    # The runtime composition must contain the exact separately accepted pure8 bytes.
    manifest = load(pure / 'resources.json')
    for row in manifest['site_inventory']:
        target = python / 'lib/python3.13/site-packages' / row['path']
        if not target.is_file() or target.stat().st_size != row['size'] or sha(target) != row['sha256']:
            raise ValueError('Native runtime lacks exact accepted pure8 composition: ' + row['path'])
    native_dirs = [str(root / 'install/lib'), *host.get('native_library_dirs', [])]
    argv = [toolkit['tools']['python'], '-B', HERE / 'acceptance/diagnostic_driver.py', 'stage',
            '--diagnostic', root / 'install/bin/blender-ohos-diagnostic', '--core', root / 'install/lib/libblender_core.so',
            '--python-root', python, '--scripts', args.repo / 'scripts', '--datafiles', args.repo / 'release/datafiles',
            '--cycles-addon', args.repo / 'intern/cycles/blender/addon', '--source-root', args.repo,
            '--source-snapshot', args.source_snapshot, '--source-snapshot-sha256', args.source_snapshot_sha256,
            '--cmake-cache', root / 'build/CMakeCache.txt', '--readelf', toolkit['tools']['readelf'], '--print-root-only']
    if compressed:
        for name in ('libbf_intern_draco_bridge.so', 'libbf_intern_meshopt_bridge.so'):
            argv += ['--dynamic-library', root / 'install/lib' / name]
    for directory in native_dirs:
        argv += ['--native-lib-dir', directory]
    for directory in host['system_library_dirs']:
        argv += ['--system-lib-dir', directory]
    stage_text = run(argv, 'real-bpy-stage')
    stage_root = Path(stage_text.strip())
    d = driver(); stage_root, staged = d.validate_root(str(stage_root))
    evidence = run.folder / ('compressed-bpy-evidence' if compressed else 'bpy-five-stage-evidence')
    try:
        if not compressed:
            run([toolkit['tools']['python'], '-B', HERE / 'acceptance/diagnostic_driver.py', 'run', '--root', stage_root,
                 '--stages', 'model', 'reopen', 'obj', 'glb', 'cycles', '--timeout', '600'], 'actual-bpy-five-stages')
            summary = load(stage_root / 'reports/summary.json')
            if summary['status'] != 'PASS' or set(summary['stages']) != {'model', 'reopen', 'obj', 'glb', 'cycles'}:
                raise ValueError('Real five-stage native bpy acceptance incomplete')
            for name, value in summary['stages'].items():
                if value['status'] != 'PASS' or value['returncode'] != 0:
                    raise ValueError('Native process/report pair did not pass: ' + name)
        else:
            summary = compressed_accept(args, stage_root, staged, d, python, root, toolkit, run)
        shutil.copytree(stage_root / 'reports', evidence / 'reports')
        shutil.copytree(stage_root / 'artifacts', evidence / 'artifacts')
        shutil.copyfile(stage_root / 'stage-manifest.json', evidence / 'stage-manifest.json')
        return {'status': 'PASS_ACTUAL_COMPRESSED_BPY' if compressed else 'PASS_ACTUAL_FIVE_STAGE_BPY',
                'summary': summary, 'retained_evidence': str(evidence), 'files': inventory(evidence),
                'ordinary_GLB_and_compressed_codecs_are_separate': True, 'SDL_window': 'NOT_RUN', 'HAP': 'NOT_RUN'}
    finally:
        # Retain failures even when a stage throws; clean this command's payload/root.
        if not evidence.exists():
            evidence.mkdir(parents=True)
            for name in ('reports', 'artifacts'):
                if (stage_root / name).exists():
                    shutil.copytree(stage_root / name, evidence / name)
            if (stage_root / 'stage-manifest.json').exists():
                shutil.copyfile(stage_root / 'stage-manifest.json', evidence / 'stage-manifest.json')
        d.cleanup_payload(stage_root)
        shutil.rmtree(stage_root)


def compressed_accept(args, stage_root, staged, d, python, root, toolkit, run):
    helper = args.repo / 'build_files/ohos/deps_gltf_sources/acceptance'
    with tempfile.TemporaryDirectory(prefix='editor-codec-bpy-', dir=args.tmp) as scratch:
        fixture_root = Path(scratch) / 'fixtures'
        env = run.env.copy()
        env['LD_LIBRARY_PATH'] = os.pathsep.join([str(root / 'install/lib'), str(python / 'lib')])
        native_report = run.folder / 'actual-bridge-codec-fixture-report.json'
        run([python / 'bin/python3.13', '-I', '-B', '-S', helper / 'bridge_abi_acceptance.py', '--lib-dir', root / 'install/lib',
             '--fixtures', fixture_root, '--report', native_report, '--source-root', args.repo, '--iterations', '4'],
            'actual-bridge-fixtures', env)
        report = load(native_report)
        if report['status'] != 'PASS':
            raise ValueError('Genuine selected final bridge C API fixture generation failed')
        stage_fixtures = stage_root / 'artifacts/compressed-fixtures'
        shutil.copytree(fixture_root, stage_fixtures)
    reports = {}
    for mode in ('import', 'export'):
        session = stage_root / ('sessions/codec-' + mode)
        for name in ('home', 'config', 'cache', 'temp'):
            (session / name).mkdir(parents=True, exist_ok=False)
        env = {'HOME': str(session / 'home'), 'XDG_CONFIG_HOME': str(session / 'config'), 'XDG_CACHE_HOME': str(session / 'cache'),
               'TMPDIR': str(session / 'temp'), 'PATH': '/system/bin:/system/xbin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'LD_LIBRARY_PATH': ':'.join(str(stage_root / name) for name in staged['ld_library_path']), 'OMP_NUM_THREADS': '2'}
        destination = stage_root / ('reports/compressed-' + mode + '.json')
        script = helper / ('bpy_' + mode + '_acceptance.py')
        values = [str(script), '--', '--fixtures', str(stage_fixtures), '--report', str(destination),
                  '--expect-addon-root', str(stage_root / 'runtime/5.2/scripts/addons_core/io_scene_gltf2'),
                  '--expect-lib-dir', str(stage_root / 'lib'), '--addon-core-dir', str(stage_root / 'runtime/5.2/scripts/addons_core'),
                  '--allow-draco-errorcleanup-patch']
        if mode == 'export':
            values += ['--output', str(stage_root / 'artifacts/compressed-export')]
        wrapper = session / 'codec_arguments.py'
        # Explicit fixed-CLI adapter: real helper receives its ordinary arguments after '--'.
        wrapper.write_text('import runpy,sys\nsys.dont_write_bytecode=True\nsys.argv=' + repr(values) + '\nrunpy.run_path(' + repr(str(script)) + ',run_name="__main__")\n')
        command = [str(stage_root / 'bin/blender-ohos-diagnostic'), '-b', '--runtime', str(stage_root / 'runtime'),
                   '--config', str(session / 'config'), '--cache', str(session / 'cache'), '--temp', str(session / 'temp'), '--python', str(wrapper)]
        logpath = stage_root / ('reports/compressed-' + mode + '.native.log')
        process = subprocess.Popen(command, cwd=session, env=env, stdout=logpath.open('wb'), stderr=subprocess.STDOUT, start_new_session=True)
        try:
            exit_code = process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            d.stop_child(process)
            raise ValueError('Actual compressed bpy process timed out')
        finally:
            d.stop_child(process)
        actual = load(destination) if destination.exists() else {}
        receipt = {'argv': command, 'actual_exit': exit_code, 'log': record(logpath), 'script': record(script),
                   'report': record(destination) if destination.exists() else None, 'status': actual.get('status', 'MISSING')}
        dump(stage_root / ('reports/compressed-' + mode + '.process.json'), receipt)
        if exit_code != 0 or actual.get('status') != 'PASS' or actual.get('stage') != 'bpy-' + mode:
            raise ValueError('Actual compressed bpy native/report pair failed: ' + mode)
        reports[mode] = receipt
    return {'status': 'PASS_ACTUAL_COMPRESSED_BPY', 'stages': reports, 'native_fixture_report': record(native_report),
            'bpy_five_stage': 'Independent gate', 'HAP': 'NOT_RUN'}
