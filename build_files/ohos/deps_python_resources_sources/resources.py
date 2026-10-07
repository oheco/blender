#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable offline full-source and eight-distribution pure resource closure."""
import sys
sys.dont_write_bytecode = True
import argparse
from email.parser import BytesParser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
NAMESPACE = 'build_files/ohos/deps_python_resources_sources'
OWNER = '.blender-ohos-python-resources-owner.json'
EXPECTED = {'requests': '2.33.0', 'urllib3': '2.8.0', 'idna': '3.15',
            'certifi': '2026.7.22', 'charset-normalizer': '3.4.1',
            'cattrs': '25.1.1', 'attrs': '25.3.0', 'typing-extensions': '4.14.1'}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


guard = module(HERE / 'source_guard.py', 'formal_python_source_guard')
sha, seal, inventory = guard.sha, guard.seal, guard.inventory


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def dump_new(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def real_path(path):
    path = Path(os.path.abspath(path))
    for ancestor in [path, *path.parents]:
        if ancestor.is_symlink():
            raise ValueError('Symlink in caller path')
    return path


def repo_file(repo, relative):
    target = real_path(repo.joinpath(*guard.canonical(relative)))
    if repo not in target.parents or not target.is_file():
        raise ValueError('Missing/unsafe repository file: ' + relative)
    return target


def verify_file(path, item):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size'] or sha(path) != item['sha256']:
        raise ValueError('Sealed bytes changed: ' + item.get('path', path.name))


def context(repo):
    repo = real_path(repo)
    lock_file = repo_file(repo, NAMESPACE + '/sources.lock.json')
    lock = json.loads(lock_file.read_text(encoding='utf-8'))
    if (lock.get('schema') != 1 or lock.get('kind') != 'offline-pure-source-closure' or
            {normalized(p['name']): p['version'] for p in lock['inputs']} != EXPECTED or
            len(lock['inputs']) != 8):
        raise ValueError('Wrong eight-distribution lock')
    helper = lock['vendor_helper']
    helper_path = repo_file(repo, helper['path'])
    verify_file(helper_path, helper)
    vendor = module(helper_path, 'formal_python_vendor_archive')
    return repo, lock, sha(lock_file), vendor


def version_tuple(value):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', value):
        raise ValueError('Unsupported closure version: ' + value)
    return tuple(int(v) for v in value.split('.'))


def constraint(version, expression):
    found = re.fullmatch(r'\s*(>=|<=|==|!=|<|>)\s*([0-9]+(?:\.[0-9]+)*)\s*', expression)
    if found is None:
        raise ValueError('Unsupported locked requirement: ' + expression)
    operation, limit = found.groups()
    a, b = version_tuple(version), version_tuple(limit)
    length = max(len(a), len(b))
    a, b = a + (0,) * (length - len(a)), b + (0,) * (length - len(b))
    return {'>=': a >= b, '<=': a <= b, '==': a == b, '!=': a != b, '<': a < b, '>': a > b}[operation]


def closure(lock):
    """Validate original mandatory metadata for CPython 3.13, no selected extras."""
    versions = {normalized(p['name']): p['version'] for p in lock['inputs']}
    edges, skipped = [], []
    for pin in lock['inputs']:
        python = pin['requires_python']
        if python and not all(constraint('3.13.13', part) for part in python.split(',')):
            raise ValueError('Locked package excludes native CPython 3.13.13')
        for requirement in pin['requires_dist']:
            base, separator, marker = requirement.partition(';')
            if separator:
                if re.search(r'\bextra\b', marker):
                    skipped.append({'distribution': pin['name'], 'requirement': requirement, 'reason': 'no extras selected'})
                    continue
                match = re.fullmatch(r'\s*python_version\s*(<|>=|>|<=|==|!=)\s*[\"\']([0-9.]+)[\"\']\s*', marker)
                if not match:
                    raise ValueError('Unsupported mandatory marker: ' + marker)
                if not constraint('3.13', match[1] + match[2]):
                    skipped.append({'distribution': pin['name'], 'requirement': requirement, 'reason': 'false for Python 3.13'})
                    continue
            match = re.fullmatch(r'\s*([A-Za-z0-9_.-]+)\s*(.*)', base)
            if match is None:
                raise ValueError('Bad original requirement')
            name, bounds = match.groups()
            key = normalized(name)
            if key not in versions or (bounds and not all(constraint(versions[key], part) for part in bounds.strip('() ').split(','))):
                raise ValueError('Incomplete mandatory dependency: ' + requirement)
            edges.append({'distribution': pin['name'], 'requirement': requirement, 'resolved_version': versions[key]})
    return {'target_python': '3.13.13', 'required_edges': edges, 'excluded_markers_or_extras': skipped}


def verify_registry(repo):
    repo, lock, lock_sha, vendor = context(repo)
    results = []
    for pin in lock['inputs']:
        manifest_path = repo_file(repo, pin['registry_manifest']['path'])
        verify_file(manifest_path, pin['registry_manifest'])
        manifest = vendor.verify(manifest_path.parent)
        for key in ('name', 'version', 'filename', 'sha256', 'size', 'license'):
            if manifest[key] != pin[key]:
                raise ValueError('Source registry identity differs: ' + key)
        if manifest['url'] != pin['source_url'] or manifest['parts'] != pin['parts']:
            raise ValueError('Source URL/parts differ from sealed mapping')
        for part in manifest['parts']:
            repo_file(repo, manifest_path.parent.relative_to(repo).as_posix() + '/' + part['filename'])
        for item in [pin['source_inventory'], pin['pypi_metadata'], *pin['notices']]:
            verify_file(repo_file(repo, item['path']), item)
        records = json.loads(repo_file(repo, pin['source_inventory']['path']).read_text())
        if len(records) != pin['source_file_count'] or seal(records) != pin['source_tree_sha256']:
            raise ValueError('Full-source inventory seal differs')
        for record in records:
            guard.canonical(record['path'])
        results.append({'name': pin['name'], 'version': pin['version'], 'registry_id': pin['registry_id'],
                        'original_sha256': pin['sha256'], 'original_size': pin['size'],
                        'source_files': len(records), 'reuse_existing': pin['reuse_existing']})
    return {'verified': True, 'lock_sha256': lock_sha, 'inputs': results, 'dependency_closure': closure(lock)}


def output_path(path, tmp):
    path, tmp = real_path(path), real_path(tmp)
    if not tmp.is_dir():
        raise ValueError('Explicit tmp directory must exist')
    # Use supplied platform roots when present; never assume /tmp or HOME semantics.
    allowed = [real_path(os.environ[k]) for k in ('XDG_CACHE_HOME', 'TMPDIR') if os.environ.get(k)]
    if allowed and not any(root in path.parents for root in allowed):
        raise ValueError('Output must be below caller private cache/tmp roots')
    ancestor = next(p for p in [path, *path.parents] if p.exists())
    if ancestor.stat().st_dev != tmp.stat().st_dev:
        raise ValueError('Outputs and explicit tmp must use the same private filesystem')
    return path, tmp


def owner(root, role, lock_sha, create=False):
    root = real_path(root)
    expected = {'schema': 1, 'owner': NAMESPACE, 'role': role, 'lock_sha256': lock_sha}
    if root.exists():
        marker = root / OWNER
        if not root.is_dir() or marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refusing existing unowned or differently sealed root')
    elif create:
        root.mkdir(parents=True, exist_ok=False)
        dump_new(root / OWNER, expected)
    else:
        raise ValueError('Owned root is missing')
    return root


def materialize(repo, cache, tmp):
    result = verify_registry(repo)
    repo, lock, lock_sha, vendor = context(repo)
    cache, tmp = output_path(cache, tmp)
    cache = owner(cache, 'source-cache', lock_sha, create=True)
    archives = cache / 'archives'
    if not archives.exists():
        archives.mkdir()
    if archives.is_symlink():
        raise ValueError('Unsafe owned archives path')
    # Existing helper owns temporary publication locks entirely within TMPDIR.
    with tempfile.TemporaryDirectory(prefix='python-source-materialize-', dir=tmp) as private:
        private = Path(private)
        for pin in lock['inputs']:
            target = archives / pin['filename']
            if target.exists():
                guard.verify_archive(target, pin)
                continue
            original = private / pin['filename']
            vendor.materialize(repo_file(repo, pin['registry_manifest']['path']).parent, original)
            guard.verify_archive(original, pin)
            with original.open('rb') as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            guard.verify_archive(target, pin)
    result.update(materialized_original_archives=True, temporary_publication_cleaned=True)
    return result


def source_records(repo, pin):
    return json.loads(repo_file(repo, pin['source_inventory']['path']).read_text())


def verify_source(repo, source, pin):
    records = inventory(source)
    if records != source_records(repo, pin):
        raise ValueError('Complete source tree differs: ' + pin['name'])
    metadata = BytesParser().parsebytes((source / 'PKG-INFO').read_bytes())
    if (normalized(metadata['Name']) != normalized(pin['name']) or metadata['Version'] != pin['version'] or
            metadata.get('Requires-Python') != pin['requires_python'] or metadata.get_all('Requires-Dist', []) != pin['requires_dist']):
        raise ValueError('Original distribution metadata differs')
    for notice in pin['notices']:
        verify_file(source / notice['source_path'], notice)
    return records


def prepare(repo, cache, tmp):
    materialize(repo, cache, tmp)
    repo, lock, lock_sha, _ = context(repo)
    cache = owner(cache, 'source-cache', lock_sha)
    if (cache / 'prepared.json').exists():
        return verify_cache(repo, cache)
    sources = cache / 'sources'
    if sources.exists():
        raise ValueError('Refusing partially prepared owned cache; select a fresh root')
    sources.mkdir()
    with tempfile.TemporaryDirectory(prefix='python-source-prepare-', dir=real_path(tmp)) as private:
        private = Path(private)
        for pin in lock['inputs']:
            unpack = private / pin['registry_id'].split('/')[-1]
            unpack.mkdir()
            root = guard.safe_extract(cache / 'archives' / pin['filename'], unpack, pin['archive_root'])
            source = unpack / root
            verify_source(repo, source, pin)
            shutil.copytree(source, sources / root)
            verify_source(repo, sources / root, pin)
    dump_new(cache / 'prepared.json', {'schema': 1, 'lock_sha256': lock_sha,
             'archives_relative': 'archives', 'sources_relative': 'sources',
             'source_file_count': sum(p['source_file_count'] for p in lock['inputs']),
             'cache_inventory': inventory(cache / 'archives') + [dict(r, path='sources/' + r['path']) for r in inventory(sources)],
             'local_source_modifications': [], 'native_builds': 0})
    return verify_cache(repo, cache)


def verify_cache(repo, cache):
    result = verify_registry(repo)
    repo, lock, lock_sha, _ = context(repo)
    cache = owner(cache, 'source-cache', lock_sha)
    prepared_path = real_path(cache / 'prepared.json')
    if not prepared_path.is_file():
        raise ValueError('Prepared cache manifest is missing')
    prepared = json.loads(prepared_path.read_text())
    if prepared['lock_sha256'] != lock_sha:
        raise ValueError('Prepared cache lock differs')
    for pin in lock['inputs']:
        guard.verify_archive(cache / 'archives' / pin['filename'], pin)
        verify_source(repo, cache / 'sources' / pin['archive_root'], pin)
    actual = inventory(cache / 'archives') + [dict(r, path='sources/' + r['path']) for r in inventory(cache / 'sources')]
    if actual != prepared['cache_inventory']:
        raise ValueError('Prepared cache complete inventory differs')
    # Reject extra paths outside the complete expected archive/source trees too.
    top = {p.name for p in cache.iterdir()}
    if top != {OWNER, 'archives', 'sources', 'prepared.json'}:
        raise ValueError('Unexpected owned cache entries')
    result.update(complete_sources_verified=True, source_file_count=prepared['source_file_count'])
    return result


def pure_records(root):
    records = inventory(root)
    for item in records:
        parts = Path(item['path']).parts
        if Path(item['path']).suffix.lower() in ('.so', '.a', '.pyc', '.pyo', '.dll', '.dylib', '.pyd') or '__pycache__' in parts:
            raise ValueError('Native/bytecode file in pure resource payload')
    return records


def assemble(repo, cache, root, tmp):
    verify_cache(repo, cache)
    repo, lock, lock_sha, _ = context(repo)
    cache = owner(cache, 'source-cache', lock_sha)
    root, tmp = output_path(root, tmp)
    if root == cache or root in cache.parents or cache in root.parents or root == repo or repo in root.parents:
        raise ValueError('Resources require independent owned output')
    root = owner(root, 'pure-resources', lock_sha, create=True)
    if (root / 'resources.json').exists():
        return verify_resources(repo, root)
    if (root / 'site-packages').exists():
        raise ValueError('Refusing partial resources; select a fresh root')
    mappings = []
    with tempfile.TemporaryDirectory(prefix='python-pure-assemble-', dir=tmp) as private:
        stage = Path(private) / 'site-packages'
        stage.mkdir()
        for pin in lock['inputs']:
            source = cache / 'sources' / pin['archive_root']
            for package in pin['packages']:
                original, target = source / package['source'], stage / package['target']
                if original.is_dir():
                    pure_records(original)
                    shutil.copytree(original, target)
                else:
                    shutil.copyfile(original, target)
                mappings.append({'distribution': pin['name'], 'source': package['source'], 'target': package['target']})
            dist = stage / pin['dist_info']
            dist.mkdir()
            shutil.copyfile(source / 'PKG-INFO', dist / 'METADATA')
            (dist / 'INSTALLER').write_text('blender-ohos-offline-pristine-source-copy\n', encoding='utf-8')
            for notice in pin['notices']:
                target = dist / 'licenses' / notice['source_path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / notice['source_path'], target)
        records = pure_records(stage)
        shutil.copytree(stage, root / 'site-packages')
    dump_new(root / 'resources.json', {'schema': 1, 'kind': 'pure-python-resources-only',
             'lock_sha256': lock_sha, 'site_relative': 'site-packages',
             'distributions': [{'name': p['name'], 'version': p['version'], 'dist_info': p['dist_info'],
                                'original_archive_sha256': p['sha256']} for p in lock['inputs']],
             'package_source_mappings': mappings, 'site_inventory': records, 'site_tree_sha256': seal(records),
             'target_cpython': '3.13.13', 'external_native_numpy': '2.3.4',
             'native_payload_assembled': False, 'temporary_assembly_cleaned': True})
    return verify_resources(repo, root)


def verify_resources(repo, root):
    result = verify_registry(repo)
    repo, lock, lock_sha, _ = context(repo)
    root = owner(root, 'pure-resources', lock_sha)
    manifest_path = real_path(root / 'resources.json')
    if not manifest_path.is_file():
        raise ValueError('Pure resource manifest is missing')
    manifest = json.loads(manifest_path.read_text())
    site = root / 'site-packages'
    records = pure_records(site)
    if manifest['lock_sha256'] != lock_sha or records != manifest['site_inventory'] or seal(records) != manifest['site_tree_sha256']:
        raise ValueError('Pure resources inventory/lock differs')
    expected_top = {p['dist_info'] for p in lock['inputs']} | {v['target'] for p in lock['inputs'] for v in p['packages']}
    if {p.name for p in site.iterdir()} != expected_top or {p.name for p in root.iterdir()} != {OWNER, 'resources.json', 'site-packages'}:
        raise ValueError('Extra/old distribution or unowned resource entries')
    expected_distributions = [{'name': p['name'], 'version': p['version'], 'dist_info': p['dist_info'],
                               'original_archive_sha256': p['sha256']} for p in lock['inputs']]
    if manifest['site_relative'] != 'site-packages' or manifest['distributions'] != expected_distributions:
        raise ValueError('Pure resource distribution mapping differs')
    source_map = {p['name']: {r['path']: r for r in source_records(repo, p)} for p in lock['inputs']}
    by_path = {r['path']: r for r in records}
    expected_paths = set()
    for pin in lock['inputs']:
        for package in pin['packages']:
            for source_path, item in source_map[pin['name']].items():
                if source_path == package['source'] or source_path.startswith(package['source'] + '/'):
                    relative = package['target'] + source_path[len(package['source']):]
                    expected_paths.add(relative)
                    actual = by_path.get(relative)
                    if actual is None or any(actual[k] != item[k] for k in ('size', 'sha256')):
                        raise ValueError('Pristine package source bytes differ: ' + relative)
        dist = site / pin['dist_info']
        expected_paths.update({pin['dist_info'] + '/METADATA', pin['dist_info'] + '/INSTALLER'})
        verify_file(dist / 'METADATA', source_map[pin['name']]['PKG-INFO'])
        if (dist / 'INSTALLER').read_bytes() != b'blender-ohos-offline-pristine-source-copy\n':
            raise ValueError('Assembler attribution differs')
        for notice in pin['notices']:
            expected_paths.add(pin['dist_info'] + '/licenses/' + notice['source_path'])
            verify_file(dist / 'licenses' / notice['source_path'], notice)
    if set(by_path) != expected_paths:
        raise ValueError('Pure payload contains extra/missing original files')
    result.update(pure_resources_verified=True, site_tree_sha256=manifest['site_tree_sha256'],
                  pure_file_count=len(records), native_payload_assembled=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['materialize', 'prepare', 'assemble', 'verify'])
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--tmp', type=Path)
    args = parser.parse_args()
    if args.action != 'verify' and (args.cache is None or args.tmp is None):
        parser.error('materialize/prepare/assemble require explicit --cache and --tmp')
    if args.action == 'assemble' and args.root is None:
        parser.error('assemble requires explicit --root')
    if args.action == 'materialize':
        result = materialize(args.repo, args.cache, args.tmp)
    elif args.action == 'prepare':
        result = prepare(args.repo, args.cache, args.tmp)
    elif args.action == 'assemble':
        result = assemble(args.repo, args.cache, args.root, args.tmp)
    else:
        result = verify_registry(args.repo)
        if args.cache:
            result = verify_cache(args.repo, args.cache)
        if args.root:
            result = verify_resources(args.repo, args.root)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
