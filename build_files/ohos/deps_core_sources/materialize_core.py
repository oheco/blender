#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify/materialize pinned core source archives only; never build dependencies."""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

def repo_file(relative):
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts:
        raise ValueError('Expected a repository-relative source record')
    target = ROOT / name
    if target.is_symlink() or ROOT not in target.resolve().parents or not target.is_file():
        raise ValueError('Missing/unsafe source record: ' + relative)
    return target

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libs', nargs='+', choices=['eigen','embree','abseil','ceres','gflags','glog'])
    parser.add_argument('--output-dir', type=Path, help='Private XDG_CACHE_HOME or TMPDIR directory')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only and args.output_dir is not None:
        parser.error('Use verify-only or materialization output, not both')
    if not args.verify_only and args.output_dir is None:
        parser.error('Materialization requires --output-dir')
    provenance = json.loads((HERE / 'provenance.json').read_text())
    spec = importlib.util.spec_from_file_location('core_vendor', ROOT / 'build_files/ohos/vendor_archive.py')
    vendor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vendor)
    results = []
    for source in provenance['sources']:
        if args.libs and source['name'] not in args.libs:
            continue
        manifest_path = repo_file(source['registry_manifest'])
        manifest = vendor.verify(manifest_path.parent)
        expected = {'name':source['name'], 'version':source['version'], 'sha256':source['sha256'],
                    'size':source['size'], 'filename':source['archive_filename']}
        if any(manifest.get(k) != v for k,v in expected.items()):
            raise ValueError('Source registry identity differs from core provenance')
        for notice in source['notices']:
            file = repo_file(notice['mirror'])
            if file.stat().st_size != notice['size'] or digest(file) != notice['sha256']:
                raise ValueError('Original license/notice mirror changed')
        for patch in provenance['patches']:
            if patch['lib'] != source['name']:
                continue
            if digest(repo_file(patch['original_patch'])) != patch['sha256']:
                raise ValueError('Sealed source patch changed')
            if patch.get('replay_patch'):
                if digest(repo_file(patch['replay_patch'])) != patch['replay_sha256']:
                    raise ValueError('Sealed EOF normalization changed')
        if not args.verify_only:
            vendor.materialize(manifest_path.parent, args.output_dir / manifest['filename'])
        results.append({'name':source['name'],'sha256':manifest['sha256'],'size':manifest['size'],
                        'parts':len(manifest['parts']),'original_notices':len(source['notices'])})
    print(json.dumps({'verified':True,'scope':'Pinned complete source archives and original notices/patch bytes only',
                      'materialized':not args.verify_only,'offline_dependency_builder_completed':False,
                      'sources':results}, indent=2))

if __name__ == '__main__':
    main()
