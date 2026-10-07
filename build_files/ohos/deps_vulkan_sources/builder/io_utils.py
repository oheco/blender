# SPDX-License-Identifier: GPL-2.0-or-later
"""Sealed repository inputs, private ownership and real command receipts.

Adapted from Blender OHOS deps_core_sources/builder/io_utils.py (GPL-2.0-or-later).
"""
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import vendor_archive

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def sha(path, algorithm='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def relative(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('Invalid repository/source path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')) or path.as_posix() != name:
        raise ValueError('Expected canonical relative path: ' + name)
    return Path(*path.parts)


def repo_file(name):
    file = REPO / relative(name)
    if not file.is_file() or file.is_symlink() or REPO not in file.resolve().parents:
        raise ValueError('Missing/unsafe repository input: ' + name)
    for parent in file.parents:
        if parent == REPO:
            break
        if parent.is_symlink():
            raise ValueError('Symlink repository input parent')
    return file


def private_path(path, temporary=False):
    value = Path(path).absolute()
    keys = ['TMPDIR'] if temporary else ['XDG_CACHE_HOME']
    roots = [Path(os.environ[k]).resolve() for k in keys]
    if not any(r in value.resolve().parents or (temporary and r == value.resolve()) for r in roots):
        raise ValueError('Output must be beneath private ' + '/'.join(keys))
    if any(p.is_symlink() for p in [value, *value.parents]):
        raise ValueError('Private path contains symlink')
    if any(c in str(value) for c in ('\n', '\r', ';', '"', '$')):
        raise ValueError('Unsupported control/CMake interpolation character in output path')
    return value


def check_case_sensitive(temporary):
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vulkan-filesystem-', dir=temporary) as td:
        a, b = Path(td) / 'CaseA', Path(td) / 'Casea'
        a.write_bytes(b'A')
        b.write_bytes(b'a')
        if a.read_bytes() != b'A' or b.read_bytes() != b'a':
            raise ValueError('Case-sensitive private filesystem required')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def sources():
    return json.loads((HERE / 'sources.lock.json').read_text())


def verify_inputs():
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    for row in lock['sealed_files']:
        file = repo_file(row['path'])
        if sha(file) != row['sha256'] or file.stat().st_size != row['size']:
            raise ValueError('Sealed input drift: ' + row['path'])
    archives = []
    for dep in sources()['sources']:
        manifest = vendor_archive.verify(repo_file(dep['registry_manifest']).parent)
        if (manifest['sha256'], manifest['size']) != (dep['sha256'], dep['size']):
            raise ValueError('Complete original archive drift')
        inventory = json.loads(repo_file(dep['inventory']).read_text())
        if inventory['source_archive_sha256'] != dep['sha256']:
            raise ValueError('Source inventory archive lineage mismatch')
        archives.append({'name': dep['name'], 'sha256': dep['sha256'], 'records': len(inventory['files'])})
    return {'result': 'PASS', 'sealed_files': len(lock['sealed_files']), 'complete_archives': archives,
            'scope': 'Frozen ordinary repository source inputs only', 'new_full_native_acceptance': False}


class Runner:
    def __init__(self, root, tmp_dir):
        self.root, self.tmp = root, tmp_dir
        self.env = os.environ.copy()
        for key in ['CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'CC', 'CXX', 'CMAKE_PREFIX_PATH',
                    'CMAKE_TOOLCHAIN_FILE', 'PKG_CONFIG_PATH', 'PKG_CONFIG_LIBDIR', 'PKG_CONFIG_SYSROOT_DIR',
                    'PKG_CONFIG_SYSTEM_INCLUDE_PATH', 'PKG_CONFIG_SYSTEM_LIBRARY_PATH', 'PKG_CONFIG_TOP_BUILD_DIR', 'LD_LIBRARY_PATH',
                    'LD_PRELOAD', 'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH', 'LIBRARY_PATH',
                    'VK_ICD_FILENAMES', 'VK_DRIVER_FILES', 'VK_ADD_DRIVER_FILES', 'VK_LAYER_PATH',
                    'VK_ADD_LAYER_PATH', 'VK_INSTANCE_LAYERS', 'VK_LAYER_SETTINGS_PATH',
                    'VK_LOADER_DRIVERS_SELECT', 'VK_LOADER_DRIVERS_DISABLE', 'VK_LOADER_LAYERS_ALLOW',
                    'VK_LOADER_LAYERS_DISABLE', 'VK_LOADER_LAYERS_ENABLE']:
            self.env.pop(key, None)
        for key in list(self.env):
            if key.startswith(('GIT_DIR', 'GIT_WORK_TREE', 'GIT_CONFIG', 'GIT_OBJECT', 'GIT_ALTERNATE', 'GIT_COMMON_DIR')):
                self.env.pop(key, None)
        self.env.update(TMPDIR=str(tmp_dir), PYTHONDONTWRITEBYTECODE='1', PKG_CONFIG_PATH='',
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        SOURCE_DATE_EPOCH='1767225600', TZ='UTC')
        import uuid
        self.logs = root / 'logs' / uuid.uuid4().hex
        self.logs.mkdir(parents=True, exist_ok=False)
        self.serial = 0

    def run(self, command, label, cwd=None, expect=0, input_text=None, timeout=None):
        self.serial += 1
        argv = list(map(str, command))
        record = {'command': argv, 'cwd': str(cwd or self.root), 'expected_exit': expect, 'timeout_seconds': timeout}
        process = subprocess.Popen(argv, cwd=cwd or self.root, env=self.env, text=True,
                                   stdin=subprocess.PIPE if input_text is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        timed_out = False
        try:
            output, _ = process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            output, _ = process.communicate()
        log = self.logs / (label + '-' + str(self.serial) + '.log')
        log.write_text(json.dumps(record, ensure_ascii=False) + '\n' + output + '\nexit=' + str(process.returncode) + '\n')
        record.update(exit_code=process.returncode, timed_out=timed_out, log=str(log), log_sha256=sha(log))
        write_json(log.with_suffix('.json'), record)
        if timed_out or process.returncode != expect:
            raise RuntimeError(label + ' exit=' + str(process.returncode) + ' timeout=' + str(timed_out) + '; inspect ' + str(log))
        return output
