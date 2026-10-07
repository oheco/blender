// A real library consumer: does not spawn glslang/glslc or use their outputs.
#ifndef SPIRV_REFLECT_USE_SYSTEM_SPIRV_H
#error "Installed metadata must propagate the system SPIRV-Headers contract"
#endif
#include <shaderc/shaderc.hpp>
#include <spirv-tools/libspirv.hpp>
#include <spirv-tools/optimizer.hpp>
#include <spirv_reflect.h>
#include <spirv/unified1/spirv.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

static void require(bool ok, const std::string &what) { if (!ok) throw std::runtime_error(what); }
static const char *compute_source = R"GLSL(#version 450
layout(local_size_x=8, local_size_y=1, local_size_z=1) in;
layout(set=1,binding=2,std430) buffer Out { vec4 value[]; } outData;
layout(push_constant,std430) uniform Params { vec4 tint; uint count; } params;
void main() { uint i=gl_GlobalInvocationID.x; if (i<params.count) outData.value[i]=params.tint*2.0; }
)GLSL";
static const char *tile_source = R"GLSL(#version 460
#extension GL_EXT_shader_tile_image : require
layout(location=0) tileImageEXT highp attachmentEXT previous_color;
layout(location=0) out highp vec4 out_color;
void main() { out_color=colorAttachmentReadEXT(previous_color); }
)GLSL";

