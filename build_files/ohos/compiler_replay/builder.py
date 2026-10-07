#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable complete-source LLVM20.1.8 native OHOS compiler replay, offline."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'helpers'))
import argparse,fcntl,json,os,shutil,hashlib
from common import HERE,REPO,TOOLS,Runner,paths,verify_inputs,sha,row,write_json,intersects
import source_tree,profile,native_stages,vendor_archive,cache_reuse
SOURCE_STAGES=['verify','copy-inputs','materialize','extract','reuse-source-cache','prepare-source','guard','plan','prepare-bootstrap']
NATIVE_STAGES=['configure','build','install','sign-install','audit']

def arguments():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=[*SOURCE_STAGES,*NATIVE_STAGES,'source-full','full'])
    for name in ['root','tmp-dir','sdk-root','resource-dir',*TOOLS]:p.add_argument('--'+name,type=lambda value:Path(os.path.abspath(value)),required=True)
    p.add_argument('--bootstrap-mode',choices=['sdk15-bootstrap','provided20-selfhost'],required=True);p.add_argument('--jobs',type=int,choices=[1,2],default=1);p.add_argument('--runtime-timeout',type=int,default=180)
    p.add_argument('--resume-source',action='store_true',help='Only exact partial original-source extraction can resume; native stages are ordered/fresh')
    p.add_argument('--copy-repo',type=lambda value:Path(os.path.abspath(value)))
    p.add_argument('--source-cache',type=lambda value:Path(os.path.abspath(value)),help='Explicit readonly COMPLETE+PATCHED owned source cache; new root/receipt; no TAR decoding or native acceptance transfer')
    return p.parse_args()

