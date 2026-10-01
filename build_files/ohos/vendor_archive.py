#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Vendor complete fixed source archives as ordinary Git parts, without LFS.

Source bytes are not transformed. Part hashes and the original archive hash are
verified before offline materialization. Extraction is left to each audited
build recipe, on a case-sensitive private filesystem.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHUNK = 32 * 1024 * 1024
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f'Unsafe archive identifier: {value!r}')
    return value


def read_manifest(directory):
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('schema_version') != 1 or manifest.get('format') != 'original-archive-parts':
        raise ValueError('Unsupported archive manifest')
    safe_name(manifest['filename'])
    if not SHA.fullmatch(manifest['sha256']):
        raise ValueError('Invalid original SHA-256')
    if not isinstance(manifest['size'], int) or manifest['size'] <= 0:
        raise ValueError('Invalid original size')
    parts = manifest['parts']
    if not parts or len({p['filename'] for p in parts}) != len(parts):
        raise ValueError('Empty/duplicate parts')
    for part in parts:
        safe_name(part['filename'])
        if not SHA.fullmatch(part['sha256']) or not 0 < part['size'] <= CHUNK:
            raise ValueError('Invalid part digest/size')
    if sum(p['size'] for p in parts) != manifest['size']:
        raise ValueError('Part sizes do not reconstruct original archive')
    return manifest


def verify(directory):
    manifest = read_manifest(directory)
    original = hashlib.sha256()
    for part in manifest['parts']:
        path = directory / part['filename']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != part['size']:
            raise ValueError(f'Missing/unsafe part: {path}')
        h = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                h.update(block)
                original.update(block)
        if h.hexdigest() != part['sha256']:
            raise ValueError(f'Part digest mismatch: {path}')
    if original.hexdigest() != manifest['sha256']:
        raise ValueError('Reconstructed original digest mismatch')
    return manifest


def pack(entry_file, archive, destination):
    entry = json.loads(entry_file.read_text())
    for key in ('name', 'version', 'filename'):
        safe_name(entry[key])
    if not SHA.fullmatch(entry['sha256']):
        raise ValueError('Source must have a fixed SHA-256')
    if not entry.get('url') or not entry.get('license'):
        raise ValueError('Upstream URL and license are required')
    if archive.name != entry['filename'] or digest(archive) != entry['sha256']:
        raise ValueError('Archive name/digest does not match fixed input')
    size = archive.stat().st_size
    if entry.get('size') is not None and size != entry['size']:
        raise ValueError('Archive size differs from fixed input')
    if destination.exists():
        raise ValueError(f'Refusing to overwrite existing vendored source: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        parts = []
        with archive.open('rb') as stream:
            number = 0
            while block := stream.read(CHUNK):
                filename = f'source.part{number:04d}'
                (destination / filename).write_bytes(block)
                parts.append({'filename': filename, 'size': len(block),
                              'sha256': hashlib.sha256(block).hexdigest()})
                number += 1
        manifest = {key: entry[key] for key in
                    ('name', 'version', 'kind', 'filename', 'url', 'license',
                     'license_files', 'upstream_repository', 'tag', 'commit') if key in entry}
        manifest.update(schema_version=1, format='original-archive-parts',
                        size=size, sha256=entry['sha256'], parts=parts)
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        verify(destination)
    except BaseException:
        # This invocation created this directory; no existing user tree is removed.
        shutil.rmtree(destination)
        raise
    return manifest


def materialize(directory, output):
    manifest = verify(directory)
    private_roots = [Path(os.environ[key]).resolve() for key in ('XDG_CACHE_HOME', 'TMPDIR')]
    resolved = output.resolve()
    if not any(root in resolved.parents for root in private_roots):
        raise ValueError('Materialize into the private XDG_CACHE_HOME or TMPDIR, not HOME')
    if output.is_symlink():
        raise ValueError(f'Refusing a symbolic-link cache output: {output}')
    if output.exists():
        if output.is_file() and output.stat().st_size == manifest['size'] and digest(output) == manifest['sha256']:
            return manifest
        raise ValueError(f'Refusing conflicting materialized archive: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = private_roots[1]
    if temporary.stat().st_dev != output.parent.stat().st_dev:
        raise ValueError('TMPDIR and source cache must share a filesystem for atomic publication')
    staging = None
    try:
        with tempfile.NamedTemporaryFile(prefix='vendor-source-', dir=temporary,
                                         delete=False) as stream:
            staging = Path(stream.name)
            for part in manifest['parts']:
                with (directory / part['filename']).open('rb') as source:
                    shutil.copyfileobj(source, stream)
        if digest(staging) != manifest['sha256']:
            raise ValueError('Materialized archive digest mismatch')
        # A hard-link attempt was denied in this private target filesystem.
        # Serialize cooperating source-cache writers and recheck the
        # destination under the lock before an atomic same-filesystem rename.
        with (output.parent / '.source-publication.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if output.is_symlink():
                raise ValueError(f'Conflicting symbolic-link source publication: {output}')
            if output.exists():
                if not output.is_file() or digest(output) != manifest['sha256']:
                    raise ValueError(f'Conflicting concurrent source publication: {output}')
                staging.unlink()
            else:
                staging.rename(output)
            staging = None
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create = commands.add_parser('pack')
    create.add_argument('--entry', type=Path, required=True)
    create.add_argument('--archive', type=Path, required=True)
    create.add_argument('--destination', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('directory', type=Path)
    restore = commands.add_parser('materialize')
    restore.add_argument('directory', type=Path)
    restore.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'pack':
        record = pack(args.entry, args.archive, args.destination)
    elif args.action == 'verify':
        record = verify(args.directory)
    else:
        record = materialize(args.directory, args.output)
    print(json.dumps({'verified': True, 'name': record['name'], 'version': record['version'],
                      'size': record['size'], 'sha256': record['sha256'],
                      'parts': len(record['parts'])}, indent=2))


if __name__ == '__main__':
    main()
