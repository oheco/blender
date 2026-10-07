#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small adversarial extraction/ownership/seal checks, all fixtures in explicit tmp."""
import sys
sys.dont_write_bytecode = True
import argparse
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--tmp', type=Path, required=True)
    ap.add_argument('--root', type=Path, help='Optional read-only accepted pure root to copy into tmp for corruption checks')
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('guard_resources', Path(__file__).with_name('resources.py'))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    guard = builder.guard
    _, lock, lock_sha, _ = builder.context(args.repo)
    fixtures = {
        'absolute':[('file','/outside')], 'traversal':[('file','root/../outside')],
        'dot_component':[('file','root/./file')], 'empty_component':[('file','root//file')],
        'windows_separator':[('file','root\\file')], 'drive_colon':[('file','C:/root')],
        'symlink':[('symlink','root/file')], 'hardlink':[('hardlink','root/file')],
        'fifo':[('fifo','root/file')], 'device':[('device','root/file')],
        'duplicate':[('file','root/file'),('file','root/file')],
        'case_collision':[('file','root/ReadMe'),('file','root/readme')],
        'implicit_case_prefix':[('file','root/Dir/one'),('file','root/dir/two')],
        'file_directory_collision':[('file','root/dir'),('file','root/dir/file')],
        'multiple_roots':[('file','one/file'),('file','two/file')],
        'noncanonical_trailing_slash':[('file','root/file/')],
    }
    checks = {}
    def reject(name, fn):
        try:
            fn()
        except (ValueError, FileNotFoundError) as error:
            checks[name] = {'status':'REJECTED', 'error':str(error)}
        else:
            raise AssertionError('Unsafe/mismatched input accepted: ' + name)
    with tempfile.TemporaryDirectory(prefix='formal-python-guards-', dir=args.tmp) as private:
        private = Path(private)
        for name, entries in fixtures.items():
            archive = private / (name + '.tar.gz')
            with tarfile.open(archive, 'w:gz') as stream:
                for kind, path in entries:
                    member = tarfile.TarInfo(path)
                    if kind == 'file':
                        member.size = 1
                        stream.addfile(member, io.BytesIO(b'x'))
                    else:
                        member.type = {'symlink':tarfile.SYMTYPE, 'hardlink':tarfile.LNKTYPE, 'fifo':tarfile.FIFOTYPE, 'device':tarfile.CHRTYPE}[kind]
                        member.linkname = '../outside'
                        stream.addfile(member)
            destination = private / name
            destination.mkdir()
            reject(name, lambda: guard.safe_extract(archive, destination))
            assert not list(destination.iterdir()), 'Unsafe metadata must be rejected before writes'
        archive = private / 'positive.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for name in ('root/LICENSE','root/pkg/__init__.py'):
                member = tarfile.TarInfo(name)
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
        destination = private / 'positive'
        destination.mkdir()
        assert guard.safe_extract(archive, destination, 'root') == 'root'
        assert len(guard.inventory(destination / 'root')) == 2
        checks['positive_original_bytes'] = {'status':'PASS'}
        reject('wrong_expected_archive_root', lambda: guard.safe_extract(archive, private / 'missing', 'different'))
        empty = private / 'wrong-root'
        empty.mkdir()
        reject('wrong_actual_archive_root', lambda: guard.safe_extract(archive, empty, 'different'))
        bad = private / 'bad-complete-original'
        bad.write_bytes(b'bad')
        reject('complete_sha_mismatch', lambda: guard.verify_archive(bad, {'name':'bad','size':3,'sha256':'0'*64}))
        reject('complete_size_mismatch', lambda: guard.verify_archive(bad, {'name':'bad','size':4,'sha256':guard.sha(bad)}))
        unowned = private / 'unowned'
        unowned.mkdir()
        marker = unowned / 'user-file'
        marker.write_bytes(b'preserve')
        reject('existing_unowned_output', lambda: builder.owner(unowned, 'pure-resources', lock_sha, create=True))
        assert marker.read_bytes() == b'preserve' and len(list(unowned.iterdir())) == 1
        owned = private / 'owned'
        builder.owner(owned, 'pure-resources', lock_sha, create=True)
        assert builder.owner(owned, 'pure-resources', lock_sha) == owned
        reject('different_role_owned_output', lambda: builder.owner(owned, 'source-cache', lock_sha))
        reject('different_lock_owned_output', lambda: builder.owner(owned, 'pure-resources', '0'*64))
        link = private / 'link'
        link.symlink_to(owned, target_is_directory=True)
        reject('symlink_output_root', lambda: builder.owner(link, 'pure-resources', lock_sha))
        reject('repository_parent_traversal', lambda: builder.repo_file(args.repo, '../outside'))
        optional = dict(lock)
        optional['inputs'] = [dict(p) for p in lock['inputs']]
        optional['inputs'][0]['requires_dist'] = ['missing-mandatory>=1.0']
        reject('incomplete_mandatory_dependency', lambda: builder.closure(optional))
        if args.root:
            clone = private / 'resources-copy'
            shutil.copytree(args.root, clone)
            assert builder.verify_resources(args.repo, clone)['pure_resources_verified']
            original_manifest = (clone / 'resources.json').read_bytes()
            package = clone / 'site-packages/requests/__init__.py'
            original_bytes = package.read_bytes()
            package.write_bytes(original_bytes + b'\n# modified\n')
            reject('changed_original_package', lambda: builder.verify_resources(args.repo, clone))
            package.write_bytes(original_bytes)
            notice = clone / 'site-packages/typing_extensions-4.14.1.dist-info/licenses/LICENSE'
            original_notice = notice.read_bytes()
            notice.write_bytes(original_notice[:-1])
            reject('truncated_original_license', lambda: builder.verify_resources(args.repo, clone))
            notice.write_bytes(original_notice)
            extra = clone / 'site-packages/requests/injected.py'
            extra.write_bytes(b'# extra file\n')
            manifest = json.loads(original_manifest)
            manifest['site_inventory'] = guard.inventory(clone / 'site-packages')
            manifest['site_tree_sha256'] = guard.seal(manifest['site_inventory'])
            (clone / 'resources.json').write_text(json.dumps(manifest))
            reject('extra_file_even_with_resigned_manifest', lambda: builder.verify_resources(args.repo, clone))
            extra.unlink()
            (clone / 'resources.json').write_bytes(original_manifest)
            old = clone / 'site-packages/requests-2.32.3.dist-info'
            old.mkdir()
            reject('old_version_duplicate', lambda: builder.verify_resources(args.repo, clone))
            old.rmdir()
            for suffix in ('.so','.a','.pyc'):
                native = clone / 'site-packages/requests' / ('forbidden' + suffix)
                native.write_bytes(b'x')
                reject('pure_forbidden_' + suffix[1:], lambda: builder.verify_resources(args.repo, clone))
                native.unlink()
            linked = clone / 'site-packages/requests/link.py'
            linked.symlink_to(package)
            reject('pure_symlink', lambda: builder.verify_resources(args.repo, clone))
            linked.unlink()
            assert builder.verify_resources(args.repo, clone)['pure_resources_verified']
            manifest_path = clone / 'resources.json'
            saved_manifest = private / 'saved-manifest.json'
            manifest_path.rename(saved_manifest)
            manifest_path.symlink_to(saved_manifest)
            reject('resource_manifest_symlink', lambda: builder.verify_resources(args.repo, clone))
            manifest_path.unlink()
            saved_manifest.rename(manifest_path)
            assert builder.verify_resources(args.repo, clone)['pure_resources_verified']
    assert not private.exists()
    report = {'status':'PASS','checks':checks,'count':len(checks), 'all_tmp_cleaned':True,
              'max_synthetic_member_bytes':1,'native_builds':0,'network_requests':0}
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
