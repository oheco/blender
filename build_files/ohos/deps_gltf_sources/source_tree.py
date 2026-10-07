# SPDX-License-Identifier: GPL-2.0-or-later
import json
from pathlib import Path
import shutil
import tempfile
from source_guard import HERE, repo_file, sha, extract_zip, reconstruct, verify_tree, inventory
from io_utils import write_json


def sources():
    return json.loads((HERE/'sources.lock.json').read_text())['sources']


def bridge_files():
    lock=json.loads((HERE/'inputs.lock.json').read_text())
    return [r for r in lock['sealed_files'] if r['path'].startswith(('intern/draco_bridge/','intern/meshoptimizer_bridge/'))]


def verify_bridges(tree, patched=True):
    changes={r['path']:r for r in json.loads((HERE/'patches.lock.json').read_text())['files']}
    expected=[]
    for row in bridge_files():
        p=tree/row['path']
        h=changes[row['path']]['after_sha256'] if patched and row['path'] in changes else row['sha256']
        if not p.is_file() or p.is_symlink() or sha(p)!=h:
            raise ValueError('Sealed actual Blender wrapper source drift')
        expected.append(row['path'])
    if {r['path'] for r in inventory(tree)} != set(expected):
        raise ValueError('Unexpected Blender wrapper inputs')
    return {'files':len(expected),'patched':patched,'actual_Blender_sources':True}


def prepare(args, runner):
    destination=args.root/'sources'
    bridge=args.root/'blender-bridge-source'
    if destination.exists():
        if not (args.root/'sources.json').is_file():
            raise ValueError('Incomplete prepared sources')
        rows=[verify_tree(destination/d['name'],d) for d in sources()]
        verify_bridges(bridge)
        return json.loads((args.root/'sources.json').read_text())
    with tempfile.TemporaryDirectory(prefix='gltf-complete-source-',dir=args.tmp_dir) as td:
        stage=Path(td)
        rows=[]
        for dep in sources():
            archive=stage/dep['filename']
            reconstruct(dep,archive)
            tree=stage/'sources'/dep['name']
            extract_zip(archive,tree,dep['archive_root'])
            rows.append(verify_tree(tree,dep))
        b=stage/'blender-bridge-source'
        for row in bridge_files():
            p=b/row['path'];p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo_file(row['path']),p)
        verify_bridges(b,False)
        for row in json.loads((HERE/'patches.lock.json').read_text())['files']:
            patch=repo_file(row['patch'])
            runner.run([args.git,'apply','--check',patch], 'bridge-patch-check-'+Path(row['path']).stem,cwd=b)
            runner.run([args.git,'apply',patch], 'bridge-patch-forward-'+Path(row['path']).stem,cwd=b)
            if sha(b/row['path'])!=row['after_sha256']:
                raise ValueError('Bridge patch after hash mismatch')
            runner.run([args.git,'apply','--reverse','--check',patch], 'bridge-patch-reverse-check-'+Path(row['path']).stem,cwd=b)
        verify_bridges(b)
        reverse=stage/'reverse';shutil.copytree(b,reverse)
        for row in reversed(json.loads((HERE/'patches.lock.json').read_text())['files']):
            runner.run([args.git,'apply','--reverse',repo_file(row['patch'])], 'bridge-patch-reverse-'+Path(row['path']).stem,cwd=reverse)
        verify_bridges(reverse,False)
        (stage/'sources').rename(destination)
        b.rename(bridge)
    result={'result':'PASS complete original archives, inventories, actual Blender wrapper patch forward/reverse hashes',
            'sources':rows,'bridges':verify_bridges(bridge),'temporary_tree_cleaned':True,
            'native_full':'NOTRUN','bpy':'NOTRUN','HAP':'NOTRUN'}
    write_json(args.root/'sources.json',result)
    return result
