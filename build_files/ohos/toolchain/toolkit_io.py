# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent path/input/receipt primitives for provided toolkit preparation."""
import hashlib,json,os,subprocess
from pathlib import Path
from env_policy import clean
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def json_write(p,data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')

def file_row(p,relative=None):
    p=Path(p);row={'path':p.relative_to(relative).as_posix() if relative else str(p),'size':p.stat().st_size,'sha256':sha(p)}
    if p.is_symlink():row['link_target']=os.readlink(p)
    return row

def inventory(root):
    root=Path(root);rows=[]
    for p in sorted(root.rglob('*')):
        if p.is_file():rows.append(file_row(p,root))
        elif p.is_symlink():raise ValueError('Dangling/unhandled provided header symlink')
    if not rows:raise ValueError('Empty provided header inventory')
    return {'root':str(root),'records':rows,'inventory_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}

def overlap(a,b):
    a=Path(a).resolve();b=Path(b).resolve();return a==b or a in b.parents or b in a.parents

def validate_paths(a):
    cache=Path(os.environ['XDG_CACHE_HOME']).resolve();tmp=Path(os.environ['TMPDIR']).resolve()
    if a.tmp_dir.resolve()!=tmp:raise ValueError('Use explicit current managed TMPDIR')
    if a.root.resolve()==cache or cache not in a.root.resolve().parents:raise ValueError('Toolkit root must be a child of private XDG cache')
    if not a.root.resolve().relative_to(cache).parts[0].startswith('blender-ohos-toolkit-'):raise ValueError('Use a dedicated NEW blender-ohos-toolkit-* operation root')
    if overlap(a.root,REPO) or overlap(a.root,a.tmp_dir):raise ValueError('Root overlaps source or TMPDIR')
    for p in [a.sdk_root,a.resource_dir,*[getattr(a,k) for k in TOOL_ROLES]]:
        if overlap(a.root,p):raise ValueError('Owned root overlaps provided prerequisite')
    for name in ['blender-ohos-toolchain','blender-ohos-deps-base','blender-ohos-deps-volume','blender-ohos-deps-vulkan','blender-ohos-base-formal-fresh-1','blender-ohos-volume-formal-fresh-1','blender-ohos-vulkan-formal-fresh-1']:
        if overlap(a.root,cache/name):raise ValueError('Root overlaps existing/foreign operation root')
    for root in a.allow_output_root:
        if cache not in root.resolve().parents or not root.resolve().relative_to(cache).parts[0].startswith('blender-ohos-editor-'):raise ValueError('Explicit downstream outputs must belong to dedicated NEW editor root')
        for p in [a.root,REPO,a.sdk_root,a.resource_dir,*[getattr(a,k) for k in TOOL_ROLES]]:
            if overlap(root,p):raise ValueError('Allowed output root overlaps provided/owner inputs')
    if a.lld.name!='ld.lld':raise ValueError('Invoke genuine lld through ld.lld basename')
    if not a.sdk_root.is_dir() or not a.resource_dir.is_dir():raise ValueError('Missing explicit SDK/resource inputs')
    for role in TOOL_ROLES:
        p=getattr(a,role)
        if not p.is_file() or not os.access(p,os.X_OK):raise ValueError('Missing executable prerequisite '+role)
    clean(tmp_dir=a.tmp_dir)

TOOL_ROLES=['cc','cxx','lld','ar','ranlib','readelf','nm','signer','python','cmake','ninja','git','pkgconf','ctest']

class Runner:
    def __init__(self,a):self.a=a;self.env,self.removed=clean(tmp_dir=a.tmp_dir);self.count=0;self.capture=None
    def run(self,command,label,input=None,expected=0,timeout=180,cwd=None):
        command=list(map(str,command))
        if self.capture is not None:
            result=self.capture.dispatch(command,self.env,Path(os.getcwd()) if cwd is None else cwd,'runner',Path(__file__),
                                         input_data=None if input is None else input.encode(),timeout=timeout)
            self.capture.event({'kind':'Runner-original-observation','label':label,'expected':expected,
                                'actual_exit':result['exit'],'dispatch':result['record'],'cleared_before_dispatch':self.removed})
            if result['exit']!=expected:raise ValueError('Command failed '+label+' original capture '+result['record']['path'])
            return result['stdout'].decode()
        p=subprocess.run(command,env=self.env,input=input,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout,cwd=cwd)
        self.count+=1;log=self.a.root/'logs'/(label+'.log');log.parent.mkdir(parents=True,exist_ok=True);log.write_text(p.stdout)
        json_write(log.with_suffix('.json'),{'argv':command,'exit':p.returncode,'expected':expected,'log_sha256':sha(log),'cleared_ambient_names':self.removed})
        if p.returncode!=expected:raise ValueError('Command failed '+label+' inspect '+str(log))
        return p.stdout

def verify_inputs():
    lock=json.loads((HERE/'inputs.lock.json').read_text())
    for row in lock['sealed_files']:
        p=REPO/row['path']
        if sha(p)!=row['sha256'] or p.stat().st_size!=row['size']:raise ValueError('Sealed recipe/source input drift '+row['path'])
    source=json.loads((HERE/'sources.lock.json').read_text())['LLVM'];full=hashlib.sha256();size=0
    for row in source['parts']:
        with (REPO/row['path']).open('rb') as f:
            while data:=f.read(1024*1024):full.update(data);size+=len(data)
    if full.hexdigest()!=source['original_sha256'] or size!=source['original_size']:raise ValueError('Complete LLVM archive joined bytes drift')
    return {'entry':'build_files/ohos/toolchain/builder.py','inputs_lock':{'path':'build_files/ohos/toolchain/inputs.lock.json','sha256':sha(HERE/'inputs.lock.json')}}
