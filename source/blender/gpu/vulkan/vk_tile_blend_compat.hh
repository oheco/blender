/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstdlib>
#include <cstring>
#include <vector>
#include <vulkan/vulkan_core.h>

namespace blender::gpu {

/* This is an explicit experimental implementation gate, NOT a native feature.
 * Default runs continue to require dualSrcBlend. Pixel tests currently cover
 * Maleoon 935 B312 + single-sample RGBA8/RGBA16F/RGBA32F separate mul/add,
 * not a whole Blender session. The explicit-fma probe has a recorded failure.
 */
inline bool vk_tile_blend_experiment_requested()
{
#ifdef __OHOS__
  const char *value = std::getenv("BLENDER_VK_TILE_BLEND_EXPERIMENT");
  return value != nullptr && std::strcmp(value, "1") == 0;
#else
  return false;
#endif
}

inline bool vk_tile_blend_device_in_verified_cohort(const VkPhysicalDeviceProperties &properties)
{
  return vk_tile_blend_experiment_requested() &&
         properties.apiVersion >= VK_API_VERSION_1_3 && properties.vendorID == 0x19e5 &&
         properties.deviceID == 0x20021000 && properties.driverVersion == 0x4fbccf1f;
}

inline bool vk_tile_blend_format_is_verified(VkFormat format)
{
  /* RGBA8: results-pixels. RGBA16F/RGBA32F: results-float-pixels,
   * 19 cases per format separateCpuRawExact + separateOrdinaryBlendRawExact.
   * FP32's explicit-fma branch failed its fused CPU model; do not use fma.
   * No inference to SRGB, packed float, SNORM, depth/discard, MSAA or MRT. */
  return format == VK_FORMAT_R8G8B8A8_UNORM ||
         format == VK_FORMAT_R16G16B16A16_SFLOAT ||
         format == VK_FORMAT_R32G32B32A32_SFLOAT;
}

inline bool vk_tile_blend_physical_device_candidate(VkPhysicalDevice physical_device)
{
  VkPhysicalDeviceProperties properties;
  vkGetPhysicalDeviceProperties(physical_device, &properties);
  if (!vk_tile_blend_device_in_verified_cohort(properties)) {
    return false;
  }
  uint32_t count = 0;
  if (vkEnumerateDeviceExtensionProperties(physical_device, nullptr, &count, nullptr) !=
      VK_SUCCESS)
  {
    return false;
  }
  std::vector<VkExtensionProperties> extensions(count);
  if (vkEnumerateDeviceExtensionProperties(
          physical_device, nullptr, &count, extensions.data()) != VK_SUCCESS)
  {
    return false;
  }
  bool has_tile_image = false;
  for (const VkExtensionProperties &extension : extensions) {
    if (std::strcmp(extension.extensionName, VK_EXT_SHADER_TILE_IMAGE_EXTENSION_NAME) == 0) {
      has_tile_image = true;
      break;
    }
  }
  if (!has_tile_image) {
    return false;
  }
  VkPhysicalDeviceShaderTileImageFeaturesEXT tile = {};
  tile.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_TILE_IMAGE_FEATURES_EXT;
  VkPhysicalDeviceDynamicRenderingFeatures dynamic_rendering = {};
  dynamic_rendering.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES;
  dynamic_rendering.pNext = &tile;
  VkPhysicalDeviceFeatures2 features = {};
  features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
  features.pNext = &dynamic_rendering;
  vkGetPhysicalDeviceFeatures2(physical_device, &features);
  return tile.shaderTileImageColorReadAccess == VK_TRUE &&
         dynamic_rendering.dynamicRendering == VK_TRUE;
}

}  // namespace blender::gpu
