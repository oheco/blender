#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify this fork has not introduced new LFS content before a text-only push.

The official GitHub mirror's public forks cannot upload new LFS objects. The
unchanged upstream objects remain available from the canonical Blender LFS URL.
Do not skip uploads if this guard reports an added/changed binary object.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def index(revision):
    output = subprocess.check_output(
        ['git', 'lfs', 'ls-files', '--json', revision], cwd=ROOT, text=True)
    rows = json.loads(output)['files']
    result = {}
    for row in rows:
        name = row['name']
        if name in result:
            raise ValueError(f'Duplicate LFS path: {name}')
        if row['oid_type'] != 'sha256':
            raise ValueError(f'Unsupported LFS digest type: {name}')
        result[name] = (row['oid'], row['size'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', default='v5.2.2')
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--verify-checkout', action='store_true',
                        help='Also hash every current LFS resource; never accepts pointers')
    args = parser.parse_args()
    base, current = index(args.baseline), index(args.revision)
    changed = sorted(name for name in current if base.get(name) != current[name])
    removed = sorted(set(base) - set(current))
    if changed or removed:
        print(json.dumps({'unchanged': False, 'new_or_changed': changed,
                          'removed': removed}, indent=2))
        raise SystemExit('LFS content differs: do not skip upload; prepare an owned data endpoint')
    if args.verify_checkout:
        for name, (digest, size) in current.items():
            path = ROOT / name
            if not path.is_file() or path.stat().st_size != size:
                raise SystemExit(f'Missing/incomplete LFS resource: {name}')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise SystemExit(f'LFS resource digest mismatch: {name}')
    print(json.dumps({'unchanged': True, 'baseline': args.baseline,
                      'revision': args.revision, 'objects': len(current),
                      'checkout_hashes_verified': args.verify_checkout}, indent=2))


if __name__ == '__main__':
    main()
