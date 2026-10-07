#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <zstd.h>
#include <zlib.h>
#include <jpeglib.h>
#include <turbojpeg.h>
#include <png.h>
#include <brotli/encode.h>
#include <brotli/decode.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include <freetype/config/ftoption.h>
#ifdef HAVE_HARFBUZZ
#include <hb.h>
#include <hb-ft.h>
#endif
#ifdef HAVE_FRIBIDI
#include <fribidi.h>
#endif
#ifdef HAVE_FMT
#include <fmt/format.h>
#endif
#ifdef HAVE_IMATH
#include <Imath/ImathConfig.h>
#include <Imath/half.h>
#include <Imath/ImathMatrix.h>
#include <Imath/ImathVec.h>
#endif
#ifndef FT_CONFIG_OPTION_USE_BROTLI
#error Required FreeType WOFF2/Brotli feature missing
#endif
#ifndef FT_CONFIG_OPTION_SYSTEM_ZLIB
#error Fixed native zlib required
#endif
#ifndef FT_CONFIG_OPTION_USE_PNG
#error FreeType PNG embedded glyph support required
#endif
static void require(bool x, const char *message) { if(!x) throw std::runtime_error(message); }
static std::vector<unsigned char> readfile(const char *p) {
  std::ifstream f(p,std::ios::binary); require(bool(f),"fixture open failed");
  return {std::istreambuf_iterator<char>(f),std::istreambuf_iterator<char>()};
}
static void writefile(const std::string &p,const unsigned char *data,size_t n) {
  std::ofstream f(p,std::ios::binary); f.write(reinterpret_cast<const char *>(data),n); require(bool(f),"output write failed");
}
static void test_zlib() {
  require(std::strcmp(zlibVersion(),"1.3.1")==0,"zlib actual source version");
  const unsigned char input[]="actual independent source-built zlib";
  unsigned char packed[128],decoded[128];uLongf n=sizeof packed,m=sizeof decoded;
  require(compress2(packed,&n,input,sizeof input,6)==Z_OK,"zlib compression");
  require(uncompress(decoded,&m,packed,n)==Z_OK&&m==sizeof input&&!std::memcmp(input,decoded,m),"zlib real roundtrip");
  m=1;require(uncompress(decoded,&m,packed,n)==Z_BUF_ERROR,"zlib insufficient output error");
  m=sizeof decoded;require(uncompress(decoded,&m,packed,n/2)!=Z_OK,"zlib truncated valid stream accepted");
  packed[0]^=255;m=sizeof decoded;require(uncompress(decoded,&m,packed,n)!=Z_OK,"zlib corrupt header accepted");
  std::puts("PASS source-built zlib1.3.1 roundtrip + valid-stream truncation/insufficient buffer/corrupt header errors");
}
static void test_zstd() {
  require(ZSTD_VERSION_NUMBER==10507 && std::strcmp(ZSTD_versionString(),"1.5.7")==0,"zstd version mismatch");
  std::vector<unsigned char> input(2*1024*1024),output(input.size()),compressed(ZSTD_compressBound(input.size()));
  for(size_t i=0;i<input.size();++i)input[i]=(i*17+(i>>11))&255;
  ZSTD_CCtx *ctx=ZSTD_createCCtx(); require(ctx,"zstd context allocation");
  require(!ZSTD_isError(ZSTD_CCtx_setParameter(ctx,ZSTD_c_compressionLevel,5)),"zstd compression parameter");
  require(!ZSTD_isError(ZSTD_CCtx_setParameter(ctx,ZSTD_c_nbWorkers,2)),"zstd native pthread workers unavailable");
  size_t n=ZSTD_compress2(ctx,compressed.data(),compressed.size(),input.data(),input.size()); ZSTD_freeCCtx(ctx);
  require(!ZSTD_isError(n) && n<input.size(),"zstd compression failed");
  size_t r=ZSTD_decompress(output.data(),output.size(),compressed.data(),n);
  require(r==input.size() && input==output,"zstd roundtrip failed");
  compressed[0]^=0xFF;require(ZSTD_isError(ZSTD_decompress(output.data(),output.size(),compressed.data(),n)),"zstd corrupt magic accepted");
  std::printf("PASS zstd 1.5.7: 2MiB multithreaded roundtrip + corrupt magic rejection (%zu bytes)\n",n);
}
static void test_png_jpeg(const char *dir) {
  constexpr int w=32,h=32;
  std::vector<unsigned char> rgb(w*h*3),rgba(w*h*4);
  for(int y=0;y<h;++y)for(int x=0;x<w;++x){
    size_t p=y*w+x;rgb[p*3]=x*8;rgb[p*3+1]=y*8;rgb[p*3+2]=(x+y)*4;
    std::copy(rgb.begin()+p*3,rgb.begin()+p*3+3,rgba.begin()+p*4);rgba[p*4+3]=(x+y)%5?255:128;
  }
  require(std::strcmp(PNG_LIBPNG_VER_STRING,"1.6.58")==0 && std::strcmp(png_get_libpng_ver(nullptr),"1.6.58")==0,"png version mismatch");
  png_image image{};image.version=PNG_IMAGE_VERSION;image.width=w;image.height=h;image.format=PNG_FORMAT_RGBA;
  png_alloc_size_t n=0;require(png_image_write_to_memory(&image,nullptr,&n,0,rgba.data(),0,nullptr),"PNG size query");
  std::vector<unsigned char> encoded(n),decoded(rgba.size());
  require(png_image_write_to_memory(&image,encoded.data(),&n,0,rgba.data(),0,nullptr),"PNG encoding");
  writefile(std::string(dir)+"/native-roundtrip.png",encoded.data(),n);
  png_image read{};read.version=PNG_IMAGE_VERSION;
  require(png_image_begin_read_from_memory(&read,encoded.data(),n),"PNG header decoding");
  require(read.width==w && read.height==h,"PNG dimensions");read.format=PNG_FORMAT_RGBA;
  require(png_image_finish_read(&read,nullptr,decoded.data(),0,nullptr),"PNG pixel decoding");png_image_free(&read);
  require(decoded==rgba,"PNG exact RGBA comparison");
  png_image bad{};bad.version=PNG_IMAGE_VERSION;require(!png_image_begin_read_from_memory(&bad,encoded.data(),7),"PNG truncated signature accepted");png_image_free(&bad);
  png_image truncated{};truncated.version=PNG_IMAGE_VERSION;bool partial=png_image_begin_read_from_memory(&truncated,encoded.data(),n/2);if(partial){truncated.format=PNG_FORMAT_RGBA;require(!png_image_finish_read(&truncated,nullptr,decoded.data(),0,nullptr),"PNG valid header truncated IDAT payload accepted");}png_image_free(&truncated);
  auto corrupt=encoded;require(corrupt.size()>33,"PNG IHDR bounds");corrupt[29]^=1;png_image crc{};crc.version=PNG_IMAGE_VERSION;require(!png_image_begin_read_from_memory(&crc,corrupt.data(),n),"PNG valid signature corrupt IHDR CRC accepted");png_image_free(&crc);
  std::printf("PASS libpng 1.6.58: 32x32 RGBA lossless roundtrip + truncated signature/real IDAT payload + IHDR CRC errors (%zu bytes)\n",size_t(n));
  require(LIBJPEG_TURBO_VERSION_NUMBER==2001003,"jpeg version mismatch");
  tjhandle c=tjInitCompress();require(c,"TurboJPEG encoder");unsigned char *jpeg=nullptr;unsigned long jsize=0;
  require(tjCompress2(c,rgb.data(),w,0,h,TJPF_RGB,&jpeg,&jsize,TJSAMP_444,95,TJFLAG_ACCURATEDCT)==0,tjGetErrorStr());
  writefile(std::string(dir)+"/native-roundtrip.jpg",jpeg,jsize);tjDestroy(c);
  tjhandle d=tjInitDecompress();require(d,"TurboJPEG decoder");int rw=0,rh=0,sub=0,colors=0;
  require(tjDecompressHeader3(d,jpeg,jsize,&rw,&rh,&sub,&colors)==0 && rw==w && rh==h && sub==TJSAMP_444,"JPEG header");
  std::vector<unsigned char> restored(rgb.size());require(tjDecompress2(d,jpeg,jsize,restored.data(),w,0,h,TJPF_RGB,TJFLAG_ACCURATEDCT)==0,"JPEG pixels");
  double error=0;for(size_t i=0;i<rgb.size();++i)error+=std::abs(int(rgb[i])-int(restored[i]));error/=rgb.size();require(error<4.0,"JPEG lossy RGB error exceeded threshold");
  const unsigned char junk[]={1,2,3,4,5};require(tjDecompressHeader3(d,junk,sizeof(junk),&rw,&rh,&sub,&colors)!=0,"JPEG invalid header accepted");
  std::vector<unsigned char> malformed(jpeg,jpeg+jsize);bool sof=false;for(size_t i=2;i+4<malformed.size();++i)if(malformed[i]==0xff&&(malformed[i+1]==0xc0||malformed[i+1]==0xc2)){malformed[i+2]=0;malformed[i+3]=1;sof=true;break;}require(sof,"Real JPEG SOF record absent");require(tjDecompressHeader3(d,malformed.data(),malformed.size(),&rw,&rh,&sub,&colors)!=0,"JPEG valid SOI structurally malformed SOF length accepted");
  tjDestroy(d);tjFree(jpeg);
  std::printf("PASS libjpeg-turbo 2.1.3: SIMD enabled 32x32 RGB roundtrip + invalid header rejection (%lu bytes, MAE %.3f)\n",jsize,error);
}
static void test_brotli() {
  require(BrotliEncoderVersion()==0x1000009 && BrotliDecoderVersion()==0x1000009,"Brotli version mismatch");
  const char *text="Native HarmonyOS WOFF2 uses the real Brotli decoder and common dictionary. Native HarmonyOS WOFF2 uses the real Brotli decoder and common dictionary.";
  unsigned char encoded[512],out[512];size_t size=sizeof(encoded),outsize=sizeof(out);
  require(BrotliEncoderCompress(9,BROTLI_DEFAULT_WINDOW,BROTLI_MODE_TEXT,std::strlen(text),reinterpret_cast<const uint8_t *>(text),&size,encoded),"Brotli compress");
  require(BrotliDecoderDecompress(size,encoded,&outsize,out)==BROTLI_DECODER_RESULT_SUCCESS && outsize==std::strlen(text) && std::memcmp(out,text,outsize)==0,"Brotli roundtrip");
  outsize=sizeof(out);require(BrotliDecoderDecompress(size/2,encoded,&outsize,out)!=BROTLI_DECODER_RESULT_SUCCESS,"Brotli truncated stream accepted");
  std::printf("PASS Brotli 1.0.9: encoder/decoder roundtrip + truncation rejection (%zu bytes)\n",size);
}
struct Glyph { unsigned int width,rows;long advance;std::vector<unsigned char> pixels; };
static Glyph render(FT_Library lib,const std::vector<unsigned char> &font,const char *label) {
  FT_Face face=nullptr;FT_Error error=FT_New_Memory_Face(lib,font.data(),font.size(),0,&face);
  if(error){const char *detail=FT_Error_String(error);std::fprintf(stderr,"FreeType %s load failed: code=0x%02x size=%zu detail=%s\n",label,error,font.size(),detail?detail:"unavailable");}
  require(error==0,"FreeType font load");
  require(FT_Set_Pixel_Sizes(face,0,24)==0,"FreeType pixel sizing");
  require(FT_Get_Char_Index(face,'a')!=0,"FreeType missing a glyph");
  require(FT_Load_Char(face,'a',FT_LOAD_RENDER)==0,"FreeType glyph rasterization");
  auto &b=face->glyph->bitmap;Glyph g{b.width,b.rows,face->glyph->advance.x,{}};
  require(b.width>0 && b.rows>0 && b.buffer,"FreeType empty glyph");
  for(unsigned int y=0;y<b.rows;++y)g.pixels.insert(g.pixels.end(),b.buffer+y*b.pitch,b.buffer+y*b.pitch+b.width);
  FT_Done_Face(face);return g;
}
static void test_font(const char *ttfpath,const char *woffpath) {
  FT_Library lib=nullptr;require(FT_Init_FreeType(&lib)==0,"FreeType init");int major,minor,patch;FT_Library_Version(lib,&major,&minor,&patch);
  require(major==2 && minor==13 && patch==3,"FreeType version mismatch");
  auto tt=readfile(ttfpath),wo=readfile(woffpath);require(wo.size()>48 && std::memcmp(wo.data(),"wOF2",4)==0,"Real WOFF2 fixture absent");
  Glyph t=render(lib,tt,"locked TTF fixture"),w=render(lib,wo,"generated WOFF2 fixture");
  require(t.width==w.width && t.rows==w.rows && t.advance==w.advance && t.pixels==w.pixels,"TTF/WOFF2 glyph raster/metrics mismatch");
  FT_Face bad=nullptr;require(FT_New_Memory_Face(lib,wo.data(),wo.size()/2,0,&bad)!=0,"FreeType real WOFF2 payload truncation accepted");
  auto broken=tt;require(broken.size()>28,"SFNT fixture directory");unsigned tables=(broken[4]<<8)|broken[5];bool changed=false;
  for(unsigned i=0;i<tables;++i){size_t record=12+16*i;require(record+16<=broken.size(),"SFNT directory bounds");if(!std::memcmp(broken.data()+record,"cmap",4)){broken[record+8]=0x7f;broken[record+9]=broken[record+10]=broken[record+11]=0xff;changed=true;break;}}
  require(changed,"Real SFNT cmap table absent");
  FT_Face sfntbad=nullptr;FT_Error sfnt_error=FT_New_Memory_Face(lib,broken.data(),broken.size(),0,&sfntbad);
  if(sfnt_error==0){require(FT_Get_Char_Index(sfntbad,'a')==0||FT_Load_Char(sfntbad,'a',FT_LOAD_RENDER)!=0,"Malformed SFNT cmap offset accepted by actual glyph lookup");FT_Done_Face(sfntbad);}
  FT_Done_FreeType(lib);
  std::printf("PASS FreeType 2.13.3: mandatory Brotli/WOFF2 + PNG/zlib compiled, TTF/WOFF2 a glyph identical %ux%u advance=%ld + truncated WOFF2 rejected\n",t.width,t.rows,t.advance);
}
#ifdef HAVE_HARFBUZZ
static void test_harfbuzz(const char *ligfont,const char *arabicfont) {
  require(std::strcmp(hb_version_string(),"10.0.1")==0 && std::strcmp(HB_VERSION_STRING,"10.0.1")==0,"HarfBuzz version mismatch");
  FT_Library lib=nullptr;require(FT_Init_FreeType(&lib)==0,"HB FT init");FT_Face face=nullptr;
  require(FT_New_Face(lib,ligfont,0,&face)==0,"HB ligature fixture load");FT_Set_Pixel_Sizes(face,0,24);
  hb_font_t *font=hb_ft_font_create_referenced(face);hb_buffer_t *buffer=hb_buffer_create();
  hb_buffer_add_utf8(buffer,"fi",2,0,2);hb_buffer_guess_segment_properties(buffer);hb_shape(font,buffer,nullptr,0);
  unsigned int count=0;auto glyphs=hb_buffer_get_glyph_infos(buffer,&count);require(count==1 && glyphs[0].codepoint!=0,"HarfBuzz fi ligature not shaped");
  hb_buffer_reset(buffer);hb_buffer_add_utf8(buffer,"fi",2,0,2);hb_buffer_guess_segment_properties(buffer);hb_feature_t feature;require(hb_feature_from_string("liga=0",-1,&feature),"HB feature parser");hb_shape(font,buffer,&feature,1);
  require(hb_buffer_get_length(buffer)==2,"HarfBuzz liga=0 did not disable ligature");hb_font_destroy(font);FT_Done_Face(face);
  require(FT_New_Face(lib,arabicfont,0,&face)==0,"HB Arabic fixture load");FT_Set_Pixel_Sizes(face,0,24);font=hb_ft_font_create_referenced(face);
  hb_buffer_reset(buffer);const char arabic[]="\xD8\xB3\xD9\x84\xD8\xA7\xD9\x85";hb_buffer_add_utf8(buffer,arabic,8,0,8);hb_buffer_guess_segment_properties(buffer);hb_shape(font,buffer,nullptr,0);
  glyphs=hb_buffer_get_glyph_infos(buffer,&count);require(count>0 && count<=4 && hb_buffer_get_direction(buffer)==HB_DIRECTION_RTL,"HarfBuzz Arabic direction/count");
  bool contextual=count<4;const hb_codepoint_t chars[]={0x633,0x644,0x627,0x645};
  for(unsigned int i=0;i<count;++i){require(glyphs[i].codepoint!=0 && glyphs[i].cluster<8,"HarfBuzz Arabic missing glyph/cluster");hb_codepoint_t nominal=0;require(hb_font_get_nominal_glyph(font,chars[glyphs[i].cluster/2],&nominal),"HarfBuzz Arabic nominal lookup");contextual|=(nominal!=glyphs[i].codepoint);if(i)require(glyphs[i-1].cluster>=glyphs[i].cluster,"HB RTL cluster ordering");}
  require(contextual,"HarfBuzz Arabic contextual forms not applied");hb_buffer_destroy(buffer);hb_font_destroy(font);FT_Done_Face(face);FT_Done_FreeType(lib);
  std::printf("PASS HarfBuzz 10.0.1: real FreeType interop, fi ligature on/off + Arabic contextual RTL shaping (%u glyphs)\n",count);
}
#endif
static void test_optional(const char *ligfont,const char *arabicfont) {
#ifdef HAVE_HARFBUZZ
  test_harfbuzz(ligfont,arabicfont);
#else
  std::printf("SKIP HarfBuzz: pinned source prepared, library not yet installed\n");
#endif
#ifdef HAVE_FRIBIDI
  require(std::strcmp(FRIBIDI_VERSION,"1.0.12")==0,"FriBidi version mismatch");
  FriBidiChar input[]={'a','b','c',' ',0x5d0,0x5d1,0x5d2},visual[7];FriBidiParType direction=FRIBIDI_PAR_LTR;FriBidiStrIndex l2v[7],v2l[7];FriBidiLevel levels[7];
  require(fribidi_log2vis(input,7,&direction,visual,l2v,v2l,levels)!=0,"FriBidi UBA");
  require(visual[0]=='a' && visual[4]==0x5d2 && visual[5]==0x5d1 && visual[6]==0x5d0 && l2v[4]==6 && v2l[4]==6 && levels[4]%2,"FriBidi Hebrew ordering/mapping");
  std::printf("PASS FriBidi 1.0.12: mixed LTR/Hebrew UBA visual order + logical/visual maps\n");
#else
  std::printf("SKIP FriBidi: pinned source prepared, library not yet installed\n");
#endif
#ifdef HAVE_FMT
  require(FMT_VERSION==120100 && fmt::format("{} {:04x} {:.2f}","OHOS",42,1.25)=="OHOS 002a 1.25","fmt linked formatting");
  std::printf("PASS fmt 12.1.0: native linked string/integer/float formatting with real C++17\n");
#else
  std::printf("SKIP fmt: pinned source prepared, library not yet installed\n");
#endif
#ifdef HAVE_IMATH
  require(std::strcmp(IMATH_VERSION_STRING,"3.2.2")==0,"Imath version mismatch");
  Imath::half value(1.5f);require(float(value)==1.5f && value.bits()==0x3e00,"Imath half conversion");
  Imath::M44f matrix;matrix.translate(Imath::V3f(1,2,3));Imath::V3f transformed;matrix.multVecMatrix(Imath::V3f(4,5,6),transformed);require(transformed==Imath::V3f(5,7,9),"Imath vector/matrix math");
  std::printf("PASS Imath 3.2.2: half lookup-table conversion + 3D matrix/vector translation with real C++17\n");
#else
  std::printf("SKIP Imath: pinned source prepared, library not yet installed\n");
#endif
}
int main(int argc,char **argv) {
  std::setvbuf(stdout,nullptr,_IONBF,0);
  if(argc!=6){std::fprintf(stderr,"Usage: native-acceptance OUTPUT_DIR TTF WOFF2 LIGATURE_TTF ARABIC_TTF\n");return 2;}
  try {test_zlib();test_zstd();test_png_jpeg(argv[1]);test_brotli();test_font(argv[2],argv[3]);test_optional(argv[4],argv[5]);std::printf("ALL INSTALLED DEPENDENCY NATIVE ACCEPTANCE TESTS PASSED\n");return 0;}
  catch(const std::exception &e){std::fprintf(stderr,"FAIL native acceptance: %s\n",e.what());return 1;}
}
