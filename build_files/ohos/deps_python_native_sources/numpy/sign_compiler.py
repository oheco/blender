#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adapted from accepted HAP NumPy signing compiler; all helper/tools are explicit.
Sign actual linked probe/extension ELF before return. Never force a probe result.
"""
import sys
sys.dont_write_bytecode=True
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


def expand(args,depth=0):
    if depth>4:raise ValueError('Response file nesting too deep')
    result=[]
    for arg in args:
        if arg.startswith('@') and Path(arg[1:]).is_file():result.extend(expand(shlex.split(Path(arg[1:]).read_text()),depth+1))
        else:result.append(arg)
    return result


def main():
    if len(sys.argv)<5 or sys.argv[3]!='--' or sys.argv[2] not in ('c','cpp'):
        raise SystemExit('Usage: sign_compiler.py compiler.json c|cpp -- compiler arguments')
    config=json.loads(Path(sys.argv[1]).read_text());language=sys.argv[2];args=sys.argv[4:];inspected=expand(args)
    nonlink={'-c','-E','-S','-M','-MM','-fsyntax-only','-###','--version','--help','-cc1','-cc1as'}
    link=not any(a in nonlink or a.startswith(('-dump','-print','--print')) for a in inspected)
    output=Path('a.out')
    for i,a in enumerate(inspected):
        if a=='-o' and i+1<len(inspected):output=Path(inspected[i+1])
        elif a.startswith('-o') and len(a)>2:output=Path(a[2:])
    link=link and any(not a.startswith('-') and Path(a).is_file() and a!=str(output) for a in inspected)
    defaults=config['flags'][language][:]
    if link:output.unlink(missing_ok=True)
    else:defaults=[a for a in defaults if a not in ('-static-libstdc++',) and not a.startswith(('--ld-path=','-fuse-ld=','-Wl,','-L'))]
    shared=any(a in ('-shared','--shared') for a in inspected)
    prefix=Path(config['runtime_prefix'])
    if link and shared:
        # OHOS extensions explicitly bind the already-loaded NEW unversioned runtime.
        args+=['-L'+str(prefix/'lib'),'-lpython3.13']
    python_link=any(a=='-lpython3.13' or not a.startswith('-') and Path(a).name=='libpython3.13.so' for a in inspected)
    if link and python_link and not shared:
        # Probe-only executable route; no absolute RPATH enters installed extensions.
        args+=['-Wl,-rpath,'+str(prefix/'lib')]
    p=subprocess.run([config['compilers'][language],*defaults,*args])
    if p.returncode:raise SystemExit(p.returncode)
    if not link or not output.is_file():return
    with output.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':return
    env=os.environ.copy();env.pop('LD_LIBRARY_PATH',None);env.pop('LD_PRELOAD',None);env['TMPDIR']=config['tmp_dir']
    with tempfile.TemporaryDirectory(prefix='numpy-real-ELF-sign-',dir=config['tmp_dir']) as td:
        signed=Path(td)/'signed'
        p=subprocess.run([config['signer'],'sign','-inFile',str(output),'-outFile',str(signed),'-selfSign','1'],
                         env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        if p.returncode or not signed.is_file():raise SystemExit('Native ELF signing failed: '+p.stdout)
        sections=subprocess.check_output([config['readelf'],'--sections',str(signed)],env=env,text=True)
        if '.codesign' not in sections:raise SystemExit('Final signed output lacks .codesign')
        signed.chmod(0o755);output.unlink();shutil.move(str(signed),str(output))


if __name__=='__main__':main()
