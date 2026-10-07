# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay the complete official SSE source using existing sealed public helpers."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
from common import HERE, sha, load, dump, record

REGISTRY = 'tpr/sources/sse2neon-227cc413fb2d50b2a10073087be96b59d5364aea'
ARCHIVE_SHA = '3427a495743bb6fd1b5f9f806b80f57d67b1ac7ccf39a5f44aedd487fd7e6da1'
HEADER_SHA = '69bb92f9553692e25079f55b6beb2de31ee01a8b270d043bf4de7d2d1b450ed9'


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def verify_sse(repo):
    vendor = module(repo / 'build_files/ohos/vendor_archive.py', 'editor_vendor_archive')
    manifest = vendor.verify(repo / REGISTRY)
    if manifest['sha256'] != ARCHIVE_SHA or manifest['size'] != 136342:
        raise ValueError('Official original SSE source changed')
    expected = load(HERE / 'sse2neon/inventory.json')
    if expected['registry_id'] != REGISTRY or expected['original_archive_sha256'] != ARCHIVE_SHA:
        raise ValueError('SSE source inventory provenance differs')
    return vendor, manifest, expected


def prepare_sse(repo, root, tmp):
    vendor, manifest, expected = verify_sse(repo)
    guard = module(repo / 'build_files/ohos/deps_python_resources_sources/source_guard.py', 'editor_source_guard')
    files = [{k: row[k] for k in ('path', 'size', 'sha256')} for row in expected['files']]
    directory = root / 'sources/sse2neon'
    receipt = root / 'sse2neon-source.json'
    if directory.exists():
        if not receipt.is_file() or guard.inventory(directory) != files:
            raise ValueError('Refuse partial/unrecorded/drifted owned SSE source')
        data = load(receipt)
        if data['source_directory'] != str(directory) or data['archive_sha256'] != ARCHIVE_SHA or data['header_sha256'] != sha(directory / 'sse2neon.h'):
            raise ValueError('SSE owned publication receipt drift')
        return data
    if receipt.exists():
        raise ValueError('SSE publication is missing its recorded source tree')
    archive = root / 'archives' / manifest['filename']
    vendor.materialize(repo / REGISTRY, archive)
    with tempfile.TemporaryDirectory(prefix='editor-source-', dir=tmp) as scratch:
        staging = Path(scratch)
        name = guard.safe_extract(archive, staging)
        extracted = staging / name
        if guard.inventory(extracted) != files or sha(extracted / 'sse2neon.h') != HEADER_SHA:
            raise ValueError('Complete official SSE source inventory/header differs')
        directory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted, directory)
    if guard.inventory(directory) != files:
        raise ValueError('SSE publication byte verification failed')
    data = {'schema': 1, 'status': 'PASS_SOURCE_ONLY', 'source_directory': str(directory),
            'registry_manifest': record(repo / REGISTRY / 'manifest.json'), 'archive_sha256': ARCHIVE_SHA,
            'archive_size': manifest['size'], 'source_files': len(files), 'inventory': record(HERE / 'sse2neon/inventory.json'),
            'header_sha256': HEADER_SHA, 'license_sha256': sha(directory / 'LICENSE'),
            'native_header_compatibility': 'NOT_RUN', 'native_full': 'NOT_RUN'}
    dump(receipt, data)
    return data
