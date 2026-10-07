# SPDX-License-Identifier: GPL-2.0-or-later
"""Metadata derives only from actual new archives and sealed driver constants.

Functionality adapted from development install_metadata.py/reused_metadata.py.
Fresh generated upstream bytes are preserved before changes; old development
metadata is never read, rebased, or silently made a future dependency.
"""
import json
from pathlib import Path
import re
import shutil
from io_utils import HERE, repo_file, sha, sources, write_json

ARCHIVES = {'zlib': ['libz.a'], 'imath': ['libImath-3_2.a'], 'tbb': ['libtbb.a', 'libtbbmalloc.a'],
            'blosc': ['libblosc.a'], 'fftw-double': ['libfftw3.a', 'libfftw3_threads.a'],
            'fftw-float': ['libfftw3f.a', 'libfftw3f_threads.a'], 'openvdb': ['libopenvdb.a']}
VERSIONS = {'ZLIB': '1.3.1', 'Imath': '3.2.2', 'TBB': '2022.3.0', 'Blosc': '1.21.1',
            'FFTW3': '3.3.10', 'FFTW3f': '3.3.10', 'OpenVDB': '13.0.0'}


def pc(name, version, libs, private='-pthread -lm', requires='', cflags=''):
    return ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\n'
            f'Name: {name}\nDescription: Native static PIC {name}\nVersion: {version}\n'
            f'Libs: -L${{libdir}} {libs}\nLibs.private: {private}\nRequires.private: {requires}\n'
            f'Cflags: -I${{includedir}} {cflags}\n')


def target(name, archive, links, includes='include', defines=''):
    return (f'if(NOT TARGET {name})\n  if(NOT EXISTS "${{_volume_prefix}}/lib/{archive}")\n'
            f'    message(FATAL_ERROR "Actual {archive} missing")\n  endif()\n'
            f'  add_library({name} STATIC IMPORTED)\n  set_target_properties({name} PROPERTIES\n'
            f'    IMPORTED_LOCATION "${{_volume_prefix}}/lib/{archive}"\n'
            f'    INTERFACE_INCLUDE_DIRECTORIES "${{_volume_prefix}}/{includes}"\n'
            f'    INTERFACE_LINK_LIBRARIES "{links}"\n    INTERFACE_COMPILE_DEFINITIONS "{defines}")\nendif()\n')


def config(body):
    return ('block(SCOPE_FOR VARIABLES)\ninclude(CMakeFindDependencyMacro)\nfind_dependency(Threads)\n'
            'get_filename_component(_volume_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n' + body + 'endblock()\n')


def version(value):
    return (f'set(PACKAGE_VERSION "{value}")\n'
            'if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)\n  set(PACKAGE_VERSION_COMPATIBLE FALSE)\n'
            'else()\n  set(PACKAGE_VERSION_COMPATIBLE TRUE)\n'
            '  if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)\n    set(PACKAGE_VERSION_EXACT TRUE)\n  endif()\nendif()\n')


