# SPDX-License-Identifier: GPL-2.0-or-later
"""Stdlib input bindings and private IO; no build or platform substitutions."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + x)))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def relative(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or p.as_posix() != value or any(x in ('', '.', '..') for x in value.split('/')) or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Noncanonical relative path: ' + repr(value))
    return p


def absolute(value, exists=True):
    p = Path(value)
    if not p.is_absolute() or any(c in str(p) for c in (';', '$', '\n', '\r', '\x00')):
        raise ValueError('Explicit safe absolute path required: ' + str(p))
    return p.resolve(strict=exists)


def record(path):
    path = Path(path)
    return {'path': str(path), 'size': path.stat().st_size, 'sha256': sha(path)}


def bound(row):
    if not isinstance(row, dict) or not {'path', 'size', 'sha256'} <= row.keys():
        raise ValueError('Missing bound-file record')
    path = absolute(row['path'])
    if type(row['size']) is not int or row['size'] < 0 or not path.is_file() or path.stat().st_size != row['size'] or sha(path) != row['sha256']:
        raise ValueError('Bound input changed: ' + str(path))
    return path


def inventory(root, exclude_git=False):
    root = Path(root).resolve(strict=True)
    rows = []
    folded = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        if exclude_git and Path(current) == root:
            dirs[:] = [x for x in dirs if x != '.git']
            files = [x for x in files if x != '.git']
        for name in sorted(dirs + files):
            p = Path(current) / name
            rel = p.relative_to(root).as_posix()
            relative(rel)
            if rel.casefold() in folded:
                raise ValueError('Case-colliding input paths: ' + rel)
            folded.add(rel.casefold())
            if p.is_symlink():
                target = os.readlink(p)
                resolved = p.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.is_file() or Path(target).is_absolute():
                    raise ValueError('External/directory/dangling link: ' + rel)
                rows.append({'path': rel, 'type': 'symlink', 'target': target,
                             'target_sha256': sha(resolved), 'target_size': resolved.stat().st_size})
            elif p.is_file():
                rows.append({'path': rel, 'type': 'file', 'size': p.stat().st_size, 'sha256': sha(p)})
            elif not p.is_dir():
                raise ValueError('Special source file: ' + rel)
        dirs[:] = sorted(x for x in dirs if not (Path(current) / x).is_symlink())
    return sorted(rows, key=lambda x: x['path'])


def verify_recipe(repo):
    lock = repo / 'build_files/ohos/editor/inputs.lock.json'
    data = load(lock)
    if data.get('kind') != 'offline-editor-orchestration-inputs' or data.get('schema_version') != 1:
        raise ValueError('Unknown editor recipe seal')
    names = set()
    for row in data['sealed_files']:
        rel = str(relative(row['path']))
        if rel.casefold() in names:
            raise ValueError('Repeated recipe path')
        names.add(rel.casefold())
        p = repo / rel
        if p.is_symlink() or not p.is_file() or p.stat().st_size != row['size'] or sha(p) != row['sha256']:
            raise ValueError('Frozen editor input changed: ' + rel)
    own = {p.relative_to(repo).as_posix() for p in (repo / 'build_files/ohos/editor').rglob('*') if p.is_file() and p.name != 'inputs.lock.json'}
    expected = {x['path'] for x in data['sealed_files'] if x['path'].startswith('build_files/ohos/editor/')}
    if own != expected:
        raise ValueError('Unexpected/missing editor recipe sibling')
    return {'input_lock_sha256': sha(lock), 'sealed_files': len(data['sealed_files'])}


def verify_snapshot(repo, path, expected):
    if sha(path) != expected:
        raise ValueError('Source snapshot JSON SHA mismatch')
    data = load(path)
    if data.get('schema') != 1 or data.get('kind') != 'editor-source-snapshot' or data.get('scope') not in ('complete', 'sealed-input-mini'):
        raise ValueError('Explicit editor source snapshot contract required')
    rows = inventory(repo, exclude_git=True)
    if rows != data['files'] or digest(rows) != data['tree_sha256']:
        raise ValueError('Source snapshot file set/content drift')
    return {'snapshot_sha256': expected, 'source_tree_sha256': data['tree_sha256'], 'scope': data['scope'], 'file_count': len(rows)}
