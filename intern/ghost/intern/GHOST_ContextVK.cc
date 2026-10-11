/* SPDX-FileCopyrightText: 2022-2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 */

#include "GHOST_ContextVK.hh"
#include "GHOST_Types.hh"
#include <vulkan/vulkan_core.h>
#ifdef WITH_GHOST_OHOS
#  include <exception>
#  include <vulkan/vulkan_ohos.h>
#endif

#ifdef _WIN32
#  include <vulkan/vulkan_win32.h>
#elif defined(__APPLE__)
#  include <vulkan/vulkan_metal.h>
#else /* X11/WAYLAND. */
#  ifdef WITH_GHOST_X11
#    include <vulkan/vulkan_xlib.h>
#  endif
#  ifdef WITH_GHOST_WAYLAND
#    include <vulkan/vulkan_wayland.h>
#  endif
#endif

#include "vulkan/vk_ghost_api.hh"
#include "vulkan/vk_tile_blend_compat.hh"

#if !defined(_WIN32) or defined(_M_ARM64)
/* Silence compilation warning on non-windows x64 systems. */
#  define VMA_EXTERNAL_MEMORY_WIN32 0
#endif
#include "vk_mem_alloc.h"

#include "CLG_log.h"

#include "BLI_string_ref.hh"
#include "BLI_vector.hh"

#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <vector>

#include <sys/stat.h>

using namespace std;

static CLG_LogRef LOG = {"ghost.context"};

#define __STR(A) "" #A
#define VK_CHECK(__expression, fail_value) \
  do { \
    VkResult r = (__expression); \
    if (r != VK_SUCCESS) { \
      CLOG_ERROR(&LOG, \
                 "Vulkan: %s resulted in code %s.", \
                 __STR(__expression), \
                 blender::gpu::to_string(r)); \
      return fail_value; \
    } \
  } while (0)

/* -------------------------------------------------------------------- */
/** \name Swap-chain resources
 * \{ */

void GHOST_SwapchainImage::destroy(VkDevice vk_device)
{
  vkDestroySemaphore(vk_device, present_semaphore, nullptr);
  present_semaphore = VK_NULL_HANDLE;
  vk_image = VK_NULL_HANDLE;
}

void GHOST_FrameDiscard::destroy(VkDevice vk_device)
{
  while (!swapchains.empty()) {
    VkSwapchainKHR vk_swapchain = swapchains.back();
    swapchains.pop_back();
    vkDestroySwapchainKHR(vk_device, vk_swapchain, nullptr);
  }
  while (!semaphores.empty()) {
    VkSemaphore vk_semaphore = semaphores.back();
    semaphores.pop_back();
    vkDestroySemaphore(vk_device, vk_semaphore, nullptr);
  }
#ifdef WITH_GHOST_OHOS
  ohos_image_bytes = 0;
#endif
}

