// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <hb.h>
#include <hb-ft.h>
#include <fribidi.h>
#include <ft2build.h>
#include FT_FREETYPE_H
static void check(bool b,const char *s){if(!b)throw std::runtime_error(s);}
struct Text{std::vector<FriBidiChar> cp;std::vector<unsigned> offsets;};
static Text strict_utf8(const std::string &s){
  Text text;
  for(unsigned i=0;i<s.size();){unsigned begin=i;unsigned char first=s[i++];unsigned need=0;uint32_t cp=0,min=0;
    if(first<128)cp=first;else if(first>=0xc2&&first<=0xdf){need=1;cp=first&31;min=0x80;}
    else if(first>=0xe0&&first<=0xef){need=2;cp=first&15;min=0x800;}
    else if(first>=0xf0&&first<=0xf4){need=3;cp=first&7;min=0x10000;}else throw std::runtime_error("invalid UTF8 lead");
    check(i+need<=s.size(),"truncated UTF8");for(unsigned j=0;j<need;++j){unsigned char next=s[i++];check((next&0xc0)==0x80,"invalid UTF8 continuation");cp=(cp<<6)|(next&63);}
    check(cp>=min&&cp<=0x10ffff&&!(cp>=0xd800&&cp<=0xdfff),"overlong/surrogate/oversize UTF8");text.cp.push_back(cp);text.offsets.push_back(begin);
  }return text;
}
static void malformed(){
  const std::vector<std::string> cases={std::string("\xc0\xaf",2),std::string("\xed\xa0\x80",3),std::string("\xf4\x90\x80\x80",4),std::string("\xe2\x82",2),std::string("\x80",1)};
  for(const auto &s:cases){bool rejected=false;try{strict_utf8(s);}catch(const std::exception &){rejected=true;}check(rejected,"strict UTF8 frontend accepted malformed input");
    auto *buffer=hb_buffer_create();hb_buffer_add_utf8(buffer,s.data(),s.size(),0,s.size());unsigned n=0;auto *info=hb_buffer_get_glyph_infos(buffer,&n);check(n==s.size(),"HB invalid-byte replacement policy");
    for(unsigned i=0;i<n;++i)check(info[i].codepoint==0xfffd&&info[i].cluster==i,"HB replacement byte cluster");hb_buffer_destroy(buffer);
  }
  hb_feature_t feature{};check(!hb_feature_from_string("liga=garbage",-1,&feature),"HB malformed feature accepted");
  std::puts("PASS malformed UTF8 strict reject and real HB per-byte replacement clusters; malformed feature parser");
}
struct Run{unsigned begin,end;FriBidiLevel level;int visual;};
static void layout(const char *latin_path,const char *arabic_path,const char *output){
  const std::string text="abc\xd8\xb3\xd9\x84\xd8\xa7\xd9\x85" "abc";auto decoded=strict_utf8(text);const size_t n=decoded.cp.size();check(n==10&&decoded.offsets[3]==3&&decoded.offsets[7]==11,"logical UTF8 byte indices");
  std::vector<FriBidiChar> visual(n);std::vector<FriBidiStrIndex> l2v(n),v2l(n);std::vector<FriBidiLevel> levels(n);FriBidiParType base=FRIBIDI_PAR_LTR;
  check(fribidi_log2vis(decoded.cp.data(),n,&base,visual.data(),l2v.data(),v2l.data(),levels.data())!=0,"integrated native UBA");
  for(unsigned i=0;i<n;++i){check(l2v[i]>=0&&l2v[i]<int(n)&&v2l[l2v[i]]==int(i),"UBA invertible logical/visual mapping");check((levels[i]&1)==(i>=3&&i<7),"expected mixed script embedding levels");}
  std::vector<Run> runs;for(unsigned begin=0;begin<n;){unsigned end=begin+1;while(end<n&&levels[end]==levels[begin])++end;int pos=l2v[begin];for(unsigned i=begin;i<end;++i)pos=std::min(pos,int(l2v[i]));runs.push_back({begin,end,levels[begin],pos});begin=end;}
  check(runs.size()==3,"logical LTR/RTL/LTR run split");std::sort(runs.begin(),runs.end(),[](const Run&a,const Run&b){return a.visual<b.visual;});
  FT_Library library=nullptr;check(!FT_Init_FreeType(&library),"layout FT init");FT_Face latin=nullptr,arabic=nullptr;check(!FT_New_Face(library,latin_path,0,&latin)&&!FT_New_Face(library,arabic_path,0,&arabic),"layout pinned fonts");check(!FT_Set_Pixel_Sizes(latin,0,24)&&!FT_Set_Pixel_Sizes(arabic,0,24),"layout font pixel sizes");
  hb_font_t *lf=hb_ft_font_create_referenced(latin),*af=hb_ft_font_create_referenced(arabic);std::vector<unsigned char> atlas(512*64);long pen=4*64,total=0;unsigned painted=0,glyph_total=0;
  for(const auto &run:runs){unsigned begin=decoded.offsets[run.begin],end=run.end<n?decoded.offsets[run.end]:text.size();bool rtl=run.level&1;auto *font=rtl?af:lf;auto face=rtl?arabic:latin;auto *buffer=hb_buffer_create();hb_buffer_add_utf8(buffer,text.data(),text.size(),begin,end-begin);hb_buffer_set_direction(buffer,rtl?HB_DIRECTION_RTL:HB_DIRECTION_LTR);hb_buffer_set_script(buffer,rtl?HB_SCRIPT_ARABIC:HB_SCRIPT_LATIN);hb_buffer_set_language(buffer,hb_language_from_string(rtl?"ar":"en",-1));hb_shape(font,buffer,nullptr,0);
    unsigned count=0;auto *info=hb_buffer_get_glyph_infos(buffer,&count);auto *positions=hb_buffer_get_glyph_positions(buffer,nullptr);check(count>0&&count<=run.end-run.begin,"layout shaped glyph count");long advance=0;
    for(unsigned i=0;i<count;++i){check(info[i].codepoint!=0&&info[i].cluster>=begin&&info[i].cluster<end,"layout glyph/absolute UTF8 cluster bounds");check(std::find(decoded.offsets.begin(),decoded.offsets.end(),info[i].cluster)!=decoded.offsets.end(),"layout cluster codepoint byte boundary");if(i)check(rtl?info[i-1].cluster>=info[i].cluster:info[i-1].cluster<=info[i].cluster,"layout HB direction/order, no extra reversal");check(positions[i].x_advance>0&&positions[i].y_advance==0&&std::abs(positions[i].x_offset)<4096&&std::abs(positions[i].y_offset)<4096,"layout actual advance/offset bounds");
      check(!FT_Load_Glyph(face,info[i].codepoint,FT_LOAD_RENDER),"layout actual native FT raster");auto &bitmap=face->glyph->bitmap;check(bitmap.pixel_mode==FT_PIXEL_MODE_GRAY&&bitmap.pitch>0,"layout gray bitmap");int x=pen/64+positions[i].x_offset/64+face->glyph->bitmap_left,y=40-face->glyph->bitmap_top-positions[i].y_offset/64;
      for(unsigned row=0;row<bitmap.rows;++row)for(unsigned col=0;col<bitmap.width;++col){int px=x+col,py=y+row;check(px>=0&&px<512&&py>=0&&py<64,"layout atlas bounds");auto value=bitmap.buffer[row*bitmap.pitch+col];atlas[py*512+px]=std::max(atlas[py*512+px],value);painted+=value!=0;}
      std::printf("LAYOUT level=%u visual=%d glyph=%u bytecluster=%u advance=%d offset=%d,%d\n",run.level,run.visual,info[i].codepoint,info[i].cluster,positions[i].x_advance,positions[i].x_offset,positions[i].y_offset);advance+=positions[i].x_advance;pen+=positions[i].x_advance;++glyph_total;
    }check(advance>0,"positive run width");total+=advance;hb_buffer_destroy(buffer);
  }check(glyph_total>=8&&glyph_total<=10&&total>64&&total<508*64&&painted>100,"integrated layout total width/glyphs/actual atlas pixels");
  std::ofstream stream(output,std::ios::binary);stream<<"P5\n512 64\n255\n";stream.write(reinterpret_cast<const char*>(atlas.data()),atlas.size());check(bool(stream),"layout PGM artifact");hb_font_destroy(lf);hb_font_destroy(af);FT_Done_Face(latin);FT_Done_Face(arabic);FT_Done_FreeType(library);
  std::printf("PASS actual UTF8 logical runs->UBA visual runs->HB direction-aware glyphs->FT atlas; glyphs=%u advance=%ld painted=%u\n",glyph_total,total,painted);
}
int main(int argc,char **argv){try{check(argc==4,"text-layout LATIN_TTF ARABIC_TTF ATLAS_PGM");malformed();layout(argv[1],argv[2],argv[3]);return 0;}catch(const std::exception&e){std::fprintf(stderr,"FAIL layout: %s\n",e.what());return 1;}}
