# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh six-PIC-dependency and genuine unversioned CPython native recipe.
Sources/pins retain integration + accepted HAP profiles. Never clones compiled deps.
"""
import sys
sys.dont_write_bytecode=True
import json
from pathlib import Path
import shlex
import shutil
from source_guard import HERE,sha
from source_tree import verify_prepared
from io_utils import dump
from toolchain import launchers,sign_file
from configure_contract import validate as validate_configure

CONFIGURE=['--build=aarch64-unknown-linux-ohos','--host=aarch64-unknown-linux-ohos','--enable-shared','--with-ensurepip=no',
           '--with-openssl-rpath=no','--with-pkg-config=no','--with-readline=no','--with-system-expat=no','--with-system-libmpdec=no','--enable-experimental-jit=no']
DEPS=[('openssl','openssl-3.5.8'),('libffi','libffi-3.5.2'),('xz','xz-5.8.4'),('sqlite','sqlite-autoconf-3510200'),('bzip2','bzip2-1.0.8'),('zlib','zlib-1.3.1')]


def plan(args,root):
    root=Path(root);source=root/'work/Python-3.13.13';objects=root/'cpython';prefix=root/'runtime';deps=root/'deps'
    return {'schema':1,'profile':'CPython3.13.13/regular GIL/shared true libpython3.13.so/71 extensions + interpreter + shim',
        'execution':'NOTRUN','source_to_mutable_clone':[{'from':str(root/'sources'/directory),'to':str(root/'work'/directory)} for _,directory in DEPS]+[{'from':str(root/'sources/Python-3.13.13'),'to':str(source)}],
        'dependency_profile':'six fresh source-built static PIC dependencies; seven archive outputs including ssl+crypto',
        'native_probe':['<own signed CC>','-O2',str(HERE/'python/helpers/float-probe.c'),'-o',str(root/'probes/float-probe')],
        'measured_configure_exception':'ax_cv_c_float_words_bigendian derived ONLY after own fresh signed probe JSON; no inherited/copy cache',
        'configure':['/usr/bin/sh','../work/Python-3.13.13/configure',*CONFIGURE,'--prefix='+str(prefix),'--with-openssl='+str(deps)],
        'build':[str(args.make),'-j'+str(args.jobs)],'install':[str(objects/'python'),str(HERE/'python/helpers/install.py'),str(source),str(objects),str(prefix)],
        'regen':'explicit pinned native3.13.13 PYTHON_FOR_REGEN, only source generation;3.14 orchestration never target headers/sysconfig',
        'link_threads':1,'jobs':args.jobs,'generated_headers':'own fresh configure/build only','SONAME':'source selected before linking/signing, no post-sign rename'}


def dependency_commands(args,work,deps,env):
    result=[];make=str(args.make);jobs='-j'+str(args.jobs)
    for name,directory in DEPS:
        cwd=work/directory
        if name=='openssl':
            commands=[[args.perl,'Configure','ohos-aarch64','no-shared','no-tests','no-module','--prefix='+str(deps),'--openssldir=/etc/ssl'],[make,jobs,'build_sw'],[make,'install_dev']]
        elif name in ('libffi','xz'):
            options=['--disable-shared','--enable-static','--disable-dependency-tracking']
            options+=['--disable-docs'] if name=='libffi' else ['--disable-nls','--disable-xz','--disable-xzdec','--disable-lzmadec','--disable-lzmainfo','--disable-scripts','--disable-doc']
            commands=[['/usr/bin/sh','configure','--build=aarch64-unknown-linux-ohos','--host=aarch64-unknown-linux-ohos','--prefix='+str(deps),*options],[make,jobs],[make,'install']]
        elif name=='sqlite':commands=[[root_cc(env),'-O2','-fPIC','-D__MUSL__','-DSQLITE_THREADSAFE=1','-DSQLITE_ENABLE_FTS5','-DSQLITE_ENABLE_RTREE','-DSQLITE_ENABLE_COLUMN_METADATA','-c','sqlite3.c','-o','sqlite3.o'],[args.ar,'rcs','libsqlite3.a','sqlite3.o']]
        elif name=='bzip2':commands=[[make,jobs,'libbz2.a','CC='+env['CC'],'CFLAGS=-O2 -fPIC','AR='+str(args.ar),'RANLIB='+str(args.ranlib)]]
        else:commands=[['/usr/bin/sh','configure','--static','--prefix='+str(deps)],[make,jobs,'libz.a']]
        result.append((name,cwd,commands))
    return result


def root_cc(env):
    parts=shlex.split(env['CC'])
    if len(parts)!=1:raise ValueError('Own CC launcher path expected')
    return parts[0]


def build(args,runner,root):
    root=Path(root);verify_prepared(root,'python');work=root/'work';deps=root/'deps';objects=root/'cpython';prefix=root/'runtime'
    if any(p.exists() for p in (work,deps,objects,prefix,root/'launchers')):raise ValueError('Python full requires wholly fresh native subtrees')
    work.mkdir();(deps/'include').mkdir(parents=True);(deps/'lib').mkdir();(root/'probes').mkdir()
    for _,directory in DEPS:shutil.copytree(root/'sources'/directory,work/directory,symlinks=True)
    env=launchers(args,root,runner.env);runner.env=env
    for name,cwd,commands in dependency_commands(args,work,deps,env):
        for n,command in enumerate(commands):runner.run(command,'python-dep-'+name+'-'+str(n),cwd=cwd)
        if name=='sqlite':
            for file in ('sqlite3.h','sqlite3ext.h'):shutil.copyfile(cwd/file,deps/'include'/file)
            shutil.copyfile(cwd/'libsqlite3.a',deps/'lib/libsqlite3.a')
        elif name=='bzip2':
            shutil.copyfile(cwd/'bzlib.h',deps/'include/bzlib.h');shutil.copyfile(cwd/'libbz2.a',deps/'lib/libbz2.a')
        elif name=='zlib':
            for file in ('zlib.h','zconf.h'):shutil.copyfile(cwd/file,deps/'include'/file)
            shutil.copyfile(cwd/'libz.a',deps/'lib/libz.a')
            # Metadata derives from its installed location, not an upstream unquoted install recipe.
            pkg=deps/'lib/pkgconfig';pkg.mkdir(exist_ok=True)
            (pkg/'zlib.pc').write_text('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\nName: zlib\nDescription: own-source zlib\nVersion: 1.3.1\nLibs: -L${libdir} -lz\nCflags: -I${includedir}\n')
    archives=['libssl.a','libcrypto.a','libffi.a','liblzma.a','libsqlite3.a','libbz2.a','libz.a']
    if any(not (deps/'lib'/a).is_file() for a in archives):raise RuntimeError('Incomplete own-source PIC closure')
    deps_receipt={'schema':1,'origin':'own-source rebuilt, no accepted compiled-prefix borrowing','sources_lock_sha256':sha(HERE/'sources.lock.json'),
        'archives':[{'path':'lib/'+a,'sha256':sha(deps/'lib'/a),'size':(deps/'lib'/a).stat().st_size} for a in archives],
        'PIC':'explicit -fPIC in every source build environment','actual_commands':'logs/python-dep-*','runtime_dependencies':['libc.so']}
    for a in archives:
        headers=runner.run([args.readelf,'--file-header',deps/'lib'/a],'python-dep-arch-'+a)
        if 'AArch64' not in headers or any('AArch64' not in line for line in headers.splitlines() if 'Machine:' in line):raise ValueError('Static archive contains non-AArch64 object')
    dump(root/'receipts/dependency-origin.json',deps_receipt)
    source=work/'Python-3.13.13';shutil.copytree(root/'sources/Python-3.13.13',source,symlinks=True);objects.mkdir()
    probe=root/'probes/float-probe'
    runner.run([root_cc(env),'-O2',HERE/'python/helpers/float-probe.c','-o',probe],'python-native-float-build')
    measurement=json.loads(runner.run([probe],'python-native-float-run'))
    if measurement!={'little_endian':True,'pointer_bytes':8,'double_bytes':'000000000000f03f'}:raise ValueError('Actual signed native double/pointer representation differs')
    dump(root/'receipts/native-float-probe.json',{'measurement':measurement,'tool_lock_sha256':sha(HERE/'tools.lock.json'),'probe_sha256':sha(probe)})
    # This one measured Autoconf exception preserves the accepted recipe. No constants
    # are copied from old pyconfig/config.cache; the own probe above must execute first.
    env.update(CFLAGS='-O2 -fPIC',CPPFLAGS='-I'+shlex.quote(str(deps/'include'))+' -D__MUSL__',
       LDFLAGS='-L'+shlex.quote(str(deps/'lib'))+" -Wl,-rpath,'$$ORIGIN/../lib' -Wl,-rpath,'$$ORIGIN/../..' -Wl,-rpath,'$$ORIGIN'",
       LIBFFI_CFLAGS='-I'+shlex.quote(str(deps/'include')),LIBFFI_LIBS='-L'+shlex.quote(str(deps/'lib'))+' -lffi',
       LIBSQLITE3_CFLAGS='-I'+shlex.quote(str(deps/'include')),LIBSQLITE3_LIBS='-L'+shlex.quote(str(deps/'lib'))+' -lsqlite3',
       OHOS_PYTHON_UNVERSIONED_SONAME='1',ax_cv_c_float_words_bigendian='no' if measurement['double_bytes']=='000000000000f03f' else 'yes')
    runner.env=dict(env)
    command=plan(args,root)['configure'];runner.run(command,'python-configure',cwd=objects)
    config=(root/'logs/python-configure.log').read_text()
    if (objects/'config.cache').exists():raise ValueError('No copied/reused config cache permitted')
    makefile=(objects/'Makefile').read_text()
    contract=validate_configure(config,makefile,args.regen_python,runner.env)
    dump(root/'receipts/native-configure-generator-binding.json',{'schema':1,'actual_native_configure_and_generated_Makefile':contract,
          'configure_log_sha256':sha(root/'logs/python-configure.log'),'generated_Makefile_sha256':sha(objects/'Makefile'),
          'pinned_generator_binary_sha256':sha(args.regen_python),'tool_lock_sha256':sha(HERE/'tools.lock.json'),
          'target_build':'NOTRUN until subsequent actual make succeeds'})
    runner.run([args.make,'-j'+str(args.jobs)],'python-build',cwd=objects)
    runner.run([objects/'python',HERE/'python/helpers/install.py',source,objects,prefix],'python-install',cwd=objects,extra_env={'LD_LIBRARY_PATH':str(objects)})
    from python_finalize import finalize
    finalize(args,root)
    verify_prepared(root,'python')
    return {'schema':1,'source_native_build':'PASS actual commands only','source_lock_sha256':sha(HERE/'sources.lock.json'),
            'dependency_origin_sha256':sha(root/'receipts/dependency-origin.json'),'native_probe_sha256':sha(root/'receipts/native-float-probe.json'),
            'native_configure_generator_binding_sha256':sha(root/'receipts/native-configure-generator-binding.json'),
             'runtime':'runtime','genuine_SONAME':'libpython3.13.so','HAP':'NOTRUN'}
