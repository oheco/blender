#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable editor orchestration. Native actions require completed explicit inputs."""
import sys
sys.dont_write_bytecode = True
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import traceback
from common import HERE, sha, load, dump, record, digest, inventory, verify_recipe, verify_snapshot, absolute, bound
from source_inputs import prepare_sse, verify_sse
from toolkit import verify_toolkit
from dependencies import verify_dependencies
from commands import plan, project_hook, create_query
from graph import audit_graph
from elf import sign_installed, audit_install
from runtime import compose_runtime

NATIVE = {'configure', 'build', 'install', 'audit', 'full', 'probe', 'bpy', 'bpy-gltf'}


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['verify-inputs', 'snapshot', 'copy-inputs', 'prepare', 'plan', *sorted(NATIVE)])
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--root', type=Path)
    p.add_argument('--tmp', type=Path, required=True)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--profile', choices=['final', 'diagnostic'], default='final')
    p.add_argument('--jobs', type=int, choices=[1, 2], default=2)
    p.add_argument('--source-snapshot', type=Path)
    p.add_argument('--source-snapshot-sha256')
    p.add_argument('--dependencies', type=Path)
    p.add_argument('--dependencies-sha256')
    p.add_argument('--toolkit', type=Path)
    p.add_argument('--toolkit-sha256')
    p.add_argument('--host-runtime', type=Path)
    p.add_argument('--host-runtime-sha256')
    p.add_argument('--destination', type=Path)
    p.add_argument('--source-commit')
    args = p.parse_args(argv)
    args.repo = absolute(args.repo)
    args.tmp = absolute(args.tmp)
    original_tmp = absolute(os.environ['TMPDIR'])
    if args.tmp != original_tmp or str(args.tmp) == '/tmp':
        p.error('--tmp must equal the explicit real application TMPDIR used by the sealed toolkit; owned subdirectories are created inside it')
    if args.stage in {'prepare', 'plan'} | NATIVE and args.root is None:
        p.error('--root is required for this stage')
    if args.stage in {'plan'} | NATIVE:
        for key in ('source_snapshot', 'dependencies', 'toolkit', 'host_runtime'):
            if getattr(args, key) is None or getattr(args, key + '_sha256') is None:
                p.error('Explicit --' + key.replace('_', '-') + ' and its SHA256 are required')
    if args.stage in {'snapshot', 'copy-inputs'} and args.destination is None:
        p.error('--destination is required')
    if args.stage == 'full' and args.profile != 'final':
        p.error('full is reserved for the strict final profile; diagnostic has separate configure/build/audit receipts')
    return args


def safe_root(args):
    root = absolute(args.root, exists=False)
    cache = absolute(os.environ['XDG_CACHE_HOME'])
    if root == cache or not root.is_relative_to(cache) or not root.relative_to(cache).parts[0].startswith('blender-ohos-editor-') or root.is_relative_to(args.repo) or args.repo.is_relative_to(root) or root.is_relative_to(args.tmp) or args.tmp.is_relative_to(root):
        raise ValueError('New state/build/install root must be private cache, disjoint from source and TMPDIR')
    return root


@contextmanager
def owned(args):
    root = safe_root(args)
    seal = sha(args.repo / 'build_files/ohos/editor/inputs.lock.json')
    marker = root / '.editor-owned.json'
    expected = {'schema': 1, 'kind': 'editor-orchestration-owned-root', 'root': str(root), 'repo': str(args.repo), 'input_lock_sha256': seal}
    if root.exists():
        if not args.resume or not marker.is_file() or load(marker) != expected:
            raise ValueError('Refuse existing/unowned/mismatched editor root; exact owned reuse needs --resume')
    else:
        root.mkdir(parents=True)
        dump(marker, expected)
    with (root / '.editor.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another editor command owns this independent root')
        yield root, seal


class Runner:
    def __init__(self, folder, tmp, toolkit):
        import importlib.util
        self.folder = folder; self.count = 0
        policy = Path(toolkit['paths']['cc_launcher']).parent / 'env_policy.py'
        spec = importlib.util.spec_from_file_location('editor_sealed_toolkit_environment', policy)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.env, self.removed_ambient_selectors = module.clean(tmp_dir=tmp)
        self.env['PATH'] = os.pathsep.join(dict.fromkeys(str(Path(p).parent) for p in toolkit['tools'].values())) + ':/usr/bin'

    def __call__(self, argv, label, env=None):
        argv = list(map(str, argv))
        if not Path(argv[0]).is_absolute():
            raise ValueError('Pinned explicit command executable required')
        self.count += 1
        base = self.folder / f'{self.count:04d}-{label}'
        started = time.monotonic()
        result = subprocess.run(argv, cwd=self.folder, env=env or self.env, capture_output=True, text=True)
        stdout = base.with_suffix('.stdout.log'); stderr = base.with_suffix('.stderr.log')
        stdout.write_text(result.stdout); stderr.write_text(result.stderr)
        dump(base.with_suffix('.json'), {'argv': argv, 'cwd': str(self.folder), 'actual_exit': result.returncode,
                                       'duration_seconds': round(time.monotonic() - started, 3), 'executable': record(Path(argv[0])),
                                       'stdout': record(stdout), 'stderr': record(stderr)})
        if result.returncode:
            raise RuntimeError(f'{label} actual exit {result.returncode}; inspect immutable command logs')
        return result.stdout + result.stderr