static std::vector<uint32_t> compile(shaderc::Compiler &compiler, const char *source, shaderc_shader_kind stage, bool optimize=false)
{
  shaderc::CompileOptions options;
  options.SetTargetEnvironment(shaderc_target_env_vulkan,shaderc_env_version_vulkan_1_3);
  options.SetTargetSpirv(shaderc_spirv_version_1_6);
  options.SetOptimizationLevel(optimize ? shaderc_optimization_level_performance : shaderc_optimization_level_zero);
  auto result=compiler.CompileGlslToSpv(source,stage,"real-library-input.glsl","main",options);
  require(result.GetCompilationStatus()==shaderc_compilation_status_success,"CompileGlslToSpv: "+result.GetErrorMessage());
  return {result.cbegin(),result.cend()};
}
static void structure(const std::vector<uint32_t> &words, bool tile)
{
  require(words.size()>=5 && words[0]==SpvMagicNumber,"SPIR-V magic");
  require(words[1]==0x00010600u,"Vulkan1.3 target generated SPIR-V1.6");
  require(words[3]>0 && words[4]==0,"SPIR-V bound/schema");
  bool extension=false,capability=false,instruction=false;
  for(size_t offset=5;offset<words.size();) {
    const uint32_t n=words[offset]>>16, op=words[offset]&0xffff;
    require(n!=0 && offset+n<=words.size(),"SPIR-V instruction bounds");
    if(op==SpvOpExtension && n>=2) {
      const char *s=reinterpret_cast<const char *>(&words[offset+1]);
      require(std::memchr(s,0,(n-1)*4)!=nullptr,"terminated OpExtension");
      if(std::strcmp(s,"SPV_EXT_shader_tile_image")==0) extension=true;
    }
    if(op==SpvOpCapability && n==2 && words[offset+1]==SpvCapabilityTileImageColorReadAccessEXT) capability=true;
    if(op==SpvOpColorAttachmentReadEXT) instruction=true;
    if(tile && op==SpvOpExecutionMode && n>=3)
      require(words[offset+2]!=SpvExecutionModeNonCoherentColorAttachmentReadEXT,"unexpected noncoherent color execution mode");
    offset+=n;
  }
  if(tile) require(extension && capability && instruction,"actual tile image extension/capability/color-read instruction");
}
static void validate(const std::vector<uint32_t> &words)
{
  spvtools::SpirvTools tools(SPV_ENV_VULKAN_1_3);
  std::string message;
  tools.SetMessageConsumer([&](spv_message_level_t,const char*,const spv_position_t&,const char *m) { message+=m; message+='\n'; });
  const bool valid=tools.Validate(words);
  require(valid,"SPIRV-Tools validator: "+message);
  std::string assembly;
  require(tools.Disassemble(words,&assembly),"SPIRV-Tools disassembly");
  require(assembly.find("OpEntryPoint")!=std::string::npos,"real disassembly output");
}
static void reflect_compute(const std::vector<uint32_t> &words)
{
  SpvReflectShaderModule module{};
  require(spvReflectCreateShaderModule(words.size()*4,words.data(),&module)==SPV_REFLECT_RESULT_SUCCESS,"reflect compute create");
  require(module.shader_stage==SPV_REFLECT_SHADER_STAGE_COMPUTE_BIT,"reflect compute stage");
  require(module.entry_point_count==1 && std::string(module.entry_point_name)=="main","reflect entry point");
  require(module.entry_points[0].local_size.x==8 && module.entry_points[0].local_size.y==1 && module.entry_points[0].local_size.z==1,"reflect workgroup");
  uint32_t n=0;
  require(spvReflectEnumerateDescriptorBindings(&module,&n,nullptr)==SPV_REFLECT_RESULT_SUCCESS && n==1,"reflect descriptor count");
  std::vector<SpvReflectDescriptorBinding *> bindings(n);
  require(spvReflectEnumerateDescriptorBindings(&module,&n,bindings.data())==SPV_REFLECT_RESULT_SUCCESS,"reflect descriptor enumeration");
  require(bindings[0]->set==1 && bindings[0]->binding==2 && bindings[0]->descriptor_type==SPV_REFLECT_DESCRIPTOR_TYPE_STORAGE_BUFFER,"reflect descriptor set/binding/type");
  require(bindings[0]->block.member_count==1,"reflect SSBO block members");
  n=0;
  require(spvReflectEnumeratePushConstantBlocks(&module,&n,nullptr)==SPV_REFLECT_RESULT_SUCCESS && n==1,"reflect push count");
  SpvReflectBlockVariable *push=nullptr;
  require(spvReflectEnumeratePushConstantBlocks(&module,&n,&push)==SPV_REFLECT_RESULT_SUCCESS,"reflect push enumerate");
  require(push && push->member_count==2 && push->members[0].offset==0 && push->members[1].offset==16 && push->size>=20,"reflect push member offsets/size");
  spvReflectDestroyShaderModule(&module);
}
static void reflect_tile(const std::vector<uint32_t> &words)
{
  SpvReflectShaderModule module{};
  require(spvReflectCreateShaderModule(words.size()*4,words.data(),&module)==SPV_REFLECT_RESULT_SUCCESS,"reflect tile module");
  require(module.shader_stage==SPV_REFLECT_SHADER_STAGE_FRAGMENT_BIT,"reflect tile fragment stage");
  bool found=false;
  for(uint32_t i=0;i<module.output_variable_count;i++)
    if(module.output_variables[i]->location==0 && module.output_variables[i]->format==SPV_REFLECT_FORMAT_R32G32B32A32_SFLOAT) found=true;
  require(found,"reflect tile color output location0 RGBA32F");
  spvReflectDestroyShaderModule(&module);
}
static void write_spv(const std::filesystem::path &path,const std::vector<uint32_t> &words)
{
  std::ofstream stream(path,std::ios::binary); stream.write(reinterpret_cast<const char *>(words.data()),words.size()*4);
  require(bool(stream),"write SPIR-V fixture");
}
int main(int argc,char **argv)
{
  try {
    require(argc==2,"output directory argument required");
    const std::filesystem::path output(argv[1]); std::filesystem::create_directories(output);
    shaderc::Compiler compiler; require(compiler.IsValid(),"real shaderc compiler initialized");
    auto compute=compile(compiler,compute_source,shaderc_compute_shader);
    structure(compute,false); validate(compute); reflect_compute(compute);
    auto tile=compile(compiler,tile_source,shaderc_fragment_shader);
    structure(tile,true); validate(tile); reflect_tile(tile);
    spvtools::Optimizer optimizer(SPV_ENV_VULKAN_1_3);
    optimizer.RegisterPerformancePasses(); std::string diagnostics;
    optimizer.SetMessageConsumer([&](spv_message_level_t,const char*,const spv_position_t&,const char *message) { diagnostics+=message; diagnostics+='\n'; });
    std::vector<uint32_t> optimized;
    const bool optimized_ok=optimizer.Run(compute.data(),compute.size(),&optimized);
    require(optimized_ok,"SPIRV-Tools optimizer: "+diagnostics);
    structure(optimized,false); validate(optimized); reflect_compute(optimized);
    std::vector<uint32_t> optimized_tile;
    const bool optimized_tile_ok=optimizer.Run(tile.data(),tile.size(),&optimized_tile);
    require(optimized_tile_ok,"SPIRV-Tools tile optimizer: "+diagnostics);
    structure(optimized_tile,true); validate(optimized_tile); reflect_tile(optimized_tile);
    auto shaderc_optimized=compile(compiler,compute_source,shaderc_compute_shader,true);
    validate(shaderc_optimized); reflect_compute(shaderc_optimized);
    shaderc::CompileOptions options;
    options.SetTargetEnvironment(shaderc_target_env_vulkan,shaderc_env_version_vulkan_1_3);
    auto bad=compiler.CompileGlslToSpv("#version 450\nvoid main(){ nonexistent_symbol=7; }",shaderc_fragment_shader,"bad.glsl",options);
    require(bad.GetCompilationStatus()==shaderc_compilation_status_compilation_error && bad.GetNumErrors()>0 && !bad.GetErrorMessage().empty(),"actual shaderc invalid GLSL diagnosis");
    auto corrupt=compute; corrupt[0]=0;
    spvtools::SpirvTools validator(SPV_ENV_VULKAN_1_3);
    require(!validator.Validate(corrupt),"SPIR-V invalid magic rejected");
    SpvReflectShaderModule badmodule{};
    require(spvReflectCreateShaderModule(corrupt.size()*4,corrupt.data(),&badmodule)!=SPV_REFLECT_RESULT_SUCCESS,"reflect invalid magic rejected");
    std::string errors[2];
    std::thread workers[2];
    for(int i=0;i<2;i++) workers[i]=std::thread([&,i] { try { shaderc::Compiler own; auto w=compile(own,compute_source,shaderc_compute_shader); validate(w); } catch(const std::exception &e) { errors[i]=e.what(); } });
    for(auto &worker:workers) worker.join();
    require(errors[0].empty() && errors[1].empty(),"independent threaded shaderc compilers: "+errors[0]+errors[1]);
    write_spv(output/"compute.spv",compute); write_spv(output/"compute-optimized.spv",optimized);
    write_spv(output/"tile.spv",tile); write_spv(output/"tile-optimized.spv",optimized_tile);
    write_spv(output/"compute-shaderc-optimized.spv",shaderc_optimized);
    std::puts("{\"CompileGlslToSpv\":true,\"target\":\"Vulkan1.3/SPIRV1.6\",\"tileExtensionInstruction\":true,\"tileCoherentMode\":true,\"spirvStructure\":true,\"validator\":true,\"optimizerComputeAndTile\":true,\"shadercOptimizer\":true,\"reflectDescriptorAndPushLayout\":true,\"reflectTileOutput\":true,\"invalidGlslRejected\":true,\"invalidSpirvRejected\":true,\"parallelCompilers\":2}");
    return 0;
  } catch(const std::exception &e) { std::fprintf(stderr,"library acceptance failure: %s\n",e.what()); return 1; }
}
