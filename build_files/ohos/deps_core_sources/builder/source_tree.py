# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete archive extraction and sealed patch forward/reverse checks."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from io_utils import BASE, HERE, REPO, repo_file, sha, vendor, write_json

GENERATED_EMBREE = {'kernels/config.h','include/embree4/rtcore_config.h','kernels/hash.h',
                    'kernels/export.linux.map','kernels/export.macosx.map'}


def relative_member(name, archive_root):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != archive_root:
        raise ValueError('Unsafe/unexpected source archive member: ' + name)
    return Path(*path.parts[1:])


def extract_complete(archive, output, archive_root):
    output.mkdir(parents=True)
    seen = set()
    links = []

    def place(name, data=None, directory=False, link=None, mode=0o644):
        relative = relative_member(name, archive_root)
        if relative == Path('.'):
            if not directory:
                raise ValueError('Unexpected source root type')
            return
        if str(relative) in seen:
            raise ValueError('Duplicate archive source path')
        seen.add(str(relative))
        target = output / relative
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        elif link is not None:
            if PurePosixPath(link).is_absolute():
                raise ValueError('Absolute source symlink')
            normalized = Path(os.path.normpath(str(target.parent / link)))
            if output not in normalized.parents:
                raise ValueError('Escaping source symlink')
            links.append((target,link))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError('Conflicting extraction source path')
            with target.open('wb') as stream:
                shutil.copyfileobj(data, stream, 1024 * 1024)
            target.chmod(mode & 0o777)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                mode = entry.external_attr >> 16
                if entry.is_dir():
                    place(entry.filename, directory=True)
                elif stat.S_ISLNK(mode):
                    place(entry.filename, link=z.read(entry).decode('utf-8'))
                else:
                    with z.open(entry) as stream:
                        place(entry.filename, data=stream, mode=(mode & 0o777) or 0o644)
    else:
        with tarfile.open(archive) as t:
            for entry in t.getmembers():
                if entry.isdir():
                    place(entry.name, directory=True)
                elif entry.issym():
                    place(entry.name, link=entry.linkname)
                elif entry.isfile():
                    with t.extractfile(entry) as stream:
                        place(entry.name, data=stream, mode=entry.mode)
                else:
                    raise ValueError('Unsupported device/hardlink/FIFO source entry')
    for target, link in links:
        if target.exists() or any(parent.is_symlink() for parent in target.parents if parent != output):
            raise ValueError('Conflicting source symlink/parent')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
    for target, link in links:
        if output.resolve() not in target.resolve().parents:
            raise ValueError('Chained source symlink escapes private extraction')


def patch_records(name):
    provenance = json.loads((BASE / 'provenance.json').read_text())
    return [x for x in provenance['patches'] if x['lib'] == name]


def verify_tree(root, name, patched=False, allow_generated=False):
    rows = json.loads((HERE / 'inventories' / (name + '.json')).read_text())['files']
    adapted = {f['path']:f['after_sha256'] for p in patch_records(name) for f in p['files']} if patched else {}
    expected = {r['path'] for r in rows}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    extra = actual - expected
    if extra and not (allow_generated and name == 'embree' and extra <= GENERATED_EMBREE):
        raise ValueError('Unexpected complete source files: ' + str(extra))
    generated = []
    for row in rows:
        file = root / row['path']
        if allow_generated and name == 'embree' and row['path'] in GENERATED_EMBREE:
            if not file.is_file():
                raise ValueError('Missing genuine native generated source output')
            generated.append({'path':row['path'],'sha256':sha(file)})
        elif 'symlink' in row:
            if not file.is_symlink() or os.readlink(file) != row['symlink']:
                raise ValueError('Original source symlink changed')
        elif not file.is_file() or file.is_symlink() or sha(file) != adapted.get(row['path'],row['sha256']):
            raise ValueError('Unsealed source file bytes: ' + name + '/' + row['path'])
    return {'name':name,'complete_original_files':len(rows),'patched':patched,'native_generated_files':generated}


def replay_patches(root, name, runner):
    records = patch_records(name)
    for record in records:
        for file in record['files']:
            if sha(root / file['path']) != file['before_sha256']:
                raise ValueError('Sealed patch before-hash mismatch')
        patch = repo_file(record.get('replay_patch',record['original_patch']))
        argv = ['git','apply'] + (['--recount'] if '--recount' in record['invocation'] else [])
        runner.run(argv + ['--check',patch], name + '-patch-check', cwd=root)
        runner.run(argv + [patch], name + '-patch-forward', cwd=root)
        for file in record['files']:
            if sha(root / file['path']) != file['after_sha256']:
                raise ValueError('Sealed patch after-hash mismatch')
        runner.run(argv + ['--reverse','--check',patch], name + '-patch-reverse-check', cwd=root)
    # Prove reversibility on a separate complete tree; leave the build tree patched.
    with tempfile.TemporaryDirectory(prefix='core-patch-reverse-', dir=runner.tmp) as td:
        copy = Path(td) / 'source'
        shutil.copytree(root, copy, symlinks=True)
        for record in reversed(records):
            patch = repo_file(record.get('replay_patch',record['original_patch']))
            argv = ['git','apply'] + (['--recount'] if '--recount' in record['invocation'] else [])
            runner.run(argv + ['--reverse',patch], name + '-patch-reverse', cwd=copy)
        verify_tree(copy, name, patched=False)
    return verify_tree(root,name,patched=True)


def prepare_sources(root, runner):
    receipt = root / 'sources.json'
    if receipt.exists():
        recorded = json.loads(receipt.read_text())
        for dep in recorded['sources']:
            configured = (root / 'build' / dep['name'] / 'CMakeCache.txt').is_file()
            verify_tree(root / 'sources' / dep['name'],dep['name'],patched=True,allow_generated=configured)
        return recorded
    destination = root / 'sources'
    if destination.exists():
        raise ValueError('Refuse incomplete/conflicting existing source tree')
    provenance = json.loads((BASE / 'provenance.json').read_text())
    vend = vendor()
    with tempfile.TemporaryDirectory(prefix='core-private-sources-', dir=runner.tmp) as td:
        stage = Path(td)
        result = []
        for dep in provenance['sources']:
            archive = stage / 'archives' / dep['archive_filename']
            vend.materialize(repo_file(dep['registry_manifest']).parent,archive)
            if dep.get('formal_hash'):
                import hashlib
                with archive.open('rb') as stream:
                    actual = hashlib.file_digest(stream, dep['formal_hash']['algorithm'].lower()).hexdigest()
                if actual != dep['formal_hash']['value']:
                    raise ValueError('Formal original archive hash mismatch')
            source = stage / 'sources' / dep['name']
            extract_complete(archive,source,dep['archive_root'])
            verify_tree(source,dep['name'])
            result.append(replay_patches(source,dep['name'],runner))
        if stage.stat().st_dev != root.stat().st_dev:
            raise ValueError('TMPDIR and source output need same private filesystem')
        (stage / 'sources').rename(destination)
    value = {'result':'PASS complete private extraction, sealed forward/reverse hashes','sources':result,
             'new_builder_accepted':False,'temporary_tree_cleaned':True}
    write_json(receipt,value)
    return value
