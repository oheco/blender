#include <manifold/manifold.h>
#include <manifold/mesh.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <map>
#include <numeric>
#include <set>
#include <vector>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <string>
using manifold::Manifold;
static void topology(const Manifold&m,int genus){
 assert(m.Status()==Manifold::Error::NoError);assert(!m.IsEmpty());assert(m.Genus()==genus);
 auto mesh=m.GetMeshGL64();assert(mesh.NumTri()==m.NumTri());
 std::vector<size_t> parent(mesh.NumVert());std::iota(parent.begin(),parent.end(),0);
 auto root=[&](size_t v){while(parent[v]!=v){parent[v]=parent[parent[v]];v=parent[v];}return v;};
 assert(mesh.mergeFromVert.size()==mesh.mergeToVert.size());
 for(size_t i=0;i<mesh.mergeFromVert.size();++i)parent[root(mesh.mergeFromVert[i])]=root(mesh.mergeToVert[i]);
 std::map<std::pair<size_t,size_t>,int> edges;std::set<size_t> vertices;
 for(size_t i=0;i<mesh.triVerts.size();i+=3){size_t v[3];for(int j=0;j<3;++j){assert(mesh.triVerts[i+j]<mesh.NumVert());v[j]=root(mesh.triVerts[i+j]);vertices.insert(v[j]);}assert(v[0]!=v[1]&&v[1]!=v[2]&&v[2]!=v[0]);for(int j=0;j<3;++j)++edges[{v[j],v[(j+1)%3]}];}
 for(const auto &e:edges){assert(e.second==1);assert(edges.at({e.first.second,e.first.first})==1);}
 long long euler=(long long)vertices.size()-(long long)edges.size()/2+(long long)mesh.NumTri();assert(euler==2-2*genus);
 Manifold roundtrip(mesh);assert(roundtrip.Status()==Manifold::Error::NoError);assert(std::abs(roundtrip.Volume()-m.Volume())<1e-8);
}
static void export_roundtrip(const Manifold &solid,const std::filesystem::path &file){
 auto mesh=solid.GetMeshGL64();
 std::ofstream out(file);out<<std::setprecision(17)<<"GEOMETRY_MESH_1 "<<mesh.numProp<<' '<<mesh.vertProperties.size()<<' '<<mesh.triVerts.size()<<' '<<mesh.mergeFromVert.size()<<'\n';
 for(auto x:mesh.vertProperties)out<<x<<' ';out<<'\n';
 for(auto x:mesh.triVerts)out<<x<<' ';out<<'\n';
 for(size_t j=0;j<mesh.mergeFromVert.size();++j)out<<mesh.mergeFromVert[j]<<' '<<mesh.mergeToVert[j]<<'\n';
 assert(out.good());out.close();
 std::ifstream in(file);std::string tag;decltype(mesh) decoded;size_t vertices,indices,merges;
 in>>tag>>decoded.numProp>>vertices>>indices>>merges;assert(tag=="GEOMETRY_MESH_1");
 decoded.vertProperties.resize(vertices);decoded.triVerts.resize(indices);decoded.mergeFromVert.resize(merges);decoded.mergeToVert.resize(merges);
 for(auto &x:decoded.vertProperties)in>>x;for(auto &x:decoded.triVerts)in>>x;
 for(size_t j=0;j<merges;++j)in>>decoded.mergeFromVert[j]>>decoded.mergeToVert[j];assert(in.good());
 assert(decoded.vertProperties==mesh.vertProperties&&decoded.triVerts==mesh.triVerts);
 Manifold restored(decoded);topology(restored,solid.Genus());assert(std::abs(restored.Volume()-solid.Volume())<1e-8);
 std::puts("PASS manifold raw mesh export/private Unicode-space file/reimport exact topology-volume; Assimp formats NOT tested");
}
int main(int argc,char **argv){
 assert(argc==2);
 auto a=Manifold::Cube({1,1,1}),b=a.Translate({.5,0,0});
 auto u=a+b,i=a^b,d=a-b;
 assert(std::abs(u.Volume()-1.5)<1e-9);assert(std::abs(i.Volume()-.5)<1e-9);assert(std::abs(d.Volume()-.5)<1e-9);
 topology(u,0);topology(i,0);topology(d,0);
 auto tunnel=Manifold::Cube({2,2,2},true)-Manifold::Cylinder(4,.5,.5,64,true);
 topology(tunnel,1);assert(tunnel.Volume()>6.4&&tunnel.Volume()<6.5);
 export_roundtrip(tunnel,std::filesystem::path(argv[1])/"隧道 manifold exported mesh.txt");
 auto disjoint=a^a.Translate({10,0,0});assert(disjoint.Status()==Manifold::Error::NoError);assert(disjoint.IsEmpty());
 manifold::MeshGL invalid;invalid.vertProperties={0,0,0,1,0,0,0,1,0};invalid.triVerts={0,1,2};Manifold open(invalid);assert(open.Status()!=Manifold::Error::NoError);
 std::printf("PASS manifold union=1.5 intersection=0.5 difference=0.5; opposite halfedge/Euler closed topology, mesh roundtrip, genus-1 tunnel, disjoint/invalid-input; Blender modifier/HAP NOT tested\n");
}
