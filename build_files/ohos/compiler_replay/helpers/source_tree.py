# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete fixed LLVM source ledger, exact links, private extraction and guards."""
import hashlib,json,os,posixpath,stat,tarfile,tempfile,shutil
from pathlib import Path,PurePosixPath
from common import HERE,repo_file,relative,sha,write_json

def ledger():
    source=json.loads((HERE/'sources.lock.json').read_text());file=repo_file(source['inventory'])
    if sha(file)!=source['inventory_sha256']:raise ValueError('Complete source ledger drift')
    data=json.loads(file.read_text());records={item['path']:item for item in data['records']}
    if len(records)!=len(data['records']):raise ValueError('Duplicate fixed source record')
    for name,item in records.items():
        if name:relative(name)
        if item['type']=='symlink':
            target=item['target'];normalized=posixpath.normpath(posixpath.join(posixpath.dirname(name),target))
            if target.startswith('/') or normalized.startswith('../') or normalized=='..' or normalized not in records:raise ValueError('Unbound/escaping source link')
            if normalized!=item['normalized_target']:raise ValueError('Source link target identity drift')
        elif item['type'] not in ['file','directory']:raise ValueError('Unsupported source type')
        for parent in PurePosixPath(name).parents:
            if str(parent)=='.':break
            if records.get(str(parent),{}).get('type')!='directory':raise ValueError('Source non-directory ancestor')
    return source,data,records

def inspect(root,records,partial=False,patched=False):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('Actual complete source directory required')
    actual={''}
    for current,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:actual.add((Path(current)/name).relative_to(root).as_posix())
    if (not partial and actual!=set(records)) or not actual<=set(records):raise ValueError('Complete source set missing/extra; preserved for investigation')
    patch=json.loads((HERE/'patches/manifest.json').read_text())
    if patched and patch['target'] in actual:
        target=root/patch['target']
        if target.is_symlink() or not target.is_file() or sha(target)!=patch['patched_sha256']:raise ValueError('Prepared source patch target byte/type drift')
    for name,item in records.items():
        if item['type']=='symlink' and name in actual:
            p=root/name
            if not p.is_symlink() or os.readlink(p)!=item['target'] or root.resolve() not in p.resolve().parents:raise ValueError('Declared source link/topology drift '+name)
    for name in sorted(actual):
        p=root/name;item=records[name];st=p.lstat();kind='symlink' if stat.S_ISLNK(st.st_mode) else 'directory' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'special'
        if kind!=item['type']:raise ValueError('Source record type drift '+name)
        if kind=='file':
            digest=patch['patched_sha256'] if patched and name==patch['target'] else item['sha256']
            if sha(p)!=digest or (not(patched and name==patch['target']) and st.st_size!=item['size']):raise ValueError('Source byte drift '+name)
            if stat.S_IMODE(st.st_mode)!=(item['mode']&0o777):raise ValueError('Source permission drift '+name)
        elif kind=='directory':
            if stat.S_IMODE(st.st_mode)!=(item['mode']&0o777):raise ValueError('Source directory permission drift '+name)
        elif kind=='symlink':
            if os.readlink(p)!=item['target'] or root.resolve() not in p.resolve().parents:raise ValueError('Exact declared link target/topology drift '+name)
    return {'records':len(actual),'complete':not partial,'patched':patched,'result':'PASS actual exact complete source ledger' if not partial else 'PASS exact partial source subset'}

def case_probe(tmp):
    with tempfile.TemporaryDirectory(prefix='compiler-source-case-',dir=tmp) as td:
        a=Path(td)/'Case';b=Path(td)/'case';a.write_bytes(b'A');b.write_bytes(b'B')
        if a.read_bytes()!=b'A' or b.read_bytes()!=b'B':raise ValueError('Complete source extraction requires case-sensitive private filesystem')

