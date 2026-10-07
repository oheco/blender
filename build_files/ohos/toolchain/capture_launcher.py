# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite NO_RSP compiler/sign/publication capture; never native acceptance."""
from pathlib import Path
import json
import os
import struct
import sys
from capture_io import Directory, absolute, identity, read_original, regular, require, temporary
from capture_store import Store, MODEL
from env_policy import clean


def grammar(args, cwd):
    """A finite CMake CompilerId/ABI grammar; unknown routes fail closed."""
    require(not any('@' in arg for arg in args), 'NO_RSP protocol')
    outputs = []; inputs = []; primary = None; index = 0
    unary = {'-o', '-MF', '-MT', '-MQ'}
    plain = {'-c', '-E', '-S', '-v', '-g', '-g0', '-O0', '-O1', '-O2', '-O3', '-DNDEBUG', '-fPIC', '-fPIE', '-pie', '-shared',
             '-rdynamic', '-MD', '-MMD', '-w', '--version', '-print-sysroot', '-print-search-dirs', '-dumpmachine', '-dumpversion'}
    dry = any(arg in {'--version', '-print-sysroot', '-print-search-dirs', '-dumpmachine', '-dumpversion'} for arg in args)
    compile_only = any(arg in {'-c', '-E', '-S'} for arg in args)
    while index < len(args):
        arg = args[index]
        if arg in unary:
            require(index + 1 < len(args), 'Missing finite compiler operand'); value = args[index + 1]; index += 2
            if arg in {'-o', '-MF'}:
                require(value != '-', 'Special stdout output route unsupported')
                path = absolute(value) if Path(value).is_absolute() else absolute(cwd / value); outputs.append(path)
                if arg == '-o': require(primary is None, 'Duplicate compiler output'); primary = path
            continue
        if arg in plain or arg.startswith(('-std=', '-Werror=', '-Wno-')) or arg == '-Wl,--threads=1':
            index += 1; continue
        if not arg.startswith('-') and Path(arg).suffix in {'.c', '.cc', '.cpp', '.cxx', '.o', '.a'}:
            path = absolute(arg) if Path(arg).is_absolute() else absolute(cwd / arg); inputs.append(path); index += 1; continue
        raise ValueError('Unclassified finite ABI compiler token: ' + arg)
    link = not dry and not compile_only and bool(inputs)
    if primary is None and not dry:
        if link: primary = cwd / 'a.out'; outputs.append(primary)
        elif '-c' in args or '-S' in args:
            require(len(inputs) == 1, 'Ambiguous implicit output'); primary = cwd / (inputs[0].stem + ('.s' if '-S' in args else '.o')); outputs.append(primary)
    if not inputs and '-v' in args: dry = True
    require(dry or inputs, 'No original compiler input in finite route')
    require(not any(arg in {'-MD', '-MMD'} for arg in args) or '-MF' in args, 'Implicit dependency output unsupported without original -MF')
    require(not dry or not outputs, 'Dry query with mutable output unsupported')
    return {'link': link, 'dry': dry, 'primary': primary, 'outputs': list(dict.fromkeys(outputs)), 'inputs': inputs}


def preflight_outputs(build, outputs, inputs):
    from contextlib import ExitStack
    stack = ExitStack(); seen = set()
    try:
        for output in outputs:
            require(output.is_relative_to(build.root), 'Unregistered finite compiler output')
            require(all(str(output) != item['original']['resolved_path'] for item in inputs), 'Compiler output overlaps original source input')
            parent, name = stack.enter_context(build.parent(output))
            try: info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError: continue
            regular(info, True); inode = tuple(identity(info))
            require(inode not in seen and all(list(inode) != item['original']['identity'] for item in inputs), 'Mutable output borrows an original input/other output inode')
            seen.add(inode)
        return stack
    except BaseException: stack.close(); raise


