# SPDX-License-Identifier: GPL-2.0-or-later
"""Readonly adoption of exact prepared source, across recipe revisions/modes."""
import fcntl,json,os,shutil,tempfile
from pathlib import Path
from common import HERE,sha,row,write_json,intersects
import source_tree

def adopt(a,runner):
    donor=a.source_cache
    if donor is None:raise ValueError('Explicit source cache required')
    source,data,records=source_tree.ledger();dest=a.root/'sources'/data['archive_root']
    if (a.root/'prepared-source.json').exists():return source_tree.guard(a)
    cache=Path(os.environ['XDG_CACHE_HOME']).resolve()
    if cache not in donor.resolve().parents or not donor.resolve().relative_to(cache).parts[0].startswith('blender-ohos-compiler-replay-') or any(p.is_symlink() for p in [donor,*donor.parents]):raise ValueError('Donor must be actual dedicated owned source cache')
    if intersects(donor,a.root) or intersects(donor,HERE) or intersects(donor,a.sdk_root) or intersects(donor,a.resource_dir):raise ValueError('Source cache overlaps output/provided/source input')
    if dest.exists():raise ValueError('Fresh cache adoption target required')
    owner_path=donor/'owner.json';marker_path=donor/'prepared-source.json';state_path=donor/'extraction-state.json';header_path=donor/'archive-header-metadata.json'
    with (donor/'operation.lock').open('r') as fd:
        fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        origin={key:row(p) for key,p in [('owner',owner_path),('prepared',marker_path),('extraction',state_path),('archive_metadata',header_path)]}
        owner=json.loads(owner_path.read_text());marker=json.loads(marker_path.read_text());state=json.loads(state_path.read_text());headers=json.loads(header_path.read_text());tree=donor/'sources'/data['archive_root']
        old_recipe=owner['recipe']['inputs_lock_sha256']
        if owner['kind']!='complete-native-ohos-compiler-source-replay' or owner['root']!=str(donor) or marker['recipe_inputs_sha256']!=old_recipe or marker['source_root']!=str(tree):raise ValueError('Donor historical owner/source binding invalid')
        if state['status'] not in ['COMPLETE','COPIED_COMPLETE_PATCHED'] or state['identity']['inventory_sha256']!=source['inventory_sha256'] or marker['inventory_sha256']!=source['inventory_sha256'] or state['identity']['archive_sha256']!=source['LLVM']['sha256']:raise ValueError('Donor not complete exact original source identity')
        if marker['patch_before']!=source['patch']['upstream_sha256'] or marker['patch_after']!=source['patch']['patched_sha256']:raise ValueError('Source cache fixed patch differs')
        if headers['original_archive_sha256']!=source['LLVM']['sha256'] or len(headers['source_metadata_records'])!=len(records):raise ValueError('Donor archive provenance incomplete')
        source_tree.inspect(tree,records,patched=True);source_tree.case_probe(a.tmp_dir)
        with tempfile.TemporaryDirectory(prefix='compiler-source-cache-copy-',dir=a.tmp_dir) as td:
            copied=Path(td)/'tree';shutil.copytree(tree,copied,symlinks=True)
            # Restore original archive times only in NEW source cache, keeping
            # original generated-source pairs ordered as the TAR declared them.
            for meta in reversed(headers['source_metadata_records']):
                p=copied/meta['path'];stamp=int(meta['mtime']*1000000000)
                os.utime(p,ns=(stamp,stamp),follow_symlinks=False)
            source_tree.inspect(copied,records,patched=True);dest.parent.mkdir(parents=True,exist_ok=True)
            if copied.stat().st_dev!=dest.parent.stat().st_dev:raise ValueError('Atomic source cache publication requires same private filesystem')
            copied.rename(dest)
        for key,p in [('owner',owner_path),('prepared',marker_path),('extraction',state_path),('archive_metadata',header_path)]:
            if sha(p)!=origin[key]['sha256']:raise ValueError('Readonly source origin changed during copy')
        source_tree.inspect(tree,records,patched=True)
        (a.root/'archive-header-metadata.json').write_bytes(header_path.read_bytes())
    patch=source['patch'];target=dest/patch['target'];diff=HERE/'patches'/patch['patch_file']
    runner.run([a.git,'-C',dest,'apply','--reverse','--check',diff],'reused-patch-reverse-check')
    runner.run([a.git,'-C',dest,'apply','--reverse',diff],'reused-patch-reverse')
    if sha(target)!=patch['upstream_sha256']:raise ValueError('New copy patch exact reverse mismatch')
    runner.run([a.git,'-C',dest,'apply','--check',diff],'reused-patch-forward-check')
    runner.run([a.git,'-C',dest,'apply',diff],'reused-patch-forward')
    if sha(target)!=patch['patched_sha256']:raise ValueError('New copy fixed patch after mismatch')
    result=source_tree.inspect(dest,records,patched=True)
    write_json(a.root/'prepared-source.json',{'recipe_inputs_sha256':sha(HERE/'inputs.lock.json'),'source_root':str(dest),'inventory_sha256':source['inventory_sha256'],'patch_before':patch['upstream_sha256'],'patch_after':patch['patched_sha256'],'forward_reverse':'PASS actual NEW copied tree reverse/forward target; full patched copy guards','guard':result,'source_cache_origin':origin,'origin_recipe_sha256':old_recipe,'tar_redecoded':False,'native_acceptance_inherited':False})
    write_json(a.root/'extraction-state.json',{'identity':{'recipe_sha256':sha(HERE/'inputs.lock.json'),'inventory_sha256':source['inventory_sha256'],'archive_sha256':source['LLVM']['sha256'],'source_root':str(dest)},'status':'COPIED_COMPLETE_PATCHED','actual_members':len(records),'origin':origin,'tar_redecoded':False})
    return result
