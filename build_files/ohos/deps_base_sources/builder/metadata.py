# SPDX-License-Identifier: GPL-2.0-or-later
"""Recorded relative static package contracts, never synthetic library outputs."""
from pathlib import Path
import os
import re
import shutil
from io_utils import HERE, repo_file, sha, sources, write_json

ARCHIVES=['libz.a','libzstd.a','libjpeg.a','libturbojpeg.a','libpng16.a','libbrotlicommon.a','libbrotlidec.a','libbrotlienc.a',
          'libfreetype.a','libharfbuzz.a','libharfbuzz-subset.a','libfribidi.a','libfmt.a','libImath-3_2.a']
PACKAGES=['zlib','libzstd','libjpeg','libturbojpeg','libpng','libbrotlicommon','libbrotlidec','libbrotlienc',
          'freetype2','harfbuzz','harfbuzz-subset','fribidi','fmt','Imath']


def pc(name,version,libs,requires='',include='',extra=''):
    return ('prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n'
            f'Name: {name}\nDescription: Pinned source-built portable static library\nVersion: {version}\n'
            f'Libs: -L${{libdir}} {libs}\nLibs.private: -pthread -lm -ldl\nRequires.private: {requires}\n'
            f'Cflags: -I${{includedir}}{include} {extra}\n')


def imported(target,archive,include='include',links='Threads::Threads;m;dl'):
    return (f'if(NOT TARGET {target})\nadd_library({target} STATIC IMPORTED)\n'
            f'set_target_properties({target} PROPERTIES IMPORTED_LOCATION "${{_base_prefix}}/lib/{archive}"\n'
            f'INTERFACE_INCLUDE_DIRECTORIES "${{_base_prefix}}/{include}" INTERFACE_LINK_LIBRARIES "{links}")\nendif()\n')


def config(body,deps=''):
    return ('# Recipe-added/repaired relative static import; original upstream metadata is SHA journaled.\n'
            'block(SCOPE_FOR VARIABLES)\ninclude(CMakeFindDependencyMacro)\nfind_dependency(Threads)\n'+deps+
            'get_filename_component(_base_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)\n'+body+'endblock()\n')


def templates():
    pcs={
      'zlib':pc('zlib','1.3.1','-lz'), 'libzstd':pc('zstd','1.5.7','-lzstd'),
      'libjpeg':pc('JPEG','2.1.3','-ljpeg'), 'libturbojpeg':pc('TurboJPEG','2.1.3','-lturbojpeg','libjpeg'),
      'libpng':pc('PNG','1.6.58','-lpng16','zlib',include='/libpng16'),
      'libpng16':pc('PNG','1.6.58','-lpng16','zlib',include='/libpng16'),
      'libbrotlicommon':pc('Brotli common','1.0.9','-lbrotlicommon'),
      'libbrotlidec':pc('Brotli decoder','1.0.9','-lbrotlidec','libbrotlicommon'),
      'libbrotlienc':pc('Brotli encoder','1.0.9','-lbrotlienc','libbrotlicommon'),
      # Preserve actual upstream libtool ABI version, not runtime/source version.
      'freetype2':pc('FreeType upstream libtool ABI','26.2.20','-lfreetype','libpng zlib libbrotlidec',include='/freetype2'),
      'harfbuzz':pc('HarfBuzz','10.0.1','-lharfbuzz','freetype2',include='/harfbuzz'),
      'harfbuzz-subset':pc('HarfBuzz subset','10.0.1','-lharfbuzz-subset','harfbuzz',include='/harfbuzz'),
      'fribidi':pc('FriBidi','1.0.12','-lfribidi',include='/fribidi'),
      'fmt':pc('fmt','12.1.0','-lfmt'), 'Imath':pc('Imath','3.2.2','-lImath-3_2',extra='-I${includedir}/Imath')}
    configs={
      'ZLIB':config(imported('ZLIB::ZLIB','libz.a')+'set(ZLIB_INCLUDE_DIR "${_base_prefix}/include")\nset(ZLIB_INCLUDE_DIRS "${_base_prefix}/include")\nset(ZLIB_LIBRARY "${_base_prefix}/lib/libz.a")\nset(ZLIB_LIBRARIES ZLIB::ZLIB)\nset(ZLIB_VERSION_STRING 1.3.1)\nset(ZLIB_VERSION 1.3.1)\nset(ZLIB_FOUND TRUE)\n'),
      'Brotli':config(imported('Brotli::common','libbrotlicommon.a')+imported('Brotli::dec','libbrotlidec.a',links='Brotli::common;m')+imported('Brotli::enc','libbrotlienc.a',links='Brotli::common;m')),
      'freetype':config(imported('Freetype::Freetype','libfreetype.a','include/freetype2','PNG::PNG;ZLIB::ZLIB;Brotli::dec;Threads::Threads;m'),
                        'find_dependency(ZLIB CONFIG)\nfind_dependency(PNG CONFIG)\nfind_dependency(Brotli CONFIG)\n'),
      'harfbuzz':config(imported('harfbuzz::harfbuzz','libharfbuzz.a','include/harfbuzz','Freetype::Freetype;Threads::Threads;m')+
                        imported('harfbuzz::subset','libharfbuzz-subset.a','include/harfbuzz','harfbuzz::harfbuzz'),
                        'find_dependency(freetype CONFIG)\n'),
      'FriBidi':config(imported('FriBidi::FriBidi','libfribidi.a','include/fribidi'))}
    return pcs,configs


