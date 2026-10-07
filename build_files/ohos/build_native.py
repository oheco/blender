#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a configured native OHOS Blender target, preserving logs and failures.

The process owns a private-directory lock for its whole lifetime. Use the parent
session's managed background job for long builds; a successful compile is not
editor/HAP acceptance. Compiler launchers sign linked generators before execution.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--target', action='append', default=[])
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=2)
    parser.add_argument('--keep-going', type=int, default=1,
                        help='Ninja failure limit; 0 collects independent failures')
    args = parser.parse_args()
    if args.keep_going < 0:
        parser.error('--keep-going must be nonnegative')
    if os.uname().sysname not in ('HarmonyOS', 'OHOS', 'OpenHarmony') or os.uname().machine != 'aarch64':
        raise SystemExit('A native ARM64 OHOS environment is required')
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve()
    build = (args.build_dir or cache / 'blender-ohos-editor-build').resolve()
    if cache not in build.parents or not (build / 'build.ninja').is_file():
        raise SystemExit('A generated Ninja build under private XDG_CACHE_HOME is required')
    ninja = shutil.which('ninja')
    if not ninja:
        raise SystemExit('Native Ninja is required')
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    os.setpriority(os.PRIO_PROCESS, 0, 10)
    logs = build / 'ohos-logs'
    logs.mkdir(exist_ok=True)
    with (build / '.ohos-build.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another managed build owns this directory')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        logpath = logs / f'build-{stamp}.log'
        receiptpath = logs / f'build-{stamp}.json'
        command = [ninja, '-C', str(build), '-j', str(args.jobs), '-k', str(args.keep_going),
                   *(args.target or ['blender'])]
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, env=env, text=True).strip()
        status = subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, env=env, text=True)
        receipt = {'source_commit': revision, 'working_tree_status': status,
                   'native_system': list(os.uname()), 'build_directory': str(build),
                   'command': command, 'log': str(logpath), 'started_utc': stamp,
                   'status': 'running', 'editor_acceptance': False}
        receiptpath.write_text(json.dumps(receipt, indent=2) + '\n')
        started = time.monotonic()
        print(f'Native build log: {logpath}', flush=True)
        with logpath.open('w') as log:
            log.write('Arguments: ' + repr(command) + '\n')
            log.flush()
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            log.write(f'\nexit_code={result.returncode}\n')
        receipt.update(exit_code=result.returncode, duration_seconds=round(time.monotonic() - started, 3),
                       status='compiled' if result.returncode == 0 else 'failed')
        receiptpath.write_text(json.dumps(receipt, indent=2) + '\n')
        print(f'Native build exit={result.returncode}; receipt={receiptpath}', flush=True)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
