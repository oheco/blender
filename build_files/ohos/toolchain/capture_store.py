# SPDX-License-Identifier: GPL-2.0-or-later
"""When-executed original materials for the finite toolkit ABI prototype.

No material is an authority or native acceptance. SOURCE_MODEL uses an explicit
mock executor; the production source route has no injected executor.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from capture_io import Directory, absolute, identity, read_original, require, selected_owner, verify_reference

MODEL = 'SOURCE_MODEL_NOT_NATIVE'
ORIGINAL = 'WHEN_EXECUTED_ORIGINAL_MATERIAL_NOT_ATTESTED'
SITE_IDS = {'runner': 'toolkit_io.Runner.run', 'compiler': 'capture_launcher.captured_compile.compiler',
            'sign': 'capture_launcher.captured_compile.sign', 'signature-query': 'capture_launcher.captured_compile.readelf',
            'transfer': 'capture_launcher.captured_compile.publish', 'modules': 'capture_store.snapshot_cmake_modules'}


class Clock:
    def __init__(self, model=False):
        self.model = model; self.counter = 0
        if model:
            self.domain = {'scope': MODEL, 'host': {'system': 'SOURCE_MODEL', 'machine': 'SOURCE_MODEL'},
                           'boot_id': 'SOURCE_MODEL_BOOT', 'session_id': uuid.uuid4().hex, 'clock': 'monotonic_ns'}
            self.boot_bytes = b'SOURCE_MODEL_BOOT\n'
        else:
            host = os.uname(); boot = Path('/proc/sys/kernel/random/boot_id')
            try:
                fd = os.open(boot, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try: self.boot_bytes = os.read(fd, 4096)
                finally: os.close(fd)
                boot_id = self.boot_bytes.decode().strip()
                require(bool(boot_id), 'Empty original boot clock identity')
            except (OSError, ValueError, UnicodeError): self.boot_bytes = b''; boot_id = None
            self.domain = {'scope': ORIGINAL, 'host': {'system': host.sysname, 'machine': host.machine},
                           'boot_id': boot_id, 'boot_source': str(boot), 'session_id': uuid.uuid4().hex,
                           'clock': 'monotonic_ns', 'clock_domain_status': 'RECORDED' if boot_id else 'NOT_READY_MISSING_BOOT_SOURCE'}

    def wall(self): return self.counter if self.model else time.time_ns()

    def now(self):
        if self.model: self.counter += 1; return self.counter
        return time.monotonic_ns()


def directory_identity(path):
    directory = Directory(path)
    try: return directory.identity
    finally: directory.close()


class Store:
    def __init__(self, root, owner_reference, *, create=False, context=None, protocol=None, clock=None, model=False):
        self.owner, self.owner_value = selected_owner(root, owner_reference)
        self.root = self.owner.root; self.scope = MODEL if model else ORIGINAL
        self.capture_root = self.root / 'native-fact-captures'
        try:
            self.directory = self.owner.mkdir(self.capture_root) if create else Directory(self.capture_root)
            if create:
                require(context is not None and protocol is not None, 'Independent parent/source context required')
                self.clock = clock or Clock(model=model)
                require(self.clock.model == model, 'SOURCE clock cannot describe actual capture')
                self.session = {'kind': 'toolkit-native-fact-capture-session-prototype', 'scope': self.scope,
                                'owner_reference': owner_reference, 'owner_identity': self.owner.identity,
                                'capture_identity': self.directory.identity, 'selected_context': context, 'source_protocol': protocol,
                                'clock_domain': self.clock.domain, 'started_ns': self.clock.now(),
                                'all_nine_native': 'NOT_READY', 'consumer_acceptance': 'NOT_READY', 'source_materials_are_authority': False}
                self.boot_ref = self.directory.write(self.capture_root / 'boot-source.bin', self.clock.boot_bytes)
                self.session['boot_original'] = self.boot_ref
                self.directory.dump(self.capture_root / 'session.json', self.session)
            else:
                data, _ = read_original(self.capture_root / 'session.json'); self.session = json.loads(data)
                require(self.session['scope'] == self.scope and self.session['owner_reference'] == owner_reference and
                        self.session['owner_identity'] == self.owner.identity and self.session['capture_identity'] == self.directory.identity,
                        'Session materials differ from independently selected owner identity')
                self.clock = clock or Clock(model=model)
                require(self.clock.model == model, 'SOURCE clock cannot describe actual child capture')
                # Child host/boot are independently observed; session id is the
                # parent's original operation domain, never a retroactive mtime.
                require(self.clock.domain['host'] == self.session['clock_domain']['host'] and
                        self.clock.domain['boot_id'] == self.session['clock_domain']['boot_id'], 'Original child clock host/boot differs')
                self.clock.domain = self.session['clock_domain']
            self.selection = self.session['selected_context']; self.protocol = self.session['source_protocol']
        except BaseException:
            if hasattr(self, 'directory'): self.directory.close()
            self.owner.close(); raise

    def close(self): self.directory.close(); self.owner.close()

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest(); path = self.capture_root / 'bytes' / (digest + '.bin')
        try:
            current, observed = read_original(path)
            require(current == data, 'Captured byte-addressed material changed'); return {k: observed[k] for k in ('path', 'size', 'sha256', 'identity')}
        except FileNotFoundError:
            try: return self.directory.write(path, data)
            except FileExistsError:
                current, observed = read_original(path); require(current == data, 'Concurrent captured bytes differ')
                return {k: observed[k] for k in ('path', 'size', 'sha256', 'identity')}

    def snapshot(self, path, role, *, alias=False):
        before = self.clock.now(); data, observed = read_original(path, alias=alias)
        preserved = self.blob(data); after = self.clock.now()
        return {'kind': 'producer-original-byte-capture-prototype', 'scope': self.scope, 'role': role,
                'original': observed, 'preserved': preserved, 'read_started_ns': before, 'captured_ns': after,
                'clock_domain': self.clock.domain}, data

    def event(self, value):
        identifier = value.get('event_id') or uuid.uuid4().hex; value = dict(value, event_id=identifier, scope=self.scope,
                clock_domain=self.clock.domain, recorded_ns=self.clock.now(), recorded_wall_ns=self.clock.wall(), native_acceptance='NOT_READY')
        return self.directory.dump(self.capture_root / 'events' / (identifier + '.json'), value)

    def callsite(self, role, module):
        require(role in SITE_IDS, 'Unknown capture source callsite')
        capture, data = self.snapshot(module, 'source-callsite')
        expected = self.protocol['modules'].get(Path(module).name)
        require(expected is not None and capture['original']['sha256'] == expected['sha256'], 'Unsealed source emitter/callsite bytes')
        return {'id': SITE_IDS[role], 'module_original': capture, 'source_recipe': self.protocol['source_recipe'],
                'recipe_input_lock': self.protocol['recipe_input_lock']}

    def dispatch(self, argv, environment, cwd, role, module, *, input_data=None, timeout=None, executor=None):
        require(role in SITE_IDS and ((self.scope == MODEL and executor is not None) or
                                     (self.scope == ORIGINAL and executor is None)), 'SOURCE_MODEL requires explicit mock; ORIGINAL refuses injection')
        argv = list(map(str, argv)); cwd = absolute(cwd); environment = dict(environment)
        require(argv and all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items()), 'Actual argv/effective env required')
        tool = argv[0] if Path(argv[0]).is_absolute() else shutil.which(argv[0], path=environment.get('PATH', ''))
        require(tool is not None, 'Original effective PATH tool resolution unavailable')
        tool_capture, _ = self.snapshot(absolute(tool), 'dispatched-tool', alias=True)
        site = self.callsite(role, module)
        identifier = uuid.uuid4().hex; started = self.clock.now()
        start = {'kind': 'producer-original-child-start-prototype', 'event_id': identifier + '-start', 'child_id': identifier,
                 'role': role, 'argv': argv, 'cwd': str(cwd), 'environment': environment,
                 'effective_environment_sha256': hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest(),
                 'tool_resolution': {'requested': argv[0], 'effective_PATH': environment.get('PATH', ''), 'resolved': str(tool), 'original_tool': tool_capture},
                 'source_callsite': site, 'started_ns': started, 'parent_session': self.clock.domain['session_id'], 'pid': os.getpid(),
                 'cwd_original_identity': directory_identity(cwd),
                 'stdin': self.blob(input_data) if input_data is not None else {'kind': 'NO_STDIN_DEVNULL'}}
        start_ref = self.event(start)
        result = None; error = None; stdout = b''; stderr = b''; actual_exit = None; actual_pid = None
        cwd_directory = Directory(cwd)
        try:
            start['cwd_original_identity'] = cwd_directory.identity
            if executor is not None:
                result = executor(argv, cwd=str(cwd), env=environment, input=input_data,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
                stdout = result.stdout or b''; stderr = result.stderr or b''; actual_exit = result.returncode
            else:
                child = subprocess.Popen(argv, cwd=str(cwd), env=environment,
                                         stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    actual_pid = child.pid
                    self.event({'kind': 'actual-created-child', 'child_id': identifier, 'actual_pid': actual_pid,
                                'parent_pid': os.getpid(), 'start_record': start_ref, 'created_ns': self.clock.now(),
                                'owned_process_group': actual_pid, 'descendant_exec_trace': 'NOT_OBSERVED_NOT_READY'})
                    try: stdout, stderr = child.communicate(input_data, timeout=timeout)
                    except subprocess.TimeoutExpired as expired:
                        import signal
                        try: os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        try: stdout, stderr = child.communicate(timeout=5)
                        except subprocess.TimeoutExpired as remaining:
                            stdout = remaining.stdout or expired.stdout or b''; stderr = remaining.stderr or expired.stderr or b''
                        actual_exit = child.poll()
                        self.event({'kind': 'bounded-child-timeout-cleanup', 'child_id': identifier, 'owned_group': actual_pid,
                                    'direct_exit': actual_exit, 'cleanup_wait_seconds': 5,
                                    'escaped_descendant_cleanup': 'NOT_OBSERVED_NOT_READY'})
                        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
                    actual_exit = child.returncode
                finally:
                    for pipe in (child.stdin, child.stdout, child.stderr):
                        if pipe is not None: pipe.close()
                    if child.poll() is None:
                        child.kill()
                        try: child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.event({'kind': 'direct-child-reap-incomplete', 'child_id': identifier, 'status': 'NOT_READY'})
            cwd_directory.check()
            _, after_tool = read_original(absolute(tool), alias=True)
            require(after_tool['sha256'] == tool_capture['original']['sha256'] and
                    after_tool['identity'] == tool_capture['original']['identity'] and
                    after_tool['resolved_path'] == tool_capture['original']['resolved_path'], 'Selected executed tool drift during child')
        except BaseException as failure:
            error = {'type': type(failure).__name__, 'message': str(failure)}
            stdout = getattr(failure, 'stdout', None) or stdout or b''; stderr = getattr(failure, 'stderr', None) or stderr or b''
        finally: cwd_directory.close()
        finished = self.clock.now()
        if isinstance(stdout, str): stdout = stdout.encode()
        if isinstance(stderr, str): stderr = stderr.encode()
        terminal = {'kind': 'producer-original-child-finish-prototype', 'role': role, 'child_id': identifier,
                    'start_record': start_ref, 'started_ns': started, 'finished_ns': finished, 'actual_exit': actual_exit,
                    'exception': error, 'stdout': self.blob(stdout), 'stderr': self.blob(stderr), 'source_callsite': site,
                    'actual_child_pid': actual_pid, 'child_pid_scope': 'MOCK_UNEXECUTED' if self.scope == MODEL else 'ACTUAL_CREATED_CHILD',
                    'signal': -actual_exit if actual_exit is not None and actual_exit < 0 else None,
                    'timeout': error is not None and error['type'] == 'TimeoutExpired'}
        terminal_ref = self.event(terminal)
        if error is not None:
            raise CaptureFailure(terminal_ref, error)
        return {'record': terminal_ref, 'started_ns': started, 'finished_ns': finished, 'exit': actual_exit,
                'stdout': stdout, 'stderr': stderr, 'child_id': identifier}

    def no_rsp(self, argv, module):
        if any('@' in str(arg) for arg in argv):
            self.event({'kind': 'rejected-command-protocol', 'protocol': 'NO_RSP', 'raw_argv': list(map(str, argv)),
                        'reason': 'RSP occurrence unsupported; original LLVM expansion not captured', 'source_callsite': self.callsite('compiler', module)})
            raise ValueError('NO_RSP ABI capture refuses every @ occurrence before compiler dispatch')

    def finish(self, status, gaps, generated=None):
        value = {'kind': 'toolkit-native-abi-capture-prototype-result', 'scope': self.scope, 'status': status,
                 'started_ns': self.session['started_ns'], 'finished_ns': self.clock.now(), 'clock_domain': self.clock.domain,
                 'selected_context': self.selection, 'source_protocol': self.protocol, 'generated': generated or {},
                 'gaps': gaps, 'all_nine_native': 'NOT_READY', 'consumer_protocol_admission': 'NOT_READY', 'native_fact_acceptance': 'NOT_READY'}
        return self.directory.dump(self.capture_root / 'result.json', value)


class CaptureFailure(RuntimeError):
    def __init__(self, record, error):
        super().__init__('Original child failed: ' + error['type'] + ': ' + error['message']); self.record = record


def snapshot_cmake_modules(config_path, cmake_root, binary_root):
    raw, _ = read_original(config_path); config = json.loads(raw); binding = config['capture_owner']
    require(absolute(binary_root) == absolute(binding['root']) / 'capture-abi-build', 'CMake hook actual build differs')
    store = Store(binding['root'], binding['owner_reference'])
    try:
        original_root = absolute(cmake_root); modules = original_root / 'Modules'; captures = []
        for path in sorted(modules.rglob('*')):
            if path.is_file():
                capture, _ = store.snapshot(path, 'selected-CMake-original-module', alias=True); captures.append(capture)
        require(captures, 'Actual selected CMAKE_ROOT has no modules')
        store.event({'kind': 'cmake-before-project-module-capture', 'actual_CMAKE_ROOT': str(original_root),
                     'actual_binary_root': str(binary_root), 'modules': captures,
                     'source_callsite': store.callsite('modules', Path(__file__)),
                     'status': 'RAW_SOURCE_MATERIAL_ONLY_NOT_READY'})
    finally: store.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path); parser.add_argument('--cmake-root', required=True, type=Path)
    parser.add_argument('--binary-root', required=True, type=Path)
    args = parser.parse_args(); snapshot_cmake_modules(args.config, args.cmake_root, args.binary_root)
