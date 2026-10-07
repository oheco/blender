#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Accepted terminal CPython pure-resource check; no bpy/HAP/DSO claim."""
import sys
sys.dont_write_bytecode = True
import argparse
import dataclasses
from email.parser import BytesParser
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import ssl
import tempfile
import time


def load(path, name, package=False):
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)] if package else None)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def main():
    start = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--tmp', type=Path, required=True)
    parser.add_argument('--http-meta', type=Path)
    args = parser.parse_args()
    if sys.version_info[:3] != (3, 13, 13) or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise ValueError('Use accepted terminal CPython 3.13.13 with -I -B -S')
    if '' in sys.path or str(Path.cwd()) in sys.path or any('site-packages' in p for p in sys.path):
        raise ValueError('Old/cwd import path present before explicit resource selection')
    builder = load(Path(__file__).with_name('resources.py'), 'native_formal_resources')
    verified = builder.verify_resources(args.repo, args.root)
    repo, lock, lock_sha, _ = builder.context(args.repo)
    root = builder.owner(args.root, 'pure-resources', lock_sha)
    site = root / 'site-packages'
    before_path_seal = builder.seal(sys.path)
    sys.path.insert(0, str(site))
    distributions = list(importlib.metadata.distributions(path=[str(site)]))
    actual_versions = {builder.normalized(d.metadata['Name']): d.version for d in distributions}
    if actual_versions != builder.EXPECTED or len(distributions) != 8:
        raise ValueError('Wrong/duplicate native import distribution metadata')
    modules = {'requests':'requests', 'urllib3':'urllib3', 'idna':'idna', 'certifi':'certifi',
               'charset-normalizer':'charset_normalizer', 'cattrs':'cattrs', 'attrs':'attrs',
               'typing_extensions':'typing_extensions'}
    origins = []
    expected_module_versions = {'requests':'2.33.0','urllib3':'2.8.0','idna':'3.15','certifi':'2026.07.22','charset-normalizer':'3.4.1'}
    records = {r['path']:r for r in builder.pure_records(site)}
    for pin in lock['inputs']:
        imported = importlib.import_module(modules[pin['name']])
        path = Path(imported.__file__).resolve()
        if site not in path.parents:
            raise ValueError('Old prefix package imported')
        relative = path.relative_to(site).as_posix()
        builder.verify_file(path, records[relative])
        if pin['name'] in expected_module_versions and imported.__version__ != expected_module_versions[pin['name']]:
            raise ValueError('Wrong pure module version')
        metadata_bytes = (site / pin['dist_info'] / 'METADATA').read_bytes()
        metadata = BytesParser().parsebytes(metadata_bytes)
        if metadata['Version'] != pin['version'] or metadata.get_all('Requires-Dist', []) != pin['requires_dist']:
            raise ValueError('Original runtime METADATA changed')
        origins.append({'distribution':pin['name'], 'distribution_version':pin['version'],
                        'module':modules[pin['name']], 'module_version':getattr(imported, '__version__', None),
                        'origin_relative':relative, 'sha256':builder.sha(path),
                        'metadata_sha256':hashlib.sha256(metadata_bytes).hexdigest(), 'complete_license_files':len(pin['notices'])})
    import attr
    import cattr
    import cattrs.preconf.json
    from typing_extensions import TypeIs
    from urllib3.util.ssl_ import create_urllib3_context
    import certifi
    import requests
    import idna
    if not (site in Path(attr.__file__).resolve().parents and site in Path(cattr.__file__).resolve().parents):
        raise ValueError('Upstream aliases imported from old site')
    @dataclasses.dataclass
    class AssetMetadata:
        url: str
        size: int
        hashes: list[str]
    payload = {'url':'https://assets.example.invalid/素材.json', 'size':121, 'hashes':['ab' * 32]}
    converter = cattrs.preconf.json.JsonConverter()
    structured = converter.loads(json.dumps(payload, ensure_ascii=False), AssetMetadata)
    assert converter.unstructure(structured) == payload
    assert json.loads(converter.dumps(structured)) == payload
    @attr.define
    class AttrPayload:
        label: str
        count: int
    assert converter.loads(converter.dumps(AttrPayload('资源', 8)), AttrPayload) == AttrPayload('资源', 8)
    assert idna.encode('例子.example').decode() == 'xn--fsqu00a.example'
    prepared = requests.Request('GET', 'https://example.invalid/资源', params={'count':'8'}).prepare()
    assert prepared.url.endswith('%E8%B5%84%E6%BA%90?count=8')
    ca = Path(certifi.where()).resolve()
    assert ca == site / 'certifi/cacert.pem'
    builder.verify_file(ca, records['certifi/cacert.pem'])
    native_context = ssl.create_default_context(cafile=str(ca))
    urllib_context = create_urllib3_context()
    urllib_context.load_verify_locations(cafile=str(ca))
    fingerprints = lambda context: sorted(hashlib.sha256(b).hexdigest() for b in context.get_ca_certs(binary_form=True))
    assert native_context.cert_store_stats()['x509_ca'] == 121
    assert urllib_context.cert_store_stats()['x509_ca'] == 121
    assert fingerprints(native_context) == fingerprints(urllib_context)
    formal_metadata = None
    if args.http_meta:
        path = builder.real_path(args.http_meta)
        source_sha = builder.sha(path)
        init = path.parent / '__init__.py'
        load(init, 'formal_http_metadata_validation', package=True)
        original = load(path, 'formal_http_metadata_validation.downloader')
        descriptor = original.RequestDescription('GET', payload['url'], {'etag':'fresh'})
        metadata = original.HTTPMetadata(descriptor, etag='pure8', last_modified='Wed, 01 Oct 2025 12:00:00 GMT', size_on_disk=121)
        with tempfile.TemporaryDirectory(prefix='pure-http-metadata-', dir=args.tmp) as private:
            # Real formal provider's converter; no filesystem lock/import of bpy needed.
            provider = original.MetadataProviderFilesystem(Path(private))
            formal_converter = provider._ensure_converter()
            encoded = formal_converter.dumps(metadata)
            decoded = formal_converter.loads(encoded, original.HTTPMetadata)
            assert decoded == metadata and decoded.request.response_headers == descriptor.response_headers
            assert len(encoded.encode()) < 1024
        assert builder.sha(path) == source_sha
        formal_metadata = {'source_sha256':source_sha, 'budget_source_sha256':builder.sha(path.parent / '_budget.py'),
                           'actual_HTTPMetadata_roundtrip':True, 'actual_MetadataProviderFilesystem_converter':True,
                           'payload_bytes':len(encoded.encode()), 'temporary_directory_cleaned':not Path(private).exists()}
    forbidden = ('socks','brotli','brotlicffi','zstandard','exceptiongroup')
    for name in forbidden:
        if importlib.util.find_spec(name) is not None:
            raise ValueError('Unexpected optional/native distribution visible: ' + name)
    if importlib.util.find_spec('bpy') is not None or 'bpy' in sys.modules:
        raise ValueError('bpy must remain unavailable during terminal pure validation')
    pure_names = {'requests','urllib3','idna','certifi','charset_normalizer','cattrs','cattr','attrs','attr','typing_extensions'}
    for name, imported in tuple(sys.modules.items()):
        if name.split('.')[0] in pure_names and getattr(imported, '__file__', None):
            path = Path(imported.__file__).resolve()
            if site not in path.parents:
                raise ValueError('Pure submodule escaped selected resource root')
            builder.verify_file(path, records[path.relative_to(site).as_posix()])
    assert builder.verify_resources(repo, root)['site_tree_sha256'] == verified['site_tree_sha256']
    report = {'status':'PASS', 'runtime_label':'accepted-terminal-CPython-3.13.13-independent-pure-only',
              'interpreter_version':'.'.join(map(str, sys.version_info[:3])), 'interpreter_sha256':builder.sha(Path(sys.executable)),
              'flags':{'isolated':True,'no_site':True,'dont_write_bytecode':True}, 'cwd_excluded':True,
              'initial_sys_path_seal':before_path_seal, 'old_sites_excluded':True, 'bpy_unavailable':True,
              'lock_sha256':lock_sha, 'site_tree_sha256':verified['site_tree_sha256'], 'pure_file_count':len(records),
              'origins':origins, 'small_actual_json_roundtrip':True, 'attrs_alias_roundtrip':True,
              'certifi_CA_bundle_sha256':builder.sha(ca), 'native_ssl_public_ca_count':121, 'urllib3_ssl_public_ca_count':121,
              'public_ca_der_set_sha256':builder.seal(fingerprints(native_context)), 'formal_metadata':formal_metadata,
              'network_requests':0, 'native_builds':0, 'elapsed_seconds':time.monotonic() - start}
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
