# SPDX-License-Identifier: GPL-2.0-or-later
import json
import os
from pathlib import Path
import shutil
import stat
from source_guard import HERE, repo_file, sha, inventory
from io_utils import write_json
from source_tree import sources

BRIDGES=['libbf_intern_draco_bridge.so','libbf_intern_meshopt_bridge.so']


def install(args):
    p=args.prefix
    original=[]
    for pc in sorted(p.rglob('*.pc')):
        before=pc.read_bytes();text=before.decode()
        if str(p) in text or pc.name=='draco.pc':
            saved=args.root/'metadata-original'/pc.relative_to(p);saved.parent.mkdir(parents=True,exist_ok=True)
            if saved.exists() and saved.read_bytes()!=before:
                raise ValueError('Original metadata changed')
            if not saved.exists(): saved.write_bytes(before)
            output=[]
            for line in text.splitlines():
                output.append('prefix=${pcfiledir}/../..' if line.startswith('prefix=') else line.replace(str(p),'${prefix}'))
            if pc.name=='draco.pc':
                output=['prefix=${pcfiledir}/../..','libdir=${prefix}/lib','includedir=${prefix}/include','',
                        'Name: draco','Description: Complete source-built Draco codec, baseline features','Version: 1.5.7',
                        'Libs: -L${libdir} -ldraco','Libs.private: -lm','Cflags: -I${includedir}']
            pc.write_text('\n'.join(output)+'\n')
            original.append({'path':pc.relative_to(p).as_posix(),'before_sha256':sha(saved),'after_sha256':sha(pc)})
    pcdir=p/'lib/pkgconfig';pcdir.mkdir(exist_ok=True)
    for name,version,libs in [('meshoptimizer','1.1','-lmeshoptimizer'),('BlenderGltfBridges','1','-lbf_intern_draco_bridge -lbf_intern_meshopt_bridge')]:
        (pcdir/(name+'.pc')).write_text('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\nName: '+name+'\nDescription: Source-built native '+name+'\nVersion: '+version+'\nLibs: -L${libdir} '+libs+'\nCflags: -I${includedir}\n')
    for dep in sources():
        for row in dep['notices']:
            target=p/'share/licenses/blender-gltf'/dep['name']/row['source_path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo_file(row['mirror']),target)
    addon=p/'share/blender-gltf/scripts/addons_core/io_scene_gltf2'
    addon_rows=[r for r in json.loads((HERE/'inputs.lock.json').read_text())['sealed_files'] if r['path'].startswith('scripts/addons_core/io_scene_gltf2/')]
    for row in addon_rows:
        relative=Path(row['path']).relative_to('scripts/addons_core/io_scene_gltf2')
        target=addon/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repo_file(row['path']),target)
    runtime=[]
    for namespace in ['share/blender-gltf/host/lib','share/blender-gltf/hap/libs/arm64-v8a','share/blender-gltf/scripts/addons_core/io_scene_gltf2']:
        target=p/namespace;target.mkdir(parents=True,exist_ok=True)
        for name in BRIDGES:
            shutil.copyfile(p/'lib'/name,target/name)
            (target/name).chmod(0o755)
            if sha(target/name)!=sha(p/'lib'/name):raise ValueError('Signed DLL copy bytes differ')
            runtime.append({'path':(target/name).relative_to(p).as_posix(),'sha256':sha(target/name)})
    write_json(args.root/'metadata.json',{'normalization':original,'runtime_copies':runtime,
               'host_execution':'NOTRUN until actual native acceptance','HAP_execution':'NOTRUN',
               'bpy_execution':'Separate parent postlink only; use actual SYSTEM_LIBS/scripts/addons_core/io_scene_gltf2 or dirname(LOCAL)/lib resolver paths. BLENDER_SYSTEM_RESOURCES does not redirect SYSTEM_LIBS.',
               'addon_source_files':len(addon_rows)})


def prefix_inventory(prefix):
    prefix=Path(prefix)
    if prefix.is_symlink() or not prefix.is_dir():raise ValueError('Real installed prefix required')
    rows=[]
    for p in sorted(prefix.rglob('*')):
        relative=p.relative_to(prefix).as_posix();mode=p.lstat().st_mode
        if stat.S_ISLNK(mode):
            link=os.readlink(p)
            # Upstream Draco executable VERSION installs these same-directory aliases.
            if p.parent!=prefix/'bin' or p.name not in ['draco_encoder','draco_decoder'] or \
               link!=p.name+'-1.5.7' or not p.resolve().is_file() or not p.resolve().is_relative_to(prefix.resolve()) or \
               (p.parent/link).is_symlink():
                raise ValueError('Unexpected installed link: '+relative)
            rows.append({'path':relative,'symlink':link,'target_sha256':sha(p),'target_size':p.stat().st_size})
        elif stat.S_ISREG(mode):rows.append({'path':relative,'size':p.stat().st_size,'sha256':sha(p)})
        elif not stat.S_ISDIR(mode):raise ValueError('Installed special file: '+relative)
    return rows


def freeze(args):
    rows=prefix_inventory(args.prefix)
    write_json(args.root/'prefix-seal.json',{'files':rows,'input_lock_sha256':sha(HERE/'inputs.lock.json')})


def verify(args):
    value=json.loads((args.root/'prefix-seal.json').read_text())
    if value['input_lock_sha256']!=sha(HERE/'inputs.lock.json') or value['files']!=prefix_inventory(args.prefix):
        raise ValueError('Actual installed prefix no longer matches signed frozen bytes')
    return value
