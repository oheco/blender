# SPDX-License-Identifier: GPL-2.0-or-later
"""Short source IO/negative gates only. Never invoke CMake, compiler, bpy or SDK."""
import argparse
import ast
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.dont_write_bytecode = True
from common import HERE, verify_recipe, sha, load, dump, inventory, digest
from contracts import pending
from dependencies import verify_dependencies
from profiles import profile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True); p.add_argument('--root', type=Path, required=True)
    p.add_argument('--tmp', type=Path, required=True)
    args = p.parse_args(); repo = args.repo.resolve(); root = args.root.resolve(); tmp = args.tmp.resolve()
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve()
    if root.exists() or root.parent != cache or not root.name.startswith('blender-ohos-editor-') or tmp != Path(os.environ['TMPDIR']).resolve():
        raise ValueError('New dedicated source-guard cache root and exact managed TMPDIR required')
    root.mkdir()
    checks = []; counter = 0
    def command(arguments, expected=0):
        nonlocal counter
        counter += 1
        process = subprocess.run([sys.executable, '-B', HERE / 'builder.py', *map(str, arguments)], capture_output=True, text=True)
        dump(root / f'{counter:03d}-command.json', {'argv': [sys.executable, '-B', str(HERE / 'builder.py'), *map(str, arguments)],
                                                  'actual_exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr})
        if process.returncode != expected:
            raise AssertionError('Unexpected source guard command exit: ' + process.stderr)
        return process
    def refuses(fn, label):
        try:
            fn()
        except (ValueError, OSError, KeyError):
            checks.append(label); return
        raise AssertionError('Guard did not refuse: ' + label)
    before = sha(repo / 'build_files/ohos/editor/inputs.lock.json')
    sealed = verify_recipe(repo)
    for file in HERE.rglob('*.py'):
        ast.parse(file.read_text(), filename=str(file))
    checks.append('All new Python source parses without executing native tools')
    base = ['--repo', repo, '--tmp', tmp]
    command(['verify-inputs', *base]); checks.append('Original sealed recipe/source CLI')
    command(['prepare', *base, '--root', root / 'original'])
    original = load(root / 'original/sse2neon-source.json')
    if original['source_files'] != 25 or original['native_full'] != 'NOT_RUN':
        raise AssertionError('Source replay scope differs')
    checks.append('Original official 25-file SSE replay, full native NOT_RUN')
    command(['full', *base, '--root', root / 'bad-native'], 2); checks.append('Missing explicit native contracts rejected')
    command(['plan', *base, '--root', root / 'bad-plan', '--unknown-define', 'HAVE_FAKE=1'], 2)
    checks.append('Unknown/forced build argument rejected')
    command(['prepare', *base, '--root', root / 'original'], 1); checks.append('Owned reuse requires explicit resume')
    with (root / 'original/.editor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command(['prepare', *base, '--root', root / 'original', '--resume'], 1)
    checks.append('Concurrent root ownership rejected')
    (root / 'unowned').mkdir()
    command(['prepare', *base, '--root', root / 'unowned', '--resume'], 1); checks.append('Unowned/partial output adoption rejected')
    bad = root / 'original/sources/sse2neon/sse2neon.h'
    content = bad.read_bytes(); bad.write_bytes(content + b'\n')
    command(['prepare', *base, '--root', root / 'original', '--resume'], 1)
    if list((root / 'original/current').glob('*.json')):
        raise AssertionError('Failed attempt retained stale PASS pointer')
    bad.write_bytes(content); checks.append('Changed prepared source rejected and prior PASS invalidated')
    with tempfile.TemporaryDirectory(prefix='editor-guards-', dir=tmp) as scratch:
        mini = Path(scratch) / 'Unicode editor 输入 空格'
        command(['copy-inputs', *base, '--destination', mini])
        entry = mini / 'build_files/ohos/editor/builder.py'
        moved_root = root / 'Unicode editor 输入 空格'
        process = subprocess.run([sys.executable, '-B', entry, 'prepare', '--repo', mini, '--tmp', tmp, '--root', moved_root],
                                 capture_output=True, text=True)
        dump(root / 'unicode-command.json', {'actual_exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr})
        if process.returncode:
            raise AssertionError('Unicode mini replay failed: ' + process.stderr)
        moved = load(moved_root / 'sse2neon-source.json')
        if inventory(root / 'original/sources/sse2neon') != inventory(moved_root / 'sources/sse2neon'):
            raise AssertionError('Original/Unicode source bytes differ')
        checks.append('Portable Unicode/space exported mini replays identical original source')
        source = {'schema': 1, 'kind': 'editor-source-snapshot', 'scope': 'sealed-input-mini', 'files': inventory(mini), 'tree_sha256': digest(inventory(mini))}
        proof = Path(scratch) / 'mini-snapshot.json'; dump(proof, source)
        from common import verify_snapshot
        if verify_snapshot(mini, proof, sha(proof))['scope'] != 'sealed-input-mini':
            raise AssertionError('Mini incorrectly claimed complete source')
        checks.append('Mini snapshot remains distinct from complete native source')
        changed = mini / 'build_files/ohos/editor/profiles.py'
        bytes_before = changed.read_bytes(); changed.write_bytes(bytes_before + b'\n')
        refuses(lambda: verify_recipe(mini), 'Stale recipe byte rejected')
        refuses(lambda: verify_snapshot(mini, proof, sha(proof)), 'Stale source snapshot rejected')
        changed.write_bytes(bytes_before)
        extra = mini / 'build_files/ohos/editor/unknown.py'; extra.write_text('x=1\n')
        refuses(lambda: verify_recipe(mini), 'Unknown sibling rejected'); extra.unlink()
        contract = pending(repo, root / 'future-dependencies')
        bundle = Path(scratch) / 'pending.json'; dump(bundle, contract)
        result = verify_dependencies(repo, bundle, sha(bundle), allow_pending=True)
        if result['status'] != 'PREPARED_NOT_RUN' or set(result['prefixes']) != set(contract['dependencies']):
            raise AssertionError('Pending producer was incorrectly promoted')
        checks.append('All nine actual current recipe/source inputs verified; pending prefixes stay pending')
        refuses(lambda: verify_dependencies(repo, bundle, sha(bundle)), 'Pending native dependencies rejected before configuration')
        refuses(lambda: verify_dependencies(repo, bundle, '0' * 64, True), 'Wrong external bundle SHA rejected')
        contract['dependencies']['color']['recipe']['inputs_lock']['sha256'] = '0' * 64; dump(bundle, contract)
        refuses(lambda: verify_dependencies(repo, bundle, sha(bundle), True), 'Immutable Color169 recipe mismatch rejected')
    final = profile('final'); diagnostic = profile('diagnostic')
    if final['required_options']['WITH_STRICT_BUILD_OPTIONS'] != 'ON' or final['required_options']['WITH_LIBMV'] != 'ON' or diagnostic['required_options']['WITH_STRICT_BUILD_OPTIONS'] != 'OFF' or diagnostic['required_options']['WITH_LIBMV'] != 'OFF':
        raise AssertionError('Strict/diagnostic scope incorrectly conflated')
    checks.append('Final strict/libmv and explicit diagnostic policies separated')
    if before != sha(repo / 'build_files/ohos/editor/inputs.lock.json'):
        raise AssertionError('Guard execution changed recipe seal')
    result = {'status': 'PASS_SOURCE_IO_GUARDS_ONLY', 'recipe': sealed, 'checks': checks, 'count': len(checks),
              'original_source': original, 'unicode_source': moved, 'temporary_roots_cleaned': True,
              'compiler_CMake_native_bpy_SDL_HAP': 'NOT_RUN'}
    dump(root / 'source-guards.json', result)
    print(json.dumps({'status': result['status'], 'count': result['count'], 'receipt': str(root / 'source-guards.json'), 'native': 'NOT_RUN'}, indent=2))


if __name__ == '__main__':
    main()
