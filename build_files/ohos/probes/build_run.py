#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build/sign/run the Vulkan probe natively using the installed real OHOS SDK.

Sources and reports remain in this new probes tree. ELF objects and executables
are exclusively under XDG_CACHE_HOME. No network, HAP permissions, namespace
workarounds, GUI server, or existing Godot/Blender modifications are involved.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent

def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--native-sdk', type=Path, help='Defaults to oheco installed.json active SDK')
    ap.add_argument('--result-dir', type=Path, default=HERE / 'results', help='Must be a new directory inside probes')
    args = ap.parse_args()
    if sys.platform != 'ohos' or platform.machine() not in ('aarch64', 'arm64'):
        raise SystemExit('Native HarmonyOS/OpenHarmony ARM64 execution is required')
    cache = Path(os.environ['XDG_CACHE_HOME']).resolve()
    result_dir = args.result_dir.resolve()
    if HERE not in result_dir.parents:
        raise SystemExit('Reports must be inside the new probes directory')
    # An explicit SDK must work without an oheco installation. Automatic
    # discovery reads only package metadata, never assumes a workspace SDK.
    installed_file = Path.home() / '.oheco/state/installed.json'
    installed = None
    sdk_artifact = None
    if args.native_sdk is not None:
        native = args.native_sdk.expanduser().resolve()
        if not (native / 'sysroot').is_dir() and (native / 'native/sysroot').is_dir():
            native = native / 'native'
    else:
        if not installed_file.is_file():
            raise SystemExit('Pass --native-sdk or install the native SDK with oheco')
        installed = json.loads(installed_file.read_text())
        native_version = installed['packages']['ohos-sdk-native']['active']
        native = (Path.home() / '.oheco/packages/ohos-sdk-native' / native_version).resolve()
        sdk_artifact = installed['packages']['ohos-sdk-native']['versions'][native_version]['artifact']
    metadata_file = native / 'oh-uni-package.json'
    metadata = json.loads(metadata_file.read_text()) if metadata_file.is_file() else None
    sysroot = native / 'sysroot'
    header = sysroot / 'usr/include/vulkan/vulkan_core.h'
    ohos_header = sysroot / 'usr/include/vulkan/vulkan_ohos.h'
    vulkan_link_library = sysroot / 'usr/lib/aarch64-linux-ohos/libvulkan.so'
    for p in (header, ohos_header, vulkan_link_library):
        if not p.is_file():
            raise SystemExit(f'Real SDK input missing: {p}')
    tools = {name: shutil.which(name) for name in ('clang++', 'binary-sign-tool', 'llvm-readelf')}
    if not all(tools.values()):
        raise SystemExit(f'Missing tools; check oheco PATH: {tools}')
    result_dir.mkdir(parents=True, exist_ok=False)
    build = Path(tempfile.mkdtemp(prefix='blender-ohos-vulkan-', dir=cache))
    meta = {'startedAtUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'nativePlatform': sys.platform, 'machine': platform.machine(),
            'nativeSdk': str(native), 'sdkMetadata': metadata,
            'installedMetadata': str(installed_file) if installed is not None else None,
            'sdkArtifact': sdk_artifact,
            'buildDirectory': str(build), 'resultDirectory': str(result_dir), 'tools': tools,
            'headerSha256': sha256(header), 'ohosHeaderSha256': sha256(ohos_header),
            'sdkVulkanLinkLibrarySha256': sha256(vulkan_link_library),
            'sourceSha256': {p.name: sha256(p) for p in (HERE / 'vulkan_probe.cpp', HERE / 'vulkan_probe.h', Path(__file__))},
            'extraHapPermissionsRequested': False, 'hapExecutionVerified': False,
            'wsiVerified': False, 'runs': []}
    log_path = result_dir / 'build.log'
    try:
        with log_path.open('w') as log:
            def command(cmd):
                print('+ ' + ' '.join(str(s) for s in cmd), file=log, flush=True)
                print('+ ' + ' '.join(str(s) for s in cmd), flush=True)
                proc = subprocess.run([str(s) for s in cmd], stdout=log, stderr=subprocess.STDOUT)
                print(f'exit={proc.returncode}', file=log, flush=True)
                if proc.returncode:
                    raise RuntimeError(f'Command failed ({proc.returncode}); inspect {log_path}: {cmd[0]}')
            command([tools['clang++'], '--version'])
            with (result_dir / 'signer-help.log').open('w') as help_log:
                subprocess.run([tools['binary-sign-tool']], stdout=help_log, stderr=subprocess.STDOUT, check=True)
            common = [tools['clang++'], '--target=aarch64-linux-ohos', '--sysroot=' + str(sysroot),
                      '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-missing-field-initializers',
                      str(HERE / 'vulkan_probe.cpp'), '-lvulkan', '-ldeviceinfo_ndk.z', '-ldl']
            command(common + ['-o', build / 'vulkan-probe.unsigned'])
            command([tools['binary-sign-tool'], 'sign', '-inFile', build / 'vulkan-probe.unsigned',
                     '-outFile', build / 'vulkan-probe', '-selfSign', '1'])
            (build / 'vulkan-probe').chmod(0o755)
            command(common + ['-fPIC', '-shared', '-DBLENDER_VULKAN_PROBE_NO_MAIN=1',
                              '-Wl,-soname,libblender_vulkan_probe.so', '-o', build / 'libblender_vulkan_probe.unsigned.so'])
            command([tools['binary-sign-tool'], 'sign', '-inFile', build / 'libblender_vulkan_probe.unsigned.so',
                     '-outFile', build / 'libblender_vulkan_probe.so', '-selfSign', '1'])
            command([tools['binary-sign-tool'], 'display-sign', '-inFile', build / 'vulkan-probe'])
            command([tools['binary-sign-tool'], 'display-sign', '-inFile', build / 'libblender_vulkan_probe.so'])
            command([tools['llvm-readelf'], '-h', '-d', build / 'vulkan-probe'])
            # Preserve error JSON instead of treating process-context Vulkan failure as build failure.
            for label, extra in [('default', []), ('api12', ['--api', '1.2']), ('api10', ['--api', '1.0'])]:
                cmd = [str(build / 'vulkan-probe'), *extra, '--context', 'terminal_child_process']
                output = result_dir / f'vulkan-{label}.json'
                stderr = result_dir / f'vulkan-{label}.stderr.log'
                print('+ ' + ' '.join(cmd), file=log, flush=True)
                with output.open('w') as out, stderr.open('w') as err:
                    proc = subprocess.run(cmd, stdout=out, stderr=err)
                run = {'label': label, 'command': cmd, 'exitCode': proc.returncode,
                       'json': str(output), 'stderr': str(stderr)}
                try:
                    report = json.loads(output.read_text())
                    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
                    run['status'] = report['status']
                    run['deviceCount'] = len(report['devices'])
                    run['instanceCreation'] = report['instanceCreation']
                except (json.JSONDecodeError, KeyError) as error:
                    run['reportParseError'] = str(error)
                meta['runs'].append(run)
                print(json.dumps(run, ensure_ascii=False), file=log, flush=True)
                print(json.dumps(run, ensure_ascii=False), flush=True)
                if proc.returncode not in (0, 2, 3):
                    raise RuntimeError(f'Unexpected probe failure {proc.returncode}: inspect {stderr}')
            meta['artifacts'] = {name: {'path': str(build / name), 'sha256': sha256(build / name),
                                      'size': (build / name).stat().st_size}
                                 for name in ('vulkan-probe', 'libblender_vulkan_probe.so')}
            meta['buildAndSignSucceeded'] = True
    except Exception as error:
        meta['error'] = str(error)
        raise
    finally:
        (result_dir / 'build-info.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n')
    print('Build/sign complete; actual device conclusions are in the JSON reports.')
    print(result_dir / 'build-info.json')

if __name__ == '__main__':
    main()
