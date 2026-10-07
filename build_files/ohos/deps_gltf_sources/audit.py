# SPDX-License-Identifier: GPL-2.0-or-later
"""Final signed ELF/archive/PIC/ABI/metadata evidence, never inferred runtime PASS."""
import json
from pathlib import Path
import re
from source_guard import HERE, sha, inventory, verify_tree
from source_tree import sources, verify_bridges
from io_utils import write_json
from metadata import BRIDGES, verify


def elf(args,runner,file,label):
    readelf=args.sdk_root/'llvm/bin/llvm-readelf'
    h=runner.run([readelf,'-h',file],label+'-header')
    sections=runner.run([readelf,'-S',file],label+'-sections')
    dynamic=runner.run([readelf,'-d',file],label+'-dynamic')
    syms=runner.run([args.sdk_root/'llvm/bin/llvm-nm','--demangle',file],label+'-symbols')
    needed=re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',dynamic)
    paths=re.findall(r'\((?:RPATH|RUNPATH)\).*?\[([^\]]*)\]',dynamic)
    if 'AArch64' not in h or 'DYN' not in h or '.codesign' not in sections:
        raise ValueError('Final actual signed native ELF required: '+str(file))
    allowed={'libc.so','libm.so','libdl.so','libpthread.so','ld-musl-aarch64.so.1'}
    if file.name=='gltf_native_acceptance':allowed.update(BRIDGES)
    if 'TEXTREL' in dynamic or paths or any(n not in allowed for n in needed):
        raise ValueError('TEXTREL/RPATH or undeclared dynamic dependency; allowed native system closure only: '+repr(needed))
    if 'std::__h::' in syms or re.search(r'\bpthread_cancel\b',syms):
        raise ValueError('Wrong libc++ ABI or unsupported symbol')
    return {'path':str(file),'sha256':sha(file),'needed':needed,'codesign':True,
            'TEXTREL':False,'RPATH':paths,'static_cpp':True,'SDK_libcpp_version':15004}


def artifacts(args,runner):
    verify(args)
    src=[verify_tree(args.root/'sources'/d['name'],d) for d in sources()]
    bridges=verify_bridges(args.root/'blender-bridge-source')
    archives=[]
    for name in ['draco','meshoptimizer']:
        p=args.prefix/'lib'/('lib'+name+'.a')
        out=runner.run([args.sdk_root/'llvm/bin/llvm-readelf','-h',p],'archive-'+name)
        machines=re.findall(r'Machine:\s+([^\n]+)',out)
        if not machines or any(v.strip()!='AArch64' for v in machines):
            raise ValueError('Archive members not all actual AArch64')
        symbols=runner.run([args.sdk_root/'llvm/bin/llvm-nm','--undefined-only','--demangle',p],'archive-abi-'+name)
        if 'std::__h::' in symbols:raise ValueError('Wrong static C++ ABI')
        archives.append({'name':name,'sha256':sha(p),'members':len(machines)})
    units=[]
    for name in ['draco','meshoptimizer','bridges']:
        database=args.root/'build'/name/'compile_commands.json';rows=json.loads(database.read_text())
        if not rows:raise ValueError('Actual source compile commands absent')
        pic, pie=0,0
        for row in rows:
            cmd=row['command']
            executable=bool(re.search(r'CMakeFiles/draco_(?:encoder|decoder)\.dir',cmd))
            if re.search(r'\s-m(?:sse|avx)',cmd) or (not executable and '-fPIC' not in cmd) or \
               (executable and '-fPIE' not in cmd and '-fPIC' not in cmd):
                raise ValueError('Actual codec PIC/tool PIE/AArch64 compile commands differ')
            if executable:pie+=1
            else:pic+=1
        units.append({'name':name,'translation_units':len(rows),'PIC_codec_bridge_units':pic,'PIE_tool_units':pie,'sha256':sha(database)})
    generated=args.root/'build/draco/draco/draco_features.h';text=generated.read_text()
    features=['DRACO_MESH_COMPRESSION_SUPPORTED','DRACO_POINT_CLOUD_COMPRESSION_SUPPORTED','DRACO_NORMAL_ENCODING_SUPPORTED',
              'DRACO_STANDARD_EDGEBREAKER_SUPPORTED','DRACO_PREDICTIVE_EDGEBREAKER_SUPPORTED','DRACO_BACKWARDS_COMPATIBILITY_SUPPORTED',
              'DRACO_ATTRIBUTE_INDICES_DEDUPLICATION_SUPPORTED','DRACO_ATTRIBUTE_VALUES_DEDUPLICATION_SUPPORTED']
    if any('#define '+f not in text for f in features) or '#define DRACO_TRANSCODER_SUPPORTED' in text:
        raise ValueError('Actual generated selected feature matrix differs')
    binaries=[elf(args,runner,args.prefix/'lib'/n,'installed-'+n) for n in BRIDGES]
    binaries += [elf(args,runner,p,'installed-tool-'+p.name) for p in sorted((args.prefix/'bin').glob('*')) if p.is_file()]
    abi={'libbf_intern_draco_bridge.so':['encoderCreate','encoderRelease','encoderSetAttribute','encoderEncode','decoderCreate','decoderRelease','decoderDecode','decoderCopyAttribute'],
         'libbf_intern_meshopt_bridge.so':['encodeVertexBuffer','decodeVertexBuffer','encodeIndexBuffer','decodeIndexBuffer','encodeFilterOct','decodeFilterOct']}
    for name,required in abi.items():
        symbols=runner.run([args.sdk_root/'llvm/bin/llvm-nm','-D','--defined-only',args.prefix/'lib'/name],'bridge-exports-'+name)
        if any(not re.search(r'\b'+n+r'$',symbols,re.M) for n in required):
            raise ValueError('Actual Blender C ABI export absent')
    metadata=[]
    forbidden=[str(args.root),str(HERE),str(args.prefix)]
    for p in args.prefix.rglob('*'):
        if p.is_file() and p.suffix in ('.pc','.cmake'):
            t=p.read_text()
            if any(v in t for v in forbidden) or re.search(r'"/(?!")[^"\n]+"',t):
                raise ValueError('Installed metadata has absolute original/source/build route: '+str(p))
            metadata.append({'path':p.relative_to(args.prefix).as_posix(),'sha256':sha(p)})
    acceptance=json.loads((args.root/'acceptance.json').read_text())
    if acceptance.get('result','').startswith('PASS') is False or acceptance['prefix_seal_sha256']!=sha(args.root/'prefix-seal.json') or \
       acceptance['input_lock_sha256']!=sha(HERE/'inputs.lock.json'):
        raise ValueError('Actual acceptance is for another prefix')
    result={'result':'PASS actual signed source-built static PIC libraries and actual Blender bridges',
            'archives':archives,'binaries':binaries,'metadata':metadata,'compile':units,
            'generated_features':{'sha256':sha(generated),'enabled':features},'sources':src,'bridges':bridges,
            'input_lock_sha256':sha(HERE/'inputs.lock.json'),'acceptance_sha256':sha(args.root/'acceptance.json'),
            'prefix_seal_sha256':sha(args.root/'prefix-seal.json'),
            'bpy':'NOTRUN separate parent postlink gate','HAP':'NOTRUN'}
    write_json(args.root/'artifacts.json',result);return result
