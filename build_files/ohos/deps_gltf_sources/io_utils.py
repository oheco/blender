# SPDX-License-Identifier: GPL-2.0-or-later
"""Private immutable ownership and actual command receipts; adapted from volume builder."""
import sys
sys.dont_write_bytecode = True
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import stat
import uuid
from source_guard import HERE, REPO, sha, repo_file, verify_inputs


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def private_path(path, temporary=False):
    p = Path(path).absolute()
    if '..' in p.parts:
        raise ValueError('Parent traversal in private output path')
    root = Path(os.environ['TMPDIR' if temporary else 'XDG_CACHE_HOME']).resolve()
    if not p.resolve().is_relative_to(root) or (p.resolve() == root and not temporary):
        raise ValueError('Output must be within declared private cache/TMPDIR')
    if any(v.is_symlink() for v in [p, *p.parents]) or any(c in str(p) for c in '\n\r;"$'):
        raise ValueError('Unsafe private output path')
    return p.resolve()


def ownership(args):
    if not args.prefix.is_relative_to(args.root) or args.prefix == args.root or \
       args.prefix.relative_to(args.root).parts[0] in ['sources','build','toolchain','logs','blender-bridge-source','metadata-original','receipt-history']:
        raise ValueError('Prefix must be inside owned root outside reserved builder paths')
    marker = args.root / '.gltf-builder-owned.json'
    expected = {'schema_version': 1, 'input_lock_sha256': sha(HERE/'inputs.lock.json'),
                'prefix': str(args.prefix), 'tmp_dir': str(args.tmp_dir)}
    if args.root.exists():
        if not args.resume or not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse existing/unowned/mismatched root; exact owned --resume required')
        if args.prefix.exists():
            from metadata import prefix_inventory
            prefix_inventory(args.prefix)
        for child in args.root.rglob('*'):
            mode=child.lstat().st_mode
            if stat.S_ISLNK(mode):
                if child.is_relative_to(args.prefix):continue
                link=os.readlink(child)
                permitted=child.parent==args.root/'build/draco' and child.name in ['draco_encoder','draco_decoder'] and \
                          link==child.name+'-1.5.7' and (child.parent/link).is_file() and not (child.parent/link).is_symlink()
                if not permitted:raise ValueError('Unexpected symlink in owned builder outputs')
            elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError('Special file in owned builder outputs')
    else:
        if args.resume or args.prefix.exists():
            raise ValueError('New root/prefix must be absent')
        args.root.mkdir(parents=True)
        if args.prefix.exists():
            raise ValueError('Install prefix appeared after new root creation')
        write_json(marker, expected)
    if not args.prefix.is_relative_to(args.root) or args.prefix == args.root:
        raise ValueError('Install prefix must be strictly inside owned root')
    if args.root.stat().st_dev != args.tmp_dir.stat().st_dev:
        raise ValueError('Private cache and TMPDIR need same filesystem')
    with tempfile.TemporaryDirectory(prefix='gltf-case-', dir=args.tmp_dir) as td:
        a,b=Path(td)/'CaseA',Path(td)/'Casea'
        a.write_bytes(b'A'); b.write_bytes(b'a')
        if a.read_bytes() != b'A' or b.read_bytes() != b'a':
            raise ValueError('Case-sensitive private filesystem required')
    if (args.root/'.gltf-builder-lock').is_symlink():
        raise ValueError('Owned root lock cannot be a symlink')
    lock=(args.root/'.gltf-builder-lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    return lock


class Runner:
    def __init__(self, root, tmp):
        self.root,self.tmp=root,tmp
        self.env=os.environ.copy()
        for k in ['CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS','CC','CXX','CMAKE_PREFIX_PATH','CMAKE_TOOLCHAIN_FILE',
                  'PKG_CONFIG_PATH','PKG_CONFIG_LIBDIR','LD_LIBRARY_PATH','LD_PRELOAD','CPATH','C_INCLUDE_PATH',
                  'CPLUS_INCLUDE_PATH','LIBRARY_PATH','PYTHONPATH','PYTHONHOME']:
            self.env.pop(k,None)
        self.env.update(TMPDIR=str(tmp),PYTHONDONTWRITEBYTECODE='1',PKG_CONFIG_PATH='')
        self.logs=root/'logs'/uuid.uuid4().hex
        self.logs.mkdir(parents=True,exist_ok=False)
        self.serial=0

    def run(self, command, label, cwd=None, input_text=None, expect=0, timeout=180, extra_env=None):
        self.serial+=1
        argv=list(map(str,command))
        record={'command':argv,'cwd':str(cwd or self.root),'expected_exit':expect}
        try:
            environment=self.env.copy();environment.update(extra_env or {})
            record['explicit_environment']=extra_env or {}
            p=subprocess.run(argv,cwd=cwd or self.root,env=environment,text=True,input=input_text,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        except subprocess.TimeoutExpired as e:
            write_json(self.logs/(label+'-timeout.json'),dict(record,status='TIMEOUT',timeout_seconds=timeout))
            raise RuntimeError('Actual command timeout: '+label) from e
        log=self.logs/(str(self.serial)+'-'+label+'.log')
        log.write_text(json.dumps(record,ensure_ascii=False)+'\n'+p.stdout+'\nexit='+str(p.returncode)+'\n')
        write_json(log.with_suffix('.json'),dict(record,exit_code=p.returncode,log_sha256=sha(log)))
        if p.returncode!=expect:
            raise RuntimeError(label+' exit='+str(p.returncode)+'; inspect '+str(log))
        return p.stdout