void GHOST_Frame::destroy(VkDevice vk_device)
{
  vkDestroyFence(vk_device, submission_fence, nullptr);
  submission_fence = VK_NULL_HANDLE;
  vkDestroySemaphore(vk_device, acquire_semaphore, nullptr);
  acquire_semaphore = VK_NULL_HANDLE;
#ifdef WITH_GHOST_OHOS
  vkDestroyFence(vk_device, ohos_acquisition_fence, nullptr);
  ohos_acquisition_fence = VK_NULL_HANDLE;
  ohos_acquisition_pending = ohos_submission_pending = ohos_retirement_witness = false;
#endif
  discard_pile.destroy(vk_device);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Extension list
 * \{ */

struct GHOST_ExtensionsVK {
  blender::Vector<VkExtensionProperties> extensions;
  blender::Vector<const char *> enabled;

  bool is_supported(const char *extension_name) const
  {
    for (const VkExtensionProperties &extension : extensions) {
      if (STREQ(extension.extensionName, extension_name)) {
        return true;
      }
    }
    return false;
  }

  bool is_supported(blender::Span<const char *> extension_names)
  {
    for (const char *extension_name : extension_names) {
      if (!is_supported(extension_name)) {
        return false;
      }
    }
    return true;
  }

  bool enable(const char *extension_name, bool optional = false)
  {
    bool supported = is_supported(extension_name);
    if (supported) {
      CLOG_TRACE(&LOG,
                 "Vulkan: %s extension enabled: name=%s",
                 optional ? "optional" : "required",
                 extension_name);
      enabled.append(extension_name);
      return true;
    }

    CLOG_AT_LEVEL(&LOG,
                  (optional ? CLG_LEVEL_TRACE : CLG_LEVEL_ERROR),
                  "Vulkan: %s extension not available: name=%s",
                  optional ? "optional" : "required",
                  extension_name);

    return false;
  }

  bool disable(const char *extension_name)
  {
    bool is_extension_enabled = is_enabled(extension_name);
    if (is_extension_enabled) {
      CLOG_TRACE(
          &LOG, "Vulkan: extension disabled for compatibility reasons: name=%s", extension_name);
      enabled.remove(enabled.first_index_of(extension_name));
      return true;
    }

    return false;
  }

  bool enable(const blender::Span<const char *> &extension_names, bool optional = false)
  {
    bool failure = false;
    for (const char *extension_name : extension_names) {
      failure |= !enable(extension_name, optional);
    }
    return !failure;
  }

  bool is_enabled(const char *extension_name) const
  {
    for (const char *enabled_extension_name : enabled) {
      if (STREQ(enabled_extension_name, extension_name)) {
        return true;
      }
    }
    return false;
  }
};

/** \} */

/* -------------------------------------------------------------------- */
/** \name Vulkan Device
 * \{ */

class GHOST_DeviceVK {
 public:
  VkPhysicalDevice vk_physical_device = VK_NULL_HANDLE;
  GHOST_ExtensionsVK extensions;

  VkDevice vk_device = VK_NULL_HANDLE;

  uint32_t generic_queue_family = 0;
  VkQueue generic_queue = VK_NULL_HANDLE;
  VmaAllocator vma_allocator = VK_NULL_HANDLE;

  VkPhysicalDeviceProperties2 properties = {
      VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
  };
  VkPhysicalDeviceVulkan12Properties properties_12 = {
      VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES,
  };
  VkPhysicalDeviceFeatures2 features = {};
  VkPhysicalDeviceVulkan11Features features_11 = {};
  VkPhysicalDeviceVulkan12Features features_12 = {};
  VkPhysicalDeviceRobustness2FeaturesEXT features_robustness2 = {
      VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT};

  int users = 0;

  /** Mutex to externally synchronize access to queue. */
  std::mutex queue_mutex;

  bool use_vk_ext_swapchain_maintenance_1 = false;
  bool use_vk_ext_swapchain_colorspace = false;
  bool shader_tile_image_color_read_enabled = false;

 public:
  GHOST_DeviceVK(VkPhysicalDevice vk_physical_device, const bool use_vk_ext_swapchain_colorspace)
      : vk_physical_device(vk_physical_device),
        use_vk_ext_swapchain_colorspace(use_vk_ext_swapchain_colorspace)
  {
    properties.pNext = &properties_12;
    vkGetPhysicalDeviceProperties2(vk_physical_device, &properties);

    features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    features_11.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES;
    features_12.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
    features.pNext = &features_11;
    features_11.pNext = &features_12;
    features_12.pNext = &features_robustness2;

    vkGetPhysicalDeviceFeatures2(vk_physical_device, &features);
    init_extensions();
  }

  ~GHOST_DeviceVK()
  {
    if (vma_allocator != VK_NULL_HANDLE) {
      vmaDestroyAllocator(vma_allocator);
      vma_allocator = VK_NULL_HANDLE;
    }
    if (vk_device != VK_NULL_HANDLE) {
      vkDestroyDevice(vk_device, nullptr);
      vk_device = VK_NULL_HANDLE;
    }
  }

  bool init_extensions()
  {
    uint32_t extensions_count;
    VK_CHECK(vkEnumerateDeviceExtensionProperties(
                 vk_physical_device, nullptr, &extensions_count, nullptr),
             false);
    extensions.extensions.resize(extensions_count);
    VK_CHECK(vkEnumerateDeviceExtensionProperties(
                 vk_physical_device, nullptr, &extensions_count, extensions.extensions.data()),
             false);
    return true;
  }

  void wait_idle()
  {
    if (vk_device) {
      std::scoped_lock lock(queue_mutex);
      vkDeviceWaitIdle(vk_device);
    }
  }

  void init_generic_queue_family()
  {
    uint32_t queue_family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(vk_physical_device, &queue_family_count, nullptr);

    vector<VkQueueFamilyProperties> queue_families(queue_family_count);
    vkGetPhysicalDeviceQueueFamilyProperties(
        vk_physical_device, &queue_family_count, queue_families.data());

    generic_queue_family = 0;
    for (const VkQueueFamilyProperties &queue_family : queue_families) {
      /* Every VULKAN implementation by spec must have one queue family that support both graphics
       * and compute pipelines. We select this one; compute only queue family hints at asynchronous
       * compute implementations. */
      if ((queue_family.queueFlags & VK_QUEUE_GRAPHICS_BIT) &&
          (queue_family.queueFlags & VK_QUEUE_COMPUTE_BIT))
      {
        return;
      }
      generic_queue_family++;
    }
  }

  void init_generic_queue()
  {
    vkGetDeviceQueue(vk_device, generic_queue_family, 0, &generic_queue);
  }

  void init_memory_allocator(VkInstance vk_instance)
  {
    VmaAllocatorCreateInfo vma_allocator_create_info = {};
    vma_allocator_create_info.vulkanApiVersion = shader_tile_image_color_read_enabled ?
                                                   VK_API_VERSION_1_3 :
                                                   VK_API_VERSION_1_2;
    vma_allocator_create_info.physicalDevice = vk_physical_device;
    vma_allocator_create_info.device = vk_device;
    vma_allocator_create_info.instance = vk_instance;
    vma_allocator_create_info.flags = VMA_ALLOCATOR_CREATE_BUFFER_DEVICE_ADDRESS_BIT;
    if (extensions.is_enabled(VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME)) {
      vma_allocator_create_info.flags |= VMA_ALLOCATOR_CREATE_EXT_MEMORY_PRIORITY_BIT;
    }
    if (extensions.is_enabled(VK_KHR_MAINTENANCE_4_EXTENSION_NAME)) {
      vma_allocator_create_info.flags |= VMA_ALLOCATOR_CREATE_KHR_MAINTENANCE4_BIT;
    }
    vmaCreateAllocator(&vma_allocator_create_info, &vma_allocator);
  }
};

/** \} */

/* -------------------------------------------------------------------- */
/** \name Vulkan Instance
 * \{ */

struct GHOST_InstanceVK {
  VkInstance vk_instance = VK_NULL_HANDLE;
  VkPhysicalDevice vk_physical_device = VK_NULL_HANDLE;
  uint32_t api_version = VK_API_VERSION_1_2;

  GHOST_ExtensionsVK extensions;

  std::optional<GHOST_DeviceVK> device;

  GHOST_InstanceVK()
  {
    init_extensions();
  }

  ~GHOST_InstanceVK()
  {
    device.reset();
    vkDestroyInstance(vk_instance, nullptr);
    vk_physical_device = VK_NULL_HANDLE;
    vk_instance = VK_NULL_HANDLE;
  }

  bool init_extensions()
  {
    uint32_t extension_count = 0;
    VK_CHECK(vkEnumerateInstanceExtensionProperties(nullptr, &extension_count, nullptr), false);
    extensions.extensions.resize(extension_count);
    VK_CHECK(vkEnumerateInstanceExtensionProperties(
                 nullptr, &extension_count, extensions.extensions.data()),
             false);
    return true;
  }

  bool create_instance(uint32_t vulkan_api_version)
  {
    if (blender::gpu::vk_tile_blend_experiment_requested()) {
      uint32_t loader_version = 0;
      if (vkEnumerateInstanceVersion(&loader_version) != VK_SUCCESS ||
          loader_version < VK_API_VERSION_1_3)
      {
        CLOG_ERROR(&LOG, "Experimental shader tile blend requires a Vulkan 1.3 loader");
        return false;
      }
      vulkan_api_version = std::max(vulkan_api_version, uint32_t(VK_API_VERSION_1_3));
    }
    api_version = vulkan_api_version;
    VkApplicationInfo vk_application_info = {VK_STRUCTURE_TYPE_APPLICATION_INFO,
                                             nullptr,
                                             "Blender",
                                             VK_MAKE_VERSION(1, 0, 0),
                                             "Blender",
                                             VK_MAKE_VERSION(1, 0, 0),
                                             vulkan_api_version};
    VkInstanceCreateInfo vk_instance_create_info = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                                    nullptr,
                                                    0,
                                                    &vk_application_info,
                                                    0,
                                                    nullptr,
                                                    uint32_t(extensions.enabled.size()),
                                                    extensions.enabled.data()

    };

    VK_CHECK(vkCreateInstance(&vk_instance_create_info, nullptr, &vk_instance), false);
    return true;
  }

  bool select_physical_device(const GHOST_GPUDevice &preferred_device,
                              const blender::Span<const char *> required_extensions)
  {
    VkPhysicalDevice requested_physical_device = VK_NULL_HANDLE;
    VkPhysicalDevice fallback_physical_device = VK_NULL_HANDLE;

    uint32_t device_count = 0;
    vkEnumeratePhysicalDevices(vk_instance, &device_count, nullptr);

    vector<VkPhysicalDevice> physical_devices(device_count);
    vkEnumeratePhysicalDevices(vk_instance, &device_count, physical_devices.data());

    int best_device_score = -1;
    /* Index of the device in the full physical-device enumeration. Matches the trailing
     * `/index` component of `GPUDevice::identifier` (see `init_device_list` in `vk_backend.cc`).
     */
    int device_index = -1;
    for (const VkPhysicalDevice &physical_device : physical_devices) {
      GHOST_DeviceVK device_vk(physical_device, false);
      device_index++;

      if (!device_vk.extensions.is_supported(required_extensions)) {
        continue;
      }
      if (!blender::gpu::GPU_vulkan_is_supported_driver(physical_device)) {
        continue;
      }

      if (
#ifndef __APPLE__
          !device_vk.features.features.geometryShader ||
#endif
          !device_vk.features.features.shaderClipDistance ||
          !device_vk.features.features.fragmentStoresAndAtomics ||
          !device_vk.features.features.multiDrawIndirect ||
          !device_vk.features.features.imageCubeArray ||
          (!device_vk.features.features.dualSrcBlend &&
           !(api_version >= VK_API_VERSION_1_3 &&
             blender::gpu::vk_tile_blend_physical_device_candidate(physical_device))) ||
          !device_vk.features.features.imageCubeArray)
      {
        continue;
      }

      const VkPhysicalDeviceProperties &vk_props = device_vk.properties.properties;
      if (preferred_device.is_override) {
        /* Requested device: vendor/device IDs of `uint(-1)` act as wildcards for by-index form. */
        const bool match = preferred_device.index == device_index &&
                           (preferred_device.vendor_id == uint(-1) ||
                            preferred_device.vendor_id == vk_props.vendorID) &&
                           (preferred_device.device_id == uint(-1) ||
                            preferred_device.device_id == vk_props.deviceID);
        if (match) {
          requested_physical_device = physical_device;
          break;
        }
      }

      int device_score = 0;
      switch (device_vk.properties.properties.deviceType) {
        case VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU:
          device_score = 400;
          break;
        case VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU:
          device_score = 300;
          break;
        case VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU:
          device_score = 200;
          break;
        case VK_PHYSICAL_DEVICE_TYPE_CPU:
          device_score = 100;
          break;
        default:
          break;
      }

      /* User has configured a preferred device. Add bonus score when vendor and device match.
       * Driver id isn't considered as drivers update more frequently and can break the device
       * selection. */
      if (vk_props.deviceID == preferred_device.fallback_device_id &&
          vk_props.vendorID == preferred_device.fallback_vendor_id)
      {
        device_score += 500;
        if (preferred_device.fallback_index == device_index) {
          device_score += 10;
        }
      }
      if (device_score > best_device_score) {
        fallback_physical_device = physical_device;
        best_device_score = device_score;
      }
    }

    if (requested_physical_device != VK_NULL_HANDLE) {
      vk_physical_device = requested_physical_device;
      return GHOST_kSuccess;
    }

    if (fallback_physical_device == VK_NULL_HANDLE) {
      CLOG_ERROR(&LOG, "No suitable Vulkan Device found!");
      return GHOST_kFailure;
    }

    if (preferred_device.is_override) {
      if (preferred_device.fail_on_invalid_override) {
        if (preferred_device.vendor_id == uint(-1)) {
          CLOG_ERROR(&LOG,
                     "Requested Vulkan GPU '%d' is unavailable or unsupported. "
                     "Run with '--gpu-device help' to list available devices.",
                     preferred_device.index);
        }
        else {
          CLOG_ERROR(&LOG,
                     "Requested Vulkan GPU '%x/%x/%x' is unavailable or unsupported. "
                     "Run with '--gpu-device help' to list available devices.",
                     preferred_device.vendor_id,
                     preferred_device.device_id,
                     uint(preferred_device.index));
        }
        return GHOST_kFailure;
      }
      if (preferred_device.vendor_id == uint(-1)) {
        CLOG_WARN(&LOG,
                  "Requested Vulkan GPU '%d' is unavailable or unsupported. "
                  "Falling back to the saved GPU preference.",
                  preferred_device.index);
      }
      else {
        CLOG_WARN(&LOG,
                  "Requested Vulkan GPU '%x/%x/%x' is unavailable or unsupported. "
                  "Falling back to the saved GPU preference.",
                  preferred_device.vendor_id,
                  preferred_device.device_id,
                  uint(preferred_device.index));
      }
    }

    vk_physical_device = fallback_physical_device;

    return GHOST_kSuccess;
  }

  bool create_device(const bool use_vk_ext_swapchain_maintenance1,
                     const bool is_debug,
                     blender::Span<const char *> required_device_extensions,
                     blender::Span<const char *> optional_device_extensions)
  {
    device.emplace(vk_physical_device, use_vk_ext_swapchain_maintenance1);
    GHOST_DeviceVK &device = *this->device;

    device.extensions.enable(required_device_extensions);
    device.extensions.enable(optional_device_extensions, true);

    const bool use_tile_blend = !device.features.features.dualSrcBlend &&
                                api_version >= VK_API_VERSION_1_3 &&
                                blender::gpu::vk_tile_blend_physical_device_candidate(
                                    vk_physical_device);
    if (use_tile_blend) {
      if (!device.extensions.enable(VK_EXT_SHADER_TILE_IMAGE_EXTENSION_NAME)) {
        return false;
      }
      /* The shaders library does not carry the target color format. Keep the
       * experimental path in full pipelines until GPL tile inputs are tested. */
      device.extensions.disable(VK_EXT_GRAPHICS_PIPELINE_LIBRARY_EXTENSION_NAME);
      device.extensions.disable(VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME);
      CLOG_WARN(&LOG,
                "Experimental coherent tile CUSTOM blending enabled; only recorded "
                "single-sample attachment formats are permitted, not native dualSrcBlend");
    }

    /* Disabling pipeline libraries and dynamic vertex input on AMD drivers due to random crashes
     * that are also happening when enabling the extension, but not using it at all. This needs
     * more investigation as it could be related to development workflows.
     *
     * This seems to affect the pro drivers more than the `Adrenalin` ones.
     * But as both share the same code-base it is better to disable them until
     * it is clear what causes the crashes and when these were fixed.
     *
     * Ref #151103
     */
    const bool is_amd_driver = device.properties_12.driverID == VK_DRIVER_ID_AMD_PROPRIETARY ||
                               device.properties_12.driverID == VK_DRIVER_ID_AMD_OPEN_SOURCE;
    if (is_amd_driver && is_debug) {
      device.extensions.disable(VK_KHR_PIPELINE_LIBRARY_EXTENSION_NAME);
      device.extensions.disable(VK_EXT_GRAPHICS_PIPELINE_LIBRARY_EXTENSION_NAME);
      device.extensions.disable(VK_EXT_VERTEX_INPUT_DYNAMIC_STATE_EXTENSION_NAME);
    }

#ifdef _WIN32
    /* Intel 7th to 10th Gen Processor iGPUs show a black screen at application startup when using
     * VK_EXT_vertex_input_dynamic_state. The used driver version for these iGPUs is 101.2xxx or
     * older.
     *
     * Ref: #147721
     */
    if (device.properties_12.driverID == VK_DRIVER_ID_INTEL_PROPRIETARY_WINDOWS &&
        device.properties.properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU)
    {
      const uint32_t driver_version = device.properties.properties.driverVersion;
      uint32_t driver_version_major = driver_version >> 14u;
      uint32_t driver_version_minor = driver_version & 0x3fffu;
      if (driver_version_major < 101 ||
          (driver_version_major == 101 && driver_version_minor < 3000))
      {
        device.extensions.disable(VK_EXT_VERTEX_INPUT_DYNAMIC_STATE_EXTENSION_NAME);
      }
    }
#endif

    device.init_generic_queue_family();

    float queue_priorities[] = {1.0f};
    vector<VkDeviceQueueCreateInfo> queue_create_infos;
    VkDeviceQueueCreateInfo graphic_queue_create_info = {};
    graphic_queue_create_info.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
    graphic_queue_create_info.queueFamilyIndex = device.generic_queue_family;
    graphic_queue_create_info.queueCount = 1;
    graphic_queue_create_info.pQueuePriorities = queue_priorities;
    queue_create_infos.push_back(graphic_queue_create_info);

    VkPhysicalDeviceFeatures device_features = {};
#ifndef __APPLE__
    device_features.geometryShader = VK_TRUE;
#endif
    device_features.vertexPipelineStoresAndAtomics =
        device.features.features.vertexPipelineStoresAndAtomics;
    device_features.multiViewport = device.features.features.multiViewport;
    device_features.shaderClipDistance = VK_TRUE;
    device_features.fragmentStoresAndAtomics = VK_TRUE;
    device_features.logicOp = device.features.features.logicOp;
    device_features.dualSrcBlend = device.features.features.dualSrcBlend;
    device_features.imageCubeArray = VK_TRUE;
    device_features.multiDrawIndirect = VK_TRUE;
    device_features.drawIndirectFirstInstance = VK_TRUE;
    device_features.samplerAnisotropy = device.features.features.samplerAnisotropy;
    device_features.wideLines = device.features.features.wideLines;

    VkDeviceCreateInfo device_create_info = {};
    device_create_info.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
    device_create_info.queueCreateInfoCount = uint32_t(queue_create_infos.size());
    device_create_info.pQueueCreateInfos = queue_create_infos.data();
    device_create_info.enabledExtensionCount = uint32_t(device.extensions.enabled.size());
    device_create_info.ppEnabledExtensionNames = device.extensions.enabled.data();
    device_create_info.pEnabledFeatures = &device_features;

    std::vector<void *> feature_struct_ptr;

    /* Enable vulkan 11 features when supported on physical device. */
    VkPhysicalDeviceVulkan11Features vulkan_11_features = {};
    vulkan_11_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES;
    vulkan_11_features.shaderDrawParameters = VK_TRUE;
    feature_struct_ptr.push_back(&vulkan_11_features);

    /* Enable optional vulkan 12 features when supported on physical device. */
    VkPhysicalDeviceVulkan12Features vulkan_12_features = {};
    vulkan_12_features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
    vulkan_12_features.shaderOutputLayer = device.features_12.shaderOutputLayer;
    vulkan_12_features.shaderOutputViewportIndex = device.features_12.shaderOutputViewportIndex;
    vulkan_12_features.bufferDeviceAddress = device.features_12.bufferDeviceAddress;
    vulkan_12_features.timelineSemaphore = VK_TRUE;
    feature_struct_ptr.push_back(&vulkan_12_features);

#ifndef __APPLE__
    /* Enable provoking vertex. */
    VkPhysicalDeviceProvokingVertexFeaturesEXT provoking_vertex_features = {};
    provoking_vertex_features.sType =
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROVOKING_VERTEX_FEATURES_EXT;
    provoking_vertex_features.provokingVertexLast = VK_TRUE;
    feature_struct_ptr.push_back(&provoking_vertex_features);
#endif

    /* Enable dynamic rendering. */
    VkPhysicalDeviceDynamicRenderingFeatures dynamic_rendering = {};
    dynamic_rendering.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES;
    dynamic_rendering.dynamicRendering = VK_TRUE;
    feature_struct_ptr.push_back(&dynamic_rendering);

    VkPhysicalDeviceShaderTileImageFeaturesEXT tile_image = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_TILE_IMAGE_FEATURES_EXT};
    if (use_tile_blend) {
      /* Only COLOR was needed and tested. Never silently enable depth/stencil. */
      tile_image.shaderTileImageColorReadAccess = VK_TRUE;
      feature_struct_ptr.push_back(&tile_image);
    }

    VkPhysicalDeviceDynamicRenderingUnusedAttachmentsFeaturesEXT
        dynamic_rendering_unused_attachments = {};
    dynamic_rendering_unused_attachments.sType =
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_UNUSED_ATTACHMENTS_FEATURES_EXT;
    dynamic_rendering_unused_attachments.dynamicRenderingUnusedAttachments = VK_TRUE;
    if (device.extensions.is_enabled(VK_EXT_DYNAMIC_RENDERING_UNUSED_ATTACHMENTS_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&dynamic_rendering_unused_attachments);
    }

    VkPhysicalDeviceDynamicRenderingLocalReadFeaturesKHR dynamic_rendering_local_read = {};
    dynamic_rendering_local_read.sType =
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_LOCAL_READ_FEATURES_KHR;
    dynamic_rendering_local_read.dynamicRenderingLocalRead = VK_TRUE;
    if (device.extensions.is_enabled(VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&dynamic_rendering_local_read);
    }

    /* VK_EXT_robustness2 */
    VkPhysicalDeviceRobustness2FeaturesEXT robustness_2_features = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT};
    if (device.extensions.is_enabled(VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) {
      robustness_2_features.nullDescriptor = device.features_robustness2.nullDescriptor;
      feature_struct_ptr.push_back(&robustness_2_features);
    }

    /* Query for Mainenance4 (core in Vulkan 1.3). */
    VkPhysicalDeviceMaintenance4FeaturesKHR maintenance_4 = {};
    maintenance_4.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_4_FEATURES_KHR;
    maintenance_4.maintenance4 = VK_TRUE;
    if (device.extensions.is_enabled(VK_KHR_MAINTENANCE_4_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&maintenance_4);
    }

    /* Swap-chain maintenance 1 is optional. */
    VkPhysicalDeviceSwapchainMaintenance1FeaturesEXT swapchain_maintenance_1 = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SWAPCHAIN_MAINTENANCE_1_FEATURES_EXT, nullptr, VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_SWAPCHAIN_MAINTENANCE_1_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&swapchain_maintenance_1);
      device.use_vk_ext_swapchain_maintenance_1 = true;
    }

    /* Query and enable Fragment Shader Barycentrics. */
    VkPhysicalDeviceFragmentShaderBarycentricFeaturesKHR fragment_shader_barycentric = {};
    fragment_shader_barycentric.sType =
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FRAGMENT_SHADER_BARYCENTRIC_FEATURES_KHR;
    fragment_shader_barycentric.fragmentShaderBarycentric = VK_TRUE;
    if (device.extensions.is_enabled(VK_KHR_FRAGMENT_SHADER_BARYCENTRIC_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&fragment_shader_barycentric);
    }

    /* VK_EXT_memory_priority */
    VkPhysicalDeviceMemoryPriorityFeaturesEXT memory_priority = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MEMORY_PRIORITY_FEATURES_EXT, nullptr, VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&memory_priority);
    }

    /* VK_EXT_pageable_device_local_memory */
    VkPhysicalDevicePageableDeviceLocalMemoryFeaturesEXT pageable_device_local_memory = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PAGEABLE_DEVICE_LOCAL_MEMORY_FEATURES_EXT,
        nullptr,
        VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&pageable_device_local_memory);
    }

    /* VK_EXT_graphics_pipeline_library */
    VkPhysicalDeviceGraphicsPipelineLibraryFeaturesEXT graphics_pipeline_library = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GRAPHICS_PIPELINE_LIBRARY_FEATURES_EXT,
        nullptr,
        VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_GRAPHICS_PIPELINE_LIBRARY_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&graphics_pipeline_library);
    }

    /* VK_EXT_line_rasterization */
    VkPhysicalDeviceLineRasterizationFeaturesKHR line_rasterization_features = {};
    line_rasterization_features.sType =
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_LINE_RASTERIZATION_FEATURES_EXT;
    line_rasterization_features.bresenhamLines = VK_TRUE;
    if (device.extensions.is_enabled(VK_EXT_LINE_RASTERIZATION_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&line_rasterization_features);
    }

    /* VK_EXT_extended_dynamic_state */
    VkPhysicalDeviceExtendedDynamicStateFeaturesEXT extended_dynamic_state = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTENDED_DYNAMIC_STATE_FEATURES_EXT, nullptr, VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&extended_dynamic_state);
    }

    /* VK_EXT_vertex_input_dynamic_state */
    VkPhysicalDeviceVertexInputDynamicStateFeaturesEXT vertex_input_dynamic_state = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VERTEX_INPUT_DYNAMIC_STATE_FEATURES_EXT,
        nullptr,
        VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_VERTEX_INPUT_DYNAMIC_STATE_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&vertex_input_dynamic_state);
    }

    /* VK_EXT_host_image_copy */
    VkPhysicalDeviceHostImageCopyFeaturesEXT host_image_copy = {
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES_EXT, nullptr, VK_TRUE};
    if (device.extensions.is_enabled(VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME)) {
      feature_struct_ptr.push_back(&host_image_copy);
    }

    /* Link all registered feature structs. */
    for (int i = 1; i < feature_struct_ptr.size(); i++) {
      ((VkBaseInStructure *)(feature_struct_ptr[i - 1]))->pNext =
          (VkBaseInStructure *)(feature_struct_ptr[i]);
    }

    device_create_info.pNext = feature_struct_ptr[0];
    VK_CHECK(vkCreateDevice(vk_physical_device, &device_create_info, nullptr, &device.vk_device),
             GHOST_kFailure);
    device.shader_tile_image_color_read_enabled = use_tile_blend;
    device.init_generic_queue();
    device.init_memory_allocator(vk_instance);
    return true;
  }
};

