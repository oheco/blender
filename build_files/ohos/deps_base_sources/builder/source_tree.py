# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent strict extraction and exact source patch ownership.

Adapted from core source_tree.py, with stricter names/types/case collisions.
Twelve complete upstream trees are preserved, including two inventory-bound internal Zstd links.
The reviewed OHOS Zstd COVER patch has exact forward/reverse bytepins.
Recipe wrappers and prefix-owned metadata remain separately recorded.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import zipfile
from io_utils import HERE, relative, repo_file, sha, sources, vendor_archive, write_json

ACTIVE = ['zlib','zstd','jpeg','png','brotli','freetype','harfbuzz','fribidi','fmt','imath','meson','pkgconf']


def extract_complete(archive, output, archive_root, allowed_links=()):
    output.mkdir(parents=True)
    seen, folded, kinds = set(), {}, {}
    allowed = {r['path']:r['target'] for r in allowed_links if r['type']=='symlink'}
    pending = []

    def place(name, directory, data=None, mode=0o644, link_target=None):
        # The archive format permits a single terminal slash only for directories.
        canonical = name[:-1] if directory and name.endswith('/') else name
        member = relative(canonical)
        if member.parts[0] != archive_root:
            raise ValueError('Unexpected archive root')
        key = member.as_posix()
        if key in seen:
            raise ValueError('Duplicate archive entry')
        seen.add(key)
        for i in range(1, len(member.parts) + 1):
            prefix = '/'.join(member.parts[:i])
            prior = folded.setdefault(prefix.casefold(), prefix)
            if prior != prefix:
                raise ValueError('Case collision in archive')
            want_dir = i < len(member.parts) or directory
            old = kinds.get(prefix)
            if old is not None and old != want_dir:
                raise ValueError('File-directory archive collision')
            kinds[prefix] = want_dir
        if len(member.parts) == 1:
            if not directory:
                raise ValueError('Archive root is not directory')
            return
        target = output.joinpath(*member.parts[1:])
        if directory:
            target.mkdir(parents=True, exist_ok=True)
        elif link_target is not None:
            rel = target.relative_to(output).as_posix()
            if allowed.get(rel) != link_target or Path(link_target).is_absolute() or '\\' in link_target:
                raise ValueError('Unbound/absolute original source symlink')
            normalized = os.path.normpath(str(relative(rel).parent / link_target))
            if normalized == '..' or normalized.startswith('../') or Path(normalized).is_absolute():
                raise ValueError('Source symlink escapes complete owned tree')
            pending.append((target, link_target, normalized))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(data, stream, 1024 * 1024)
            target.chmod((mode & 0o755) or 0o644)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not member.is_dir()):
                    raise ValueError('Symlink/special archive entry forbidden')
                if member.is_dir():
                    place(member.filename, True)
                else:
                    with source.open(member) as data:
                        place(member.filename, False, data, mode)
    else:
        with tarfile.open(archive) as source:
            for member in source:
                if member.isdir():
                    place(member.name, True)
                elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    with source.extractfile(member) as data:
                        place(member.name, False, data, member.mode)
                elif member.issym():
                    place(member.name, False, link_target=member.linkname)
                else:
                    raise ValueError('Unbound symlink/hardlink/special archive entry forbidden')
    if {p.relative_to(output).as_posix() for p,_,_ in pending} != set(allowed):
        raise ValueError('Original bounded source link inventory differs')
    for target, original, normalized in pending:
        referent = output / normalized
        if not referent.is_file() or referent.is_symlink():
            raise ValueError('Bound source symlink target must be an original internal regular file')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.symlink_to(original)


def patch_records(name):
    return [r for r in sources()['patches'] if r['name'] == name]


