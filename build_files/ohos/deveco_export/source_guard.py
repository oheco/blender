# SPDX-License-Identifier: GPL-2.0-or-later
"""Inventory and copy a caller-selected complete source tree exactly once per export."""
from __future__ import annotations
import os
from pathlib import Path
import stat
import subprocess
from common import Rejected, beneath, copy_exact, digest, load, object_digest, relative, require
from evidence import CORE_SOURCE_BINDINGS

LFS_HEADER = b'version https://git-lfs.github.com/spec/v1'
# Git administrative state and interpreter bytecode are not build inputs.
EXCLUDED_DIRS = {'.git', '__pycache__'}
REQUIRED = (
    'CMakeLists.txt', 'COPYING', 'intern/ghost/GHOST_OHOSHost.h',
    'intern/ghost/GHOST_OHOSEngine.h', 'intern/ghost/GHOST_OHOSNative.h', 'source/creator/creator_ohos.h',
    'source/creator/creator_ohos_files.h', 'source/creator/ohos/core.exports.map',
    'scripts/startup/bl_ui/__init__.py', 'scripts/startup/bl_operators/__init__.py',
    'scripts/addons_core/io_scene_gltf2/__init__.py',
    'scripts/addons_core/bl_pkg/bl_extension_worker.py',
    'release/datafiles/colormanagement/config.ocio',
    'intern/cycles/blender/addon/__init__.py', 'build_files/ohos/vendor_archive.py',
    'build_files/ohos/deps_python_resources_sources/sources.lock.json',
)
REQUIRED = tuple(sorted(set(REQUIRED) | CORE_SOURCE_BINDINGS))

def source_paths(root: Path):
    seen = set()
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED_DIRS)
        symlink_dirs = [d for d in directories if (Path(folder) / d).is_symlink()]
        directories[:] = [d for d in directories if d not in symlink_dirs]
        for name in sorted(files + symlink_dirs):
            if name.endswith(('.pyc', '.pyo')):
                continue
            path = Path(folder) / name
            name = relative(path.relative_to(root).as_posix())
            require(name.casefold() not in seen, 'Case-colliding source destination: ' + name)
            seen.add(name.casefold())
            yield name, path

def reject_pointer(path: Path):
    with path.open('rb') as stream:
        require(not stream.read(1024).startswith(LFS_HEADER), 'Unresolved Git LFS object: ' + str(path))

def check_layout(root: Path):
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    require(not missing, 'Incomplete formal source layout: ' + ', '.join(missing))
    require((root / 'tpr/sources').is_dir(), 'Complete formal tpr/sources is required')
    header = (root / 'source/creator/creator_ohos.h').read_text()
    require('#define BLENDER_OHOS_CREATOR_ABI_VERSION 2u' in header and
            'BlenderOHOSSession **session, int32_t *exit_code' in header, 'Require real creator ABI2 source')

def verify_registry_records(root: Path, rows):
    records = {row['path']: row for row in rows}
    manifests = [name for name in records if name.startswith('tpr/sources/') and name.endswith('/manifest.json')]
    require(manifests, 'Complete formal source registry missing')
    for name in manifests:
        manifest = load(root / name)
        require(manifest.get('format') == 'original-archive-parts' and manifest.get('parts'), 'Unknown/incomplete formal archive registry: ' + name)
        total = 0
        for part in manifest['parts']:
            relative(part['filename'])
            require('/' not in part['filename'], 'Unsafe archive part name')
            path = str(Path(name).parent / part['filename'])
            row = records.get(path)
            require(row and row.get('kind') == 'file' and row['size'] == part['size'] and row['sha256'] == part['sha256'],
                    'Incomplete/changed formal archive part: ' + path)
            total += row['size']
        require(total == manifest['size'], 'Formal archive part sizes do not close: ' + name)
    return len(manifests)

