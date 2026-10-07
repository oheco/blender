// SPDX-License-Identifier: GPL-2.0-or-later
// Real generated OpenSubdiv basis + declared accepted ShaderC/SPIRV validator.
#include <opensubdiv/osd/glslPatchShaderSource.h>
#include <shaderc/shaderc.hpp>
#include <spirv-tools/libspirv.hpp>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

int main(int argc, char **argv)
{
  assert(argc == 2);
  const std::filesystem::path fixtures(argv[1]);
  const std::string basis = OpenSubdiv::Osd::GLSLPatchShaderSource::GetPatchBasisShaderSource();
  assert(basis.size() > 1000 && basis.find("OsdEvaluatePatchBasis") != std::string::npos);
  const std::string shader = "#version 450\n#define OSD_PATCH_BASIS_GLSL\n" + basis + R"GLSL(
layout(local_size_x=1) in;
layout(set=0, binding=0, std430) buffer Result { float result[]; } output_data;
void main() {
  float wp[20], ds[20], dt[20], dss[20], dst[20], dtt[20];
  OsdPatchParam param = OsdPatchParamInit(0, 0, 0.0);
  int count = OsdEvaluatePatchBasis(OSD_PATCH_DESCRIPTOR_REGULAR, param, 0.25, 0.75,
                                   wp, ds, dt, dss, dst, dtt);
  float total = 0.0;
  for (int i=0; i<count; ++i) total += wp[i];
  output_data.result[0] = total;
}
)GLSL";
  shaderc::Compiler compiler;
  shaderc::CompileOptions options;
  options.SetTargetEnvironment(shaderc_target_env_vulkan, shaderc_env_version_vulkan_1_3);
  options.SetTargetSpirv(shaderc_spirv_version_1_6);
  options.SetWarningsAsErrors();
  std::vector<uint32_t> first;
  int checks = 0;
  for (const auto level : {shaderc_optimization_level_zero, shaderc_optimization_level_performance}) {
    options.SetOptimizationLevel(level);
    auto compiled = compiler.CompileGlslToSpv(shader, shaderc_compute_shader, "opensubdiv-real-basis.comp", options);
    if (compiled.GetCompilationStatus() != shaderc_compilation_status_success) {
      std::fprintf(stderr, "%s\n", compiled.GetErrorMessage().c_str());
      return 1;
    }
    std::vector<uint32_t> words(compiled.cbegin(), compiled.cend());
    assert(words.size() > 5 && words[0] == 0x07230203 && words[1] == 0x00010600);
    spvtools::SpirvTools validator(SPV_ENV_VULKAN_1_3);
    assert(validator.IsValid() && validator.Validate(words));
    ++checks;
    const auto file = fixtures / (level == shaderc_optimization_level_zero ? "basis-unoptimized.spv" : "basis-optimized.spv");
    std::ofstream stream(file, std::ios::binary);
    stream.write(reinterpret_cast<const char *>(words.data()), words.size() * sizeof(uint32_t));
    assert(stream.good());
    if (first.empty()) first = words;
  }
  auto invalid = compiler.CompileGlslToSpv(shader + "\nvoid impossible(){undefined_variable=7;}\n",
                                         shaderc_compute_shader, "invalid-real-basis.comp", options);
  assert(invalid.GetCompilationStatus() != shaderc_compilation_status_success && !invalid.GetErrorMessage().empty());
  ++checks;
  first[0] = 0;
  spvtools::SpirvTools validator(SPV_ENV_VULKAN_1_3);
  assert(!validator.Validate(first));
  ++checks;
  std::printf("ALL PASS GLSL checks=%d basis_bytes=%zu Vulkan1.3/SPIRV1.6 compile+optimized+validator+errors; GPU dispatch NOT tested\n", checks, basis.size());
}
