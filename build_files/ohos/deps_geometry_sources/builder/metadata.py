# SPDX-License-Identifier: GPL-2.0-or-later
"""Relative imports from actual new static archives; retain upstream metadata."""
import json
from pathlib import Path
import shutil
from io_utils import HERE, repo_file, sha, sources, write_json

ARCHIVES = {'tbb': ['libtbb.a', 'libtbbmalloc.a'], 'gmp': ['libgmp.a', 'libgmpxx.a'],
            'opensubdiv': ['libosdCPU.a', 'libosdGPU.a'], 'manifold': ['libmanifold.a']}
VERSIONS = {'TBB': '2022.3.0', 'GMP': '6.3.0', 'OpenSubdiv': '3.7.0', 'manifold': '3.5.2'}


def pc(name, version, libs, private='-pthread -ldl -lm', requires='', cflags=''):
    return ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\n'
            f'Name: {name}\nDescription: Native static PIC {name}\nVersion: {version}\n'
            f'Libs: -L${{libdir}} {libs}\nLibs.private: {private}\nRequires.private: {requires}\n'
            f'Cflags: -I${{includedir}} {cflags}\n')


def target(name, archive, links, defines=''):
    return (f'if(TARGET {name})\n  get_target_property(_existing {name} IMPORTED_LOCATION)\n'
            f'  get_target_property(_release {name} IMPORTED_LOCATION_RELEASE)\n'
            '  if(NOT _existing)\n    set(_existing "${_release}")\n  endif()\n'
            f'  if(NOT _existing STREQUAL "${{_geometry_prefix}}/lib/{archive}")\n'
            f'    message(FATAL_ERROR "{name} already belongs to another prefix")\n  endif()\n'
            f'else()\n  if(NOT EXISTS "${{_geometry_prefix}}/lib/{archive}")\n'
            f'    message(FATAL_ERROR "Actual {archive} missing")\n  endif()\n'
            f'  add_library({name} STATIC IMPORTED)\n  set_target_properties({name} PROPERTIES\n'
            f'    IMPORTED_LOCATION "${{_geometry_prefix}}/lib/{archive}"\n'
            '    INTERFACE_INCLUDE_DIRECTORIES "${_geometry_prefix}/include"\n'
            f'    INTERFACE_LINK_LIBRARIES "{links}"\n    INTERFACE_COMPILE_DEFINITIONS "{defines}")\nendif()\n')


def config(body, dependencies=''):
    return ('block(SCOPE_FOR VARIABLES)\ninclude(CMakeFindDependencyMacro)\nfind_dependency(Threads)\n'
            'get_filename_component(_geometry_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n' + dependencies + body + 'endblock()\n')


def version(value):
    return (f'set(PACKAGE_VERSION "{value}")\n'
            'if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)\n  set(PACKAGE_VERSION_COMPATIBLE FALSE)\n'
            'else()\n  set(PACKAGE_VERSION_COMPATIBLE TRUE)\n'
            '  if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)\n    set(PACKAGE_VERSION_EXACT TRUE)\n  endif()\nendif()\n')


def templates(name):
    pcs, configs = {}, {}
    if name == 'tbb':
        pcs['tbb'] = pc('tbb', '2022.3.0', '-ltbb', cflags='-D__TBB_NO_IMPLICIT_LINKAGE')
        pcs['tbbmalloc'] = pc('tbbmalloc', '2022.3.0', '-ltbbmalloc')
        configs['TBB'] = config(target('TBB::tbb', 'libtbb.a', 'Threads::Threads;dl;m', '__TBB_NO_IMPLICIT_LINKAGE') +
                                target('TBB::tbbmalloc', 'libtbbmalloc.a', 'Threads::Threads;dl;m'))
    elif name == 'gmp':
        pcs['gmp'] = pc('gmp', '6.3.0', '-lgmp', private='')
        pcs['gmpxx'] = pc('gmpxx', '6.3.0', '-lgmpxx', private='', requires='gmp')
        configs['GMP'] = config(target('GMP::gmp', 'libgmp.a', '') + target('GMP::gmpxx', 'libgmpxx.a', 'GMP::gmp'))
    elif name == 'opensubdiv':
        pcs['opensubdiv'] = pc('opensubdiv', '3.7.0', '-losdGPU -losdCPU', requires='tbb', cflags='-DOPENSUBDIV_HAS_TBB')
        configs['OpenSubdiv'] = config(target('OpenSubdiv::osdCPU_static', 'libosdCPU.a', 'TBB::tbb;Threads::Threads;m', 'OPENSUBDIV_HAS_TBB') +
                                      target('OpenSubdiv::osdGPU_static', 'libosdGPU.a', 'OpenSubdiv::osdCPU_static', 'OPENSUBDIV_HAS_TBB'),
            'find_dependency(TBB 2022.3.0 EXACT CONFIG PATHS "${_geometry_prefix}/lib/cmake/TBB" NO_DEFAULT_PATH)\n')
    elif name == 'manifold':
        pcs['manifold'] = pc('manifold', '3.5.2', '-lmanifold', requires='tbb', cflags='-DMANIFOLD_PAR=1')
        configs['manifold'] = config(target('manifold::manifold', 'libmanifold.a', 'TBB::tbb;Threads::Threads;m', 'MANIFOLD_PAR=1'),
            'find_dependency(TBB 2022.3.0 EXACT CONFIG PATHS "${_geometry_prefix}/lib/cmake/TBB" NO_DEFAULT_PATH)\n')
    else:
        raise ValueError('Unknown geometry component')
    return pcs, configs


