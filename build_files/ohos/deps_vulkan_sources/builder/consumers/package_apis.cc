// SPDX-License-Identifier: GPL-2.0-or-later
// Public header/API imports only; no VMA device allocation or GPU work.
#include <vk_mem_alloc.h>
#include <vulkan/vulkan.h>
#include <cstdio>
#include <type_traits>
#include <vector>
#ifndef VK_USE_PLATFORM_OHOS
#error "CMake application must select OHOS; the PC entry must propagate this selector"
#endif
static_assert(VK_HEADER_VERSION==341,"complete fixed original Vulkan headers required");
static_assert(sizeof(VkSurfaceCreateInfoOHOS)==32 && alignof(VkSurfaceCreateInfoOHOS)==8,"real OHOS surface layout");
using SurfacePFN = VkResult(VKAPI_PTR *)(VkInstance,const VkSurfaceCreateInfoOHOS*,const VkAllocationCallbacks*,VkSurfaceKHR*);
static_assert(std::is_same_v<PFN_vkCreateSurfaceOHOS,SurfacePFN> && std::is_same_v<decltype(&vkCreateSurfaceOHOS),SurfacePFN>,"real surface PFN/declaration ABI");
int main(){
  VmaAllocationCreateInfo allocation{};VmaAllocatorCreateInfo allocator{};
  if(allocation.flags || allocator.flags)return 4;
  std::puts("{\"VMAHeaderPackage\":true,\"OHOSHeaderPFNLayout\":true,\"GPUDeviceAllocation\":false,\"WSI\":false}");
  return 0;
}
