#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Short Python/IO/native pkgconf grammar verification; never native compilation.

All temporary mini checkouts, archives, outputs, caches and fixtures are owned
under TMPDIR and cleaned; persistent original prepare is a separate CLI stage.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
from io_utils import HERE, REPO, relative, sha, sources, write_json
import builder
import metadata
import source_tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['git','pkgconf','python','output']:
        parser.add_argument('--'+name, type=Path, required=True)
    args=parser.parse_args()
    tmp=Path(os.environ['TMPDIR']).resolve()
    if not args.output.absolute().is_relative_to(tmp):
        raise ValueError('Temporary short receipt must be under TMPDIR')
    results=[]
    def passed(name, details=None):
        results.append({'check':name,'result':'PASS','details':details})
    def rejected(name, action):
        try:
            action()
        except (ValueError, RuntimeError, OSError) as e:
            passed(name, str(e))
            return
        raise AssertionError('Negative action unexpectedly accepted: '+name)
    with tempfile.TemporaryDirectory(prefix='color-short-',dir=tmp) as td:
        work=Path(td)
        for text in ['/absolute','../parent','a/../parent','a//b','a/./b','a\\b','a/','']:
            rejected('unsafe relative '+repr(text),lambda text=text:relative(text))
        fixture_serial=0
        def archive_test(name, entries, approved=None):
            nonlocal fixture_serial
            fixture_serial+=1
            archive=work/('unsafe-'+str(fixture_serial)+'.tar')
            with tarfile.open(archive,'w') as output:
                for path, kind, body in entries:
                    info=tarfile.TarInfo(path)
                    if kind=='dir': info.type=tarfile.DIRTYPE
                    elif kind=='file': info.size=len(body)
                    elif kind=='link': info.type=tarfile.SYMTYPE; info.linkname=body
                    elif kind=='hard': info.type=tarfile.LNKTYPE; info.linkname=body
                    else: info.type=tarfile.FIFOTYPE
                    output.addfile(info, BytesIO(body) if kind=='file' else None)
            rejected(name,lambda:source_tree.extract_complete(archive,work/('extract-'+str(fixture_serial)),'root',approved))
        archive_test('absolute archive entry',[('/root/a','file',b'a')])
        archive_test('parent archive entry',[('root/../a','file',b'a')])
        archive_test('noncanonical archive entry',[('root//a','file',b'a')])
        archive_test('backslash archive entry',[('root/a\\b','file',b'a')])
        archive_test('wrong archive root',[('other/a','file',b'a')])
        archive_test('duplicate archive entry',[('root/a','file',b'a'),('root/a','file',b'a')])
        archive_test('case-colliding archive entry',[('root/Case','file',b'a'),('root/case','file',b'a')])
        archive_test('file directory type collision',[('root/a','file',b'a'),('root/a/b','file',b'b')])
        archive_test('unsealed symlink',[('root/a','link','b')])
        archive_test('sealed escaping symlink',[('root/a','link','../../outside')],{'a':'../../outside'})
        archive_test('hardlink archive entry',[('root/a','hard','root/b')])
        archive_test('special archive entry',[('root/a','fifo',b'')])
        rejected('non-ld.lld basename',lambda:__import__('toolchain').preflight(SimpleNamespace(lld=Path('/some/lld')),None))
        # The mini repo includes exactly sealed ordinary inputs, no .git or
        # original cache. Its cache override is a disposable native cache under
        # TMPDIR, distinct from the caller's persistent prepare outputs.
        mini=work/'移动 mini repository with spaces'
        sealed=json.loads((HERE/'inputs.lock.json').read_text())['sealed_files']
        for row in sealed:
            source=REPO/relative(row['path']); target=mini/relative(row['path'])
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        lock_target=mini/HERE.relative_to(REPO)/'inputs.lock.json'
        shutil.copyfile(HERE/'inputs.lock.json',lock_target)
        entry=mini/HERE.relative_to(REPO)/'builder.py'
        env=os.environ.copy();env.update(XDG_CACHE_HOME=str(work/'cache'),TMPDIR=str(work/'tmp'),PYTHONDONTWRITEBYTECODE='1')
        Path(env['XDG_CACHE_HOME']).mkdir();Path(env['TMPDIR']).mkdir()
        def cli(stage, root=None, resume=False, expected=0):
            command=[str(args.python),str(entry),stage]
            if root is not None:
                command+=['--root',str(root),'--tmp-dir',env['TMPDIR']]
            if resume:command+=['--resume']
            if stage=='prepare':command+=['--git',str(args.git),'--source','pystring']
            done=subprocess.run(command,env=env,capture_output=True,timeout=60)
            if (done.returncode==0)!=(expected==0):
                raise AssertionError('Mini CLI result differs '+stage+' '+done.stdout.decode(errors='replace')+done.stderr.decode(errors='replace'))
            return {'command':command,'exit':done.returncode,'stdout_sha256':hashlib.sha256(done.stdout).hexdigest(),
                    'stderr':done.stderr.decode(errors='replace') if expected else ''}
        passed('Unicode-space mini verify-inputs',cli('verify-inputs'))
        root=Path(env['XDG_CACHE_HOME'])/'颜色 source root with spaces'
        passed('Unicode-space mini materialize',cli('materialize',root))
        passed('Unicode-space mini complete prepare forward/reverse',cli('prepare',root,True))
        prepared=source_tree.verify_tree(root/'sources/pystring','pystring',patched=True)
        passed('Unicode-space mini owned source byte/type/mode resume',prepared)
        def ownership(value, resume):
            return builder.own_workspace(SimpleNamespace(root=value,prefix=value/'prefix',tmp_dir=Path(env['TMPDIR']),resume=resume))
        rejected('unowned existing root rejection',lambda:ownership(root,False))
        forged=Path(env['XDG_CACHE_HOME'])/'unowned';forged.mkdir()
        rejected('existing unmarked root rejection',lambda:ownership(forged,True))
        marker=root/'.color-builder-owned.json';old_marker=marker.read_bytes();marker.write_text('{}\n')
        rejected('mismatched ownership rejection',lambda:ownership(root,True));marker.write_bytes(old_marker)
        original=root/'sources/pystring/pystring.cpp';original_bytes=original.read_bytes();original.write_bytes(original_bytes+b'\n')
        rejected('prepared source tamper rejection',lambda:source_tree.verify_tree(root/'sources/pystring','pystring',patched=True));original.write_bytes(original_bytes)
        patch=mini/HERE.relative_to(REPO)/'patches/pystring-applied-source.patch';old_patch=patch.read_bytes();patch.write_bytes(old_patch+b'\n')
        passed('sealed patch tamper rejection',cli('verify-inputs',expected=1));patch.write_bytes(old_patch)
        part=mini/'tpr/sources/pystring-v1.1.3/source.part0000';old_part=part.read_bytes();part.write_bytes(old_part[:-1]+bytes([old_part[-1]^1]))
        passed('sealed archive part tamper rejection',cli('verify-inputs',expected=1));part.write_bytes(old_part)
        known=mini/HERE.relative_to(REPO)/'recipe.json';known_bytes=known.read_bytes();known.unlink();known.symlink_to(HERE/'recipe.json')
        passed('sealed input symlink type rejection',cli('verify-inputs',expected=1));known.unlink();known.write_bytes(known_bytes)
        extra=root/'sources/pystring/EXTRA';extra.write_bytes(b'x')
        rejected('unexpected complete source path rejection',lambda:source_tree.verify_tree(root/'sources/pystring','pystring',patched=True));extra.unlink()
        modefile=root/'sources/pystring/LICENSE';old_mode=modefile.stat().st_mode & 0o777;modefile.chmod(old_mode^0o010)
        rejected('original source mode tamper rejection',lambda:source_tree.verify_tree(root/'sources/pystring','pystring',patched=True));modefile.chmod(old_mode)
        # Failed standalone reruns invalidate dependent success pointers first.
        for stage, expected in [('acceptance', ['acceptance.json','artifacts.json','migration.json','full-native-acceptance.json']),
                                ('audit',['artifacts.json','migration.json','full-native-acceptance.json']),
                                ('migrate',['migration.json','full-native-acceptance.json'])]:
            receipt_root=work/('receipt-'+stage);receipt_root.mkdir()
            for leaf in ['acceptance.json','artifacts.json','migration.json','full-native-acceptance.json']:
                (receipt_root/leaf).write_text('{"historical_test_fixture":true}\n')
            builder.invalidate_receipts(SimpleNamespace(root=receipt_root,stage=stage),SimpleNamespace(logs=work/'history-id'))
            if any((receipt_root/leaf).exists() for leaf in expected):
                raise AssertionError('Stale receipt survived '+stage)
            passed('dependent receipt invalidation '+stage)
        # PC sample verifies real pkgconf bytes and exact path token round trip;
        # it does not fabricate a target or claim linking/runtime success.
        p=work/'颜色 prefix with spaces/lib/pkgconfig';p.mkdir(parents=True)
        (p/'sample.pc').write_text(metadata.pc('sample','1','-lsample'))
        pcenv=env.copy();pcenv.update(PKG_CONFIG_LIBDIR=str(p),PKG_CONFIG_PATH='')
        query=subprocess.run([str(args.pkgconf),'--static','--cflags','--libs','sample'],env=pcenv,capture_output=True,timeout=20)
        if query.returncode:raise AssertionError(query.stderr)
        tokens=metadata.pkgconf_tokens(query.stdout)
        expected=['-I'+str(p/'../../include'),'-L'+str(p/'../../lib'),'-lsample','-pthread','-lm']
        if tokens!=expected:raise AssertionError((tokens,expected))
        passed('real native pkgconf single byte escape Unicode-space grammar',{'exit':query.returncode,'raw_hex':query.stdout.hex(),'tokens':tokens})
        # Preserve a digest of each selected complete prepared file/link tree.
        tree_records=json.loads((root/'prepared/pystring.json').read_text())['source']
        passed('Unicode-space selected complete source counts (full closure separate)',tree_records)
    write_json(args.output,{'result':'PASS actual short offline source/path/type/case/tamper/ownership/byte-grammar checks',
                           'checks':results,'temporary_tree_cleaned':True,'no_native_compile':True,'no_network':True,
                           'new_full_native_acceptance':False,'input_lock_sha256':sha(HERE/'inputs.lock.json')})
    print(json.dumps({'result':'PASS','checks':len(results),'temporary_tree_cleaned':True,'new_full_native_acceptance':False}))

if __name__=='__main__':main()
