#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable offline provided OHOS toolkit preparation and native acceptance."""
import sys
sys.dont_write_bytecode=True
import argparse,fcntl,json,os,shutil,hashlib,copy
from pathlib import Path
from toolkit_io import HERE,REPO,TOOL_ROLES,Runner,sha,file_row,json_write,overlap,validate_paths,verify_inputs
import profile,source_replay,native_probes,abi_capture


def parser():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['plan','prepare','audit','copy-inputs','full-probes','source-replay-plan','capture-abi-facts'])
    for name in ['root','tmp-dir','sdk-root','resource-dir',*[k.replace('_','-') for k in TOOL_ROLES]]:p.add_argument('--'+name,type=lambda x:Path(os.path.abspath(x)),required=True)
    p.add_argument('--jobs',type=int,choices=[1,2],default=1);p.add_argument('--runtime-timeout',type=int,default=180);p.add_argument('--copy-repo',type=lambda x:Path(os.path.abspath(x)));p.add_argument('--allow-output-root',action='append',type=lambda x:Path(os.path.abspath(x)),default=[])
    p.add_argument('--native-fact-context',type=lambda x:Path(os.path.abspath(x)))
    p.add_argument('--native-fact-context-sha256')
    return p


def owner(a,recipe):
    a.root.mkdir(parents=True,exist_ok=True);guard=(a.root/'active-operation.lock').open('a+')
    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    identity={'kind':'provided-native-ohos-toolkit-owner','recipe_inputs_sha256':recipe['inputs_lock']['sha256'],'root':str(a.root),'provided_paths':{k:str(getattr(a,k)) for k in ['sdk_root','resource_dir',*TOOL_ROLES]},'allowed_consumer_output_roots':[str(p) for p in a.allow_output_root]}
    file=a.root/'owner.json'
    if file.exists():
        if json.loads(file.read_text())!=identity:raise ValueError('Owned root/input identity mismatch')
    else:
        if any(p.name not in ['active-operation.lock'] for p in a.root.iterdir()):raise ValueError('Refuse foreign/unowned nonempty root')
        json_write(file,identity)
    return guard


def link_plan(a,runner,paths=None):
    flags=profile.config(a,Path(paths['resource_dir']) if paths else a.resource_dir)['flags']['cxx']
    command=[a.cxx,*flags,'-std=c++20','-###','-x','c++','-','-o',a.root/'never-produced-plan-output']
    out=runner.run(command,'actual-full-native-link-plan',input='int main(){return 0;}\n')
    if str(a.lld) not in out or '-lc++experimental' not in out or '-lc++abi' not in out:raise ValueError('Actual linker plan misses exact lld/static SDK closure')
    if (a.root/'never-produced-plan-output').exists():raise ValueError('Dry link plan produced binary')
    return file_row(a.root/'logs/actual-full-native-link-plan.log')


