# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual CMAKE/PC image/text/static DLL and unavailable-old-prefix migration."""
import json
from pathlib import Path
import shlex
import shutil
import struct
import tempfile
from io_utils import HERE, repo_file, sha, write_json
import metadata
import audit


def cmake_quote(s):
    return '"'+str(s).replace('\\','/').replace('"','\\"').replace(';','\\;')+'"'


def pc_flags(a,runner,folder,prefix):
    pc=a.root/'tools/bin/pkgconf'
    if not pc.is_file():raise ValueError('Native consumers require this full source-built pkgconf, not bootstrap')
    runner.env.update(PKG_CONFIG_LIBDIR=str(prefix/'lib/pkgconfig')+':'+str(prefix/'share/pkgconfig'),PKG_CONFIG_PATH='')
    rows={};lines=[]
    for name in metadata.PACKAGES:
        id=name.replace('-','_');parts={}
        for mode in ['cflags','libs']:
            raw=runner.run([pc,'--static','--'+mode,name],'pc-'+id+'-'+mode,encoding='latin1')
            values=[token.encode('latin1').decode('utf8') for token in shlex.split(raw)]
            for token in values:
                if token.startswith(('-I','-L')) and not (Path(token[2:]).resolve()==prefix.resolve() or prefix.resolve() in Path(token[2:]).resolve().parents):raise ValueError('PC source/old/system fallback path')
            parts[mode]=values;lines.append('set(PC_'+id+'_'+mode.upper()+' '+' '.join(cmake_quote(v) for v in values)+')')
        rows[name]=parts
    file=folder/'pc-flags.cmake';file.parent.mkdir(parents=True,exist_ok=True);file.write_text('\n'.join(lines)+'\n')
    write_json(folder/'pc-flags.json',{'source_built_pkgconf':str(pc),'source_built_pkgconf_sha256':sha(pc),'prefix':str(prefix),'single_package_flags':rows});return file


def woff2(a,runner,ttf,work):
    data=ttf.read_bytes();flavor,n=struct.unpack_from('>IH',data,0);directory=bytearray();raw=bytearray();sfntsize=12+16*n
    def base128(value):
        digits=[value&127];value>>=7
        while value:digits.append((value&127)|128);value>>=7
        return bytes(reversed(digits))
    for i in range(n):
        tag,checksum,offset,length=struct.unpack_from('>4sIII',data,12+16*i)
        if offset+length>len(data):raise ValueError('Malformed pinned SFNT')
        directory.append(63|(0xc0 if tag in (b'glyf',b'loca') else 0));directory.extend(tag);directory.extend(base128(length));raw.extend(data[offset:offset+length]);sfntsize+=(length+3)&~3
    packed=work/'sfnt-tables.bin';compressed=work/'sfnt-tables.brotli';packed.write_bytes(raw)
    runner.run([a.prefix/'bin/brotli','--quality=11','--output='+str(compressed),packed],'native-source-built-brotli-woff2')
    compressed_bytes=compressed.read_bytes();payload=48+len(directory)+len(compressed_bytes);padding=(-payload)%4;size=payload+padding
    header=struct.pack('>4sIIHHIIHHIIIII',b'wOF2',flavor,size,n,0,sfntsize,len(compressed_bytes),1,0,0,0,0,0,0)
    out=work/'locked-Roboto.woff2';out.write_bytes(header+directory+compressed_bytes+b'\0'*padding)
    return out,{'source_sha256':sha(ttf),'generated_sha256':sha(out),'tables':n,'size':size,'compressed_bytes':len(compressed_bytes),'alignment_padding':padding,'generator':'This full source-built genuine Brotli quality11'}


