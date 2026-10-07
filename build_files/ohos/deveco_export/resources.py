# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the host's content-blob format from the selected native93/pure8 assembly."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from common import beneath, copy_exact, digest, load, object_digest, relative, require
from source_guard import reject_pointer, source_paths

PURE_LOCK = '27aeac5e6fd312c672d09d31c6d937d3fa0bb9b77080017617ff75d4e444536d'
PURE_MANIFEST = '375ff9f5534a7ea1ba9b058aaf3603f815c6ecca70606312eb3fbaa5f4a0f8ac'
PURE_SITE = '346536f46c7171bfd7d689cbf8742f9fc157b1ffa459a859bdc04439171618dd'

def verify_pure(source: Path, runtime: Path, pure_root: Path, development):
    manifest_path = pure_root / 'resources.json'
    lock_path = source / 'build_files/ohos/deps_python_resources_sources/sources.lock.json'
    require(digest(manifest_path) == PURE_MANIFEST == development['pure_resource_manifest_sha256'], 'Selected formal pure8 manifest differs')
    require(digest(lock_path) == PURE_LOCK == development['pure_resource_lock_sha256'], 'Selected formal pure8 source lock differs')
    manifest = load(manifest_path)
    rows = manifest['site_inventory']
    seal = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(len(rows) == 193 and seal == PURE_SITE == manifest['site_tree_sha256'] == development['pure_site_tree_sha256'],
            'Require exact sealed pure193 tree')
    pins = load(lock_path)['inputs']
    require(len(pins) == 8 and len(manifest['distributions']) == 8, 'Require complete eight distributions')
    actual_distributions = {(row['name'], row['version'], row['original_archive_sha256']) for row in manifest['distributions']}
    require(actual_distributions == {(row['name'], row['version'], row['sha256']) for row in pins}, 'Pure8 versions/source archives differ')
    site = runtime / 'lib/python3.13/site-packages'
    expected = set()
    for row in rows:
        name = relative(row['path'])
        require(name not in expected, 'Duplicate pure resource path')
        expected.add(name)
        for root in (pure_root / 'site-packages', site):
            path = beneath(root, name)
            require(path.is_file() and not path.is_symlink() and path.stat().st_size == row['size'] and digest(path) == row['sha256'],
                    'Pure8 resource/notices changed: ' + name)
            with path.open('rb') as stream:
                require(stream.read(4) != b'\x7fELF', 'Native ELF in pure resource closure')
    pure_actual = {name for name, _ in source_paths(pure_root / 'site-packages')}
    assembled_actual = {name for name, _ in source_paths(site)
                        if not name.startswith(('numpy/', 'numpy-2.3.4.dist-info/')) and name != 'README.txt'}
    require(pure_actual == expected == assembled_actual, 'Old/additional pure package files inherited')
    return {'manifest_sha256': PURE_MANIFEST, 'lock_sha256': PURE_LOCK, 'site_tree_sha256': PURE_SITE,
            'file_count': 193, 'distributions': manifest['distributions'], 'all_dist_info_notices_verified': True}

class Blobs:
    def __init__(self, raw: Path):
        self.raw = raw
        self.files = []
        self.names = set()
        self.raw.mkdir(parents=True, exist_ok=True)
    def add(self, source: Path, name: str, expected=None):
        relative(name)
        require(name.casefold() not in self.names, 'Duplicate resource destination: ' + name)
        self.names.add(name.casefold())
        require(source.is_file() and not source.is_symlink(), 'Regular resource required')
        reject_pointer(source)
        with source.open('rb') as stream:
            require(stream.read(4) != b'\x7fELF', 'ELF must not be extracted from rawfiles')
        sha = digest(source)
        require(expected is None or sha == expected, 'Resource changed')
        target = self.raw / 'runtime' / sha
        if target.exists() or target.is_symlink():
            require(target.is_file() and not target.is_symlink() and target.stat().st_size == source.stat().st_size and digest(target) == sha,
                    'Existing digest-named resource blob differs/is unsafe')
        else:
            copy_exact(source, target, sha)
        self.files.append({'path': name, 'blob': 'runtime/' + sha, 'sha256': sha, 'size': source.stat().st_size})
    def tree(self, root: Path, prefix: str, skip=()):
        require(root.is_dir(), 'Required resource/notice directory missing: ' + str(root))
        for name, path in source_paths(root):
            if name in skip:
                continue
            require(not path.is_symlink(), 'Resource links need explicit owned mapping: ' + name)
            self.add(path, prefix + '/' + name)

def stage_resources(project: Path, source: Path, runtime: Path, rows, notice_inputs, native_bindings, library_rows, dependencies):
    blobs = Blobs(project / 'entry/src/main/resources/rawfile')
    blobs.tree(source / 'scripts', '5.2/scripts')
    blobs.tree(source / 'release/datafiles', '5.2/datafiles')
    blobs.tree(source / 'intern/cycles/blender/addon', '5.2/scripts/addons_core/cycles')
    native_paths = {row['path'].removeprefix('lib/python3.13/') for row in rows if row['path'].startswith('lib/python3.13/')}
    blobs.tree(runtime / 'lib/python3.13', '5.2/python/lib/python3.13', native_paths)
    support = project / 'entry/src/main/cpp/types/libblender_host'
    blobs.add(support / 'blender_hap_native.py',
              '5.2/python/lib/python3.13/site-packages/blender_hap_native.py')
    blobs.add(support / '00_blender_hap_native.pth',
              '5.2/python/lib/python3.13/site-packages/00_blender_hap_native.pth')
    blobs.add(support / 'blender_hap_documents.py',
              '5.2/scripts/startup/blender_hap_documents.py')
    blobs.tree(runtime / 'share/licenses', 'notices/python-numpy')
    # Full source/tpr retains all original notices, even when an archive is not expanded.
    blobs.add(source / 'COPYING', 'notices/blender/COPYING')
    for item in notice_inputs:
        root = Path(item['root']).resolve(strict=True)
        manifest = load(Path(item['inventory']))
        require(manifest.get('files'), 'Explicit complete notice inventory required')
        require({name for name, _ in source_paths(root)} == {row['path'] for row in manifest['files']}, 'Notice file set differs')
        for row in manifest['files']:
            path = beneath(root, row['path'])
            require(path.stat().st_size == row['size'], 'Notice size differs')
            blobs.add(path, 'notices/' + relative(item['label']) + '/' + row['path'], row['sha256'])
    manifest = {'schema': 1, 'blenderVersion': '5.2', 'pythonVersion': '3.13',
                'files': blobs.files, 'native': native_bindings, 'libraries': library_rows,
                'nativeDependencies': dependencies}
    manifest['id'] = object_digest(manifest)
    return manifest
