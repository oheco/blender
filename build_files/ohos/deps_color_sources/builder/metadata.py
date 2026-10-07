# SPDX-License-Identifier: GPL-2.0-or-later
"""Own newly installed metadata, exact originals and relative static closure.

No development prefix or old generated metadata is a build input. Upstream
exports remain authoritative; only generated absolute prefix paths and explicit
static pkgconf interfaces are adapted with before/after receipts.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
from io_utils import HERE, repo_file, sha, sources, write_json

ARCHIVES = {
    'zlib': ['libz.a'], 'png': ['libpng16.a'], 'jpeg': ['libjpeg.a', 'libturbojpeg.a'],
    'fmt': ['libfmt.a'], 'imath': ['libImath-3_2.a'], 'tbb': ['libtbb.a', 'libtbbmalloc.a'],
    'deflate': ['libdeflate.a'], 'openjph': ['libopenjph.a'], 'yamlcpp': ['libyaml-cpp.a'],
    'pystring': ['libpystring.a'], 'expat': ['libexpat.a'], 'minizipng': ['libminizip.a'],
    'tiff': ['libtiff.a', 'libtiffxx.a'], 'robinmap': [], 'pugixml': ['libpugixml.a'],
    'openjpeg': ['libopenjp2.a'],
    'webp': ['libwebp.a', 'libwebpdecoder.a', 'libwebpdemux.a', 'libwebpmux.a', 'libsharpyuv.a'],
    'openexr': ['libOpenEXR-3_4.a', 'libOpenEXRCore-3_4.a', 'libOpenEXRUtil-3_4.a', 'libIex-3_4.a', 'libIlmThread-3_4.a'],
    'opencolorio': ['libOpenColorIO.a'], 'openimageio': ['libOpenImageIO.a', 'libOpenImageIO_Util.a']}


def pc(name, version, libs, private='-pthread -lm', requires='', cflags=''):
    # pkgconf performs one shell escape for Unicode bytes and spaces. Additional
    # quotes would double-escape pcfiledir and break native CMake consumers.
    return ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\n'
            f'Name: {name}\nDescription: Native static PIC {name}\nVersion: {version}\n'
            f'Libs: -L${{libdir}} {libs}\nLibs.private: {private}\nRequires.private: {requires}\n'
            f'Cflags: -I${{includedir}} {cflags}\n')


def pkgconf_tokens(raw):
    """Unescape pkgconf at byte level before UTF-8 decoding, exactly once."""
    import shlex
    return [os.fsdecode(token.encode('latin1')) for token in shlex.split(raw.decode('latin1'))]


def query_static(args, runner, prefix, label):
    output = runner.run([args.pkgconf, '--static', '--cflags', '--libs',
                         'OpenEXR', 'OpenImageIO', 'OpenColorIO', 'Imath', 'tbb', 'tbbmalloc'],
                        label + '-pkgconf-static-plan', raw=True)
    tokens = pkgconf_tokens(output)
    for token in tokens:
        if token.startswith(('-I', '-L')):
            path = Path(token[2:]).resolve()
            if not path.is_relative_to(prefix.resolve()):
                raise ValueError('Actual pkgconf path outside selected prefix: ' + token)
    required = ['-lOpenImageIO', '-lOpenColorIO', '-lOpenEXR-3_4', '-lImath-3_2',
                '-lpystring', '-lpugixml', '-ltbb', '-ltbbmalloc', '-lz']
    if any(token not in tokens for token in required):
        raise ValueError('Actual static pkgconf dependency closure missing')
    return tokens


def zlib_config():
    return ('include(CMakeFindDependencyMacro)\nfind_dependency(Threads)\n'
            'get_filename_component(_color_zlib_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n'
            'if(NOT TARGET ZLIB::ZLIB)\n  if(NOT EXISTS "${_color_zlib_prefix}/lib/libz.a")\n'
            '    message(FATAL_ERROR "Actual source-built Zlib archive missing")\n  endif()\n'
            '  add_library(ZLIB::ZLIB STATIC IMPORTED)\n  set_target_properties(ZLIB::ZLIB PROPERTIES\n'
            '    IMPORTED_LOCATION "${_color_zlib_prefix}/lib/libz.a"\n'
            '    INTERFACE_INCLUDE_DIRECTORIES "${_color_zlib_prefix}/include"\n'
            '    INTERFACE_LINK_LIBRARIES "Threads::Threads;m")\nendif()\nset(ZLIB_VERSION 1.3.1)\n')


def configured_install_layout(cache_text, prefix):
    values = {}
    for line in cache_text.splitlines():
        if line.startswith(('#', '//')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        name = key.split(':', 1)[0]
        if name in ('CMAKE_INSTALL_PREFIX', 'CMAKE_INSTALL_LIBDIR'):
            if name in values:
                raise ValueError('Duplicate configured install directory: ' + name)
            values[name] = value
    actual_prefix = Path(values.get('CMAKE_INSTALL_PREFIX', ''))
    if not actual_prefix.is_absolute() or actual_prefix.resolve() != prefix.resolve():
        raise ValueError('Configured install prefix differs from selected owned prefix')
    libdir = values.get('CMAKE_INSTALL_LIBDIR')
    selected = prefix.resolve()
    libpath = (actual_prefix / 'lib').resolve()
    if libdir != 'lib' or (prefix / 'lib').is_symlink() or not libpath.is_relative_to(selected):
        raise ValueError('Configured library install directory must be relative lib beneath selected prefix: ' + str(libdir))
    return {'prefix': str(prefix), 'libdir': libdir}


def guard_install_layout(build, prefix):
    cache = build / 'CMakeCache.txt'
    if not cache.is_file() or cache.is_symlink():
        raise ValueError('Actual configured install cache required')
    return configured_install_layout(cache.read_text(), prefix)


def archive_records(name, prefix):
    archives = []
    for leaf in ARCHIVES[name]:
        file = prefix / 'lib' / leaf
        if not file.is_file() or file.is_symlink():
            raise ValueError('Actual newly built archive missing: ' + leaf)
        with file.open('rb') as stream:
            if stream.read(8) != b'!<arch>\n':
                raise ValueError('Actual archive format required: ' + leaf)
        archives.append({'path': str(file.relative_to(prefix)), 'sha256': sha(file), 'size': file.stat().st_size})
    return archives


def install(name, args, root):
    prefix = args.prefix
    guard_install_layout(root / 'build' / name, prefix)
    archives = archive_records(name, prefix)
    changes = []
    def save(file, text):
        before = file.read_bytes() if file.exists() else None
        after = text.encode()
        if before == after:
            return
        if file.is_symlink():
            raise ValueError('Generated metadata may not be overwritten through a symlink')
        relative = file.relative_to(prefix)
        if before is not None:
            original = root / 'metadata-original' / hashlib.sha256(before).hexdigest() / relative
            original.parent.mkdir(parents=True, exist_ok=True)
            if original.exists() and original.read_bytes() != before:
                raise ValueError('Owned generated metadata snapshot differs')
            if not original.exists():
                original.write_bytes(before)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(after)
        changes.append({'path': relative.as_posix(), 'before_sha256': None if before is None else hashlib.sha256(before).hexdigest(), 'after_sha256': sha(file)})
    for file in sorted(prefix.rglob('*')):
        if file.is_symlink() or not file.is_file() or file.suffix not in ('.pc', '.cmake'):
            continue
        text = file.read_text()
        if file.suffix == '.pc':
            text = text.replace(str(prefix), '${prefix}')
            text = re.sub(r'^prefix=.*$', 'prefix=${pcfiledir}/' + os.path.relpath(prefix, file.parent), text, flags=re.M)
        elif str(prefix) in text:
            text = text.replace(str(prefix), '${CMAKE_CURRENT_LIST_DIR}/' + os.path.relpath(prefix, file.parent))
        save(file, text)
    pcs = {}
    if name == 'zlib':
        pcs['zlib'] = pc('zlib', '1.3.1', '-lz')
        save(prefix / 'lib/cmake/ZLIB/ZLIBConfig.cmake', zlib_config())
        save(prefix / 'lib/cmake/ZLIB/ZLIBConfigVersion.cmake',
             'set(PACKAGE_VERSION "1.3.1")\nif(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)\n'
             '  set(PACKAGE_VERSION_COMPATIBLE FALSE)\nelse()\n  set(PACKAGE_VERSION_COMPATIBLE TRUE)\n'
             '  if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)\n    set(PACKAGE_VERSION_EXACT TRUE)\n  endif()\nendif()\n')
    elif name == 'tbb':
        pcs['tbb'] = pc('oneTBB', '2022.3.0', '-ltbb', '-pthread -ldl -lm', cflags='-D__TBB_NO_IMPLICIT_LINKAGE')
        pcs['tbbmalloc'] = pc('oneTBB allocator', '2022.3.0', '-ltbbmalloc', '-pthread -ldl -lm')
    elif name == 'pystring':
        pcs['pystring'] = pc('pystring', '1.1.3', '-lpystring')
    elif name == 'openexr':
        pcs['OpenEXR'] = pc('OpenEXR', '3.4.10', '-lOpenEXR-3_4 -lOpenEXRCore-3_4 -lIex-3_4 -lIlmThread-3_4',
                            '-pthread -ldl -lm', 'Imath libdeflate openjph', '-I${includedir}/OpenEXR')
    elif name == 'opencolorio':
        pcs['OpenColorIO'] = pc('OpenColorIO', '2.5.0', '-lOpenColorIO', '-lpystring -pthread -lm',
                                'Imath zlib expat yaml-cpp minizip', '-DOpenColorIO_SKIP_IMPORTS')
    elif name == 'openimageio':
        pcs['OpenImageIO'] = pc('OpenImageIO', '3.1.13.1', '-lOpenImageIO -lOpenImageIO_Util', '-lpugixml -pthread -ldl -lm',
                                'OpenEXR OpenColorIO libpng16 libjpeg libtiff-4 libopenjp2 libwebp libwebpdemux libwebpmux tbb',
                                '-DOIIO_STATIC_DEFINE=1 -DFMT_HEADER_ONLY=1')
    for package, text in pcs.items():
        save(prefix / 'lib/pkgconfig' / (package + '.pc'), text)
    dep = next(d for d in sources()['sources'] if d['name'] == name)
    for notice in dep['notices']:
        source = repo_file(notice['mirror']) if notice.get('mirror') else root / 'sources' / name / notice['source_path']
        if sha(source) != notice['sha256'] or source.stat().st_size != notice['size']:
            raise ValueError('Original notice bytes changed')
        target = prefix / 'share/licenses/color-deps' / name / notice['source_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for file in prefix.rglob('*'):
        if file.is_file() and file.suffix in ('.pc', '.cmake'):
            if any(p in file.read_text() for p in (str(prefix), str(root), '/storage/Users', '/data/storage')):
                raise ValueError('Private route retained in generated metadata: ' + str(file))
    result = {'archives': archives, 'changes': changes, 'driver_sha256': sha(HERE / 'metadata.py'),
              'lineage': 'Own actual fresh build/install metadata snapshots and complete original source; no copied prefix'}
    write_json(root / 'metadata' / (name + '.json'), result)
    return result
