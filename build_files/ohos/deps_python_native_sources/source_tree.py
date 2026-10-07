# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-group pristine inventories, exact reversible patches, immutable source inputs."""
import sys
sys.dont_write_bytecode=True
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from source_guard import HERE,REPO,sha,inventory,reconstruct,extract,repo_file
from io_utils import dump,owned,temporary_root,clean_env


def git_env(root):
    env=clean_env(root)
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_SYSTEM='/dev/null',GIT_CEILING_DIRECTORIES=str(root))
    return env


def lock():return json.loads((HERE/'sources.lock.json').read_text())


def entries(group):return [s for s in lock()['sources'] if s['group']==group]


def patch_python(stage,git):
    python=stage/'Python-3.13.13';ffi=stage/'libffi-3.5.2';openssl=stage/'openssl-3.5.8'
    for root,name in ((python,'cpython-ohos.patch'),(python,'cpython-unversioned-soname.patch'),(ffi,'libffi-tramp-ohos.patch')):
        patch=HERE/'python/patches'/name
        for argv in ([git,'apply','--check',str(patch)],[git,'apply',str(patch)],[git,'apply','--reverse','--check',str(patch)]):
            subprocess.run(argv,cwd=root,check=True,capture_output=True,env=git_env(root))
    for root in (ffi,stage/'xz-5.8.4'):
        for relative in ('config.sub','build-aux/config.sub'):
            path=root/relative
            if path.is_file():shutil.copyfile(python/'config.sub',path)
    config=openssl/'Configurations/99-ohos.conf'
    if config.exists():raise ValueError('Unexpected original OpenSSL platform config')
    config.write_text('my %targets = (\n  "ohos-aarch64" => {\n    inherit_from => ["linux-aarch64"],\n    CC => "clang", CXX => "clang++",\n    cppflags => add("-D__OHOS__ -D__MUSL__"),\n  },\n);\n')
    file=openssl/'crypto/x509/x509_def.c';text=file.read_text();old='return X509_CERT_FILE;'
    if text.count(old)!=1:raise ValueError('OpenSSL original exact CA context mismatch')
    file.write_text(text.replace(old,'return "/etc/ssl/certs/cacert.pem";',1))
    # GNUmake adaptation is copied as a separate minimal patch; never a tmux tree.
    make=stage/'make-4.4.1';patch=HERE/'python/patches/make-config-sub-ohos.patch'
    if patch.exists():
        for argv in ([git,'apply','--check',str(patch)],[git,'apply',str(patch)],[git,'apply','--reverse','--check',str(patch)]):subprocess.run(argv,cwd=make,check=True,capture_output=True,env=git_env(make))


def patch_group(stage,group,git):
    if group=='python':patch_python(stage,git)
    elif group=='numpy':
        source=stage/'numpy-2.3.4';patch=HERE/'numpy/patches/numpy-vendored-meson-ohos.patch'
        for argv in ([git,'apply','--check',str(patch)],[git,'apply',str(patch)],[git,'apply','--reverse','--check',str(patch)]):subprocess.run(argv,cwd=source,check=True,capture_output=True,env=git_env(source))
    elif group=='tools':
        source=stage/'perl5-5.44.0'
        patch=stage/'ohos-perl-bb0517e7437015b24025438084aed0661fb80553/0001-add-ohos-support.patch'
        for argv in ([git,'apply','--check',str(patch)],[git,'apply',str(patch)],[git,'apply','--reverse','--check',str(patch)]):subprocess.run(argv,cwd=source,check=True,capture_output=True,env=git_env(source))
    else:raise ValueError('Unknown source group')


def verify_prepared(root,group):
    root=owned(root);state_file=root/'receipts'/f'prepared-{group}.json'
    state=json.loads(state_file.read_text())
    if state['sources_lock_sha256']!=sha(HERE/'sources.lock.json'):raise ValueError('Prepared source lock drift')
    selected=entries(group)
    if set(state['sources'])!={s['registry_id'] for s in selected}:raise ValueError('Source group closure differs')
    for source in selected:
        original=source['sha256'];archive=root/'archives'/source['filename'];path=root/'sources'/source['directory']
        expected=json.loads(repo_file(source['patched_inventory']).read_text())
        if not archive.is_file() or archive.is_symlink() or sha(archive)!=original or inventory(path)!=expected:
            raise ValueError('Prepared original/archive/patched source tampered: '+source['registry_id'])
        if state['sources'][source['registry_id']]!=sha(repo_file(source['patched_inventory'])):raise ValueError('Prepared inventory binding drift')
    return state


def prepare(root,group,tmp,git):
    root=owned(root,create=True);tmp=temporary_root(tmp);selected=entries(group)
    tool=json.loads((HERE/'tools.lock.json').read_text())['tools']['git'];program=Path(git)
    if not program.is_absolute() or program.stat().st_size!=tool['size'] or sha(program)!=tool['sha256']:raise ValueError('Exact explicit Git patch tool bytes required')
    if not selected:raise ValueError('Source group is not available in this candidate')
    receipt=root/'receipts'/f'prepared-{group}.json'
    if receipt.exists():return verify_prepared(root,group)
    if any((root/'sources'/s['directory']).exists() or (root/'archives'/s['filename']).exists() for s in selected):raise ValueError('Partial group exists; use a fresh root')
    with tempfile.TemporaryDirectory(prefix='python-native-source-'+group+'-',dir=tmp) as td:
        stage=Path(td);trees=stage/'sources';trees.mkdir();archives=stage/'archives';archives.mkdir()
        for s in selected:
            archive=archives/s['filename'];reconstruct(s,archive);target=trees/s['directory']
            extract(archive,target,s['directory'])
            if inventory(target)!=json.loads(repo_file(s['pristine_inventory']).read_text()):raise ValueError('Pristine complete source inventory differs')
            for notice in s['license_files']:
                if not (target/notice).is_file():raise ValueError('Original notice missing')
        patch_group(trees,group,git)
        for s in selected:
            if inventory(trees/s['directory'])!=json.loads(repo_file(s['patched_inventory']).read_text()):raise ValueError('Patched complete source differs')
        for s in selected:
            (root/'sources').mkdir(exist_ok=True);(root/'archives').mkdir(exist_ok=True)
            shutil.copytree(trees/s['directory'],root/'sources'/s['directory'],symlinks=True)
            shutil.copyfile(archives/s['filename'],root/'archives'/s['filename'])
    result={'schema':1,'group':group,'sources_lock_sha256':sha(HERE/'sources.lock.json'),
            'sources':{s['registry_id']:sha(repo_file(s['patched_inventory'])) for s in selected},
            'complete_pristine_archives':len(selected),'source_only':True,'temporary_cleaned':True,
            'native_configure':'NOTRUN','native_full93':'NOTRUN','HAP':'NOTRUN'}
    dump(receipt,result);return verify_prepared(root,group)