/** \} */

/* -------------------------------------------------------------------- */
/** \name Vulkan Instance Extensions Query
 * \{ */

/**
 * A shared device between multiple contexts.
 *
 * The logical device needs to be shared as multiple contexts can be created and the logical
 * vulkan device they share should be the same otherwise memory operations might be done on the
 * incorrect device.
 */
static std::optional<GHOST_InstanceVK> vulkan_instance;

bool GHOST_ContextVK::is_instance_extension_enabled(blender::StringRefNull extension_name)
{
  if (!vulkan_instance.has_value()) {
    return false;
  }
  return vulkan_instance->extensions.is_enabled(extension_name.c_str());
}

bool GHOST_ContextVK::is_device_extension_enabled(blender::StringRefNull extension_name)
{
  if (!vulkan_instance.has_value()) {
    return false;
  }
  if (!vulkan_instance->device.has_value()) {
    return false;
  }
  return vulkan_instance->device->extensions.is_enabled(extension_name.c_str());
}

/** \} */

GHOST_ContextVK::GHOST_ContextVK(const GHOST_ContextParams &context_params,
#ifdef _WIN32
                                 HWND hwnd,
#elif defined(__APPLE__)
                                 void *metal_layer,
#else
                                 GHOST_TVulkanPlatformType platform,
                                 /* X11 */
                                 Window window,
                                 Display *display,
                                 /* Wayland */
                                 wl_surface *wayland_surface,
                                 wl_display *wayland_display,
                                 const GHOST_ContextVK_WindowInfo *wayland_window_info,
#endif
                                 int contextMajorVersion,
                                 int contextMinorVersion,
                                 const GHOST_GPUDevice &preferred_device,
                                 const GHOST_WindowHDRInfo *hdr_info
#ifdef WITH_GHOST_OHOS
                                 , void *ohos_native_window,
                                 const GHOST_ContextVK_WindowInfo *ohos_window_info
#endif
                                 )
    : GHOST_Context(context_params),
#ifdef _WIN32
      hwnd_(hwnd),
#elif defined(__APPLE__)
      metal_layer_(metal_layer),
#else
      platform_(platform),
      /* X11 */
      display_(display),
      window_(window),
      /* Wayland */
      wayland_surface_(wayland_surface),
      wayland_display_(wayland_display),
      wayland_window_info_(wayland_window_info),
#endif
#ifdef WITH_GHOST_OHOS
      ohos_native_window_(ohos_native_window),
      ohos_window_info_(ohos_window_info),
#endif
      context_major_version_(contextMajorVersion),
      context_minor_version_(contextMinorVersion),
      preferred_device_(preferred_device),
      hdr_info_(hdr_info),
      surface_(VK_NULL_HANDLE),
      swapchain_(VK_NULL_HANDLE),
      frame_data_(2),
      render_frame_(0),
      use_hdr_swapchain_(false)
{
  frame_data_.reserve(5);
}

GHOST_ContextVK::~GHOST_ContextVK()
{
  if (vulkan_instance.has_value()) {
    GHOST_InstanceVK &instance_vk = vulkan_instance.value();
    if (!instance_vk.device.has_value() || instance_vk.device->vk_device == VK_NULL_HANDLE) {
      if (surface_ != VK_NULL_HANDLE) {
        vkDestroySurfaceKHR(instance_vk.vk_instance, surface_, nullptr);
        surface_ = VK_NULL_HANDLE;
      }
      vulkan_instance.reset();
      return;
    }

    GHOST_DeviceVK &device_vk = instance_vk.device.value();
    device_vk.wait_idle();
    for (VkFence fence : fence_pile_) {
      vkDestroyFence(device_vk.vk_device, fence, nullptr);
    }
    fence_pile_.clear();
    destroySwapchain();

    if (surface_ != VK_NULL_HANDLE) {
      vkDestroySurfaceKHR(instance_vk.vk_instance, surface_, nullptr);
      surface_ = VK_NULL_HANDLE;
    }

    if (device_vk.users > 0
#ifdef WITH_GHOST_OHOS
        && ohos_device_user_acquired_
#endif
        ) {
      device_vk.users--;
    }
    if (device_vk.users == 0) {
      vulkan_instance.reset();
    }
  }
}

#ifdef WITH_GHOST_OHOS
static uint64_t ohos_monotonic_ms()
{
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

bool GHOST_ContextVK::needsOHOSPresentProgress() const
{
  if (platform_ != GHOST_kVulkanPlatformOHOS || !ohos_native_window_ || ohos_terminal_failure_) {
    return false;
  }
  if (ohos_recreate_pending_ || ohos_settlement_pending_ ||
      !ohos_pending_retirement_.swapchains.empty())
  {
    return true;
  }
  if (ohos_window_info_ && (uint32_t(ohos_window_info_->size[0]) != ohos_requested_extent_.width ||
                            uint32_t(ohos_window_info_->size[1]) != ohos_requested_extent_.height))
  {
    return true;
  }
  for (const GHOST_Frame &frame : frame_data_) {
    if (frame.ohos_retirement_witness) {
      return true;
    }
  }
  return false;
}

bool GHOST_ContextVK::collectOHOSRetiredSwapchains()
{
  const VkDevice device = vulkan_instance->device->vk_device;
  /* Check EVERY slot before any acquisition fence is reset, including slots skipped after timeout.
   * Khronos swapchain_recreation: reacquiring the successor's first presented image and completing
   * that acquisition proves ancestor presentations finished. An empty submit/queue idle does not.
   * If a successor is retired before this happens, its ancestors follow the next successor. */
  for (GHOST_Frame &frame : frame_data_) {
    if (!frame.ohos_retirement_witness) {
      continue;
    }
    const VkResult status = vkGetFenceStatus(device, frame.ohos_acquisition_fence);
    if (status != VK_SUCCESS && status != VK_NOT_READY) {
      ohos_terminal_failure_ = true;
      return false;
    }
    if (GHOST_OHOSSwapchainRetirement::witness_complete(
            frame.ohos_acquisition_pending, frame.ohos_retirement_witness, status == VK_SUCCESS))
    {
      frame.discard_pile.destroy(device);
      frame.ohos_retirement_witness = false;
    }
  }
  return true;
}

GHOST_TSuccess GHOST_ContextVK::swapBufferAcquireOHOS()
{
  if (!ohos_native_window_ || !ohos_window_info_ || ohos_terminal_failure_ ||
      ohos_window_info_->size[0] <= 0 || ohos_window_info_->size[1] <= 0 ||
      acquired_swapchain_image_index_.has_value() || frame_data_.empty())
  {
    return GHOST_kFailure;
  }
  ohos_recreations_this_tick_ = 0;
  const uint64_t now = ohos_monotonic_ms();
  const VkExtent2D latest = {uint32_t(ohos_window_info_->size[0]),
                             uint32_t(ohos_window_info_->size[1])};
  if (latest.width != ohos_latest_extent_.width || latest.height != ohos_latest_extent_.height) {
    ohos_latest_extent_ = latest;
    ohos_host_changed_ms_ = now;
    ohos_settlement_pending_ = true;
  }
  if (!collectOHOSRetiredSwapchains()) {
    return GHOST_kFailure;
  }
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(16);
  const auto remaining_ns = [&]() -> uint64_t {
    const auto remaining = std::chrono::duration_cast<std::chrono::nanoseconds>(
                               deadline - std::chrono::steady_clock::now())
                               .count();
    return remaining > 0 ? uint64_t(remaining) : 0;
  };
  const VkDevice device = vulkan_instance->device->vk_device;
  render_frame_ = (render_frame_ + 1) % frame_data_.size();
  GHOST_Frame &frame = frame_data_[render_frame_];
  if (frame.ohos_submission_pending) {
    const VkResult result = vkWaitForFences(
        device, 1, &frame.submission_fence, VK_TRUE, remaining_ns());
    if (result != VK_SUCCESS) {
      if (result != VK_TIMEOUT)
        ohos_terminal_failure_ = true;
      return GHOST_kFailure;
    }
    /* This fence belongs to the successful GPU submission that consumed THIS slot's semaphore.
     * It permits semaphore reuse, but is deliberately not ancestor presentation proof. */
    frame.ohos_submission_pending = false;
  }
  if (frame.ohos_acquisition_pending) {
    const VkResult result = vkWaitForFences(
        device, 1, &frame.ohos_acquisition_fence, VK_TRUE, remaining_ns());
    if (result != VK_SUCCESS) {
      if (result != VK_TIMEOUT)
        ohos_terminal_failure_ = true;
      return GHOST_kFailure;
    }
    if (!collectOHOSRetiredSwapchains()) {
      return GHOST_kFailure;
    }
    assert(!frame.ohos_retirement_witness);
    frame.ohos_acquisition_pending = false;
  }
  const bool use_hdr_swapchain = hdr_info_ &&
                                 (hdr_info_->wide_gamut_enabled || hdr_info_->hdr_enabled) &&
                                 vulkan_instance->device->use_vk_ext_swapchain_colorspace;
  if (use_hdr_swapchain != use_hdr_swapchain_)
    ohos_settlement_pending_ = true;
  const bool settled = GHOST_OHOSSwapchainRetirement::host_settled(now, ohos_host_changed_ms_);
  const bool resolution_update = ohos_settlement_pending_ && settled &&
                                 GHOST_OHOSSwapchainRetirement::host_settled(
                                     now, ohos_last_settlement_ms_);
  if (!swapchain_ || ohos_recreate_pending_ || use_hdr_swapchain != use_hdr_swapchain_ ||
      resolution_update)
  {
    const bool forced = !swapchain_ || ohos_recreate_pending_;
    if (recreateSwapchainOHOS(use_hdr_swapchain, forced) != GHOST_kSuccess &&
        (!swapchain_ || ohos_recreate_pending_))
    {
      /* Backpressure leaves all ancestors and the last displayed frame intact. A valid current
       * chain can still acquire/present and complete the witness despite a pending host request.
       */
      return GHOST_kFailure;
    }
  }
  if (!swapchain_ || initializeFrameData() != GHOST_kSuccess) {
    return GHOST_kFailure;
  }
  for (uint32_t attempt = 0; attempt < 2; attempt++) {
    if (!collectOHOSRetiredSwapchains() ||
        vkResetFences(device, 1, &frame.ohos_acquisition_fence) != VK_SUCCESS)
    {
      ohos_terminal_failure_ = true;
      return GHOST_kFailure;
    }
    uint32_t image = 0;
    const VkResult result = vkAcquireNextImageKHR(device,
                                                  swapchain_,
                                                  remaining_ns(),
                                                  frame.acquire_semaphore,
                                                  frame.ohos_acquisition_fence,
                                                  &image);
    if (ELEM(result, VK_SUCCESS, VK_SUBOPTIMAL_KHR)) {
      frame.ohos_acquisition_pending = true;
      ohos_current_chain_.acquired_image();
      if (ohos_current_chain_.witnesses_ancestors(image) &&
          !ohos_pending_retirement_.swapchains.empty())
      {
        assert(frame.discard_pile.swapchains.empty() && frame.discard_pile.semaphores.empty());
        std::swap(frame.discard_pile, ohos_pending_retirement_);
        frame.ohos_retirement_witness = true;
      }
      if (result == VK_SUBOPTIMAL_KHR && ohos_current_chain_.note_suboptimal()) {
        /* Probe once per generation. An unchanged/clamped extent may stay SUBOPTIMAL; do not
         * keep an otherwise idle window presenting forever after the request was handled. */
        ohos_settlement_pending_ = true;
      }
      acquired_swapchain_image_index_ = image;
      if (swap_buffer_acquired_callback_)
        swap_buffer_acquired_callback_();
      return GHOST_kSuccess;
    }
    /* On failure neither synchronization object was signaled and no image was acquired. */
    if (result == VK_ERROR_OUT_OF_DATE_KHR) {
      ohos_recreate_pending_ = true;
      if (attempt == 0 && ohos_recreations_this_tick_ == 0 &&
          recreateSwapchainOHOS(use_hdr_swapchain, true) == GHOST_kSuccess)
      {
        continue;
      }
    }
    else if (!ELEM(result, VK_TIMEOUT, VK_NOT_READY)) {
      CLOG_ERROR(&LOG, "OHOS Vulkan acquisition failed: %s", blender::gpu::to_string(result));
      ohos_terminal_failure_ = true;
      ohos_surface_lost_ = result == VK_ERROR_SURFACE_LOST_KHR;
    }
    return GHOST_kFailure;
  }
  return GHOST_kFailure;
}
#endif

GHOST_TSuccess GHOST_ContextVK::swapBufferAcquire()
{
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    return swapBufferAcquireOHOS();
  }
#endif
  if (acquired_swapchain_image_index_.has_value()) {
    assert(false);
    return GHOST_kFailure;
  }

  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  VkDevice vk_device = device_vk.vk_device;

  /* WM acquires before drawing. Reuse a frame only after its previous GPU submission completed;
   * release passes this frame's fence to the GPU submission callback. An acquisition failure
   * must leave that fence signaled and must not expose an image index to release. */
  render_frame_ = (render_frame_ + 1) % frame_data_.size();
  GHOST_Frame &submission_frame_data = frame_data_[render_frame_];
  /* Wait for previous time that the frame was used to finish rendering. Presenting can
   * still happen in parallel, but acquiring needs can only happen when the frame acquire semaphore
   * has been signaled and waited for. */
  uint64_t acquire_timeout = UINT64_MAX;
  uint32_t acquire_attempt_limit = UINT32_MAX;
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    acquire_timeout =
        16'000'000; /* Return to the host event pump instead of blocking on resize. */
    acquire_attempt_limit = 2;
  }
