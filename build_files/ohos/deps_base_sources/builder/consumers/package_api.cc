// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdio>
#include <cstring>
#include <stdexcept>
#if PK_ZLIB
#include <zlib.h>
#elif PK_ZSTD
#include <zstd.h>
#elif PK_JPEG
#include <jpeglib.h>
#elif PK_TURBOJPEG
#include <turbojpeg.h>
#elif PK_PNG
#include <png.h>
#elif PK_BROTLICOMMON
#include <brotli/types.h>
#elif PK_BROTLIDEC
#include <brotli/decode.h>
#elif PK_BROTLIENC
#include <brotli/encode.h>
#elif PK_FREETYPE
#include <ft2build.h>
#include FT_FREETYPE_H
#elif PK_HARFBUZZ
#include <hb-ft.h>
#elif PK_HBSUBSET
#include <hb-subset.h>
#elif PK_FRIBIDI
#include <fribidi.h>
#elif PK_FMT
#include <fmt/format.h>
#elif PK_IMATH
#include <Imath/half.h>
#endif
static void check(bool value,const char *message){if(!value)throw std::runtime_error(message);}
int main(){try{
#if PK_ZLIB
  check(std::strcmp(zlibVersion(),"1.3.1")==0,"zlib source version");unsigned char packed[128],decoded[32];uLongf n=sizeof packed,m=sizeof decoded;const unsigned char data[]="sourcebuilt-zlib";check(compress(packed,&n,data,sizeof data)==Z_OK&&uncompress(decoded,&m,packed,n)==Z_OK&&m==sizeof data&&!std::memcmp(data,decoded,m),"zlib API closure");packed[0]^=255;m=sizeof decoded;check(uncompress(decoded,&m,packed,n)!=Z_OK,"zlib corrupt stream");
#elif PK_ZSTD
  check(std::strcmp(ZSTD_versionString(),"1.5.7")==0,"zstd version");auto *context=ZSTD_createCCtx();check(context,"zstd allocator");ZSTD_freeCCtx(context);
#elif PK_JPEG
  jpeg_compress_struct value{};jpeg_error_mgr errors{};value.err=jpeg_std_error(&errors);jpeg_create_compress(&value);check(value.global_state>0,"jpeg native allocator");jpeg_destroy_compress(&value);
#elif PK_TURBOJPEG
  auto *value=tjInitDecompress();check(value,"turbojpeg allocator");unsigned char malformed[64]{};int w=0,h=0,s=0,c=0;check(tjDecompressHeader3(value,malformed,sizeof malformed,&w,&h,&s,&c)!=0,"JPEG malformed interior");tjDestroy(value);
#elif PK_PNG
  check(std::strcmp(png_get_libpng_ver(nullptr),"1.6.58")==0,"PNG version");auto *value=png_create_read_struct(PNG_LIBPNG_VER_STRING,nullptr,nullptr,nullptr);check(value,"PNG read struct");png_destroy_read_struct(&value,nullptr,nullptr);
#elif PK_BROTLICOMMON
  // Common declares shared public types; it has no standalone declared runtime
  // function. Its implemented symbols are exercised by encoder/decoder and
  // all its members are audited/wholelinked in the independent module gate.
  brotli_alloc_func allocator=nullptr;check(allocator==nullptr&&sizeof(BROTLI_BOOL)==sizeof(int),"Brotli common public types");
#elif PK_BROTLIDEC
  auto *value=BrotliDecoderCreateInstance(nullptr,nullptr,nullptr);check(value,"Brotli decoder static closure");BrotliDecoderDestroyInstance(value);
#elif PK_BROTLIENC
  auto *value=BrotliEncoderCreateInstance(nullptr,nullptr,nullptr);check(value,"Brotli encoder static closure");BrotliEncoderDestroyInstance(value);
#elif PK_FREETYPE
  FT_Library library=nullptr;check(!FT_Init_FreeType(&library),"FT static closure");int a,b,c;FT_Library_Version(library,&a,&b,&c);check(a==2&&b==13&&c==3,"FT source runtime version");FT_Done_FreeType(library);
#elif PK_HARFBUZZ
  check(!std::strcmp(hb_version_string(),"10.0.1"),"HB version");auto *value=hb_buffer_create();check(value&&hb_buffer_allocation_successful(value),"HB static closure");hb_buffer_destroy(value);FT_Library library=nullptr;check(!FT_Init_FreeType(&library),"HB propagated FT closure");FT_Done_FreeType(library);
#elif PK_HBSUBSET
  auto *value=hb_subset_input_create_or_fail();check(value,"HB subset static closure");hb_set_add(hb_subset_input_unicode_set(value),'a');hb_subset_input_destroy(value);
#elif PK_FRIBIDI
  check(!std::strcmp(FRIBIDI_VERSION,"1.0.12"),"FriBidi source version");check(fribidi_get_bidi_type(0x633)==FRIBIDI_TYPE_AL,"FriBidi generated Unicode table");
#elif PK_FMT
  check(fmt::format("{} {:04x}","OHOS",42)=="OHOS 002a","fmt real compiled symbol");
#elif PK_IMATH
  Imath::half value(1.5f);check(float(value)==1.5f&&value.bits()==0x3e00,"Imath real half lookup API");
#endif
  std::puts("PASS individual source-built public static package API");return 0;
}catch(const std::exception &error){std::fprintf(stderr,"FAIL package: %s\n",error.what());return 1;}}
