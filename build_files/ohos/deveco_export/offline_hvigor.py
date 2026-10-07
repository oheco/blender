# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind a fixed native Hvigor adapter to sealed offline inputs; IO only."""
from pathlib import Path
from common import digest, load, require, verify_record, object_digest
import offline_cache_links

ADAPTER = '@oheco/hvigor'
ADAPTER_VERSION = '6.26.4-ohos.1'
UPSTREAM_VERSION = '6.26.4'
REGISTRY = 'https://repo.harmonyos.com/npm/'
MANIFESTS = {
    ADAPTER: 'package.json',
    '@ohos/hvigor': 'node_modules/@ohos/hvigor/package.json',
    '@ohos/hvigor-ohos-plugin': 'node_modules/@ohos/hvigor-ohos-plugin/package.json',
}
UPSTREAM_ARCHIVES = {
    '@ohos/hvigor': ('33b2741aca3ee00f6375d6a988b4951875a0c2369d0b0ce3568a6d69249ad82d', 16267563,
                    'sha512-uZDPJpEiYwy4+hM1Pffx/lW3YmKBehDzOFgzMrGjB4I8XPkDTylvRqLVaFfTlEl7vx/I6kjPW1DrizGjxFwHdQ=='),
    '@ohos/hvigor-ohos-plugin': ('2f97a309bad4297a478091278ed6372c18330f1159551427529436c3525779b6', 33653590,
                                'sha512-OSmhqIermRUhpp6c0HbqClB2OCKNsxsJd5cQe4DJWLgBBxhlIs1cXJYSgRgL5UB5BseVSuE0fdRV6oPv0L9T8g=='),
}