def owner(a,recipe):
    a.root.mkdir(parents=True,exist_ok=True);lock=(a.root/'operation.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    prerequisite_inventory={'resource':profile.inventory(a.resource_dir/'include'),'sdk_cxx':profile.inventory(a.sdk_root/'llvm/include/libcxx-ohos/include/c++/v1'),'sdk_sysroot':profile.inventory(a.sdk_root/'sysroot'),'SDKNOTICE':row(a.sdk_root/'NOTICE.txt'),'sdk_runtime':[row(a.sdk_root/'llvm/lib/clang/15.0.4/lib/aarch64-linux-ohos'/n) for n in profile.RT]+[row(a.sdk_root/'llvm/lib/aarch64-linux-ohos'/n) for n in profile.LIBS]}
    provided_fingerprint=hashlib.sha256(json.dumps(prerequisite_inventory,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    identity={'provided_input_inventory_sha256':provided_fingerprint,'kind':'complete-native-ohos-compiler-source-replay','recipe':recipe,'root':str(a.root),'bootstrap_mode':a.bootstrap_mode,'tools':[dict(role=k,**row(getattr(a,k))) for k in TOOLS],'sdk_root':str(a.sdk_root),'resource_dir':str(a.resource_dir),'jobs':a.jobs,'readonly_source_cache_origin':str(a.source_cache) if a.source_cache else None}
    p=a.root/'owner.json'
    if p.exists():
        if json.loads(p.read_text())!=identity:raise ValueError('Owned compiler-replay/input identity mismatch')
    else:
        if any(p.name!='operation.lock' for p in a.root.iterdir()):raise ValueError('Foreign/nonempty root')
        write_json(p,identity);write_json(a.root/'stage-journal.json',{'compiler_output_REBUILD':'NOT_RUN','native_stages':[],'source_stages':[]})
    return lock

def materialize(a):
    source=json.loads((HERE/'sources.lock.json').read_text());directory=REPO/Path(source['registry_manifest']).parent;output=a.root/'downloads'/source['LLVM']['filename']
    vendor_archive.materialize(directory,output);return output

def copy(a):
    dest=a.copy_repo
    if dest is None or dest.exists() or a.tmp_dir.resolve() not in dest.resolve().parents:raise ValueError('NEW --copy-repo under exact managed TMPDIR required')
    for protected in [REPO,a.sdk_root,a.resource_dir,a.root,*[getattr(a,k) for k in TOOLS]]:
        if intersects(dest,protected):raise ValueError('Copied repo overlaps provided inputs')
    data=json.loads((HERE/'inputs.lock.json').read_text());items=[*data['sealed_files'],row(HERE/'inputs.lock.json',REPO)]
    for item in items:
        p=dest/item['path'];p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/item['path'],p)
        if sha(p)!=item['sha256']:raise ValueError('Exact copied source closure differs')
    return {'copied_files':len(items),'repo':str(dest),'copied':'Source recipe/complete inventory/notices/original LLVM5parts; no provided tools/SDK blobs'}

def source_stage(a,runner,stage):
    if stage=='verify':
        data=vendor_archive.verify(REPO/'tpr/sources/llvm-20.1.8');source,inventory,records=source_tree.ledger();result={'original_archive_sha256':data['sha256'],'complete_source_records':len(records),'source_regular_bytes':inventory['regular_bytes'],'compiler_output_REBUILD':'NOT_RUN'}
    elif stage=='copy-inputs':result=copy(a)
    elif stage=='materialize':result=row(materialize(a))
    elif stage=='extract':result=source_tree.guard(a) if (a.root/'prepared-source.json').exists() else source_tree.extract(a,materialize(a))
    elif stage=='reuse-source-cache':result=cache_reuse.adopt(a,runner)
    elif stage=='prepare-source':result=source_tree.prepare(a,runner)
    elif stage=='guard':result=source_tree.guard(a)
    elif stage=='prepare-bootstrap':
        result=profile.generate(a,runner,a.root/'bootstrap-profile',a.cc,a.cxx,a.lld,a.resource_dir,15 if a.bootstrap_mode=='sdk15-bootstrap' else 20,'bootstrap')
    else:
        source_tree.guard(a)
        result={'compiler_output_REBUILD':'NOT_RUN','bootstrap_mode':a.bootstrap_mode,'commands':{k:[[str(v) for v in command] for command in value] if k=='install' else [str(v) for v in value] for k,value in native_stages.command_plan(a).items()},'future_native_order':NATIVE_STAGES,'source_identity':row(a.root/'prepared-source.json'),'whole_project_scope':'clang/lld/AArch64/TableGen/components; full original source kept; optional projects and upstream tests not built'}
    write_json(a.root/('stage-'+stage+'.json'),result);return result

def native_stage(a,runner,stage,journal):
    done=journal['native_stages'];index=NATIVE_STAGES.index(stage)
    if done!=NATIVE_STAGES[:index]:raise ValueError('Native stage must follow exact successful fresh order; no hidden resume')
    source_tree.guard(a);profile.guard_profile(a,'bootstrap')
    try:result=getattr(native_stages,stage.replace('-','_'))(a,runner)
    finally:
        verify_inputs();source_tree.guard(a);profile.guard_profile(a,'bootstrap')
    done.append(stage)
    if stage=='audit':journal['compiler_output_REBUILD']='PASS actual NEW LLVM source compiler rebuild/native acceptance'
    write_json(a.root/'stage-journal.json',journal);return result or {'stage':stage,'actual_result':'PASS','compiler_output_REBUILD':journal['compiler_output_REBUILD']}

def main():
    a=arguments();paths(a);recipe=verify_inputs();lock=owner(a,recipe);runner=Runner(a);journal=json.loads((a.root/'stage-journal.json').read_text())
    if a.stage in SOURCE_STAGES:
        result=source_stage(a,runner,a.stage);journal['source_stages'].append(a.stage);write_json(a.root/'stage-journal.json',journal)
    elif a.stage in NATIVE_STAGES:result=native_stage(a,runner,a.stage,journal)
    else:
        sequence=['verify','reuse-source-cache','guard'] if a.source_cache else ['verify','materialize','extract','prepare-source','guard']
        for stage in sequence:
            result=source_stage(a,runner,stage);journal['source_stages'].append(stage);write_json(a.root/'stage-journal.json',journal)
        if not (a.root/'bootstrap-profile').exists():source_stage(a,runner,'prepare-bootstrap')
        else:profile.guard_profile(a,'bootstrap')
        source_stage(a,runner,'plan')
        if a.stage=='full':
            if journal['native_stages']:raise ValueError('Fresh native full required; use explicit ordered manual stages for inspected partial native work')
            for stage in NATIVE_STAGES:result=native_stage(a,runner,stage,journal)
    print(json.dumps({'stage':a.stage,'root':str(a.root),'recipe_inputs_sha256':recipe['inputs_lock_sha256'],'source_operation':'PASS actual requested source/ordered native entry','compiler_output_REBUILD':journal['compiler_output_REBUILD'],'new_native_not_run_in_source_stages':a.stage in [*SOURCE_STAGES,'source-full']},indent=2));lock.close()

if __name__=='__main__':main()
