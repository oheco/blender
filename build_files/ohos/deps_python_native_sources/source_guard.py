# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete original part reassembly and bounded tar source preparation.
Adapted from OHOS pure-resource/core guards; original archives remain untouched.
"""
import sys
sys.dont_write_bytecode=True
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def canonical(name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or any(ord(c)<32 or ord(c)==127 for c in name) or \
       PurePosixPath(name).is_absolute() or any(p in ('','.','..') for p in name.split('/')):
        raise ValueError('Noncanonical path: '+repr(name))
    return PurePosixPath(name).parts


def repo_file(name):
    p=REPO.joinpath(*canonical(name))
    if not p.is_file() or any(v.is_symlink() for v in [p,*p.parents]):raise ValueError('Unsafe repository input '+name)
    return p


def inventory(root,case_pairs=()):
    allowed_cases={tuple(sorted(pair)) for pair in case_pairs}
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('Real inventory root required')
    rows=[];spellings={}
    for p in sorted(root.rglob('*')):
        name=p.relative_to(root).as_posix();parts=canonical(name);mode=p.lstat().st_mode
        for n in range(1,len(parts)+1):
            prefix='/'.join(parts[:n])
            other=spellings.setdefault(prefix.casefold(),prefix)
            if other!=prefix and tuple(sorted((other,prefix))) not in allowed_cases:raise ValueError('Source case collision')
        if stat.S_ISREG(mode):rows.append({'path':name,'size':p.stat().st_size,'sha256':sha(p),'executable':bool(mode&0o111)})
        elif stat.S_ISLNK(mode):
            link=os.readlink(p)
            if PurePosixPath(link).is_absolute() or not p.resolve().is_relative_to(root.resolve()) or not p.resolve().exists():
                raise ValueError('Unsafe/dangling source link')
            rows.append({'path':name,'symlink':link})
        elif not stat.S_ISDIR(mode):raise ValueError('Source special file')
    return rows


def original_mtime_ns(member):
    """Original regular mtime, exactly representable in signed 64-bit epoch ns.

    PAX decimal text is authoritative; TarInfo.mtime's float loses original
    fractional precision. Reject nonzero sub-ns digits; no rounding or clamp.
    """
    value=member.pax_headers.get('mtime')
    if value is None:
        if type(member.mtime) is not int:raise ValueError('Invalid original regular tar mtime')
        ns=member.mtime*1_000_000_000
    else:
        if not isinstance(value,str) or len(value)>128 or not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value):
            raise ValueError('Invalid original regular PAX mtime')
        whole,_,fraction=value.lstrip('-').partition('.')
        if any(c!='0' for c in fraction[9:]):raise ValueError('Original regular PAX mtime is not exactly representable in nanoseconds')
        ns=int(whole)*1_000_000_000+int((fraction+'000000000')[:9])
        if value.startswith('-'):ns=-ns
    if not -(1<<63)<=ns<(1<<63):raise ValueError('Original regular tar mtime outside signed 64-bit ns range')
    return ns


def extract(archive,destination,root_name,case_pairs=()):
    allowed_cases={tuple(sorted(pair)) for pair in case_pairs}
    destination=Path(destination)
    if destination.exists():raise ValueError('Source extraction target must be absent')
    if allowed_cases:
        expected={('perl-5.44.0-ohos-arm64/lib/5.44.0/Pod','perl-5.44.0-ohos-arm64/lib/5.44.0/pod')}
        if root_name!='perl-5.44.0-ohos-arm64' or allowed_cases!=expected or Path(archive).stat().st_size!=17551204 or sha(archive)!='053e5252d736fa55a98a09e5124ead6391103717eb3bd465c1da882705a9cfef':raise ValueError('Only exact whole original Perl archive Pod/pod pair is allowed')
        with tempfile.TemporaryDirectory(prefix='perl-original-case-proof-',dir=destination.parent) as td:
            probe=Path(td)
            (probe/'Case').write_text('upper')
            with (probe/'case').open('x') as f:f.write('lower')
            if (probe/'Case').read_text()!='upper':raise ValueError('Required case-sensitive filesystem unavailable')
    with tarfile.open(archive,'r:*') as tar:
        members=[];declared_bytes=0
        for m in tar:
            if len(members)>=40000 or m.size<0:raise ValueError('Source tar member/size ceiling exceeded')
            if m.isfile():declared_bytes+=m.size
            if declared_bytes>600*1024*1024:raise ValueError('Source tar expanded byte ceiling exceeded')
            if m.type not in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE,tarfile.SYMTYPE,tarfile.LNKTYPE) or m.sparse is not None:
                raise ValueError('Unsupported/sparse source tar type')
            members.append(m)
        nodes={};spellings={};seen=set();roots=set();links=[]
        for m in members:
            name=m.name.rstrip('/') if m.isdir() else m.name
            parts=canonical(name);roots.add(parts[0])
            if name in seen:raise ValueError('Duplicate source tar path')
            seen.add(name)
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):raise ValueError('Source special tar member')
            for n in range(1,len(parts)+1):
                prefix='/'.join(parts[:n]);kind='directory' if n<len(parts) or m.isdir() else 'link' if m.issym() or m.islnk() else 'file'
                other=spellings.setdefault(prefix.casefold(),prefix)
                if other!=prefix and tuple(sorted((other,prefix))) not in allowed_cases:raise ValueError('Case-colliding tar prefix')
                if prefix in nodes and nodes[prefix]!=kind:raise ValueError('Tar node/ancestor type collision')
                nodes[prefix]=kind
            if m.issym() or m.islnk():
                if not m.linkname or '\\' in m.linkname or ':' in m.linkname or '\x00' in m.linkname or m.linkname.startswith('/'):
                    raise ValueError('Unsafe tar link')
                target=posixpath.normpath(posixpath.join(posixpath.dirname(name) if m.issym() else '',m.linkname))
                if not target.startswith(root_name+'/'):raise ValueError('Escaping tar link')
                links.append((m,name,target))
        if roots!={root_name} or nodes.get(root_name)!='directory':raise ValueError('Wrong tar root')
        if allowed_cases:
            by_name={m.name.rstrip('/'):m for m in members}
            for pair in allowed_cases:
                if any(not by_name[p].isdir() or by_name[p].mode!=0o755 for p in pair):raise ValueError('Pinned Perl case pair original directory type/mode differs')
        for m,name,target in links:
            if target not in nodes:raise ValueError('Missing tar link target')
            if m.islnk() and nodes[target]!='file':raise ValueError('Hardlink requires direct regular target')
            # Reject link chains/cycles so extraction never depends on ambient files.
            if nodes[target]=='link':raise ValueError('Tar link chains are unsupported')
        regular_members={m.name:m for m in members if m.isfile()}
        regular_mtimes={name:original_mtime_ns(m) for name,m in regular_members.items()}
        hardlink_targets={name:target for m,name,target in links if m.islnk()}
        effective_bytes=declared_bytes+sum(regular_members[target].size for target in hardlink_targets.values())
        if effective_bytes>600*1024*1024:raise ValueError('Materialized hardlink byte ceiling exceeded')
        destination.mkdir(parents=True)
        try:
            for m in members:
                name=m.name.rstrip('/') if m.isdir() else m.name
                p=destination.joinpath(*canonical(name)[1:])
                if m.isdir():p.mkdir(parents=True,exist_ok=True)
                elif m.isfile() or m.islnk():
                    p.parent.mkdir(parents=True,exist_ok=True)
                    original=regular_members[hardlink_targets[m.name]] if m.islnk() else m
                    with tar.extractfile(original) as f,p.open('xb') as out:shutil.copyfileobj(f,out)
                    p.chmod(m.mode&0o777 if allowed_cases else 0o755 if m.mode&0o111 else 0o644)
                    # A materialized hardlink inherits its direct regular target's
                    # original time, regardless of its own link-header timestamp.
                    ns=regular_mtimes[original.name]
                    os.utime(p,ns=(p.stat().st_atime_ns,ns),follow_symlinks=False)
                    if p.stat().st_mtime_ns!=ns:raise ValueError('Filesystem did not preserve original regular tar mtime')
            if allowed_cases:
                for m in sorted((m for m in members if m.isdir()),key=lambda m:len(m.name.split('/')),reverse=True):
                    destination.joinpath(*canonical(m.name.rstrip('/'))[1:]).chmod(m.mode&0o777)
            for m,name,target in links:
                if m.issym():
                    p=destination.joinpath(*canonical(name)[1:]);p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to(m.linkname)
            inventory(destination,case_pairs=[tuple(p[len(root_name)+1:] for p in pair) for pair in allowed_cases])
        except BaseException:
            shutil.rmtree(destination);raise
    return {'members':len(members),'hardlinks_materialized_as_exact_regular_bytes':sum(m.islnk() for m in members),
            'internal_symlinks':sum(m.issym() for m in members)}


def reconstruct(dep,output):
    manifest=json.loads(repo_file(dep['manifest']).read_text());output=Path(output)
    if output.exists():raise ValueError('Archive output must be absent')
    if manifest.get('schema_version')!=1 or manifest.get('format')!='original-archive-parts' or \
       manifest['sha256']!=dep['sha256'] or manifest['size']!=dep['size']:raise ValueError('Original manifest differs')
    output.parent.mkdir(parents=True,exist_ok=True);combined=hashlib.sha256();total=0
    try:
        with output.open('xb') as out:
            for n,row in enumerate(manifest['parts']):
                if row['filename']!='source.part%04d'%n or not 0<row['size']<=32*1024*1024:raise ValueError('Invalid source part')
                p=repo_file(str(PurePosixPath(dep['manifest']).parent/row['filename']))
                if p.stat().st_size!=row['size'] or sha(p)!=row['sha256']:raise ValueError('Source part SHA/size differs')
                with p.open('rb') as f:
                    while b:=f.read(1024*1024):combined.update(b);total+=len(b);out.write(b)
        if total!=dep['size'] or combined.hexdigest()!=dep['sha256']:raise ValueError('Complete original archive differs')
    except BaseException:output.unlink(missing_ok=True);raise


def verify_inputs():
    lock=json.loads((HERE/'inputs.lock.json').read_text())
    for row in lock['sealed_files']:
        p=repo_file(row['path'])
        if p.stat().st_size!=row['size'] or sha(p)!=row['sha256']:raise ValueError('Sealed source/input drift: '+row['path'])
    prefix=HERE.relative_to(REPO).as_posix()+'/'
    expected={row['path'] for row in lock['sealed_files'] if row['path'].startswith(prefix)}
    actual={p.relative_to(REPO).as_posix() for p in HERE.rglob('*') if p.is_file() and p!=HERE/'inputs.lock.json'}
    if actual!=expected:raise ValueError('Unsealed extra/missing native namespace files')
    return {'result':'PASS sealed input bytes only','sealed_files':len(lock['sealed_files']),'native_full':'NOTRUN','HAP':'NOTRUN'}
