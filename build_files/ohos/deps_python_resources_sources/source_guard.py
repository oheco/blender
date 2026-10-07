#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded pristine-sdist extraction; see SOURCE-ATTRIBUTION.md for provenance."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def seal(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def canonical(name):
    if (not isinstance(name, str) or not name or name.startswith('/') or
            '\\' in name or ':' in name or '\x00' in name or
            any(p in ('', '.', '..') for p in name.split('/')) or
            PurePosixPath(name).as_posix() != name):
        raise ValueError('Non-canonical relative path: ' + repr(name))
    return PurePosixPath(name).parts


def inventory(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Inventory requires a real directory')
    result, prefixes = [], {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        parts = canonical(relative)
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('Link/special file in source tree: ' + relative)
        for length in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:length])
            old = prefixes.setdefault(prefix.casefold(), prefix)
            if old != prefix:
                raise ValueError('Case-colliding tree prefix: ' + prefix)
        if stat.S_ISREG(mode):
            result.append({'path': relative, 'size': path.stat().st_size, 'sha256': sha(path)})
    return result


def verify_archive(path, pin):
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != pin['size'] or
            sha(path) != pin['sha256']):
        raise ValueError('Complete original archive SHA/size differs: ' + pin['name'])


def safe_extract(archive, destination, expected_root=None):
    """Validate every member before writes; never apply upstream modes/links."""
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Extraction requires a new empty real directory')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        if len(members) > 10000 or any(m.size < 0 for m in members) or sum(m.size for m in members) > 64 * 1024 * 1024:
            raise ValueError('Source archive exceeds fixed member/byte ceilings')
        seen, nodes, spellings, roots = set(), {}, {}, set()
        for member in members:
            name = member.name
            if name.endswith('/') and member.isdir():
                name = name[:-1]
            parts = canonical(name)
            if not (member.isfile() or member.isdir()):
                raise ValueError('Link/special archive member: ' + name)
            if name in seen:
                raise ValueError('Duplicate archive member: ' + name)
            seen.add(name)
            roots.add(parts[0])
            for length in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:length])
                previous = spellings.setdefault(prefix.casefold(), prefix)
                if previous != prefix:
                    raise ValueError('Case-colliding archive prefix: ' + prefix)
                kind = 'file' if length == len(parts) and member.isfile() else 'directory'
                if prefix in nodes and nodes[prefix] != kind:
                    raise ValueError('File/directory archive collision: ' + prefix)
                nodes[prefix] = kind
        if len(roots) != 1 or (expected_root is not None and roots != {expected_root}):
            raise ValueError('Archive has wrong/multiple canonical roots')
        root = next(iter(roots))
        if nodes[root] != 'directory':
            raise ValueError('Archive root must be a directory')
        for member in members:
            name = member.name[:-1] if member.isdir() and member.name.endswith('/') else member.name
            target = destination.joinpath(*canonical(name))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, target.open('xb') as output:
                    shutil.copyfileobj(stream, output)
                if target.stat().st_size != member.size:
                    raise ValueError('Truncated source member: ' + name)
        return root
