# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual new native artifacts, source integrity and prefix migration gates."""
import json
from pathlib import Path
import re
import shutil
import tempfile
from io_utils import HERE, sha, sources, write_json
from source_tree import ACTIVE, verify_tree
from metadata import ARCHIVES


def file_inventory(prefix):
    rows = []
    for file in sorted(prefix.rglob('*')):
        if file.is_symlink():
            raise ValueError('Unexpected installed prefix symlink: ' + str(file))
        if file.is_file():
            rows.append({'path': file.relative_to(prefix).as_posix(), 'sha256': sha(file), 'size': file.stat().st_size})
    return rows


def freeze_prefix(args, root):
    rows = file_inventory(args.prefix)
    write_json(root / 'prefix-files.json', {'files': rows, 'input_lock_sha256': sha(HERE / 'inputs.lock.json')})
    return rows


def verify_prefix(args, root):
    receipt = json.loads((root / 'prefix-files.json').read_text())
    if receipt['files'] != file_inventory(args.prefix) or receipt['input_lock_sha256'] != sha(HERE / 'inputs.lock.json'):
        raise ValueError('Actual installed prefix bytes/lineage drift')


def elf(args, runner, file, label):
    tool = args.sdk_root / 'llvm/bin'
    header = runner.run([tool / 'llvm-readelf', '-h', file], label + '-header')
    sections = runner.run([tool / 'llvm-readelf', '-S', file], label + '-sections')
    dynamic = runner.run([tool / 'llvm-readelf', '-d', file], label + '-dynamic')
    symbols = runner.run([tool / 'llvm-nm', '--demangle', file], label + '-symbols')
    if not re.search(r'Machine:\s+AArch64', header) or '.codesign' not in sections:
        raise ValueError('Actual native ELF/signature gate failed')
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', dynamic)
    if needed != ['libc.so'] or re.search(r'RPATH|RUNPATH|TEXTREL', dynamic):
        raise ValueError('Native static closure/PIC gate failed')
    if 'std::__n1::' not in symbols or 'std::__h::' in symbols:
        raise ValueError('Actual SDK15 native C++ ABI gate failed')
    runner.run([args.signer, 'display-sign', '-inFile', file], label + '-display-sign')
    return {'path': str(file), 'sha256': sha(file), 'machine': 'AArch64', 'codesign': True,
            'cpp_abi': '__n1', 'needed': needed, 'rpath': False, 'textrel': False}


def artifacts(args, root, runner):
    verify_prefix(args, root)
    archives = []
    tool = args.sdk_root / 'llvm/bin'
    for leaves in ARCHIVES.values():
        for leaf in leaves:
            file = args.prefix / 'lib' / leaf
            headers = runner.run([tool / 'llvm-readelf', '-h', file], 'archive-header-' + leaf)
            machines = re.findall(r'Machine:\s+([^\n]+)', headers)
            types = re.findall(r'Type:\s+(\S+)', headers)
            if not machines or any(m.strip() != 'AArch64' for m in machines) or any(t != 'REL' for t in types):
                raise ValueError('Actual archive AArch64 relocatable member gate failed')
            symbols = runner.run([tool / 'llvm-nm', '--undefined-only', '--demangle', file], 'archive-symbols-' + leaf)
            if 'std::__h::' in symbols or re.search(r'\bpthread_cancel\b', symbols):
                raise ValueError('Wrong SDK ABI or unsupported cancellation import')
            archives.append({'path': leaf, 'sha256': sha(file), 'members': len(machines), 'signature_scope': 'Static archives are not signed'})
    compile_rows = []
    for name in ARCHIVES:
        database = root / 'build' / name / 'compile_commands.json'
        entries = json.loads(database.read_text())
        if not entries or any('-fPIC' not in e.get('command', ' '.join(e.get('arguments', []))) for e in entries):
            raise ValueError('Actual static PIC compile evidence absent: ' + name)
        compile_rows.append({'name': name, 'units': len(entries), 'sha256': sha(database)})
    metadata = []
    for file in args.prefix.rglob('*'):
        if file.is_file() and file.suffix in ('.pc', '.cmake'):
            text = file.read_text()
            if any(p in text for p in [str(args.root), str(args.prefix), str(args.sdk_root), '/storage/Users', '/data/storage']):
                raise ValueError('Installed metadata contains an absolute source/prefix/development route')
            metadata.append({'path': str(file.relative_to(args.prefix)), 'sha256': sha(file)})
    source_rows = [verify_tree(root / 'sources' / name, name, patched=True) for name in ACTIVE]
    accepted = json.loads((root / 'acceptance.json').read_text())
    # Receipts cannot stand in for current final ELF bytes.
    for test in accepted['consumers']:
        for artifact in test['artifacts']:
            if sha(Path(artifact['path'])) != artifact['sha256']:
                raise ValueError('Accepted signed ELF changed')
    result = {'result': 'PASS actual built archives, native consumers, static PIC and metadata/source integrity',
              'archives': archives, 'compile': compile_rows, 'metadata': metadata, 'sources': source_rows,
              'pic_module_dlopened': False, 'duplicated_static_cpp_runtime_ownership': 'Separate integration scope'}
    write_json(root / 'artifacts.json', result)
    return result


def migrate(args, root, runner, consumer):
    verify_prefix(args, root)
    original = file_inventory(args.prefix)
    with tempfile.TemporaryDirectory(prefix='volume-native-migrate-', dir=runner.tmp) as td:
        moved = Path(td) / '卷 dependency prefix with spaces'
        shutil.copytree(args.prefix, moved)
        if file_inventory(moved) != original:
            raise ValueError('Moved prefix bytes differ')
        results = []
        for mode in ['CMAKE', 'PC']:
            results.append(consumer(Path(td) / ('fresh ' + mode + ' consumer'), moved, mode, 'migrated-' + mode,
                                    forbidden=[str(args.prefix), str(root / 'build'), str(root / 'sources')]))
        result = {'result': 'PASS actual moved Unicode/space prefix fresh CMake/PC native consumers',
                  'preserved_files': len(original), 'consumers': results, 'pic_module_dlopened': False}
    result['temporary_tree_cleaned'] = True
    write_json(root / 'migration.json', result)
    return result
