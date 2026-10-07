# SPDX-License-Identifier: GPL-2.0-or-later
"""Repository input verification and private, owned output paths."""
import sys
sys.dont_write_bytecode = True
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
BASE = HERE.parent


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def repo_file(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected repository-relative input')
    file = REPO / path
    if file.is_symlink() or not file.is_file() or REPO not in file.resolve().parents:
        raise ValueError('Missing/unsafe repository input: ' + name)
    return file


def vendor():
    spec = importlib.util.spec_from_file_location('core_vendor', REPO / 'build_files/ohos/vendor_archive.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_path(path, temporary=False):
    value = Path(path).absolute()
    roots = [Path(os.environ[k]).resolve() for k in (['TMPDIR'] if temporary else ['XDG_CACHE_HOME','TMPDIR'])]
    if value.is_symlink() or not any(r in value.resolve().parents or (temporary and r == value.resolve()) for r in roots):
        raise ValueError('Output must be beneath declared private ' + ('TMPDIR' if temporary else 'cache/TMPDIR'))
    for part in [value] + list(value.parents):
        if part.is_symlink():
            raise ValueError('Private output path contains a symlink')
    return value


def check_case_sensitive(temporary):
    temporary.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='core-filesystem-probe-', dir=temporary) as td:
        a = Path(td) / 'nativeCaseA'
        b = Path(td) / 'nativeCasea'
        a.write_bytes(b'A')
        b.write_bytes(b'a')
        if a.read_bytes() != b'A' or b.read_bytes() != b'a':
            raise ValueError('Case-sensitive private source storage required')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def verify_inputs():
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    for entry in lock['sealed_files']:
        file = repo_file(entry['path'])
        if sha(file) != entry['sha256'] or file.stat().st_size != entry['size']:
            raise ValueError('Sealed input changed: ' + entry['path'])
    provenance = json.loads((BASE / 'provenance.json').read_text())
    vend = vendor()
    archives = []
    for dep in provenance['sources'] + [lock['tbb_source']]:
        manifest_file = repo_file(dep['registry_manifest'])
        manifest = vend.verify(manifest_file.parent)
        if manifest['sha256'] != dep['sha256'] or manifest['size'] != dep['size']:
            raise ValueError('Fixed original archive changed')
        for notice in dep.get('notices', []):
            file = repo_file(notice['mirror'])
            if sha(file) != notice['sha256']:
                raise ValueError('Original source notice changed')
        archives.append({'name':dep['name'],'sha256':manifest['sha256'],'parts':len(manifest['parts'])})
    return {'result':'PASS','scope':'Repository fixed source bytes, original notices and sealed builder inputs only',
            'archives':archives,'sealed_files':len(lock['sealed_files']),'new_builder_accepted':False}


class Runner:
    def __init__(self, root, tmp_dir):
        self.root = root
        self.tmp = tmp_dir
        self.env = os.environ.copy()
        for key in ['CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS','CC','CXX','CMAKE_PREFIX_PATH',
                    'CMAKE_TOOLCHAIN_FILE','PKG_CONFIG_PATH','LD_LIBRARY_PATH','LD_PRELOAD']:
            self.env.pop(key,None)
        self.env['TMPDIR'] = str(tmp_dir)
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.logs = root / 'logs'
        self.logs.mkdir(parents=True, exist_ok=True)

    def run(self, command, label, cwd=None, expect=0, input_text=None):
        argv = [str(x) for x in command]
        result = subprocess.run(argv, cwd=cwd or self.root, env=self.env, input=input_text,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with (self.logs / (label + '.log')).open('a') as stream:
            stream.write(json.dumps({'command':argv,'cwd':str(cwd or self.root),'expected_exit':expect}) + '\n')
            stream.write(result.stdout + '\nexit=' + str(result.returncode) + '\n')
        if result.returncode != expect:
            raise RuntimeError(label + ' failed; inspect ' + str(self.logs / (label + '.log')))
        return result.stdout
