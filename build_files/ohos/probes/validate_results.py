#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate recorded JSON against real SDK structs and cross-check native runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent

def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sdk_bool_fields(header, struct):
    match = re.search(r'typedef struct ' + re.escape(struct) + r' \{(.*?)\} ' + re.escape(struct) + ';', header, re.S)
    require(match is not None, f'{struct} not found in actual SDK')
    return set(re.findall(r'VkBool32\s+(\w+)\s*;', match.group(1)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('result_dir', type=Path, nargs='?', default=HERE / 'results-verified')
    args = ap.parse_args()
    results = args.result_dir.resolve()
    meta = json.loads((results / 'build-info.json').read_text())
    require(meta['buildAndSignSucceeded'], 'Build/sign did not succeed')
    require(meta['nativePlatform'] == 'ohos' and meta['machine'] in ('aarch64', 'arm64'), 'Not native OHOS ARM64')
    require(meta['hapExecutionVerified'] is False and meta['wsiVerified'] is False, 'Overstated HAP/WSI validation')
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve()
    for name, artifact in meta['artifacts'].items():
        path = Path(artifact['path']).resolve()
        require(cache in path.parents, f'{name} is not stored in XDG_CACHE_HOME')
        with path.open('rb') as f:
            require(hashlib.file_digest(f, 'sha256').hexdigest() == artifact['sha256'], f'{name} hash mismatch')
    for name in ('vulkan_probe.cpp', 'vulkan_probe.h'):
        with (HERE / name).open('rb') as f:
            require(hashlib.file_digest(f, 'sha256').hexdigest() == meta['sourceSha256'][name], f'{name} changed after build')
    header = (Path(meta['nativeSdk']) / 'sysroot/usr/include/vulkan/vulkan_core.h').read_text()
    schema = {key: sdk_bool_fields(header, struct) for key, struct in
              [('features10', 'VkPhysicalDeviceFeatures'),
               ('features11', 'VkPhysicalDeviceVulkan11Features'),
               ('features12', 'VkPhysicalDeviceVulkan12Features')]}
    reports = {label: json.loads((results / f'vulkan-{label}.json').read_text()) for label in ('default', 'api12', 'api10')}
    for label, report in reports.items():
        require(report['wsi']['verified'] is False, f'{label}: WSI overclaim')
        if report['instanceCreation']['code'] != 0:
            require(not report['devices'], f'{label}: devices despite initialization failure')
            require(report['status'] == 'instance_initialization_failed', f'{label}: bad failure label')
            continue
        for d in report['devices']:
            for key, names in schema.items():
                if key != 'features10' and not d[key + 'Queried']:
                    require(d[key] is None, f'{label}: unqueried aggregate features must be null')
                    continue
                require(set(d[key]) == names, f'{label}: {key} fields do not match actual SDK')
                require(all(type(v) is bool for v in d[key].values()), f'{label}: {key} is not bool-only')
            cross = d['features10CrossCheck']
            require(cross['legacyFeatures'] == d['features10'], f'{label}: independent direct feature query disagreement')
            require(cross['legacyVsFeatures2Consistent'], f'{label}: consistency flag false')
            checklist = d['blender522Required']
            required = checklist['features']
            for name in schema['features10'] & set(required):
                require(required[name] == d['features10'][name], f'{label}: false checklist value for {name}')
            for name, key in [('shaderDrawParameters', 'features11'), ('timelineSemaphore', 'features12'), ('bufferDeviceAddress', 'features12')]:
                if d[key + 'Queried']:
                    require(required[name] == d[key][name], f'{label}: checklist mismatch for {name}')
            require(required['dynamicRendering'] == d['dynamicRendering'], f'{label}: dynamicRendering mismatch')
            require(required['provokingVertexLast'] == d['provokingVertexLast'], f'{label}: provokingVertexLast mismatch')
            require(set(checklist['missingFeatures']) == {k for k, v in required.items() if v is False}, f'{label}: missing feature list mismatch')
            require(set(checklist['unknownFeatures']) == {k for k, v in required.items() if v is None}, f'{label}: unknown feature list mismatch')
            instance_names = {p['name'] for p in report['instanceExtensions']}
            device_names = {p['name'] for p in d['extensions']}
            for name, value in checklist['extensions'].items():
                listed = name in (instance_names if name in ('VK_KHR_surface', 'VK_OHOS_surface') else device_names)
                require(value == listed, f'{label}: extension support assertion not backed by enumeration: {name}')
            require(set(checklist['missingExtensions']) == {k for k, v in checklist['extensions'].items() if v is False}, f'{label}: missing extension list mismatch')
            expected_pass = (checklist['minimumApi12'] and all(v is True for v in required.values())
                             and all(v is True for v in checklist['extensions'].values()))
            require(checklist['strictEnumerationPass'] == expected_pass, f'{label}: false strictEnumerationPass')
            for trial in d.get('singleFeatureEnableTrials', []):
                feature = trial['feature']
                require(trial['reportedSupported'] == d['features10'][feature], f'{label}: trial support value mismatch')
                # VK_ERROR_FEATURE_NOT_PRESENT confirms a rejected false capability.
                expected = 0 if trial['reportedSupported'] else -8
                require(trial['creation']['code'] == expected, f'{label}: feature enable test contradicts query: {feature}')
    if reports['default']['devices'] and reports['api12']['devices']:
        a, b = reports['default']['devices'][0], reports['api12']['devices'][0]
        for key in ('vendorID', 'deviceID', 'features10', 'features11', 'features12', 'dynamicRendering', 'provokingVertexLast'):
            require(a[key] == b[key], f'API 1.3/1.2 disagreement: {key}')
    for d in reports['api10']['devices']:
        require(not d['features11Queried'] and not d['features12Queried'], 'Invalid aggregate feature query at effective API 1.0')
        require(not d['blender522Required']['minimumApi12'], 'API 1.0 cannot meet minimum Vulkan 1.2')
    summary = {'validated': True, 'sdkFeatureFieldCounts': {k: len(v) for k, v in schema.items()},
               'nativeReports': {k: {'status': v['status'], 'deviceCount': len(v['devices'])} for k, v in reports.items()},
               'checks': ['SDK-exact feature schemas', 'artifact/source SHA-256', 'legacy-vs-features2 equality',
                          'checklist agrees with raw features/extensions', 'API1.3/1.2 consistency',
                          'single-feature vkCreateDevice rejection/positive-control', 'unqueried is null',
                          'WSI/HAP not overclaimed']}
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
