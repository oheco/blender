// SPDX-License-Identifier: GPL-2.0-or-later
#include <spirv-tools/libspirv.hpp>
#include <spirv-tools/optimizer.hpp>
#include <cstdio>
#include <vector>
int main(){spvtools::SpirvTools validator(SPV_ENV_VULKAN_1_3);spvtools::Optimizer optimizer(SPV_ENV_VULKAN_1_3);optimizer.RegisterPerformancePasses();
 if(validator.Validate(std::vector<uint32_t>{0,0,0,0,0}))return 1;
 std::puts("{\"isolatedUpstreamToolsPublicAPI\":true}");return 0;}