def audit_manifest(a,recipe):
    path=a.root/'toolkit-manifest.json';manifest=json.loads(path.read_text())
    if manifest['schema_version']!=1 or manifest['kind']!='provided-native-ohos-toolkit' or manifest['paths']['root']!=str(a.root) or manifest['recipe']!=recipe:raise ValueError('Toolkit manifest owner/recipe binding mismatch')
    profile.verify_records(manifest['readonly_facts'])
    actual={p.relative_to(a.root).as_posix() for p in (a.root/'profile').rglob('*') if p.is_file()}
    if actual!={row['path'] for row in manifest['owned_generated']}:raise ValueError('Generated profile file set drift')
    for row in manifest['owned_generated']:
        p=a.root/row['path']
        if sha(p)!=row['sha256'] or p.stat().st_size!=row['size']:raise ValueError('Generated profile/launcher/resource bytes drift')
    anchored={str(a.root/row['path']) for row in manifest['owned_generated']}
    for key in ['toolchain_file','cc_launcher','cxx_launcher','compiler_config','native_link_features_include']:
        if manifest['paths'][key] not in anchored:raise ValueError('Manifest path absent from derived file inventory')
    probe=manifest['native_probe']
    if probe['status']=='PASS':
        receipt=probe['receipt'];p=Path(receipt['path'])
        if p!=a.root/'native-probe-receipt.json' or sha(p)!=receipt['sha256']:raise ValueError('Native receipt path/hash drift')
        data=json.loads(p.read_text());before=copy.deepcopy(manifest);before['native_probe']={'status':'NOT_RUN','receipt':None}
        expected=hashlib.sha256((json.dumps(before,indent=2,ensure_ascii=False)+'\n').encode()).hexdigest()
        if data['current_recipe_inputs_sha256']!=recipe['inputs_lock']['sha256'] or data['status']!='PASS' or data['manifest_before_native_sha256']!=expected:raise ValueError('Unbound native PASS')
        required={'native_probe','affinity_probe','exceptions_rtti_format_error','cpp17','c17','module_owner'}
        runs=data['real_native_runs']
        if {r['name'] for r in runs}!=required or len(runs)!=len(required) or any(r['exit']!=0 for r in runs):raise ValueError('Missing real required native runs')
        build=a.root/'native-build';artifacts=data['artifacts']
        expected_paths={str(build/name) for name in required}|{str(build/'libowner_module.so')}
        if not expected_paths<={r['path'] for r in artifacts}:raise ValueError('Missing required signed native artifacts')
        artifact_hashes={r['path']:r['sha256'] for r in artifacts}
        for row in artifacts:
            target=Path(row['path']);lineage=row['signature_lineage']
            if build not in target.parents or sha(target)!=row['sha256'] or lineage['signed_sha256']!=row['sha256'] or not lineage['signature_checked']:raise ValueError('Native accepted artifact/path/signature drift')
            origins=[json.loads(q.read_text()) for q in (a.root/'signatures').glob('*.json')]
            if lineage not in origins:raise ValueError('Native artifact missing exact compiler signature origin')
        for run in runs:
            hashes=run['signed_sha256']
            expected_hashes=[artifact_hashes[str(build/'module_owner')],artifact_hashes[str(build/'libowner_module.so')]] if run['name']=='module_owner' else artifact_hashes[str(build/run['name'])]
            if hashes!=expected_hashes:raise ValueError('Run/artifact bytes binding drift')
        for key,relative in [('genuine_try_run','genuine-native-try-run.txt'),('real_registration_retention','toolkit-link-probe/result.txt')]:
            row=data[key];target=build/relative
            if row['path']!=str(target) or sha(target)!=row['sha256']:raise ValueError('Missing genuine native configure/registration evidence')
        if data['input_guards']!='PASS':raise ValueError('Native inputs guard incomplete')
    elif probe['status']!='NOT_RUN' or probe['receipt'] is not None:raise ValueError('Unknown/fabricated native state')
    manifest['_loaded_manifest_sha256']=sha(path);return manifest


def prepare(a,runner,recipe):
    if (a.root/'profile').exists() or (a.root/'toolkit-manifest.json').exists():raise ValueError('Fresh profile preparation required, no resume')
    readonly=profile.facts(a,runner);paths=profile.generate(a,readonly);patch=source_replay.patch_check(a,runner);planned=link_plan(a,runner,paths)
    # Execute only existing compiler version through generated shell/Python launchers.
    for lang in ['c','cxx']:
        value=runner.run([paths[lang+'c_launcher'] if lang=='c' else paths['cxx_launcher'],'--version'],'prepared-launcher-version-'+lang)
        if 'clang version 20.1.8' not in value:raise ValueError('Generated launcher invocation identity mismatch')
    generated=[file_row(p,a.root) for p in sorted((a.root/'profile').rglob('*')) if p.is_file()]
    resource_rows=[row for row in generated if row['path'].startswith('profile/resource/')]
    manifest={'schema_version':1,'kind':'provided-native-ohos-toolkit','recipe':recipe,'current_recipe_inputs_sha256':recipe['inputs_lock']['sha256'],
              'host':{'system':os.uname().sysname,'machine':os.uname().machine},'profile':{'id':'clang20-sdk15-native','actual_clang20':'20.1.8','sdk_version':'26.0.0.35-Beta','libcxx_version':15004,'libcxx_abi_namespace':'__n1','target':'aarch64-unknown-linux-ohos','experimental_static_cpp':True,'resource_overlay':'genuine LLVM20 include bytes plus only provided SDK15 CRT/builtins','lld_invocation':'ld.lld'},
              'paths':paths,'tool_records':readonly['tool_records'],'sdk':{'state':'PROVIDED_PREREQUISITE','root':str(a.sdk_root),'version':'26.0.0.35-Beta','NOTICE':file_row(a.sdk_root/'NOTICE.txt')},
              'source_inputs':json.loads((HERE/'sources.lock.json').read_text()),'readonly_facts':readonly,'header_inventories':readonly['header_inventories'],'selected_sdk_macros':readonly['selected_sdk_macros'],
              'owned_generated':generated,'resources':resource_rows,'source_patch_replay':patch,'readonly_link_plan':planned,
              'scope':{'provided_toolkit_prepared':True,'LLVM_from_source':'NOT_RUN','compiler_rt20':'NOT_BUILT','Cxx20_all_features':'NOT_CLAIMED','BlenderEditor':'NOT_TESTED','HAP':'NOT_TESTED'},
              'native_probe':{'status':'NOT_RUN','receipt':None}}
    json_write(a.root/'toolkit-manifest.json',manifest);json_write(a.root/'compiler-replay-plan.json',source_replay.plan(a,paths));audit_manifest(a,recipe)
    return manifest