def configure(a,runner,preset,mode,prefix,folder,only_package=None):
    folder.mkdir(parents=True,exist_ok=True)
    flagsfile=pc_flags(a,runner,folder,prefix) if mode=='PC' else None
    command=[a.cmake,'-S',HERE/'consumers','-B',folder,'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(a.ninja),'-DCMAKE_TOOLCHAIN_FILE='+str(preset),
             '-DCMAKE_PROJECT_INCLUDE='+str(HERE/'cmake/native-host.cmake'),'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_PREFIX_PATH='+str(prefix),
             '-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON','-DCMAKE_FIND_USE_CMAKE_ENVIRONMENT_PATH=OFF','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
             '-DCMAKE_SKIP_RPATH:BOOL=ON',
             '-DCMAKE_JOB_POOLS=compile='+str(a.jobs)+';link=1','-DCMAKE_JOB_POOL_COMPILE=compile','-DCMAKE_JOB_POOL_LINK=link','-DMODE='+mode,
             '-DWHOLE_ARCHIVES='+';'.join(str(prefix/'lib'/name) for name in metadata.ARCHIVES),
             '-DPINNED_FONT='+str(repo_file(metadata.verify_fonts()[0]['fixture']))]
    if only_package:command+=['-DONLY_PACKAGE='+only_package]
    if flagsfile:command+=['-DPC_FLAGS_FILE='+str(flagsfile)]
    runner.run(command,'consumer-'+mode+'-configure');runner.run([a.cmake,'--build',folder,'--parallel',str(a.jobs)],'consumer-'+mode+'-build')
    compile_rows=json.loads((folder/'compile_commands.json').read_text())
    for row in compile_rows:
        # Installed public targets supply all library headers; consumer source
        # path and genuine SDK/compiler resource headers are naturally allowed.
        text=row.get('command',' '.join(row.get('arguments',[])))
        if str(a.root/'sources') in text or str(a.root/'build') in text:raise ValueError('Consumer leaks library source/build headers')
    return compile_rows


def consume(a,runner,preset,prefix,folder):
    fonts=metadata.verify_fonts();ttf,ligature,arabic=[repo_file(row['fixture']) for row in fonts]
    results={}
    independent={}
    for package in metadata.PACKAGES:
        build=folder/'independent-cmake'/package
        commands=configure(a,runner,preset,'CMAKE',prefix,build,only_package=package)
        executable=build/('package_'+package.replace('-','_'))
        row=audit.elf(executable,a,runner,'standalone-config-'+package)
        runner.run([executable],'standalone-config-'+package+'-actual',timeout=a.runtime_timeout)
        if package=='Imath':
            audit.elf(build/'imath_config_alias',a,runner,'standalone-Imath-Config-alias')
            runner.run([build/'imath_config_alias'],'standalone-Imath-Config-alias-actual',timeout=a.runtime_timeout)
        independent[package]={'CONFIG_loaded_alone':True,'ELF':row,'compile_commands':commands}
    results['independently_loaded_CONFIGs']=independent
    for mode in ['CMAKE','PC']:
        build=folder/mode.lower();commands=configure(a,runner,preset,mode,prefix,build)
        executables=[build/('package_'+name.replace('-','_')) for name in metadata.PACKAGES]+[build/'native-acceptance',build/'text-layout',build/'zstd-cover-concurrent',build/'module-owner',build/'base_bridge.so']
        elfrows=[audit.elf(file,a,runner,mode+'-elf-'+str(index)) for index,file in enumerate(executables)]
        for name in metadata.PACKAGES:runner.run([build/('package_'+name.replace('-','_'))],mode+'-package-'+name,timeout=a.runtime_timeout)
        cover_runs=[]
        for workers,iterations,level in [(2,6,0),(3,3,3)]:
            arguments=['--workers='+str(workers),'--iterations='+str(iterations),'--codec-workers=2','--optimizer-threads=2','--notification-level='+str(level)]
            coverout=runner.run([build/'zstd-cover-concurrent',*arguments],mode+'-actual-cover-'+str(workers)+'-workers',timeout=a.runtime_timeout)
            expected='PASS actual COVER trainers='+str(workers)+' branches=d<=8,d>8 iterations='+str(iterations)
            if 'SKIP ' in coverout or expected not in coverout or 'missing-dictionary+corrupt-frame rejection' not in coverout:
                raise ValueError('Actual concurrent COVER dictionary regression was not completed')
            cover_runs.append({'arguments':arguments,'stdout':coverout,'executable_sha256':sha(build/'zstd-cover-concurrent'),'actual_exit':0})
        with tempfile.TemporaryDirectory(prefix='base-native-fixtures-',dir=a.tmp_dir) as td:
            work=Path(td);wo,woinfo=woff2(a,runner,ttf,work)
            imageout=runner.run([build/'native-acceptance',work,ttf,wo,ligature,arabic],mode+'-real-images-fonts',timeout=a.runtime_timeout)
            if 'SKIP ' in imageout or 'ALL INSTALLED DEPENDENCY NATIVE ACCEPTANCE TESTS PASSED' not in imageout:raise ValueError('No native image/font/math acceptance SKIP allowed')
            layoutout=runner.run([build/'text-layout',ttf,arabic,work/'layout.pgm'],mode+'-integrated-text-layout',timeout=a.runtime_timeout)
            artifacts=[{'name':p.name,'sha256':sha(p),'size':p.stat().st_size} for p in work.glob('native-roundtrip.*')]+[{'name':'layout.pgm','sha256':sha(work/'layout.pgm'),'size':(work/'layout.pgm').stat().st_size}]
            archived=folder/'actual-fixtures'/mode.lower();archived.mkdir(parents=True,exist_ok=True)
            for p in [*work.glob('native-roundtrip.*'),work/'layout.pgm']:shutil.copyfile(p,archived/p.name)
        moduleout=runner.run([build/'module-owner',build/'base_bridge.so',ttf],mode+'-whole-static-module-owner',timeout=a.runtime_timeout)
        if 'SKIP ' in moduleout or 'PASS signed whole-archive static image/text/math MODULE dlopen/dlsym' not in moduleout or 'PASS MODULE COVER preexisting=2 internal=2 branches=d<=8,d>8 iterations=2' not in moduleout or 'all workers joined and owners destroyed before dlclose' not in moduleout:
            raise ValueError('Actual module-owned COVER TLS/owner regression was not completed')
        results[mode]={'ELFs':elfrows,'individual_public_packages':metadata.PACKAGES,'compile_commands':commands,'generated_woff2':woinfo,'real_artifacts':artifacts,
                       'image_font_math_stdout':imageout,'integrated_layout_stdout':layoutout,'private_temp_cleaned':not work.exists(),'whole_static_dlopen_opaque_owner':'PASS',
                       'concurrent_cover_training':cover_runs,'whole_static_module_stdout':moduleout,
                       'module_cover_preexisting_threads':2,'module_cover_internal_threads':2}
    return results