#endif
  if (submission_frame_data.submission_fence) {
    const VkResult wait_result = vkWaitForFences(
        vk_device, 1, &submission_frame_data.submission_fence, true, acquire_timeout);
    if (wait_result != VK_SUCCESS) {
      return GHOST_kFailure;
    }
  }
  for (VkSwapchainKHR swapchain : submission_frame_data.discard_pile.swapchains) {
    this->destroySwapchainPresentFences(swapchain);
  }
  submission_frame_data.discard_pile.destroy(vk_device);

  const bool use_hdr_swapchain = hdr_info_ &&
                                 (hdr_info_->wide_gamut_enabled || hdr_info_->hdr_enabled) &&
                                 device_vk.use_vk_ext_swapchain_colorspace;
  if (use_hdr_swapchain != use_hdr_swapchain_) {
    /* Re-create swapchain if HDR mode was toggled in the system settings. */
    if (recreateSwapchain(use_hdr_swapchain) != GHOST_kSuccess) {
      return GHOST_kFailure;
    }
  }
  else {
#ifdef WITH_GHOST_WAYLAND
    /* Wayland doesn't provide a WSI with windowing capabilities, therefore cannot detect whether
     * the swap-chain needs to be recreated. But as a side effect we can recreate the swap-chain
     * before presenting. */
    if (wayland_window_info_) {
      const bool recreate_swapchain =
          ((wayland_window_info_->size[0] !=
            std::max(render_extent_.width, render_extent_min_.width)) ||
           (wayland_window_info_->size[1] !=
            std::max(render_extent_.height, render_extent_min_.height)));

      if (recreate_swapchain) {
        /* Swap-chain is out of date. Recreate swap-chain. */
        recreateSwapchain(use_hdr_swapchain);
      }
    }
#endif
#ifdef WITH_GHOST_OHOS
    if (platform_ == GHOST_kVulkanPlatformOHOS && ohos_window_info_) {
      /* OHOS may impose a fixed currentExtent while its surface size catches
       * up with ArkUI. Compare against the request handled by the last successful
       * creation, rather than rebuilding every frame when WSI chose another size.
       * Genuine invalidation is still handled by acquire/present OUT_OF_DATE. */
      const uint32_t width = uint32_t(ohos_window_info_->size[0]);
      const uint32_t height = uint32_t(ohos_window_info_->size[1]);
      if (ohos_recreate_pending_ || width != ohos_requested_extent_.width ||
          height != ohos_requested_extent_.height)
      {
        if (recreateSwapchain(use_hdr_swapchain, ohos_recreate_pending_) != GHOST_kSuccess) {
          return GHOST_kFailure;
        }
      }
    }
#endif
  }
  /* there is no valid swapchain when the previous window was minimized. User can have maximized
   * the window so we need to check if the swapchain has to be created. */
  if (swapchain_ == VK_NULL_HANDLE && recreateSwapchain(use_hdr_swapchain) != GHOST_kSuccess) {
    return GHOST_kFailure;
  }

  if (initializeFrameData() != GHOST_kSuccess) {
    return GHOST_kFailure;
  }
  /* Acquire next image; only successful results own an image and signaled semaphore. */
  uint32_t image_index = 0;
  if (swapchain_ != VK_NULL_HANDLE) {
    /* Some platforms (NVIDIA/Wayland) can receive an out of date swapchain when acquiring the next
     * swapchain image. Other do it when calling vkQueuePresent. */
    VkResult acquire_result = VK_ERROR_OUT_OF_DATE_KHR;
    const auto must_recreate = [&](VkResult result) {
#ifdef WITH_GHOST_OHOS
      /* SUBOPTIMAL acquired a usable image and signaled the binary semaphore.
       * Reacquiring with that semaphore is invalid. Host resize requests already
       * cause recreation; use this image until the next frame or OUT_OF_DATE. */
      if (platform_ == GHOST_kVulkanPlatformOHOS) {
        return result == VK_ERROR_OUT_OF_DATE_KHR;
      }
#endif
      return ELEM(result, VK_ERROR_OUT_OF_DATE_KHR, VK_SUBOPTIMAL_KHR);
    };
    for (uint32_t attempt = 0; attempt < acquire_attempt_limit; attempt++) {
      acquire_result = vkAcquireNextImageKHR(vk_device,
                                             swapchain_,
                                             acquire_timeout,
                                             frame_data_[render_frame_].acquire_semaphore,
                                             VK_NULL_HANDLE,
                                             &image_index);
      if (!must_recreate(acquire_result)) {
        break;
      }
      if (attempt + 1 == acquire_attempt_limit) {
#ifdef WITH_GHOST_OHOS
        if (platform_ == GHOST_kVulkanPlatformOHOS) {
          ohos_recreate_pending_ = true;
        }
#endif
        return GHOST_kFailure;
      }
      if (recreateSwapchain(use_hdr_swapchain) != GHOST_kSuccess || swapchain_ == VK_NULL_HANDLE) {
        return GHOST_kFailure;
      }
    }
    if (!ELEM(acquire_result, VK_SUCCESS, VK_SUBOPTIMAL_KHR)) {
      if (!ELEM(acquire_result, VK_TIMEOUT, VK_NOT_READY)) {
        CLOG_ERROR(
            &LOG, "Vulkan: image acquisition failed: %s", blender::gpu::to_string(acquire_result));
      }
      return GHOST_kFailure;
    }
  }

  /* Acquired callback is also called when there is no swapchain.
   *
   * When acquiring swap chain (image) and the swap chain is discarded (window has been minimized).
   * We have trigger a last acquired callback to reduce the attachments of the GPUFramebuffer.
   * Vulkan backend will retrieve the data (getVulkanSwapChainFormat) containing a render extent of
   * 0,0.
   *
   * The next frame window manager will detect that the window is minimized and doesn't draw the
   * window at all.
   */
  if (swap_buffer_acquired_callback_) {
    swap_buffer_acquired_callback_();
  }

  if (swapchain_ == VK_NULL_HANDLE) {
    CLOG_TRACE(&LOG, "Swap-chain unavailable (minimized window).");
    return GHOST_kSuccess;
  }

  CLOG_DEBUG(&LOG,
             "Acquired swap-chain image (render_frame=%" PRIu64 ", image_index=%u)",
             render_frame_,
             image_index);
  acquired_swapchain_image_index_ = image_index;

  return GHOST_kSuccess;
}
VkFence GHOST_ContextVK::getFence()
{
  if (!fence_pile_.empty()) {
    VkFence fence = fence_pile_.back();
    fence_pile_.pop_back();
    return fence;
  }
  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  VkFence fence = VK_NULL_HANDLE;
  const VkFenceCreateInfo fence_create_info = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  vkCreateFence(device_vk.vk_device, &fence_create_info, nullptr, &fence);
  return fence;
}

void GHOST_ContextVK::setPresentFence(VkSwapchainKHR swapchain, VkFence present_fence)
{
  if (present_fence == VK_NULL_HANDLE) {
    return;
  }
  present_fences_[swapchain].push_back(present_fence);
  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  /** Recycle signaled fences. */
  for (std::pair<const VkSwapchainKHR, std::vector<VkFence>> &item : present_fences_) {
    std::vector<VkFence>::iterator end = item.second.end();
    std::vector<VkFence>::iterator it = std::remove_if(
        item.second.begin(), item.second.end(), [&](const VkFence fence) {
          if (vkGetFenceStatus(device_vk.vk_device, fence) == VK_NOT_READY) {
            return false;
          }
          vkResetFences(device_vk.vk_device, 1, &fence);
          fence_pile_.push_back(fence);
          return true;
        });
    item.second.erase(it, end);
  }
}

GHOST_TSuccess GHOST_ContextVK::swapBufferRelease()
{
  /* Minimized windows don't have a swapchain and swapchain image. In this case we perform the draw
   * to release render graph and discarded resources. */
  if (swapchain_ == VK_NULL_HANDLE) {
    GHOST_VulkanSwapChainData swap_chain_data = {};
    if (swap_buffer_draw_callback_) {
      swap_buffer_draw_callback_(&swap_chain_data, true);
    }
    return GHOST_kSuccess;
  }

  if (!acquired_swapchain_image_index_.has_value()) {
    assert(false);
    return GHOST_kFailure;
  }
  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  VkDevice vk_device = device_vk.vk_device;

  uint32_t image_index = acquired_swapchain_image_index_.value();
  GHOST_SwapchainImage &swapchain_image = swapchain_images_[image_index];
  GHOST_Frame &submission_frame_data = frame_data_[render_frame_];
  const bool use_hdr_swapchain = hdr_info_ &&
                                 (hdr_info_->wide_gamut_enabled || hdr_info_->hdr_enabled) &&
                                 device_vk.use_vk_ext_swapchain_colorspace;

  GHOST_VulkanSwapChainData swap_chain_data;
  swap_chain_data.image = swapchain_image.vk_image;
  swap_chain_data.surface_format = surface_format_;
  swap_chain_data.extent = render_extent_;
  swap_chain_data.submission_fence = submission_frame_data.submission_fence;
  swap_chain_data.acquire_semaphore = submission_frame_data.acquire_semaphore;
  swap_chain_data.present_semaphore = swapchain_image.present_semaphore;
  swap_chain_data.sdr_scale = (hdr_info_) ? hdr_info_->sdr_white_level : 1.0f;

#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    if (ohos_terminal_failure_ || !swap_buffer_draw_callback_ ||
        vkResetFences(vk_device, 1, &submission_frame_data.submission_fence) != VK_SUCCESS)
    {
      ohos_terminal_failure_ = true;
      return GHOST_kFailure;
    }
    /* GPU callback must report submission failure to its caller; a reset but never-submitted fence
     * cannot be treated as completed. That separate GPU error propagation is a prerequisite. */
    submission_frame_data.ohos_submission_pending = true;
  }
  else
#endif
  {
    vkResetFences(vk_device, 1, &submission_frame_data.submission_fence);
  }
  if (swap_buffer_draw_callback_) {
#ifdef WITH_GHOST_OHOS
    if (platform_ == GHOST_kVulkanPlatformOHOS) {
      try {
        swap_buffer_draw_callback_(&swap_chain_data, true);
      }
      catch (const std::exception &error) {
        /* The acquired image/semaphore remain owned for teardown. Never queue a present waiting
         * on a semaphore when its producing batch was rejected by vkQueueSubmit. */
        ohos_terminal_failure_ = true;
        CLOG_ERROR(&LOG, "OHOS Vulkan submission failed before presentation: %s", error.what());
        return GHOST_kFailure;
      }
      catch (...) {
        ohos_terminal_failure_ = true;
        CLOG_ERROR(&LOG, "OHOS Vulkan submission failed before presentation");
        return GHOST_kFailure;
      }
    }
    else
#endif
    {
      swap_buffer_draw_callback_(&swap_chain_data, true);
    }
  }

  VkPresentInfoKHR present_info = {};
  present_info.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;
  present_info.waitSemaphoreCount = 1;
  present_info.pWaitSemaphores = &swapchain_image.present_semaphore;
  present_info.swapchainCount = 1;
  present_info.pSwapchains = &swapchain_;
  present_info.pImageIndices = &image_index;
  present_info.pResults = nullptr;

  VkResult present_result = VK_SUCCESS;
  {
    std::scoped_lock lock(device_vk.queue_mutex);
    VkSwapchainPresentFenceInfoEXT fence_info{VK_STRUCTURE_TYPE_SWAPCHAIN_PRESENT_FENCE_INFO_EXT};
    VkFence present_fence = VK_NULL_HANDLE;
    if (device_vk.use_vk_ext_swapchain_maintenance_1
#ifdef WITH_GHOST_OHOS
        && platform_ != GHOST_kVulkanPlatformOHOS
#endif
    )
    {
      present_fence = this->getFence();

      fence_info.swapchainCount = 1;
      fence_info.pFences = &present_fence;

      present_info.pNext = &fence_info;
    }
    present_result = vkQueuePresentKHR(device_vk.generic_queue, &present_info);
    this->setPresentFence(swapchain_, present_fence);
  }
  acquired_swapchain_image_index_.reset();