def normalize(args,root):
    prefix=args.prefix
    for name in ARCHIVES:
        file=prefix/'lib'/name
        if not file.is_file() or file.open('rb').read(8)!=b'!<arch>\n':raise ValueError('Genuine source-built archive absent: '+name)
    journal=[]
    originals={}
    for file in sorted(prefix.rglob('*')):
        if not file.is_file() or file.suffix not in ('.pc','.cmake'):continue
        if prefix.resolve() not in file.resolve().parents:raise ValueError('Metadata symlink escapes prefix')
        original=file.read_bytes();snapshot=root/'upstream-metadata-originals'/file.relative_to(prefix)
        snapshot.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(original)
        relative=file.relative_to(prefix).as_posix();originals[relative]=original
        journal.append({'path':relative,'upstream_sha256':sha(snapshot),'original_snapshot':str(snapshot),'original_link_target':os.readlink(file) if file.is_symlink() else None,
                        'origin':'recipe-added zlib initial PC seed' if relative=='lib/pkgconfig/zlib.pc' else 'actual upstream installed metadata'})
    # Snapshot all aliases before writing any byte. Detach metadata symlinks so
    # each canonical/public name has its explicitly recorded independent bytes.
    for row in journal:
        file=prefix/row['path']
        if file.is_symlink():file.unlink()
    for row in journal:
        file=prefix/row['path'];text=originals[row['path']].decode('utf8')
        if file.suffix=='.pc':
            text=text.replace(str(prefix),'${prefix}')
            text=re.sub(r'^prefix=.*$','prefix=${pcfiledir}/../..',text,flags=re.M)
            if not re.search(r'^prefix=',text,re.M):text='prefix=${pcfiledir}/../..\n'+text
        elif '/cmake/Imath/' in str(file):
            text=re.sub(r'(INTERFACE_LINK_LIBRARIES\s+)([\"\']?)ImathConfig\2',r'\1"Imath::ImathConfig"',text)
        # FT/HB upstream absolute targets are retained as original evidence and
        # superseded by explicit adapters; consumers never patch their closure.
        if file.suffix=='.cmake' and any(piece in str(file.relative_to(prefix)) for piece in ['/freetype/','/harfbuzz/']):
            file.unlink();replacement='superseded by truthful source-built relative static adapter'
        else:
            if any(path in text for path in [str(root/'sources'),str(root/'build')]):raise ValueError('Installed source/build metadata path')
            file.write_text(text);replacement=sha(file)
        row['transformation']=replacement
    pcs,configs=templates()
    for name,text in pcs.items():
        file=prefix/'lib/pkgconfig'/(name+'.pc');file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
        other=prefix/'share/pkgconfig'/(name+'.pc')
        if other.exists():other.write_text(text)
    for name,text in configs.items():
        folder=prefix/'lib/cmake'/name;folder.mkdir(parents=True,exist_ok=True)
        (folder/(name+'Config.cmake')).write_text(text)
        if name in ['freetype','harfbuzz']:
            (folder/(name.lower()+'-config.cmake')).write_text(text)
    old_paths={row['path'] for row in journal}
    for row in journal:
        file=prefix/row['path'];row['final_sha256']=sha(file) if file.is_file() else None
        row['final_state']='recorded final installed bytes' if file.is_file() else 'superseded original export removed'
    added=[{'path':p.relative_to(prefix).as_posix(),'final_sha256':sha(p),'origin':'explicit recipe-added static metadata'} for p in sorted(prefix.rglob('*'))
           if p.is_file() and p.suffix in ('.pc','.cmake') and p.relative_to(prefix).as_posix() not in old_paths]
    write_json(root/'metadata-normalization.json',{'upstream_originals':journal,'recipe_added_final_files':added,'recipe_added':['ZLIB/Brotli/FriBidi imports','FT/HB include/static closure repairs','HB subset import','TurboJPEG static-to-JPEG PC closure; upstream PUBLIC CONFIG export retained','relative static PC interfaces'],
               'FreeType_runtime_vs_pc_version':'Source/runtime2.13.3 separately from preserved upstream libtool PC26.2.20'})
    for dep in sources()['sources']:
        for row in dep['notices']:
            dest=prefix/'share/licenses'/dep['name']/row['source_path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repo_file(row['mirror']),dest)
    fontlicenses=prefix/'share/licenses/font-fixtures';shutil.copytree(HERE/'fixtures/licenses',fontlicenses)
    shutil.copyfile(HERE/'sources.lock.json',prefix/'share/base-builder-source-provenance.json')
    shutil.copyfile(args.sdk_root/'NOTICE.txt',prefix/'share/licenses/SDK15-NOTICE.txt')


def verify_fonts():
    for row in sources()['fonts']:
        file=repo_file(row['fixture'])
        if sha(file)!=row['sha256'] or file.stat().st_size!=row['size']:raise ValueError('Pinned original font bytes drift')
    return sources()['fonts']
