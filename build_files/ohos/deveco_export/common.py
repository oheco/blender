# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared guards. These helpers never compile, sign, download, or install."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

class Rejected(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise Rejected(message)

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def object_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

def load(path: Path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    with path.open(encoding='utf-8') as stream:
        return json.load(stream, object_pairs_hook=pairs)

def relative(value: str) -> str:
    require(isinstance(value, str) and value and '\\' not in value and
            not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Unsafe relative path')
    parts = value.split('/')
    require(not value.startswith('/') and all(p and p not in ('.', '..') for p in parts),
            'Unsafe relative path: ' + value)
    require(PurePosixPath(value).as_posix() == value, 'Noncanonical path: ' + value)
    return value

def explicit_path(value, label: str) -> Path:
    require(isinstance(value, str) and value and Path(value).is_absolute(), 'Explicit absolute input required: ' + label)
    return Path(value).resolve(strict=True)

def beneath(root: Path, value: str) -> Path:
    path = root / relative(value)
    require(path.resolve().is_relative_to(root.resolve()), 'Path escapes input: ' + value)
    return path

def metadata_output(path: Path, roots=()) -> Path:
    # Do not resolve the final component through an existing symlink.
    path = Path(os.path.abspath(path))
    require(not path.exists() and not path.is_symlink(), 'Output must be absent: ' + str(path))
    path = path.parent.resolve() / path.name
    for root in roots:
        root = root.resolve()
        require(not path.is_relative_to(root) and not root.is_relative_to(path), 'Input/output overlap')
    return path

def ensure_fresh_output(path: Path, roots=()) -> Path:
    path = metadata_output(path, roots)
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve(strict=True)
    require(path.parent.is_relative_to(cache), 'Output must be under explicit private XDG_CACHE_HOME')
    return path

def write_new(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')

def copy_exact(source: Path, target: Path, expected=None):
    require(source.is_file() and not source.is_symlink(), 'Regular input required: ' + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            h.update(block)
            outgoing.write(block)
    observed = h.hexdigest()
    require(expected is None or observed == expected, 'Input bytes changed: ' + str(source))
    os.chmod(target, stat.S_IMODE(source.stat().st_mode))
    require(digest(target) == observed and (target.stat().st_mode & 0o111) == (source.stat().st_mode & 0o111),
            'Copied bytes/modes differ: ' + str(target))
    return {'sha256': observed, 'size': target.stat().st_size, 'mode': stat.S_IMODE(target.stat().st_mode)}

def record_file(root: Path, path: Path):
    require(not path.is_symlink() and path.is_file(), 'Regular sealed file required')
    return {'path': relative(path.relative_to(root).as_posix()), 'sha256': digest(path),
            'size': path.stat().st_size, 'mode': stat.S_IMODE(path.stat().st_mode)}

def verify_record(root: Path, row):
    path = beneath(root, row['path'])
    require(not path.is_symlink() and path.is_file(), 'Missing sealed file: ' + row['path'])
    require(path.stat().st_size == row['size'] and digest(path) == row['sha256'], 'Sealed bytes differ: ' + row['path'])
    if 'mode' in row:
        require((path.stat().st_mode & 0o111) == (row['mode'] & 0o111), 'Sealed executable bits differ: ' + row['path'])
    return path

def verify_self_lock(directory: Path):
    lock = load(directory / 'inputs.lock.json')
    require(lock['schema'] == 1, 'Wrong exporter lock schema')
    for row in lock['files']:
        verify_record(directory, row)
    return digest(directory / 'inputs.lock.json')
