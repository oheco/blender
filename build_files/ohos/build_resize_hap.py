#!/usr/bin/env python3
"""Offline resize-candidate packaging; invoke only after MAIN stages signed native inputs.

The official Release SDK is used for ArkTS/resources/HAP signing and packaging.
It never compiles, strips, or signs native ELF inputs, and never modifies --project.
--prepare-sdk-view creates only the private SDK selector; --validate-tools is read-only.
Successful builds create NEW HAP, sanitized log, and <output>.receipt.json exclusively.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zipfile

sys.dont_write_bytecode = True
DEFAULT_TOOLS = Path('/storage/Users/currentUser/.oheco/packages/command-line-tools/26.0.0.201')
DEFAULT_VIEW = Path('/data/storage/el2/base/haps/entry/cache/blender-resize-hap-sdk26-release-view')
DEFAULT_HVIGOR_HOME = Path('/data/storage/el2/base/haps/entry/cache/blender-resize-hvigor7')
DEFAULT_BASELINE = Path('/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.6-signed.hap')
BASELINE_SHA256 = '014d87ea359904f8c549d1143aa24bbaeb0369c0bb1f7bb8b2fbe6dff98b0a91'
OLD_CORE_SHA256 = 'f25b4b153abe4572a930a171a1255019f860af6f8066754f8646c47ebdd4ed8c'
COMPONENTS = ('native', 'ets', 'js', 'toolchains', 'previewer')
ENGINE_VERSION = '7.26.8'
SDK_VERSION = '26.0.0.105'
CLEARED_ENV = ('NODE_PATH', 'NODE_OPTIONS', 'NODE_OPTS', 'OHOS_SDK_HOME', 'HOS_SDK_HOME',
               'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONHOME', 'PYTHONPATH', 'config')
SENSITIVE_KEY = re.compile(r'password|passwd|pwd|secret|token|credential', re.I)
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class BuildError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise BuildError(message)


def sha_stream(stream):
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(1024 * 1024):
        digest.update(block)
        size += len(block)
    return {'sha256': digest.hexdigest(), 'size': size}


def file_record(path):
    with path.open('rb') as stream:
        return sha_stream(stream)


def exclusive_json(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def build_env(tools, view, hvigor_home):
    env = dict(os.environ)
    for key in CLEARED_ENV:
        env.pop(key, None)
    env.update(NODE_HOME=str(tools / 'node/bin'), DEVECO_SDK_HOME=str(tools / 'sdk'),
               OHOS_BASE_SDK_HOME=str(view), HVIGOR_USER_HOME=str(hvigor_home),
               PATH=f'{tools}/node/bin:{tools}/.oo-launchers:/system/bin:/usr/bin:/bin',
               PYTHONDONTWRITEBYTECODE='1', npm_config_offline='true',
               npm_config_audit='false', npm_config_fund='false',
               npm_config_update_notifier='false')
    # Legacy profile placeholders refer to engine/plugin paths, never to new keys.
    env.update(OHECO_HVIGOR=str(tools / 'hvigor/hvigor'),
               OHECO_HVIGOR_HOME=str(tools / 'hvigor/hvigor'),
               OHECO_HVIGOR_PLUGIN=str(tools / 'hvigor/hvigor-ohos-plugin'))
    return env


def json5_read(path, tools, env):
    # Raw profile/JSON5 parser output remains on a private pipe, never a log/console.
    code = "const fs=require('fs');const j=require(process.argv[1]);try{process.stdout.write(JSON.stringify(j.parse(fs.readFileSync(process.argv[2],'utf8'))));}catch(e){process.exit(2)}"
    result = subprocess.run([str(tools / 'node/bin/node'), '-e', code,
                             str(tools / 'hvigor/hvigor-ohos-plugin/node_modules/json5'), str(path)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    require(result.returncode == 0, 'Private JSON5 parsing failed; raw diagnostics suppressed')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise BuildError('Private JSON5 parser returned invalid data') from None


class Redactor:
    def __init__(self, *objects):
        self.values = set()
        for obj in objects:
            self.discover(obj)

    def discover(self, obj, sensitive=False):
        if isinstance(obj, dict):
            for key, value in obj.items():
                self.discover(value, sensitive or bool(SENSITIVE_KEY.search(key)))
        elif isinstance(obj, list):
            for value in obj:
                self.discover(value, sensitive)
        elif sensitive and isinstance(obj, str) and obj:
            self.add(obj)
            # Resolve environment placeholders without displaying their contents.
            self.add(os.path.expandvars(obj))

    def add(self, value):
        if not value:
            return
        for part in [value, *value.splitlines()]:
            if part:
                self.values.update((part, json.dumps(part)[1:-1], urllib.parse.quote(part, safe=''),
                                    part.replace("'", "'\\''")))

    def __call__(self, text):
        text = ANSI.sub('', text)
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, '<redacted>')
        # Suppress signing argv/profile dumps even if a new field was introduced.
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if re.search(r'password|passwd|pwd|signingConfigs|\bmaterial\b|keyAlias|storeFile|keystoreFile|profileFile|certpath|hap-sign-tool.*(?:sign-app|sign-profile)', line, re.I):
                lines[index] = '<redacted signing diagnostic>\n'
        return ''.join(lines)


def sdk_metadata(tools):
    data = {}
    for component in COMPONENTS:
        path = tools / 'sdk/default/openharmony' / component
        require(path.is_dir(), f'Official SDK component missing: {component}')
        item = json.loads((path / 'oh-uni-package.json').read_text())
        require((str(item.get('apiVersion')), item.get('platformVersion'), item.get('version'), item.get('releaseType'))
                == ('26', '26.0.0', SDK_VERSION, 'Release'),
                f'Official SDK component is not the pinned Release: {component}')
        data[component] = {key: item[key] for key in ('apiVersion', 'platformVersion', 'version', 'releaseType')}
    return data


def prepare_sdk_view(tools, view):
    sdk_metadata(tools)
    # Refuse to repair/delete existing entries: an inconsistent view is a hard error.
    require(not view.is_symlink(), 'SDK view root must not be a symlink')
    view.mkdir(parents=True, exist_ok=True, mode=0o700)
    platform = view / '26.0.0'
    require(not platform.is_symlink(), 'SDK platform selector must not be a symlink')
    platform.mkdir(exist_ok=True, mode=0o700)
    for component in COMPONENTS:
        dest = platform / component
        source = (tools / 'sdk/default/openharmony' / component).resolve(strict=True)
        if os.path.lexists(dest):
            require(dest.is_symlink() and dest.resolve(strict=True) == source,
                    f'SDK view entry differs from official tools: {component}')
        else:
            dest.symlink_to(source, target_is_directory=True)
    return validate_sdk_view(tools, view)


def validate_sdk_view(tools, view):
    data = sdk_metadata(tools)
    require(view.is_dir() and not view.is_symlink(), 'Private SDK view is absent; run --prepare-sdk-view')
    require(set(p.name for p in view.iterdir()) == {'26.0.0'}, 'SDK view contains a foreign platform')
    platform = view / '26.0.0'
    require(platform.is_dir() and not platform.is_symlink(), 'SDK platform selector is invalid')
    require(set(p.name for p in platform.iterdir()) == set(COMPONENTS), 'SDK view component set is inconsistent')
    for component in COMPONENTS:
        dest = platform / component
        require(dest.is_symlink() and dest.resolve(strict=True) == (tools / 'sdk/default/openharmony' / component).resolve(strict=True),
                f'SDK view component does not reference the pinned Release: {component}')
    return data


def validate_tools(tools, view, env):
    versions = {}
    for name in ('hvigor', 'hvigor-ohos-plugin'):
        root = tools / 'hvigor' / name
        package = json.loads((root / 'package.json').read_text())
        require(package['version'] == ENGINE_VERSION, f'Official {name} version differs from the pin')
        versions[name] = package['version']
    required = ('node/bin/node', 'hvigor/bin/hvigorw', 'hvigor/bin/hvigorw.js',
                'hvigor/hvigor/bin/hvigor.js', 'hvigor/hvigor-ohos-plugin/index.js',
                'sdk/default/openharmony/ets/build-tools/ets-loader/bin/ark/ts2abc.js',
                'sdk/default/openharmony/ets/build-tools/ets-loader/bin/ark/build/bin/es2abc',
                '.oo-launchers/hap-sign-tool', '.oo-launchers/restool', '.oo-launchers/ohos_packing_tool',
                'sdk/default/openharmony/toolchains/restool',
                'sdk/default/openharmony/toolchains/lib/hap-sign-tool',
                'sdk/default/openharmony/toolchains/lib/ohos_packing_tool')
    for relative in required:
        require((tools / relative).is_file(), f'Official build tool missing: {relative}')
    for relative in ('node/bin/node', 'sdk/default/openharmony/toolchains/restool',
                     'sdk/default/openharmony/toolchains/lib/hap-sign-tool',
                     'sdk/default/openharmony/toolchains/lib/ohos_packing_tool'):
        require(os.access(tools / relative, os.X_OK), f'Official tool is not executable: {relative}')
    result = subprocess.run([str(tools / 'node/bin/node'), '--version'], env=env,
                            capture_output=True, text=True, timeout=30)
    require(result.returncode == 0 and re.fullmatch(r'v\d+\.\d+\.\d+\s*', result.stdout),
            'Official Node version probe failed; diagnostics suppressed')
    versions['node'] = result.stdout.strip()
    return {'tools_root': str(tools), 'versions': versions, 'sdk_view': str(view),
            'sdk': validate_sdk_view(tools, view),
            'native_sdk_role': 'selector only; all native ELF inputs stay prebuilt with original Beta SDK',
            'network': 'No package installation; bundled engine/plugin and local file dependencies only'}


def excluded(path, name):
    if name in {'.git', '.idea', '.hvigor', 'node_modules', 'oh_modules', '.cxx', '.externalNativeBuild',
                '__pycache__', 'build', 'dist', 'hvigorw', 'hvigorw.bat', '.npmrc', '.ohpmrc', 'local.properties'}:
        return True
    if path.parts[-3:] == ('src', 'main', 'cpp') and name != 'types':
        return True
    return False


def copy_project(source, destination):
    def ignore(path, names):
        return [name for name in names if excluded(Path(path), name)]
    # Dereference payload symlinks into owned files, so the packager cannot write through.
    shutil.copytree(source, destination, symlinks=False, ignore=ignore)
    for path in destination.rglob('*'):
        if path.is_file():
            os.chmod(path, 0o600 | (stat.S_IMODE(path.stat().st_mode) & 0o111))
        elif path.is_dir():
            os.chmod(path, 0o700)


def source_inventory(project):
    digest = hashlib.sha256()
    count = 0
    def visit(path):
        nonlocal count
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if excluded(path, child.name):
                continue
            relative = child.relative_to(project).as_posix()
            if relative == 'build-profile.json5':
                continue  # Signing profile is deliberately outside the public source digest.
            if child.is_dir():
                visit(child)
            elif child.is_file():
                record = file_record(child)
                digest.update((relative + '\0' + record['sha256'] + '\0' + str(record['size']) + '\n').encode())
                count += 1
            else:
                raise BuildError('Source project contains a dangling link or special file')
    visit(project)
    return {'sha256': digest.hexdigest(), 'files': count,
            'scope': 'copied source/payload files, excluding signing root profile and generated/cache/config files'}


def codesign_record(path):
    with path.open('rb') as stream:
        header = stream.read(64)
        require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01', 'Core must be a little-endian ELF64')
        fields = struct.unpack('<16sHHIQQQIHHHHHH', header)
        offset, stride, count, names_index = fields[6], fields[11], fields[12], fields[13]
        require(stride == 64 and 0 < count < 65536 and names_index < count, 'Core ELF section table is invalid')
        stream.seek(offset)
        sections = [struct.unpack('<IIQQQQIIQQ', stream.read(stride)) for _ in range(count)]
        names_section = sections[names_index]
        stream.seek(names_section[4])
        names = stream.read(names_section[5])
        signed = [section for section in sections if names[section[0]:].split(b'\0', 1)[0] == b'.codesign']
        require(len(signed) == 1 and signed[0][5] > 0, 'Staged Core must have one nonempty .codesign section')
        stream.seek(signed[0][4])
        content = stream.read(signed[0][5])
        require(len(content) == signed[0][5], 'Core .codesign section is truncated')
        return {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content), 'offset': signed[0][4]}


def rebuilt_host_input(receipt_path, project, records):
    receipt = json.loads(Path(receipt_path).read_text())
    require(receipt.get('kind') == 'resize-host-native-build' and receipt.get('status') == 'success',
            'Rebuilt host requires a successful real native build receipt')
    for key, member in (('host', 'libs/arm64-v8a/libblender_host.so'),
                        ('core', 'libs/arm64-v8a/libblender_core.so')):
        item = receipt[key]
        expected = {name: item[name] for name in ('sha256', 'size')}
        require(records[member] == expected and file_record(Path(item['path'])) == expected,
                'Rebuilt host/Core receipt differs from the staged or linked input')
    require(receipt.get('all_core_imports_bound') is True and
            any(name.partition('@')[0] == 'ghost_ohos_native_resize_observe'
                for name in receipt.get('core_imports', [])),
            'Rebuilt host must bind the resize observation API and all Core imports')
    for key in ('configure', 'build'):
        require(receipt[key]['exit_code'] == 0, 'Rebuilt host configure/build must succeed')
        item = receipt[key]['log']
        require(file_record(Path(item['path'])) == {name: item[name] for name in ('sha256', 'size')},
                'Rebuilt host command log changed')
    commands = receipt['compile_commands']
    require(file_record(Path(commands['path'])) == {name: commands[name] for name in ('sha256', 'size')},
            'Rebuilt host compile command evidence changed')
    actual_cpp = project / 'entry/src/main/cpp'
    for item in receipt['source_inputs']:
        path = Path(item['path'])
        expected = {name: item[name] for name in ('sha256', 'size')}
        require(file_record(path) == expected, 'Rebuilt host source/header changed after compilation')
        if path.name in ('napi_init.cc', 'host_bridge.cc', 'reliable_input.cc', 'xcomponent_input.cc', 'ime_input.cc'):
            require(file_record(actual_cpp / path.name) == expected, 'Packaged host source differs from compiled source')
    require(codesign_record(actual_cpp.parents[2] / 'libs/arm64-v8a/libblender_host.so') == receipt['host_codesign'],
            'Rebuilt host native signature changed')
    return {'path': str(Path(receipt_path).resolve()), **file_record(Path(receipt_path))}


def native_inputs(project, baseline, rebuilt_host_receipt=None):
    root = project / 'entry/libs/arm64-v8a'
    require((root / 'libblender_core.so').is_file(), 'MAIN must stage the signed new Core before building')
    require((root / 'libblender_host.so').is_file(), 'MAIN must stage the verified baseline or explicitly rebuilt host')
    records = {f'libs/arm64-v8a/{p.name}': file_record(p) for p in sorted(root.iterdir()) if p.is_file()}
    core = records['libs/arm64-v8a/libblender_core.so']
    require(core['sha256'] != OLD_CORE_SHA256, 'Old Core is forbidden for resize-candidate packaging')
    require(file_record(baseline)['sha256'] == BASELINE_SHA256, 'Prior host6 HAP hash differs from the trusted baseline')
    with zipfile.ZipFile(baseline) as archive:
        with archive.open('libs/arm64-v8a/libblender_host.so') as stream:
            prior_host = sha_stream(stream)
    host_origin = {'host6_bytes_preserved': records['libs/arm64-v8a/libblender_host.so'] == prior_host}
    if rebuilt_host_receipt:
        host_origin['rebuilt_receipt'] = rebuilt_host_input(rebuilt_host_receipt, project, records)
    else:
        require(host_origin['host6_bytes_preserved'], 'Staged host differs from verified host6 bytes; provide --rebuilt-host-receipt')
    manifest_path = project / 'entry/src/main/resources/rawfile/runtime-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    libraries = manifest.get('libraries', [])
    require(isinstance(libraries, list), 'Runtime manifest libraries must be a list')
    for item in libraries:
        member = 'libs/arm64-v8a/' + item['library']
        require(member in records and item.get('sha256') == records[member]['sha256']
                and item.get('size') == records[member]['size'], 'Runtime native manifest does not match staged library bytes')
    require(sum(item.get('library') == 'libblender_core.so' for item in libraries) == 1,
            'Runtime manifest must inventory the new Core exactly once')
    return records, codesign_record(root / 'libblender_core.so'), file_record(manifest_path), host_origin


def replace_legacy(value, env, tools):
    if isinstance(value, dict):
        return {key: replace_legacy(item, env, tools) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_legacy(item, env, tools) for item in value]
    if not isinstance(value, str) or 'OHECO_HVIGOR' not in value:
        return value
    # Replace only recognized placeholders; unknown legacy references fail privately.
    pattern = r'\$\{(OHECO_HVIGOR[A-Z_0-9]*)\}|\$(OHECO_HVIGOR[A-Z_0-9]*)|\b(OHECO_HVIGOR[A-Z_0-9]*)\b'
    def substitute(match):
        key = next(group for group in match.groups() if group is not None)
        require(key in {'OHECO_HVIGOR_HOME', 'OHECO_HVIGOR_PLUGIN', 'OHECO_HVIGOR'},
                'Unknown legacy Hvigor signing reference; inspect privately')
        return env[key]
    return re.sub(pattern, substitute, value)


def configure_private(project, original, tools, view, env, root_profile, redactor):
    require(project.resolve() != original.resolve()
            and project.resolve().parent.name.startswith('blender-resize-hap-'),
            'Private project ownership check failed')
    root_profile = replace_legacy(root_profile, env, tools)
    products = [p for p in root_profile.get('app', {}).get('products', []) if p.get('name') == 'default']
    require(len(products) == 1, 'Source profile must define one default product')
    product = products[0]
    require((str(product.get('compileSdkVersion')), str(product.get('targetSdkVersion')),
             product.get('runtimeOS'))
            == ('26.0.0', '26.0.0', 'OpenHarmony'), 'Source SDK projection differs from API26/OpenHarmony')
    minimum_api = product.get('compatibleSdkVersion')
    require(type(minimum_api) is int and minimum_api in (15, 22), 'Unreviewed minimum API projection')
    # Plugin 7 accepts HarmonyOS only; its API26 HMS SDK uses the coherent OpenHarmony base.
    # This changes private packaging selection, never the signed prebuilt native inputs.
    product['runtimeOS'] = 'HarmonyOS'
    # Pinned official HosVersionMapper: API15 -> 5.0.3, API22 -> 6.0.2.
    # Preserve the source's reviewed minimum instead of lowering new input APIs.
    product['compatibleSdkVersion'] = {15: '5.0.3(15)', 22: '6.0.2(22)'}[minimum_api]
    identity = product.get('signingConfig')
    configs = root_profile['app'].get('signingConfigs', [])
    selected = [config for config in configs if config.get('name') == identity]
    require(identity and len(selected) == 1, 'Default product must select an existing signing identity')
    for config in configs:
        for key, value in config.get('material', {}).items():
            if key.lower() in {'storefile', 'profile', 'profilefile', 'certpath', 'certfile'} and isinstance(value, str):
                path = Path(value)
                if not path.is_absolute():
                    path = (original / value).resolve()
                require(path.is_file(), 'An existing signing material file is missing; path suppressed')
                config['material'][key] = str(path)
    redactor.discover(root_profile)
    (project / 'build-profile.json5').write_text(json.dumps(root_profile, indent=2) + '\n')
    package = json5_read(project / 'oh-package.json5', tools, env)
    require(package.get('modelVersion') == '6.0.0', 'Unexpected root modelVersion')
    for field in ('dependencies', 'devDependencies'):
        deps = package.setdefault(field, {})
        require(set(deps) <= {'@ohos/hvigor', '@ohos/hvigor-ohos-plugin'}, 'Root has unprepared external dependencies')
        if '@ohos/hvigor' in deps:
            deps['@ohos/hvigor'] = ENGINE_VERSION
    package['devDependencies']['@ohos/hvigor-ohos-plugin'] = ENGINE_VERSION
    (project / 'oh-package.json5').write_text(json.dumps(package, indent=2) + '\n')
    hvigor = json5_read(project / 'hvigor/hvigor-config.json5', tools, env)
    require(not hvigor.get('dependencies'), 'Hvigor dependencies must be empty to avoid installation')
    hvigor['dependencies'] = {}
    hvigor.setdefault('properties', {})['hvigor.dependency.useNpm'] = True
    hvigor.setdefault('execution', {}).update(daemon=False, incremental=False, parallel=False)
    (project / 'hvigor/hvigor-config.json5').write_text(json.dumps(hvigor, indent=2) + '\n')
    entry = json5_read(project / 'entry/build-profile.json5', tools, env)
    def remove_native(value):
        if isinstance(value, dict):
            value.pop('externalNativeOptions', None)
            if 'nativeLib' in value:
                value['nativeLib'].setdefault('debugSymbol', {})['strip'] = False
            for child in value.values():
                remove_native(child)
        elif isinstance(value, list):
            for child in value:
                remove_native(child)
    remove_native(entry)
    entry.setdefault('buildOption', {}).setdefault('nativeLib', {}).setdefault('debugSymbol', {})['strip'] = False
    (project / 'entry/build-profile.json5').write_text(json.dumps(entry, indent=2) + '\n')
    (project / 'local.properties').write_text('sdk.dir=' + str(view).replace('\\', '\\\\') + '\n')
    # Bundled packages only. The official wrapper also links these into its cache.
    links = project / 'node_modules/@ohos'
    links.mkdir(parents=True, mode=0o700)
    for name in ('hvigor', 'hvigor-ohos-plugin'):
        (links / name).symlink_to(tools / 'hvigor' / name, target_is_directory=True)
    entry_package = json5_read(project / 'entry/oh-package.json5', tools, env)
    for field in ('dependencies', 'devDependencies'):
        for name, location in entry_package.get(field, {}).items():
            require(isinstance(location, str) and location.startswith('file:'), 'Entry has unprepared external dependencies')
            source = (project / 'entry' / location[5:]).resolve(strict=True)
            require(source.is_relative_to(project) and source.is_dir(), 'Entry file dependency escapes private project')
            require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'Invalid local dependency name')
            destination = project / 'entry/oh_modules' / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.symlink_to(source, target_is_directory=True)
    return {'name': 'default', 'compileSdkVersion': '26.0.0', 'targetSdkVersion': '26.0.0',
            'compatibleSdkVersion': minimum_api, 'runtimeOS': 'OpenHarmony', 'existing_signing_identity': True}


def run_sanitized(command, cwd, env, redactor, log, suppress_output=False):
    # Sensitive values are never part of this builder's argv; tool output is filtered first.
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors='replace', bufsize=1, start_new_session=True)
    try:
        for line in process.stdout:
            if suppress_output:
                continue  # A verifier may print an embedded profile; keep it private.
            safe = redactor(line)
            log.write(safe)
            log.flush()
            sys.stdout.write(safe)
            sys.stdout.flush()
        return process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise
    finally:
        process.stdout.close()


def check_hap(path, records, manifest):
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        require(len(members) == len(set(members)), 'Signed HAP contains duplicate ZIP members')
        actual_libs = {name for name in members if name.startswith('libs/') and not name.endswith('/')}
        require(actual_libs == set(records), 'Packaged native library set differs from staged inputs')
        for member, expected in records.items():
            with archive.open(member) as stream:
                require(sha_stream(stream) == expected, 'Packaged native bytes differ from staged signed ELF')
        name = 'resources/rawfile/runtime-manifest.json'
        require(name in members, 'Packaged runtime manifest is absent')
        with archive.open(name) as stream:
            require(sha_stream(stream) == manifest, 'Packaged runtime manifest differs from staged input')
    return {'all_native_bytes_equal': True, 'libraries': len(records),
            'core_codesign_preserved_by_full_byte_parity': True,
            'host_bytes_preserved': True, 'runtime_manifest_bytes_equal': True}


def build(args, tools, view, env, tool_receipt):
    require(args.project and args.output and args.log, 'Build requires --project, --output and --log')
    project = Path(args.project).resolve(strict=True)
    output = Path(args.output).absolute()
    log_path = Path(args.log).absolute()
    receipt = Path(str(output) + '.receipt.json')
    for path in (output, log_path, receipt):
        require(not os.path.lexists(path), 'Output, log or receipt already exists; overwriting is forbidden')
        require(path.parent.is_dir(), 'Output/log/receipt parent directory must already exist')
        require(not path.resolve().is_relative_to(project), 'Outputs must be outside the source project')
    require(len({str(p.resolve()) for p in (output, log_path, receipt)}) == 3, 'Output paths must be distinct')
    tmp_base = Path(os.environ.get('TMPDIR', '')).resolve(strict=True)
    require(tmp_base.is_relative_to(Path('/data/storage/el2/base/haps/entry/temp')),
            'TMPDIR must be the real private temporary filesystem')
    original_profile_record = file_record(project / 'build-profile.json5')
    root_profile = json5_read(project / 'build-profile.json5', tools, env)
    redactor = Redactor(root_profile, {k: v for k, v in env.items() if SENSITIVE_KEY.search(k)})
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    t0 = time.monotonic()
    records, codesign, manifest, host_origin = native_inputs(
        project, Path(args.baseline).resolve(strict=True), args.rebuilt_host_receipt)
    app = json5_read(project / 'AppScope/app.json5', tools, env).get('app', {})
    baseline_version = None
    with zipfile.ZipFile(args.baseline) as archive:
        packed = json.loads(archive.read('module.json'))
        baseline_version = packed.get('app', {}).get('versionCode')
    require(isinstance(app.get('versionCode'), int) and isinstance(baseline_version, int)
            and app['versionCode'] > baseline_version, 'MAIN must bump AppScope versionCode above host6 before packaging')
    inventory = source_inventory(project)
    status = {'schema': 1, 'status': 'failed', 'started_utc': started, 'toolchain': tool_receipt,
              'source_project': str(project), 'source': inventory,
              'builder': file_record(Path(__file__).resolve()),
              'native_rebuild': False, 'native_strip': False,
              'native_inputs': records, 'core_codesign': codesign, 'host_origin': host_origin,
              'runtime_manifest': manifest, 'app_version': {key: app.get(key) for key in ('versionCode', 'versionName')},
              'prior_host6_sha256': BASELINE_SHA256,
              'scope': 'Packaging/parity only; native runtime/device E2E delivery gate remains pending'}
    with log_path.open('x', encoding='utf-8') as log:
        # TemporaryDirectory owns this exact generated path and cleans it on every exit.
        with tempfile.TemporaryDirectory(prefix='blender-resize-hap-', dir=tmp_base) as owned:
            owner = Path(owned).resolve(strict=True)
            require(owner.parent == tmp_base and owner.name.startswith('blender-resize-hap-'), 'Temporary ownership check failed')
            temp_project = owner / 'project'
            private_original = owner / 'signing-original.json5'
            shutil.copyfile(project / 'build-profile.json5', private_original)
            os.chmod(private_original, 0o600)
            try:
                copy_project(project, temp_project)
                require(source_inventory(temp_project) == inventory, 'Private project copy differs from source')
                projection = configure_private(temp_project, project, tools, view, env, root_profile, redactor)
                status['product'] = projection
                for member, expected in records.items():
                    require(file_record(temp_project / 'entry' / member) == expected, 'Private native copy differs from source')
                args_build = ['sh', str(tools / 'hvigor/bin/hvigorw'), '--mode', 'module', '-p', 'module=entry@default',
                              '-p', 'product=default', '-p', 'buildMode=debug', 'assembleHap', '--no-daemon', '--stacktrace']
                code = run_sanitized(args_build, temp_project, env, redactor, log)
                status['hvigor_exit_code'] = code
                require(code == 0, 'Official Hvigor packaging failed; inspect sanitized log')
                candidates = list((temp_project / 'entry/build/default/outputs/default').glob('*-signed.hap'))
                require(len(candidates) == 1 and candidates[0].is_file(), 'Expected exactly one signed default HAP')
                hap = candidates[0]
                status['parity'] = check_hap(hap, records, manifest)
                # Official verifier receives no passwords, aliases or profile arguments.
                verify = [str(tools / '.oo-launchers/hap-sign-tool'), 'verify-app', '-inFile', str(hap),
                          '-outCertChain', str(owner / 'verified-cert-chain.cer'),
                          '-outProfile', str(owner / 'verified-profile.p7b')]
                verify_code = run_sanitized(verify, owner, env, redactor, log, suppress_output=True)
                log.write(f'Official HAP verifier exit code: {verify_code}\n')
                log.flush()
                status['hap_verify_exit_code'] = verify_code
                require(verify_code == 0, 'Official signed HAP verification failed')
                require(source_inventory(project) == inventory
                        and file_record(project / 'build-profile.json5') == original_profile_record,
                        'Source project changed during packaging')
                # Create exclusively only after signing, full byte parity and source stability pass.
                built_record = file_record(hap)
                with hap.open('rb') as src, output.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                output_record = file_record(output)
                require(output_record == built_record, 'Copied output differs from verified signed HAP')
                status['output'] = {'path': str(output), **output_record}
                status['status'] = 'success'
            except (BuildError, OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
                status['error'] = redactor(str(error)) if isinstance(error, BuildError) else 'Packaging failed; private diagnostics suppressed'
            finally:
                status['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                status['elapsed_seconds'] = time.monotonic() - t0
        log.flush()
    status['sanitized_log'] = {'path': str(log_path), **file_record(log_path)}
    exclusive_json(receipt, status)
    print(json.dumps({'status': status['status'], 'receipt': str(receipt), 'output': status.get('output'),
                      'error': status.get('error')}, indent=2))
    return 0 if status['status'] == 'success' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-sdk-view', action='store_true')
    mode.add_argument('--validate-tools', action='store_true')
    parser.add_argument('--project')
    parser.add_argument('--output')
    parser.add_argument('--log')
    parser.add_argument('--rebuilt-host-receipt', type=Path,
                        help='Explicit successful native build/sign/source receipt for a rebuilt host')
    parser.add_argument('--tools', type=Path, default=DEFAULT_TOOLS)
    parser.add_argument('--sdk-view', type=Path, default=DEFAULT_VIEW)
    parser.add_argument('--hvigor-user-home', type=Path, default=DEFAULT_HVIGOR_HOME)
    parser.add_argument('--baseline', type=Path, default=DEFAULT_BASELINE,
                        help='Read-only trusted host6 HAP; pinned SHA256 is always enforced')
    args = parser.parse_args()
    try:
        tools = args.tools.resolve(strict=True)
        view = args.sdk_view.absolute()
        env = build_env(tools, view, args.hvigor_user_home.absolute())
        if args.prepare_sdk_view:
            print(json.dumps({'sdk_view': str(view), 'sdk': prepare_sdk_view(tools, view)}, indent=2))
            return 0
        tool_receipt = validate_tools(tools, view, env)
        if args.validate_tools:
            print(json.dumps(tool_receipt, indent=2))
            return 0
        return build(args, tools, view, env, tool_receipt)
    except BuildError as error:
        print('resize HAP builder: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile, struct.error):
        print('resize HAP builder: preflight failed; private diagnostics suppressed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