#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    if (ELEM(present_result, VK_SUCCESS, VK_SUBOPTIMAL_KHR)) {
      /* Only an actually queued successful presentation selects the successor witness image. */
      ohos_current_chain_.queued_present(image_index);
      if (present_result == VK_SUBOPTIMAL_KHR && ohos_current_chain_.note_suboptimal()) {
        ohos_settlement_pending_ = true;
      }
      return GHOST_kSuccess;
    }
    /* Error returns may leave ownership/consumption ambiguous. Retain this used generation and
     * its present semaphores until a later successor witness; do not acquire/reuse it again. */
    ohos_recreate_pending_ = true;
    if (present_result == VK_ERROR_DEVICE_LOST || present_result == VK_ERROR_SURFACE_LOST_KHR) {
      ohos_terminal_failure_ = true;
      ohos_surface_lost_ = present_result == VK_ERROR_SURFACE_LOST_KHR;
    }
    if (present_result != VK_ERROR_OUT_OF_DATE_KHR) {
      CLOG_ERROR(
          &LOG, "OHOS Vulkan presentation failed: %s", blender::gpu::to_string(present_result));
    }
    return GHOST_kFailure;
  }
#endif
  if (ELEM(present_result, VK_ERROR_OUT_OF_DATE_KHR, VK_SUBOPTIMAL_KHR)) {
    recreateSwapchain(use_hdr_swapchain);
    return GHOST_kSuccess;
  }
  if (present_result != VK_SUCCESS) {
    CLOG_ERROR(&LOG,
               "Vulkan: failed to present swap-chain image : %s",
               blender::gpu::to_string(present_result));
    return GHOST_kFailure;
  }

  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::getVulkanSwapChainFormat(
    GHOST_VulkanSwapChainData *r_swap_chain_data)
{
  r_swap_chain_data->image = VK_NULL_HANDLE;
  r_swap_chain_data->surface_format = surface_format_;
  r_swap_chain_data->extent = render_extent_;
  r_swap_chain_data->sdr_scale = (hdr_info_) ? hdr_info_->sdr_white_level : 1.0f;

  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::getVulkanHandles(GHOST_VulkanHandles &r_handles)
{
  r_handles = {
      VK_NULL_HANDLE, /* instance */
      VK_NULL_HANDLE, /* physical_device */
      VK_NULL_HANDLE, /* device */
      0,              /* queue_family */
      VK_NULL_HANDLE, /* queue */
      nullptr,        /* queue_mutex */
      VK_NULL_HANDLE, /* vma_allocator */
  };

  if (vulkan_instance.has_value() && vulkan_instance.value().device.has_value()) {
    GHOST_InstanceVK &instance_vk = vulkan_instance.value();
    GHOST_DeviceVK &device_vk = instance_vk.device.value();
    r_handles = {
        instance_vk.vk_instance,
        device_vk.vk_physical_device,
        device_vk.vk_device,
        device_vk.generic_queue_family,
        device_vk.generic_queue,
        &device_vk.queue_mutex,
        device_vk.vma_allocator,
        device_vk.shader_tile_image_color_read_enabled,
    };
  }

  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::setVulkanSwapBuffersCallbacks(
    std::function<void(const GHOST_VulkanSwapChainData *, bool)> swap_buffer_draw_callback,
    std::function<void(void)> swap_buffer_acquired_callback,
    std::function<void(GHOST_VulkanOpenXRData *)> openxr_acquire_framebuffer_image_callback,
    std::function<void(GHOST_VulkanOpenXRData *)> openxr_release_framebuffer_image_callback)
{
  swap_buffer_draw_callback_ = swap_buffer_draw_callback;
  swap_buffer_acquired_callback_ = swap_buffer_acquired_callback;
  openxr_acquire_framebuffer_image_callback_ = openxr_acquire_framebuffer_image_callback;
  openxr_release_framebuffer_image_callback_ = openxr_release_framebuffer_image_callback;
  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::activateDrawingContext()
{
  active_context_ = this;
  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::releaseDrawingContext()
{
  active_context_ = nullptr;
  return GHOST_kSuccess;
}

static const char *to_string_vk_format(VkFormat format)
{
  switch (format) {
    case VK_FORMAT_R8G8B8A8_UNORM:
      return STRINGIFY(VK_FORMAT_R8G8B8A8_UNORM);
    case VK_FORMAT_B8G8R8A8_UNORM:
      return STRINGIFY(VK_FORMAT_B8G8R8A8_UNORM);
    case VK_FORMAT_R16G16B16A16_SFLOAT:
      return STRINGIFY(VK_FORMAT_R16G16B16A16_SFLOAT);
    default:
      return STRINGIFY_ARG(format);
  }
}

static const char *to_string_vk_color_space(VkColorSpaceKHR color_space)
{
  switch (color_space) {
    case VK_COLOR_SPACE_SRGB_NONLINEAR_KHR:
      return STRINGIFY(VK_COLOR_SPACE_SRGB_NONLINEAR_KHR);
    case VK_COLOR_SPACE_EXTENDED_SRGB_LINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_EXTENDED_SRGB_LINEAR_EXT);
    case VK_COLOR_SPACE_PASS_THROUGH_EXT:
      return STRINGIFY(VK_COLOR_SPACE_PASS_THROUGH_EXT);
    case VK_COLOR_SPACE_DISPLAY_P3_NONLINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_DISPLAY_P3_NONLINEAR_EXT);
    case VK_COLOR_SPACE_DISPLAY_P3_LINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_DISPLAY_P3_LINEAR_EXT);
    case VK_COLOR_SPACE_BT709_LINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_BT709_LINEAR_EXT);
    case VK_COLOR_SPACE_BT709_NONLINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_BT709_NONLINEAR_EXT);
    case VK_COLOR_SPACE_BT2020_LINEAR_EXT:
      return STRINGIFY(VK_COLOR_SPACE_BT2020_LINEAR_EXT);
    case VK_COLOR_SPACE_HDR10_ST2084_EXT:
      return STRINGIFY(VK_COLOR_SPACE_HDR10_ST2084_EXT);
    case VK_COLOR_SPACE_DISPLAY_NATIVE_AMD:
      return STRINGIFY(VK_COLOR_SPACE_DISPLAY_NATIVE_AMD);
    default:
      return STRINGIFY_ARG(color_space);
  }
}

static const char *to_string_vk_present_mode(VkPresentModeKHR present_mode)
{
  switch (present_mode) {
    case VK_PRESENT_MODE_FIFO_KHR:
      return STRINGIFY(VK_PRESENT_MODE_FIFO_KHR);
    case VK_PRESENT_MODE_MAILBOX_KHR:
      return STRINGIFY(VK_PRESENT_MODE_MAILBOX_KHR);
    case VK_PRESENT_MODE_IMMEDIATE_KHR:
      return STRINGIFY(VK_PRESENT_MODE_IMMEDIATE_KHR);
    case VK_PRESENT_MODE_FIFO_RELAXED_KHR:
      return STRINGIFY(VK_PRESENT_MODE_FIFO_RELAXED_KHR);
    default:
      return STRINGIFY_ARG(present_mode);
  }
}

static GHOST_TSuccess selectPresentMode(const GHOST_TVSyncModes vsync,
                                        VkPhysicalDevice device,
                                        VkSurfaceKHR surface,
                                        VkPresentModeKHR *r_presentMode)
{
  uint32_t present_count;
  vkGetPhysicalDeviceSurfacePresentModesKHR(device, surface, &present_count, nullptr);
  vector<VkPresentModeKHR> presents(present_count);
  vkGetPhysicalDeviceSurfacePresentModesKHR(device, surface, &present_count, presents.data());

  if (vsync != GHOST_kVSyncModeUnset) {
    const bool vsync_off = (vsync == GHOST_kVSyncModeOff);
    if (vsync_off) {
      for (const VkPresentModeKHR present_mode : presents) {
        if (present_mode == VK_PRESENT_MODE_IMMEDIATE_KHR) {
          *r_presentMode = present_mode;
          return GHOST_kSuccess;
        }
      }
      CLOG_WARN(&LOG,
                "Vulkan: VSync off was requested via --gpu-vsync, "
                "but VK_PRESENT_MODE_IMMEDIATE_KHR is not supported.");
    }
  }

  /* MAILBOX is the lowest latency V-Sync enabled mode. We will use it if available as it fixes
   * some lag on NVIDIA/Intel GPUs. */
  /* TODO: select the correct presentation mode based on the actual being performed by the user.
   * When low latency is required (paint cursor) we should select mailbox, otherwise we can do FIFO
   * to reduce CPU/GPU usage. */
  for (const VkPresentModeKHR present_mode : presents) {
    if (present_mode == VK_PRESENT_MODE_MAILBOX_KHR) {
      *r_presentMode = present_mode;
      return GHOST_kSuccess;
    }
  }

  /* FIFO present mode is always available and we (should) prefer it as it will keep the main loop
   * running along the monitor refresh rate. Mailbox and FIFO relaxed can generate a lot of frames
   * that will never be displayed. */
  *r_presentMode = VK_PRESENT_MODE_FIFO_KHR;
  return GHOST_kSuccess;
}

/**
 * Select the surface format that we will use.
 *
 * We will select any 8bit UNORM surface.
 */
static bool selectSurfaceFormat(const VkPhysicalDevice physical_device,
                                const VkSurfaceKHR surface,
                                bool use_hdr_swapchain,
                                VkSurfaceFormatKHR &r_surfaceFormat)
{
  uint32_t format_count;
  vkGetPhysicalDeviceSurfaceFormatsKHR(physical_device, surface, &format_count, nullptr);
  vector<VkSurfaceFormatKHR> formats(format_count);
  vkGetPhysicalDeviceSurfaceFormatsKHR(physical_device, surface, &format_count, formats.data());

  array<VkSurfaceFormatKHR, 3> selection_order = {{
#if defined(_WIN32) || defined(__APPLE__)
      {VK_FORMAT_R16G16B16A16_SFLOAT, VK_COLOR_SPACE_EXTENDED_SRGB_LINEAR_EXT},
#else
      {VK_FORMAT_R16G16B16A16_SFLOAT, VK_COLOR_SPACE_PASS_THROUGH_EXT},
#endif
      {VK_FORMAT_R8G8B8A8_UNORM, VK_COLOR_SPACE_SRGB_NONLINEAR_KHR},
      {VK_FORMAT_B8G8R8A8_UNORM, VK_COLOR_SPACE_SRGB_NONLINEAR_KHR}}};

  for (const VkSurfaceFormatKHR &config : selection_order) {
    if (!use_hdr_swapchain && config.format == VK_FORMAT_R16G16B16A16_SFLOAT) {
      continue;
    }

    for (const VkSurfaceFormatKHR &format : formats) {
      if (format.format == config.format && format.colorSpace == config.colorSpace) {
        r_surfaceFormat = format;
        return true;
      }
    }
  }

  return false;
}

GHOST_TSuccess GHOST_ContextVK::initializeFrameData()
{
  VkDevice device = vulkan_instance.value().device.value().vk_device;

  const VkSemaphoreCreateInfo vk_semaphore_create_info = {
      VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, nullptr, 0};
  const VkFenceCreateInfo vk_fence_create_info = {
      VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, nullptr, VK_FENCE_CREATE_SIGNALED_BIT};
  for (GHOST_SwapchainImage &swapchain_image : swapchain_images_) {
    /* VK_EXT_swapchain_maintenance1 reuses present semaphores. */
    if (swapchain_image.present_semaphore == VK_NULL_HANDLE) {
      VK_CHECK(vkCreateSemaphore(
                   device, &vk_semaphore_create_info, nullptr, &swapchain_image.present_semaphore),
               GHOST_kFailure);
    }
  }

  for (int index = 0; index < frame_data_.size(); index++) {
    GHOST_Frame &frame_data = frame_data_[index];
    /* VK_EXT_swapchain_maintenance1 reuses acquire semaphores. */
    if (frame_data.acquire_semaphore == VK_NULL_HANDLE) {
      VK_CHECK(vkCreateSemaphore(
                   device, &vk_semaphore_create_info, nullptr, &frame_data.acquire_semaphore),
               GHOST_kFailure);
    }
    if (frame_data.submission_fence == VK_NULL_HANDLE) {
      VK_CHECK(vkCreateFence(device, &vk_fence_create_info, nullptr, &frame_data.submission_fence),
               GHOST_kFailure);
    }
#ifdef WITH_GHOST_OHOS
    if (platform_ == GHOST_kVulkanPlatformOHOS &&
        frame_data.ohos_acquisition_fence == VK_NULL_HANDLE)
    {
      const VkFenceCreateInfo acquisition_info = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
      VK_CHECK(
          vkCreateFence(device, &acquisition_info, nullptr, &frame_data.ohos_acquisition_fence),
          GHOST_kFailure);
    }
#endif
  }

  return GHOST_kSuccess;
}