def verify_tree(root, name, patched=False):
    dep = next(d for d in sources()['sources'] if d['name'] == name)
    rows = json.loads(repo_file(dep['inventory']).read_text())['files']
    expected = {r['path'] for r in rows}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != expected:
        raise ValueError('Complete source inventory differs: ' + name + ' ' + str(actual ^ expected))
    adapted = {r['path']: r['after_sha256'] for p in patch_records(name) for r in p['files']} if patched else {}
    for row in rows:
        file = root / relative(row['path'])
        if row.get('type') == 'symlink':
            if not file.is_symlink() or os.readlink(file) != row['target'] or root.resolve() not in file.resolve().parents:
                raise ValueError('Original bounded source symlink drift: '+name+'/'+row['path'])
        elif file.is_symlink() or not file.is_file() or sha(file) != adapted.get(row['path'], row['sha256']):
            raise ValueError('Original/patched source bytes drift: ' + name + '/' + row['path'])
    return {'name': name, 'records': len(rows), 'verification_phase': 'after-applicable-patches' if patched else 'pristine', 'applied_patches': len(patch_records(name)) if patched else 0}


def materialize(root, runner):
    rows = []
    for dep in sources()['sources']:
        archive = root / 'archives' / dep['archive_filename']
        vendor_archive.materialize(repo_file(dep['registry_manifest']).parent, archive)
        if sha(archive) != dep['sha256'] or sha(archive, dep['formal_hash']['algorithm'].lower()) != dep['formal_hash']['value']:
            raise ValueError('Original SHA/formal hash mismatch')
        rows.append({'name': dep['name'], 'archive': str(archive), 'sha256': sha(archive), 'size': archive.stat().st_size})
    result = {'result': 'PASS complete pristine original archives', 'archives': rows, 'network_used': False}
    write_json(root / 'materialization.json', result)
    return result


def replay_patches(tree, name, runner, git):
    records = patch_records(name)
    for record in records:
        for row in record['files']:
            if sha(tree / relative(row['path'])) != row['before_sha256']:
                raise ValueError('Owned patch before hash differs')
        patch = repo_file(record['path'])
        runner.run([git, 'apply', '--check', patch], name + '-patch-check', cwd=tree)
        runner.run([git, 'apply', patch], name + '-patch-forward', cwd=tree)
        for row in record['files']:
            if sha(tree / relative(row['path'])) != row['after_sha256']:
                raise ValueError('Owned patch after hash differs')
        runner.run([git, 'apply', '--reverse', '--check', patch], name + '-patch-reverse-check', cwd=tree)
    if records:
        with tempfile.TemporaryDirectory(prefix='base-patch-reverse-', dir=runner.tmp) as td:
            copy = Path(td) / 'source'
            shutil.copytree(tree, copy, symlinks=True)
            for record in reversed(records):
                runner.run([git, 'apply', '--reverse', repo_file(record['path'])], name + '-patch-reverse', cwd=copy)
            verify_tree(copy, name)
    return verify_tree(tree, name, patched=True)


def prepare(root, runner, git):
    receipt = root / 'sources.json'
    if receipt.exists():
        for name in ACTIVE:
            verify_tree(root / 'sources' / name, name, patched=True)
        return json.loads(receipt.read_text())
    materialize(root, runner)
    if (root / 'sources').exists():
        raise ValueError('Refuse incomplete existing source output')
    with tempfile.TemporaryDirectory(prefix='base-source-prepare-', dir=runner.tmp) as td:
        stage = Path(td)
        rows = []
        for dep in sources()['sources']:
            if dep['name'] not in ACTIVE:
                continue
            tree = stage / 'sources' / dep['name']
            extract_complete(root / 'archives' / dep['archive_filename'], tree, dep['archive_root'], dep.get('links',()))
            verify_tree(tree, dep['name'])
            rows.append(replay_patches(tree, dep['name'], runner, git))
        (stage / 'sources').rename(root / 'sources')
    result = {'result': 'PASS independent complete selected source trees and real forward/reverse patch hashes',
              'sources': rows, 'excluded': {}, 'original_bounded_source_symlinks_preserved': 2,
              'temporary_tree_cleaned': True, 'native_acceptance': False}
    write_json(receipt, result)
    return result
