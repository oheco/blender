# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent portable compiler-replay IO, ownership and command receipts."""
import hashlib,json,os,subprocess
from pathlib import Path,PurePosixPath
from env_policy import clean
HERE=Path(__file__).resolve().parents[1];REPO=HERE.parents[2]
TOOLS=['cc','cxx','lld','ar','ranlib','readelf','nm','signer','python','cmake','ninja','git','ctest']

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def row(p,root=None):
    p=Path(p);return {'path':p.relative_to(root).as_posix() if root else str(p),'size':p.stat().st_size,'sha256':sha(p)}

def write_json(p,data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')

def relative(name):
    if not isinstance(name,str) or name.startswith('/') or '\0' in name or any(t in ('','.','..') for t in name.split('/')):raise ValueError('Noncanonical path '+repr(name))
    p=PurePosixPath(name)
    if p.as_posix()!=name:raise ValueError('Ambiguous path')
    return Path(*p.parts)

def repo_file(name):
    p=REPO/relative(name)
    if not p.is_file() or p.is_symlink() or REPO not in p.resolve().parents:raise ValueError('Unsafe/missing fixed repo input')
    return p

def intersects(a,b):
    a=Path(a).resolve();b=Path(b).resolve();return a==b or a in b.parents or b in a.parents

def paths(a):
    cache=Path(os.environ['XDG_CACHE_HOME']).resolve();tmp=Path(os.environ['TMPDIR']).resolve()
    if a.tmp_dir.resolve()!=tmp:raise ValueError('Exact managed TMPDIR required')
    if cache not in a.root.resolve().parents or not a.root.resolve().relative_to(cache).parts[0].startswith('blender-ohos-compiler-replay-'):raise ValueError('Dedicated NEW private compiler-replay root required')
    for p in [REPO,tmp,a.sdk_root,a.resource_dir,*[getattr(a,k) for k in TOOLS]]:
        if intersects(a.root,p):raise ValueError('Output overlaps provided/foreign input')
    if any(p.is_symlink() for p in [a.root,*a.root.parents]):raise ValueError('Owned output path contains symlink')
    if a.lld.name!='ld.lld':raise ValueError('Preserve ld.lld invocation basename')
    if (os.uname().sysname,os.uname().machine)!=('HarmonyOS','aarch64'):raise ValueError('Actual native HarmonyOS/aarch64 required')
    for role in TOOLS:
        p=getattr(a,role)
        if not p.is_file() or not os.access(p,os.X_OK):raise ValueError('Missing explicit executable '+role)
    if a.jobs not in (1,2):raise ValueError('Conservative compile jobs1 or2, link1')
    clean(tmp_dir=a.tmp_dir)

class Runner:
    def __init__(self,a):self.a=a;self.env,self.cleared=clean(tmp_dir=a.tmp_dir);self.env['CMAKE_BUILD_PARALLEL_LEVEL']=str(a.jobs)
    def run(self,argv,label,input=None,expected=0,timeout=None):
        argv=list(map(str,argv));log=self.a.root/'logs'/(label+'.log');log.parent.mkdir(parents=True,exist_ok=True)
        record={'argv':argv,'status':'running','cwd':str(self.a.root),'cleared_ambient_names':self.cleared};receipt=log.with_suffix('.json');write_json(receipt,record)
        with log.open('w') as stream:
            proc=subprocess.run(argv,cwd=self.a.root,env=self.env,input=input,text=True,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        record.update(status='completed',exit=proc.returncode,log_sha256=sha(log));write_json(receipt,record)
        if proc.returncode!=expected:raise ValueError('Command failed '+label+' inspect '+str(log))
        return log.read_text()

def verify_inputs():
    lock=json.loads((HERE/'inputs.lock.json').read_text())
    for item in lock['sealed_files']:
        p=repo_file(item['path'])
        if sha(p)!=item['sha256'] or p.stat().st_size!=item['size']:raise ValueError('Fixed recipe/source input drift '+item['path'])
    return {'entry':'build_files/ohos/compiler_replay/builder.py','inputs_lock_sha256':sha(HERE/'inputs.lock.json')}