#ifdef WITH_GHOST_OHOS
GHOST_TSuccess GHOST_ContextVK::recreateSwapchainOHOS(bool use_hdr_swapchain, bool force)
{
  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  const VkDevice device = device_vk.vk_device;
  VkSurfaceFormatKHR format = {};
  VkPresentModeKHR mode;
  VkSurfaceCapabilitiesKHR capabilities = {};
  if (!selectSurfaceFormat(device_vk.vk_physical_device, surface_, use_hdr_swapchain, format) ||
      !selectPresentMode(getVSync(), device_vk.vk_physical_device, surface_, &mode))
  {
    return GHOST_kFailure;
  }
  VK_CHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(
               device_vk.vk_physical_device, surface_, &capabilities),
           GHOST_kFailure);
  VkExtent2D extent = capabilities.currentExtent;
  if (extent.width == UINT32_MAX) {
    extent = {std::clamp(uint32_t(ohos_window_info_->size[0]),
                         capabilities.minImageExtent.width,
                         capabilities.maxImageExtent.width),
              std::clamp(uint32_t(ohos_window_info_->size[1]),
                         capabilities.minImageExtent.height,
                         capabilities.maxImageExtent.height)};
  }
  const uint64_t now = ohos_monotonic_ms();
  const VkExtent2D latest = {uint32_t(ohos_window_info_->size[0]),
                             uint32_t(ohos_window_info_->size[1])};
  if (latest.width != ohos_latest_extent_.width || latest.height != ohos_latest_extent_.height) {
    ohos_latest_extent_ = latest;
    ohos_host_changed_ms_ = now;
    ohos_settlement_pending_ = true;
  }
  if (!force && swapchain_ && extent.width == render_extent_.width &&
      extent.height == render_extent_.height && format.format == surface_format_.format &&
      format.colorSpace == surface_format_.colorSpace && mode == ohos_present_mode_ &&
      capabilities.currentTransform == ohos_surface_transform_ &&
      use_hdr_swapchain == use_hdr_swapchain_)
  {
    ohos_requested_extent_ = {uint32_t(ohos_window_info_->size[0]),
                              uint32_t(ohos_window_info_->size[1])};
    ohos_settlement_pending_ = false;
    ohos_last_settlement_ms_ = now;
    return GHOST_kSuccess;
  }
  if (!force && !ohos_pending_retirement_.swapchains.empty()) {
    /* Prefer presenting/reacquiring this valid successor until it witnesses the ancestors. A
     * newer host size must not starve the first-present image's completion opportunity. */
    return GHOST_kFailure;
  }
  /* Do not retire a usable chain before resource admission, and do not turn a zero-extent tick
   * into another retired generation. Forced invalidation stays pending until recovery is possible.
   */
  if (extent.width == 0 || extent.height == 0 || ohos_recreations_this_tick_ != 0 ||
      capabilities.minImageCount > GHOST_OHOSSwapchainRetirement::max_images)
  {
    return GHOST_kFailure;
  }
  uint32_t requested_images = std::max(capabilities.minImageCount,
                                       mode == VK_PRESENT_MODE_MAILBOX_KHR ? 3u : 2u);
  if (capabilities.maxImageCount)
    requested_images = std::min(requested_images, capabilities.maxImageCount);
  const uint32_t admission_images = capabilities.maxImageCount ?
                                        std::min(capabilities.maxImageCount,
                                                 GHOST_OHOSSwapchainRetirement::max_images) :
                                        GHOST_OHOSSwapchainRetirement::max_images;
  const uint32_t texel_bytes = format.format == VK_FORMAT_R16G16B16A16_SFLOAT ? 8 : 4;
  const uint64_t candidate_bytes = GHOST_OHOSSwapchainRetirement::image_bytes(
      extent.width, extent.height, texel_bytes, admission_images);
  uint32_t resident_generations = uint32_t(ohos_pending_retirement_.swapchains.size()) +
                                  (swapchain_ != VK_NULL_HANDLE);
  uint64_t resident_bytes = ohos_pending_retirement_.ohos_image_bytes + ohos_current_image_bytes_;
  for (const GHOST_Frame &frame : frame_data_) {
    resident_generations += uint32_t(frame.discard_pile.swapchains.size());
    resident_bytes += frame.discard_pile.ohos_image_bytes;
  }
  /* An unused current intermediate needs no completion witness. Admit its replacement after
   * reclaiming that payload, then reclaim BEFORE allocating, so the hard cap cannot trap an
   * OUT_OF_DATE-before-first-acquire chain merely because it has no image to reacquire. */
  const bool replace_unused = swapchain_ && !ohos_current_chain_.acquired;
  if (replace_unused) {
    --resident_generations;
    resident_bytes -= ohos_current_image_bytes_;
  }
  const bool recovery = resident_generations == 0 ||
                        (force &&
                         GHOST_OHOSSwapchainRetirement::host_settled(now, ohos_host_changed_ms_));
  if (!GHOST_OHOSSwapchainRetirement::admit(
          resident_generations, resident_bytes, candidate_bytes, recovery))
  {
    /* Preserve dirty/latest size and let the valid current chain reach its first-present witness.
     * If every generation is invalidated before any witness, the hard limit intentionally stops
     * allocation; a recovery slot is reserved for a settled latest target, not endless churn. */
    return GHOST_kFailure;
  }
  /* Reserve host bookkeeping before passing oldSwapchain. After that call even a failed creation
   * retires the old chain; we can no longer back out to acquiring it. */
  ohos_pending_retirement_.swapchains.reserve(ohos_pending_retirement_.swapchains.size() + 1);
  ohos_pending_retirement_.semaphores.reserve(ohos_pending_retirement_.semaphores.size() +
                                              swapchain_images_.size());
  if (replace_unused) {
    for (GHOST_SwapchainImage &image : swapchain_images_)
      image.destroy(device);
    swapchain_images_.clear();
    vkDestroySwapchainKHR(device, swapchain_, nullptr);
    swapchain_ = VK_NULL_HANDLE;
    ohos_current_image_bytes_ = 0;
    ohos_current_chain_ = {};
  }
  VkSwapchainCreateInfoKHR info = {};
  info.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR;
  info.surface = surface_;
  info.minImageCount = requested_images;
  info.imageFormat = format.format;
  info.imageColorSpace = format.colorSpace;
  info.imageExtent = extent;
  info.imageArrayLayers = 1;
  info.imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                    (use_hdr_swapchain ? VK_IMAGE_USAGE_STORAGE_BIT : 0);
  info.preTransform = capabilities.currentTransform;
  info.compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
  info.presentMode = mode;
  info.clipped = VK_TRUE;
  info.oldSwapchain = swapchain_;
  info.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
  VkSwapchainKHR candidate = VK_NULL_HANDLE;
  ++ohos_recreations_this_tick_;
  const VkResult created = vkCreateSwapchainKHR(device, &info, nullptr, &candidate);
  const VkSwapchainKHR old = swapchain_;
  if (old && ohos_current_chain_.acquired) {
    /* Conservatively retain every used chain, including present OUT_OF_DATE/error cases and an
     * acquired chain whose GPU callback failed. No current/old render fence can retire it. */
    ohos_pending_retirement_.swapchains.push_back(old);
    ohos_pending_retirement_.ohos_image_bytes += ohos_current_image_bytes_;
    for (GHOST_SwapchainImage &image : swapchain_images_) {
      if (image.present_semaphore) {
        ohos_pending_retirement_.semaphores.push_back(image.present_semaphore);
        image.present_semaphore = VK_NULL_HANDLE;
      }
    }
  }
  else {
    /* A chain never acquired/submitted/presented has no GPU or presentation users. Passing it as
     * oldSwapchain retired it; free this intermediate now, without waiting for a successor. */
    for (GHOST_SwapchainImage &image : swapchain_images_)
      image.destroy(device);
    if (old)
      vkDestroySwapchainKHR(device, old, nullptr);
  }
  swapchain_images_.clear();
  swapchain_ = candidate;
  ohos_current_chain_ = {};
  ohos_current_image_bytes_ = 0;
  ohos_recreate_pending_ = true;
  if (created != VK_SUCCESS) {
    swapchain_ = VK_NULL_HANDLE;
    CLOG_ERROR(
        &LOG, "OHOS Vulkan swapchain creation failed: %s", blender::gpu::to_string(created));
    if (created == VK_ERROR_DEVICE_LOST || created == VK_ERROR_SURFACE_LOST_KHR) {
      ohos_terminal_failure_ = true;
      ohos_surface_lost_ = created == VK_ERROR_SURFACE_LOST_KHR;
    }
    return GHOST_kFailure;
  }
  const auto reject_unused_candidate = [&]() {
    vkDestroySwapchainKHR(device, swapchain_, nullptr);
    swapchain_ = VK_NULL_HANDLE;
    swapchain_images_.clear();
    return GHOST_kFailure;
  };
  uint32_t count = 0;
  if (vkGetSwapchainImagesKHR(device, swapchain_, &count, nullptr) != VK_SUCCESS || count == 0 ||
      count > admission_images)
  {
    /* Driver may return more images than requested. Never use a candidate outside the admitted
     * image bound; it has no acquisition/presentation users and can be immediately discarded. */
    return reject_unused_candidate();
  }
  std::vector<VkImage> images(count);
  if (vkGetSwapchainImagesKHR(device, swapchain_, &count, images.data()) != VK_SUCCESS ||
      count == 0 || count > images.size())
  {
    return reject_unused_candidate();
  }
  swapchain_images_.resize(count);
  for (uint32_t index = 0; index < count; ++index)
    swapchain_images_[index].vk_image = images[index];
  ohos_current_image_bytes_ = GHOST_OHOSSwapchainRetirement::image_bytes(
      extent.width, extent.height, texel_bytes, count);
  render_extent_ = extent;
  render_extent_min_ = capabilities.minImageExtent;
  surface_format_ = format;
  use_hdr_swapchain_ = use_hdr_swapchain;
  image_count_ = count;
  ohos_requested_extent_ = {uint32_t(ohos_window_info_->size[0]),
                            uint32_t(ohos_window_info_->size[1])};
  ohos_latest_extent_ = ohos_requested_extent_;
  ohos_extent_max_ = capabilities.maxImageExtent;
  ohos_present_mode_ = mode;
  ohos_surface_transform_ = capabilities.currentTransform;
  ohos_recreate_pending_ = ohos_settlement_pending_ = false;
  ohos_last_settlement_ms_ = now;
  /* Keep frame count and ALL per-frame acquire semaphores/fences across generations. Recreating
   * a chain does not end another slot's acquire-semaphore wait or successful submission. */
  return initializeFrameData();
}
#endif

GHOST_TSuccess GHOST_ContextVK::recreateSwapchain(bool use_hdr_swapchain, bool force)
{
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    return recreateSwapchainOHOS(use_hdr_swapchain, force);
  }
#endif
  GHOST_InstanceVK &instance_vk = vulkan_instance.value();
  GHOST_DeviceVK &device_vk = instance_vk.device.value();

  VkSurfaceFormatKHR new_surface_format = {};
  if (!selectSurfaceFormat(
          device_vk.vk_physical_device, surface_, use_hdr_swapchain, new_surface_format))
  {
    return GHOST_kFailure;
  }

  VkPresentModeKHR present_mode;
  if (!selectPresentMode(getVSync(), device_vk.vk_physical_device, surface_, &present_mode)) {
    return GHOST_kFailure;
  }

  /* Query the surface capabilities for the given present mode on the surface. */
  VkSurfacePresentScalingCapabilitiesEXT vk_surface_present_scaling_capabilities = {
      VK_STRUCTURE_TYPE_SURFACE_PRESENT_SCALING_CAPABILITIES_EXT,
  };
  VkSurfaceCapabilities2KHR vk_surface_capabilities = {
      VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_2_KHR,
      &vk_surface_present_scaling_capabilities,
  };
  VkSurfacePresentModeEXT vk_surface_present_mode = {
      VK_STRUCTURE_TYPE_SURFACE_PRESENT_MODE_EXT, nullptr, present_mode};
  VkPhysicalDeviceSurfaceInfo2KHR vk_physical_device_surface_info = {
      VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR, &vk_surface_present_mode, surface_};
  VkSurfaceCapabilitiesKHR capabilities = {};

  if (device_vk.use_vk_ext_swapchain_maintenance_1) {
    VK_CHECK(vkGetPhysicalDeviceSurfaceCapabilities2KHR(device_vk.vk_physical_device,
                                                        &vk_physical_device_surface_info,
                                                        &vk_surface_capabilities),
             GHOST_kFailure);
    capabilities = vk_surface_capabilities.surfaceCapabilities;
  }
  else {
    VK_CHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(
                 device_vk.vk_physical_device, surface_, &capabilities),
             GHOST_kFailure);
  }

  VkExtent2D new_extent = capabilities.currentExtent;
  if (new_extent.width == UINT32_MAX) {
    /* Respect WSI's fixed currentExtent; only an unconstrained extent uses the host request. */
    int width = 0;
    int height = 0;
#ifdef WITH_GHOST_WAYLAND
    if (wayland_window_info_) {
      width = wayland_window_info_->size[0];
      height = wayland_window_info_->size[1];
    }
#endif
#ifdef WITH_GHOST_OHOS
    if (platform_ == GHOST_kVulkanPlatformOHOS && ohos_window_info_) {
      width = ohos_window_info_->size[0];
      height = ohos_window_info_->size[1];
    }
#endif
    if (width == 0 || height == 0) {
      width = 1280;
      height = 720;
    }
    new_extent = {uint32_t(width), uint32_t(height)};
#ifdef WITH_GHOST_OHOS
    if (platform_ == GHOST_kVulkanPlatformOHOS) {
      new_extent.width = std::clamp(
          new_extent.width, capabilities.minImageExtent.width, capabilities.maxImageExtent.width);
      new_extent.height = std::clamp(new_extent.height,
                                     capabilities.minImageExtent.height,
                                     capabilities.maxImageExtent.height);
    }
#endif
    new_extent.width = std::max(new_extent.width, capabilities.minImageExtent.width);
    new_extent.height = std::max(new_extent.height, capabilities.minImageExtent.height);
  }
  if (device_vk.use_vk_ext_swapchain_maintenance_1) {
    new_extent.width = std::max(
        new_extent.width, vk_surface_present_scaling_capabilities.minScaledImageExtent.width);
    new_extent.height = std::max(
        new_extent.height, vk_surface_present_scaling_capabilities.minScaledImageExtent.height);
  }
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS && !force && swapchain_ &&
      new_extent.width == render_extent_.width && new_extent.height == render_extent_.height &&
      new_surface_format.format == surface_format_.format &&
      new_surface_format.colorSpace == surface_format_.colorSpace &&
      present_mode == ohos_present_mode_ &&
      capabilities.currentTransform == ohos_surface_transform_ &&
      use_hdr_swapchain == use_hdr_swapchain_)
  {
    /* A delayed WSI may choose the same extent for several distinct host sizes. The final blit
     * already scales to it; keep its images/semaphores until a genuine invalidation occurs. */
    ohos_requested_extent_ = {uint32_t(ohos_window_info_->size[0]),
                              uint32_t(ohos_window_info_->size[1])};
    return GHOST_kSuccess;
  }
#else
  (void)force;