def templates(name):
    pcs, configs = {}, {}
    if name == 'zlib':
        pcs['zlib'] = pc('zlib', '1.3.1', '-lz')
        configs['ZLIB'] = config(target('ZLIB::ZLIB', 'libz.a', 'Threads::Threads;m'))
    elif name == 'imath':
        pcs['Imath'] = pc('Imath', '3.2.2', '-lImath-3_2', cflags='-I${includedir}/Imath')
        configs['Imath'] = config(target('Imath::Imath', 'libImath-3_2.a', 'Threads::Threads;m', 'include/Imath'))
    elif name == 'tbb':
        for component in ['tbb', 'tbbmalloc']:
            pcs[component] = pc(component, '2022.3.0', '-l' + component, '-pthread -ldl -lm')
        configs['TBB'] = config(target('TBB::tbb', 'libtbb.a', 'Threads::Threads;dl;m', defines='__TBB_NO_IMPLICIT_LINKAGE') +
                                target('TBB::tbbmalloc', 'libtbbmalloc.a', 'Threads::Threads;dl;m'))
    elif name == 'blosc':
        pcs['blosc'] = pc('Blosc', '1.21.1', '-lblosc')
        configs['Blosc'] = config(target('Blosc::blosc', 'libblosc.a', 'Threads::Threads;m'))
    elif name.startswith('fftw'):
        suffix = 'f' if name == 'fftw-float' else ''
        lib, package = 'fftw3' + suffix, 'FFTW3' + suffix
        pcs[lib] = pc(lib, '3.3.10', '-l' + lib)
        pcs[lib + '_threads'] = pc(lib + ' pthreads', '3.3.10', '-l' + lib + '_threads', requires=lib)
        configs[package] = config(target('FFTW3::' + lib, 'lib' + lib + '.a', 'Threads::Threads;m') +
                                  target('FFTW3::' + lib + '_threads', 'lib' + lib + '_threads.a', 'FFTW3::' + lib + ';Threads::Threads;m'))
    elif name == 'openvdb':
        definitions = 'OPENVDB_STATICLIB;__TBB_NO_IMPLICIT_LINKAGE;OPENVDB_OPENEXR_STATICLIB'
        deps = ''.join(f'find_dependency({p} {VERSIONS[p]} EXACT CONFIG PATHS "${{_volume_prefix}}/lib/cmake/{p}" NO_DEFAULT_PATH)\n'
                       for p in ['TBB', 'Imath', 'Blosc', 'ZLIB'])
        nano = ('if(NOT TARGET OpenVDB::nanovdb)\n  add_library(OpenVDB::nanovdb INTERFACE IMPORTED)\n'
                '  set_target_properties(OpenVDB::nanovdb PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "${_volume_prefix}/include"\n'
                '    INTERFACE_COMPILE_DEFINITIONS "NANOVDB_USE_OPENVDB;NANOVDB_USE_TBB;NANOVDB_USE_BLOSC;NANOVDB_USE_ZIP"\n'
                '    INTERFACE_LINK_LIBRARIES "OpenVDB::openvdb")\nendif()\nset(OpenVDB_VERSION 13.0.0)\nset(NanoVDB_VERSION 32.9.0)\n')
        configs['OpenVDB'] = config(deps + target('OpenVDB::openvdb', 'libopenvdb.a', 'Imath::Imath;TBB::tbb;Blosc::blosc;ZLIB::ZLIB;Threads::Threads;m', defines=definitions) + nano)
        pcs['openvdb'] = pc('OpenVDB', '13.0.0', '-lopenvdb', requires='Imath tbb blosc zlib', cflags='-DOPENVDB_STATICLIB -D__TBB_NO_IMPLICIT_LINKAGE -DOPENVDB_OPENEXR_STATICLIB')
        pcs['nanovdb'] = pc('NanoVDB', '32.9.0', '', requires='openvdb', cflags='-DNANOVDB_USE_OPENVDB -DNANOVDB_USE_TBB -DNANOVDB_USE_BLOSC -DNANOVDB_USE_ZIP')
    return pcs, configs


def install(name, args, root):
    prefix = args.prefix
    archives = []
    for leaf in ARCHIVES[name]:
        file = prefix / 'lib' / leaf
        if not file.is_file() or file.open('rb').read(8) != b'!<arch>\n':
            raise ValueError('Real built archive required before metadata: ' + leaf)
        archives.append({'path': str(file.relative_to(prefix)), 'sha256': sha(file), 'size': file.stat().st_size})
    changes = []
    def save(path, text):
        file = prefix / path
        before = file.read_bytes() if file.exists() else None
        if before is not None:
            original = root / 'metadata-original' / sha(file) / path
            original.parent.mkdir(parents=True, exist_ok=True)
            if original.exists() and original.read_bytes() != before:
                raise ValueError('Sealed generated original metadata differs')
            if not original.exists():
                original.write_bytes(before)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
        changes.append({'path': path, 'before_sha256': None if before is None else __import__('hashlib').sha256(before).hexdigest(), 'after_sha256': sha(file)})
    # Upstream PCs can live under share/pkgconfig; normalize exactly the owned
    # generated originals, with complete snapshots. Zstd PCs never enter prefix.
    for file in sorted(prefix.rglob('*.pc')):
        text = file.read_text()
        if str(prefix) in text:
            lines = []
            for line in text.splitlines():
                if line.startswith('prefix='):
                    line = 'prefix=${pcfiledir}/' + __import__('os').path.relpath(prefix, file.parent)
                else:
                    line = line.replace(str(prefix), '${prefix}')
                lines.append(line)
            save(file.relative_to(prefix).as_posix(), '\n'.join(lines) + '\n')
    pcs, configs = templates(name)
    for package, text in pcs.items():
        save('lib/pkgconfig/' + package + '.pc', text)
    for package, text in configs.items():
        directory = package.lower() if package.startswith('FFTW') else package
        save('lib/cmake/' + directory + '/' + package + 'Config.cmake', text)
        save('lib/cmake/' + directory + '/' + package + 'ConfigVersion.cmake', version(VERSIONS[package]))
    depname = 'fftw' if name.startswith('fftw') else name
    dep = next(d for d in sources()['sources'] if d['name'] == depname)
    for notice in dep['notices'] + dep.get('inspected_source_spdx', []):
        out = prefix / 'share/licenses/volume-deps' / depname / notice['source_path']
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_file(notice['mirror']), out)
    for notice in sources()['supplementary_notices']:
        out = prefix / 'share/licenses/volume-deps/supplementary' / Path(notice['path']).name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_file(notice['path']), out)
    result = {'archives': archives, 'changes': changes, 'driver_sha256': sha(HERE / 'metadata.py'),
              'lineage': 'Own actual new build/install snapshots plus sealed original sources; no reused metadata'}
    write_json(root / 'metadata' / (name + '.json'), result)
    return result
