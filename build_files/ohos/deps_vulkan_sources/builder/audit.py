# SPDX-License-Identifier: GPL-2.0-or-later
"""Real archive/ELF/PIC/source lineage and byte-identical prefix migration."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from io_utils import HERE, sha, write_json
import metadata
import source_tree


def file_inventory(prefix):
    rows = []
    for file in sorted(prefix.rglob('*')):
        rel = file.relative_to(prefix).as_posix()
        if file.is_symlink():
            target = os.readlink(file)
            if Path(target).is_absolute() or prefix.resolve() not in file.resolve().parents:
                raise ValueError('Installed link must remain relative within prefix')
            rows.append({'path':rel,'symlink':target})
        elif file.is_file():
            rows.append({'path':rel,'sha256':sha(file),'size':file.stat().st_size})
    return rows


def freeze_prefix(args, root):
    write_json(root/'prefix-files.json',{'files':file_inventory(args.prefix),'input_lock_sha256':sha(HERE/'inputs.lock.json')})


def verify_prefix(args, root):
    saved = json.loads((root/'prefix-files.json').read_text())
    if saved['files'] != file_inventory(args.prefix) or saved['input_lock_sha256'] != sha(HERE/'inputs.lock.json'):
        raise ValueError('Installed prefix bytes or sealed lineage changed')


def source_guard(root):
    return [source_tree.verify_tree(root/'sources'/name,name,patched=True) for name in source_tree.ACTIVE]


def elf(args, runner, path, label, loader=False, require_n1=False):
    header = runner.run([args.readelf,'-h',path],label+'-header')
    sections = runner.run([args.readelf,'-S',path],label+'-sections')
    dynamic = runner.run([args.readelf,'-d',path],label+'-dynamic')
    symbols = runner.run([args.nm,'--demangle',path],label+'-symbols')
    needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]',dynamic)
    expected = {'libc.so',args.loader.name} if loader else {'libc.so'}
    if not re.search(r'Machine:\s+AArch64',header) or '.codesign' not in sections or set(needed) != expected or re.search(r'RPATH|RUNPATH|TEXTREL',dynamic):
        raise ValueError('Native signed ELF, dynamic closure or PIC failure: '+str(path))
    if re.search(r'std::__(?:h|1|blender20)::',symbols) or (require_n1 and 'std::__n1::' not in symbols):
        raise ValueError('Native SDK15 standard-library ABI failure')
    runner.run([args.signer,'display-sign','-inFile',path],label+'-signature')
    return {'path':str(path),'sha256':sha(path),'needed':needed,'machine':'AArch64','codesign':True,'sdk15_n1_symbols':symbols.count('std::__n1::'),'rpath':False,'textrel':False}


def installed_dsos(args, root, runner):
    records = []
    for file in sorted((args.prefix/'lib').glob('*.so*')):
        if file.is_symlink():
            continue
        if 'vulkan' in file.name.lower():
            raise ValueError('Bundled Vulkan loader forbidden')
        candidates = {p.resolve() for p in (root/'build').rglob(file.name) if p.is_file() and not p.is_symlink() and sha(p)==sha(file)}
        if len(candidates)!=1:
            raise ValueError('Signed build/install DSO bytes differ or ambiguous origin: '+file.name)
        record = elf(args,runner,file,'installed-'+file.name)
        record['build_path']=str(candidates.pop())
        records.append(record)
    return records


def artifacts(args, root, runner):
    verify_prefix(args,root)
    metadata.verify_headers(args,root)
    archives = []
    for file in sorted((args.prefix/'lib').glob('*.a')):
        if file.open('rb').read(8)!=b'!<arch>\n':
            raise ValueError('Real complete archive required')
        members = runner.run([args.ar,'t',file],'archive-members-'+file.stem).splitlines()
        headers = runner.run([args.readelf,'-h',file],'archive-header-'+file.stem)
        machines = re.findall(r'Machine:\s+([^\n]+)',headers)
        types = re.findall(r'Type:\s+(\S+)',headers)
        if len(machines)!=len(members) or any(x.strip()!='AArch64' for x in machines) or any(x!='REL' for x in types):
            raise ValueError('Archive contains non-native/missing relocatable member')
        symbols = runner.run([args.nm,'--demangle',file],'archive-symbols-'+file.stem)
        if re.search(r'std::__(?:h|1|blender20)::',symbols):
            raise ValueError('Wrong SDK C++ ABI in archive')
        if file.name=='libshaderc_combined.a':
            for api in ['shaderc_compiler_initialize','shaderc_compile_into_spv','spvtools::Optimizer::Run','spvValidate']:
                if not any(re.search(r'\b[TtWw]\s+'+re.escape(api),line) for line in symbols.splitlines()):
                    raise ValueError('Genuine combined API implementation absent: '+api)
            if 'std::__n1::' not in symbols:
                raise ValueError('Combined archive not linked to SDK15 ABI')
        if file.name=='libVulkanSafeStruct.a':
            undef = runner.run([args.nm,'--undefined-only',file],'opaque-handle-symbols')
            if re.search(r'OH_(?:NativeBuffer|NativeWindow).*(?:Reference|Unreference|Acquire|Release)',undef):
                raise ValueError('Borrowed pointer patch gained unrequested native ref/unref ownership')
        archives.append({'path':file.name,'sha256':sha(file),'members':len(members),'signed':False,'n1_symbols':symbols.count('std::__n1::')})
    compile_rows = []
    for name in ['shaderc','vulkan-utility','spirv-reflect']:
        database=root/'build'/name/'compile_commands.json'
        rows=json.loads(database.read_text())
        if not rows or any('-fPIC' not in row.get('command',' '.join(row.get('arguments',[]))) for row in rows):
            raise ValueError('Actual library PIC compile evidence missing')
        compile_rows.append({'group':name,'units':len(rows),'sha256':sha(database)})
    for file in args.prefix.rglob('*'):
        if file.is_file() and file.suffix in ('.pc','.cmake'):
            if any(route in file.read_text() for route in [str(args.root),str(args.prefix),'/storage/Users','/data/storage']):
                raise ValueError('Absolute source/build/prefix route in installed metadata: '+str(file))
    dsos=installed_dsos(args,root,runner)
    accepted=json.loads((root/'acceptance.json').read_text())
    for record in accepted['consumers']:
        for artifact in record['artifacts']:
            if sha(Path(artifact['path']))!=artifact['sha256']:
                raise ValueError('Signed accepted consumer changed')
    result={'result':'PASS actual native archives, DSOs, public headers, consumers, PIC and metadata/source lineage',
            'archives':archives,'installed_dsos':dsos,'compile':compile_rows,'sources':source_guard(root),
            'pic_modules_dlopened':False,'static_cpp_runtime_module_ownership':'Separate application integration scope',
            'gpu_dispatch_draw_pixels_wsi':'NOT_TESTED'}
    write_json(root/'artifacts.json',result)
    return result


def migrate(args, root, runner, consumer):
    verify_prefix(args,root)
    original=file_inventory(args.prefix)
    with tempfile.TemporaryDirectory(prefix='vulkan-native-migrate-',dir=runner.tmp) as td:
        moved=Path(td)/'Vulkan 图形 prefix with spaces'
        shutil.copytree(args.prefix,moved,symlinks=True)
        if file_inventory(moved)!=original:
            raise ValueError('Moved prefix bytes/relative symlinks differ')
        results=[consumer(Path(td)/('fresh '+mode+' consumer'),moved,mode,'migrated-'+mode,
                          forbidden=[str(args.prefix),str(root/'sources'),str(root/'build')]) for mode in ['CMAKE','PC']]
        result={'result':'PASS real byte-identical moved Unicode/space CMAKE/PC native library consumers',
                'preserved_files':len(original),'consumers':results,'pic_modules_dlopened':False,'gpu_wsi_pixels':'NOT_TESTED'}
    result['temporary_tree_cleaned']=True
    write_json(root/'migration.json',result)
    return result
