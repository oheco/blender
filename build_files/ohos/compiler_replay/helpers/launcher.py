#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Caller-configured actual compiler invocation; sign each linked ELF before use."""
import sys
sys.dont_write_bytecode = True
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import struct
import hashlib
from env_policy import clean


def expand(args, depth=0):
    if depth > 8:
        raise ValueError('Response file nesting too deep')
    result = []
    for arg in args:
        if arg.startswith('@') and Path(arg[1:]).is_file():
            result.extend(expand(shlex.split(Path(arg[1:]).read_text()), depth + 1))
        else:
            result.append(arg)
    return result


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    language = sys.argv[2]
    if sys.argv[3] != '--' or language not in ('c', 'cxx'):
        raise SystemExit('Expected config language -- compiler arguments')
    args = sys.argv[4:]
    inspected = expand(args)
    nonlink = {'-c','-E','-S','-M','-MM','-fsyntax-only','-###','--version','--help','-cc1','-cc1as'}
    link = not any(a in nonlink or a.startswith(('-dump','-print','--print')) for a in inspected)
    output = Path('a.out');explicit_output=False
    for index, arg in enumerate(inspected):
        if arg == '-o' and index + 1 < len(inspected):
            output = Path(inspected[index + 1]);explicit_output=True
        elif arg.startswith('-o') and len(arg) > 2 and not arg.startswith(('-objc','-offload','-opt-','-object-','-openmp')):
            output = Path(arg[2:]);explicit_output=True
    link = link and ('-' in inspected or any(not a.startswith('-') and Path(a).is_file() and a != str(output) for a in inspected))
    dry_query=any(a in ('-###','--version','--help','-fsyntax-only') or a.startswith(('-dump','-print','--print','--help=')) for a in inspected)
    outputs=[]
    if not dry_query:
        if link or explicit_output:outputs.append(output)
        elif any(a in inspected for a in ['-c','-S']):
            suffix='.s' if '-S' in inspected else '.o'
            outputs.extend(Path(Path(a).stem+suffix) for a in inspected if not a.startswith('-') and Path(a).is_file())
        for index,arg in enumerate(inspected):
            if arg in ('-MF','-MJ','--serialize-diagnostics') and index+1<len(inspected):outputs.append(Path(inspected[index+1]))
            elif arg.startswith(('-MF','-MJ')) and len(arg)>3:outputs.append(Path(arg[3:]))
        if any(a.startswith(('-save-temps','-fprofile-instr-generate','-fmodules-cache-path')) for a in inspected):raise ValueError('Unregistered implicit compiler output mode')
    for candidate in outputs:
        resolved=candidate.resolve();cache=Path(config['private_cache']).resolve()
        if cache not in resolved.parents:raise ValueError('Output must belong to private XDG cache')
        if not any(Path(p).resolve() in resolved.parents for p in config['allowed_output_roots']):raise ValueError('Output not under explicitly registered consumer build root')
        for protected in config['protected_inputs']:
            p=Path(protected).resolve()
            if resolved==p or p in resolved.parents or resolved in p.parents:raise ValueError('Output overlaps immutable provided/recipe input')
    defaults = config['flags'][language][:]
    if not link and '-###' not in inspected:
        defaults = [a for a in defaults if not (a in ('-static-libstdc++','-lc++experimental') or
                    a.startswith(('-fuse-ld=','--ld-path=','-Wl,','-L')))]
    elif link:
        output.unlink(missing_ok=True)
    environment,_=clean(tmp_dir=config['tmp_dir'])
    result = subprocess.run([config['compilers'][language]] + defaults + args,env=environment)
    if result.returncode:
        raise SystemExit(result.returncode)
    if not link or not output.is_file():
        return
    with output.open('rb') as stream:header=stream.read(64)
    if header[:4]!=b'\x7fELF':return
    if header[4:6]!=b'\x02\x01':raise ValueError('Expected native ELF64 little endian')
    elf_type,machine=struct.unpack_from('<HH',header,16)
    if elf_type==1:return
    if elf_type not in (2,3) or machine!=183:raise ValueError('Linked output not native AArch64 ET_EXEC/ET_DYN')
    environment = os.environ.copy()
    environment.pop('LD_LIBRARY_PATH', None)
    environment.pop('LD_PRELOAD', None)
    environment['TMPDIR'] = config['tmp_dir']
    with tempfile.TemporaryDirectory(prefix='core-linked-sign-', dir=config['tmp_dir']) as td:
        signed = Path(td) / 'signed'
        signed_run = subprocess.run([config['signer'],'sign','-inFile',str(output),'-outFile',str(signed),'-selfSign','1'],
                                    env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if signed_run.returncode or not signed.is_file():
            raise SystemExit('ELF signing failed: ' + signed_run.stdout)
        sections = subprocess.check_output([config['readelf'],'--file-header','--sections',str(signed)], env=environment, text=True)
        with signed.open('rb') as stream:signed_header=stream.read(20)
        if 'AArch64' not in sections or '.codesign' not in sections or signed_header[:4]!=b'\x7fELF' or struct.unpack_from('<HH',signed_header,16)!=(elf_type,183):
            raise SystemExit('Signer result lacks native type/signature')
        unsigned_sha=hashlib.sha256(output.read_bytes()).hexdigest();signed_sha=hashlib.sha256(signed.read_bytes()).hexdigest()
        signed.chmod(0o755)
        output.unlink()
        shutil.move(str(signed), str(output))
        receipt={'path':str(output.resolve()),'argv':[config['compilers'][language],*defaults,*args],'unsigned_sha256':unsigned_sha,'signed_sha256':signed_sha,'ELF_type':elf_type,'machine':'AArch64','signer':config['signer'],'signature_checked':True}
        payload=json.dumps(receipt,indent=2)+'\n'
        output.with_name(output.name+'.signed-receipt.json').write_text(payload)
        directory=Path(config['signature_receipts_dir']);directory.mkdir(parents=True,exist_ok=True)
        directory.joinpath(hashlib.sha256(str(output.resolve()).encode()).hexdigest()+'-'+signed_sha+'.json').write_text(payload)


if __name__ == '__main__':
    main()
