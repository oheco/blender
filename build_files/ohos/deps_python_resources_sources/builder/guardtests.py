#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused sealed-wrapper/source/data IO checks; no terminal13/compiler/network."""
import sys
sys.dont_write_bytecode=True
import argparse
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace


def module(path):
    spec=importlib.util.spec_from_file_location('pure_resource_wrapper_under_test',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--tmp',type=Path,required=True)
    args=parser.parse_args()
    entry=args.repo/'build_files/ohos/deps_python_resources_sources/builder/builder.py'
    wrapper=module(entry)
    checks=[]
    def passed(name,detail=None):checks.append({'check':name,'result':'PASS','detail':detail})
    def rejected(name,action):
        try:action()
        except (ValueError,OSError,RuntimeError) as error:passed(name,str(error))
        else:raise AssertionError('Expected guard rejection: '+name)
    proof,_=wrapper.verify_inputs(args.repo)
    passed('complete frozen inputs',{'files':proof['sealed_files'],'sources':len(proof['registry']['inputs'])})
    with tempfile.TemporaryDirectory(prefix='pure-wrapper-guards-',dir=args.tmp) as td:
        work=Path(td);tmp=work/'tmp';tmp.mkdir()
        def call(repo,group,stage='full',resume=False,**options):
            root=work/group
            root.mkdir(exist_ok=True)
            value=SimpleNamespace(repo=repo,root=root/'state',cache=root/'sources',resources=root/'pure',tmp=tmp,
                                  stage=stage,resume=resume,terminal_python=None,destination=None)
            for key,item in options.items():setattr(value,key,item)
            return wrapper.execute(value)
        rejected('source/resources overlap',lambda:call(args.repo,'overlap',resources=work/'overlap/sources'))
        rejected('state/source overlap',lambda:call(args.repo,'overlap-state',cache=work/'overlap-state/state/sources'))
        rejected('temporary/state overlap',lambda:call(args.repo,'overlap-tmp',root=tmp/'nested-state'))
        rejected('repository overlap',lambda:call(args.repo,'overlap-repo',root=args.repo/'forbidden-state'))
        unowned=work/'unowned/state';unowned.mkdir(parents=True);(unowned/'preserve').write_bytes(b'owned by user')
        rejected('unowned state preservation',lambda:call(args.repo,'unowned',resume=True))
        assert (unowned/'preserve').read_bytes()==b'owned by user'
        source=work/'old-output/sources';source.mkdir(parents=True)
        rejected('cannot adopt existing output',lambda:call(args.repo,'old-output'))
        original=call(args.repo,'original')
        assert original['status']=='PASS' and original['sources']['source_file_count']==643 and original['resources']['pure_file_count']==193
        assert not original['terminal_native_checked'] and original['application_native_acceptance']=='NOT_RUN'
        passed('original pure full uses public8 recipe',{'sources':643,'resources':193,'terminal_native_checked':False})
        rejected('exact resume required',lambda:call(args.repo,'original'))
        own=work/'original/state'
        with (own/'.pure-builder.lock').open('a+') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            rejected('active orchestration lock',lambda:call(args.repo,'original',stage='audit',resume=True))
        mini=work/'复制 unicode mini repository with spaces'
        wrapper.execute(SimpleNamespace(stage='copy-inputs',repo=args.repo,destination=mini))
        passed('sealed complete Unicode-space mini copy')
        moved=module(mini/'build_files/ohos/deps_python_resources_sources/builder/builder.py')
        value=SimpleNamespace(repo=mini,root=work/'移动 output space/state',cache=work/'移动 output space/sources',
                              resources=work/'移动 output space/pure',tmp=tmp,stage='full',resume=False,terminal_python=None)
        replay=moved.execute(value)
        assert replay['resources']['site_tree_sha256']==original['resources']['site_tree_sha256']
        passed('Unicode-space full source/assemble matches',{'site_tree_sha256':replay['resources']['site_tree_sha256']})
        for relative,label in [('build_files/ohos/deps_python_resources_sources/resources.py','old recipe helper tamper'),
                               ('build_files/ohos/vendor_archive.py','vendor helper tamper'),
                               ('build_files/ohos/deps_python_resources_sources/sources.lock.json','original source lock tamper'),
                               ('build_files/ohos/deps_python_resources_sources/builder/builder.py','new entry tamper'),
                               ('tpr/sources/requests-2.33.0/source.part0000','original archive part tamper')]:
            target=mini/relative;before=target.read_bytes();target.write_bytes(before+b'\n')
            rejected(label,lambda:moved.verify_inputs(mini));target.write_bytes(before)
        rogue=mini/'build_files/ohos/deps_python_resources_sources/builder/unsealed.py';rogue.write_text('# extra helper\n')
        rejected('unsealed sibling recipe helper',lambda:moved.verify_inputs(mini));rogue.unlink()
        marker=value.root/moved.OWNER;before=marker.read_bytes();marker.write_text('{}\n')
        value.stage='audit';value.resume=True
        rejected('differently sealed owner',lambda:moved.execute(value));marker.write_bytes(before)
        package=value.resources/'site-packages/requests/__init__.py';before=package.read_bytes();package.write_bytes(before+b'\n')
        rejected('failed audit invalidates all current PASS pointers',lambda:moved.execute(value))
        assert not list((value.root/'current').iterdir())
        results=[json.loads(p.read_text()) for p in sorted((value.root/'attempts').glob('*/result.json'))]
        assert [r['status'] for r in results]==['PASS','FAIL']
        package.write_bytes(before)
        audited=moved.execute(value)
        assert audited['status']=='PASS' and not (value.root/'current/full.json').exists()
        passed('monotonic history and no inherited full/native PASS')
        helper=mini/'build_files/ohos/deps_python_resources_sources/resources.py';before=helper.read_bytes();helper.write_bytes(before+b'\n')
        rejected('sealed recipe failure invalidates current receipts before import',lambda:moved.execute(value))
        assert not list((value.root/'current').iterdir())
        helper.write_bytes(before)
        public=moved.verify_inputs(mini)[1]
        for label,names in [('unsafe original extraction',['/outside']),('case original extraction',['root/Case','root/case'])]:
            archive=work/(label+'.tar.gz')
            with tarfile.open(archive,'w:gz') as stream:
                for name in names:
                    info=tarfile.TarInfo(name);info.size=1;stream.addfile(info,io.BytesIO(b'x'))
            dest=work/label;dest.mkdir()
            rejected(label,lambda:public.guard.safe_extract(archive,dest,'root'))
            assert not list(dest.iterdir())
        passed('original extractor rejects before writes')
    assert not work.exists()
    print(json.dumps({'status':'PASS','checks':checks,'count':len(checks),'temporary_trees_cleaned':True,
                      'input_lock_sha256':proof['input_lock_sha256'],'site_tree_sha256':original['resources']['site_tree_sha256'],
                      'source_files_each':643,'resource_files_each':193,'terminal_native_checked':False,
                      'compiler_execution':False,'network_requests':0,'bpy_acceptance':'NOT_RUN','HAP_acceptance':'NOT_RUN'},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
