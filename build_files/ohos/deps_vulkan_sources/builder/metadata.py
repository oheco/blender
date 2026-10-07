# SPDX-License-Identifier: GPL-2.0-or-later
"""Relative prefix-owned imports; preserve real upstream metadata before repair."""
import json
from pathlib import Path
import re
import shutil
from io_utils import HERE, REPO, repo_file, sha, sources, write_json

ARCHIVES = {
    'shaderc': ['libshaderc_combined.a', 'libSPIRV-Tools.a', 'libSPIRV-Tools-opt.a', 'libglslang.a', 'libglslang-default-resource-limits.a'],
    'vulkan-utility': ['libVulkanSafeStruct.a', 'libVulkanLayerSettings.a'],
    'spirv-reflect': ['libspirv-reflect-static.a'],
}


def pc(name, version, libs='', requires='', defines='', private='-pthread -lm -ldl'):
    # pcfiledir is already escaped by native pkgconf: additional quotes double-escape spaces.
    return ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n'
            f'Name: {name}\nDescription: Portable pinned OHOS library profile\nVersion: {version}\n'
            f'Libs: -L${{libdir}} {libs}\nLibs.private: {private}\nRequires.private: {requires}\n'
            f'Cflags: -I${{includedir}} {defines}\n')


def config(target, archive, dependencies='', defines=''):
    return ('# Recipe-added relative static import; not an upstream export.\nblock(SCOPE_FOR VARIABLES)\n'
            'include(CMakeFindDependencyMacro)\nfind_dependency(Threads)\n' + dependencies +
            'get_filename_component(_vulkan_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n'
            f'if(NOT TARGET {target})\n  add_library({target} STATIC IMPORTED)\n'
            f'  set_target_properties({target} PROPERTIES IMPORTED_LOCATION "${{_vulkan_prefix}}/lib/{archive}"\n'
            '    INTERFACE_INCLUDE_DIRECTORIES "${_vulkan_prefix}/include"\n'
            f'    INTERFACE_COMPILE_DEFINITIONS "{defines}"\n'
            '    INTERFACE_LINK_LIBRARIES "Threads::Threads;m;dl")\nendif()\nendblock()\n')


def templates():
    pcs = {
        'shaderc_combined': pc('Shaderc combined source pin (recipe adapter)', '2025.4', '-lshaderc_combined'),
        'spirv-reflect': pc('SPIRV Reflect source SDK tag', '1.4.341.0', '-lspirv-reflect-static', 'SPIRV-Headers', '-DSPIRV_REFLECT_USE_SYSTEM_SPIRV_H'),
        'vulkan-utility': pc('Vulkan Utility Libraries', '1.4.341', '-lVulkanSafeStruct -lVulkanLayerSettings', 'Vulkan-Headers', '-DVK_USE_PLATFORM_OHOS'),
        'Vulkan-Headers': pc('Vulkan Headers', '1.4.341', private='', defines='-DVK_USE_PLATFORM_OHOS'),
        'SPIRV-Headers': pc('SPIRV Headers upstream project', '1.5.5', private=''),
        'vma': pc('Vulkan Memory Allocator source pin', '3.2.1', requires='Vulkan-Headers', private=''),
        'glslang': pc('glslang upstream project', '15.4.0', '-lglslang -lglslang-default-resource-limits', 'SPIRV-Tools-opt'),
        'SPIRV-Tools': pc('SPIRV Tools upstream PC version', '2026.1.1', '-lSPIRV-Tools', 'SPIRV-Headers'),
        'SPIRV-Tools-opt': pc('SPIRV Tools optimizer upstream PC version', '2026.1.1', '-lSPIRV-Tools-opt', 'SPIRV-Tools'),
    }
    configs = {
        'ShaderC': config('ShaderC::shaderc_combined', 'libshaderc_combined.a'),
        'SPIRVReflect': config('SPIRVReflect::SPIRVReflect', 'libspirv-reflect-static.a', 'find_dependency(SPIRV-Headers CONFIG)\n')
    }
    configs['SPIRVReflect'] = configs['SPIRVReflect'].replace('INTERFACE_COMPILE_DEFINITIONS ""', 'INTERFACE_COMPILE_DEFINITIONS "SPIRV_REFLECT_USE_SYSTEM_SPIRV_H"').replace('Threads::Threads;m;dl', 'SPIRV-Headers::SPIRV-Headers;Threads::Threads;m;dl')
    return pcs, configs


