#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Repository-relocatable offline source prep and explicit fresh native replay.
No native configure/probes/build occurs except the explicitly selected full action.
"""
import sys
sys.dont_write_bytecode=True
import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
from source_guard import HERE,REPO,sha,verify_inputs,repo_file,inventory
from io_utils import owned,owned_marker,private_root,temporary_root,clean_env,dump,Runner,safe_outputs,FailureReceipt,regular_bytes,real_path
from source_tree import prepare,verify_prepared,entries
import filesystem
import toolchain
import resourcesstage
import audit

SLOTS=('python','regen_python','cc','cxx','lld','ar','ranlib','nm','strip','readelf','make','ninja','perl','signer','git')


def module(name,relative):
    spec=importlib.util.spec_from_file_location(name,HERE/relative);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def require_tools(args):
    missing=[slot for slot in (*SLOTS,'sdk_root','resource_dir') if getattr(args,slot,None) is None]
    if missing:raise ValueError('Supply every explicit native tool path: '+', '.join(missing))
    if args.jobs not in (1,2):raise ValueError('jobs 1 or 2 only; linked LLD threads remains1')


def numpy_args(args,root):
    args.numpy_sources={row['registry_id']:root/'sources'/row['directory'] for row in entries('numpy')}
    args.numpy_verify_sources=lambda:verify_prepared(root,'numpy')
    return args


def copy_inputs(root):
    root=owned(root,create=True)
    if set(p.name for p in root.iterdir())!={'.python-native-builder-owned.json'}:raise ValueError('Input-copy requires marker-only own root')
    pins=json.loads((HERE/'inputs.lock.json').read_text())
    files=[repo_file(r['path']) for r in pins['sealed_files']]+[HERE/'inputs.lock.json']
    for source in files:
        target=root/source.relative_to(REPO);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        if sha(target)!=sha(source):raise ValueError('Portable input copy differs')
    return {'schema':1,'copied_files':len(files),'portable_repo_relative_inputs':True,'root':str(root),'native_compile':'NOTRUN','HAP':'NOTRUN'}


def plan(args,root):
    require_tools(args);root=owned(root)
    for group in ('python','numpy','tools'):verify_prepared(root,group)
    py=module('python_native_recipe','python/module.py');np=module('numpy_native_recipe','numpy/module.py')
    np.verify_inputs('799b6a7b580b5b4009b61a8502cc139c3f6d6485ed188768ea3164b486f15501')
    value={'schema':1,'kind':'fresh-own-source-native-argv-plan','source_lock_sha256':sha(HERE/'sources.lock.json'),
       'inputs_lock_sha256':sha(HERE/'inputs.lock.json'),'tools_lock_sha256':sha(HERE/'tools.lock.json'),
       'python':py.plan(args,root),'numpy':np.plan(numpy_args(args,root),root,root/'runtime'),
       'resources':resourcesstage.contract(root),'readonly_tools':'NOTRUN unless --readonly-tools',
       'native_full':'NOTRUN','native_probes':'NOTRUN','installed_HAP':'NOTRUN','Blender_postlink':'NOTRUN',
       'native_build_cache_constraint':'Explicit whitespace-free absolute private build root; source checkout may use Unicode/spaces. Sourceprep/copy-inputs permit Unicode/space roots; actual relocated runtime is a separate native gate.'}
    if args.readonly_tools:
        result,_=toolchain.validate(args,Runner(root,clean_env(args.tmp_dir)))
        value['readonly_tools']=result
    return value


def _full(args,root,failure):
    require_tools(args)
    if not args.inputs_lock_sha256:raise ValueError('full requires independently supplied frozen --inputs-lock-sha256')
    root=private_root(root)
    if any(c.isspace() for c in str(root)):raise ValueError('Upstream native Make/install requires a whitespace-free private build root; Unicode/space SOURCE checkout remains supported')
    if root.exists():raise ValueError('full requires absent owned root; no partial native cache replay')
    root=owned(root,create=True);failure.acquire(root);filesystem.check(root,args.tmp_dir)
    runner=Runner(root,clean_env(args.tmp_dir));profile,env=toolchain.validate(args,runner)
    runner.env=env;dump(root/'receipts/readonly-tool-profile.json',profile)
    for group in ('python','numpy','tools'):prepare(root,group,args.tmp_dir,args.git)
    from tools import prepare_perl
    prepare_perl(args,root,runner)
    py=module('python_native_recipe','python/module.py');py_result=py.build(args,runner,root)
    dump(root/'receipts/python-build.json',py_result)
    # Per-stage isolation: no Python Autoconf exception/compiler/env leaks into Meson.
    runner.env=clean_env(args.tmp_dir)
    np=module('numpy_native_recipe','numpy/module.py');np.verify_inputs('799b6a7b580b5b4009b61a8502cc139c3f6d6485ed188768ea3164b486f15501')
    numpy_result=np.build(numpy_args(args,root),runner,root,root/'runtime')
    source=root/'numpy/install/lib/python3.13/site-packages';target=root/'runtime/lib/python3.13/site-packages'
    for p in source.iterdir():
        destination=target/p.name
        if destination.exists():raise ValueError('NumPy collides with new prefix')
        if p.is_dir():shutil.copytree(p,destination,symlinks=True)
        else:shutil.copy2(p,destination)
    source_notices=root/'numpy/install/share/licenses'
    if source_notices.is_dir():
        destination=root/'runtime/share/licenses/numpy-native';shutil.copytree(source_notices,destination,symlinks=False)
    dump(root/'receipts/numpy-build.json',numpy_result)
    pure=resourcesstage.prepare_selected(args,root,runner)
    initial=audit.prefix(root,stage_hap=True);dump(root/'receipts/native-elf-before-pure.json',initial)
    resourcesstage.assemble(root,pure)
    runner.env=clean_env(args.tmp_dir)
    python=root/'runtime/bin/python3.13'
    runner.run([python,'-I',HERE/'native_acceptance.py','--root',root,'--report',root/'receipts/native93-pure8.json'],'native93-pure8-actual')
    runner.run([python,'-I','-S',HERE/'flat_entry.py',root,root/'receipts/flat92-pure8.json'],'flat92-pure8-actual',
          extra_env={'LD_LIBRARY_PATH':str(root/'hap-native/libs/arm64-v8a')})
    from native_embedding import run as run_embedding
    run_embedding(args,root,runner)
    final=audit.prefix(root);dump(root/'receipts/final-elf-audit.json',final)
    for group in ('python','numpy','tools'):verify_prepared(root,group)
    verify_inputs();safe_outputs(root)
    receipts=['receipts/python-build.json','receipts/numpy-build.json','receipts/native93-pure8.json','receipts/flat92-pure8.json',
       'receipts/embedding-dlopen.json','receipts/final-elf-audit.json','receipts/perl-generator-origin.json','receipts/selected-pure8-source-assembly.json']
    manifest={'schema':1,'kind':'python-native-runtime-source-replay','runtime_relative':'runtime','hap_native_relative':'hap-native/libs/arm64-v8a',
        'module_map_relative':'hap-native/python-modules.json','raw_resources_relative':'hap-resources/resources/rawfile/python',
        'source_lock_sha256':sha(HERE/'sources.lock.json'),'inputs_lock_sha256':sha(HERE/'inputs.lock.json'),'tool_lock_sha256':sha(HERE/'tools.lock.json'),
        'selected_pure8_lock_sha256':resourcesstage.PURE_SHA,'runtime_inventory':inventory(root/'runtime'),
        'HAP_native_inventory':inventory(root/'hap-native'),'raw_resources_manifest_sha256':sha(root/'hap-resources/resource-manifest.json'),
        'counts':{'runtime_ELF':93,'HAP_DSO':92,'native_map':90,'pure_distributions':8},
        'actual_fresh_native_and_selected_pure8':'PASS only after actual commands above','fresh_receipts':[{'path':p,'sha256':sha(root/p)} for p in receipts],
        'source_tool_rebuild_Perl_Ninja_make':'NOTRUN consumes explicit whole pinned build-tool prerequisites with complete original source/recipe lineage',
        'installed_HAP':'NOTRUN','Blender_postlink':'NOTRUN','source_frozen_input_root':str(REPO)}
    dump(root/'runtime-manifest.json',manifest);return manifest


def failed_source_closure(root):
    """Read immutable prepared inputs without re-entering the rejecting output scan."""
    root=owned_marker(root)
    for group in ('python','numpy','tools'):
        receipt=root/'receipts'/('prepared-'+group+'.json')
        if not receipt.exists() and not receipt.is_symlink():continue
        state=json.loads(regular_bytes(receipt));selected=entries(group)
        if state.get('sources_lock_sha256')!=sha(HERE/'sources.lock.json') or set(state.get('sources',{}))!={s['registry_id'] for s in selected}:raise ValueError('Failed prepared source closure differs')
        for source in selected:
            archive=root/'archives'/source['filename'];expected=repo_file(source['patched_inventory'])
            source_path=real_path(root/'sources'/source['directory'])
            if hashlib.sha256(regular_bytes(archive)).hexdigest()!=source['sha256'] or inventory(source_path)!=json.loads(expected.read_text()) or state['sources'][source['registry_id']]!=sha(expected):raise ValueError('Failed prepared immutable source drift: '+source['registry_id'])


def full(args,root):
    root=private_root(root)
    if root.exists():raise ValueError('full requires absent private root; no prior output inspection/mutation')
    failure=FailureReceipt();primary_error=None;result=None
    try:
        result=_full(args,root,failure)
        checked=owned(root)
        for group in ('python','numpy','tools'):
            if (checked/'receipts'/('prepared-'+group+'.json')).is_file():verify_prepared(checked,group)
        verify_inputs();return result
    except BaseException as primary:
        primary_error=primary;secondary=[]
        # Every check is diagnostic on failure; none can mask the original cause.
        for label,check in (('immutable input closure',verify_inputs),('prepared source closure',lambda:failed_source_closure(root))):
            try:check()
            except BaseException as error:
                diagnostic=label+': '+type(error).__name__+': '+str(error)
                secondary.append(diagnostic);primary.add_note(diagnostic)
        try:failure.emit(primary,secondary)
        except BaseException as error:primary.add_note('failure receipt unavailable: '+type(error).__name__+': '+str(error))
        raise
    finally:
        for diagnostic in failure.close():
            if primary_error is not None:primary_error.add_note(diagnostic)
            elif isinstance(result,dict):result.setdefault('secondary_diagnostics',[]).append(diagnostic)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['verify-inputs','prepare','plan','full','audit','resources-stage','copy-inputs'])
    p.add_argument('--root',type=Path);p.add_argument('--tmp-dir',type=Path)
    p.add_argument('--inputs-lock-sha256');p.add_argument('--group',choices=['python','numpy','tools']);p.add_argument('--jobs',type=int,default=1)
    p.add_argument('--readonly-tools',action='store_true')
    for slot in (*SLOTS,'sdk_root','resource_dir'):p.add_argument('--'+slot.replace('_','-'),type=Path)
    a=p.parse_args()
    if a.inputs_lock_sha256 and sha(HERE/'inputs.lock.json')!=a.inputs_lock_sha256:raise ValueError('Supplied frozen input-lock digest differs')
    verified=verify_inputs()
    if a.action=='verify-inputs':result=verified
    else:
        if a.root is None or a.tmp_dir is None:p.error('Explicit --root and --tmp-dir required')
        a.tmp_dir=temporary_root(a.tmp_dir)
        if a.action=='copy-inputs':result=copy_inputs(a.root)
        elif a.action=='full':result=full(a,a.root)
        elif a.action=='prepare':
            if not a.group or not a.git:p.error('prepare needs --group and explicit --git')
            root=owned(a.root,create=True);filesystem.check(root,a.tmp_dir);result=prepare(root,a.group,a.tmp_dir,a.git)
        elif a.action=='plan':result=plan(a,a.root)
        elif a.action=='audit':result=audit.prefix(a.root)
        else:result=resourcesstage.assemble(a.root)
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
