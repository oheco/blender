// SPDX-License-Identifier: GPL-2.0-or-later
// Opaque C ABI: module owns all allocations, resources and exceptions.
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <stdexcept>
#include <zstd.h>
#include <png.h>
#include <turbojpeg.h>
#include <hb-ft.h>
#include <fribidi.h>
#include <fmt/format.h>
#include <Imath/half.h>
#include "zstd_cover_module.hh"
static void check(bool value){if(!value)throw std::runtime_error("module real source-built API check");}
struct Owner{std::string label;std::vector<unsigned char> pixels;FT_Library library=nullptr;FT_Face face=nullptr;~Owner(){if(face)FT_Done_Face(face);if(library)FT_Done_FreeType(library);}};
extern "C" __attribute__((visibility("default"))) void *base_create(const char *font){try{auto *owner=new Owner;try{owner->label=fmt::format("module-{}",ZSTD_versionString());owner->pixels.resize(4*8*8,200);check(!FT_Init_FreeType(&owner->library));check(!FT_New_Face(owner->library,font,0,&owner->face));check(!FT_Set_Pixel_Sizes(owner->face,0,24));return owner;}catch(...){delete owner;throw;}}catch(...){return nullptr;}}
extern "C" __attribute__((visibility("default"))) int base_check(void *handle){try{auto &owner=*static_cast<Owner*>(handle);check(owner.label=="module-1.5.7");std::vector<unsigned char> packed(ZSTD_compressBound(owner.pixels.size())),decoded(owner.pixels.size());auto n=ZSTD_compress(packed.data(),packed.size(),owner.pixels.data(),owner.pixels.size(),3);check(!ZSTD_isError(n));check(ZSTD_decompress(decoded.data(),decoded.size(),packed.data(),n)==owner.pixels.size()&&decoded==owner.pixels);
  png_image image{};image.version=PNG_IMAGE_VERSION;image.width=8;image.height=8;image.format=PNG_FORMAT_RGBA;png_alloc_size_t size=0;check(png_image_write_to_memory(&image,nullptr,&size,0,owner.pixels.data(),0,nullptr));std::vector<unsigned char> png(size);check(png_image_write_to_memory(&image,png.data(),&size,0,owner.pixels.data(),0,nullptr));png_image reader{};reader.version=PNG_IMAGE_VERSION;check(png_image_begin_read_from_memory(&reader,png.data(),size));reader.format=PNG_FORMAT_RGBA;check(png_image_finish_read(&reader,nullptr,decoded.data(),0,nullptr));png_image_free(&reader);check(decoded==owner.pixels);
  auto *jpeg=tjInitDecompress();check(jpeg);tjDestroy(jpeg);auto *font=hb_ft_font_create_referenced(owner.face);auto *buffer=hb_buffer_create();hb_buffer_add_utf8(buffer,"abc",3,0,3);hb_buffer_guess_segment_properties(buffer);hb_shape(font,buffer,nullptr,0);unsigned count=0;auto *info=hb_buffer_get_glyph_infos(buffer,&count);check(count==3&&info[0].codepoint);check(!FT_Load_Glyph(owner.face,info[0].codepoint,FT_LOAD_RENDER));check(owner.face->glyph->bitmap.width>0);hb_buffer_destroy(buffer);hb_font_destroy(font);check(fribidi_get_bidi_type(0x633)==FRIBIDI_TYPE_AL);Imath::half value(1.5f);check(float(value)==1.5f);return 0;}catch(...){return 1;}}
extern "C" __attribute__((visibility("default"))) void base_destroy(void *handle){delete static_cast<Owner*>(handle);}
