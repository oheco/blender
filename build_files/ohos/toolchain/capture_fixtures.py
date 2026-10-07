# SPDX-License-Identifier: GPL-2.0-or-later
"""One focused SOURCE mock/IO batch. No producer CLI/SDK/native subprocess."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import capture_io as io
from capture_store import Store, Clock, MODEL, CaptureFailure
import capture_launcher as launcher
from abi_capture import scalar, capture_generated, parent_context, NAMES
from toolkit_io import REPO


def row(path):
    data = Path(path).read_bytes(); return {'path': str(path), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path


def events(root): return [json.loads(path.read_text()) for path in sorted((root / 'native-fact-captures/events').glob('*.json'))]


def preserved(item): return Path(item['preserved']['path']).read_bytes()


class Fixture:
    def __init__(self, base, number):
        self.root = base / ('模型 Toolkit ' + str(number)); self.root.mkdir(); self.build = self.root / 'capture-abi-build'; self.build.mkdir()
        self.tools = {}
        for name in ('cc', 'cxx', 'signer', 'readelf', 'cmake'):
            self.tools[name] = write(self.root / 'input-tools' / name, b'SOURCE_MODEL_TOOL_UNEXECUTED_' + name.encode())
        self.source = write(self.build / 'source.c', b'/* SOURCE_MODEL_UNEXECUTED_C_INPUT */\nint main(){return 0;}\n')
        self.output = self.build / 'abi-output'
        owner = {'kind': 'provided-native-ohos-toolkit-owner', 'root': str(self.root), 'recipe_inputs_sha256': 'SOURCE_MODEL_NO_RECIPE'}
        self.owner_ref = row(write(self.root / 'owner.json', (json.dumps(owner) + '\n').encode()))
        self.clock = Clock(model=True)
        self.protocol = {'source_recipe': {'scope': MODEL}, 'recipe_input_lock': {'scope': MODEL},
                         'modules': {name: row(Path(__file__).parent / name) for name in
                                     ('capture_launcher.py', 'capture_store.py', 'toolkit_io.py')}}
        self.store = Store(self.root, self.owner_ref, create=True, context={'scope': MODEL}, protocol=self.protocol,
                           model=True, clock=self.clock)
        self.config = {'compilers': {'c': str(self.tools['cc']), 'cxx': str(self.tools['cxx'])},
                       'flags': {'c': ['--target=SOURCE_MODEL', '--sysroot=SOURCE_MODEL_SDK', '-resource-dir=SOURCE_MODEL', '--ld-path=SOURCE_MODEL_LLD', '-Wl,--threads=1', '-LSOURCE_MODEL'],
                                 'cxx': ['--driver-mode=g++', '--target=SOURCE_MODEL', '--sysroot=SOURCE_MODEL_SDK', '-resource-dir=SOURCE_MODEL', '--ld-path=SOURCE_MODEL_LLD', '-Wl,--threads=1', '-LSOURCE_MODEL', '-nostdinc++', '-isystem', 'SOURCE_MODEL_CXX_INCLUDE', '-fexperimental-library', '-static-libstdc++', '-lc++experimental']},
                       'tmp_dir': os.environ['TMPDIR'], 'signer': str(self.tools['signer']), 'readelf': str(self.tools['readelf']),
                       'capture_owner': {'root': str(self.root), 'owner_reference': self.owner_ref, 'model_cwd': str(self.build)}}
        self.config_path = write(self.root / 'profile/compiler.json', json.dumps(self.config).encode())
        self.calls = []; self.mode = 'success'
        self.environment = {'PATH': '/SOURCE_MODEL_PATH', 'CC': 'bad selector before clear', 'LD_PRELOAD': 'bad injection before clear', 'CFLAGS': '-DFAKE=1'}

    def close(self): self.store.close()

    def execute(self, argv, **kwargs):
        self.calls.append({'argv': argv, 'env': kwargs['env'], 'cwd': kwargs['cwd']})
        if argv[0] in (str(self.tools['cc']), str(self.tools['cxx'])):
            output = Path(argv[argv.index('-o') + 1]) if '-o' in argv else self.build / 'a.out'
            if not output.is_absolute(): output = self.build / output
            data = bytearray(b'\x7fELF\x02\x01' + bytes(58)); struct.pack_into('<HH', data, 16, 2, 183)
            data.extend(b'SOURCE_MODEL_UNSIGNED_NEVER_EXECUTED'); write(output, bytes(data))
            if '-MF' in argv: write(self.build / argv[argv.index('-MF') + 1], b'SOURCE_MODEL_DEPENDENCIES')
            if self.mode == 'compiler_timeout': raise subprocess.TimeoutExpired(argv, 1, output=b'SOURCE_MODEL_PARTIAL_CC_LOG')
            return SimpleNamespace(returncode=17 if self.mode == 'compiler_exit' else 0, stdout=b'SOURCE_MODEL_COMPILER_RAW_STDOUT', stderr=b'SOURCE_MODEL_COMPILER_RAW_STDERR')
        if argv[0] == str(self.tools['signer']):
            source = Path(argv[argv.index('-inFile') + 1]); dest = Path(argv[argv.index('-outFile') + 1])
            write(dest, source.read_bytes() + b'SOURCE_MODEL_SIGNED_NEVER_EXECUTED')
            if self.mode == 'sign_timeout': raise subprocess.TimeoutExpired(argv, 1, output=b'SOURCE_MODEL_PARTIAL_SIGN_LOG')
            return SimpleNamespace(returncode=23 if self.mode == 'sign_exit' else 0, stdout=b'SOURCE_MODEL_SIGN_RAW_STDOUT', stderr=b'SOURCE_MODEL_SIGN_RAW_STDERR')
        if argv[0] == str(self.tools['readelf']):
            return SimpleNamespace(returncode=0, stdout=b'SOURCE_MODEL_ONLY AArch64 .codesign', stderr=b'SOURCE_MODEL_ONLY_NOT_ELF_VERIFICATION')
        return SimpleNamespace(returncode=0, stdout=b'SOURCE_MODEL_RAW_LOG', stderr=b'SOURCE_MODEL_RAW_ERROR')

    def compile(self, args=None, language='c'):
        return launcher.captured_compile(self.config_path, language, args or [str(self.source), '-o', str(self.output)],
                                          model=True, clock=self.clock, source_environment=self.environment, executor=self.execute)


def run():
    receipts = []; count = 0
    def check(label, action, rejected=False):
        nonlocal count
        count += 1
        try:
            action()
            if rejected: raise AssertionError('Expected source rejection: ' + label)
        except (ValueError, OSError, CaptureFailure) as error:
            if not rejected: raise
            receipts.append({'name': label, 'status': 'PASS_EXPECTED_REJECTION', 'actual_exception': type(error).__name__, 'message': str(error)})
        else: receipts.append({'name': label, 'status': 'PASS_SOURCE_MODEL_IO'})
    def expect(condition):
        if not condition: raise AssertionError('SOURCE model/IO condition failed')
    with tempfile.TemporaryDirectory(prefix='toolkit-source-capture-suite-', dir=os.environ['TMPDIR']) as folder:
        base = Path(folder); fixtures = []
        def new():
            value = Fixture(base, len(fixtures)); fixtures.append(value); return value
        try:
            f = new(); result = f.compile(); all_events = events(f.root)
            check('explicit SOURCE model success has no native acceptance', lambda: expect(result['exit'] == 0 and all(e['scope'] == MODEL and e['native_acceptance'] == 'NOT_READY' for e in all_events)))
            publication = next(e for e in all_events if e['kind'] == 'actual-signed-publication')
            check('unsigned preserved before signer and before unlink', lambda: expect(preserved(publication['unsigned']).endswith(b'SOURCE_MODEL_UNSIGNED_NEVER_EXECUTED') and publication['unsigned']['captured_ns'] < publication['transfer']['started_ns']))
            check('signed temp preserved before publication/destruction', lambda: expect(preserved(publication['signed_tmp']) == preserved(publication['installed']) and publication['signed_tmp']['captured_ns'] < publication['transfer']['started_ns']))
            check('actual transfer event and original paths retained', lambda: expect(publication['transfer']['kind'] in ('rename', 'copyfile_then_unlink') and publication['transfer']['from_path'] != publication['transfer']['to_path']))
            cleanup = next(e for e in all_events if e['kind'] == 'actual-temp-cleanup')
            check('source fixture temp cleanup actual absence', lambda: expect(cleanup['removed'] and not Path(cleanup['path']).exists()))
            check('environment cleared before compiler and sign rather than retromasked', lambda: expect(all('CC' not in call['env'] and 'CFLAGS' not in call['env'] and 'LD_PRELOAD' not in call['env'] for call in f.calls)))
            check('original incoming/default/config occurrences retained', lambda: expect(any(e['kind'] == 'compiler-wrapper-context' and e['raw_incoming_argv'][0] == str(f.source) and preserved(e['config_original']) == f.config_path.read_bytes() for e in all_events)))
            check('compiler child raw stdout and stderr independently retained', lambda: expect(any(e['kind'] == 'producer-original-child-finish-prototype' and e['role'] == 'compiler' and Path(e['stdout']['path']).read_bytes() == b'SOURCE_MODEL_COMPILER_RAW_STDOUT' and Path(e['stderr']['path']).read_bytes() == b'SOURCE_MODEL_COMPILER_RAW_STDERR' for e in all_events)))
            check('mock child pid and clock domain never actual', lambda: expect(all(e.get('child_pid_scope', 'MOCK_UNEXECUTED') == 'MOCK_UNEXECUTED' and e['clock_domain']['host']['system'] == 'SOURCE_MODEL' for e in all_events)))
            check('SOURCE model requires explicit executor', lambda: f.store.dispatch([str(f.tools['cc'])], {}, f.build, 'compiler', Path(launcher.__file__)), True)
            g = new(); g.compile(['-c', str(g.source), '-o', str(g.output)], language='cxx')
            context_event = next(e for e in events(g.root) if e['kind'] == 'compiler-wrapper-context')
            check('CXX ordered SDK include pair and compile defaults exact original config', lambda: expect(context_event['actual_defaults'][-5:] == ['-nostdinc++', '-isystem', 'SOURCE_MODEL_CXX_INCLUDE', '-fexperimental-library'][-5:] or context_event['actual_defaults'] == ['--driver-mode=g++', '--target=SOURCE_MODEL', '--sysroot=SOURCE_MODEL_SDK', '-resource-dir=SOURCE_MODEL', '-nostdinc++', '-isystem', 'SOURCE_MODEL_CXX_INCLUDE', '-fexperimental-library']))
            check('CXX child source is selected CXX not foreign Python compiler guess', lambda: expect(g.calls[0]['argv'][0] == str(g.tools['cxx']) and len(g.calls) == 1))
            g = new(); g.environment['CMAKE_UNKNOWN_SELECTOR'] = 'unclassified'
            check('unknown ambient selector refuses before any compiler', lambda: g.compile(), True)
            check('unknown ambient selector cleared no retrospective label', lambda: expect(not g.calls))
            for token in ('@missing', '-Wl,@embedded', '-Wa,x,@embedded'):
                g = new(); check('NO_RSP refuses raw ' + token, lambda g=g, token=token: g.compile([token]), True)
                check('NO_RSP refusal before any mock child dispatch ' + token, lambda g=g: expect(not g.calls))
            for label, args in [('missing explicit dependency', ['-c', str(f.source), '-MD', '-o', str(f.build / 'out.o')]),
                                ('stdout primary', [str(f.source), '-o', '-']), ('stdout dependency', ['-c', str(f.source), '-MF', '-']),
                                ('compiler config override', [str(f.source), '--config=/unbound']), ('target override', [str(f.source), '--target=fake']),
                                ('scalar macro override', [str(f.source), '-D__SIZEOF_POINTER__=8'])]:
                check('finite grammar ' + label, lambda args=args: launcher.grammar(args, f.build), True)
            g = new(); check('source path/output overlap refused before deletion', lambda: g.compile([str(g.source), '-o', str(g.source)]), True)
            check('original source still exists after overlap rejection', lambda: expect(g.source.read_bytes().startswith(b'/* SOURCE_MODEL')))
            g = new(); outside = write(base / 'borrowed-outside', b'ORIGINAL_BORROWED_BYTES'); os.symlink(outside, g.output)
            check('mutable output symlink refused before compiler', lambda: g.compile(['-c', str(g.source), '-o', str(g.output)]), True)
            check('borrowed leaf unchanged and compiler not called', lambda: expect(outside.read_bytes() == b'ORIGINAL_BORROWED_BYTES' and not g.calls))
            g = new(); shared = base / 'borrowed-dir'; shared.mkdir(); os.symlink(shared, g.build / 'alias-parent')
            check('mutable output symlink ancestor refused before compiler', lambda: g.compile(['-c', str(g.source), '-o', str(g.build / 'alias-parent/new.o')]), True)
            check('borrowed ancestor gained no compiler output', lambda: expect(not list(shared.iterdir()) and not g.calls))
            for mode, exit_expected in [('compiler_exit', 17), ('sign_exit', None), ('compiler_timeout', None), ('sign_timeout', None)]:
                g = new(); g.mode = mode
                if exit_expected is None: check('actual SOURCE failure preserved ' + mode, lambda g=g: g.compile(), True)
                else: check('actual SOURCE compiler nonzero preserved', lambda g=g: expect(g.compile()['exit'] == exit_expected))
                ev = events(g.root)
                if mode.startswith('sign'):
                    record = next(e for e in ev if e['kind'] in {'sign-output-before-error-or-cleanup', 'sign-exception-output-retention'})
                    check('partial signed bytes retained before cleanup ' + mode, lambda record=record: expect(b'SOURCE_MODEL_SIGNED' in preserved(record['signed_tmp'])))
                    check('sign failure temp actually cleaned ' + mode, lambda ev=ev: expect(next(e for e in ev if e['kind'] == 'actual-temp-cleanup')['removed']))
                else:
                    record = next(e for e in ev if e['kind'] in {'compiler-output-observations', 'compiler-exception-output-retention'})
                    check('partial unsigned output retained before outer failure ' + mode, lambda record=record: expect(record['outputs'] and b'SOURCE_MODEL_UNSIGNED' in preserved(record['outputs'][0])))
            g = new(); alias = g.root / 'tool-alias'; os.symlink('input-tools/cc', alias)
            check('selected readonly tool alias chain original', lambda: expect(io.read_original(alias, alias=True)[1]['readonly_aliases'][0]['target'] == 'input-tools/cc'))
            def borrowed_write():
                directory = io.Directory(g.root)
                try: directory.write(alias, b'bad', exclusive=False)
                finally: directory.close()
            check('readonly alias not permitted as mutable sink', borrowed_write, True)
            tricky = g.root / 'tricky-alias'; os.symlink('input-tools/../input-tools/cc', tricky)
            check('readonly alias non-leading parent before kernel resolution refused', lambda: io.read_original(tricky, alias=True), True)
            upward = g.build / 'leading-parent-alias'; os.symlink('../input-tools/cc', upward)
            check('readonly leading-parent alias exact object captured', lambda: expect(io.read_original(upward, alias=True)[1]['resolved_path'] == str(g.tools['cc'])))
            check('independent owner reference exact SHA required', lambda: Store(g.root, dict(g.owner_ref, sha256='0' * 64), model=True, clock=g.clock), True)
            clock = Clock(model=True); clock.domain['boot_id'] = 'DIFFERENT_MODEL_BOOT'
            check('original child boot domain mismatch refused', lambda: Store(g.root, g.owner_ref, model=True, clock=clock), True)
            require_module = dict(g.protocol['modules']['capture_launcher.py']); g.store.protocol['modules']['capture_launcher.py'] = dict(require_module, sha256='0' * 64)
            check('source callsite not in original recipe refused', lambda: g.store.callsite('compiler', Path(launcher.__file__)), True)
            for language, value in [('C', '4'), ('CXX', '16')]:
                check('generated language scalar source no default8 ' + language, lambda language=language, value=value: expect(scalar(('set(CMAKE_' + language + '_SIZEOF_DATA_PTR "' + value + '")\n').encode(), language) == value))
            bad = {'missing': b'set(OTHER 8)', 'case': b'set(cmake_c_sizeof_data_ptr "8")', 'message-data': b'message(\nset(CMAKE_C_SIZEOF_DATA_PTR "8")\n)',
                   'quoted-data': b'message("set(CMAKE_C_SIZEOF_DATA_PTR 8)")', 'comment': b'# set(CMAKE_C_SIZEOF_DATA_PTR "8")',
                   'conditional': b'if(FALSE)\nset(CMAKE_C_SIZEOF_DATA_PTR "8")\nendif()', 'function': b'function(f)\nset(CMAKE_C_SIZEOF_DATA_PTR "8")\nendfunction()',
                   'duplicate': b'set(CMAKE_C_SIZEOF_DATA_PTR "8")\nset(CMAKE_C_SIZEOF_DATA_PTR "8")', 'unset': b'set(CMAKE_C_SIZEOF_DATA_PTR "8")\nunset(CMAKE_C_SIZEOF_DATA_PTR)',
                   'literal-global': b'set(CMAKE_C_SIZEOF_DATA_PTR "8")\nset(CMAKE_SIZEOF_VOID_P 8)', 'malformed': b'set(CMAKE_C_SIZEOF_DATA_PTR "8)',
                   'unknown-ABI': b'set(CMAKE_C_SIZEOF_DATA_PTR "")', 'negative': b'set(CMAKE_C_SIZEOF_DATA_PTR "-8")',
                   'dynamic-target': b'set(CMAKE_C_SIZEOF_DATA_PTR "8")\nset(${k} "4")',
                   'dynamic-construction': b'string(CONCAT k CMAKE_C_SIZEOF_ DATA_PTR)\nset(CMAKE_C_SIZEOF_DATA_PTR "8")\nset(${k} "4")'}
            for label, data in bad.items(): check('generated scalar refuses ' + label, lambda data=data: scalar(data, 'C'), True)
            g = new(); gen = g.build / 'CMakeFiles/actual-model-version'; gen.mkdir(parents=True)
            write(gen / 'CMakeCCompiler.cmake', b'set(CMAKE_C_SIZEOF_DATA_PTR "4")\n')
            write(gen / 'CMakeCXXCompiler.cmake', b'set(CMAKE_CXX_SIZEOF_DATA_PTR "16")\n')
            generated, gaps = capture_generated(g.store, g.build)
            check('both original ABI language sources retained unequal NOT_READY', lambda: expect(set(generated) == {'C', 'CXX'} and gaps and all(preserved(item['source']) for item in generated.values())))
            write(gen / 'CMakeCXXCompiler.cmake', b'set(CMAKE_CXX_SIZEOF_DATA_PTR "4")\n'); generated, gaps = capture_generated(g.store, g.build)
            check('both original ABI scalar equality remains consumer NOT_READY', lambda: expect(not gaps and all(item['native_acceptance'] == 'NOT_READY' for item in generated.values())))
            other = base / 'independent-origin'; other.mkdir(); entry = row(write(other / 'actual-source-entry', b'SOURCE_MODEL_DIFFERENT_SOURCE_ORIGIN')); source_lock = row(write(other / 'original-source-lock', b'SOURCE_MODEL_PHYSICAL_SOURCE_LOCK'))
            original_ref = row(write(other / 'source-review', b'SOURCE_MODEL_NO_NATIVE_REVIEW'))
            parent = {'schema_version': 1, 'kind': 'parent-provided-toolkit-native-fact-context-prototype',
                      'producer': {'toolkit_root': str(g.root), 'producer_source_root': str(REPO), 'recipe': {'scope': MODEL}},
                      'consumer': {'source_root': str(other), 'input_lock': source_lock, 'output_root': str(other / 'future-output')},
                      'source_revision': original_ref, 'root_registry': original_ref, 'parent_clock_context': original_ref,
                      'all_nine_physical_refs': {name: {'entry': entry, 'inputs_lock': source_lock, 'source_review': original_ref, 'source_locks': [source_lock]} for name in NAMES},
                      'all_nine_native': 'NOT_READY'}
            context_path = other / 'independent-parent-context.json'
            def context(value): return row(write(context_path, json.dumps(value).encode()))
            check('independent physical source origin differs from produced artifact origin', lambda: expect(parent_context(context(parent), g.root, {'scope': MODEL})['all_nine_physical_refs']['base']['entry']['path'] == str(other / 'actual-source-entry')))
            for field in ('source_revision', 'root_registry', 'parent_clock_context'):
                wrong = dict(parent); wrong[field] = dict(original_ref, sha256='0' * 64)
                check('independent parent origin rejects ' + field, lambda wrong=wrong: parent_context(context(wrong), g.root, {'scope': MODEL}), True)
            wrong = dict(parent); wrong['producer'] = dict(parent['producer'], toolkit_root=str(other))
            check('selected toolkit root original parent differs', lambda: parent_context(context(wrong), g.root, {'scope': MODEL}), True)
            wrong = dict(parent); wrong['all_nine_physical_refs'] = dict(parent['all_nine_physical_refs']); wrong['all_nine_physical_refs'].pop('base')
            check('all-nine independent physical origin missing stays NOT_READY', lambda: parent_context(context(wrong), g.root, {'scope': MODEL}), True)
            wrong = dict(parent, all_nine_native='PASS')
            check('toolkit prototype cannot supply all-nine native PASS', lambda: parent_context(context(wrong), g.root, {'scope': MODEL}), True)
        finally:
            for fixture in fixtures: fixture.close()
    return {'status': 'PASS_NEW_CAPTURE_SOURCE_MOCK_IO_ONLY', 'controls': count, 'results': receipts,
            'native_SDK_or_producer_CLI_execution': 'NOT_RUN', 'all_nine_native': 'NOT_READY', 'consumer_protocol': 'NOT_ADMITTED', 'temporary_suite_root_cleaned': True}