#endif

  /* Discard swapchain resources of current swapchain. */
  GHOST_FrameDiscard *discard_pile = &frame_data_[render_frame_].discard_pile;
  for (GHOST_SwapchainImage &swapchain_image : swapchain_images_) {
    swapchain_image.vk_image = VK_NULL_HANDLE;
    if (swapchain_image.present_semaphore != VK_NULL_HANDLE) {
      discard_pile->semaphores.push_back(swapchain_image.present_semaphore);
      swapchain_image.present_semaphore = VK_NULL_HANDLE;
    }
  }

  /* Swap-chains with out any resolution should not be created. In the case the render extent is
   * zero we should not use the swap-chain.
   *
   * VUID-VkSwapchainCreateInfoKHR-imageExtent-01689
   */
  if (new_extent.width == 0 || new_extent.height == 0) {
    if (swapchain_) {
      discard_pile->swapchains.push_back(swapchain_);
      swapchain_ = VK_NULL_HANDLE;
    }
    return GHOST_kFailure;
  }

  /* Use double buffering when using FIFO. Increasing the number of images could stall when doing
   * actions that require low latency (paint cursor, UI resizing). MAILBOX prefers triple
   * buffering. */
  uint32_t image_count_requested = present_mode == VK_PRESENT_MODE_MAILBOX_KHR ? 3 : 2;
  /* NOTE: maxImageCount == 0 means no limit. */
  if (capabilities.minImageCount != 0 && image_count_requested < capabilities.minImageCount) {
    image_count_requested = capabilities.minImageCount;
  }
  if (capabilities.maxImageCount != 0 && image_count_requested > capabilities.maxImageCount) {
    image_count_requested = capabilities.maxImageCount;
  }

  VkSwapchainKHR old_swapchain = swapchain_;

  /* First time we stretch the swapchain image as it can happen that the first frame size isn't
   * correctly reported by the initial swapchain. All subsequent creations will use one to one as
   * that can reduce resizing artifacts. */
  VkPresentScalingFlagBitsEXT vk_present_scaling = old_swapchain == VK_NULL_HANDLE ?
                                                       VK_PRESENT_SCALING_STRETCH_BIT_EXT :
                                                       VK_PRESENT_SCALING_ONE_TO_ONE_BIT_EXT;

  VkSwapchainPresentModesCreateInfoEXT vk_swapchain_present_modes = {
      VK_STRUCTURE_TYPE_SWAPCHAIN_PRESENT_MODES_CREATE_INFO_EXT, nullptr, 1, &present_mode};
  VkSwapchainPresentScalingCreateInfoEXT vk_swapchain_present_scaling = {
      VK_STRUCTURE_TYPE_SWAPCHAIN_PRESENT_SCALING_CREATE_INFO_EXT,
      &vk_swapchain_present_modes,
      vk_surface_present_scaling_capabilities.supportedPresentScaling & vk_present_scaling,
      vk_surface_present_scaling_capabilities.supportedPresentGravityX &
          VK_PRESENT_GRAVITY_MIN_BIT_EXT,
      vk_surface_present_scaling_capabilities.supportedPresentGravityY &
          VK_PRESENT_GRAVITY_MAX_BIT_EXT,
  };

  VkSwapchainCreateInfoKHR create_info = {};
  create_info.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR;
  if (device_vk.use_vk_ext_swapchain_maintenance_1) {
    create_info.pNext = &vk_swapchain_present_scaling;
  }
  create_info.surface = surface_;
  create_info.minImageCount = image_count_requested;
  create_info.imageFormat = new_surface_format.format;
  create_info.imageColorSpace = new_surface_format.colorSpace;
  create_info.imageExtent = new_extent;
  create_info.imageArrayLayers = 1;
  create_info.imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                           (use_hdr_swapchain ? VK_IMAGE_USAGE_STORAGE_BIT : 0);
  create_info.preTransform = capabilities.currentTransform;
  create_info.compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
  create_info.presentMode = present_mode;
  create_info.clipped = VK_TRUE;
  create_info.oldSwapchain = old_swapchain;
  create_info.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
  create_info.queueFamilyIndexCount = 0;
  create_info.pQueueFamilyIndices = nullptr;

  /* Passing oldSwapchain retires it even when creation fails. Never acquire it again. */
  swapchain_ = VK_NULL_HANDLE;
  if (old_swapchain) {
    discard_pile->swapchains.push_back(old_swapchain);
  }
  VK_CHECK(vkCreateSwapchainKHR(device_vk.vk_device, &create_info, nullptr, &swapchain_),
           GHOST_kFailure);

  const auto invalid_images = [&]() {
    discard_pile->swapchains.push_back(swapchain_);
    swapchain_ = VK_NULL_HANDLE;
    swapchain_images_.clear();
    return GHOST_kFailure;
  };
  /* image_count may not be what we requested! Getter for final value. */
  uint32_t actual_image_count = 0;
  if (vkGetSwapchainImagesKHR(device_vk.vk_device, swapchain_, &actual_image_count, nullptr) !=
          VK_SUCCESS ||
      actual_image_count == 0)
  {
    return invalid_images();
  }
  /* Some platforms require a minimum amount of render frames that is larger than we expect. When
   * that happens we should increase the number of frames in flight. We could also consider
   * splitting the frame in flight and image specific data. */
  if (actual_image_count > frame_data_.size()) {
    CLOG_TRACE(&LOG, "Vulkan: Increasing frame data to %u frames", actual_image_count);
    frame_data_.resize(actual_image_count);
    discard_pile = &frame_data_[render_frame_].discard_pile;
  }
  swapchain_images_.resize(actual_image_count);
  std::vector<VkImage> swapchain_images(actual_image_count);
  if (vkGetSwapchainImagesKHR(
          device_vk.vk_device, swapchain_, &actual_image_count, swapchain_images.data()) !=
      VK_SUCCESS)
  {
    return invalid_images();
  }
  for (int index = 0; index < actual_image_count; index++) {
    swapchain_images_[index].vk_image = swapchain_images[index];
  }
  render_extent_ = new_extent;
  render_extent_min_ = capabilities.minImageExtent;
  surface_format_ = new_surface_format;
  use_hdr_swapchain_ = use_hdr_swapchain;
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS && ohos_window_info_) {
    ohos_requested_extent_ = {uint32_t(ohos_window_info_->size[0]),
                              uint32_t(ohos_window_info_->size[1])};
    ohos_extent_max_ = capabilities.maxImageExtent;
    ohos_present_mode_ = present_mode;
    ohos_surface_transform_ = capabilities.currentTransform;
    ohos_recreate_pending_ = false;
  }
#endif
  CLOG_DEBUG(&LOG,
             "Vulkan: recreating swapchain: width=%u, height=%u, format=%s, colorSpace=%s, "
             "present_mode=%s, image_count_requested=%u, image_count_acquired=%u, "
             "swapchain=%" PRIx64 ", old_swapchain=%" PRIx64 "",
             render_extent_.width,
             render_extent_.height,
             to_string_vk_format(surface_format_.format),
             to_string_vk_color_space(surface_format_.colorSpace),
             to_string_vk_present_mode(present_mode),
             image_count_requested,
             actual_image_count,
             uint64_t(swapchain_),
             uint64_t(old_swapchain));
  /* Construct new semaphores. It can be that image_count is larger than previously. We only need
   * to fill in where the handle is `VK_NULL_HANDLE`. */
  /* Previous handles from the frame data cannot be used and should be discarded. */
  for (GHOST_Frame &frame : frame_data_) {
    if (frame.acquire_semaphore != VK_NULL_HANDLE) {
      discard_pile->semaphores.push_back(frame.acquire_semaphore);
    }
    frame.acquire_semaphore = VK_NULL_HANDLE;
  }
  image_count_ = actual_image_count;
  return initializeFrameData();
}

void GHOST_ContextVK::destroySwapchainPresentFences(VkSwapchainKHR swapchain)
{
  GHOST_DeviceVK &device_vk = vulkan_instance.value().device.value();
  const std::vector<VkFence> &fences = present_fences_[swapchain];
  if (!fences.empty()) {
    vkWaitForFences(device_vk.vk_device, fences.size(), fences.data(), VK_TRUE, UINT64_MAX);
    for (VkFence fence : fences) {
      vkDestroyFence(device_vk.vk_device, fence, nullptr);
    }
  }
  present_fences_.erase(swapchain);
}

GHOST_TSuccess GHOST_ContextVK::destroySwapchain()
{
  GHOST_DeviceVK &device_vk = vulkan_instance.value().device.value();
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    /* Shutdown is separate from runtime retirement: drain GPU use BEFORE destroying images/chain.
     * Without swapchain maintenance/present-wait there is still no formal final-presentation
     * completion witness once the surface is gone. Existing idle-based teardown retains that
     * explicit no-extension limitation; runtime resize never uses idle as presentation proof. */
    device_vk.wait_idle();
  }
#endif

  if (swapchain_ != VK_NULL_HANDLE) {
    this->destroySwapchainPresentFences(swapchain_);
    vkDestroySwapchainKHR(device_vk.vk_device, swapchain_, nullptr);
  }
#ifdef WITH_GHOST_OHOS
  if (platform_ != GHOST_kVulkanPlatformOHOS)
#endif
  {
    device_vk.wait_idle();
  }
  for (GHOST_SwapchainImage &swapchain_image : swapchain_images_) {
    swapchain_image.destroy(device_vk.vk_device);
  }
  swapchain_images_.clear();
  for (GHOST_Frame &frame_data : frame_data_) {
    for (VkSwapchainKHR swapchain : frame_data.discard_pile.swapchains) {
      this->destroySwapchainPresentFences(swapchain);
    }
    frame_data.destroy(device_vk.vk_device);
  }
  frame_data_.clear();
#ifdef WITH_GHOST_OHOS
  if (platform_ == GHOST_kVulkanPlatformOHOS) {
    ohos_pending_retirement_.destroy(device_vk.vk_device);
    ohos_current_chain_ = {};
    ohos_current_image_bytes_ = 0;
    ohos_recreate_pending_ = ohos_settlement_pending_ = false;
    /* A lost device stays terminal across surface detach/attach; only a new context/device can
     * recover it. Do not mistake teardown for successful device-loss recovery. */
    ohos_latest_extent_ = ohos_requested_extent_ = {0, 0};
    ohos_host_changed_ms_ = ohos_last_settlement_ms_ = 0;
    ohos_recreations_this_tick_ = 0;
  }
#endif

  return GHOST_kSuccess;
}

#ifdef WITH_GHOST_OHOS
GHOST_TSuccess GHOST_ContextVK::detachOHOSWindow()
{
  if (platform_ != GHOST_kVulkanPlatformOHOS) return GHOST_kFailure;
  if (surface_ == VK_NULL_HANDLE) {
    ohos_native_window_ = nullptr;
    ohos_window_info_ = nullptr;
    return GHOST_kSuccess;
  }
  if (!vulkan_instance.has_value() || !vulkan_instance->device.has_value()) return GHOST_kFailure;
  /* NOT delete context / decrement users: Blender GPU resources still own this device. */
  GHOST_InstanceVK &instance = vulkan_instance.value();
  instance.device->wait_idle();
  destroySwapchain();
  swapchain_ = VK_NULL_HANDLE;
  acquired_swapchain_image_index_.reset();
  vkDestroySurfaceKHR(instance.vk_instance, surface_, nullptr);
  surface_ = VK_NULL_HANDLE;
  ohos_native_window_ = nullptr;
  ohos_window_info_ = nullptr;
  render_extent_ = {0, 0};
  render_extent_min_ = {0, 0};
  ohos_extent_max_ = {UINT32_MAX, UINT32_MAX};
  return GHOST_kSuccess;
}
GHOST_TSuccess GHOST_ContextVK::attachOHOSWindow(
    void *native_window, const GHOST_ContextVK_WindowInfo *window_info)
{
  if (platform_ != GHOST_kVulkanPlatformOHOS || !native_window || !window_info ||
      window_info->size[0] <= 0 || window_info->size[1] <= 0 || surface_ != VK_NULL_HANDLE ||
      !vulkan_instance.has_value() || !vulkan_instance->device.has_value()) return GHOST_kFailure;
  GHOST_InstanceVK &instance = vulkan_instance.value();
  auto create_surface = reinterpret_cast<PFN_vkCreateSurfaceOHOS>(
      vkGetInstanceProcAddr(instance.vk_instance, "vkCreateSurfaceOHOS"));
  if (!create_surface) return GHOST_kFailure;
  VkSurfaceCreateInfoOHOS info{};
  info.sType = VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS;
  info.window = static_cast<OHNativeWindow *>(native_window);
  VK_CHECK(create_surface(instance.vk_instance, &info, nullptr, &surface_), GHOST_kFailure);
  ohos_native_window_ = native_window;
  ohos_window_info_ = window_info;
  if (ohos_surface_lost_) {
    /* A newly leased surface may recover WSI loss; device/submission failures remain terminal. */
    ohos_surface_lost_ = false;
    ohos_terminal_failure_ = false;
  }
  /* destroySwapchain clears frame storage; the next acquisition creates WSI sync. */
  frame_data_.resize(2);
  render_frame_ = 0;
  return GHOST_kSuccess;
}
#endif

const char *GHOST_ContextVK::getPlatformSpecificSurfaceExtension() const
{
#ifdef _WIN32
  return VK_KHR_WIN32_SURFACE_EXTENSION_NAME;
#elif defined(__APPLE__)
  return VK_EXT_METAL_SURFACE_EXTENSION_NAME;
#else /* UNIX/Linux */
  switch (platform_) {
#  ifdef WITH_GHOST_X11
    case GHOST_kVulkanPlatformX11:
      return VK_KHR_XLIB_SURFACE_EXTENSION_NAME;
      break;
#  endif
#  ifdef WITH_GHOST_WAYLAND
    case GHOST_kVulkanPlatformWayland:
      return VK_KHR_WAYLAND_SURFACE_EXTENSION_NAME;
      break;
#  endif
#ifdef WITH_GHOST_OHOS
    case GHOST_kVulkanPlatformOHOS:
      return VK_OHOS_SURFACE_EXTENSION_NAME;
#endif
    case GHOST_kVulkanPlatformHeadless:
      break;
  }
#endif
  return nullptr;
}

