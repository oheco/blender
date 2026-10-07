#include <OpenEXR/ImfChannelList.h>
#include <OpenEXR/ImfCompression.h>
#include <OpenEXR/ImfFrameBuffer.h>
#include <OpenEXR/ImfHeader.h>
#include <OpenEXR/ImfInputFile.h>
#include <OpenEXR/ImfOutputFile.h>
#include <OpenEXR/ImfStringAttribute.h>
#include <OpenColorIO/OpenColorIO.h>
#include <OpenImageIO/color.h>
#include <OpenImageIO/filesystem.h>
#include <OpenImageIO/imagebuf.h>
#include <OpenImageIO/imagebufalgo.h>
#include <OpenImageIO/imageio.h>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace OCIO = OCIO_NAMESPACE;
static int checks = 0;
static void require(bool ok, const std::string& why) {
    ++checks; if (!ok) throw std::runtime_error(why);
}
static void near(float got, float want, float eps, const std::string& why) {
    require(std::isfinite(got) && std::abs(got-want)<=eps, why+": got="+std::to_string(got)+" expected="+std::to_string(want));
}
template<class F> static void throws(F f, const std::string& why) {
    bool caught=false; try {f();} catch(const std::exception& e) {caught=true; require(*e.what(),why+" has a diagnostic");}
    require(caught, why+" was not rejected");
}
static void exr(const fs::path& root) {
    constexpr int w=16,h=8,n=4;
    std::vector<float> pixels(w*h*n);
    for(int i=0;i<w*h;++i) { pixels[n*i]=-2.f+i*.01f; pixels[n*i+1]=.125f; pixels[n*i+2]=32.f+i; pixels[n*i+3]=.75f; }
    for(auto codec: {Imf::NO_COMPRESSION,Imf::ZIP_COMPRESSION,Imf::PIZ_COMPRESSION,Imf::HTJ2K32_COMPRESSION}) {
        std::string codecname; Imf::getCompressionNameFromId(codec,codecname);
        auto file=root/("HDR negative "+codecname+".exr");
        Imf::Header hdr(w,h); hdr.compression()=codec;
        const char* names[]={"R","G","B","A"};
        for(auto name:names) hdr.channels().insert(name,Imf::Channel(Imf::FLOAT));
        hdr.insert("color-proof",Imf::StringAttribute("HarmonyOS static color dependency acceptance"));
        hdr.insert("oiio:ColorSpace",Imf::StringAttribute("lin_rec709_scene"));
        Imf::FrameBuffer frame;
        for(int c=0;c<n;++c) frame.insert(names[c],Imf::Slice(Imf::FLOAT,reinterpret_cast<char*>(pixels.data()+c),sizeof(float)*n,sizeof(float)*n*w));
        { Imf::OutputFile out(file.c_str(),hdr); out.setFrameBuffer(frame); out.writePixels(h); }
        std::vector<float> decoded(pixels.size());
        Imf::InputFile in(file.c_str());
        require(in.header().typedAttribute<Imf::StringAttribute>("color-proof").value()=="HarmonyOS static color dependency acceptance","EXR custom metadata");
        require(in.header().compression()==codec,"EXR codec metadata");
        Imf::FrameBuffer readframe;
        for(int c=0;c<n;++c) readframe.insert(names[c],Imf::Slice(Imf::FLOAT,reinterpret_cast<char*>(decoded.data()+c),sizeof(float)*n,sizeof(float)*n*w));
        in.setFrameBuffer(readframe); in.readPixels(0,h-1);
        for(size_t i=0;i<pixels.size();++i) near(decoded[i],pixels[i],0.f,"lossless FLOAT EXR "+codecname);
        std::cout<<"PASS EXR "<<codecname<<" FLOAT negative/HDR/metadata\n";
    }
    throws([&]{Imf::InputFile in((root/"missing.exr").c_str());},"EXR missing file");
    {std::ofstream out(root/"corrupt.exr",std::ios::binary); out<<"not an EXR";}
    throws([&]{Imf::InputFile in((root/"corrupt.exr").c_str());},"EXR corrupt header");
    require(!fs::exists(root/"missing directory"),"invalid EXR output fixture directory must be absent");
    // A valid header prevents an empty-channel-header failure from masking IO.
    Imf::Header valid_output_header(2,2); valid_output_header.channels().insert("R",Imf::Channel(Imf::FLOAT));
    throws([&]{Imf::OutputFile out((root/"missing directory/out.exr").c_str(),valid_output_header);},"EXR invalid output path");
}
static fs::path ocio(const fs::path& root) {
    auto cfg=OCIO::Config::Create(); cfg->setMajorVersion(2); cfg->setMinorVersion(0);
    cfg->setDescription("offline native HarmonyOS acceptance configuration"); cfg->setSearchPath(".");
    auto linear=OCIO::ColorSpace::Create(OCIO::REFERENCE_SPACE_SCENE); linear->setName("linear"); cfg->addColorSpace(linear);
    auto scaled=OCIO::ColorSpace::Create(OCIO::REFERENCE_SPACE_SCENE); scaled->setName("scaled");
    double matrix[16]={2,0,0,0,0,3,0,0,0,0,4,0,0,0,0,1};
    auto m=OCIO::MatrixTransform::Create(); m->setMatrix(matrix); scaled->setTransform(m,OCIO::COLORSPACE_DIR_TO_REFERENCE); cfg->addColorSpace(scaled);
    cfg->setRole(OCIO::ROLE_DEFAULT,"linear"); cfg->setRole(OCIO::ROLE_SCENE_LINEAR,"linear");
    cfg->addDisplayView("offline-display","raw","linear","");
    auto look=OCIO::Look::Create(); look->setName("boost"); look->setProcessSpace("linear");
    double lm[16]={1.25,0,0,0,0,1.25,0,0,0,0,1.25,0,0,0,0,1}; double lo[4]={.1,.1,.1,0};
    auto l=OCIO::MatrixTransform::Create(); l->setMatrix(lm); l->setOffset(lo); look->setTransform(l); cfg->addLook(look); cfg->validate();
    auto configfile=root/"offline config.ocio"; { std::ofstream out(configfile); cfg->serialize(out); }
    auto loaded=OCIO::Config::CreateFromFile(configfile.c_str()); loaded->validate();
    float px[3]={-1.5f,.125f,32.f};
    loaded->getProcessor("scaled","linear")->getDefaultCPUProcessor()->applyRGB(px);
    near(px[0],-3,1.e-6f,"OCIO negative matrix"); near(px[1],.375,1.e-6f,"OCIO matrix"); near(px[2],128,1.e-5f,"OCIO HDR matrix");
    loaded->getProcessor("linear","scaled")->getDefaultCPUProcessor()->applyRGB(px);
    near(px[0],-1.5,1.e-6f,"OCIO inverse negative"); near(px[2],32,1.e-5f,"OCIO inverse HDR");
    auto lt=OCIO::LookTransform::Create(); lt->setSrc("linear"); lt->setDst("linear"); lt->setLooks("boost");
    float lp[3]={-.25f,.5f,8.f}; loaded->getProcessor(lt)->getDefaultCPUProcessor()->applyRGB(lp);
    near(lp[0],-.2125,1.e-5f,"OCIO negative look"); near(lp[2],10.1,1.e-4f,"OCIO HDR look");
    auto cube=root/"local LUT.cube"; { std::ofstream out(cube); out<<"TITLE \"offline fixture\"\nLUT_1D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n0 0 0\n2 3 4\n"; }
    auto ft=OCIO::FileTransform::Create(); ft->setSrc(cube.c_str()); ft->setInterpolation(OCIO::INTERP_LINEAR);
    float cp[3]={.25,.5,.75}; loaded->getProcessor(ft)->getDefaultCPUProcessor()->applyRGB(cp);
    near(cp[0],.5,1.e-5f,"OCIO LUT R"); near(cp[1],1.5,1.e-5f,"OCIO LUT G"); near(cp[2],3,1.e-5f,"OCIO LUT B");
    auto absent=OCIO::FileTransform::Create(); absent->setSrc((root/"missing.cube").c_str());
    throws([&]{loaded->getProcessor(absent);},"OCIO missing LUT");
    {std::ofstream out(root/"malformed.cube");out<<"LUT_3D_SIZE 0\ngarbage\n";}
    absent->setSrc((root/"malformed.cube").c_str()); throws([&]{loaded->getProcessor(absent);},"OCIO malformed LUT");
    std::cout<<"PASS OCIO serialized local config, negative/HDR matrix/inverse/look/LUT + errors\n";
    return configfile;
}
static void dds_volume(const fs::path& file) {
    std::array<std::uint32_t,32> header{};
    header[0]=0x20534444; header[1]=124; header[2]=0x0080100f;
    header[3]=2; header[4]=2; header[5]=8; header[6]=2;
    header[19]=32; header[20]=0x41; header[22]=32;
    header[23]=0xff;header[24]=0xff00;header[25]=0xff0000;header[26]=0xff000000;
    header[27]=0x1008;header[28]=0x200000;
    std::ofstream out(file,std::ios::binary);
    for(auto word:header) for(int i=0;i<4;++i) out.put(static_cast<char>((word>>(8*i))&255));
    for(int i=0;i<8;++i){out.put(char(20+i));out.put(char(40+i));out.put(char(60+i));out.put(char(255));}
}
static void oiio(const fs::path& root,const fs::path& configfile) {
    OIIO::attribute("threads",2); OIIO::attribute("exr_threads",2);
    constexpr int w=64,h=32,n=3;
    std::vector<unsigned char> pixels(w*h*n);
    for(int i=0;i<w*h;++i){pixels[i*n]=64;pixels[i*n+1]=128;pixels[i*n+2]=192;}
    for(const char* extension:{"png","tif","jpg","webp","jp2","tga","bmp"}) {
        auto file=root/(std::string("image with space.")+extension);
        auto out=OIIO::ImageOutput::create(file.string()); require(bool(out),std::string("OIIO create ")+extension+": "+OIIO::geterror());
        OIIO::ImageSpec spec(w,h,n,OIIO::TypeDesc::UINT8); spec.attribute("ImageDescription","HarmonyOS image metadata"); spec.set_colorspace("srgb_rec709_scene");
        spec.attribute("CompressionQuality",100);
        require(out->open(file.string(),spec),std::string("OIIO open output ")+extension+": "+out->geterror());
        require(out->write_image(OIIO::span<unsigned char>(pixels)),std::string("OIIO write ")+extension+": "+out->geterror());
        require(out->close(),std::string("OIIO close ")+extension);
        auto in=OIIO::ImageInput::open(file.string()); require(bool(in),std::string("OIIO open input ")+extension+": "+OIIO::geterror());
        auto got=in->spec(); require(got.width==w&&got.height==h&&got.nchannels>=3,std::string("OIIO dimensions ")+extension);
        std::vector<unsigned char> decoded(w*h*got.nchannels);
        require(in->read_image(0,0,0,-1,OIIO::span<unsigned char>(decoded)),std::string("OIIO decode ")+extension+": "+in->geterror());
        int tolerance=std::string(extension)=="jpg"?5:2;
        for(int i=0;i<w*h;++i) for(int c=0;c<3;++c) near(decoded[i*got.nchannels+c],pixels[i*3+c],float(tolerance),std::string("OIIO pixels ")+extension);
        if(std::string(extension)=="tif") require(got.get_string_attribute("ImageDescription")=="HarmonyOS image metadata","TIFF image description metadata");
        require(in->close(),std::string("OIIO close input ")+extension);
        std::cout<<"PASS OIIO "<<extension<<" read/write/pixels\n";
    }
    auto exrin=OIIO::ImageInput::open((root/"HDR negative zip.exr").string()); require(bool(exrin),"OIIO direct EXR consumption");
    require(exrin->spec().get_string_attribute("color-proof")=="HarmonyOS static color dependency acceptance","OIIO EXR custom metadata");
    require(!exrin->spec().get_string_attribute("oiio:ColorSpace").empty(),"OIIO EXR color metadata");
    std::vector<float> hdr(16*8*4); require(exrin->read_image(0,0,0,-1,OIIO::span<float>(hdr)),"OIIO EXR FLOAT read");
    int rc=-1,bc=-1; for(int c=0;c<4;++c){if(exrin->spec().channelnames[c]=="R")rc=c;if(exrin->spec().channelnames[c]=="B")bc=c;}
    require(rc>=0&&bc>=0,"OIIO EXR channel names"); near(hdr[rc],-2,0,"OIIO EXR negative");near(hdr[bc],32,0,"OIIO EXR HDR"); exrin->close();
    std::vector<unsigned char> encoded; OIIO::Filesystem::IOVecOutput proxyout(encoded);
    auto memout=OIIO::ImageOutput::create("memory.png",&proxyout); require(bool(memout)&&memout->supports("ioproxy"),"PNG memory output proxy support");
    OIIO::ImageSpec ms(w,h,n,OIIO::TypeDesc::UINT8); require(memout->open("memory.png",ms),"PNG memory open");
    require(memout->write_image(OIIO::span<unsigned char>(pixels))&&memout->close(),"PNG memory write"); require(!encoded.empty(),"PNG memory bytes");
    OIIO::Filesystem::IOMemReader proxyin(encoded.data(),encoded.size());
    auto memin=OIIO::ImageInput::open("memory.png",nullptr,&proxyin); require(bool(memin),"PNG memory decode open");
    std::vector<unsigned char> memdecoded(pixels.size()); require(memin->read_image(0,0,0,-1,OIIO::span<unsigned char>(memdecoded)),"PNG memory read"); require(memdecoded==pixels,"PNG memory pixel equality"); memin->close();
    OIIO::ImageSpec floatSpec(2,1,3,OIIO::TypeDesc::FLOAT); OIIO::ImageBuf src(floatSpec),dst;
    float sample[3]={-1.5f,.125f,32.f}; src.setpixel(0,0,sample);src.setpixel(1,0,sample);
    OIIO::ColorConfig colors(configfile.string());
    require(OIIO::ImageBufAlgo::colorconvert(dst,src,"scaled","linear",false,"","",&colors,{},2),"OIIO explicit offline OCIO conversion: "+dst.geterror());
    float converted[3]={};dst.getpixel(0,0,converted);near(converted[0],-3,1.e-5f,"OIIO negative color conversion");near(converted[2],128,1.e-3f,"OIIO HDR color conversion");
    auto dds=root/"volume.dds";dds_volume(dds);auto volume=OIIO::ImageInput::open(dds.string());require(bool(volume),"OIIO DDS volume open");
    require(volume->spec().depth==2&&volume->spec().tile_width==2&&volume->spec().tile_depth==1,"DDS volume emulated tile dimensions");
    std::vector<unsigned char> voxels(32);require(volume->read_image(0,0,0,-1,OIIO::span<unsigned char>(voxels)),"OIIO DDS volume decode");
    for(int i=0;i<8;++i){near(voxels[i*4],20+i,0,"DDS volume R");near(voxels[i*4+3],255,0,"DDS volume alpha");}volume->close();
    require(!OIIO::ImageInput::open((root/"missing.png").string()),"OIIO missing file rejection"); require(!OIIO::geterror().empty(),"OIIO missing diagnostic");
    require(!OIIO::ImageInput::open((root/"corrupt.exr").string()),"OIIO corrupt file rejection");require(!OIIO::geterror().empty(),"OIIO corrupt diagnostic");
    std::cout<<"PASS OIIO EXR negative/HDR/color metadata, memory proxy, ImageBuf+OCIO, DDS volume and errors\n";
}
int main(int argc,char**argv) {
    try {
        require(argc==2,"private fixture directory argument required"); fs::path root=argv[1]; require(fs::is_directory(root),"fixture directory must exist");
        std::cout<<"ABI libc++="<<_LIBCPP_VERSION<<" OIIO="<<OIIO_VERSION_STRING<<" OCIO="<<OCIO::GetVersion()<<"\n";
        exr(root); auto configfile=ocio(root); oiio(root,configfile);
        std::cout<<"ALL PASS checks="<<checks<<"\n";return 0;
    }catch(const std::exception&e){std::cerr<<"FAIL after checks="<<checks<<": "<<e.what()<<"\n";return 1;}
}
