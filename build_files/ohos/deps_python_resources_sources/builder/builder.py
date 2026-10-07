#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sealed orchestration of the existing eight-source pure-resource public API."""
import sys
sys.dont_write_bytecode = True
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

NAMESPACE = 'build_files/ohos/deps_python_resources_sources'
ENTRY = NAMESPACE + '/builder/builder.py'
LOCK = NAMESPACE + '/builder/inputs.lock.json'
SOURCE_LOCK = NAMESPACE + '/sources.lock.json'
SOURCE_SHA = '27aeac5e6fd312c672d09d31c6d937d3fa0bb9b77080017617ff75d4e444536d'
OWNER = '.pure-builder-owned.json'
DEPENDENT = {stage:['prepared','assembled','audit','full','terminal']
             for stage in ('prepare','assemble','full','audit')}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def real(path):
    value = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in [value, *value.parents]):
        raise ValueError('Symbolic link in caller/input path')
    return value


def relative(name):
    value = PurePosixPath(name)
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or value.is_absolute() or any(p in ('','.','..') for p in name.split('/')):
        raise ValueError('Noncanonical repository path')
    return value


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False)
        stream.write('\n')


def load(path):
    spec = importlib.util.spec_from_file_location('sealed_pure_resource_api',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_inputs(repo):
    repo = real(repo)
    lock = real(repo / LOCK)
    data = json.loads(lock.read_text())
    rows = data['sealed_files']
    if data.get('schema_version') != 1 or data.get('kind') != 'offline-pure-resource-orchestration-inputs' or data['source_lock']['sha256'] != SOURCE_SHA:
        raise ValueError('Wrong sealed recipe/source lock')
    seen, folded = set(),set()
    for row in rows:
        name = relative(row['path']).as_posix()
        if name in seen or name.casefold() in folded:
            raise ValueError('Duplicate/case-colliding seal path')
        seen.add(name);folded.add(name.casefold())
        path = real(repo / name)
        if not path.is_file() or path.stat().st_size != row['size'] or sha(path) != row['sha256']:
            raise ValueError('Frozen recipe/input bytes changed: '+name)
    if ENTRY not in seen or SOURCE_LOCK not in seen or sha(repo/SOURCE_LOCK) != SOURCE_SHA:
        raise ValueError('Entry or original source lock not frozen')
    for parent in [repo/NAMESPACE, repo/NAMESPACE/'builder']:
        for path in parent.glob('*.py'):
            if path.relative_to(repo).as_posix() not in seen:
                raise ValueError('Unsealed recipe helper')
    api = load(repo/NAMESPACE/'resources.py')
    registry = api.verify_registry(repo)
    selected = json.loads((repo/SOURCE_LOCK).read_text())['inputs']
    inventories = [{'name':p['name'],'version':p['version'],'files':p['source_file_count'],
                    'tree_sha256':p['source_tree_sha256'],'inventory':p['source_inventory']} for p in selected]
    return {'result':'PASS frozen recipe and all eight complete original inputs',
            'input_lock_sha256':sha(lock),'source_lock_sha256':SOURCE_SHA,
            'sealed_files':len(rows),'registry':registry,'source_inventories':inventories},api


def contained(a,b):
    return a == b or a in b.parents or b in a.parents


def private(path, temporary=False):
    value = real(path)
    allowed = real(os.environ['TMPDIR'] if temporary else os.environ['XDG_CACHE_HOME'])
    extra = real(os.environ['TMPDIR'])
    if value == allowed or (allowed not in value.parents and (temporary or extra not in value.parents)):
        raise ValueError('Explicit output must be below private cache/TMPDIR')
    ancestor = next(p for p in [value,*value.parents] if p.exists())
    if ancestor.stat().st_dev != extra.stat().st_dev:
        raise ValueError('Output and TMPDIR must share a private filesystem')
    return value


def paths(args):
    args.repo = real(args.repo)
    args.root,args.cache,args.resources = [private(p) for p in (args.root,args.cache,args.resources)]
    args.tmp = real(args.tmp)
    envtmp = real(os.environ['TMPDIR'])
    if not args.tmp.is_dir() or not (args.tmp == envtmp or envtmp in args.tmp.parents):
        raise ValueError('Explicit tmp must exist at/below TMPDIR')
    outputs = [args.root,args.cache,args.resources]
    if any(contained(a,b) for i,a in enumerate(outputs) for b in outputs[i+1:]) or any(contained(p,args.repo) or contained(p,args.tmp) for p in outputs):
        raise ValueError('State/cache/resources/repository/tmp must not overlap')


@contextmanager
def own(args):
    paths(args)
    fingerprint = sha(real(args.repo/LOCK))
    expected = {'schema_version':1,'owner':ENTRY,'input_lock_sha256':fingerprint,
                'source_lock_sha256':SOURCE_SHA,'cache':str(args.cache),'resources':str(args.resources)}
    if args.root.exists():
        marker = real(args.root/OWNER)
        if not args.resume or not args.root.is_dir() or not marker.is_file() or json.loads(marker.read_text()) != expected:
            raise ValueError('Refuse unowned/differently sealed state; exact resume required')
        if {p.name for p in args.root.iterdir()} - {OWNER,'.pure-builder.lock','attempts','current'}:
            raise ValueError('Unexpected state entries')
    else:
        if args.cache.exists() or args.resources.exists():
            raise ValueError('New state cannot adopt old/accepted source or resource outputs')
        args.root.mkdir(parents=True,exist_ok=False)
        put(args.root/OWNER,expected)
    lockfile = real(args.root/'.pure-builder.lock')
    with lockfile.open('a+') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fingerprint
        fcntl.flock(guard,fcntl.LOCK_UN)


def attempt(args):
    directory = real(args.root/'attempts')
    directory.mkdir(exist_ok=True)
    numbers = [int(p.name.split('-',1)[0]) for p in directory.iterdir()]
    item = directory/(str(max(numbers,default=0)+1).zfill(6)+'-'+args.stage)
    item.mkdir()
    current = real(args.root/'current')
    current.mkdir(exist_ok=True)
    for name in DEPENDENT[args.stage]:
        pointer = real(current/(name+'.json'))
        if pointer.exists():
            pointer.rename(item/('previous-'+name+'.json'))
    put(item/'request.json',{'stage':args.stage,'input_lock_sha256':sha(args.repo/LOCK),
                            'source_lock_sha256':SOURCE_SHA,'terminal_check_requested':args.terminal_python is not None})
    return item


def pointer(args,name,item,report):
    put(args.root/'current'/(name+'.json'),{'attempt':item.relative_to(args.root).as_posix(),
        'receipt_sha256':sha(item/'result.json'),'input_lock_sha256':report['input_lock_sha256'],
        'source_lock_sha256':SOURCE_SHA,'site_tree_sha256':report.get('resources',{}).get('site_tree_sha256'),
        'terminal_native_checked':report['terminal_native_checked'],'application_native_acceptance':'NOT_RUN'})


def terminal(args,api,item,resource):
    executable = Path(os.path.abspath(args.terminal_python))
    if not executable.is_file():raise ValueError('Explicit terminal interpreter missing')
    before = sha(executable)
    command = [str(executable),'-I','-B','-S',str(args.repo/NAMESPACE/'check_native.py'),
               '--repo',str(args.repo),'--root',str(args.resources),'--tmp',str(args.tmp)]
    env = dict(os.environ,TMPDIR=str(args.tmp),PYTHONDONTWRITEBYTECODE='1')
    for key in ['LD_LIBRARY_PATH','LD_PRELOAD','PYTHONPATH','PYTHONHOME']:env.pop(key,None)
    try:
        result = subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    except subprocess.TimeoutExpired as error:
        (item/'terminal.stdout.log').write_bytes(error.stdout or b'')
        (item/'terminal.stderr.log').write_bytes(error.stderr or b'')
        raise RuntimeError('Terminal check timed out; no native PASS') from error
    (item/'terminal.stdout.log').write_bytes(result.stdout)
    (item/'terminal.stderr.log').write_bytes(result.stderr)
    put(item/'terminal-command.json',{'argv':command,'actual_exit':result.returncode,'interpreter_sha256':before})
    if result.returncode or sha(executable)!=before:raise RuntimeError('Actual terminal check/interpreter integrity failed')
    receipt = json.loads(result.stdout)
    if receipt['status']!='PASS' or receipt['interpreter_version']!='3.13.13' or receipt['lock_sha256']!=SOURCE_SHA or receipt['site_tree_sha256']!=resource['site_tree_sha256']:
        raise ValueError('Terminal receipt does not bind the selected pure payload')
    return receipt


def copy_inputs(args):
    proof,_ = verify_inputs(args.repo)
    destination = private(args.destination,temporary=True)
    if contained(destination,real(args.repo)) or destination.exists():raise ValueError('Copy requires absent independent TMPDIR mini repository')
    rows = json.loads((args.repo/LOCK).read_text())['sealed_files']
    destination.mkdir(parents=True,exist_ok=False)
    try:
        for row in [*rows,{'path':LOCK}]:
            target = destination/relative(row['path'])
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(args.repo/row['path'],target)
        copied,_ = verify_inputs(destination)
        if copied!=proof:raise ValueError('Copied full recipe/input proof differs')
    except BaseException:
        shutil.rmtree(destination);raise
    return {'result':'PASS complete sealed mini inputs copied','destination':str(destination),'proof':copied}


def execute(args):
    args.repo = real(args.repo)
    if args.stage=='verify-inputs':return verify_inputs(args.repo)[0]
    if args.stage=='copy-inputs':return copy_inputs(args)
    if args.terminal_python is not None and args.stage not in ('full','audit'):
        raise ValueError('Explicit terminal check belongs only to full/audit')
    with own(args) as fingerprint:
        item = attempt(args)
        report = {'stage':args.stage,'status':'FAIL','input_lock_sha256':fingerprint,
                  'source_lock_sha256':SOURCE_SHA,'terminal_native_checked':False,
                  'native_payload_assembled':False,'application_native_acceptance':'NOT_RUN',
                  'bpy_acceptance':'NOT_RUN','HAP_acceptance':'NOT_RUN','network_requests':0,'native_builds':0}
        try:
            proof,api = verify_inputs(args.repo)
            report['inputs'] = proof
            if args.stage in ('prepare','full'):
                report['sources'] = api.prepare(args.repo,args.cache,args.tmp)
            else:
                report['sources'] = api.verify_cache(args.repo,args.cache)
            if args.stage in ('assemble','full'):
                report['resources'] = api.assemble(args.repo,args.cache,args.resources,args.tmp)
            elif args.stage=='audit':
                report['resources'] = api.verify_resources(args.repo,args.resources)
            if 'resources' in report:
                report['resource_manifest'] = {'relative_to_resources':'resources.json',
                    'sha256':sha(args.resources/'resources.json'),'size':(args.resources/'resources.json').stat().st_size}
            if args.terminal_python is not None:
                report['terminal'] = terminal(args,api,item,report['resources'])
                report['terminal_native_checked'] = True
            report['status']='PASS'
            report['scope']='Complete source/data resource proof; terminal gate only if explicitly executed'
        except BaseException as error:
            report['error']=repr(error)
            raise
        finally:
            put(item/'result.json',report)
        names = {'prepare':['prepared'],'assemble':['assembled'],'audit':['audit'],
                 'full':['prepared','assembled','audit','full']}[args.stage]
        for name in names:pointer(args,name,item,report)
        if report['terminal_native_checked']:pointer(args,'terminal',item,report)
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['verify-inputs','copy-inputs','prepare','assemble','full','audit'])
    parser.add_argument('--repo',type=Path,required=True)
    for name in ['root','cache','resources','tmp','destination','terminal-python']:
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.stage=='copy-inputs' and args.destination is None:parser.error('copy-inputs requires --destination')
    if args.stage not in ('verify-inputs','copy-inputs') and any(getattr(args,k) is None for k in ['root','cache','resources','tmp']):
        parser.error('explicit --root/--cache/--resources/--tmp required')
    print(json.dumps(execute(args),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