GHOST_TSuccess GHOST_ContextVK::initializeDrawingContext()
{
  bool use_vk_ext_swapchain_colorspace = false;
#ifdef _WIN32
  const bool use_window_surface = (hwnd_ != nullptr);
#elif defined(__APPLE__)
  const bool use_window_surface = (metal_layer_ != nullptr);
#else /* UNIX/Linux */
  bool use_window_surface = false;
  switch (platform_) {
#  ifdef WITH_GHOST_X11
    case GHOST_kVulkanPlatformX11:
      use_window_surface = (display_ != nullptr) && (window_ != (Window) nullptr);
      break;
#  endif
#  ifdef WITH_GHOST_WAYLAND
    case GHOST_kVulkanPlatformWayland:
      use_window_surface = (wayland_display_ != nullptr) && (wayland_surface_ != nullptr);
      break;
#  endif
#ifdef WITH_GHOST_OHOS
    case GHOST_kVulkanPlatformOHOS:
      use_window_surface = ohos_native_window_ != nullptr;
      break;
#endif
    case GHOST_kVulkanPlatformHeadless:
      use_window_surface = false;
      break;
  }
#endif

  blender::Vector<const char *> required_device_extensions;
  blender::Vector<const char *> optional_device_extensions;

#ifdef WITH_GHOST_OHOS
  /* The first context can be offscreen; keep the shared instance/device capable
   * of attaching the host's surface later. Window classes request API 1.3. */
  if (!use_window_surface) required_device_extensions.append(VK_KHR_SWAPCHAIN_EXTENSION_NAME);
#endif
  /* Initialize VkInstance */
  if (!vulkan_instance.has_value()) {
    vulkan_instance.emplace();
    GHOST_InstanceVK &instance_vk = vulkan_instance.value();
    instance_vk.extensions.enable(VK_EXT_DEBUG_UTILS_EXTENSION_NAME, true);

    /* Some XR platforms load functions without knowing if they were replaced by a core
     * function. Monado for example always uses the extension functions. Due to maintenance changes
     * drivers now only return the function pointer when the extension is enabled.
     *
     * We work around this by requesting Vulkan promoted extensions.
     */
#ifdef WITH_XR_OPENXR
    /* Vulkan 1.1 promoted instance extensions, enabled for OpenXR usage. */
    instance_vk.extensions.enable(VK_KHR_EXTERNAL_FENCE_CAPABILITIES_EXTENSION_NAME);
    instance_vk.extensions.enable(VK_KHR_EXTERNAL_MEMORY_CAPABILITIES_EXTENSION_NAME);
    instance_vk.extensions.enable(VK_KHR_EXTERNAL_SEMAPHORE_CAPABILITIES_EXTENSION_NAME);
    instance_vk.extensions.enable(VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME);

    /* SteamVR requests both NVIDIA and KHR rectified extension. */
    instance_vk.extensions.enable(VK_NV_EXTERNAL_MEMORY_CAPABILITIES_EXTENSION_NAME, true);

    /* Has been promoted to VK_EXT_debug_utils. */
    instance_vk.extensions.enable(VK_EXT_DEBUG_REPORT_EXTENSION_NAME, true);
#endif

#ifdef WITH_GHOST_OHOS
    instance_vk.extensions.enable(VK_KHR_SURFACE_EXTENSION_NAME);
    instance_vk.extensions.enable(VK_OHOS_SURFACE_EXTENSION_NAME);
#endif
    if (use_window_surface) {
      const char *native_surface_extension_name = getPlatformSpecificSurfaceExtension();
      instance_vk.extensions.enable(VK_KHR_SURFACE_EXTENSION_NAME);
      instance_vk.extensions.enable(native_surface_extension_name);
      /* X11 doesn't use the correct swapchain offset, flipping can squash the first frames. */
      const bool use_vk_ext_swapchain_maintenance1 =
#ifdef WITH_GHOST_X11
          platform_ != GHOST_kVulkanPlatformX11 &&
#endif
          instance_vk.extensions.is_supported(VK_EXT_SURFACE_MAINTENANCE_1_EXTENSION_NAME) &&
          instance_vk.extensions.is_supported(VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME);
      if (use_vk_ext_swapchain_maintenance1) {
        instance_vk.extensions.enable(VK_EXT_SURFACE_MAINTENANCE_1_EXTENSION_NAME);
        instance_vk.extensions.enable(VK_KHR_GET_SURFACE_CAPABILITIES_2_EXTENSION_NAME);
        optional_device_extensions.append(VK_EXT_SWAPCHAIN_MAINTENANCE_1_EXTENSION_NAME);
      }

      use_vk_ext_swapchain_colorspace = instance_vk.extensions.enable(
          VK_EXT_SWAPCHAIN_COLOR_SPACE_EXTENSION_NAME, true);

      required_device_extensions.append(VK_KHR_SWAPCHAIN_EXTENSION_NAME);
    }

    const uint32_t requested_api = VK_MAKE_VERSION(context_major_version_, context_minor_version_, 0);
    if (!instance_vk.create_instance(
#ifdef WITH_GHOST_OHOS
            std::max(requested_api, uint32_t(VK_API_VERSION_1_3))))
#else
            requested_api))
#endif
    {
      vulkan_instance.reset();
      return GHOST_kFailure;
    }
  }
  GHOST_InstanceVK &instance_vk = vulkan_instance.value();

  /* Initialize VkSurface */
  if (use_window_surface) {
#ifdef _WIN32
    VkWin32SurfaceCreateInfoKHR surface_create_info = {};
    surface_create_info.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
    surface_create_info.hinstance = GetModuleHandle(nullptr);
    surface_create_info.hwnd = hwnd_;
    VK_CHECK(
        vkCreateWin32SurfaceKHR(instance_vk.vk_instance, &surface_create_info, nullptr, &surface_),
        GHOST_kFailure);
#elif defined(__APPLE__)
    VkMetalSurfaceCreateInfoEXT info = {};
    info.sType = VK_STRUCTURE_TYPE_METAL_SURFACE_CREATE_INFO_EXT;
    info.pNext = nullptr;
    info.flags = 0;
    info.pLayer = static_cast<CAMetalLayer *>(metal_layer_);
    VK_CHECK(vkCreateMetalSurfaceEXT(instance_vk.vk_instance, &info, nullptr, &surface_),
             GHOST_kFailure);
#else
    switch (platform_) {
#  ifdef WITH_GHOST_X11
      case GHOST_kVulkanPlatformX11: {
        VkXlibSurfaceCreateInfoKHR surface_create_info = {};
        surface_create_info.sType = VK_STRUCTURE_TYPE_XLIB_SURFACE_CREATE_INFO_KHR;
        surface_create_info.dpy = display_;
        surface_create_info.window = window_;
        VK_CHECK(vkCreateXlibSurfaceKHR(
                     instance_vk.vk_instance, &surface_create_info, nullptr, &surface_),
                 GHOST_kFailure);
        break;
      }
#  endif
#  ifdef WITH_GHOST_WAYLAND
      case GHOST_kVulkanPlatformWayland: {
        VkWaylandSurfaceCreateInfoKHR surface_create_info = {};
        surface_create_info.sType = VK_STRUCTURE_TYPE_WAYLAND_SURFACE_CREATE_INFO_KHR;
        surface_create_info.display = wayland_display_;
        surface_create_info.surface = wayland_surface_;
        VK_CHECK(vkCreateWaylandSurfaceKHR(
                     instance_vk.vk_instance, &surface_create_info, nullptr, &surface_),
                 GHOST_kFailure);
        break;
      }
#  endif
#ifdef WITH_GHOST_OHOS
      case GHOST_kVulkanPlatformOHOS: {
        VkSurfaceCreateInfoOHOS info{};
        info.sType = VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS;
        info.window = static_cast<OHNativeWindow *>(ohos_native_window_);
        /* The SDK's real extension, not Android/Xlib surface emulation. */
        auto create_surface_ohos = reinterpret_cast<PFN_vkCreateSurfaceOHOS>(
            vkGetInstanceProcAddr(instance_vk.vk_instance, "vkCreateSurfaceOHOS"));
        if (!create_surface_ohos) return GHOST_kFailure;
        VK_CHECK(create_surface_ohos(instance_vk.vk_instance, &info, nullptr, &surface_),
                 GHOST_kFailure);
        break;
      }
#endif
      case GHOST_kVulkanPlatformHeadless: {
        surface_ = VK_NULL_HANDLE;
        break;
      }
    }

#endif
  }

  /* Initialize VkDevice */
  if (!vulkan_instance->device.has_value()) {
    /* External memory extensions. */
#ifdef _WIN32
    optional_device_extensions.append(VK_KHR_EXTERNAL_MEMORY_WIN32_EXTENSION_NAME);
#elif defined(__APPLE__)
#else /* Linux */
    optional_device_extensions.append(VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME);
#endif

#ifndef __APPLE__
    required_device_extensions.append(VK_EXT_PROVOKING_VERTEX_EXTENSION_NAME);
#endif
    required_device_extensions.append(VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_DYNAMIC_RENDERING_LOCAL_READ_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_DYNAMIC_RENDERING_UNUSED_ATTACHMENTS_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_SHADER_STENCIL_EXPORT_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_MAINTENANCE_4_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_FRAGMENT_SHADER_BARYCENTRIC_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_ROBUSTNESS_2_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_SYNCHRONIZATION_2_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_MEMORY_PRIORITY_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_PAGEABLE_DEVICE_LOCAL_MEMORY_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_PIPELINE_LIBRARY_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_GRAPHICS_PIPELINE_LIBRARY_EXTENSION_NAME);
    optional_device_extensions.append(VK_EXT_LINE_RASTERIZATION_EXTENSION_NAME);
    /* Disabled as the extension is available, but without any features set. */
#ifndef __APPLE__
    optional_device_extensions.append(VK_EXT_EXTENDED_DYNAMIC_STATE_EXTENSION_NAME);
#endif
    optional_device_extensions.append(VK_EXT_VERTEX_INPUT_DYNAMIC_STATE_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_COPY_COMMANDS_2_EXTENSION_NAME);
    optional_device_extensions.append(VK_KHR_FORMAT_FEATURE_FLAGS_2_EXTENSION_NAME);
#if 0
    /* VK_EXT_host_image_copy isn't supported by Renderdoc and also isn't working as expected. */
    optional_device_extensions.append(VK_EXT_HOST_IMAGE_COPY_EXTENSION_NAME);
#endif

#ifdef WITH_XR_OPENXR
    optional_device_extensions.extend({
#  ifdef _WIN32
        VK_KHR_EXTERNAL_FENCE_WIN32_EXTENSION_NAME,
        VK_KHR_EXTERNAL_SEMAPHORE_WIN32_EXTENSION_NAME,

        VK_KHR_WIN32_KEYED_MUTEX_EXTENSION_NAME,
#  elif defined(__APPLE__)
#  else
        VK_KHR_EXTERNAL_FENCE_FD_EXTENSION_NAME,
        VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME,
#  endif
        /* Vulkan 1.1 promoted device extensions, enabled for OpenXR usage. */
        VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME,
        VK_KHR_BIND_MEMORY_2_EXTENSION_NAME,
        VK_KHR_DEDICATED_ALLOCATION_EXTENSION_NAME,
        VK_KHR_EXTERNAL_FENCE_EXTENSION_NAME,
        VK_KHR_EXTERNAL_MEMORY_EXTENSION_NAME,
        VK_KHR_EXTERNAL_SEMAPHORE_EXTENSION_NAME,
        VK_KHR_GET_MEMORY_REQUIREMENTS_2_EXTENSION_NAME,
        VK_KHR_IMAGE_FORMAT_LIST_EXTENSION_NAME,
        VK_KHR_MULTIVIEW_EXTENSION_NAME,
        VK_KHR_MAINTENANCE_1_EXTENSION_NAME,
        VK_KHR_MAINTENANCE_2_EXTENSION_NAME,

        /* Vulkan 1.2 promoted device extensions, enabled for OpenXR usage. */
        VK_KHR_CREATE_RENDERPASS_2_EXTENSION_NAME,
        VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME,

        /* Vulkan 1.3 promoted device extensions, enabled for OpenXR usage. */
        VK_EXT_PIPELINE_CREATION_CACHE_CONTROL_EXTENSION_NAME,

        /* Vulkan 1.4 promoted device extensions, enabled for OpenXR usage. */
        VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME,

        /* Has been promoted to VK_EXT_debug_utils */
        VK_EXT_DEBUG_MARKER_EXTENSION_NAME});
#endif

    if (!instance_vk.select_physical_device(preferred_device_, required_device_extensions)) {
      return GHOST_kFailure;
    }

    if (!instance_vk.create_device(use_vk_ext_swapchain_colorspace,
                                   context_params_.is_debug,
                                   required_device_extensions,
                                   optional_device_extensions))
    {
      return GHOST_kFailure;
    }
  }
  GHOST_DeviceVK &device_vk = instance_vk.device.value();

  device_vk.users++;
#ifdef WITH_GHOST_OHOS
  ohos_device_user_acquired_ = true;
#endif

  render_extent_ = {0, 0};
  render_extent_min_ = {0, 0};
  surface_format_ = {VK_FORMAT_R8G8B8A8_UNORM, VK_COLOR_SPACE_SRGB_NONLINEAR_KHR};

  active_context_ = this;
  return GHOST_kSuccess;
}

GHOST_TSuccess GHOST_ContextVK::releaseNativeHandles()
{
  return GHOST_kSuccess;
}

#ifdef WITH_GHOST_WAYLAND
GHOST_TSuccess GHOST_ContextVK::supportsWaylandColorManagement()
{
  if (!vulkan_instance.has_value()) {
    return GHOST_kFailure;
  }
  if (!vulkan_instance->device.has_value()) {
    return GHOST_kFailure;
  }

  GHOST_DeviceVK &device_vk = vulkan_instance->device.value();
  if (device_vk.properties_12.driverID != VK_DRIVER_ID_NVIDIA_PROPRIETARY) {
    return GHOST_kSuccess;
  }

  uint32_t driver_version = device_vk.properties.properties.driverVersion;
  uint32_t major_version = (driver_version >> 22) & 0x3ff;
  /* NVIDIA has a well known implementation of wayland color management protocol since driver
   * version 595. Using the color management protocol leads to very bright output when using on
   * older driver. The output isn't influenced by surface configuration.
   */
  if (major_version < 595) {
    return GHOST_kFailure;
  }

  return GHOST_kSuccess;
}
#endif
