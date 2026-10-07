#include <opensubdiv/far/topologyDescriptor.h>
#include <opensubdiv/far/topologyRefinerFactory.h>
#include <opensubdiv/far/stencilTableFactory.h>
#include <opensubdiv/osd/cpuEvaluator.h>
#include <opensubdiv/osd/tbbEvaluator.h>
#include <opensubdiv/osd/glslPatchShaderSource.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>
int main(){
 namespace osd=OpenSubdiv;
 int counts[6]={4,4,4,4,4,4};
 int faces[24]={0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7};
 float positions[24]={-1,-1,-1,1,-1,-1,1,1,-1,-1,1,-1,-1,-1,1,1,-1,1,1,1,1,-1,1,1};
 osd::Far::TopologyDescriptor desc;desc.numVertices=8;desc.numFaces=6;desc.numVertsPerFace=counts;desc.vertIndicesPerFace=faces;
 osd::Far::TopologyRefinerFactory<osd::Far::TopologyDescriptor>::Options options(osd::Sdc::SCHEME_CATMARK);
 std::unique_ptr<osd::Far::TopologyRefiner> ref(osd::Far::TopologyRefinerFactory<osd::Far::TopologyDescriptor>::Create(desc,options));assert(ref);
 ref->RefineUniform(osd::Far::TopologyRefiner::UniformOptions(2));
 assert(ref->GetLevel(2).GetNumVertices()==98);assert(ref->GetLevel(2).GetNumFaces()==96);
 osd::Far::StencilTableFactory::Options so;so.generateOffsets=true;so.generateIntermediateLevels=false;
 std::unique_ptr<const osd::Far::StencilTable> table(osd::Far::StencilTableFactory::Create(*ref,so));assert(table);assert(table->GetNumStencils()==98);
 std::vector<float> cpu(98*3),parallel(98*3);
 osd::Osd::BufferDescriptor layout(0,3,3);
 auto evaluate=[&](float *dst,bool threads){
  if(threads)return osd::Osd::TbbEvaluator::EvalStencils(positions,layout,dst,layout,table->GetSizes().data(),table->GetOffsets().data(),table->GetControlIndices().data(),table->GetWeights().data(),0,98);
  return osd::Osd::CpuEvaluator::EvalStencils(positions,layout,dst,layout,table->GetSizes().data(),table->GetOffsets().data(),table->GetControlIndices().data(),table->GetWeights().data(),0,98);
 };
 assert(evaluate(cpu.data(),false));assert(evaluate(parallel.data(),true));
 double centroid[3]={0,0,0};
 for(size_t i=0;i<cpu.size();++i){assert(std::isfinite(cpu[i]));assert(std::abs(cpu[i])<=1.00001);assert(std::abs(cpu[i]-parallel[i])<1e-6);centroid[i%3]+=cpu[i];}
 for(auto c:centroid)assert(std::abs(c)<1e-4);
 for(int s=0;s<98;++s){double sum=0;int begin=table->GetOffsets()[s];for(int i=0;i<table->GetSizes()[s];++i)sum+=table->GetWeights()[begin+i];assert(std::abs(sum-1)<1e-5);}
 auto basis=osd::Osd::GLSLPatchShaderSource::GetPatchBasisShaderSource();
 assert(basis.size()>1000);assert(basis.find("OsdEvaluatePatchBasis")!=std::string::npos);
 std::printf("PASS Catmull-Clark cube L2=98 vertices/96 faces, CPU==TBB stencils, affine weights/centroid, GLSL basis bytes=%zu; GPU dispatch/Blender Vulkan NOT tested\n",basis.size());
}
