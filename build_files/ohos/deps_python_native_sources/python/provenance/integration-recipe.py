#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable fixed-source CPython3.13 OHOS recipe; never downloads in build.
Final location: build_files/ohos/python/recipe.py. Integration staging callers
must pass --repo-root. Inputs come exclusively from tpr archive-part manifests.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
LOCK=json.loads((HERE/'sources.lock.json').read_text())


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def load_vendor(repo):
    path=repo/'build_files/ohos/vendor_archive.py'
    spec=importlib.util.spec_from_file_location('ohos_vendor_archive',path)
    if spec is None or spec.loader is None:raise ValueError('Missing repository vendor_archive.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def verify_recipe():
    seal=json.loads((HERE/'recipe.files.json').read_text())
    for relative,digest in seal.items():
        if sha(HERE/relative)!=digest:raise ValueError('Recipe input changed: '+relative)
    return hashlib.sha256(json.dumps(seal,sort_keys=True).encode()).hexdigest()


def registry(repo,vendor):
    records={}
    for expected in LOCK['sources']:
        directory=repo/LOCK['source_registry']/expected['registry_id']
        observed=vendor.verify(directory)
        for key in ('name','version','filename','size','sha256','url','license'):
            if observed.get(key)!=expected[key]:raise ValueError(f'Fixed registry mismatch {expected["registry_id"]}: {key}')
        records[expected['name']]=(expected,directory)
    return records


def safe_extract(archive,parent,root_name):
    """Inspect all members first; manually copy hardlinks as regular data files."""
    parent=Path(parent)
    with tarfile.open(archive) as tar:
        members=tar.getmembers();seen=set()
        for member in members:
            path=PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or '..' in path.parts or path.parts[0]!=root_name:
                raise ValueError('Unsafe archive path: '+member.name)
            normalized=posixpath.normpath(member.name)
            if normalized in seen and not member.isdir():raise ValueError('Duplicate archive file: '+member.name)
            seen.add(normalized)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError('Archive special file rejected: '+member.name)
            if member.issym() or member.islnk():
                base=posixpath.dirname(member.name) if member.issym() else ''
                target=posixpath.normpath(posixpath.join(base,member.linkname))
                if member.linkname.startswith('/') or not (target==root_name or target.startswith(root_name+'/')):
                    raise ValueError('Escaping archive link: '+member.name)
        # All directories/files first, then links. Never follow an archive symlink
        # while writing another member, and never depend on host hardlink support.
        for member in members:
            destination=parent/member.name
            if member.isdir():destination.mkdir(parents=True,exist_ok=True)
            elif member.isfile() or member.islnk():
                destination.parent.mkdir(parents=True,exist_ok=True)
                source=tar.extractfile(member)
                if source is None:raise ValueError('Unreadable archive file: '+member.name)
                with source,destination.open('xb') as output:shutil.copyfileobj(source,output)
                destination.chmod(member.mode & 0o777)
        for member in members:
            if member.issym():
                destination=parent/member.name;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.symlink_to(member.linkname)
    return parent/root_name


def tree_manifest(root):
    result={}
    for file in sorted(Path(root).rglob('*')):
        relative=str(file.relative_to(root))
        if file.is_symlink():result[relative]={'link':os.readlink(file)}
        elif file.is_file():result[relative]={'sha256':sha(file),'executable':bool(file.stat().st_mode&0o111)}
        elif file.is_dir():result[relative]={'directory':True}
        else:raise ValueError('Unsupported source-tree object: '+relative)
    return result


def apply_patch(source,name):
    patch=HERE/'patches'/name
    subprocess.run(['git','apply','--check',str(patch)],cwd=source,check=True)
    subprocess.run(['git','apply',str(patch)],cwd=source,check=True)


def patch_sources(sources,flavor):
    cpython=sources/'Python-3.13.13'
    apply_patch(cpython,'cpython-ohos.patch')
    if flavor=='hap-unversioned':apply_patch(cpython,'cpython-unversioned-soname.patch')
    ffi=sources/'libffi-3.5.2';apply_patch(ffi,'libffi-tramp-ohos.patch')
    for root in (ffi,sources/'xz-5.8.4'):
        for relative in ('config.sub','build-aux/config.sub'):
            file=root/relative
            if file.is_file():shutil.copyfile(cpython/'config.sub',file)
    openssl=sources/'openssl-3.5.8'
    config=openssl/'Configurations/99-ohos.conf'
    if config.exists():raise ValueError('Unexpected prepatched OpenSSL OHOS target')
    config.write_text('my %targets = (\n  "ohos-aarch64" => {\n    inherit_from => ["linux-aarch64"],\n    CC => "clang", CXX => "clang++",\n    cppflags => add("-D__OHOS__ -D__MUSL__"),\n  },\n);\n')
    file=openssl/'crypto/x509/x509_def.c';text=file.read_text();old='return X509_CERT_FILE;'
    if text.count(old)!=1:raise ValueError('OpenSSL CA edit upstream context mismatch')
    file.write_text(text.replace(old,'return "/etc/ssl/certs/cacert.pem";',1))


def private_build(path):
    root=Path(os.environ['XDG_CACHE_HOME']).resolve();path=Path(path).absolute()
    if path.is_symlink() or root not in path.resolve().parents:raise ValueError('Build root must be a new private XDG_CACHE_HOME subdirectory')
    return path


def prepare(repo,build,vendor,records,flavor,fingerprint):
    owner=build/'recipe-state.json'
    identity={'recipe':fingerprint,'flavor':flavor,'sources':{name:e['sha256'] for name,(e,d) in records.items()}}
    if owner.exists():
        previous=json.loads(owner.read_text())
        if previous['identity']!=identity:raise ValueError('Existing build root belongs to different recipe/flavor/inputs; use fresh root')
        verify_source(build,identity)
        return previous
    if build.exists() and any(build.iterdir()):raise ValueError('Refusing unowned existing build cache')
    build.mkdir(parents=True,exist_ok=True);archives=build/'archives';archives.mkdir()
    for name,(entry,directory) in records.items():vendor.materialize(directory,archives/entry['filename'])
    with tempfile.TemporaryDirectory(prefix='cpython-source-stage-',dir=os.environ['TMPDIR']) as td:
        stage=Path(td)/'sources';stage.mkdir()
        for entry,directory in records.values():safe_extract(archives/entry['filename'],stage,entry['directory'])
        patch_sources(stage,flavor)
        for entry,directory in records.values():
            for notice in entry['license_files']:
                if not (stage/entry['directory']/notice).is_file():raise ValueError('Missing retained license: '+notice)
        manifests={entry['name']:tree_manifest(stage/entry['directory']) for entry,directory in records.values()}
        shutil.copytree(stage,build/'sources',symlinks=True)
    state={'identity':identity,'source_manifests':manifests,'note':'Immutable patched input trees; all build mutation goes to separately cloned work trees'}
    owner.write_text(json.dumps(state,sort_keys=True,indent=2)+'\n')
    return state


def verify_source(build,identity=None):
    state=json.loads((build/'recipe-state.json').read_text())
    if identity is not None and state['identity']!=identity:raise ValueError('Recipe/source identity mismatch')
    for entry in LOCK['sources']:
        actual=tree_manifest(build/'sources'/entry['directory'])
        if actual!=state['source_manifests'][entry['name']]:raise ValueError('Prepared source modified/added/deleted: '+entry['name'])
    return state


def resolve_tools(args,build):
    sdk=args.sdk_root or os.environ.get('OHOS_SDK_ROOT')
    if not sdk:
        candidate=Path.home()/'.oheco/packages/ohos-sdk-native'/LOCK['external_build_tools']['sdk']['version']
        if candidate.is_dir():sdk=str(candidate)
    if not sdk:raise ValueError('Pass --sdk-root/OHOS_SDK_ROOT or install pinned oheco ohos-sdk-native')
    sdk=Path(sdk).resolve();cc=sdk/'llvm/bin/clang';cxx=sdk/'llvm/bin/clang++'
    if not cc.is_file() or not cxx.is_file():raise ValueError('SDK LLVM compiler absent')
    make=args.make or os.environ.get('OHOS_MAKE') or shutil.which('make')
    if not make:raise ValueError('Pass --make/OHOS_MAKE: native GNU Make4.4.1 required, never a workspace tmux fallback')
    version=subprocess.check_output([make,'--version'],text=True)
    if not version.startswith('GNU Make 4.4.1'):raise ValueError('Pinned GNU Make4.4.1 required')
    macros=subprocess.check_output([str(cc),'--target=aarch64-unknown-linux-ohos','--sysroot='+str(sdk/'sysroot'),'-dM','-E','-x','c','/dev/null'],text=True)
    if '#define __OHOS__ 1' not in macros or '#define __aarch64__ 1' not in macros:raise ValueError('SDK is not true OHOS ARM64 compiler')
    compiler_version=subprocess.check_output([str(cc),'--version'],text=True)
    if 'clang version 15.0.4' not in compiler_version:raise ValueError('Pinned SDK clang15.0.4 required; use a new audited recipe for another toolchain')
    if not shutil.which('binary-sign-tool'):raise ValueError('binary-sign-tool missing from PATH (oheco ohos-sdk-toolchains prerequisite)')
    perl=args.perl or os.environ.get('OHOS_PERL')
    env=os.environ.copy()
    for key in ('LD_LIBRARY_PATH','LD_PRELOAD','PYTHONHOME','PYTHONPATH'):env.pop(key,None)
    if not perl and args.perl_archive:
        tool=LOCK['external_build_tools']['perl'];archive=Path(args.perl_archive)
        if archive.stat().st_size!=tool['size'] or sha(archive)!=tool['sha256']:raise ValueError('External native Perl archive hash/size mismatch')
        directory=build/'external-tools';directory.mkdir(exist_ok=True)
        destination=directory/tool['directory']
        if not destination.exists():safe_extract(archive,directory,tool['directory'])
        perl=str(destination/'bin/perl')
        env['PERL5LIB']=f'{destination}/lib/5.44.0:{destination}/lib/5.44.0/aarch64-linux'
        # These are our newly extracted external tool inodes, not user installs.
        for file in [Path(perl),*destination.rglob('*.so')]:sign_file(file,env)
    if not perl:raise ValueError('Pass --perl/OHOS_PERL or fixed --perl-archive; binary Perl is an external build tool, not runtime source')
    perl_version=subprocess.check_output([perl,'-e','print $^V'],env=env,text=True)
    if perl_version!='v5.44.0':raise ValueError('Pinned native Perl5.44.0 required')
    env['PATH']=str(sdk/'llvm/bin')+':'+str(Path(make).parent)+':'+str(Path(perl).parent)+':'+env['PATH']
    tools={'sdk':str(sdk),'cc':str(cc),'cxx':str(cxx),'make':str(Path(make).resolve()),'perl':str(Path(perl).resolve()),'compiler_version':compiler_version,
           'make_version':version.splitlines()[0],'perl_version':perl_version,'sources':LOCK['external_build_tools']}
    (build/'toolchain.json').write_text(json.dumps(tools,indent=2)+'\n')
    return tools,env


def sign_file(file,env):
    with tempfile.TemporaryDirectory(prefix='cpython-final-sign-',dir=os.environ['TMPDIR']) as td:
        signed=Path(td)/'signed'
        subprocess.run(['binary-sign-tool','sign','-inFile',str(file),'-outFile',str(signed),'-selfSign','1'],env=env,check=True,stdout=subprocess.DEVNULL)
        sections=subprocess.check_output(['llvm-readelf','--sections',str(signed)],env=env,text=True)
        if '.codesign' not in sections:raise ValueError('Signer produced no .codesign')
        file.unlink();shutil.copyfile(signed,file);file.chmod(0o755)


def run(command,cwd,env,log):
    with log.open('ab') as stream:
        stream.write(('$ '+shlex.join(list(map(str,command)))+'\n').encode());stream.flush()
        subprocess.run(list(map(str,command)),cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)


def build_all(args,build,tools,env):
    if platform.system() not in ('HarmonyOS','OHOS','OpenHarmony') or platform.machine()!='aarch64':raise ValueError('Native OHOS ARM64 build required')
    os.setpriority(os.PRIO_PROCESS,0,10)
    deps=build/'deps';work=build/'work';launch=build/'launchers';logs=build/'logs'
    for path in (deps/'lib',deps/'include',work,launch,logs):path.mkdir(parents=True,exist_ok=True)
    sdk=Path(tools['sdk']);python=sys.executable
    for name,compiler,flags in [('cc',tools['cc'],[]),('cxx',tools['cxx'],['-static-libstdc++'])]:
        command=[python,str(HERE/'helpers/sign_compiler.py'),compiler,'--target=aarch64-unknown-linux-ohos','--sysroot='+str(sdk/'sysroot'),'-fuse-ld=lld','-Wl,--threads=1',*flags,'--']
        file=launch/name;file.write_text('#!/usr/bin/sh\nexec '+shlex.join(command)+' "$@"\n');file.chmod(0o755)
    env.update(CC=str(launch/'cc'),CXX=str(launch/'cxx'),AR=str(sdk/'llvm/bin/llvm-ar'),RANLIB=str(sdk/'llvm/bin/llvm-ranlib'),NM=str(sdk/'llvm/bin/llvm-nm'),LD=str(sdk/'llvm/bin/ld.lld'),
               CFLAGS='-O2 -fPIC -D__MUSL__',CPPFLAGS='-D__MUSL__',CONFIG_SHELL='/usr/bin/sh',LC_ALL='C',PYTHONDONTWRITEBYTECODE='1')
    probe=build/'float-probe'
    run([launch/'cc','-O2',HERE/'helpers/float-probe.c','-o',probe],build,env,logs/'float-probe-build.log')
    measurement=json.loads(subprocess.check_output([str(probe)],env=env,text=True))
    if measurement!={'little_endian':True,'pointer_bytes':8,'double_bytes':'000000000000f03f'}:
        raise ValueError('Actual signed native float/pointer probe does not support the ARM64 configure override')
    (build/'native-float-probe.json').write_text(json.dumps(measurement,indent=2)+'\n')
    make=tools['make'];jobs='-j2'
    for entry in LOCK['sources']:
        if entry['role']!='runtime':continue
        name=entry['name'];source=build/'sources'/entry['directory'];target=work/entry['directory']
        if target.exists():raise ValueError('Mutable work tree already exists; use a fresh root rather than silently resume patched inputs')
        shutil.copytree(source,target,symlinks=True);log=logs/(name+'.log')
        if name=='openssl':
            run([tools['perl'],'Configure','ohos-aarch64','no-shared','no-tests','no-module',f'--prefix={deps}','--openssldir=/etc/ssl'],target,env,log)
            run([make,jobs,'build_sw'],target,env,log);run([make,'install_dev'],target,env,log)
        elif name in ('libffi','xz'):
            options=['--disable-shared','--enable-static','--disable-dependency-tracking']
            options+=['--disable-docs'] if name=='libffi' else ['--disable-nls','--disable-xz','--disable-xzdec','--disable-lzmadec','--disable-lzmainfo','--disable-scripts','--disable-doc']
            run(['/usr/bin/sh','configure','--build=aarch64-unknown-linux-ohos','--host=aarch64-unknown-linux-ohos',f'--prefix={deps}',*options],target,env,log)
            run([make,jobs],target,env,log);run([make,'install'],target,env,log)
        elif name=='sqlite':
            run([tools['cc'],'-O2','-fPIC','-D__MUSL__','-DSQLITE_THREADSAFE=1','-DSQLITE_ENABLE_FTS5','-DSQLITE_ENABLE_RTREE','-DSQLITE_ENABLE_COLUMN_METADATA','-c','sqlite3.c','-o','sqlite3.o'],target,env,log)
            run([env['AR'],'rcs','libsqlite3.a','sqlite3.o'],target,env,log)
            for file in ('sqlite3.h','sqlite3ext.h'):shutil.copyfile(target/file,deps/'include'/file)
            shutil.copyfile(target/'libsqlite3.a',deps/'lib/libsqlite3.a')
        elif name=='bzip2':
            run([make,jobs,'libbz2.a','CC='+env['CC'],'CFLAGS=-O2 -fPIC','AR='+env['AR'],'RANLIB='+env['RANLIB']],target,env,log)
            shutil.copyfile(target/'bzlib.h',deps/'include/bzlib.h');shutil.copyfile(target/'libbz2.a',deps/'lib/libbz2.a')
        elif name=='zlib':
            run(['/usr/bin/sh','configure','--static',f'--prefix={deps}'],target,env,log);run([make,jobs],target,env,log);run([make,'install'],target,env,log)
    verify_source(build)
    source=work/'Python-3.13.13';shutil.copytree(build/'sources/Python-3.13.13',source,symlinks=True)
    objects=build/'cpython';objects.mkdir();prefix=build/'runtime'
    env.update(CFLAGS='-O2 -fPIC',CPPFLAGS=f'-I{deps}/include -D__MUSL__',LDFLAGS=f"-L{deps}/lib -Wl,-rpath,'$$ORIGIN/../lib' -Wl,-rpath,'$$ORIGIN/../..'",
      LIBFFI_CFLAGS=f'-I{deps}/include',LIBFFI_LIBS=f'-L{deps}/lib -lffi',LIBSQLITE3_CFLAGS=f'-I{deps}/include',LIBSQLITE3_LIBS=f'-L{deps}/lib -lsqlite3',ax_cv_c_float_words_bigendian='no')
    if args.flavor=='hap-unversioned':env['OHOS_PYTHON_UNVERSIONED_SONAME']='1'
    run(['/usr/bin/sh',source/'configure','--build=aarch64-unknown-linux-ohos','--host=aarch64-unknown-linux-ohos',f'--prefix={prefix}',
      '--enable-shared','--with-ensurepip=no',f'--with-openssl={deps}','--with-openssl-rpath=no','--with-pkg-config=no','--with-readline=no','--with-system-expat=no','--with-system-libmpdec=no','--enable-experimental-jit=no'],objects,env,logs/'configure.log')
    run([make,jobs],objects,env,logs/'build.log')
    runtime_env=env.copy();runtime_env['LD_LIBRARY_PATH']=str(objects)
    run([objects/'python',HERE/'helpers/install.py',source,objects,prefix],objects,runtime_env,logs/'install.log')
    run([python,HERE/'helpers/finalize.py',prefix,source,deps,HERE/'helpers'],objects,env,logs/'finalize.log')
    run([python,HERE/'helpers/verify.py',prefix,build,HERE/'helpers',launch/'cc'],objects,env,logs/'verify.log')
    verify_source(build)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=HERE.parents[2]);parser.add_argument('--build-root',type=Path)
    parser.add_argument('--flavor',choices=['cli','hap-unversioned'],default='cli');parser.add_argument('--offline',action='store_true')
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--plan',action='store_true');mode.add_argument('--prepare',action='store_true');mode.add_argument('--verify-source',action='store_true');mode.add_argument('--build',action='store_true')
    parser.add_argument('--sdk-root',type=Path);parser.add_argument('--make');parser.add_argument('--perl');parser.add_argument('--perl-archive',type=Path)
    args=parser.parse_args()
    if not args.offline:raise ValueError('This recipe requires --offline; source downloads are a separate preparation task')
    fingerprint=verify_recipe();repo=args.repo_root.resolve();vendor=load_vendor(repo);records=registry(repo,vendor)
    build=private_build(args.build_root or Path(os.environ['XDG_CACHE_HOME'])/f'blender-cpython-3.13.13-{args.flavor}-recipe')
    if args.plan:
        print(json.dumps({'offline':True,'repo_root':str(repo),'build_root':str(build),'flavor':args.flavor,'source_inputs':[e for e,d in records.values()],
          'external_build_tools':LOCK['external_build_tools'],'patch_order':LOCK['patch_order'],'source_registry_verified':True,'build_started':False},indent=2));return
    if args.verify_source:
        identity={'recipe':fingerprint,'flavor':args.flavor,'sources':{name:e['sha256'] for name,(e,d) in records.items()}}
        verify_source(build,identity);print(json.dumps({'verified_source':True,'build_started':False,'flavor':args.flavor}));return
    state=prepare(repo,build,vendor,records,args.flavor,fingerprint)
    if args.build:
        with (build/'.build.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            tools,env=resolve_tools(args,build);build_all(args,build,tools,env)
    print(json.dumps({'prepared':True,'build_started':args.build,'source_trees':len(state['source_manifests']),'flavor':args.flavor}));


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,AssertionError,subprocess.CalledProcessError,tarfile.TarError) as error:
        print('cpython-recipe: '+str(error),file=sys.stderr);sys.exit(1)