def install(name, args, root):
    prefix = args.prefix
    for leaf in ARCHIVES[name]:
        if not (prefix / 'lib' / leaf).is_file():
            raise ValueError('Actual source-built archive missing: ' + leaf)
    journal = []
    previously_adapted = set()
    for prior in (root / 'metadata').glob('*.json'):
        for row in json.loads(prior.read_text()).get('journal', []):
            if row.get('action') in ['relative-PC-adapter', 'relative-CONFIG-adapter']:
                previously_adapted.add(row['path'])
    # Save genuine upstream outputs, then provide the declared relative interface.
    # Remove unused .la descriptors; never mutate ELF or archive bytes after signing.
    for file in sorted(prefix.rglob('*')):
        if not file.is_file() or file.is_symlink() or file.suffix not in ('.cmake', '.pc', '.la'):
            continue
        relative = file.relative_to(prefix)
        if relative.as_posix() in previously_adapted:
            continue
        original = root / 'upstream-metadata-originals' / relative
        if original.exists():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, original)
        row = {'path': relative.as_posix(), 'original_sha256': sha(original), 'original_size': original.stat().st_size}
        if file.suffix == '.la':
            file.unlink()
            row.update(action='omit-unused-libtool-descriptor', derived_sha256=None,
                       reason='Static CMAKE/PC consumers do not use .la; absolute libdir is not portable')
        else:
            row['action'] = 'preserve-upstream-original-before-relative-CONFIG-adapter'
        journal.append(row)
    pcs, configs = templates(name)
    for package, content in pcs.items():
        file = prefix / 'lib/pkgconfig' / (package + '.pc')
        file.parent.mkdir(parents=True, exist_ok=True)
        before = sha(file) if file.exists() else None
        file.write_text(content)
        journal.append({'path': file.relative_to(prefix).as_posix(), 'original_sha256': before,
                        'derived_sha256': sha(file), 'action': 'relative-PC-adapter'})
    for package, content in configs.items():
        folder = prefix / 'lib/cmake' / package
        folder.mkdir(parents=True, exist_ok=True)
        for leaf, data in [(package + 'Config.cmake', content), (package + 'ConfigVersion.cmake', version(VERSIONS[package]))]:
            file = folder / leaf
            before = sha(file) if file.exists() else None
            file.write_text(data)
            journal.append({'path': file.relative_to(prefix).as_posix(), 'original_sha256': before,
                            'derived_sha256': sha(file), 'action': 'relative-CONFIG-adapter'})
    dep = next(d for d in sources()['sources'] if d['name'] == name)
    for row in dep['notices']:
        original = repo_file(row['mirror'])
        if sha(original) != row['sha256']:
            raise ValueError('Original upstream notice drift')
        dest = prefix / 'share/licenses' / name / row['source_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, dest)
    for filename in ('sources.lock.json', 'recipe.json', 'ATTRIBUTION.md'):
        dest = prefix / 'share/geometry-builder' / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / filename, dest)
    write_json(root / 'metadata' / (name + '.json'), {'component': name, 'journal': journal,
                'archives_not_mutated': True, 'elf_not_mutated': True})
