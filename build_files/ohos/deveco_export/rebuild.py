#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Detect source rebuild prerequisites; never route through old work/cache builders."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from common import Rejected, digest, load, metadata_output, relative, require, write_new

# A source registry or a development configure driver does not satisfy a builder stage.
STAGES = {
    'toolchain': ('build_files/ohos/toolchain/builder.py', 'build_files/ohos/toolchain/inputs.lock.json'),
    'base': ('build_files/ohos/deps_base_sources/builder/builder.py', 'build_files/ohos/deps_base_sources/builder/inputs.lock.json'),
    'geometry': ('build_files/ohos/deps_geometry_sources/builder/builder.py', 'build_files/ohos/deps_geometry_sources/builder/inputs.lock.json'),
    'volume': ('build_files/ohos/deps_volume_sources/builder/builder.py', 'build_files/ohos/deps_volume_sources/builder/inputs.lock.json'),
    'core': ('build_files/ohos/deps_core_sources/builder/builder.py', 'build_files/ohos/deps_core_sources/builder/inputs.lock.json'),
    'color': ('build_files/ohos/deps_color_sources/builder/builder.py', 'build_files/ohos/deps_color_sources/builder/inputs.lock.json'),
    'vulkan': ('build_files/ohos/deps_vulkan_sources/builder/builder.py', 'build_files/ohos/deps_vulkan_sources/builder/inputs.lock.json'),
    'python_native': ('build_files/ohos/deps_python_native_sources/builder.py', 'build_files/ohos/deps_python_native_sources/inputs.lock.json'),
    'gltf': ('build_files/ohos/deps_gltf_sources/builder.py', 'build_files/ohos/deps_gltf_sources/inputs.lock.json'),
    'pure_resources': ('build_files/ohos/deps_python_resources_sources/builder/builder.py', 'build_files/ohos/deps_python_resources_sources/builder/inputs.lock.json'),
    'editor': ('build_files/ohos/editor/builder.py', 'build_files/ohos/editor/inputs.lock.json'),
}
ORDER = ['toolchain', 'base', 'geometry', 'volume', 'core', 'color', 'vulkan', 'python_native', 'gltf', 'pure_resources', 'editor']
PLACEHOLDERS = {'SOURCE', 'CACHE', 'TMP', 'PREFIX', 'SDK_ROOT', 'SDK_ETS_ROOT', 'TOOL', 'RESOURCE_DIR', 'INPUT'}

def safe_argument(token):
    if not isinstance(token, str) or not token or any(ord(c) < 32 for c in token):
        return False
    value = token.split('=', 1)[-1]
    if value.startswith('@'):
        root, separator, suffix = value[1:].partition('/')
        if root not in PLACEHOLDERS:
            return False
        try:
            if separator: relative(suffix)
        except Rejected:
            return False
        return True
    return not any(mark in value for mark in ('/', '\\', '@')) and value not in ('.', '..')

def lock_freeze(source, entry, lock, source_inventory=None):
    value = load(source / lock)
    records = value.get('sealed_files', value.get('files', [])) if isinstance(value, dict) else []
    if not isinstance(records, list) or not records:
        return 'SOURCE_LOCK_ONLY_RECIPE_NOT_FROZEN', False
    indexed = {row['path']: row for row in records if isinstance(row, dict) and 'path' in row}
    if entry not in indexed or digest(source / entry) != indexed[entry].get('sha256'):
        return 'ENTRY_UNSEALED_OR_CHANGED', False
    for path in (source / entry).parent.glob('*.py'):
        name = path.relative_to(source).as_posix()
        if name not in indexed or digest(path) != indexed[name].get('sha256'):
            return 'RECIPE_HELPER_UNSEALED_OR_CHANGED', False
    if source_inventory is not None:
        snapshot = {row['path']: row for row in source_inventory['files'] if row['kind'] == 'file'}
        if any(row['path'] not in snapshot or row['sha256'] != snapshot[row['path']]['sha256'] or
               row.get('size', snapshot[row['path']]['size']) != snapshot[row['path']]['size'] for row in records):
            return 'LOCK_INPUT_DIFFERS_FROM_SOURCE_SNAPSHOT', False
        return 'RECIPE_AND_INPUTS_BOUND_TO_SNAPSHOT', True
    return 'RECIPE_ENTRY_HELPERS_FROZEN_OTHER_INPUTS_NOT_RUN', True

def detect(source: Path, declarations=None, source_inventory=None):
    declarations = declarations or {}
    rows = []
    for name in ORDER:
        entry, lock = STAGES[name]
        missing = [value for value in (entry, lock) if not (source / value).is_file()]
        freeze, frozen = ('MISSING_RECIPE_OR_LOCK', False)
        if not missing:
            try:
                freeze, frozen = lock_freeze(source, entry, lock, source_inventory)
            except (Rejected, OSError, KeyError, ValueError, TypeError):
                freeze, frozen = ('INVALID_OR_UNFROZEN_RECIPE_LOCK', False)
        declaration = declarations.get(name)
        # Commands are explicit structured argv, never shell strings. Detection does
        # not execute a recipe or assume that an earlier prototype accepts this one.
        valid_command = isinstance(declaration, dict) and isinstance(declaration.get('argv'), list) and bool(declaration['argv'])
        if valid_command:
            argv = declaration['argv']
            valid_command = len(argv) >= 2 and argv[0] == '@TOOL/python' and argv[1] == '@SOURCE/' + entry
            valid_command = valid_command and all(safe_argument(token) for token in argv)
        rows.append({'stage': name, 'entry': entry, 'lock': lock,
                     'entry_sha256': digest(source / entry) if not missing else None,
                     'lock_sha256': digest(source / lock) if (source / lock).is_file() else None,
                     'status': 'SOURCE_RECIPE_FROZEN_COMMAND_PREPARED_NOT_RUN' if not missing and valid_command and frozen else 'BLOCKED',
                     'recipe_freeze': freeze, 'missing_files': missing, 'command_declared': valid_command,
                     'native_fresh_build': 'NOT_RUN', 'argv': declaration['argv'] if valid_command else None})
    gaps = [row['stage'] for row in rows if row['status'] == 'BLOCKED']
    return {'schema': 1, 'kind': 'offline-source-rebuild-contract', 'order': ORDER,
            'stages': rows, 'gaps': gaps, 'status': 'BLOCKED_MISSING_SOURCE_REBUILD_STAGES' if gaps else 'PREPARED_NOT_RUN',
            'complete_portable_rebuild': False,
            'note': 'Presence/declared argv is preparation only. Fresh native build, signatures, relocation and application acceptance are separate receipts.'}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', required=True, type=Path)
    p.add_argument('--declarations', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    source = args.source_root.resolve(strict=True)
    output = metadata_output(args.output, [source])
    result = detect(source, load(args.declarations) if args.declarations else {})
    write_new(output, result)
    print(result['status'] + ': ' + ', '.join(result['gaps']))
    return 2 if result['gaps'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
