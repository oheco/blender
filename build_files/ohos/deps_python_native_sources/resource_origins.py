# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind observed source imports and resources to the selected raw-file inputs.

This checks an already running interpreter. It does not establish where its
bootstrap modules came from or prove HAP/PyConfig initialization.
"""
import hashlib
import importlib
from importlib import metadata
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys

PURE_TOPLEVEL = {
    'requests': 'requests', 'urllib3': 'urllib3', 'idna': 'idna',
    'certifi': 'certifi', 'charset-normalizer': 'charset_normalizer',
    'attrs': 'attrs', 'cattrs': 'cattrs', 'typing-extensions': 'typing_extensions',
}


def canonical_path(path):
    """Require an existing path with no symlink in it or its ancestors."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError('Symlink in selected resource path: ' + str(part))
    if path.resolve(strict=True) != path:
        raise RuntimeError('Noncanonical selected resource path: ' + str(path))
    return path


def selected_path(base, relative):
    relative = Path(relative)
    if (relative.is_absolute() or not relative.parts or
            any(part in ('.', '..') for part in relative.parts) or
            relative.as_posix() != str(relative)):
        raise RuntimeError('Unsafe selected resource relative path: ' + str(relative))
    base = canonical_path(base)
    path = canonical_path(base / relative)
    if not path.is_relative_to(base):
        raise RuntimeError('Selected resource escaped its root: ' + str(path))
    return path


def exact_path(observed, expected):
    expected = canonical_path(expected)
    if observed is None or str(observed) in ('frozen', 'built-in'):
        raise RuntimeError('Observed resource has no source path: ' + str(observed))
    path = canonical_path(observed)
    if path != expected:
        raise RuntimeError('Observed resource origin differs: ' + str(path) + ' != ' + str(expected))
    return path


def locked_bytes(path, record):
    path = canonical_path(path)
    if not path.is_file():
        raise RuntimeError('Missing selected resource file: ' + str(path))
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != record['size'] or digest != record['sha256']:
        raise RuntimeError('Selected original source bytes differ: ' + str(path))
    return data, {'path': str(path), 'size': len(data), 'sha256': digest}


def source_module(module, expected, record=None):
    """Validate both actual module file and loader spec; never rewrite either."""
    path = exact_path(getattr(module, '__file__', None), expected)
    spec = getattr(module, '__spec__', None)
    if (spec is None or spec.origin in ('frozen', 'built-in') or
            not isinstance(spec.loader, SourceFileLoader)):
        raise RuntimeError('Selected module requires an observed non-frozen source loader: ' + module.__name__)
    exact_path(spec.origin, path)
    exact_path(spec.loader.path, path)
    if path.suffix != '.py' or not path.is_file():
        raise RuntimeError('Selected module is not a source file: ' + module.__name__)
    if path.name == '__init__.py':
        for search in (getattr(module, '__path__', ()), spec.submodule_search_locations or ()):
            if len(search) != 1:
                raise RuntimeError('Selected package has an unexpected search path: ' + module.__name__)
            exact_path(search[0], path.parent)
    elif getattr(module, '__path__', None) is not None or spec.submodule_search_locations is not None:
        raise RuntimeError('Selected source module unexpectedly acts as a package: ' + module.__name__)
    result = {'module': module.__name__, 'path': str(path), 'spec_origin': spec.origin,
              'loader': type(spec.loader).__name__, 'frozen': False}
    if record is not None:
        _, checked = locked_bytes(path, record)
        result.update(size=checked['size'], sha256=checked['sha256'], original_source_path=record['path'])
    return result


def source_inventory(repo, item):
    inventory_path = selected_path(repo, item['source_inventory']['path'])
    data, _ = locked_bytes(inventory_path, item['source_inventory'])
    records = json.loads(data)
    result = {row['path']: row for row in records}
    if len(result) != len(records):
        raise RuntimeError('Duplicate selected source inventory entries: ' + item['name'])
    return result


def distribution_origin(item, site, records):
    """Check the actual distribution object's exact METADATA file and content."""
    expected = selected_path(site, item['dist_info'] + '/METADATA')
    distribution = metadata.distribution(item['name'])
    if not isinstance(distribution, metadata.PathDistribution):
        raise RuntimeError('Selected metadata requires a filesystem PathDistribution: ' + item['name'])
    # PathDistribution.locate_file('') returns site-packages, not its metadata
    # directory. Check its actual metadata backing directory explicitly.
    exact_path(getattr(distribution, '_path', None), expected.parent)
    original = records.get('PKG-INFO')
    if original is None:
        raise RuntimeError('Missing original selected PKG-INFO inventory: ' + item['name'])
    data, result = locked_bytes(expected, original)
    if distribution.read_text('METADATA') != data.decode('utf-8'):
        raise RuntimeError('Observed distribution metadata differs from its selected file: ' + item['name'])
    if distribution.version != item['version']:
        raise RuntimeError('Wrong selected pure source distribution: ' + item['name'])
    result.update(name=item['name'], version=distribution.version,
                  distribution_path=str(expected.parent), original_source_path='PKG-INFO')
    return result


def verify_resources(pure_dir, repo, selected):
    """Reject inherited runtime modules/resources even when versions match."""
    pure = canonical_path(pure_dir)
    site = selected_path(pure, 'site-packages')
    ssl = importlib.import_module('ssl')
    ssl_origin = source_module(ssl, selected_path(pure, 'ssl.py'))
    inputs = selected['inputs']
    names = [item['name'].replace('_', '-').lower() for item in inputs]
    if len(names) != 8 or set(names) != set(PURE_TOPLEVEL):
        raise RuntimeError('Exactly the selected eight pure distributions are required')
    versions, origins, distributions = {}, [], []
    certifi_item = certifi_records = certifi_module = None
    for item, key in zip(inputs, names):
        records = source_inventory(repo, item)
        top = PURE_TOPLEVEL[key]
        targets = [part for part in item['packages'] if part['target'] in (top, top + '.py')]
        if len(targets) != 1:
            raise RuntimeError('Missing exact selected source target: ' + top)
        target = targets[0]
        suffix = '' if target['target'].endswith('.py') else '/__init__.py'
        original_path = target['source'] + suffix
        if original_path not in records:
            raise RuntimeError('Missing selected topmodule source inventory: ' + original_path)
        expected = selected_path(site, target['target'] + suffix)
        module = importlib.import_module(top)
        origins.append(source_module(module, expected, records[original_path]))
        distributions.append(distribution_origin(item, site, records))
        versions[item['name']] = item['version']
        if top == 'certifi':
            certifi_item, certifi_records, certifi_module = item, records, module
    ca_targets = [part for part in certifi_item['packages'] if part['target'] == 'certifi']
    ca_original = ca_targets[0]['source'] + '/cacert.pem'
    if ca_original not in certifi_records:
        raise RuntimeError('Missing original certifi CA source inventory')
    expected_ca = selected_path(site, 'certifi/cacert.pem')
    exact_path(certifi_module.where(), expected_ca)
    _, ca_origin = locked_bytes(expected_ca, certifi_records[ca_original])
    ca_origin['original_source_path'] = ca_original
    return {'ssl': ssl_origin, 'pure_modules': origins, 'pure_versions': versions,
            'distribution_metadata': distributions, 'certifi_ca': ca_origin,
            'observed_sys_path': list(sys.path),
            'HAP_PyConfig_startup': 'NOTRUN: resource imports do not prove interpreter initialization'}
