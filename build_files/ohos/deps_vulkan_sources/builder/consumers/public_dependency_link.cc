// Minimal public-package/link/ABI smoke, not full shader or rendering acceptance.
// SPIRV_REFLECT_USE_SYSTEM_SPIRV_H must come from the installed import target.
#ifndef SPIRV_REFLECT_USE_SYSTEM_SPIRV_H
#error "Installed Reflect import lost the public system SPIRV-Headers contract"
#endif
#include <shaderc/shaderc.hpp>
#include <spirv-tools/libspirv.hpp>
#include <spirv-tools/optimizer.hpp>
#include <spirv_reflect.h>
#include <spirv/unified1/spirv.hpp11>
#include <cstdio>
#include <vector>
static_assert(SpvMagicNumber==0x07230203u,"fixed SPIR-V header magic");
int main()
{
  shaderc::Compiler compiler;
  if(!compiler.IsValid())return 1;
  shaderc::CompileOptions options;
  options.SetTargetEnvironment(shaderc_target_env_vulkan,shaderc_env_version_vulkan_1_3);
  options.SetTargetSpirv(shaderc_spirv_version_1_6);
  unsigned version=0,revision=0;
  shaderc_get_spv_version(&version,&revision);
  spvtools::SpirvTools tools(SPV_ENV_VULKAN_1_3);
  spvtools::Optimizer optimizer(SPV_ENV_VULKAN_1_3);
  optimizer.RegisterPerformancePasses();
  const std::vector<uint32_t> bad={0,0,0,0,0};
  if(tools.Validate(bad))return 2;
  SpvReflectShaderModule module{};
  if(spvReflectCreateShaderModule(bad.size()*4,bad.data(),&module)==SPV_REFLECT_RESULT_SUCCESS){spvReflectDestroyShaderModule(&module);return 3;}
  std::printf("{\"publicSPIRVHeaders\":true,\"propagatedReflectMacro\":true,\"FindShaderCCombined\":true,\"realShadercCompilerLifecycle\":true,\"optimizerAndValidatorSymbols\":true,\"reflectSymbols\":true,\"spirvVersion\":%u,\"spirvRevision\":%u}\n",version,revision);
  return 0;
}
