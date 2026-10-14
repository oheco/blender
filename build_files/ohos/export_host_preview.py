#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export the accepted host.17 as a self-signable DevEco preview project.

This packages the already-signed native inputs, not a fresh Core build. The
allowlist starts from the public source template; no private project/profile,
credentials, device identifiers, build caches or SDK paths are copied.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
import zipfile

VERSION = '5.2.2-ohos.17'
HAP_SHA256 = '9a986a6402679b80079895d8760bed0d2e47395e22e34cdddda58e423a1299de'
CORE_SHA256 = '5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443'
HOST_SHA256 = '14f05da77e6016bea44110a78cf9ad3ee63476b92af5f14092ec4030b4d71e99'
README = '''# Blender 5.2.2-ohos.17 — DevEco preview

This project contains the editor sources, runtime resources, and the
same 94 signed ARM64 native libraries as the accepted development HAP. The
native Core was built incrementally with strict OFF/libmv OFF; this is an
experimental editor preview, not complete Blender port acceptance.

Use HarmonyOS API22 or later; only MOR-M2/Maleoon935 on API26 was exercised.
Resize (120ms ordinary, 250ms maximize/recover), IME and shortcuts received
user confirmation. This revision adds Desktop/Documents/Download authorization,
SDK directory mapping, INTERNET permission, a relocatable CA bundle and the
system HTTP/HTTPS browser bridge. The user confirmed all three directory
shortcuts, browser opening and online extension-list refresh on the same device.
Broad editing/rendering, other devices and cross-display candidate placement
were not comprehensively accepted.

## Build and sign

Open this directory in a DevEco version supporting API26, using official
command-line-tools 26.0.0.201, Hvigor/plugin 7.26.8 and SDK26. Configure your
own signing identity and provisioning profile in DevEco. No certificate,
private key, password, personal signing configuration or SDK path is included.
The default build compiles ArkTS/resources and preserves the bundled native
libraries (`strip: false`); it does not rebuild the Blender Core or host.
Allow DevEco to resolve the pinned build plugin. An offline build needs the
official wrapper's package cache prepared first.

Bundle: `org.oheco.blender`. Version code: 17. Configure a valid profile with
Desktop directory permission approved in its ACL; Documents/Download also need
the platform's provisioning authorization. At first launch, grant the requested
directory access. INTERNET is a normal permission and has no user-grant dialog.

For an existing installation, preserve its signing configuration and use update
installation. In this device acceptance, the user regenerated signing material;
the certificate public key changed and the update still preserved UID, token and
application data. This does not establish signature-change compatibility on other
devices. Do not uninstall an existing app merely to change signatures.

If using hdc on an authorized device:

```sh
hdc -t <device> install -r <your-signed-hap>
```

The separate downloadable pre-signed HAP uses a developer provisioning profile
and only installs on its already-authorized devices. Exporting this project
with `oo export blender app` does not build, sign or install the app.

## Optional host rebuild

The C++ host source is included, with four matching ABI headers under
`blender-abi`. Native compilation is disabled in the default entry profile.
To rebuild it deliberately, enable externalNativeOptions for
`entry/src/main/cpp/CMakeLists.txt`, pass BLENDER_SOURCE=<project>/blender-abi,
BLENDER_CORE_LIBRARY=<project>/entry/libs/arm64-v8a/libblender_core.so,
OHOS_STL=c++_static and BLENDER_OHOS_MALEOON_TILE_EXPERIMENT=ON. Build serially,
sign the new ELF and preserve its final bytes. Core remains prebuilt. Full
Core/dependency build instructions and corresponding source are in
https://github.com/oheco/blender/tree/ohos/5.2.2/build_files/ohos .

## Source and licenses

Upstream: https://github.com/blender/blender (v5.2.2).
Adaptation: https://github.com/oheco/blender/tree/5.2.2-ohos.17 .
Blender is GPL-2.0-or-later. COPYING and third-party license notices are
included. PREVIEW_MANIFEST.json binds the accepted HAP, native libraries,
ABI/source inputs and bundled payload. Do not describe an app rebuild using
these prebuilt native libraries as a fresh full Core build.
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def archive_name(name):
    parts = PurePosixPath(name).parts
    require(parts and not name.startswith('/') and '..' not in parts and '\\' not in name,
            'Unsafe archive member')
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hap', type=Path, required=True)
    parser.add_argument('--hap-receipt', type=Path, required=True)
    parser.add_argument('--host-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    template = repo / 'build_files/ohos/hap_host/deveco'
    require(not args.output.exists() and args.output.parent.is_dir(), 'Output must be new')
    require(digest(args.hap) == HAP_SHA256, 'Use the accepted, unchanged host.17 HAP')
    packaging = json.loads(args.hap_receipt.read_text())
    native = json.loads(args.host_receipt.read_text())
    require(packaging['status'] == native['status'] == 'success' and
            packaging['hap_verify_exit_code'] == 0 and native['all_core_imports_bound'],
            'Successful native, official signature and packaging receipts required')
    require(packaging['output']['sha256'] == HAP_SHA256 and packaging['parity']['libraries'] == 94,
            'Wrong packaging evidence')
    require(native['core']['sha256'] == CORE_SHA256 and native['host']['sha256'] == HOST_SHA256,
            'Wrong native inputs')
    require(all(digest(Path(row['path'])) == row['sha256'] for row in native['source_inputs']),
            'Compiled native source inputs changed')
    tracked = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z'],
                             capture_output=True, check=True).stdout.decode().split('\0')
    public = [p for p in template.rglob('*') if p.is_file()]
    require(all(not p.is_symlink() for p in public), 'Public template cannot contain symlinks')
    generated = {
        'build-profile.json5': json.dumps({'app': {'products': [{
            'name': 'default', 'compileSdkVersion': '26.0.0', 'targetSdkVersion': '26.0.0',
            'compatibleSdkVersion': '6.0.2(22)', 'runtimeOS': 'HarmonyOS'}]},
            'modules': [{'name': 'entry', 'srcPath': './entry', 'targets': [{'name': 'default'}]}]}, indent=2) + '\n',
        'oh-package.json5': json.dumps({'name': 'blender-ohos-host', 'version': '5.2.2',
            'description': 'Blender 5.2.2-ohos.17 preview with prebuilt signed native inputs',
            'modelVersion': '6.0.0', 'devDependencies': {'@ohos/hvigor-ohos-plugin': '7.26.8'}}, indent=2) + '\n',
        'entry/build-profile.json5': json.dumps({'apiType': 'stageMode',
            'buildOption': {'nativeLib': {'debugSymbol': {'strip': False, 'exclude': []}}},
            'targets': [{'name': 'default'}]}, indent=2) + '\n',
        'README.md': README,
        '.gitignore': 'node_modules/\noh_modules/\n.hvigor/\nentry/build/\nlocal.properties\nsigning/\n',
    }
    app = json.loads((template / 'AppScope/app.json5').read_text())
    app['app'].update(versionCode=17, versionName=VERSION)
    generated['AppScope/app.json5'] = json.dumps(app, indent=2) + '\n'
    inventory = {}
    names_seen = set()
    entries = {}
    for path in public:
        name = archive_name(path.relative_to(template).as_posix())
        require(not any(part in {'node_modules', 'oh_modules', '.hvigor', 'build'} for part in PurePosixPath(name).parts),
                'Generated or cache directory found in public template')
        entries[name] = path
    for name in generated:
        entries.pop(name, None)
    for name in tracked:
        if not name:
            continue
        if name.startswith('release/license/') or (name.startswith('tpr/sources/') and '/licenses/' in name) or (
                name.startswith('build_files/ohos/deps_') and '/notices/' in name):
            path = repo / name
            require(path.is_file() and not path.is_symlink(), 'License notice must be an ordinary file')
            entries['licenses/' + name] = path
    entries['COPYING'] = repo / 'COPYING'
    for name in ['intern/ghost/GHOST_OHOSHost.h', 'intern/ghost/GHOST_OHOSNative.h',
                 'source/creator/creator_ohos.h', 'source/creator/creator_ohos_files.h']:
        entries['blender-abi/' + name] = repo / name
    root = 'blender-app-' + VERSION
    native_members = {}
    payload_members = {}
    with zipfile.ZipFile(args.hap) as accepted, zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED,
                                                             compresslevel=6, allowZip64=True) as archive:
        require(accepted.testzip() is None, 'Accepted HAP ZIP CRC verification failed')
        def put(name, data):
            name = archive_name(name)
            require(name.casefold() not in names_seen, 'Duplicate or case-clashing archive member')
            names_seen.add(name.casefold())
            info = zipfile.ZipInfo(root + '/' + name, date_time=(2026, 10, 11, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=6)
            inventory[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
        for name, path in sorted(entries.items()):
            put(name, path.read_bytes())
        for name, text in sorted(generated.items()):
            put(name, text.encode())
        for member in accepted.infolist():
            if member.is_dir():
                continue
            name = archive_name(member.filename)
            if name.startswith('libs/arm64-v8a/'):
                data = accepted.read(member)
                require(name in packaging['native_inputs'] and
                        hashlib.sha256(data).hexdigest() == packaging['native_inputs'][name]['sha256'],
                        'Accepted native bytes differ from packaging evidence')
                target = 'entry/' + name
                put(target, data)
                native_members[name] = inventory[target]
            elif name.startswith('resources/rawfile/'):
                target = 'entry/src/main/' + name
                put(target, accepted.read(member))
                payload_members[name] = inventory[target]
        require(len(native_members) == 94 and 'resources/rawfile/runtime-manifest.json' in payload_members,
                'Missing native libraries or generated runtime payload')
        require(native_members['libs/arm64-v8a/libblender_core.so']['sha256'] == CORE_SHA256 and
                native_members['libs/arm64-v8a/libblender_host.so']['sha256'] == HOST_SHA256,
                'Native byte parity failed')
        metadata = {'schema': 1, 'version': VERSION, 'bundle': 'org.oheco.blender', 'minimum_api': 22,
            'native_policy': 'prebuilt signed libraries preserved; no fresh Core build',
            'accepted_hap_sha256': HAP_SHA256, 'accepted_hap_version': packaging['app_version'],
            'native_libraries': native_members,
            'payload_files': payload_members, 'source_and_notices': {k: v for k, v in inventory.items()
                if not k.startswith(('entry/libs/', 'entry/src/main/resources/rawfile/'))},
            'signing_material_included': False,
            'user_confirmed': ['resize', 'IME', 'shortcuts', 'Desktop/Documents/Download shortcuts',
                               'system browser links', 'online extension-list refresh'],
            'limits': ['incremental diagnostic Core: strict OFF, libmv OFF',
                       'MOR-M2 Maleoon935/API26 cohort only', 'no broad editing/rendering acceptance'],
            'source_repository': 'https://github.com/oheco/blender', 'source_tag': VERSION}
        put('PREVIEW_MANIFEST.json', (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode())
    with zipfile.ZipFile(args.output) as finished:
        require(finished.testzip() is None, 'Exported project ZIP CRC verification failed')
    result = {'status': 'success', 'version': VERSION, 'project': {'path': str(args.output),
        'size': args.output.stat().st_size, 'sha256': digest(args.output)},
        'native_libraries': len(native_members), 'payload_files': len(payload_members),
        'files': len(inventory), 'native_parity': True, 'signing_material_included': False}
    with args.output.with_suffix(args.output.suffix + '.receipt.json').open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