def captured_compile(config_path, language, args, *, source_module=None, source_environment=None, model=False, clock=None, executor=None):
    raw, config_observed = read_original(config_path); config = json.loads(raw)
    require(language in ('c', 'cxx'), 'Unknown finite ABI language')
    binding = config['capture_owner']; root = absolute(binding['root'])
    module = Path(source_module or __file__)
    temporary_path = None; temp_cleaned = False; identifier = None; output_lease = None
    store = Store(root, binding['owner_reference'], model=model, clock=clock)
    try: build = Directory(root / 'capture-abi-build')
    except BaseException:
        store.close(); raise
    try:
        store.no_rsp(args, module)
        config_capture, same = store.snapshot(config_path, 'compiler-config-actual-read')
        require(same == raw, 'Compiler config changed after exact parsing')
        cwd = absolute(os.getcwd()) if not model else absolute(binding['model_cwd'])
        require(cwd.is_relative_to(build.root), 'Compiler actual cwd outside finite ABI build')
        cwd_fd = Directory(cwd)
        try: cwd_identity = cwd_fd.identity
        finally: cwd_fd.close()
        try: route = grammar(args, cwd)
        except ValueError as error:
            store.event({'kind': 'rejected-command-protocol', 'raw_argv': args, 'reason': str(error), 'language': language,
                         'config_original': config_capture, 'protocol': 'FINITE_NO_RSP', 'status': 'NOT_READY'}); raise
        for output in route['outputs']: require(output.is_relative_to(build.root), 'Unregistered finite compiler output')
        originals = [store.snapshot(path, 'compiler-input-occurrence')[0] for path in route['inputs']]
        output_lease = preflight_outputs(build, route['outputs'], originals)
        require(not route['inputs'] or len(route['inputs']) <= 64, 'Finite ABI source occurrence bound exceeded')
        defaults = config['flags'][language][:]
        if not route['link']:
            defaults = [arg for arg in defaults if arg not in ('-static-libstdc++', '-lc++experimental') and
                        not arg.startswith(('-fuse-ld=', '--ld-path=', '-Wl,', '-L'))]
        environment, removed = clean(source_environment, tmp_dir=config['tmp_dir'])
        identifier = store.event({'kind': 'compiler-wrapper-context', 'language': language, 'raw_incoming_argv': args,
                       'default_flags_before_filter': config['flags'][language], 'actual_defaults': defaults,
                       'actual_inner_argv': [config['compilers'][language], *defaults, *args], 'config_original': config_capture,
                       'source_inputs': originals, 'cwd': str(cwd), 'cwd_identity': cwd_identity,
                       'cleared_before_dispatch': removed, 'response_protocol': 'NO_RSP', 'actual_linker_descendant': 'NOT_OBSERVED_NOT_READY',
                       'parent_configure_dispatch': store.session.get('configure_dispatch', 'CORRELATE_SESSION_EVENT_INTERVAL'),
                       'source_callsite': store.callsite('compiler', module)})
        primary = route['primary']
        if route['link'] and primary.exists():
            before, _ = store.snapshot(primary, 'old-output-before-unlink'); started = store.clock.now(); build.unlink(primary)
            store.event({'kind': 'remove-old-output', 'output_before': before, 'started_ns': started, 'finished_ns': store.clock.now()})
        try:
            result = store.dispatch([config['compilers'][language], *defaults, *args], environment, cwd, 'compiler', module, executor=executor)
        except BaseException:
            partial = [store.snapshot(output, 'partial-compiler-output-before-error')[0] for output in route['outputs'] if output.exists()]
            store.event({'kind': 'compiler-exception-output-retention', 'language': language, 'outputs': partial, 'status': 'NOT_READY'})
            raise
        outputs = []
        for output in route['outputs']:
            if output.exists(): outputs.append(store.snapshot(output, 'actual-compiler-output-before-sign')[0])
        store.event({'kind': 'compiler-output-observations', 'language': language, 'compiler_dispatch': result['record'],
                     'wrapper_context': identifier, 'outputs': outputs, 'actual_exit': result['exit']})
        if result['exit'] != 0: return result
        if not route['link'] or primary is None or not primary.is_file(): return result
        unsigned, data = store.snapshot(primary, 'unsigned-pre-sign-original')
        if data[:4] != b'\x7fELF':
            store.event({'kind': 'unsupported-output', 'unsigned': unsigned, 'reason': 'Linked original is not ELF', 'status': 'NOT_READY'}); return result
        require(len(data) >= 20 and data[4:6] == b'\x02\x01', 'Native signing structure expected')
        elf_type, machine = struct.unpack_from('<HH', data, 16)
        require(elf_type in (2, 3) and machine == 183, 'ABI linked output structure differs')
        sign_env, sign_removed = clean(source_environment, tmp_dir=config['tmp_dir'])
        lifecycle_started = store.clock.now()
        with temporary(config['tmp_dir']) as temp:
            temporary_path = temp.root; signed = temp.root / 'signed'
            create_ref = store.event({'kind': 'actual-temp-create', 'path': str(temp.root), 'directory_identity': temp.identity,
                         'managed_TMPDIR': config['tmp_dir'], 'started_ns': lifecycle_started, 'created_ns': store.clock.now(),
                         'scope': store.scope, 'parent_unsigned': unsigned})
            try:
                sign = store.dispatch([config['signer'], 'sign', '-inFile', str(primary), '-outFile', str(signed), '-selfSign', '1'],
                                      sign_env, cwd, 'sign', module, executor=executor)
            except BaseException:
                partial = store.snapshot(signed, 'partial-sign-temp-before-exception-cleanup')[0] if signed.exists() else None
                store.event({'kind': 'sign-exception-output-retention', 'unsigned_input': unsigned, 'signed_tmp': partial,
                             'temp_creation': create_ref, 'status': 'NOT_READY', 'gap': None if partial else 'NO_PARTIAL_SIGN_OUTPUT'})
                raise
            if signed.exists(): signed_capture, signed_bytes = store.snapshot(signed, 'actual-TMPDIR-sign-output-original')
            else: signed_capture = None; signed_bytes = b''
            store.event({'kind': 'sign-output-before-error-or-cleanup', 'unsigned_input': unsigned, 'sign_dispatch': sign['record'],
                         'signed_tmp': signed_capture, 'temp_creation': create_ref, 'cleared_before_sign': sign_removed})
            require(sign['exit'] == 0 and signed_capture is not None, 'Actual signing child failed/no signed output')
            query = store.dispatch([config['readelf'], '--file-header', '--sections', str(signed)], sign_env, cwd,
                                   'signature-query', module, executor=executor)
            output = query['stdout'].decode(errors='replace')
            require(query['exit'] == 0 and 'AArch64' in output and '.codesign' in output and signed_bytes[:4] == b'\x7fELF' and
                    len(signed_bytes) >= 20 and struct.unpack_from('<HH', signed_bytes, 16) == (elf_type, 183), 'Producer signed structure/query differs')
            unchanged, original_after = read_original(primary)
            require(unchanged == data, 'Unsigned bytes changed during signer/query')
            with temp.parent(signed) as (fd, name):
                leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try: regular(os.fstat(leaf), True); os.fchmod(leaf, 0o755)
                finally: os.close(leaf)
            transfer_started = store.clock.now(); build.unlink(primary)
            transfer_kind = build.move_from(temp, signed, primary)
            installed, installed_bytes = store.snapshot(primary, 'installed-signed-output-before-CMake-copy')
            require(installed_bytes == signed_bytes, 'Actual published bytes differ from signed temp')
            store.event({'kind': 'actual-signed-publication', 'language': language, 'compiler_dispatch': result['record'],
                         'unsigned': unsigned, 'signed_tmp': signed_capture, 'installed': installed,
                         'sign_dispatch': sign['record'], 'query_dispatch': query['record'], 'temp_creation': create_ref,
                         'transfer': {'kind': transfer_kind, 'from_path': str(signed), 'to_path': str(primary),
                                      'started_ns': transfer_started, 'finished_ns': store.clock.now(),
                                      'source_callsite': store.callsite('transfer', module)},
                         'signature_status': 'PRODUCER_STRUCTURAL_CHECK_ONLY_NOT_ATTESTED', 'CMake_ABI_COPY': 'NOT_OBSERVED_NOT_READY'})
        temp_cleaned = not temporary_path.exists()
        return result
    except BaseException as error:
        store.event({'kind': 'compiler-wrapper-failure', 'language': language, 'wrapper_context': identifier,
                     'type': type(error).__name__, 'message': str(error), 'raw_argv': list(args), 'status': 'NOT_READY'})
        raise
    finally:
        try:
            if temporary_path is not None:
                store.event({'kind': 'actual-temp-cleanup', 'path': str(temporary_path), 'removed': not temporary_path.exists(),
                             'cleanup_confirmed': temp_cleaned or not temporary_path.exists(), 'observed_ns': store.clock.now(),
                             'status': 'RECORDED' if not temporary_path.exists() else 'NOT_READY_CLEANUP_INCOMPLETE'})
        finally:
            try:
                if output_lease is not None: output_lease.close()
            finally:
                try: build.close()
                finally: store.close()


def main(config_path, language, args):
    result = captured_compile(config_path, language, args)
    sys.stdout.buffer.write(result['stdout']); sys.stderr.buffer.write(result['stderr'])
    return result['exit']
