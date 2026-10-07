# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual new native artifacts, source integrity and prefix migration gates."""
import json
import hashlib
import os
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
            link = os.readlink(file)
            if Path(link).is_absolute() or not file.resolve().is_relative_to(prefix.resolve()) or not file.exists():
                raise ValueError('Unsafe installed prefix symlink')
            rows.append({'path': file.relative_to(prefix).as_posix(), 'type': 'symlink', 'target': link,
                         'sha256': hashlib.sha256(os.fsencode(link)).hexdigest(), 'size': len(os.fsencode(link))})
            continue
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


def elf(args, runner, file, label, require_cpp=True):
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
    if (require_cpp and 'std::__n1::' not in symbols) or 'std::__h::' in symbols:
        raise ValueError('Actual SDK15 native C++ ABI gate failed')
    runner.run([args.signer, 'display-sign', '-inFile', file], label + '-display-sign')
    return {'path': str(file), 'sha256': sha(file), 'machine': 'AArch64', 'codesign': True,
            'cpp_abi': '__n1' if 'std::__n1::' in symbols else 'C/system libc only', 'needed': needed, 'rpath': False, 'textrel': False}


def archive_pic_units(build, leaves, prefix=None):
    """Follow actual Ninja static-library object inputs, excluding executable PIE."""
    import shlex
    graph = (build / 'build.ninja').read_text()
    required = set()
    found = set()
    for line in graph.splitlines():
        match = re.match(r'^build (.+): (\S*STATIC_LIBRARY_LINKER\S*) (.*)$', line)
        if not match:
            continue
        outputs = shlex.split(match[1].replace('$ ', '\\ '))
        owned = [Path(p).name for p in outputs if Path(p).name in leaves]
        if not owned:
            continue
        found.update(owned)
        if prefix is not None:
            for output in outputs:
                if Path(output).name in owned and sha(build / output) != sha(prefix / 'lib' / Path(output).name):
                    raise ValueError('Installed archive bytes differ from actual source-built Ninja output')
        inputs = match[3].split(' |', 1)[0]
        for object_name in shlex.split(inputs.replace('$ ', '\\ ')):
            if object_name.endswith(('.o', '.obj')):
                required.add((build / object_name).resolve())
    if found != set(leaves) or not required:
        raise ValueError('Actual static archive object ownership absent: ' + str(set(leaves) - found))
    database = build / 'compile_commands.json'
    rows = json.loads(database.read_text())
    owned = {}
    for row in rows:
        tokens = row.get('arguments') or shlex.split(row['command'])
        output = row.get('output')
        if output is None and '-o' in tokens:
            output = tokens[tokens.index('-o') + 1]
        if output is None:
            continue
        object_path = (Path(row['directory']) / output).resolve()
        if object_path in required:
            if '-fPIC' not in tokens or '-fPIE' in tokens:
                raise ValueError('Actual archive object is not PIC: ' + str(object_path))
            owned[object_path] = row
    if set(owned) != required:
        raise ValueError('Archive members lack actual PIC compilation records: ' + str(required - set(owned)))
    return {'units': len(owned), 'excluded_nonarchive_units': len(rows)-len(owned),
            'sha256': sha(database), 'ninja_graph_sha256': sha(build / 'build.ninja'),
            'archive_object_paths': sorted(str(p.relative_to(build.resolve())) for p in required)}


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
    for name, leaves in ARCHIVES.items():
        if not leaves:
            continue
        row = archive_pic_units(root / 'build' / name, leaves, args.prefix)
        row['name'] = name
        compile_rows.append(row)
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
    installed_elfs = []
    for file in sorted(args.prefix.rglob('*')):
        if file.is_file() and not file.is_symlink():
            with file.open('rb') as stream:
                if stream.read(4) == b'\x7fELF':
                    installed_elfs.append(elf(args, runner, file, 'installed-' + file.name, require_cpp=False))
    result = {'installed_elfs': installed_elfs, 'result': 'PASS actual built archives, native consumers, static PIC and metadata/source integrity',
              'archives': archives, 'compile': compile_rows, 'metadata': metadata, 'sources': source_rows,
              'pic_module_dlopened': False, 'duplicated_static_cpp_runtime_ownership': 'Separate integration scope'}
    write_json(root / 'artifacts.json', result)
    return result


def migrate(args, root, runner, consumer):
    verify_prefix(args, root)
    original = file_inventory(args.prefix)
    with tempfile.TemporaryDirectory(prefix='color-native-migrate-', dir=runner.tmp) as td:
        moved = Path(td) / '卷 dependency prefix with spaces'
        shutil.copytree(args.prefix, moved, symlinks=True)
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
