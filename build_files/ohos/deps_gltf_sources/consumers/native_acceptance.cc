/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <draco/compression/decode.h>
#include <draco/compression/encode.h>
#include <draco/core/data_buffer.h>
#include <draco/core/decoder_buffer.h>
#include <draco/core/encoder_buffer.h>
#include <draco/core/draco_version.h>
#include <draco/mesh/mesh.h>
#include <meshoptimizer.h>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fstream>
#include <memory>
#include <string>
#include <vector>
#define STR2(x) #x
#define STR(x) STR2(x)
static_assert(_LIBCPP_VERSION == 15004, "actual SDK15 libc++ required");
extern "C" void *encoderCreate(uint32_t);
extern "C" void encoderRelease(void *);
extern "C" size_t encodeVertexBufferBound(size_t,size_t);
static int checks=0;
static void require(bool value,const char *what) { ++checks; if(!value) { std::fprintf(stderr,"FAIL %s\n",what); std::exit(1); } }
int main(int argc,char **argv) {
  require(argc==4,"fixture root plus actual bridge DLL paths");
  require(std::string(STR(_LIBCPP_ABI_NAMESPACE))=="__n1","actual libc++ namespace");
  require(std::string(draco::kDracoVersion)=="1.5.7","actual Draco version");
  require(MESHOPTIMIZER_VERSION==1010,"actual meshoptimizer 1.1 header");
  void *owned_encoder=encoderCreate(4);require(owned_encoder!=nullptr,"CMAKE/PC metadata linked actual Draco bridge ABI");
  encoderRelease(owned_encoder);
  require(encodeVertexBufferBound(4,12)>0,"CMAKE/PC metadata linked actual meshopt bridge ABI");
  const std::string text="顶点 café Δ 圧縮";
  const std::string filename=std::string(argv[1])+"/几何 geometry with spaces.txt";
  { std::ofstream f(filename,std::ios::binary); f<<text; require(bool(f),"UTF8 source codepoints path write"); }
  { std::ifstream f(filename,std::ios::binary); std::string actual((std::istreambuf_iterator<char>(f)),{}); require(actual==text,"UTF8 source codepoints path read"); }
  const std::array<float,12> positions={0,0,0, 1,0,0, 1,1,0, 0,1,0};
  const std::array<float,12> normals={0,0,1, 0,0,1, 0,0,1, 0,0,1};
  const std::array<float,8> uv={0,0, 1,0, 1,1, 0,1};
  const std::array<unsigned int,6> indices={0,1,2, 0,2,3};
  draco::Mesh mesh; mesh.set_num_points(4); mesh.SetNumFaces(2);
  mesh.SetFace(draco::FaceIndex(0),{draco::PointIndex(0),draco::PointIndex(1),draco::PointIndex(2)});
  mesh.SetFace(draco::FaceIndex(1),{draco::PointIndex(0),draco::PointIndex(2),draco::PointIndex(3)});
  std::vector<std::unique_ptr<draco::DataBuffer>> buffers;
  auto attr=[&](draco::GeometryAttribute::Type semantic,int components,const float *data) {
    auto buffer=std::make_unique<draco::DataBuffer>();
    draco::GeometryAttribute a; a.Init(semantic,buffer.get(),components,draco::DT_FLOAT32,false,components*sizeof(float),0);
    int id=mesh.AddAttribute(a,true,4); require(id>=0,"attribute added");
    for(unsigned i=0;i<4;++i) mesh.attribute(id)->SetAttributeValue(draco::AttributeValueIndex(i),data+i*components);
    buffers.emplace_back(std::move(buffer));return id;
  };
  int ids[]={attr(draco::GeometryAttribute::POSITION,3,positions.data()),attr(draco::GeometryAttribute::NORMAL,3,normals.data()),attr(draco::GeometryAttribute::TEX_COORD,2,uv.data())};
  draco::Encoder enc; enc.SetEncodingMethod(draco::MESH_SEQUENTIAL_ENCODING); enc.SetAttributeQuantization(draco::GeometryAttribute::POSITION,14);
  enc.SetAttributeQuantization(draco::GeometryAttribute::NORMAL,12); enc.SetAttributeQuantization(draco::GeometryAttribute::TEX_COORD,14);
  draco::EncoderBuffer encoded;require(enc.EncodeMeshToBuffer(mesh,&encoded).ok(),"Draco real indexed geometry encode");
  draco::DecoderBuffer input; input.Init(encoded.data(),encoded.size()); draco::Decoder decoder;
  auto result=decoder.DecodeMeshFromBuffer(&input);require(result.ok(),"Draco real geometry decode");auto out=std::move(result).value();
  require(out->num_points()==4 && out->num_faces()==2,"decoded indexed geometry counts");
  for(unsigned f=0;f<2;++f) for(unsigned corner=0;corner<3;++corner) {
    auto point=out->face(draco::FaceIndex(f))[corner]; unsigned src=indices[f*3+corner];
    for(int a=0;a<3;++a) { auto *at=out->GetAttributeByUniqueId(ids[a]);require(at!=nullptr,"real attribute ID");
      float v[3]={};require(at->ConvertValue(at->mapped_index(point),v),"attribute conversion");
      const float *expected=a==0?positions.data()+src*3:a==1?normals.data()+src*3:uv.data()+src*2;
      for(int c=0;c<(a==2?2:3);++c) require(std::abs(v[c]-expected[c])<0.002f,"geometry normal UV tolerance");
    }
  }
  char bad[8]={}; draco::DecoderBuffer corrupt;corrupt.Init(bad,sizeof(bad));require(!decoder.DecodeMeshFromBuffer(&corrupt).ok(),"corrupt Draco rejected");
  meshopt_encodeIndexVersion(1);meshopt_encodeVertexVersion(0);
  std::vector<unsigned char> ib(meshopt_encodeIndexBufferBound(6,4));size_t il=meshopt_encodeIndexBuffer(ib.data(),ib.size(),indices.data(),6);require(il>0,"index encode");
  std::array<unsigned int,6> oi={};require(meshopt_decodeIndexBuffer(oi.data(),6,4,ib.data(),il)==0 && oi==indices,"index roundtrip");
  require(meshopt_decodeIndexBuffer(oi.data(),6,4,ib.data(),1)!=0,"corrupt index rejected");
  for(const auto *data:{positions.data(),normals.data()}) {
    std::vector<unsigned char> vb(meshopt_encodeVertexBufferBound(4,12));size_t vl=meshopt_encodeVertexBuffer(vb.data(),vb.size(),data,4,12);require(vl>0,"attribute encode");
    std::array<float,12> ov={};require(meshopt_decodeVertexBuffer(ov.data(),4,12,vb.data(),vl)==0 && std::memcmp(ov.data(),data,sizeof(ov))==0,"attribute exact roundtrip");
    require(meshopt_decodeVertexBuffer(ov.data(),4,12,vb.data(),1)!=0,"corrupt attribute rejected");
  }
  std::vector<unsigned char> ub(meshopt_encodeVertexBufferBound(4,8));size_t ul=meshopt_encodeVertexBuffer(ub.data(),ub.size(),uv.data(),4,8);
  std::array<float,8> ou={};require(ul>0 && meshopt_decodeVertexBuffer(ou.data(),4,8,ub.data(),ul)==0 && ou==uv,"UV exact roundtrip");
  for(int i=2;i<4;++i) {
    void *h=dlopen(argv[i],RTLD_NOW|RTLD_LOCAL);require(h!=nullptr,"actual Blender DLL dlopen");
    require(dlsym(h,i==2?"encoderCreate":"encodeVertexBuffer")!=nullptr,"actual Blender exported C ABI");
    require(dlclose(h)==0,"DLL handle ownership release");
  }
  std::printf("ALL PASS gltf native checks=%d libcxx=%d namespace=%s\n",checks,_LIBCPP_VERSION,STR(_LIBCPP_ABI_NAMESPACE));
}