def context(args, allow_pending=False):
    proof = verify_recipe(args.repo)
    snapshot = verify_snapshot(args.repo, args.source_snapshot, args.source_snapshot_sha256)
    deps = verify_dependencies(args.repo, args.dependencies, args.dependencies_sha256, allow_pending=allow_pending)
    toolkit = verify_toolkit(args.repo, args.toolkit, args.toolkit_sha256, allow_pending=allow_pending)
    if sha(args.host_runtime) != args.host_runtime_sha256:
        raise ValueError('Host runtime contract byte drift')
    host = load(args.host_runtime)
    if host.get('schema_version') != 1 or host.get('kind') != 'editor-host-runtime-contract':
        raise ValueError('Explicit host-runtime input contract required')
    bound(host['vulkan_library'])
    if not host['system_libraries'] or not host.get('system_library_dirs'):
        raise ValueError('Actual named system runtime libraries and dirs must be explicitly provided')
    seen = set()
    for row in host['system_libraries']:
        if row['name'] in seen or '/' in row['name']:
            raise ValueError('Repeated/nonbasename system SONAME')
        seen.add(row['name']); bound(row['file'])
    for value in host['system_library_dirs']:
        absolute(value)
    root = safe_root(args)
    for prefix in deps['prefixes'].values():
        p = absolute(prefix, exists=False)
        if root.is_relative_to(p) or p.is_relative_to(root):
            raise ValueError('Editor output cannot overlap any provided prefix')
    toolkit_root = absolute(toolkit['paths']['root'])
    if root.is_relative_to(toolkit_root) or toolkit_root.is_relative_to(root):
        raise ValueError('Editor output cannot share the toolkit owned root')
    gaps = list(deps['gaps']) + list(toolkit['gaps'])
    allowed = toolkit['compiler_config']['allowed_output_roots']
    if str(root) not in allowed:
        gaps.append('EDITOR_ROOT_NOT_EXPLICITLY_DECLARED_IN_IMMUTABLE_TOOLKIT_ALLOWED_OUTPUT_ROOTS')
    if toolkit['compiler_config']['tmp_dir'] != str(args.tmp):
        raise ValueError('Editor and immutable toolkit must use the same explicit real TMPDIR')
    if snapshot['scope'] != 'complete':
        gaps.append('SOURCE_SNAPSHOT_IS_A_SEALED_MINI_REPLAY_ONLY')
    if gaps and not allow_pending:
        raise ValueError('Editor native prerequisites incomplete: ' + repr(gaps))
    identity = {'recipe': proof['input_lock_sha256'], 'source': args.source_snapshot_sha256,
                'dependencies': args.dependencies_sha256, 'toolkit': args.toolkit_sha256,
                'host_runtime': args.host_runtime_sha256, 'profile': args.profile}
    return proof, snapshot, deps, toolkit, host, identity, gaps


def bind_context(root, identity):
    path = root / 'native-context.json'
    if path.exists():
        if load(path) != identity:
            raise ValueError('Refuse changed source/toolkit/prefix/host/profile context in existing native build')
    else:
        if any((root / name).exists() for name in ('build', 'install')):
            raise ValueError('Refuse adopting preexisting native build or installation')
        dump(path, identity)


def graph_check(args, root, planned, deps, toolkit, run, compiled):
    selected = dict(planned['profile']); selected['require_compiled_objects'] = compiled
    selected['compiler_paths'] = {'C': toolkit['paths']['cc_launcher'], 'CXX': toolkit['paths']['cxx_launcher']}
    prefixes = dict(deps['prefixes'])
    compiler = load(toolkit['paths']['compiler_config'])
    flags = compiler['flags']['cxx']
    sdk = [x.split('=', 1)[1] for x in flags if x.startswith('--sysroot=')]
    if len(sdk) != 1:
        raise ValueError('Toolkit actual SDK sysroot is ambiguous')
    prefixes['sdk'] = str(Path(sdk[0]).parent)
    result = audit_graph(args.repo, root / 'build', selected, prefixes, run)
    expected = ('PASS_GENERATED_GRAPH' if args.profile == 'final' else 'PASS_DIAGNOSTIC_GRAPH') if compiled else 'PASS_GENERATED_PLAN'
    if result['status'] != expected:
        raise ValueError('Actual editor generated graph failed: ' + repr(result['gaps']))
    return result


