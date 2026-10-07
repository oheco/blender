# SPDX-License-Identifier: GPL-2.0-or-later
"""Read native ELF bytes without subprocesses; section evidence is not a trust proof."""
import hashlib
from pathlib import Path
import struct


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def inspect(path):
    return inspect_bytes(Path(path).read_bytes(),str(path))


def inspect_bytes(data,path='<source-parser bytes>'):
    def take(offset,size):
        if offset<0 or size<0 or offset+size>len(data):raise ValueError('ELF range escapes file')
        return data[offset:offset+size]
    if len(data)<64 or data[:7]!=b'\x7fELF\x02\x01\x01':raise ValueError('Expected ELF64 LE v1')
    header=struct.unpack('<HHIQQQIHHHHHH',take(16,48))
    kind,machine,version,entry,phoff,shoff,flags,ehsize,phentsize,phnum,shentsize,shnum,shstrndx=header
    if kind!=3 or machine!=183 or version!=1 or ehsize!=64:raise ValueError('Native AArch64 ET_DYN required')
    if shentsize!=64 or not 0<shnum<=4096 or not 0<=shstrndx<shnum:raise ValueError('Invalid/unsupported ELF section table')
    sections=[struct.unpack('<IIQQQQIIQQ',take(shoff+n*shentsize,shentsize)) for n in range(shnum)]
    strings=take(sections[shstrndx][4],sections[shstrndx][5])
    def string(table,index):
        if not 0<=index<len(table):raise ValueError('ELF string index escapes table')
        end=table.find(b'\x00',index)
        if end<0:raise ValueError('Unterminated ELF string')
        return table[index:end].decode('utf-8')
    names=[string(strings,s[0]) for s in sections]
    needed,sonames,runpaths=[],[],[];textrel=False
    for s in sections:
        if s[1]!=6:continue
        if s[9]!=16 or s[5]%16 or not 0<=s[6]<shnum:raise ValueError('Invalid ELF dynamic section')
        dynstrings=take(sections[s[6]][4],sections[s[6]][5])
        for off in range(s[4],s[4]+s[5],16):
            tag,value=struct.unpack('<qQ',take(off,16))
            if tag==0:break
            if tag==1:needed.append(string(dynstrings,value))
            elif tag==14:sonames.append(string(dynstrings,value))
            elif tag in (15,29):runpaths.extend(string(dynstrings,value).split(':'))
            elif tag==22 or tag==30 and value&4:textrel=True
    signed=False
    if '.codesign' in names:
        signsection=sections[names.index('.codesign')]
        signed=signsection[1]!=8 and bool(take(signsection[4],signsection[5]))
    return {'path':str(path),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'aarch64':True,'type':'ET_DYN',
            'needed':needed,'sonames':sonames,'runpaths':runpaths,'TEXTREL':textrel,'codesign':signed}


def extension(path):
    value=inspect(path)
    if not value['codesign'] or value['TEXTREL'] or value['runpaths'] or 'libpython3.13.so' not in value['needed'] or \
       set(value['needed'])-{'libpython3.13.so','libc.so'}:
        raise ValueError('Extension must be finally signed, no RPATH/TEXTREL, native unversioned Python/system closure only')
    return value


def python_library(path):
    value=inspect(path)
    if Path(path).name!='libpython3.13.so' or value['sonames']!=['libpython3.13.so'] or not value['codesign'] or \
       value['TEXTREL'] or set(value['needed'])-{'libc.so'} or any(p and not p.startswith('$ORIGIN') for p in value['runpaths']):
        raise ValueError('Actual fresh signed unversioned libpython3.13.so required; never rename a versioned binary')
    return value
