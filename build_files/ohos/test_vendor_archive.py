#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hermetic source-vendoring tests. All fixtures are removed from TMPDIR."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import vendor_archive as vendor


class VendorArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blender-vendor-test-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.patch_env = mock.patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.cache)})
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.patch_chunk = mock.patch.object(vendor, 'CHUNK', 1024)
        self.patch_chunk.start()
        self.addCleanup(self.patch_chunk.stop)
        self.bytes = bytes(range(256)) * 12 + b'complete fixed original source archive'
        self.archive = self.root / 'example.tar.xz'
        self.archive.write_bytes(self.bytes)
        self.entry = self.root / 'entry.json'
        self.entry.write_text(json.dumps({'name': 'example', 'version': '1.0', 'kind': 'source',
            'filename': self.archive.name, 'url': 'https://example.invalid/source', 'license': 'MIT',
            'size': len(self.bytes), 'sha256': hashlib.sha256(self.bytes).hexdigest()}))
        self.destination = self.root / 'vendor/example-1.0'

    def pack(self):
        return vendor.pack(self.entry, self.archive, self.destination)

    def test_roundtrip_preserves_all_bytes_and_parts(self):
        result = self.pack()
        self.assertEqual(4, len(result['parts']))
        self.assertEqual(result, vendor.verify(self.destination))
        output = self.cache / self.archive.name
        vendor.materialize(self.destination, output)
        self.assertEqual(self.bytes, output.read_bytes())
        vendor.materialize(self.destination, output)
        self.assertEqual(self.bytes, output.read_bytes())

    def test_existing_vendor_directory_is_not_overwritten(self):
        self.pack()
        before = (self.destination / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            self.pack()
        self.assertEqual(before, (self.destination / 'manifest.json').read_bytes())

    def test_bad_original_digest_does_not_publish(self):
        record = json.loads(self.entry.read_text())
        record['sha256'] = '0' * 64
        self.entry.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.destination.exists())

    def test_corrupt_part_is_rejected(self):
        record = self.pack()
        path = self.destination / record['parts'][0]['filename']
        data = bytearray(path.read_bytes())
        data[5] ^= 1
        path.write_bytes(data)
        with self.assertRaises(ValueError):
            vendor.verify(self.destination)

    def test_unsafe_manifest_path_is_rejected(self):
        record = self.pack()
        record['parts'][0]['filename'] = '../escape'
        (self.destination / 'manifest.json').write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            vendor.verify(self.destination)

    def test_source_part_symlink_is_rejected(self):
        record = self.pack()
        path = self.destination / record['parts'][0]['filename']
        saved = self.root / 'other-part'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(ValueError):
            vendor.verify(self.destination)

    def test_conflicting_cache_is_not_overwritten(self):
        self.pack()
        output = self.cache / self.archive.name
        output.write_bytes(b'user content')
        with self.assertRaises(ValueError):
            vendor.materialize(self.destination, output)
        self.assertEqual(b'user content', output.read_bytes())

    def test_cache_symlink_is_not_accepted(self):
        self.pack()
        output = self.cache / self.archive.name
        output.symlink_to(self.archive)
        with self.assertRaises(ValueError):
            vendor.materialize(self.destination, output)
        self.assertTrue(output.is_symlink())

    def test_outside_private_root_is_rejected(self):
        self.pack()
        with mock.patch.dict(os.environ, {'TMPDIR': str(self.root / 'private-temp')}):
            (self.root / 'private-temp').mkdir()
            output = self.root / 'outside-source'
            with self.assertRaises(ValueError):
                vendor.materialize(self.destination, output)
            self.assertFalse(output.exists())

    def test_incorrect_part_size_sum_is_rejected(self):
        record = self.pack()
        record['parts'][-1]['size'] -= 1
        (self.destination / 'manifest.json').write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            vendor.verify(self.destination)


if __name__ == '__main__':
    unittest.main()
