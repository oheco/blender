// SPDX-License-Identifier: GPL-2.0-or-later
// Independent actual Half/Imath and texture workflow consumer.
#include <Imath/half.h>
#include <Imath/ImathVec.h>
#include <OpenImageIO/imagebuf.h>
#include <OpenImageIO/imagebufalgo.h>
#include <OpenImageIO/imageio.h>
#include <OpenImageIO/texture.h>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <vector>
namespace fs = std::filesystem;
static int checks = 0;
static void require(bool ok, const std::string& why) {
    ++checks; if (!ok) throw std::runtime_error(why);
}
static void near(float got, float want, const std::string& why) {
    require(std::isfinite(got) && std::abs(got-want) < 0.0001f, why);
}
int main(int argc, char** argv) {
    try {
        require(argc == 2 && fs::is_directory(argv[1]), "private fixture root required");
        fs::path root = argv[1];
        using IMATH_NAMESPACE::half;
        for (unsigned bits = 0; bits < 65536; ++bits) {
            half original; original.setBits(static_cast<uint16_t>(bits));
            if (!original.isNan() && !original.isInfinity()) {
                half converted(static_cast<float>(original));
                require(converted.bits() == original.bits(), "all finite HALF encodings round trip including signed zero/subnormals");
            }
        }
        IMATH_NAMESPACE::V3f a(1,2,3), b(2,0,-1);
        near(a.dot(b), -1, "Imath vector dot");
        near(a.cross(b).x, -2, "Imath vector cross");
        std::vector<half> pixels(8*4*3);
        for (size_t i=0;i<pixels.size();i+=3) { pixels[i]=half(-1.5f); pixels[i+1]=half(.125f); pixels[i+2]=half(32.f); }
        auto half_file = root / "HALF negative HDR.exr";
        auto out=OIIO::ImageOutput::create(half_file.string());
        require(bool(out), "EXR HALF output creation");
        OIIO::ImageSpec hs(8,4,3,OIIO::TypeDesc::HALF);
        hs.attribute("compression","zip");
        require(out->open(half_file.string(),hs), "EXR HALF open");
        require(out->write_image(OIIO::span<half>(pixels)) && out->close(), "EXR HALF write");
        auto in=OIIO::ImageInput::open(half_file.string());
        require(bool(in) && in->spec().format==OIIO::TypeDesc::HALF, "EXR HALF native storage");
        std::vector<half> decoded(pixels.size());
        require(in->read_image(0,0,0,-1,OIIO::span<half>(decoded)) && in->close(), "EXR HALF read");
        for(size_t i=0;i<pixels.size();++i) require(decoded[i].bits()==pixels[i].bits(), "HALF EXR lossless bits");
        OIIO::attribute("threads",2); OIIO::attribute("exr_threads",2);
        OIIO::ImageSpec is(16,16,3,OIIO::TypeDesc::FLOAT);
        OIIO::ImageBuf image(is);
        float value[3]={-1.5f,.125f,32.f};
        float other[3]={2.5f,.875f,64.f};
        for(int y=0;y<16;++y) for(int x=0;x<16;++x) image.setpixel(x,y,x<8 ? value : other);
        OIIO::ImageSpec config;
        config.format=OIIO::TypeDesc::FLOAT; config.tile_width=16; config.tile_height=16;
        config.attribute("maketx:fileformat","openexr");
        config.attribute("maketx:constant_color_detect",0);
        config.attribute("maketx:monochrome_detect",0);
        auto texture_file=root/"纹理 HDR texture with spaces.tx";
        std::ostringstream diagnostics;
        require(OIIO::ImageBufAlgo::make_texture(OIIO::ImageBufAlgo::MakeTxTexture,image,texture_file.string(),config,&diagnostics),
                "make_texture tiled MIP EXR: " + diagnostics.str());
        auto textures=OIIO::TextureSystem::create(false);
        require(bool(textures), "independent TextureSystem");
        int mips=0;
        require(textures->get_texture_info(OIIO::ustring(texture_file.string()),0,OIIO::ustring("miplevels"),OIIO::TypeDesc::INT,&mips) && mips>=5,
                "actual tiled mip chain");
        OIIO::TextureOpt options;
        // Nonconstant image prevents a constant-color fast path. Periodic wrap
        // gives a known full-image average for the coarsest filtered footprint.
        options.swrap=options.twrap=OIIO::TextureOpt::WrapPeriodic;
        options.mipmode=OIIO::TextureOpt::MipModeOneLevel;
        options.interpmode=OIIO::TextureOpt::InterpClosest;
        for(float footprint:{0.001f,0.05f}) {
            float left[3]={},right[3]={};
            require(textures->texture(OIIO::ustring(texture_file.string()),options,.25f,.5f,footprint,0,0,footprint,3,left), "narrow filtered left texture lookup");
            require(textures->texture(OIIO::ustring(texture_file.string()),options,.75f,.5f,footprint,0,0,footprint,3,right), "narrow filtered right texture lookup");
            for(int c=0;c<3;++c) { near(left[c],value[c],"negative/HDR narrow footprint"); near(right[c],other[c],"nonconstant HDR narrow footprint"); }
        }
        float coarse[3]={};
        require(textures->texture(OIIO::ustring(texture_file.string()),options,.25f,.5f,1,0,0,1,3,coarse), "coarse filtered MIP texture lookup");
        for(int c=0;c<3;++c) near(coarse[c],(value[c]+other[c])/2,"coarsest MIP mean differs from narrow sample");
        int64_t tiles_read=0;
        require(textures->get_texture_info(OIIO::ustring(texture_file.string()),0,OIIO::ustring("stat:tilesread"),OIIO::TypeDesc::INT64,&tiles_read) && tiles_read>0,
                "actual texture tiles were read");
        float missing[3]={};
        require(!textures->texture(OIIO::ustring((root/"missing.tx").string()),options,.5f,.5f,0,0,0,0,3,missing), "missing texture rejected");
        require(!textures->geterror().empty(), "missing texture diagnostic");
        OIIO::TextureSystem::destroy(textures);
        std::cout << "ALL PASS half-texture checks=" << checks << "\n";
        return 0;
    } catch(const std::exception& e) {
        std::cerr << "FAIL half-texture after checks=" << checks << ": " << e.what() << "\n";
        return 1;
    }
}
