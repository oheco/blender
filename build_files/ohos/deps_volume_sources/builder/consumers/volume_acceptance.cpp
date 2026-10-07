#include <openvdb/openvdb.h>
#include <openvdb/io/File.h>
#include <openvdb/io/Compression.h>
#include <openvdb/tools/MeshToVolume.h>
#include <openvdb/tools/VolumeToMesh.h>
#include <nanovdb/tools/CreateNanoGrid.h>
#include <nanovdb/io/IO.h>
#include <oneapi/tbb/global_control.h>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
namespace fs=std::filesystem;
static int checks=0;
static void require(bool ok,const std::string& why){++checks;if(!ok)throw std::runtime_error(why);}
static void near(double a,double b,double eps,const std::string& why){require(std::isfinite(a)&&std::abs(a-b)<=eps,why);}
template<class F> static void throws(F f,const std::string& why){bool rejected=false;try{f();}catch(const std::exception& e){rejected=true;require(*e.what(),why+" diagnostic");}require(rejected,why);}
static float value(int x,int y,int z){return float((x+4)*16+(y+2)*4+(z+1))-24.5f;}
static void grid_checks(const openvdb::FloatGrid& grid){
 require(grid.getName()=="negative HDR sparse","VDB name");
 require(grid.activeVoxelCount()==96,"VDB active voxel topology");
 require(grid.metaValue<std::string>("volume-proof")=="HarmonyOS native static volume acceptance","VDB custom metadata");
 near(grid.voxelSize().x(),.25,0,"VDB voxel size");
 auto world=grid.indexToWorld(openvdb::Vec3d(0));near(world.x(),1,0,"VDB transform X");near(world.y(),2,0,"VDB transform Y");near(world.z(),3,0,"VDB transform Z");
 for(int x=-4;x<4;++x)for(int y=-2;y<2;++y)for(int z=-1;z<2;++z){openvdb::Coord p(x,y,z);require(grid.tree().isValueOn(p),"VDB active value");near(grid.tree().getValue(p),value(x,y,z),0,"VDB lossless negative/HDR values");}
 require(!grid.tree().isValueOn(openvdb::Coord(100,100,100)),"VDB inactive exterior");near(grid.tree().getValue(openvdb::Coord(100,100,100)),0,0,"VDB background");
}
static void sparse_io(const fs::path& root,openvdb::FloatGrid::Ptr grid){
 require(openvdb::io::Archive::hasBloscCompression(),"actual VDB Blosc support");require(openvdb::io::Archive::hasZLibCompression(),"actual VDB Zlib support");
 require(!openvdb::io::Archive::isDelayedLoadingEnabled(),"retained Blender delayed-loading policy");
 for(auto codec:{openvdb::io::COMPRESS_ZIP,openvdb::io::COMPRESS_BLOSC}){
  auto file=root/(codec==openvdb::io::COMPRESS_ZIP?"sparse ZIP volume.vdb":"sparse BLOSC volume.vdb");
  std::cerr<<"TRACE VDB write codec="<<codec<<"\n";
  openvdb::io::File out(file.string());out.setCompression(codec|openvdb::io::COMPRESS_ACTIVE_MASK);out.write(openvdb::GridPtrVec{grid});
  std::cerr<<"TRACE VDB read codec="<<codec<<"\n";
  require(fs::file_size(file)>0,"VDB actual file written");openvdb::io::File in(file.string());in.open();
  auto read=openvdb::gridPtrCast<openvdb::FloatGrid>(in.readGrid(grid->getName()));require(bool(read),"VDB typed read");grid_checks(*read);in.close();
 }
 throws([&]{openvdb::io::File in((root/"missing.vdb").string());in.open();},"VDB missing file rejected");
 {std::ofstream out(root/"corrupt.vdb",std::ios::binary);out<<"invalid OpenVDB fixture";}
 throws([&]{openvdb::io::File in((root/"corrupt.vdb").string());in.open();},"VDB corrupt file rejected");
 std::cout<<"PASS OpenVDB sparse negative/HDR/topology/transform/metadata ZIP+BLOSC IO and errors\n";
}
static void mesh_conversion(){
 std::vector<openvdb::Vec3s> points={{-1,-1,-1},{1,-1,-1},{1,1,-1},{-1,1,-1},{-1,-1,1},{1,-1,1},{1,1,1},{-1,1,1}};
 std::vector<openvdb::Vec4I> quads={{0,3,2,1},{4,5,6,7},{0,1,5,4},{3,7,6,2},{0,4,7,3},{1,2,6,5}};
 auto transform=openvdb::math::Transform::createLinearTransform(.25);
 auto grid=openvdb::tools::meshToLevelSet<openvdb::FloatGrid>(*transform,points,quads,3.f);
 require(bool(grid)&&grid->activeVoxelCount()>0,"mesh-to-volume creates real active voxels");require(grid->getGridClass()==openvdb::GRID_LEVEL_SET,"mesh volume class");
 require(grid->tree().getValue(openvdb::Coord(0,0,0))<0,"closed cube interior negative");require(grid->tree().getValue(openvdb::Coord(12,0,0))>0,"closed cube exterior positive");
 std::vector<openvdb::Vec3s> outpoints;std::vector<openvdb::Vec4I> outquads;openvdb::tools::volumeToMesh(*grid,outpoints,outquads,0.0);
 require(!outpoints.empty()&&!outquads.empty(),"volume-to-mesh real surface");
 float lo=10,hi=-10;for(const auto& p:outpoints){for(int i=0;i<3;++i){require(std::isfinite(p[i])&&std::abs(p[i])<1.5,"finite cube surface bounds");lo=std::min(lo,p[i]);hi=std::max(hi,p[i]);}}
 require(lo<-.5&&hi>.5,"converted surface retains extent");std::map<std::pair<unsigned,unsigned>,unsigned> edges;
 for(const auto& q:outquads)for(int i=0;i<4;++i){unsigned a=q[i],b=q[(i+1)%4];require(a<outpoints.size()&&b<outpoints.size()&&a!=b,"valid mesh indices");++edges[std::minmax(a,b)];}
 for(const auto& edge:edges)require(edge.second==2,"closed converted mesh edge incidence");
 require(long(outpoints.size())-long(edges.size())+long(outquads.size())==2,"converted cube Euler topology");
 std::cout<<"PASS OpenVDB meshToLevelSet/volumeToMesh cube vertices="<<outpoints.size()<<" quads="<<outquads.size()<<"\n";
}
static void nano_conversion(const fs::path& root,const openvdb::FloatGrid& source){
 std::cerr<<"TRACE createNanoGrid begin\n";
 auto handle=nanovdb::tools::createNanoGrid(source);
 std::cerr<<"TRACE createNanoGrid complete\n";
 auto grid=handle.grid<float>();require(grid!=nullptr,"real OpenVDB-to-NanoVDB conversion");
 require(grid->activeVoxelCount()==source.activeVoxelCount(),"NanoVDB active topology");require(std::string(grid->gridName())==source.getName(),"NanoVDB grid name");near(grid->voxelSize()[0],.25,0,"NanoVDB voxel size");
 for(int x=-4;x<4;++x)for(int y=-2;y<2;++y)for(int z=-1;z<2;++z)near(grid->tree().getValue(nanovdb::Coord(x,y,z)),value(x,y,z),0,"NanoVDB converted negative/HDR values");
 for(auto codec:{nanovdb::io::Codec::ZIP,nanovdb::io::Codec::BLOSC}){
  auto path=root/(codec==nanovdb::io::Codec::ZIP?"converted ZIP volume.nvdb":"converted BLOSC volume.nvdb");
  std::cerr<<"TRACE NanoVDB write codec="<<int(codec)<<"\n";nanovdb::io::writeGrid(path.string(),handle,codec);
  std::cerr<<"TRACE NanoVDB read codec="<<int(codec)<<"\n";
  auto read=nanovdb::io::readGrid(path.string());auto decoded=read.grid<float>();require(decoded!=nullptr,"NanoVDB typed native file read");
  std::cerr<<"TRACE NanoVDB decoded codec="<<int(codec)<<"\n";
  require(decoded->activeVoxelCount()==96,"NanoVDB read topology");
  for(int x=-4;x<4;++x)for(int y=-2;y<2;++y)for(int z=-1;z<2;++z)near(decoded->tree().getValue(nanovdb::Coord(x,y,z)),value(x,y,z),0,"NanoVDB lossless native IO");
 }
 std::cerr<<"TRACE NanoVDB missing file rejection\n";
 throws([&]{auto h=nanovdb::io::readGrid((root/"missing.nvdb").string());},"NanoVDB missing file rejected");
 {std::ofstream out(root/"corrupt.nvdb",std::ios::binary);out<<"invalid NanoVDB fixture";}
 std::cerr<<"TRACE NanoVDB corrupt file rejection\n";
 throws([&]{auto h=nanovdb::io::readGrid((root/"corrupt.nvdb").string());},"NanoVDB corrupt file rejected");
 std::cout<<"PASS NanoVDB CPU conversion/topology/negative HDR values ZIP+BLOSC IO and errors\n";
}
int main(int argc,char**argv){try{
 require(argc==2,"private fixture root required");fs::path root=argv[1];require(fs::is_directory(root),"fixture root exists");
 oneapi::tbb::global_control limit(oneapi::tbb::global_control::max_allowed_parallelism,2);openvdb::initialize();
 std::cout<<"ABI libc++="<<_LIBCPP_VERSION<<" OpenVDB="<<openvdb::OPENVDB_LIBRARY_MAJOR_VERSION<<'.'<<openvdb::OPENVDB_LIBRARY_MINOR_VERSION<<'.'<<openvdb::OPENVDB_LIBRARY_PATCH_VERSION<<" NanoVDB="<<NANOVDB_MAJOR_VERSION_NUMBER<<'.'<<NANOVDB_MINOR_VERSION_NUMBER<<'.'<<NANOVDB_PATCH_VERSION_NUMBER<<"\n";
 std::cerr<<"TRACE grid create\n";
 auto grid=openvdb::FloatGrid::create(0);grid->setName("negative HDR sparse");grid->insertMeta("volume-proof",openvdb::StringMetadata("HarmonyOS native static volume acceptance"));
 auto transform=openvdb::math::Transform::createLinearTransform(.25);transform->postTranslate(openvdb::Vec3d(1,2,3));grid->setTransform(transform);
 for(int x=-4;x<4;++x)for(int y=-2;y<2;++y)for(int z=-1;z<2;++z)grid->tree().setValue(openvdb::Coord(x,y,z),value(x,y,z));
 std::cerr<<"TRACE grid checks\n";grid_checks(*grid);
 std::cerr<<"TRACE sparse IO\n";sparse_io(root,grid);
 std::cerr<<"TRACE mesh conversion\n";mesh_conversion();
 std::cerr<<"TRACE nano conversion\n";nano_conversion(root,*grid);
 std::cerr<<"TRACE uninitialize\n";openvdb::uninitialize();
 std::cout<<"ALL PASS volume checks="<<checks<<"\n";return 0;
 }catch(const std::exception& e){std::cerr<<"FAIL volume after checks="<<checks<<": "<<e.what()<<"\n";return 1;}}