def execute(args):
    with owned(args) as (root, seal):
        attempts = root / 'attempts'; attempts.mkdir(exist_ok=True)
        indexes = [int(p.name.split('-')[0]) for p in attempts.iterdir()]
        folder = attempts / f'{max(indexes, default=0) + 1:06d}-{args.stage}'
        folder.mkdir()
        current = root / 'current'; current.mkdir(exist_ok=True)
        previous = folder / 'previous-current'; previous.mkdir()
        for pointer in current.glob('*.json'):
            pointer.rename(previous / pointer.name)
        request = {'stage': args.stage, 'input_lock_sha256': seal, 'root': str(root), 'repo': str(args.repo),
                   'profile': args.profile, 'native_full': 'NOT_RUN', 'bpy': 'NOT_RUN', 'HAP': 'NOT_RUN'}
        dump(folder / 'request.json', request)
        try:
            verify_recipe(args.repo)
            if args.stage == 'prepare':
                result = {'status': 'PASS_SOURCE_PREPARED', 'sse2neon': prepare_sse(args.repo, root, args.tmp),
                          'native_full': 'NOT_RUN', 'bpy': 'NOT_RUN', 'HAP': 'NOT_RUN'}
            else:
                if (os.uname().sysname, os.uname().machine) != ('HarmonyOS', 'aarch64'):
                    raise ValueError('Actual native HarmonyOS/aarch64 host required')
                proof, snapshot, deps, toolkit, host, identity, gaps = context(args)
                bind_context(root, identity)
                sse = prepare_sse(args.repo, root, args.tmp)
                planned = plan(args.repo, root, deps['prefixes'], toolkit, host, args.profile, args.jobs, Path(sse['source_directory']))
                project_hook(root, toolkit, planned['profile'])
                dump(folder / 'commands.json', planned)
                run = Runner(folder, args.tmp, toolkit)
                run.env['PKG_CONFIG_LIBDIR'] = os.pathsep.join(str(Path(p) / d) for p in deps['prefixes'].values() for d in ('lib/pkgconfig', 'share/pkgconfig'))
                result = {'status': 'RUNNING', 'identity': identity, 'source_snapshot': snapshot, 'native_full': 'NOT_RUN', 'bpy': 'NOT_RUN', 'HAP': 'NOT_RUN'}
                if args.stage in ('configure', 'full'):
                    if (root / 'build').exists():
                        raise ValueError('Configure requires a fresh owned build directory; use a new editor root')
                    create_query(root / 'build')
                    run(planned['commands']['configure'], 'formal-configure')
                    result['configured_graph'] = graph_check(args, root, planned, deps, toolkit, run, False)
                if args.stage in ('build', 'full'):
                    if not (root / 'build/build.ninja').is_file():
                        raise ValueError('Real configured Ninja graph missing')
                    graph_check(args, root, planned, deps, toolkit, run, False)
                    run(planned['commands']['build'], 'formal-native-build')
                    result['compiled_graph'] = graph_check(args, root, planned, deps, toolkit, run, True)
                if args.stage in ('install', 'full'):
                    if (root / 'install').exists():
                        raise ValueError('Refuse existing/partial installation; use fresh owned root')
                    result['compiled_graph'] = graph_check(args, root, planned, deps, toolkit, run, True)
                    run(planned['commands']['install'], 'formal-install-relink')
                    result['runtime_composition'] = compose_runtime(root / 'install', Path(deps['prefixes']['python_native']), Path(deps['prefixes']['pure_resources']), deps['records'])
                    result['final_signatures'] = sign_installed(root / 'install', toolkit, args.tmp, run)
                    dump(root / 'final-signatures.json', result['final_signatures'])
                if args.stage in ('audit', 'full', 'probe', 'bpy', 'bpy-gltf'):
                    result['compiled_graph'] = graph_check(args, root, planned, deps, toolkit, run, True)
                    result['final_elf'] = audit_install(root / 'install', root / 'install/5.2/python', toolkit, host, run)
                    result['final_signatures'] = load(root / 'final-signatures.json')
                    signed = {row['signed_output']['path']: row['signed_output']['sha256'] for row in result['final_signatures']}
                    for rel, info in result['final_elf']['elf'].items():
                        if signed.get(str(root / 'install' / rel)) != info['sha256']:
                            raise ValueError('Final installed ELF lost actual final signer lineage')
                if args.stage == 'probe':
                    python = root / 'install/5.2/python'
                    core = root / 'install/lib/libblender_core.so'
                    provider = python / 'lib/libpython3.13.so'
                    env = run.env.copy(); env['LD_LIBRARY_PATH'] = os.pathsep.join([str(root / 'install/lib'), str(python / 'lib'), *host.get('native_library_dirs', [])])
                    output = run([python / 'bin/python3.13', '-I', '-B', '-S', HERE / 'abi_probe.py', '--core', core, '--core-sha256', sha(core),
                                  '--python-provider', provider, '--python-provider-sha256', sha(provider)], 'actual-core9-abi2-loader-probe', env)
                    result['core_probe'] = json.loads(output)
                    if result['core_probe']['status'] != 'PASS_ACTUAL_CORE_DLOPEN_ABI2_CORE9':
                        raise ValueError('Actual core loader probe did not pass')
                if args.stage in ('bpy', 'bpy-gltf'):
                    from acceptance_runner import accept
                    result['bpy_acceptance'] = accept(args, root, deps, toolkit, host, run, compressed=args.stage == 'bpy-gltf')
                # Detect changed caller inputs before publishing any current successful pointer.
                context(args)
                if result.get('final_elf'):
                    for rel, info in result['final_elf']['elf'].items():
                        if sha(root / 'install' / rel) != info['sha256']:
                            raise ValueError('Final signed ELF changed before publication')
                result['status'] = {'full': 'PASS_NATIVE_BUILD_FINAL_ELF_ONLY', 'probe': 'PASS_CORE_NATIVE_LOADER_ONLY',
                                    'bpy': 'PASS_ACTUAL_BPY_FIVE_STAGES', 'bpy-gltf': 'PASS_ACTUAL_COMPRESSED_BPY_ONLY'}.get(args.stage, 'PASS_' + args.stage.upper())
                if args.stage == 'full':
                    result['native_full'] = 'PASS_NATIVE_BUILD_FINAL_ELF_ONLY; core probe/bpy/SDL/HAP separate'
            result.update(stage=args.stage, input_lock_sha256=seal, attempt=folder.relative_to(root).as_posix())
            dump(folder / 'result.json', result)
            dump(current / (args.stage + '.json'), {'attempt': result['attempt'], 'receipt_sha256': sha(folder / 'result.json'),
                                                   'input_lock_sha256': seal, 'status': result['status']})
            return result
        except BaseException as error:
            dump(folder / 'result.json', {**request, 'status': 'FAIL', 'error': str(error), 'traceback': traceback.format_exc()})
            raise


