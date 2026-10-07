#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual short Python/IO negatives; no native prefix or tool execution."""
import sys
sys.dont_write_bytecode=True
import argparse
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import shaderc as adapter

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-root',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
contract=adapter.load(adapter.HERE/'contract.json');cases=[];commands=[]


def reject(name,call):
    try: call()
    except (ValueError,OSError,KeyError) as error:
        cases.append({'name':name,'actual_rejection':type(error).__name__,'message':str(error)})
    else: raise AssertionError('Unsafe fixture accepted: '+name)


def cli(command,label,env,expected):
    result=subprocess.run(list(map(str,command)),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
    file=args.output/(label+'.log');file.write_bytes(result.stdout)
    commands.append({'command':list(map(str,command)),'label':label,'exit_code':result.returncode,'log_sha256':adapter.digest(file)})
    if result.returncode!=expected: raise AssertionError(label+' actualexit='+str(result.returncode))
    return json.loads(result.stdout)


for name in ['shaderc.py','validate.py']: ast.parse((adapter.HERE/name).read_text(),filename=name)
original_env=os.environ.copy();seal=adapter.self_seal()
with tempfile.TemporaryDirectory(prefix='shaderc-adapter-source-validation-',dir=os.environ['TMPDIR']) as td:
    root=Path(td);cache=root/'private-cache';cache.mkdir();temp=root/'temporary';temp.mkdir()
    env=original_env.copy();env.update(XDG_CACHE_HOME=str(cache),TMPDIR=str(temp),PYTHONDONTWRITEBYTECODE='1')
    os.environ.update(XDG_CACHE_HOME=str(cache),TMPDIR=str(temp))
    copies=[]
    for source,namespace,label in [(args.source_root.absolute(),adapter.HERE,'original')]:
        out=cache/(label+'-inspect')
        result=cli([sys.executable,namespace/'shaderc.py','inspect','--source-root',source,
                    '--source-lock-sha256',contract['source_input_lock_sha256'],'--output',out,'--tmp-dir',temp],label+'-inspect',env,0)
        assert result['status']=='NOTREADY' and result['temporary_tree_cleaned'] is True
        packet=adapter.load(out/'inspection.json');assert 'new_full_native_acceptance' not in packet
        copies.append({'repository':label,'adapter_input_lock_sha256':result['adapter_input_lock_sha256'],'status':'NOTREADY source contract only'})
        cli([sys.executable,namespace/'shaderc.py','inspect','--source-root',source,
            '--source-lock-sha256',contract['source_input_lock_sha256'],'--output',out,'--tmp-dir',temp],label+'-no-adoption',env,2)
    mini=root/'离线 copied repository with spaces';namespace=mini/'build_files/ohos/dependency_receipts';namespace.mkdir(parents=True)
    for file in adapter.HERE.iterdir():
        if file.is_file(): shutil.copyfile(file,namespace/file.name)
    source=mini/'build_files/ohos/deps_vulkan_sources'
    for name in contract['reviewed_source_sha256']:
        target=source/adapter.rel(name);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(args.source_root/name,target)
    moved=cli([sys.executable,namespace/'shaderc.py','inspect','--source-root',source,
        '--source-lock-sha256',contract['source_input_lock_sha256'],'--output',cache/'moved-inspect','--tmp-dir',temp],
        'Unicode-space-copy-inspect',env,0)
    assert moved['adapter_input_lock_sha256']==seal and moved['status']=='NOTREADY'
    copies.append({'repository':'Unicode-space moved source-contract mini repository','adapter_input_lock_sha256':seal,'status':'NOTREADY'})
    original=(source/'builder/audit.py').read_bytes();(source/'builder/audit.py').write_bytes(original+b'\n# tamper\n')
    cli([sys.executable,namespace/'shaderc.py','inspect','--source-root',source,'--source-lock-sha256',contract['source_input_lock_sha256'],
        '--output',cache/'tamper-not-created','--tmp-dir',temp],'changed-source-hash',env,2)
    (source/'builder/audit.py').write_bytes(original)
    # A slash separates Path components; '-' changes full-string ordering.
    # Both rows share build.sh but must remain distinct complete source paths.
    rows=[{'path':'kokoro/linux-clang-release/build.sh','sha256':'0'*64,'size':1},
          {'path':'kokoro/linux-clang-release-bazel/build.sh','sha256':'1'*64,'size':2}]
    by_components=sorted(rows,key=lambda r:Path(r['path']))
    by_text=sorted(rows,key=lambda r:r['path'])
    assert by_components!=by_text and adapter.source_rows_match(by_components,by_text)
    changed=copy.deepcopy(by_text);changed[0]['sha256']='2'*64
    assert not adapter.source_rows_match(by_components,changed)
    reject('same-leaf-source-row-duplicate',lambda:adapter.source_rows_match(rows+[rows[0]],rows))
    changed=copy.deepcopy(rows);changed[1]['path']='KOKORO/LINUX-CLANG-RELEASE/BUILD.SH'
    reject('case-alias-source-row',lambda:adapter.source_rows_match(changed,rows))
    reject('unknown-full-schema',lambda: adapter.validate_receipts({'full-native-acceptance.json':{'result':contract['full_result'],'new_full_native_acceptance':True}},contract))
    reject('incomplete-full-schema',lambda: adapter.validate_receipts({'full-native-acceptance.json':{'result':contract['full_result']}},contract))
    reject('opaque-archive',lambda:adapter.archive_members(b'NOT AN ARCHIVE'))
    reject('empty-archive-header-spoof',lambda:adapter.archive_members(b'!<arch>\n'))
    header=b'foo.o/          '+b'0           '+b'0     '+b'0     '+b'100644  '+b'8         '+b'`\n'
    reject('archive-non-ELF-payload-spoof',lambda:adapter.archive_members(b'!<arch>\n'+header+b'NOTELF!!'))
    for name in ['../escape','/absolute','a//b','a\\b','./a']:
        reject('unsafe-path-'+repr(name),lambda name=name:adapter.rel(name))
    duplicate=root/'duplicate.json';duplicate.write_text('{"status":"pending","status":"completed"}')
    reject('duplicate-json-fields',lambda:adapter.load(duplicate))
    # NON-NATIVE metadata file set, used only for byte/alias/unknown schema rejection.
    prefix=cache/'metadata-prefix';prefix.mkdir()
    for name in adapter.SENTINELS:
        file=prefix/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'NON-NATIVE METADATA ONLY\n')
    saved={'files':adapter.inventory(prefix),'input_lock_sha256':contract['source_input_lock_sha256']}
    adapter.validate_prefix(prefix,saved,contract['source_input_lock_sha256'])
    file=prefix/'include/shaderc/shaderc.hpp';before=file.read_bytes();file.write_bytes(before+b'tamper')
    reject('changed-current-header-bytes',lambda:adapter.validate_prefix(prefix,saved,contract['source_input_lock_sha256']));file.write_bytes(before)
    extra=prefix/'include/extra.h';extra.write_bytes(b'extra')
    reject('unknown-extra-prefix-file',lambda:adapter.validate_prefix(prefix,saved,contract['source_input_lock_sha256']));extra.unlink()
    wrong=copy.deepcopy(saved);wrong['files'][0]['surplus']=True
    reject('unknown-prefix-row-field',lambda:adapter.validate_prefix(prefix,wrong,contract['source_input_lock_sha256']))
    wrong=copy.deepcopy(saved);wrong['files'].append(wrong['files'][0])
    reject('duplicate-prefix-row',lambda:adapter.validate_prefix(prefix,wrong,contract['source_input_lock_sha256']))
    alias=cache/'prefix-alias';alias.symlink_to(prefix,target_is_directory=True)
    reject('prefix-directory-alias',lambda:adapter.validate_prefix(alias,saved,contract['source_input_lock_sha256']))
    output=cache/'policy-output';policy=SimpleNamespace(output=output,tmp_dir=temp,source_root=source,prefix=prefix,
        receipt_root=cache/'NOT_PRESENT_native_root',sdk_root=cache/'NOT_PRESENT_sdk',resource_dir=cache/'NOT_PRESENT_resource',job_receipt=None,
        ar=None,nm=None,readelf=None,signer=None)
    adapter.output_policy(policy);output.mkdir()
    reject('preexisting-empty-output-no-adoption',lambda:adapter.output_policy(policy));output.rmdir()
    policy.output=prefix/'output';reject('output-overlaps-prefix',lambda:adapter.output_policy(policy))
    policy.output=root/'outside-cache';reject('output-outside-private-cache',lambda:adapter.output_policy(policy));policy.output=output
    # The pending gate must precede all actual native-root/source/prefix/tool reads.
    native=cache/'NEVER_CREATED_live_root'
    pending={'schema_version':1,'kind':'dsh-completed-native-vulkan-full','status':'running','job_id':'bash-fixture-NONNATIVE',
        'exit_code':None,'command':[],'cwd':str(root),'source_root':str(source),'receipt_root':str(native),'prefix':str(native/'prefix'),
        'input_lock_sha256':contract['source_input_lock_sha256'],'stdout_log':str(root/'NEVER_CREATED_stdout'),
        'stdout_sha256':'0'*64,'command_log_dir':str(native/'logs/NEVER_CREATED')}
    job=root/'pending-envelope.json';job.write_text(json.dumps(pending))
    tool_args=[]
    for name in ['ar','nm','readelf','signer']:
        tool_args += ['--'+name,str(root/'NEVER_CREATED_tool_directory'/name),'--'+name+'-sha256','0'*64]
    base=[sys.executable,namespace/'shaderc.py','accept','--source-root',source,'--source-lock-sha256',contract['source_input_lock_sha256'],
        '--receipt-root',native,'--prefix',native/'prefix','--sdk-root',root/'NEVER_CREATED_SDK','--resource-dir',root/'NEVER_CREATED_resource',
        '--job-receipt',job,'--job-receipt-sha256',adapter.digest(job),'--output',cache/'pending-not-created','--tmp-dir',temp,*tool_args]
    rejected=cli(base,'pending-full-rejects-before-live-inputs',env,3);assert rejected['status']=='NOTREADY'
    assert not native.exists() and not (cache/'pending-not-created').exists()
    pending['extra_accepted_boolean']=True;job.write_text(json.dumps(pending));base[base.index('--job-receipt-sha256')+1]=adapter.digest(job)
    cli(base,'unknown-executor-boolean-schema-rejects',env,2)
    job.write_text(json.dumps(pending));base[base.index('--job-receipt-sha256')+1]='0'*64
    cli(base,'changed-executor-hash-rejects',env,2)
    # No accepted packet, native artifact, SDK program or resurrected migrated file.
    assert not list(root.rglob('shaderc-accepted.json'))
    assert not list(root.rglob('*.o')) and not list(root.rglob('*.so'))
    temporary=str(root)
os.environ.clear();os.environ.update(original_env)
assert not Path(temporary).exists()
assert adapter.self_seal()==seal
result={'status':'PASS short source-only contract/negative checks','adapter_input_lock_sha256':seal,
    'original_and_Unicode_space_source_contracts':copies,'actual_internal_rejections':cases,'actual_external_commands':commands,
    'metadata_placeholder_prefix_is_NOT_NATIVE':True,'accepted_receipt_emitted':False,'temporary_tree_cleaned':True,
    'native_compile_count':0,'SDK_or_native_tool_execution_count':0,'live_native_prefix_read':False,
    'actual_adapter_native_acceptance':'NOTRUN','Blender_bpy_HAP_GPU_pixels_release':'NOTRUN'}
(args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':result['status'],'adapter_input_lock_sha256':seal,'internal_rejections':len(cases),
                  'external_commands':len(commands),'accepted_receipt_emitted':False,'native_compile_count':0},indent=2))
