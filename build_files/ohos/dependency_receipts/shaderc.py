#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent immutable adapter for a completed pinned native Vulkan full run."""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
CONSUMERS = ['library-acceptance', 'public-dependency-acceptance', 'blender-find-acceptance',
             'utility-acceptance', 'borrowed-handle-acceptance', 'public-package-acceptance',
             'glslang-package-acceptance', 'tools-package-acceptance', 'system-loader-acceptance']
PIC = ['libpic-' + n + '.so' for n in ['combined', 'reflect', 'utility', 'standalone']]
SENTINELS = {'include/shaderc/shaderc.h', 'include/shaderc/shaderc.hpp',
             'include/spirv-tools/libspirv.hpp', 'lib/libshaderc_combined.a'}

class NotReady(ValueError):
    pass


def digest(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rel(name):
    if not isinstance(name, str) or not name or '\\' in name or any(c in name for c in '\x00\r\n'):
        raise ValueError('Invalid canonical relative path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or path.as_posix() != name:
        raise ValueError('Noncanonical/traversing relative path')
    return Path(*path.parts)


def canonical(path):
    path = Path(path).absolute()
    if path != path.resolve() or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Aliases/symlinks/noncanonical input or output path: ' + str(path))
    return path


def regular(file):
    file = canonical(file)
    if not file.is_file() or not stat.S_ISREG(file.stat().st_mode):
        raise ValueError('Expected existing regular file: ' + str(file))
    return file


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('Duplicate JSON field: ' + key)
        result[key] = value
    return result


def load(file):
    value = json.loads(regular(file).read_text(encoding='utf-8'), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('Object-rooted receipt required')
    return value


def keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required.split()):
        raise ValueError('Unknown/incomplete receipt schema: expected ' + required)


def bound_file(file, sha256, size=None):
    file = regular(file)
    if not re.fullmatch('[0-9a-f]{64}', sha256) or digest(file) != sha256 or (size is not None and file.stat().st_size != size):
        raise ValueError('Declared file bytes/size changed: ' + str(file))
    return {'path': str(file), 'sha256': sha256, 'size': file.stat().st_size}


def self_seal():
    lock = load(HERE / 'inputs.lock.json')
    keys(lock, 'schema_version kind files')
    if lock['schema_version'] != 1 or lock['kind'] != 'shaderc-receipt-adapter-input-seal':
        raise ValueError('Unknown adapter seal')
    seen = set()
    for row in lock['files']:
        keys(row, 'path sha256 size')
        name = rel(row['path']).as_posix()
        if name in seen:
            raise ValueError('Duplicate sealed file')
        seen.add(name)
        bound_file(HERE / name, row['sha256'], row['size'])
    if seen != {p.name for p in HERE.iterdir() if p.is_file() and p.name != 'inputs.lock.json'}:
        raise ValueError('Unsealed/extra adapter input')
    return digest(HERE / 'inputs.lock.json')


def source_contract(source_root, source_sha):
    contract = load(HERE / 'contract.json')
    source_root = canonical(source_root)
    if source_sha != contract['source_input_lock_sha256']:
        raise ValueError('Unsupported Vulkan source revision; separate adapter review required')
    repo = source_root.parents[2]
    if source_root.relative_to(repo).as_posix() != contract['source_namespace']:
        raise ValueError('Explicit selected source namespace does not match contract')
    for name, expected in contract['reviewed_source_sha256'].items():
        bound_file(source_root / rel(name), expected)
    return contract, repo


def envelope(args):
    if args.job_receipt is None or args.job_receipt_sha256 is None:
        raise NotReady('No caller-pinned completed full-job envelope; native inputs remain unread')
    proof = bound_file(args.job_receipt, args.job_receipt_sha256)
    value = load(args.job_receipt)
    keys(value, 'schema_version kind status job_id exit_code command cwd source_root receipt_root prefix input_lock_sha256 stdout_log stdout_sha256 command_log_dir')
    if value['schema_version'] != 1 or value['kind'] != 'dsh-completed-native-vulkan-full' or not isinstance(value['job_id'], str) or not value['job_id'].startswith('bash-'):
        raise NotReady('Unknown/opaque executor receipt')
    if value['status'] != 'completed' or type(value['exit_code']) is not int or value['exit_code'] != 0:
        raise NotReady('Actual full job is pending or did not complete exit0')
    for name in ['source_root', 'receipt_root', 'prefix']:
        if canonical(value[name]) != canonical(getattr(args, name)):
            raise ValueError('Completed job binds different canonical inputs')
    if value['input_lock_sha256'] != args.source_lock_sha256:
        raise ValueError('Completed job source seal differs')
    command = value['command']
    if not isinstance(command, list) or len(command) < 3 or any(not isinstance(t, str) for t in command) or canonical(command[1]) != args.source_root / 'builder/builder.py' or command[2] != 'full':
        raise ValueError('Completed job is not this actual full builder invocation')
    allowed = {'--root', '--prefix', '--tmp-dir', '--jobs', '--runtime-timeout', '--sdk-root', '--cc', '--cxx', '--lld', '--resource-dir', '--signer', '--python', '--cmake', '--ninja', '--git', '--pkgconf', '--ctest', '--loader', '--ar', '--ranlib', '--readelf', '--nm'}
    flags, index = {}, 3
    while index < len(command):
        option = command[index]
        if option == '--resume':
            if option in flags:
                raise ValueError('Repeated completed-job option')
            flags[option] = True;index += 1;continue
        if option not in allowed or option in flags or index + 1 >= len(command):
            raise ValueError('Unknown/repeated/incomplete completed-job option')
        flags[option] = command[index + 1];index += 2
    for option, expected in {'--root': args.receipt_root, '--prefix': args.prefix,
                             '--sdk-root': args.sdk_root, '--resource-dir': args.resource_dir,
                             '--signer': args.signer}.items():
        selected = Path(flags.get(option, '/missing')).absolute()
        expected = Path(expected).absolute()
        if option not in flags or (selected != expected if option == '--signer' else canonical(selected) != canonical(expected)):
            raise ValueError('Actual completed job does not bind caller SDK/tool/root: ' + option)
    for name in ['ar', 'readelf', 'nm']:
        selected = Path(flags.get('--' + name, str(args.sdk_root / 'llvm/bin' / ('llvm-' + name))))
        if selected.absolute() != getattr(args, name).absolute():
            raise ValueError('Actual completed job and caller readonly tool differ')
    logs = canonical(value['command_log_dir'])
    if logs.parent != args.receipt_root / 'logs':
        raise ValueError('Explicit full-job command-log directory outside selected root')
    stdout = bound_file(Path(value['stdout_log']), value['stdout_sha256'])
    final = None
    with Path(value['stdout_log']).open('rb') as stream:
        for line in stream:
            if line.strip(): final = line.strip()
    if final != b'PASS actual requested stage: full':
        raise NotReady('Actual full stdout lacks final completed-stage marker')
    return {'receipt': proof, 'stdout': stdout, 'value': value}


def inventory(root, flavor='sources'):
    root = canonical(root)
    rows, folds = [], {}
    for file in sorted(root.rglob('*')):
        name = file.relative_to(root).as_posix();rel(name)
        folded = name.casefold()
        if folded in folds and folds[folded] != name:
            raise ValueError('Case-colliding current inventory')
        folds[folded] = name
        if file.is_symlink():
            target=os.readlink(file)
            if flavor=='prefix' and not Path(target).is_absolute() and file.resolve().is_relative_to(root) and file.resolve().is_file():
                rows.append({'path':name,'symlink':target});continue
            if flavor!='sdk': raise ValueError('Unowned/special source/prefix symlink entry')
        if not (file.is_dir() or file.is_file()): raise ValueError('Special current inventory entry')
        if file.is_file(): rows.append({'path': name, 'sha256': digest(file), 'size': file.stat().st_size})
    return rows


def validate_prefix(prefix, saved, source_sha):
    keys(saved, 'files input_lock_sha256')
    if saved['input_lock_sha256'] != source_sha:
        raise ValueError('Prefix lineage differs')
    names = set()
    for row in saved['files']:
        keys(row, 'path symlink' if 'symlink' in row else 'path sha256 size')
        name = rel(row['path']).as_posix()
        if name in SENTINELS and 'symlink' in row: raise ValueError('Canonical archive/public header cannot be an alias')
        if name in names: raise ValueError('Duplicate prefix row')
        names.add(name)
    if not SENTINELS.issubset(names) or inventory(prefix,'prefix') != saved['files']:
        raise ValueError('Whole canonical current prefix bytes/header closure differ')


def validate_receipts(receipts, contract):
    full = receipts['full-native-acceptance.json']
    keys(full, 'result input_lock_sha256 gpu_pixels_wsi_vma_device_allocation_blender_hap pic_modules_dlopened')
    if full != {'result': contract['full_result'], 'input_lock_sha256': contract['source_input_lock_sha256'],
                'gpu_pixels_wsi_vma_device_allocation_blender_hap': 'NOT_TESTED', 'pic_modules_dlopened': False} or type(full['pic_modules_dlopened']) is not bool:
        raise NotReady('Full receipt completion/scope differs')
    accepted = receipts['acceptance.json'];keys(accepted, 'consumers scope')
    artifacts = receipts['artifacts.json'];keys(artifacts, 'result archives installed_dsos compile sources pic_modules_dlopened static_cpp_runtime_module_ownership gpu_dispatch_draw_pixels_wsi')
    migration = receipts['migration.json'];keys(migration, 'result preserved_files consumers pic_modules_dlopened gpu_wsi_pixels temporary_tree_cleaned')
    if artifacts['result'] != contract['artifact_result'] or migration['result'] != contract['migration_result'] or artifacts['pic_modules_dlopened'] is not False or migration['pic_modules_dlopened'] is not False or migration['temporary_tree_cleaned'] is not True:
        raise NotReady('Artifact/migration/source completion differs')
    if migration['preserved_files'] != len(receipts['prefix-files.json']['files']):
        raise ValueError('Moved complete prefix count differs')
    for packet in [accepted, migration]:
        if len(packet['consumers']) != 2 or [c['mode'] for c in packet['consumers']] != ['CMAKE', 'PC']:
            raise ValueError('Both actual original/moved native consumer routes required')
        for consumer in packet['consumers']:
            keys(consumer, 'mode artifacts native_results actual_spirv_outputs temporary_fixtures_cleaned pic_modules_dlopened gpu_device_dispatch_render_pixels_wsi')
            if consumer['pic_modules_dlopened'] is not False or consumer['gpu_device_dispatch_render_pixels_wsi'] != 'NOT_TESTED':
                raise ValueError('Consumer scope differs')
            if set(consumer['native_results']) != set(CONSUMERS) or len(consumer['artifacts']) != 13 or {Path(a['path']).name for a in consumer['artifacts']} != set(CONSUMERS+PIC):
                raise ValueError('Incomplete/duplicate signed consumer/runtime closure')
            if consumer['native_results']['system-loader-acceptance']['runtime_loader'] != '/system/lib64/libvulkan.so':
                raise ValueError('Actual genuine system loader differs')
            if consumer['native_results']['library-acceptance'] != contract['library_features'] or any(type(consumer['native_results']['library-acceptance'].get(k)) is not type(v) for k,v in contract['library_features'].items()):
                raise NotReady('Actual ShaderC/SPIRV compile/validator/optimizer/error API receipt incomplete')
            for name in ['public-dependency-acceptance', 'blender-find-acceptance']:
                value = consumer['native_results'][name]
                required = ['publicSPIRVHeaders', 'propagatedReflectMacro', 'FindShaderCCombined', 'realShadercCompilerLifecycle', 'optimizerAndValidatorSymbols', 'reflectSymbols']
                keys(value, ' '.join(required + ['spirvVersion', 'spirvRevision']))
                if any(value[k] is not True for k in required): raise ValueError('Real public compiler/validator symbol gate missing')
            expected_spv = {'compute.spv', 'compute-optimized.spv', 'tile.spv', 'compute-shaderc-optimized.spv', 'tile-optimized.spv'}
            spv = consumer['actual_spirv_outputs']
            if consumer['temporary_fixtures_cleaned'] is not True or len(spv) != 5 or {r['path'] for r in spv} != expected_spv:
                raise ValueError('Actual shader output fixture closure missing')
            for row in spv:
                keys(row, 'path sha256 size')
                if not re.fullmatch('[0-9a-f]{64}',row['sha256']) or type(row['size']) is not int or row['size'] <= 20 or row['size'] % 4:
                    raise ValueError('Actual generated SPIRV fixture digest/size invalid')
    return artifacts


def source_rows_match(current, expected):
    for rows in [current,expected]:
        names=set();folds={}
        for row in rows:
            keys(row,'path sha256 size');name=rel(row['path']).as_posix()
            if name in names or (name.casefold() in folds and folds[name.casefold()]!=name):
                raise ValueError('Duplicate/case-alias complete source row')
            names.add(name);folds[name.casefold()]=name
    return sorted(current,key=lambda r:r['path'])==sorted(expected,key=lambda r:r['path'])


def source_modes(archive, archive_root):
    rows={}
    def member(name, mode):
        path=rel(name)
        if len(path.parts)<2 or path.parts[0]!=archive_root: raise ValueError('Archive source root differs')
        key=Path(*path.parts[1:]).as_posix()
        if key in rows: raise ValueError('Duplicate regular archive mode row')
        rows[key]=(mode & 0o755) or 0o644
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                if not item.is_dir():
                    mode=item.external_attr >> 16
                    if stat.S_IFMT(mode) not in [0,stat.S_IFREG]: raise ValueError('Special original ZIP source')
                    member(item.filename,mode)
    else:
        with tarfile.open(archive) as source:
            for item in source:
                if item.isfile(): member(item.name,item.mode)
                elif not item.isdir(): raise ValueError('Special original TAR source')
    return rows


def prepared_sources(source_root, repo, native_root, receipts, contract, mode_journal=None):
    lock = load(source_root / 'builder/inputs.lock.json')
    seen = set()
    for row in lock['sealed_files']:
        keys(row, 'path sha256 size');name = rel(row['path']).as_posix()
        if name in seen: raise ValueError('Duplicate source sealed input')
        seen.add(name);bound_file(repo / name, row['sha256'], row['size'])
    source_lock = load(source_root / 'builder/sources.lock.json')
    patches = {p['name']: p for p in source_lock['patches']}
    rows = []
    for dep in source_lock['sources']:
        name = dep['name']
        if name not in contract['source_groups']: raise ValueError('Unknown complete source group')
        original = load(repo / rel(dep['inventory']))
        if original['source_archive_sha256'] != dep['sha256']:
            raise ValueError('Inventory/archive source lineage differs')
        expected = original['files'];adapted = {f['path']: f['after_sha256'] for f in patches.get(name, {}).get('files', [])}
        source = native_root / 'sources' / name
        current = inventory(source)
        desired = [{'path': r['path'], 'sha256': adapted.get(r['path'], r['sha256']), 'size': (source / rel(r['path'])).stat().st_size} for r in expected]
        if not source_rows_match(current,desired):
            raise ValueError('Current complete original/patched prepared source bytes differ')
        archive=native_root / 'archives' / dep['archive_filename']
        bound_file(archive, dep['sha256'], dep['size'])
        modes=source_modes(archive,dep['archive_root'])
        if set(modes)!={r['path'] for r in expected}: raise ValueError('Complete regular source type/mode inventory differs')
        derived_names={'scripts/generators/safe_struct_generator.py','src/vulkan/vk_safe_struct_vendor.cpp'}
        for file_name,mode in modes.items():
            actual=(source/rel(file_name)).stat().st_mode & 0o777
            derived=(name=='vulkan-utility' and file_name in derived_names and file_name in adapted and mode==0o644 and actual==0o664)
            if actual!=mode and not derived: raise ValueError('Unreviewed source permission/execute/world-write difference')
            if derived and mode_journal is not None:
                mode_journal.append({'group':name,'path':file_name,'original_extraction_mode':'0644','actual_patched_mode':'0664',
                                     'after_sha256':adapted[file_name],'reason':'Pinned Git apply replacement adds group write; type/execute/world bits unchanged'})
        rows.append({'name':name,'records':len(expected),'verification_phase':'after-applicable-patches','applied_patches':int(name in patches)})
    if [r['name'] for r in rows] != contract['source_groups'] or receipts['sources.json']['sources'] != rows or receipts['artifacts.json']['sources'] != rows:
        raise ValueError('Full eight prepared-source receipt/inventory closure differs')
    return rows


def command_evidence(native_root, log_directory):
    result = []
    for file in sorted(log_directory.glob('*.json')):
        record = load(file)
        keys(record, 'command cwd expected_exit timeout_seconds exit_code timed_out log log_sha256')
        log = canonical(record['log'])
        if log != file.with_suffix('.log') or not log.is_relative_to(native_root / 'logs'):
            raise ValueError('Command log alias/outside native root')
        bound_file(log, record['log_sha256'])
        text = log.read_text(encoding='utf-8')
        header, rest = text.split('\n',1)
        before = json.loads(header,object_pairs_hook=pairs)
        if before != {k:record[k] for k in ['command','cwd','expected_exit','timeout_seconds']} or not rest.endswith('\nexit=' + str(record['exit_code']) + '\n'):
            raise ValueError('Command log header/actual exit differs')
        result.append((file,record,rest.rsplit('\nexit=',1)[0]))
    if not result: raise ValueError('Actual command log evidence absent')
    return result


def logged_elf(row, label, logs):
    keys(row, 'path sha256 needed machine codesign sdk15_n1_symbols rpath textrel')
    outputs={}
    for part in ['header','sections','dynamic','symbols','signature']:
        matches=[(f,r,b) for f,r,b in logs if f.name.startswith(label+'-'+part+'-')]
        if len(matches)!=1: raise ValueError('Missing/ambiguous actual ELF audit log: '+label+'-'+part)
        file,record,body=matches[0]
        command=record['command']
        if record['exit_code']!=0 or record['timed_out'] is not False or command[-1]!=row['path']:
            raise ValueError('Actual ELF/signature audit command did not complete')
        expected_options={'header':['-h'],'sections':['-S'],'dynamic':['-d'],'symbols':['--demangle'],'signature':['display-sign','-inFile']}[part]
        if command[1:-1]!=expected_options: raise ValueError('Different ELF audit option contract')
        outputs[part]=body
    expected={'libc.so','libvulkan.so'} if Path(row['path']).name=='system-loader-acceptance' else {'libc.so'}
    needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',outputs['dynamic'])
    if set(needed)!=expected or needed!=row['needed'] or row['machine']!='AArch64' or row['codesign'] is not True or row['rpath'] is not False or row['textrel'] is not False or not re.search(r'Machine:\s+AArch64',outputs['header']) or '.codesign' not in outputs['sections'] or re.search(r'RPATH|RUNPATH|TEXTREL',outputs['dynamic']) or re.search(r'std::__(?:h|1|blender20)::',outputs['symbols']) or outputs['symbols'].count('std::__n1::')!=row['sdk15_n1_symbols'] or not outputs['signature'].strip():
        raise ValueError('Actual logged signed native ELF/SDK ABI/PIC differs')
    if Path(row['path']).name=='library-acceptance' and row['sdk15_n1_symbols']<1: raise ValueError('Real compiler consumer lacks SDK15 symbols')


def patch_evidence(logs, source_root, repo):
    source_lock=load(source_root/'builder/sources.lock.json');evidence=[]
    for patch in source_lock['patches']:
        expected={'check':['apply','--check'],'forward':['apply'],'reverse-check':['apply','--reverse','--check'],'reverse':['apply','--reverse']}
        for operation,options in expected.items():
            label=patch['name']+'-patch-'+operation
            matches=[(f,r,b) for f,r,b in logs if re.fullmatch(re.escape(label)+r'-\d+\.json',f.name)]
            if len(matches)!=1: raise ValueError('Missing/ambiguous actual patch source operation')
            file,record,body=matches[0]
            if record['command'][1:]!=options+[str(repo/rel(patch['path']))] or record['exit_code']!=0 or record['timed_out'] is not False:
                raise ValueError('Actual patch forward/reverse/check command incomplete')
            evidence.append({'path':str(file),'sha256':digest(file),'stdout_log_sha256':record['log_sha256']})
    return evidence


def verify_runtime(receipts, logs, native_root):
    evidence = []
    for label in ['native-preflight-configure','native-preflight-build','native-preflight-real-runtime','actual-sdk-preprocessor','native-driver-link-plan']:
        matches=[(f,r,b) for f,r,b in logs if f.name.startswith(label+'-')]
        if len(matches)!=1 or matches[0][1]['exit_code']!=0 or matches[0][1]['timed_out'] is not False:
            raise ValueError('Actual native compiler/probe/driver completion log absent')
        file,record,body=matches[0]
        if label=='native-preflight-real-runtime' and '100% tests passed' not in body: raise ValueError('Actual native CTest probe completion absent')
        if label=='actual-sdk-preprocessor':
            for key,value in {'_LIBCPP_VERSION':'15004','_LIBCPP_ABI_NAMESPACE':'__n1','__clang_major__':'20','__OHOS__':'1','__aarch64__':'1'}.items():
                if not re.search(r'^#define '+key+r'\s+'+value+r'\s*$',body,re.M): raise ValueError('Actual SDK macro stdout differs')
        evidence.append({'path':str(file),'sha256':digest(file),'stdout_log_sha256':record['log_sha256']})
    for moved, packet in [(False,receipts['acceptance.json']),(True,receipts['migration.json'])]:
        for consumer in packet['consumers']:
            label = ('migrated-' if moved else 'original-') + consumer['mode']
            for artifact in consumer['artifacts']:
                logged_elf(artifact,label+'-'+Path(artifact['path']).name,logs)
            for name in CONSUMERS:
                matches = [(file,rec,body) for file,rec,body in logs if file.name.startswith(label + '-' + name + '-real-runtime-')]
                if len(matches) != 1: raise ValueError('Ambiguous/absent actual runtime log: ' + label + '-' + name)
                file,record,body = matches[0]
                artifact = next((a for a in consumer['artifacts'] if Path(a['path']).name == name),None)
                if artifact is None or record['command'][0] != artifact['path'] or record['expected_exit'] != 0 or record['exit_code'] != 0 or record['timed_out'] is not False:
                    raise ValueError('Actual signed runtime command/exit incomplete')
                lines = [line for line in body.splitlines() if line.strip()]
                actual = json.loads(lines[-1],object_pairs_hook=pairs)
                if actual != consumer['native_results'][name]: raise ValueError('Recorded runtime JSON differs from actual stdout')
                if not moved: bound_file(Path(artifact['path']),artifact['sha256'])
                evidence.append({'path':str(file),'sha256':digest(file),'stdout_log_sha256':record['log_sha256']})
            # Both original and moved SPIRV fixtures are deliberately cleaned by
            # the pinned builder. Their five SHA/size rows are retained provenance,
            # not a claim that deleted temporary SPIRV files were reread here.
            # Successful fresh configure/build/link plans are independently logged.
            for suffix in ['configure','build','actual-link-plan']:
                found = [(f,r,b) for f,r,b in logs if f.name.startswith(label + '-' + suffix + '-')]
                if len(found) != 1 or found[0][1]['exit_code'] != 0 or found[0][1]['timed_out'] is not False:
                    raise ValueError('Native consumer configure/build/link evidence absent')
                if suffix == 'actual-link-plan':
                    body = found[0][2]
                    if 'libshaderc_combined.a' not in body: raise ValueError('Actual combined link missing')
                    if moved and any(str(native_root / leaf) in body for leaf in ['sources','build','prefix']):
                        raise ValueError('Moved link retained original native roots')
    return evidence


def readonly_tool(args, name, options, file):
    command = [str(getattr(args,name)),*options,str(file)]
    result = subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120,
                            env={'PATH':os.environ.get('PATH',''),'TMPDIR':str(args.tmp_dir),'HOME':os.environ.get('HOME','')})
    if result.returncode: raise ValueError('Readonly artifact audit command failed: '+name)
    return result.stdout.decode('utf-8',errors='strict'), {'command':command,'exit_code':0,'stdout_sha256':hashlib.sha256(result.stdout).hexdigest()}


def archive_members(raw):
    if len(raw) <= 8 or raw[:8] != b'!<arch>\n': raise ValueError('Opaque/empty/spoofed archive')
    offset, count = 8, 0
    while offset < len(raw):
        if offset+60>len(raw) or raw[offset+58:offset+60] != b'`\n': raise ValueError('Malformed archive member')
        header = raw[offset:offset+60]
        try: size=int(header[48:58].strip())
        except ValueError: raise ValueError('Malformed archive member size')
        name=header[:16].strip();payload=raw[offset+60:offset+60+size]
        if len(payload)!=size: raise ValueError('Truncated archive member')
        if name not in [b'/',b'//',b'/SYM64/']:
            if name.startswith(b'#1/'):
                try: payload=payload[int(name[3:]):]
                except ValueError: raise ValueError('Malformed BSD archive name')
            if len(payload)<64 or payload[:4]!=b'\x7fELF' or payload[4:6]!=b'\x02\x01' or int.from_bytes(payload[16:18],'little')!=1 or int.from_bytes(payload[18:20],'little')!=183:
                raise ValueError('Archive payload is not actual AArch64 ELF64 REL')
            count+=1
        offset += 60+size+(size%2)
    if offset != len(raw) or not count: raise ValueError('Archive has no complete ELF members')
    return count


def audit_current(args, receipts):
    evidence=[];artifacts=receipts['artifacts.json']
    tracked={r['path'] for r in artifacts['archives']}
    if tracked != {p.name for p in (args.prefix/'lib').glob('*.a')}:
        raise ValueError('Current/audited archive set differs')
    for row in artifacts['archives']:
        keys(row,'path sha256 members signed n1_symbols')
        if len(rel(row['path']).parts) != 1 or row['signed'] is not False:
            raise ValueError('Unexpected archive path/signing schema')
        file=args.prefix/'lib'/rel(row['path']);bound_file(file,row['sha256'])
        members=archive_members(file.read_bytes())
        names,ev=readonly_tool(args,'ar',['t'],file);evidence.append(ev)
        symbols,ev=readonly_tool(args,'nm',['--demangle'],file);evidence.append(ev)
        if members!=row['members'] or len(names.splitlines())!=members or re.search(r'std::__(?:h|1|blender20)::|pthread_cancel',symbols) or symbols.count('std::__n1::') != row['n1_symbols']:
            raise ValueError('Actual archive members/native SDK ABI differ')
        if file.name=='libshaderc_combined.a':
            apis=['shaderc_compiler_initialize','shaderc_compile_into_spv','spvtools::Optimizer::Run','spvValidate']
            if 'std::__n1::' not in symbols or any(not any(re.search(r'\b[TtWw]\s+'+re.escape(api),line) for line in symbols.splitlines()) for api in apis):
                raise ValueError('Combined has no genuine defined compiler/optimizer/validator/SDK15 symbols')
    if len(artifacts['compile'])!=3 or {r['group'] for r in artifacts['compile']}!={'shaderc','vulkan-utility','spirv-reflect'}:
        raise ValueError('Full library PIC compile database closure missing')
    for row in artifacts['compile']:
        keys(row,'group units sha256')
        file=args.receipt_root/'build'/row['group']/'compile_commands.json';bound_file(file,row['sha256'])
        database=json.loads(file.read_text())
        if len(database)!=row['units'] or not database or any('-fPIC' not in r.get('command',' '.join(r.get('arguments',[]))) for r in database):
            raise ValueError('Actual PIC compile evidence differs')
        for unit in database:
            src=Path(unit['file']);src=src if src.is_absolute() else Path(unit['directory'])/src
            if not (src.resolve().is_relative_to(args.receipt_root/'sources') or src.resolve().is_relative_to(args.receipt_root/'build'/row['group'])):
                raise ValueError('PIC compiled source escaped own prepared/generated source')
    elves=[r for c in receipts['acceptance.json']['consumers'] for r in c['artifacts']]+artifacts['installed_dsos']
    for row in elves:
        file=canonical(row['path'])
        if not (file.is_relative_to(args.receipt_root/'build') or file.is_relative_to(args.prefix/'lib')):
            raise ValueError('Current signed consumer/DSO outside selected native roots')
        bound_file(file,row['sha256'])
        outputs={}
        for part,opts in [('header',['-h']),('sections',['-S']),('dynamic',['-d'])]:
            outputs[part],ev=readonly_tool(args,'readelf',opts,file);evidence.append(ev)
        symbols,ev=readonly_tool(args,'nm',['--demangle'],file);evidence.append(ev)
        signed,ev=readonly_tool(args,'signer',['display-sign','-inFile'],file);evidence.append(ev)
        needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',outputs['dynamic'])
        expected={'libc.so','libvulkan.so'} if file.name=='system-loader-acceptance' else {'libc.so'}
        if set(needed)!=expected or needed!=row['needed'] or not re.search(r'Machine:\s+AArch64',outputs['header']) or '.codesign' not in outputs['sections'] or re.search(r'RPATH|RUNPATH|TEXTREL',outputs['dynamic']) or re.search(r'std::__(?:h|1|blender20)::',symbols) or symbols.count('std::__n1::')!=row['sdk15_n1_symbols'] or not signed.strip():
            raise ValueError('Actual current signed native ELF/SDK15/PIC evidence differs')
        if 'build_path' in row: bound_file(Path(row['build_path']),row['sha256'])
    return evidence


def tool_bytes(file, sha256, size=None):
    # Explicit multicall tool aliases are permitted only when the caller path
    # also occurs verbatim in the independently completed native tool receipt.
    file=Path(file).absolute()
    if not file.is_file() or digest(file)!=sha256 or (size is not None and file.stat().st_size!=size):
        raise ValueError('Declared actual native tool bytes differ')


def profile(args, receipts):
    pre=receipts['prerequisites.json'];cfg=receipts['toolchain/compiler.json']
    keys(pre, 'scope host versions selected_sdk_macros tool_inputs header_inventories source_graph native_runtime_probe')
    keys(cfg, 'compilers flags signer readelf tmp_dir')
    if Path(cfg['signer']).absolute()!=args.signer or Path(cfg['readelf']).absolute()!=args.readelf:
        raise ValueError('Completed compiler launcher tool binding differs')
    if pre['host'][0]!='HarmonyOS' or pre['host'][-1]!='aarch64' or pre['selected_sdk_macros']!={'_LIBCPP_ABI_NAMESPACE':'__n1','_LIBCPP_VERSION':'15004','__clang_major__':'20','__OHOS__':'1','__aarch64__':'1'}:
        raise ValueError('Actual native source tool profile differs')
    for row in pre['tool_inputs']: tool_bytes(Path(row['path']),row['sha256'],row['size'])
    for name in ['ar','nm','readelf','signer']:
        expected=getattr(args,name+'_sha256');tool_bytes(getattr(args,name),expected)
        if not any(Path(r['path']).absolute()==getattr(args,name).absolute() and r['sha256']==expected for r in pre['tool_inputs']):
            raise ValueError('Caller tool is outside actual native prerequisite proof')
    for name,root in [('sdk_cxx',args.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),('clang20_resource',args.resource_dir/'include'),('sdk_sysroot',args.sdk_root/'sysroot/usr/include')]:
        if inventory(root,'sdk')!=pre['header_inventories'][name]: raise ValueError('Actual selected SDK/resource header bytes differ')
    for name in ['libclang_rt.builtins.a','clang_rt.crtbegin.o','clang_rt.crtend.o']:
        if digest(args.resource_dir/'lib/aarch64-linux-ohos'/name)!=digest(args.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/name):
            raise ValueError('SDK15 compiler-rt overlay differs')
    expected=['--target=aarch64-unknown-linux-ohos','--sysroot='+str(args.sdk_root/'sysroot'),'--ld-path=', '-resource-dir='+str(args.resource_dir),'-L'+str(args.sdk_root/'llvm/lib/aarch64-linux-ohos')]
    flags=cfg['flags']['c']
    if flags[:2]!=expected[:2] or flags[3:]!=expected[3:] or not flags[2].startswith(expected[2]) or cfg['flags']['cxx']!=flags+['--driver-mode=g++','-nostdinc++','-isystem',str(args.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),'-fexperimental-library','-static-libstdc++','-lc++experimental']:
        raise ValueError('Actual native compiler/runtime flag profile differs')


def output_policy(args):
    out=canonical(args.output);cache=canonical(os.environ['XDG_CACHE_HOME']);tmp=canonical(args.tmp_dir)
    if out.exists() or not out.is_relative_to(cache) or out==cache:
        raise ValueError('Output must be absent beneath declared private cache; no adoption/resume')
    if not tmp.is_relative_to(canonical(os.environ['TMPDIR'])):
        raise ValueError('TMP must be under real declared TMPDIR')
    protected=[args.source_root]+[getattr(args,name,None) for name in ['prefix','receipt_root','sdk_root','resource_dir','job_receipt']]
    for source in [p for p in protected if p is not None]:
        source=canonical(source)
        if out==source or out.is_relative_to(source) or source.is_relative_to(out): raise ValueError('Output overlaps selected input tree/file')
    for name in ['ar','nm','readelf','signer']:
        tool=getattr(args,name,None)
        if tool is not None and out.is_relative_to(Path(tool).parent.resolve()): raise ValueError('Output overlaps actual tool directory')
    if tmp==out or tmp.is_relative_to(out) or out.is_relative_to(tmp): raise ValueError('Separate output and TMP required')
    return out,tmp


def run(args):
    seal=self_seal();contract,repo=source_contract(args.source_root,args.source_lock_sha256)
    out,tmp=output_policy(args)
    if args.stage=='inspect':
        packet={'schema_version':1,'kind':'shaderc-adapter-source-contract-inspection','status':'NOTREADY',
                'adapter_input_lock_sha256':seal,'source_input_lock_sha256':args.source_lock_sha256,
                'reason':'Only pinned source contract inspected; completed job/current native closure not read or accepted',
                'contract':contract,'native_compile_count':0,'native_receipt_accepted':False}
        files={'inspection.json':packet}
    else:
        gate=envelope(args) # Must precede every read of live native receipt/prefix/source outputs.
        receipts={name:load(args.receipt_root/name) for name in contract['required_receipts']}
        owner=receipts['.vulkan-builder-owned.json']
        if owner['input_lock_sha256']!=args.source_lock_sha256 or Path(owner['prefix'])!=args.prefix or Path(owner['sources_root'])!=args.receipt_root/'sources' or Path(owner['archives_root'])!=args.receipt_root/'archives':
            raise ValueError('Actual completed native root ownership differs')
        validate_receipts(receipts,contract);validate_prefix(args.prefix,receipts['prefix-files.json'],args.source_lock_sha256)
        mode_journal=[]
        sources=prepared_sources(args.source_root,repo,args.receipt_root,receipts,contract,mode_journal)
        profile(args,receipts);logs=command_evidence(args.receipt_root, canonical(gate['value']['command_log_dir']));runtime=verify_runtime(receipts,logs,args.receipt_root)
        patches=patch_evidence(logs,args.source_root,repo)
        current=audit_current(args,receipts)
        underlying=[{'path':str(args.receipt_root/name),'sha256':digest(args.receipt_root/name)} for name in contract['required_receipts']]
        proof={'executor':gate,'underlying_receipts':underlying,'source_groups':sources,'source_mode_journal':mode_journal,
               'actual_patch_forward_reverse_command_receipts':patches,'actual_runtime_command_receipts':runtime,
               'readonly_current_artifact_commands':current,'scope':contract['output_scope'],'excluded':contract['not_tested']}
        # Detect changes during audit. This is a readonly adapter, never the native builder.
        if any(digest(Path(r['path']))!=r['sha256'] for r in underlying): raise ValueError('Underlying completed receipts changed during audit')
        validate_prefix(args.prefix,receipts['prefix-files.json'],args.source_lock_sha256)
        accepted={'schema_version':1,'kind':'independent-completed-native-shaderc-closure-adapter',
                  'new_full_native_acceptance':True,'input_lock_sha256':args.source_lock_sha256,'adapter_input_lock_sha256':seal,
                  'prefix':str(args.prefix),'scope':contract['output_scope'],'underlying_receipts':underlying,
                  'evidence_sha256':hashlib.sha256((json.dumps(proof,ensure_ascii=False,indent=2)+'\n').encode()).hexdigest(),
                  'gpu_pixels_wsi_vma_device_allocation_blender_hap':'NOT_TESTED','pic_modules_dlopened':False}
        files={'shaderc-accepted.json':accepted,'prefix-files.json':receipts['prefix-files.json'],'evidence.json':proof}
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shaderc-receipt-adapter-',dir=tmp) as td:
        stage=Path(td)/'packet';stage.mkdir()
        for name,packet in files.items(): (stage/name).write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
        # Atomic absent-directory reservation prevents adopting an output created
        # during the readonly audit. Publish the accepted receipt last.
        out.mkdir(exist_ok=False)
        ordered=sorted(files,key=lambda name:name=='shaderc-accepted.json')
        for name in ordered:
            with (out/name).open('xb') as stream: stream.write((stage/name).read_bytes())
    return {'status':'NOTREADY' if args.stage=='inspect' else 'ACCEPTED complete independently finished native ShaderC closure',
            'output':str(out),'adapter_input_lock_sha256':seal,'temporary_tree_cleaned':True}


def arguments():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['inspect','accept'])
    for name in ['source_root','receipt_root','prefix','output','tmp_dir','job_receipt','sdk_root','resource_dir','ar','nm','readelf','signer']:
        parser.add_argument('--'+name.replace('_','-'),type=Path,required=name in ['source_root','output'])
    for name in ['source_lock','job_receipt','ar','nm','readelf','signer']:
        parser.add_argument('--'+name.replace('_','-')+'-sha256',required=name=='source_lock')
    args=parser.parse_args()
    if args.stage=='accept':
        missing=[name for name in ['receipt_root','prefix','job_receipt','job_receipt_sha256','sdk_root','resource_dir','ar','nm','readelf','signer','ar_sha256','nm_sha256','readelf_sha256','signer_sha256'] if getattr(args,name) is None]
        if missing: parser.error('Explicit completed native proof/tools required: '+','.join(missing))
    args.tmp_dir=args.tmp_dir or Path(os.environ['TMPDIR'])
    for name in ['source_root','receipt_root','prefix','output','tmp_dir','job_receipt','sdk_root','resource_dir','ar','nm','readelf','signer']:
        if getattr(args,name) is not None: setattr(args,name,getattr(args,name).absolute())
    return args


if __name__=='__main__':
    try:
        print(json.dumps(run(arguments()),ensure_ascii=False,indent=2))
    except NotReady as error:
        print(json.dumps({'status':'NOTREADY','reason':str(error),'accepted_receipt_emitted':False},ensure_ascii=False),file=sys.stderr);sys.exit(3)
    except (ValueError,OSError,KeyError,StopIteration,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'REJECTED','reason':str(error),'accepted_receipt_emitted':False},ensure_ascii=False),file=sys.stderr);sys.exit(2)
