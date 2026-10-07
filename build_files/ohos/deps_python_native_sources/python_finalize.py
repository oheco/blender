# SPDX-License-Identifier: GPL-2.0-or-later
"""Finalize only the new installed prefix; compiler metadata follows its current home."""
import sys
sys.dont_write_bytecode=True
import json
from pathlib import Path
import shutil
from source_guard import HERE,sha
from source_tree import lock
from io_utils import dump
from toolchain import sign_file


def finalize(args,root):
    root=Path(root);prefix=root/'runtime';lib=prefix/'lib/python3.13';deps=root/'deps';helpers=HERE/'python/helpers'
    config,=lib.glob('_sysconfigdata_*.py')
    config.write_text(config.read_text()+'\nbuild_time_vars["OHOS_BUILD_DEPS"] = '+repr(str(deps))+'\n')
    driver=prefix/'libexec/python3.13';driver.mkdir(parents=True,exist_ok=False)
    for name in ('sign_compiler.py','python-config.py'):shutil.copyfile(helpers/name,driver/name)
    context={'tools':{role:{'path':str(getattr(args,role)),'sha256':sha(getattr(args,role))} for role in ('cc','cxx','lld','ar','ranlib','signer','readelf')},
             'sdk_root':str(args.sdk_root),'resource_dir':str(args.resource_dir),'tmp_dir':str(args.tmp_dir)}
    dump(driver/'tool-context.json',context)
    launcher='''import sys,os,json,hashlib,subprocess
from pathlib import Path
sys.dont_write_bytecode=True
prefix=Path(__file__).resolve().parents[2]
ctx=json.loads((Path(__file__).parent/'tool-context.json').read_text())
language=sys.argv[1];role='cxx' if language=='cxx' else 'cc'
for name in (role,'lld','signer','readelf'):
    item=ctx['tools'][name]
    with Path(item['path']).open('rb') as f:
        if hashlib.file_digest(f,'sha256').hexdigest()!=item['sha256']:raise SystemExit('Explicit SDK tool bytes changed')
env=os.environ.copy()
for key in ('CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','LIBRARY_PATH','SDKROOT','COMPILER_PATH','INCLUDE','LIB','LIBPATH','LD_LIBRARY_PATH','LD_PRELOAD'):env.pop(key,None)
env['OHOS_BINARY_SIGN_TOOL']=ctx['tools']['signer']['path'];env['OHOS_LLVM_READELF']=ctx['tools']['readelf']['path']
if not env.get('TMPDIR'):raise SystemExit('Explicit private TMPDIR required')
flags=['--target=aarch64-unknown-linux-ohos','--sysroot='+ctx['sdk_root']+'/sysroot','-resource-dir='+ctx['resource_dir'],'--ld-path='+ctx['tools']['lld']['path'],'-Wl,--threads=1']
if role=='cxx':flags.append('-static-libstdc++')
arguments=sys.argv[2:]
if '-shared' in arguments:arguments+=['-L'+str(prefix/'lib'),'-lpython3.13']
raise SystemExit(subprocess.call([str(prefix/'bin/python3.13'),str(Path(__file__).parent/'sign_compiler.py'),ctx['tools'][role]['path'],*flags,'--',*arguments],env=env))
'''
    (driver/'compiler-entry.py').write_text(launcher)
    for name,role in (('ohos-clang','cc'),('ohos-clang++','cxx')):
        file=prefix/'bin'/name;file.write_text('#!/usr/bin/sh\nset -eu\nprefix=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\nexec "$prefix/bin/python3.13" "$prefix/libexec/python3.13/compiler-entry.py" '+role+' "$@"\n');file.chmod(0o755)
    for name in ('python3-config','python3.13-config'):
        file=prefix/'bin'/name
        if file.exists() or file.is_symlink():raise ValueError('Unexpected already installed config entry')
        file.write_text('#!/usr/bin/sh\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$base/python3.13" "$base/../libexec/python3.13/python-config.py" "$@"\n');file.chmod(0o755)
    originals={}
    for file in (prefix/'lib/pkgconfig').glob('*.pc'):
        if file.is_symlink():continue
        originals[file.name]={'sha256':sha(file),'text':file.read_text()}
        file.write_text('\n'.join('prefix=${pcfiledir}/../..' if row.startswith('prefix=') else row for row in file.read_text().splitlines())+'\n')
    dump(root/'receipts/original-generated-python-pc.json',originals)
    licenses=prefix/'share/licenses/python-native';licenses.mkdir(parents=True,exist_ok=False)
    for source in lock()['sources']:
        if source['group']!='python':continue
        for name in source['license_files']:
            target=licenses/source['registry_id']/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/'sources'/source['directory']/name,target)
    shutil.copyfile(HERE/'sources.lock.json',licenses/'sources.lock.json');shutil.copyfile(Path(args.sdk_root)/'NOTICE.txt',licenses/'SDK-NOTICE.txt')
    files=[]
    for p in sorted(prefix.rglob('*')):
        if p.is_symlink() or not p.is_file():continue
        with p.open('rb') as f:magic=f.read(4)
        if magic==b'\x7fELF':sign_file(args,p);files.append(p.relative_to(prefix).as_posix())
    if len(files)!=74:raise ValueError('Require complete genuine accepted-profile 74 Python signed outputs')
    dump(root/'receipts/python-final-signatures.json',{'count':74,'files':files,'source_selected_SONAME':'libpython3.13.so',
           'metadata_only_normalization_before_final_sign':True,'postsign_binary_rename':False})
