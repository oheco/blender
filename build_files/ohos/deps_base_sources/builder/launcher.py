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
    output = Path('a.out')
    for index, arg in enumerate(inspected):
        if arg == '-o' and index + 1 < len(inspected):
            output = Path(inspected[index + 1])
        elif arg.startswith('-o') and len(arg) > 2:
            output = Path(arg[2:])
    link = link and any(not a.startswith('-') and Path(a).is_file() and a != str(output) for a in inspected)
    defaults = config['flags'][language][:]
    if not link:
        defaults = [a for a in defaults if not (a in ('-static-libstdc++','-lc++experimental') or
                    a.startswith(('-fuse-ld=','--ld-path=','-Wl,','-L')))]
    else:
        output.unlink(missing_ok=True)
    result = subprocess.run([config['compilers'][language]] + defaults + args)
    if result.returncode:
        raise SystemExit(result.returncode)
    if not link or not output.is_file():
        return
    with output.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            return
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
        sections = subprocess.check_output([config['readelf'],'--sections',str(signed)], env=environment, text=True)
        if '.codesign' not in sections:
            raise SystemExit('Signer result lacks .codesign')
        signed.chmod(0o755)
        output.unlink()
        shutil.move(str(signed), str(output))


if __name__ == '__main__':
    main()