def normalize(args, root):
    prefix = args.prefix
    for leaves in ARCHIVES.values():
        for leaf in leaves:
            file = prefix / 'lib' / leaf
            if not file.is_file() or file.open('rb').read(8) != b'!<arch>\n':
                raise ValueError('Real installed archive absent; never generate a stub: ' + leaf)
    journal = []
    original_installed = {Path(line) for manifest in (root/'build').glob('*/install_manifest.txt')
                          for line in manifest.read_text().splitlines()}
    for file in sorted(prefix.rglob('*')):
        if file not in original_installed or not file.is_file() or file.suffix not in ('.pc', '.cmake'):
            continue
        original = file.read_bytes()
        receipt = root / 'upstream-metadata-originals' / file.relative_to(prefix)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        if receipt.exists() and receipt.read_bytes() != original:
            raise ValueError('Conflicting original metadata journal')
        receipt.write_bytes(original)
        text = original.decode('utf8')
        if file.suffix == '.pc':
            text = text.replace(str(prefix), '${prefix}')
            text = re.sub(r'^prefix=.*$', 'prefix=${pcfiledir}/../..', text, flags=re.M)
            if not re.search(r'^prefix=', text, re.M):
                text = 'prefix=${pcfiledir}/../..\n' + text
        # Native upstream CMake exports must themselves use relative _IMPORT_PREFIX.
        if file.suffix == '.cmake' and str(prefix) in text:
            raise ValueError('Unexpected upstream absolute export; review before changing semantics')
        if any(path in text for path in [str(root / 'sources'), str(root / 'build')]):
            raise ValueError('Source/build route in installed metadata')
        if text.encode() != original:
            file.write_text(text)
        journal.append({'path':str(file.relative_to(prefix)), 'upstream_sha256':sha(receipt), 'derived_sha256':sha(file), 'upstream_snapshot':str(receipt)})
    pcs, configs = templates()
    for name, text in pcs.items():
        path = prefix / 'lib/pkgconfig' / (name + '.pc')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            journal.append({'path':str(path.relative_to(prefix)), 'upstream_sha256':sha(path), 'recipe_adapter_version':re.search(r'^Version: (.*)$',text,re.M).group(1)})
        path.write_text(text)
        # Embedded Shaderc overwrites the header PC with an empty top project
        # version; mirror the explicit source-project adapter to both entries.
        other = prefix / 'share/pkgconfig' / (name + '.pc')
        if other.exists():
            other.write_text(text)
            journal.append({'path':str(other.relative_to(prefix)), 'recipe_adapter':'same relative PC as lib/pkgconfig', 'derived_sha256':sha(other)})
    for name, text in configs.items():
        folder = prefix / 'lib/cmake' / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (name + 'Config.cmake')).write_text(text)
    write_json(root / 'metadata-normalization.json', {'upstream':journal, 'recipe_added':['ShaderC CMake export','Reflect CMake export','relative PC interfaces'],
               'source_pin_vs_upstream_version':'Shaderc original PC2023.8.1 retained in journal; source-pin adapter PC2025.4 is explicitly recipe-provided'})
    for dep in sources()['sources']:
        for row in dep['notices']:
            path = prefix / 'share/licenses' / dep['name'] / row['source_path']
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo_file(row['mirror']),path)
    shutil.copyfile(HERE / 'sources.lock.json', prefix / 'share/vulkan-builder-sources.lock.json')
    shutil.copyfile(args.sdk_root / 'NOTICE.txt', prefix / 'share/licenses/SDK15-NOTICE.txt')
    verify_headers(args,root)


def verify_headers(args, root):
    for name, src, dest in [('vulkan-headers','include','include'),('vulkan-headers','registry','share/vulkan/registry'),
                            ('spirv-headers','include/spirv','include/spirv'),('vma','include','include')]:
        for file in (root/'sources'/name/src).rglob('*'):
            if file.is_file():
                installed = args.prefix / dest / file.relative_to(root/'sources'/name/src)
                if not installed.is_file() or sha(installed) != sha(file):
                    raise ValueError('Complete unchanged public source/header/registry installation missing: '+str(installed))
    for leaf,source in [('spirv_reflect.h','spirv-reflect/spirv_reflect.h')]:
        if sha(args.prefix/'include'/leaf) != sha(root/'sources'/source):
            raise ValueError('Original public Reflect header changed')
