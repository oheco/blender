# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict complete ZIP inventories and source parts; adapted from OHOS resource guard."""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def canonical(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or
        '\x00' in name or any(p in ('', '.', '..') for p in name.split('/')) or
        PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix() != name):
        raise ValueError('Noncanonical relative path: ' + repr(name))
    return PurePosixPath(name).parts


def repo_file(name):
    p = REPO.joinpath(*canonical(name))
    if not p.is_file() or any(v.is_symlink() for v in [p, *p.parents]):
        raise ValueError('Unsafe/missing repository input: ' + name)
    return p


def inventory(root):
    root = Path(root)
    rows, spellings = [], {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Real source directory required')
    for p in sorted(root.rglob('*')):
        name = p.relative_to(root).as_posix()
        parts = canonical(name)
        mode = p.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('Link/special source file: ' + name)
        for n in range(1, len(parts)+1):
            prefix = '/'.join(parts[:n])
            if spellings.setdefault(prefix.casefold(), prefix) != prefix:
                raise ValueError('Case collision: ' + prefix)
        if stat.S_ISREG(mode):
            rows.append({'path': name, 'size': p.stat().st_size, 'sha256': sha(p)})
    return rows


def extract_zip(archive, output, expected_root):
    output = Path(output)
    if output.exists():
        raise ValueError('Extraction target must be absent')
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) > 2000 or sum(i.file_size for i in entries) > 128*1024*1024:
            raise ValueError('ZIP ceilings exceeded')
        seen, spellings, nodes, roots = set(), {}, {}, set()
        for i in entries:
            name = i.filename[:-1] if i.is_dir() else i.filename
            parts = canonical(name)
            mode = i.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or i.flag_bits & 1:
                raise ValueError('Link/special/encrypted ZIP member')
            if (kind == stat.S_IFDIR) != i.is_dir() and kind != 0:
                raise ValueError('ZIP member type disagreement')
            if name in seen:
                raise ValueError('Duplicate ZIP member')
            seen.add(name)
            roots.add(parts[0])
            for n in range(1, len(parts)+1):
                prefix = '/'.join(parts[:n])
                if spellings.setdefault(prefix.casefold(), prefix) != prefix:
                    raise ValueError('Case-colliding ZIP prefix')
                t = 'file' if n == len(parts) and not i.is_dir() else 'directory'
                if prefix in nodes and nodes[prefix] != t:
                    raise ValueError('ZIP file/directory collision')
                nodes[prefix] = t
        if roots != {expected_root} or nodes[expected_root] != 'directory':
            raise ValueError('Wrong ZIP root')
        output.mkdir(parents=True)
        try:
            for i in entries:
                name = i.filename[:-1] if i.is_dir() else i.filename
                relative = canonical(name)[1:]
                p = output.joinpath(*relative)
                if i.is_dir():
                    p.mkdir(parents=True, exist_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(i) as source, p.open('xb') as target:
                        shutil.copyfileobj(source, target)
                    if p.stat().st_size != i.file_size:
                        raise ValueError('Truncated ZIP source')
        except BaseException:
            shutil.rmtree(output)
            raise


def reconstruct(dep, output):
    manifest = json.loads(repo_file(dep['manifest']).read_text())
    if (manifest['schema_version'], manifest['format'], manifest['sha256'], manifest['size']) != \
       (1, 'original-archive-parts', dep['sha256'], dep['size']):
        raise ValueError('Unsealed original archive manifest')
    output = Path(output)
    if output.exists():
        raise ValueError('Archive output must be absent')
    output.parent.mkdir(parents=True, exist_ok=True)
    combined = hashlib.sha256()
    total = 0
    with output.open('xb') as f:
        for n, row in enumerate(manifest['parts']):
            if row['filename'] != 'source.part%04d' % n or not 0 < row['size'] <= 32*1024*1024:
                raise ValueError('Noncanonical/oversized archive part')
            p = repo_file(str(PurePosixPath(dep['manifest']).parent / row['filename']))
            if p.stat().st_size != row['size'] or sha(p) != row['sha256']:
                raise ValueError('Archive part drift')
            with p.open('rb') as src:
                while block := src.read(1024*1024):
                    combined.update(block)
                    total += len(block)
                    f.write(block)
    if total != dep['size'] or combined.hexdigest() != dep['sha256']:
        output.unlink()
        raise ValueError('Original reconstructed archive differs')
    return manifest


def verify_tree(root, dep, generated=False):
    expected = json.loads(repo_file(dep['inventory']).read_text())['files']
    actual = inventory(root)
    # Exact Draco 1.5.7 generates draco_features.h in the build directory only.
    if actual != expected:
        raise ValueError('Complete source inventory differs: ' + dep['name'])
    return {'name': dep['name'], 'files': len(expected), 'archive_sha256': dep['sha256']}


def verify_inputs():
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    for row in lock['sealed_files']:
        p = repo_file(row['path'])
        if sha(p) != row['sha256'] or p.stat().st_size != row['size']:
            raise ValueError('Sealed input drift: ' + row['path'])
    archives=[]
    for dep in json.loads((HERE/'sources.lock.json').read_text())['sources']:
        manifest=json.loads(repo_file(dep['manifest']).read_text())
        combined=hashlib.sha256();total=0
        for n,row in enumerate(manifest['parts']):
            if row['filename']!='source.part%04d'%n or not 0<row['size']<=32*1024*1024:
                raise ValueError('Noncanonical archive parts')
            p=repo_file(str(PurePosixPath(dep['manifest']).parent/row['filename']))
            if p.stat().st_size!=row['size'] or sha(p)!=row['sha256']:
                raise ValueError('Original part SHA/size mismatch')
            with p.open('rb') as f:
                while b:=f.read(1024*1024):combined.update(b);total+=len(b)
        if total!=dep['size'] or combined.hexdigest()!=dep['sha256'] or manifest['sha256']!=dep['sha256'] or manifest['size']!=total:
            raise ValueError('Original reconstructed archive hash mismatch')
        inv=json.loads(repo_file(dep['inventory']).read_text())
        if inv['source_archive_sha256']!=dep['sha256']:
            raise ValueError('Inventory archive lineage mismatch')
        archives.append({'name':dep['name'],'parts':len(manifest['parts']),'size':total,
                         'sha256':combined.hexdigest(),'inventory_files':len(inv['files'])})
    return {'result': 'PASS', 'sealed_files': len(lock['sealed_files']), 'archives':archives,
            'scope': 'Repository-relative input bytes and exact complete original archive reassembly only; no native build acceptance'}
