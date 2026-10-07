# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent strict extraction and exact source patch ownership.

Adapted from the repository core/volume patterns with strict names/types/case
collisions and exactly sealed original in-tree links. OpenEXR and Expat each
have one such link. Excluded Zstd's two links remain in its complete archive
and inventory and are never extracted by this selected profile.
"""
import json
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import zipfile
from io_utils import HERE, relative, repo_file, sha, sources, vendor_archive, write_json

ACTIVE = ['zlib', 'png', 'jpeg', 'fmt', 'imath', 'tbb', 'deflate', 'openjph',
          'yamlcpp', 'pystring', 'expat', 'minizipng', 'tiff', 'robinmap',
          'pugixml', 'openjpeg', 'webp', 'openexr', 'opencolorio', 'openimageio']


def extract_complete(archive, output, archive_root, approved_links=None):
    if output.exists():
        raise ValueError('Extraction output must be absent')
    output.mkdir(parents=True)
    seen, folded, kinds, links, directory_modes = set(), {}, {}, [], []
    approved_links = approved_links or {}

    def place(name, directory, data=None, mode=0o644):
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
            directory_modes.append((target, mode & 0o777))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(data, stream, 1024 * 1024)
            target.chmod(mode & 0o777)

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
                    place(member.name, True, mode=member.mode)
                elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    with source.extractfile(member) as data:
                        place(member.name, False, data, member.mode)
                elif member.issym():
                    name = member.name.removeprefix(archive_root + '/')
                    if approved_links.get(name) != member.linkname:
                        raise ValueError('Unpinned source link forbidden')
                    # Register its path/type without following it during extraction.
                    from io import BytesIO
                    place(member.name, False, BytesIO(b''))
                    target = output / relative(name)
                    target.unlink()
                    if member.linkname.startswith('/') or '\\' in member.linkname or '\x00' in member.linkname:
                        raise ValueError('Unsafe pinned source link')
                    resolved = (target.parent / member.linkname).resolve()
                    if not resolved.is_relative_to(output.resolve()):
                        raise ValueError('Pinned source link escapes extraction root')
                    links.append((target, member.linkname))
                else:
                    raise ValueError('Symlink/hardlink/special archive entry forbidden')
    # Only sealed original in-tree links; never extraction parents or link chains.
    for target, link in links:
        resolved = (target.parent / link).resolve()
        if not resolved.exists() or resolved.is_symlink():
            raise ValueError('Pinned link target must exist in complete source')
        target.symlink_to(link, target_is_directory=resolved.is_dir())
    for directory, mode in reversed(directory_modes):
        directory.chmod(mode)


def patch_records(name):
    return [r for r in sources()['patches'] if r['name'] == name]


def verify_tree(root, name, patched=False):
    dep = next(d for d in sources()['sources'] if d['name'] == name)
    inventory = json.loads(repo_file(dep['inventory']).read_text())
    rows = inventory['files']
    adapted = {r['path']: r['after_sha256'] for p in patch_records(name) for r in p['files']} if patched else {}
    expected = {r['path'] for r in rows} | set(adapted)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != expected:
        raise ValueError('Complete source inventory differs: ' + name + ' ' + str(actual ^ expected))
    for path, digest in adapted.items():
        file = root / relative(path)
        if file.is_symlink() or not file.is_file() or sha(file) != digest:
            raise ValueError('Patched source bytes drift: ' + name + '/' + path)
    for row in rows:
        file = root / relative(row['path'])
        if 'symlink' in row:
            if not file.is_symlink() or __import__('os').readlink(file) != row['symlink'] or not file.resolve().is_relative_to(root.resolve()):
                raise ValueError('Pinned source link drift: ' + name + '/' + row['path'])
        elif file.is_symlink() or not file.is_file() or sha(file) != adapted.get(row['path'], row['sha256']):
            raise ValueError('Original/patched source bytes drift: ' + name + '/' + row['path'])
    expected_dirs = {r['path'] for r in inventory['directories']}
    for path in expected:
        expected_dirs.update(p.as_posix() for p in relative(path).parents if p.as_posix() != '.')
    actual_dirs = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_dir() and not p.is_symlink()}
    if actual_dirs != expected_dirs:
        raise ValueError('Complete source directory/type inventory differs: ' + name)
    for row in inventory['directories']:
        if stat.S_IMODE((root / relative(row['path'])).stat().st_mode) != row['mode']:
            raise ValueError('Original source directory mode drift: ' + name + '/' + row['path'])
    for row in rows:
        if 'symlink' not in row and stat.S_IMODE((root / relative(row['path'])).stat().st_mode) != row['mode']:
            raise ValueError('Original source file mode drift: ' + name + '/' + row['path'])
    return {'name': name, 'records': len(rows), 'directories': len(actual_dirs), 'patched': patched}


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
            file = tree / relative(row['path'])
            if (sha(file) if file.exists() else None) != row['before_sha256']:
                raise ValueError('Owned patch before hash differs')
        patch = repo_file(record['path'])
        runner.run([git, 'apply', '--check', patch], name + '-patch-check', cwd=tree)
        runner.run([git, 'apply', patch], name + '-patch-forward', cwd=tree)
        for row in record['files']:
            if sha(tree / relative(row['path'])) != row['after_sha256']:
                raise ValueError('Owned patch after hash differs')
        runner.run([git, 'apply', '--reverse', '--check', patch], name + '-patch-reverse-check', cwd=tree)
    if records:
        with tempfile.TemporaryDirectory(prefix='color-patch-reverse-', dir=runner.tmp) as td:
            copy = Path(td) / 'source'
            shutil.copytree(tree, copy, symlinks=True)
            for record in reversed(records):
                runner.run([git, 'apply', '--reverse', repo_file(record['path'])], name + '-patch-reverse', cwd=copy)
            verify_tree(copy, name)
    return verify_tree(tree, name, patched=True)


def prepare(root, runner, git, names=None):
    receipt = root / 'sources.json'
    if receipt.exists():
        for name in ACTIVE:
            verify_tree(root / 'sources' / name, name, patched=True)
        return json.loads(receipt.read_text())
    selected = names or ACTIVE
    if any(name not in ACTIVE for name in selected):
        raise ValueError('Unknown selected prepare source')
    if not (root / 'materialization.json').is_file():
        materialize(root, runner)
    source_parent = root / 'sources'
    if source_parent.is_symlink() or (source_parent.exists() and not source_parent.is_dir()):
        raise ValueError('Unsafe source parent type')
    source_parent.mkdir(exist_ok=True)
    rows = []
    for name in selected:
        dep = next(d for d in sources()['sources'] if d['name'] == name)
        tree = source_parent / name
        owned = root / 'prepared' / (name + '.json')
        if owned.is_symlink():
            raise ValueError('Unsafe source receipt type')
        if owned.exists():
            record = json.loads(owned.read_text())
            if record['input_lock_sha256'] != sha(HERE / 'inputs.lock.json') or not tree.is_dir() or tree.is_symlink():
                raise ValueError('Selected source ownership/input seal differs')
            rows.append(verify_tree(tree, name, patched=True))
            continue
        if tree.exists() or tree.is_symlink():
            raise ValueError('Refuse incomplete/unowned selected source tree')
        archive = root / 'archives' / dep['archive_filename']
        if archive.is_symlink() or sha(archive) != dep['sha256'] or sha(archive, dep['formal_hash']['algorithm'].lower()) != dep['formal_hash']['value']:
            raise ValueError('Owned complete archive drift')
        with tempfile.TemporaryDirectory(prefix='color-source-prepare-', dir=runner.tmp) as td:
            stage_tree = Path(td) / name
            inventory = json.loads(repo_file(dep['inventory']).read_text())['files']
            approved_links = {r['path']: r['symlink'] for r in inventory if 'symlink' in r}
            extract_complete(archive, stage_tree, dep['archive_root'], approved_links)
            verify_tree(stage_tree, name)
            row = replay_patches(stage_tree, name, runner, git)
            stage_tree.rename(tree)
        write_json(owned, {'input_lock_sha256': sha(HERE / 'inputs.lock.json'), 'source': row,
                           'complete_tree_published': True, 'forward_reverse_verified': True})
        rows.append(row)
    complete = all((root / 'prepared' / (name + '.json')).is_file() for name in ACTIVE)
    result = {'result': 'PASS complete selected source groups and real forward/reverse hashes',
              'sources': rows, 'all_selected_complete': complete, 'selected_count': len(ACTIVE),
              'excluded': {'zstd': 'Complete archive verified; codecs OFF; original symlink entries not extracted'},
              'temporary_tree_cleaned': True, 'native_acceptance': False}
    if complete:
        result['sources'] = [verify_tree(source_parent / name, name, patched=True) for name in ACTIVE]
        result['result'] = 'PASS independent complete selected20 source trees and real forward/reverse patch hashes'
        write_json(receipt, result)
    return result