def extract(a,archive):
    source,data,records=ledger();root=a.root/'sources'/data['archive_root'];state=a.root/'extraction-state.json'
    identity={'recipe_sha256':sha(HERE/'inputs.lock.json'),'inventory_sha256':sha(repo_file(source['inventory'])),'archive_sha256':sha(archive),'source_root':str(root)}
    if identity['archive_sha256']!=source['LLVM']['sha256']:raise ValueError('Extract only exact complete original archive')
    if root.exists():
        saved=json.loads(state.read_text())
        if saved['identity']!=identity:raise ValueError('Stale/foreign partial source identity')
        if saved['status']=='COMPLETE':return guard(a) if (a.root/'prepared-source.json').exists() else inspect(root,records)
        if not a.resume_source:raise ValueError('Interrupted extraction retained; explicit --resume-source required')
        inspect(root,records,partial=True)
    else:
        root.mkdir(parents=True);root.chmod(records['']['mode']&0o777);write_json(state,{'identity':identity,'status':'EXTRACTING','actual_members':0})
    case_probe(a.tmp_dir);seen=set();links=[];header_metadata=[]
    with tempfile.TemporaryDirectory(prefix='compiler-source-stream-',dir=a.tmp_dir) as td:
        scratch=Path(td)/'current-member'
        with tarfile.open(archive,'r|xz') as tar:
            for member in tar:
                canonical=member.name.rstrip('/')
                if canonical==data['archive_root']:name=''
                elif canonical.startswith(data['archive_root']+'/'):name=canonical[len(data['archive_root'])+1:];relative(name)
                else:raise ValueError('Unknown original archive root')
                if name not in records or name in seen:raise ValueError('Unexpected/duplicate original member')
                item=records[name];seen.add(name);p=root/name
                header_metadata.append({'path':name,'mtime':member.mtime,'uid':member.uid,'gid':member.gid,'uname':member.uname,'gname':member.gname,'pax_headers':member.pax_headers})
                kind='directory' if member.isdir() else 'file' if member.isfile() else 'symlink' if member.issym() else 'unsupported'
                if kind!=item['type'] or member.mode!=item['mode'] or member.size!=item['size']:raise ValueError('Original tar member metadata drift')
                if name:
                    for parent in p.parents:
                        if parent==root:break
                        if parent.is_symlink() or not parent.is_dir():raise ValueError('Unsafe extraction ancestor')
                if kind=='directory':
                    if not p.exists():p.mkdir();p.chmod(item['mode']&0o777)
                elif kind=='file':
                    h=hashlib.sha256()
                    with scratch.open('wb') as out:
                        stream=tar.extractfile(member)
                        while block:=stream.read(1024*1024):h.update(block);out.write(block)
                    if h.hexdigest()!=item['sha256'] or scratch.stat().st_size!=item['size']:raise ValueError('Original extracted bytes drift')
                    if p.exists():
                        if p.is_symlink() or sha(p)!=item['sha256']:raise ValueError('Resume source content differs')
                        scratch.unlink()
                    else:scratch.chmod(item['mode']&0o777);shutil.move(scratch,p)
                elif kind=='symlink':
                    if member.linkname!=item['target']:raise ValueError('Archive declared link drift')
                    links.append((p,item['target']))
                if len(seen)%10000==0:
                    write_json(state,{'identity':identity,'status':'EXTRACTING','actual_members':len(seen)});print('extracted fixed members '+str(len(seen)),flush=True)
    if seen!=set(records):raise ValueError('Incomplete original archive extraction')
    for p,target in links:
        if p.is_symlink():
            if os.readlink(p)!=target:raise ValueError('Resume link target drift')
        elif p.exists():raise ValueError('Source symlink occupied by non-link')
        else:p.symlink_to(target)
    for meta in reversed(header_metadata):
        stamp=int(meta['mtime']*1000000000);os.utime(root/meta['path'],ns=(stamp,stamp),follow_symlinks=False)
    write_json(a.root/'archive-header-metadata.json',{'original_archive_sha256':identity['archive_sha256'],'source_metadata_records':header_metadata,'extract_policy':'Archive UID/GID/PAX retained as provenance; native owner unchanged, modes and original member mtime restored in NEW complete tree without rewriting original archive'})
    result=inspect(root,records);write_json(state,{'identity':identity,'status':'COMPLETE','actual_members':len(seen),'guard':result});return result

def prepare(a,runner):
    source,data,records=ledger();root=a.root/'sources'/data['archive_root'];meta=source['patch'];marker=a.root/'prepared-source.json'
    if marker.exists():
        saved=json.loads(marker.read_text())
        if saved['recipe_inputs_sha256']!=sha(HERE/'inputs.lock.json'):raise ValueError('Prepared source recipe drift')
        return inspect(root,records,patched=True)
    inspect(root,records);diff=HERE/'patches'/meta['patch_file'];target=root/meta['target']
    def apply(reverse=False,check=False,label=''):
        runner.run([a.git,'-C',root,'apply',*(['--reverse'] if reverse else []),*(['--check'] if check else []),diff],label)
    apply(check=True,label='source-patch-forward-check');apply(label='source-patch-forward')
    if sha(target)!=meta['patched_sha256']:raise ValueError('Patch after hash drift')
    apply(reverse=True,check=True,label='source-patch-reverse-check');apply(reverse=True,label='source-patch-reverse')
    if sha(target)!=meta['upstream_sha256']:raise ValueError('Patch exact reverse mismatch')
    inspect(root,records);apply(label='source-patch-final-forward');result=inspect(root,records,patched=True)
    write_json(marker,{'recipe_inputs_sha256':sha(HERE/'inputs.lock.json'),'source_root':str(root),'inventory_sha256':source['inventory_sha256'],'patch_before':meta['upstream_sha256'],'patch_after':meta['patched_sha256'],'forward_reverse':'PASS actual complete source before/after/reverse/final','guard':result})
    return result

def guard(a):
    source,data,records=ledger();marker=json.loads((a.root/'prepared-source.json').read_text())
    if marker['recipe_inputs_sha256']!=sha(HERE/'inputs.lock.json') or marker['inventory_sha256']!=source['inventory_sha256']:raise ValueError('Source/input binding drift')
    return inspect(a.root/'sources'/data['archive_root'],records,patched=True)