def copy_inputs(a):
    dest=a.copy_repo
    if dest is None or dest.exists():raise ValueError('copy-inputs requires a new explicit --copy-repo')
    if not (a.tmp_dir.resolve() in dest.resolve().parents or a.root.resolve() in dest.resolve().parents):raise ValueError('Source copy must belong to managed TMPDIR or own root')
    for protected in [REPO,a.sdk_root,a.resource_dir,a.root/'profile',*[getattr(a,k) for k in TOOL_ROLES]]:
        if overlap(dest,protected):raise ValueError('Source copy overlaps protected input')
    lock=json.loads((HERE/'inputs.lock.json').read_text());paths=[*lock['sealed_files'],file_row(HERE/'inputs.lock.json',REPO)]
    for row in paths:
        output=dest/row['path'];output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/row['path'],output)
        if sha(output)!=row['sha256']:raise ValueError('Copied source closure mismatch')
    return {'source_repo':str(dest),'copied_files':len(paths),'result':'PASS byte-exact sealed recipe+complete original LLVM parts; no tool/SDK blobs copied'}


def main():
    a=parser().parse_args();validate_paths(a);recipe=verify_inputs();guard=owner(a,recipe);runner=Runner(a)
    if a.stage=='plan':
        facts=profile.facts(a,runner);value={'recipe':recipe,'readonly_facts':facts,'link_plan':link_plan(a,runner),'native_probe':'NOT_RUN','LLVM_from_source':'NOT_RUN'};json_write(a.root/'plan.json',value)
    elif a.stage=='prepare':value=prepare(a,runner,recipe)
    elif a.stage=='copy-inputs':value=copy_inputs(a);json_write(a.root/'copy-inputs.json',value)
    else:
        manifest=audit_manifest(a,recipe)
        if a.stage=='capture-abi-facts':
            from capture_io import read_original
            if a.native_fact_context is None or a.native_fact_context_sha256 is None:raise ValueError('Original independent parent context path/hash pair required')
            context_bytes,context_observed=read_original(a.native_fact_context)
            if context_observed['sha256']!=a.native_fact_context_sha256:raise ValueError('Original independent parent context hash differs')
            context_reference={'path':str(a.native_fact_context),'size':len(context_bytes),'sha256':a.native_fact_context_sha256}
            try:value=abi_capture.run(a,runner,manifest,context_reference)
            finally:
                verify_inputs();profile.verify_records(manifest['readonly_facts'])
                for row in manifest['owned_generated']:
                    from capture_io import read_original
                    original_bytes,original_record=read_original(a.root/row['path'])
                    if original_record['sha256']!=row['sha256'] or original_record['size']!=row['size']:raise ValueError('Original selected profile/source changed during ABI capture')
        elif a.stage=='full-probes':
            if manifest['native_probe']['status']!='NOT_RUN':raise ValueError('Fresh probe stage required, no resume')
            try:manifest['native_probe']=native_probes.full(a,runner,manifest)
            finally:
                verify_inputs();profile.verify_records(manifest['readonly_facts'])
                for row in manifest['owned_generated']:
                    if sha(a.root/row['path'])!=row['sha256']:raise ValueError('Prepared source/profile changed during native operation')
            manifest.pop('_loaded_manifest_sha256');json_write(a.root/'toolkit-manifest.json',manifest);value=audit_manifest(a,recipe)
        elif a.stage=='source-replay-plan':value=source_replay.plan(a,manifest['paths']);json_write(a.root/'compiler-replay-plan.json',value)
        else:value={'result':'PASS byte-bound toolkit owner, provided inputs, source/derived metadata audit','manifest':file_row(a.root/'toolkit-manifest.json'),'native_probe':manifest['native_probe'],'LLVM_from_source':'NOT_RUN'};json_write(a.root/'audit.json',value)
    print(json.dumps({'stage':a.stage,'root':str(a.root),'recipe_inputs_sha256':recipe['inputs_lock']['sha256'],'result':'RECORDED PRODUCER PROTOTYPE NATIVE_FACTS_NOT_READY' if a.stage=='capture-abi-facts' else 'PASS actual requested stage','new_native_probe':value.get('native_probe','NOT_RUN') if isinstance(value,dict) else 'NOT_RUN'},indent=2));guard.close()

if __name__=='__main__':main()