def main(argv=None):
    args = parse(argv)
    if args.stage == 'verify-inputs':
        result = verify_recipe(args.repo); _, manifest, expected = verify_sse(args.repo)
        result.update(status='PASS_SEALED_SOURCE_INPUTS_ONLY', sse2neon_archive_sha256=manifest['sha256'], sse2neon_source_files=len(expected['files']), native_full='NOT_RUN')
    elif args.stage == 'snapshot':
        destination = absolute(args.destination, exists=False)
        if destination.exists() or destination.is_relative_to(args.repo):
            raise ValueError('Snapshot output must be absent and outside source')
        rows = inventory(args.repo, exclude_git=True)
        result = {'schema': 1, 'kind': 'editor-source-snapshot', 'scope': 'complete', 'source_commit': args.source_commit,
                  'tree_sha256': digest(rows), 'files': rows}
        dump(destination, result)
        result = {'status': 'PASS_SOURCE_SNAPSHOT_ONLY', 'snapshot': record(destination), 'source_tree_sha256': result['tree_sha256'], 'files': len(rows)}
    elif args.stage == 'copy-inputs':
        verify_recipe(args.repo)
        destination = absolute(args.destination, exists=False)
        if destination.exists() or not destination.is_relative_to(args.tmp) or destination == args.tmp:
            raise ValueError('Mini replay destination must be absent under explicit TMPDIR')
        destination.mkdir(parents=True)
        try:
            lock = args.repo / 'build_files/ohos/editor/inputs.lock.json'
            for row in load(lock)['sealed_files'] + [{'path': lock.relative_to(args.repo).as_posix()}]:
                target = destination / row['path']; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.repo / row['path'], target)
            result = verify_recipe(destination)
            result.update(status='PASS_SEALED_INPUT_MINI_COPY', destination=str(destination), native_full='NOT_RUN')
        except BaseException:
            shutil.rmtree(destination)
            raise
    elif args.stage == 'plan':
        proof, snapshot, deps, toolkit, host, identity, gaps = context(args, True)
        root = safe_root(args)
        result = plan(args.repo, root, deps['prefixes'], toolkit, host, args.profile, args.jobs, root / 'sources/sse2neon')
        result.update(identity=identity, gaps=gaps, ready_for_native=not gaps, source_snapshot=snapshot)
    else:
        result = execute(args)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('editor: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
