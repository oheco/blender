#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline fresh/Unicode/relocation acceptance; child processes are short pure IO checks."""
import sys
sys.dont_write_bytecode = True
import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--cache', type=Path, required=True, help='NEW owned container for both acceptance runs')
    ap.add_argument('--tmp', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True, help='NEW evidence directory')
    ap.add_argument('--python', type=Path, required=True)
    ap.add_argument('--http-meta', type=Path)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('acceptance_resources', Path(__file__).with_name('resources.py'))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    repo, lock, lock_sha, _ = builder.context(args.repo)
    cache, tmp = builder.output_path(args.cache, args.tmp)
    if cache.exists() or args.output.exists():
        raise ValueError('Acceptance requires NEW cache container and evidence directory')
    cache.mkdir(parents=True, exist_ok=False)
    builder.dump_new(cache / '.acceptance-owner.json', {'owner':builder.NAMESPACE,'lock_sha256':lock_sha,'role':'fresh-offline-acceptance'})
    args.output.mkdir(parents=True, exist_ok=False)
    interpreter_sha = builder.sha(args.python)
    receipts = []
    report = {'schema':1,'status':'RUNNING','runtime_label':'accepted-terminal-CPython-3.13.13-independent-pure-only',
              'interpreter_sha256':interpreter_sha,'lock_sha256':lock_sha,'steps':receipts,
              'native_builds':0,'network_requests':0}
    def cpu_limit():
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    def run(label, script, arguments, cwd):
        child_tmp = cwd / (label + '-tmp')
        child_tmp.mkdir()
        arguments = list(arguments)
        if '--tmp' in arguments:
            arguments[arguments.index('--tmp') + 1] = child_tmp
        command = [str(args.python), '-I', '-B', '-S', str(script), *map(str, arguments)]
        started = time.monotonic()
        child_env = dict(os.environ, TMPDIR=str(child_tmp))
        cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        wall_timeout = False
        try:
            result = subprocess.run(command, cwd=cwd, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=60, preexec_fn=cpu_limit)
            content, code = result.stdout, result.returncode
        except subprocess.TimeoutExpired as error:
            content = (error.stdout or b'') + b'\nACCEPTANCE WALL TIMEOUT\n'
            code = 124
            wall_timeout = True
        log = args.output / (label + '.log')
        with log.open('xb') as out:
            out.write(content)
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        item = {'step':label,'command':command,'actual_exit':None if wall_timeout else code,
                'harness_exit':code,'wall_timeout':wall_timeout,'wall_budget_seconds':60,'cpu_limit_seconds':15,
                'actual_cpu_seconds':cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime,
                'child_tmp_under_owned_parent':True,'elapsed_seconds':time.monotonic() - started,
                'log':log.name,'log_sha256':builder.sha(log)}
        receipts.append(item)
        print(label, 'actualexit=' + str(code), flush=True)
        if code:
            raise RuntimeError('Preserved failing acceptance log: ' + log.name)
        return json.loads(content)
    try:
        with tempfile.TemporaryDirectory(prefix='formal-pure-acceptance-', dir=tmp) as private:
            private = Path(private)
            # Poison cwd proves isolated imports never fall back to cwd/old sites.
            for name in ('requests.py','cattrs.py','bpy.py'):
                (private / name).write_text("raise AssertionError('cwd import poison')\n")
            outputs = []
            for label, source_repo, source_cache, pure_root in [
                    ('fresh', repo, cache / 'fresh-sources', cache / 'fresh-pure'),
                    ('unicode-space', cache / '源码 仓库 space', cache / '独立 缓存 sources', cache / '纯资源 space root')]:
                if label == 'unicode-space':
                    source_repo.mkdir()
                    shutil.copytree(repo / builder.NAMESPACE, source_repo / builder.NAMESPACE)
                    helper = source_repo / 'build_files/ohos/vendor_archive.py'
                    helper.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(repo / 'build_files/ohos/vendor_archive.py', helper)
                    for pin in lock['inputs']:
                        shutil.copytree(repo / pin['registry_id'], source_repo / pin['registry_id'])
                    original = builder.inventory(repo / builder.NAMESPACE)
                    assert builder.inventory(source_repo / builder.NAMESPACE) == original
                    report['source_only_replay_repository'] = {'relative_to_owned_cache':source_repo.name,
                        'namespace_inventory':original,'namespace_tree_sha256':builder.seal(original),
                        'registry_ids':[p['registry_id'] for p in lock['inputs']], 'complete_blender_copy':False}
                entry = source_repo / builder.NAMESPACE
                common = ['--repo',source_repo,'--cache',source_cache,'--tmp',tmp]
                run(label + '-materialize', entry / 'materialize.py', common, private)
                run(label + '-prepare', entry / 'prepare.py', common, private)
                assembled = run(label + '-assemble', entry / 'assemble.py', common + ['--root',pure_root], private)
                run(label + '-verify', entry / 'verify.py', ['--repo',source_repo,'--cache',source_cache,'--root',pure_root], private)
                native_args = ['--repo',source_repo,'--root',pure_root,'--tmp',tmp]
                if label == 'fresh' and args.http_meta:
                    native_args += ['--http-meta',args.http_meta]
                native = run(label + '-native', entry / 'check_native.py', native_args, private)
                outputs.append({'case':label,'site_tree_sha256':assembled['site_tree_sha256'], 'native':native,
                                'source_cache_relative':source_cache.name,'pure_root_relative':pure_root.name})
                for pin in lock['inputs']:
                    materialized = source_cache / 'archives' / pin['filename']
                    original = b''.join((repo / pin['registry_id'] / part['filename']).read_bytes() for part in pin['parts'])
                    assert materialized.read_bytes() == original
                if label == 'fresh':
                    guards = run('adversarial-guards', entry / 'test_guards.py', ['--repo',repo,'--tmp',tmp,'--root',pure_root], private)
                    report['negative_guards'] = guards
            assert outputs[0]['site_tree_sha256'] == outputs[1]['site_tree_sha256']
            relocated = cache / '迁移 空格 pure resources'
            (cache / outputs[1]['pure_root_relative']).rename(relocated)
            replay_repo = cache / '源码 仓库 space'
            entry = replay_repo / builder.NAMESPACE
            run('relocated-verify', entry / 'verify.py', ['--repo',replay_repo,'--root',relocated], private)
            relocated_native = run('relocated-native', entry / 'check_native.py', ['--repo',replay_repo,'--root',relocated,'--tmp',tmp], private)
            assert relocated_native['site_tree_sha256'] == outputs[0]['site_tree_sha256']
            report.update(status='PASS',runs=outputs,relocated_root_relative=relocated.name,
                          relocated_native=relocated_native,original_archive_bytes_equal=True,
                          exact_original_dependency_metadata=True,relocatable_manifests=True)
        assert not private.exists()
        assert builder.sha(args.python) == interpreter_sha
        report.update(all_tmp_cleaned=True,interpreter_unchanged=True)
    except BaseException as error:
        report.update(status='FAIL',error=repr(error),all_tmp_cleaned=not Path(private).exists() if 'private' in locals() else True)
        raise
    finally:
        builder.dump_new(args.output / 'acceptance.json', report)
    print(json.dumps({'status':report['status'],'steps':len(receipts),'site_tree_sha256':outputs[0]['site_tree_sha256'],
                      'source_files':lock['complete_source_file_count'],'pure_files':outputs[0]['native']['pure_file_count'],
                      'all_tmp_cleaned':True}, indent=2))


if __name__ == '__main__':
    main()