def validate_payload(origin: Path, manifest: dict, selected_entry: dict, cache=None) -> dict:
    """Check bytes, fixed identities, registry/integrity and selected entry binding."""
    files = manifest['files']
    rows = {row['path']: row for row in files}
    require(len(rows) == len(files), 'Duplicate offline inventory paths')
    require(((manifest.get('kind') == 'frozen-offline-cache' and cache is None) or
             (manifest.get('kind') == offline_cache_links.KIND and cache is not None and
              cache.manifest is manifest and cache.root == Path(origin))) and
            manifest.get('network_at_build_time') is False,
            'Sealed offline cache or explicitly verified Hvigor filelink cache required')
    require(manifest.get('resolution_lock') == 'npm-shrinkwrap.json',
            'Native adapter npm-shrinkwrap must be the sealed resolution lock')
    required = set(MANIFESTS.values()) | {'bin/hvigor.cjs', 'upstream-lock.json', 'npm-shrinkwrap.json'}
    require(required <= set(rows), 'Native Hvigor adapter/lock/entry inputs are not sealed')
    observed = {name: cache.file(name) if cache is not None else verify_record(origin, rows[name])
                for name in required}
    declared = {(p['name'], p['version'], p['manifest']) for p in manifest['packages']}
    require({(name, ADAPTER_VERSION if name == ADAPTER else UPSTREAM_VERSION, path)
             for name, path in MANIFESTS.items()} <= declared,
            'Selected native adapter and both official manifests must be declared')
    packages = {name: load(observed[path]) for name, path in MANIFESTS.items()}
    for name, value in packages.items():
        require(value.get('name') == name and
                value.get('version') == (ADAPTER_VERSION if name == ADAPTER else UPSTREAM_VERSION),
                'Unreviewed Hvigor package version: ' + name)
    adapter = packages[ADAPTER]
    expected_deps = {'@ohos/hvigor': UPSTREAM_VERSION, '@ohos/hvigor-ohos-plugin': UPSTREAM_VERSION}
    require(adapter.get('dependencies') == expected_deps and
            adapter.get('bin', {}).get('hvigor') == 'bin/hvigor.cjs' and
            adapter.get('os') == ['openharmony'] and adapter.get('cpu') == ['arm64'] and
            adapter.get('engines', {}).get('node') == '>=24',
            'Actual native adapter platform, entry or dependency declaration differs')
    upstream = load(observed['upstream-lock.json'])
    require(upstream.get('schemaVersion') == 1 and upstream.get('registry') == REGISTRY and
            set(upstream.get('packages', {})) == set(expected_deps),
            'Native adapter upstream source lock differs')
    shrink = load(observed['npm-shrinkwrap.json'])
    require(shrink.get('name') == ADAPTER and shrink.get('version') == ADAPTER_VERSION and
            shrink.get('lockfileVersion') == 3, 'Native adapter resolution identity differs')
    resolved = shrink.get('packages', {})
    require(resolved.get('', {}).get('dependencies') == expected_deps and
            resolved.get('', {}).get('name') == ADAPTER and
            resolved.get('', {}).get('version') == ADAPTER_VERSION,
            'Resolution root disagrees with native adapter')
    for name in expected_deps:
        source = upstream['packages'][name]
        record = resolved.get('node_modules/' + name, {})
        expected_url = REGISTRY + name + '/-/' + name + '-' + UPSTREAM_VERSION + '.tgz'
        expected_sha, expected_size, expected_integrity = UPSTREAM_ARCHIVES[name]
        require(source.get('version') == UPSTREAM_VERSION and source.get('url') == expected_url and
                source.get('sha256') == expected_sha and source.get('size') == expected_size and
                source.get('integrity') == expected_integrity and
                record.get('version') == UPSTREAM_VERSION and record.get('resolved') == source['url'] and
                record.get('integrity') == source['integrity'],
                'Official source and resolution lock disagree: ' + name)
    entry = observed['bin/hvigor.cjs']
    require(selected_entry['sha256'] == digest(entry),
            'Selected Hvigor entry is not the sealed native adapter entry')
    offline_cache_links.bind_selected_entry(selected_entry, entry)
    return {'adapter': ADAPTER, 'adapter_version': ADAPTER_VERSION,
            'upstream_version': UPSTREAM_VERSION, 'official_registry': REGISTRY,
            'entry_sha256': digest(entry), 'resolution_lock_sha256': digest(observed['npm-shrinkwrap.json']),
            'upstream_lock_sha256': digest(observed['upstream-lock.json']),
            'inventory_digest': object_digest(manifest),
            'selected_entry': selected_entry,
            'scope': 'SOURCE_IO_PACKAGE_LOCK_ENTRY_BINDING_ONLY',
            'offline_resolution_and_CompileArkTS': 'NOT_RUN'}


def derive_project_package(project: Path, identity: dict) -> dict:
    """Update only the private copied template after successful payload validation."""
    require(identity.get('adapter') == ADAPTER and identity.get('adapter_version') == ADAPTER_VERSION and
            identity.get('upstream_version') == UPSTREAM_VERSION and
            identity.get('scope') == 'SOURCE_IO_PACKAGE_LOCK_ENTRY_BINDING_ONLY',
            'Validated native Hvigor identity required for project derivation')
    file = project / 'oh-package.json5'
    before_sha = digest(file)
    value = load(file)
    deps = value.get('devDependencies', {})
    require(deps.get('@ohos/hvigor-ohos-plugin') == '6.0.0',
            'Copied original host plugin declaration differs from reviewed template')
    deps['@ohos/hvigor-ohos-plugin'] = UPSTREAM_VERSION
    # hvigor-config modelVersion is a schema version. Preserve its original bytes.
    model = project / 'hvigor/hvigor-config.json5'
    model_sha = digest(model)
    import json
    with file.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    require(digest(model) == model_sha, 'Hvigor model schema was changed during package derivation')
    return {'path': 'oh-package.json5', 'before_sha256': before_sha, 'after_sha256': digest(file),
            'reason': 'Copied project plugin version equals selected native adapter official plugin 6.26.4',
            'hvigor_model_schema_preserved_sha256': model_sha,
            'scope': 'PRIVATE_EXPORT_COPY_ONLY_ORIGINAL_HOST_AND_OFFICIAL_PACKAGES_UNCHANGED'}