def run(a,runner,preset):
    preflight=a.root/'build/native-preflight'
    runner.run([a.cmake,'-S',HERE/'preflight','-B',preflight,'-G','Ninja','-DCMAKE_MAKE_PROGRAM='+str(a.ninja),'-DCMAKE_TOOLCHAIN_FILE='+str(preset),'-DCMAKE_PROJECT_INCLUDE='+str(HERE/'cmake/native-host.cmake')],'native-preflight-configure')
    runner.run([a.cmake,'--build',preflight,'--parallel',str(a.jobs)],'native-preflight-build')
    for file in [preflight/'native_probe',preflight/'affinity_probe']:
        audit.elf(file,a,runner,file.name+'-audit');runner.run([file],file.name+'-actual-execute',timeout=a.runtime_timeout)
    results=consume(a,runner,preset,a.prefix,a.root/'consumers')
    write_json(a.root/'native-validation.json',{'new_independent_native':'PASS','CMAKE_PC_consumers':results,'BlenderTextImage':'NOT_TESTED','HAP':'NOT_TESTED'});return results


def migrate(a,runner,preset):
    original=a.prefix;hidden=original.parent/(original.name+'.unavailable-owned');moved=a.root/'migration/迁移 libraries/source-built prefix'
    if hidden.exists() or moved.exists():raise ValueError('Refuse preexisting native migration tree')
    moved.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(original,moved,symlinks=True)
    before={p.relative_to(original).as_posix():sha(p) for p in original.rglob('*') if p.is_file() and not p.is_symlink()}
    after={p.relative_to(moved).as_posix():sha(p) for p in moved.rglob('*') if p.is_file() and not p.is_symlink()}
    if before!=after:raise ValueError('Migrated prefix must preserve every signed/file byte')
    original.rename(hidden)
    try:
        a.prefix=moved
        results=consume(a,runner,preset,moved,a.root/'migration/consumers 新 paths')
        for mode in ['CMAKE','PC']:
            for row in results[mode]['compile_commands']:
                text=row.get('command',' '.join(row.get('arguments',[])))
                if str(original) in text or str(hidden) in text:raise ValueError('Migrated consumer old prefix fallback')
    finally:a.prefix=original;hidden.rename(original)
    result={'result':'PASS actual moved Unicode/space CMAKE/PC/sourcebuilt module/image/text/math recompile/sign/execute','old_prefix_unavailable_during_consumers':True,
            'original_restored':original.is_dir(),'all_migrated_file_sha256_match':True,'consumers':results,'BlenderTextImage':'NOT_TESTED','HAP':'NOT_TESTED'}
    write_json(a.root/'migration-validation.json',result);return result