def inventory(root: Path, identity: str):
    check_layout(root)
    rows = []
    for name, path in source_paths(root):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            require(not Path(target).is_absolute() and path.resolve(strict=True).is_relative_to(root),
                    'Source symlink escapes complete tree: ' + name)
            resolved = path.resolve(strict=True)
            terminal = resolved.relative_to(root).as_posix()
            require(not set(Path(terminal).parts) & EXCLUDED_DIRS, 'Source link points into excluded state')
            row = {'path': name, 'kind': 'symlink', 'target': target, 'resolved_target': terminal}
            if resolved.is_dir():
                row['target_directory'] = resolved.relative_to(root).as_posix()
            rows.append(row)
        else:
            require(stat.S_ISREG(metadata.st_mode), 'Special source file: ' + name)
            reject_pointer(path)
            rows.append({'path': name, 'kind': 'file', 'sha256': digest(path),
                         'size': metadata.st_size, 'mode': stat.S_IMODE(metadata.st_mode)})
    regular = {row['path'] for row in rows if row['kind'] == 'file'}
    for row in rows:
        if row['kind'] == 'symlink' and 'target_directory' not in row:
            require(row['resolved_target'] in regular, 'Source link target is not reconstructable from inventory')
    registry_count = verify_registry_records(root, rows)
    manifest = {'schema': 1, 'kind': 'complete-formal-source-snapshot', 'identity': identity,
                'complete_registry_manifest_count': registry_count,
                'exclusions': sorted(EXCLUDED_DIRS) + ['*.pyc', '*.pyo'], 'files': rows,
                'lfs_unresolved': False, 'native_build_validated': False}
    manifest['tree_sha256'] = object_digest(rows)
    return manifest

def validate_manifest(manifest):
    require(manifest.get('schema') == 1 and manifest.get('kind') == 'complete-formal-source-snapshot',
            'A complete caller source inventory is required')
    require(manifest.get('lfs_unresolved') is False and manifest.get('identity'), 'Unresolved/anonymous source snapshot')
    require(manifest['tree_sha256'] == object_digest(manifest['files']), 'Source inventory seal differs')
    seen = set()
    for row in manifest['files']:
        relative(row['path'])
        require(row['path'].casefold() not in seen, 'Duplicate source inventory destination')
        seen.add(row['path'].casefold())
        require(row['kind'] in ('file', 'symlink'), 'Invalid source object kind')
    require(set(REQUIRED).issubset({row['path'] for row in manifest['files']}), 'Source inventory omits required formal inputs')
    require(any(row['path'].startswith('tpr/sources/') for row in manifest['files']), 'Source inventory omits complete tpr')

def copy_source(root: Path, destination: Path, manifest):
    validate_manifest(manifest)
    check_layout(root)
    require(verify_registry_records(root, manifest['files']) == manifest['complete_registry_manifest_count'], 'Registry closure count changed')
    actual = {name for name, _ in source_paths(root)}
    require(actual == {row['path'] for row in manifest['files']}, 'Source file set changed after caller snapshot')
    destination.mkdir(parents=True)
    for row in manifest['files']:
        path = beneath(root, row['path'])
        target = destination / row['path']
        if row['kind'] == 'symlink':
            require(path.is_symlink() and os.readlink(path) == row['target'], 'Source symlink changed')
            resolved = path.resolve(strict=True)
            require(not Path(row['target']).is_absolute() and resolved.is_relative_to(root), 'Unsafe source link')
            require(resolved.relative_to(root).as_posix() == row['resolved_target'] and resolved.is_dir() == ('target_directory' in row),
                    'Source terminal link kind/path changed')
            if not resolved.is_dir():
                require(row['resolved_target'] in {item['path'] for item in manifest['files'] if item['kind'] == 'file'}, 'Link target omitted')
            if 'target_directory' in row:
                terminal = row['target_directory']
                require(path.resolve(strict=True).relative_to(root).as_posix() == terminal, 'Directory link target changed')
                if terminal != '.':
                    beneath(destination, terminal).mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(row['target'])
        else:
            reject_pointer(path)
            copied = copy_exact(path, target, row['sha256'])
            require(copied['size'] == row['size'] and (copied['mode'] & 0o111) == (row['mode'] & 0o111), 'Source mode/size changed')
    return {'tree_sha256': manifest['tree_sha256'], 'objects': len(manifest['files']),
            'complete_tpr_copied': True, 'original_notices_preserved': True, 'unresolved_lfs': False}
