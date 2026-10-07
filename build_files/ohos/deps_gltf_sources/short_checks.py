#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source/IO/ownership/Python-only negatives. NEVER configure, compile or run native code."""
import sys
sys.dont_write_bytecode=True
import ast
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile
from types import SimpleNamespace
from source_guard import HERE, REPO, sha, extract_zip, verify_inputs, verify_tree, inventory, repo_file
from source_tree import prepare, sources
from io_utils import Runner, ownership, write_json, private_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--git',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():raise ValueError('Short report must be absent')
    checks=[]
    def negative(label,call):
        try:call()
        except (ValueError,FileExistsError,RuntimeError):checks.append({'check':label,'result':'PASS rejected'})
        else:raise AssertionError('Expected rejection: '+label)
    for p in HERE.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
    checks.append({'check':'all Python AST parse','result':'PASS'})
    verified=verify_inputs()
    original_env={k:os.environ[k] for k in ['TMPDIR','XDG_CACHE_HOME']}
    with tempfile.TemporaryDirectory(prefix='gltf-short-only-',dir=original_env['TMPDIR']) as td:
        base=Path(td);cache=base/'private cache';tmp=base/'private temp';cache.mkdir();tmp.mkdir()
        os.environ.update(XDG_CACHE_HOME=str(cache),TMPDIR=str(tmp))
        root=cache/'original source with spaces';a=SimpleNamespace(root=root,prefix=root/'prefix',tmp_dir=tmp,resume=False,git=args.git)
        with ownership(a):
            r=Runner(root,tmp);prepared=prepare(a,r)
        a.resume=True
        with ownership(a):pass
        checks.append({'check':'exact owned resume','result':'PASS'})
        internal=root/'toolchain';internal.symlink_to(tmp,target_is_directory=True)
        negative('owned resume internal output symlink',lambda:ownership(a));internal.unlink()
        a.resume=False;negative('existing root without resume',lambda:ownership(a))
        a.resume=True;a.prefix=root/'other';negative('mismatched owned prefix',lambda:ownership(a));a.prefix=root/'prefix'
        negative('HOME output',lambda:private_path(REPO/'unowned-output'))
        negative('prefix parent traversal escape',lambda:private_path(root/'..'/'existing-unowned'))
        reserved=SimpleNamespace(root=cache/'new reserved root',prefix=cache/'new reserved root/sources',tmp_dir=tmp,resume=False)
        negative('reserved source prefix',lambda:ownership(reserved))
        link=cache/'link';link.symlink_to(root,target_is_directory=True)
        negative('symlink output path',lambda:private_path(link/'new'))
        for dep in sources():verify_tree(root/'sources'/dep['name'],dep)
        tamper=root/'sources/draco/LICENSE';tamper.write_bytes(tamper.read_bytes()+b'changed')
        negative('complete original source tamper',lambda:verify_tree(root/'sources/draco',sources()[0]))
        patch_records=json.loads((HERE/'patches.lock.json').read_text())
        checks.append({'check':'complete original archive inventories and bridge patch forward/reverse exacthash','result':'PASS','sources':prepared['sources'],'patches':patch_records})
        cases=[('absolute','/root/file',stat.S_IFREG|0o644),('parent','root/../file',stat.S_IFREG|0o644),
               ('backslash','root/a\\b',stat.S_IFREG|0o644),('colon','root/a:b',stat.S_IFREG|0o644),
               ('dot','root/./file',stat.S_IFREG|0o644),('symlink','root/link',stat.S_IFLNK|0o777),
               ('FIFO','root/pipe',stat.S_IFIFO|0o644),('wrong root','other/file',stat.S_IFREG|0o644)]
        for number,(label,name,mode) in enumerate(cases):
            z=base/('bad'+str(number)+'.zip')
            with zipfile.ZipFile(z,'w') as archive:
                i=zipfile.ZipInfo(name);i.create_system=3;i.external_attr=mode<<16;archive.writestr(i,b'target')
            target=base/('out'+str(number));negative('ZIP '+label,lambda z=z,target=target:extract_zip(z,target,'root'))
            if target.exists():raise AssertionError('Rejected ZIP wrote destination')
        for label,names in [('case prefixes',['root/A/a','root/a/b']),('duplicate',['root/a','root/a']),
                            ('file-directory',['root/a','root/a/b'])]:
            z=base/(label+'.zip')
            with zipfile.ZipFile(z,'w') as archive:
                for name in names:archive.writestr(name,b'data')
            target=base/(label+' output');negative('ZIP '+label,lambda z=z,target=target:extract_zip(z,target,'root'))
            if target.exists():raise AssertionError('Rejected ZIP wrote destination')
        # Artifact link policy IO only: these are plain test bytes, never an ELF/native proof.
        import metadata
        pi=base/'link policy fixture';(pi/'bin').mkdir(parents=True)
        (pi/'bin/draco_encoder-1.5.7').write_bytes(b'IO-only link fixture')
        (pi/'bin/draco_encoder').symlink_to('draco_encoder-1.5.7')
        aliases=metadata.prefix_inventory(pi)
        moved_pi=base/'link policy moved';shutil.copytree(pi,moved_pi,symlinks=True)
        if aliases!=metadata.prefix_inventory(moved_pi):raise AssertionError('Controlled tool alias bytes changed')
        checks.append({'check':'controlled relative VERSION alias preserved; IO-only fixture, not native ELF','result':'PASS'})
        (pi/'bin/draco_encoder').unlink();(pi/'bin/draco_encoder').symlink_to('../../escape')
        negative('installed alias escape',lambda:metadata.prefix_inventory(pi))
        # Fresh sealed checkout copied under Unicode and spaces; no original-root paths are used by the new process.
        moved=cache/'移动 repository input with spaces'
        cmd=[sys.executable,str(HERE/'builder.py'),'copy-inputs','--destination',str(moved)]
        result=subprocess.run(cmd,text=True,capture_output=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        builder=moved/HERE.relative_to(REPO)/'builder.py'
        result=subprocess.run([sys.executable,str(builder),'verify-inputs'],text=True,capture_output=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        moved_verified=json.loads(result.stdout)
        moved_root=cache/'移动 new prepared source with spaces'
        result=subprocess.run([sys.executable,str(builder),'prepare','--root',str(moved_root),'--tmp-dir',str(tmp),'--git',str(args.git)],
                              text=True,capture_output=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        moved_prepared=json.loads(result.stdout)
        checks.append({'check':'Unicode/space relocated sealed repository + complete original preparation + exact bridge patches','result':'PASS',
                       'verify':moved_verified,'prepare':moved_prepared})
        # Tamper only the NEW copied test checkout; existing formal inputs remain untouched.
        changed=moved/HERE.relative_to(REPO)/'feature-matrix.json';changed.write_bytes(changed.read_bytes()+b' ')
        result=subprocess.run([sys.executable,str(builder),'verify-inputs'],text=True,capture_output=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        if result.returncode==0:raise AssertionError('Relocated sealed tamper accepted')
        checks.append({'check':'relocated sealed input tamper','result':'PASS rejected','exit_code':result.returncode})
        os.environ.update(original_env)
    report={'result':'PASS short source/IO/Python-only checks','verified':verified,'checks':checks,
            'temporary_tree_cleaned':True,'native_CXX_build':'NOTRUN','dependency_configure':'NOTRUN','main_configure':'NOTRUN',
            'native_geometry_or_DLL_run':'NOTRUN','bpy':'NOTRUN','HAP':'NOTRUN','network':'NOTRUN'}
    write_json(args.report,report)
    print(json.dumps({'result':report['result'],'checks':len(checks),'report':str(args.report)},indent=2))


if __name__=='__main__':main()
