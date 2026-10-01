// SPDX-License-Identifier: MIT
// A native OHOS Vulkan capability probe; no desktop-platform emulation.
#if !defined(__OHOS__) || !defined(__aarch64__)
#error This probe must be compiled natively for HarmonyOS/OpenHarmony ARM64.
#endif
#define VK_USE_PLATFORM_OHOS 1
#include <vulkan/vulkan.h>
#include <deviceinfo.h>
#include <dlfcn.h>
#include <sys/utsname.h>
#include <unistd.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
#include <utility>
#include <vector>
#include "vulkan_probe.h"

namespace {
using Fields = std::vector<std::pair<std::string, std::string>>;
std::string quote(const char *s) {
  if (!s) return "null";
  std::string r = "\"";
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); *p; ++p) {
    switch (*p) {
      case '"': r += "\\\""; break;
      case '\\': r += "\\\\"; break;
      case '\n': r += "\\n"; break;
      case '\r': r += "\\r"; break;
      case '\t': r += "\\t"; break;
      default:
        if (*p < 32) { char b[7]; std::snprintf(b, sizeof(b), "\\u%04x", *p); r += b; }
        else r += static_cast<char>(*p);
    }
  }
  return r + '"';
}
std::string quote(const std::string &s) { return quote(s.c_str()); }
std::string num(uint64_t n) { return std::to_string(n); }
std::string boolean(bool b) { return b ? "true" : "false"; }
std::string object(const Fields &fields) {
  std::string r = "{";
  for (const auto &f : fields) { if (r.size() > 1) r += ','; r += quote(f.first) + ':' + f.second; }
  return r + '}';
}
std::string array(const std::vector<std::string> &items) {
  std::string r = "[";
  for (const auto &x : items) { if (r.size() > 1) r += ','; r += x; }
  return r + ']';
}
std::string version(uint32_t v) {
  return std::to_string(VK_API_VERSION_MAJOR(v)) + '.' + std::to_string(VK_API_VERSION_MINOR(v)) + '.' +
         std::to_string(VK_API_VERSION_PATCH(v));
}
std::string hex(uint32_t n) { char b[11]; std::snprintf(b, sizeof(b), "0x%08x", n); return quote(b); }
const char *result_name(VkResult r) {
  switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    default: return "other_VkResult";
  }
}
std::string result(VkResult r) { return object({{"code", std::to_string(r)}, {"name", quote(result_name(r))}}); }
void log_result(const char *stage, VkResult r) { std::fprintf(stderr, "%s: %s (%d)\n", stage, result_name(r), r); }

template<class T, class F> VkResult enumerate(std::vector<T> &out, F f) {
  // VK_INCOMPLETE can occur when counts change; do not mistake a partial list for the full set.
  for (unsigned attempt = 0; attempt < 8; ++attempt) {
    uint32_t n = 0; VkResult r = f(&n, nullptr);
    if (r != VK_SUCCESS && r != VK_INCOMPLETE) return r;
    out.resize(n);
    if (!n) return VK_SUCCESS;
    r = f(&n, out.data()); out.resize(n);
    if (r != VK_INCOMPLETE) return r;
  }
  return VK_INCOMPLETE;
}
bool has(const std::vector<VkExtensionProperties> &v, const char *name) {
  return std::any_of(v.begin(), v.end(), [name](const auto &p) { return std::strcmp(p.extensionName, name) == 0; });
}
std::string extensions(std::vector<VkExtensionProperties> v) {
  std::sort(v.begin(), v.end(), [](const auto &a, const auto &b) { return std::strcmp(a.extensionName, b.extensionName) < 0; });
  std::vector<std::string> items;
  for (const auto &p : v) items.push_back(object({{"name", quote(p.extensionName)}, {"specVersion", num(p.specVersion)}}));
  return array(items);
}
#define FEATURES10(X) \
  X(robustBufferAccess) X(fullDrawIndexUint32) X(imageCubeArray) X(independentBlend) \
  X(geometryShader) X(tessellationShader) X(sampleRateShading) X(dualSrcBlend) X(logicOp) \
  X(multiDrawIndirect) X(drawIndirectFirstInstance) X(depthClamp) X(depthBiasClamp) \
  X(fillModeNonSolid) X(depthBounds) X(wideLines) X(largePoints) X(alphaToOne) X(multiViewport) \
  X(samplerAnisotropy) X(textureCompressionETC2) X(textureCompressionASTC_LDR) X(textureCompressionBC) \
  X(occlusionQueryPrecise) X(pipelineStatisticsQuery) X(vertexPipelineStoresAndAtomics) \
  X(fragmentStoresAndAtomics) X(shaderTessellationAndGeometryPointSize) X(shaderImageGatherExtended) \
  X(shaderStorageImageExtendedFormats) X(shaderStorageImageMultisample) \
  X(shaderStorageImageReadWithoutFormat) X(shaderStorageImageWriteWithoutFormat) \
  X(shaderUniformBufferArrayDynamicIndexing) X(shaderSampledImageArrayDynamicIndexing) \
  X(shaderStorageBufferArrayDynamicIndexing) X(shaderStorageImageArrayDynamicIndexing) \
  X(shaderClipDistance) X(shaderCullDistance) X(shaderFloat64) X(shaderInt64) X(shaderInt16) \
  X(shaderResourceResidency) X(shaderResourceMinLod) X(sparseBinding) X(sparseResidencyBuffer) \
  X(sparseResidencyImage2D) X(sparseResidencyImage3D) X(sparseResidency2Samples) X(sparseResidency4Samples) \
  X(sparseResidency8Samples) X(sparseResidency16Samples) X(sparseResidencyAliased) \
  X(variableMultisampleRate) X(inheritedQueries)
#define FEATURES11(X) \
  X(storageBuffer16BitAccess) X(uniformAndStorageBuffer16BitAccess) X(storagePushConstant16) \
  X(storageInputOutput16) X(multiview) X(multiviewGeometryShader) X(multiviewTessellationShader) \
  X(variablePointersStorageBuffer) X(variablePointers) X(protectedMemory) X(samplerYcbcrConversion) X(shaderDrawParameters)
#define FEATURES12(X) \
  X(samplerMirrorClampToEdge) X(drawIndirectCount) X(storageBuffer8BitAccess) \
  X(uniformAndStorageBuffer8BitAccess) X(storagePushConstant8) X(shaderBufferInt64Atomics) \
  X(shaderSharedInt64Atomics) X(shaderFloat16) X(shaderInt8) X(descriptorIndexing) \
  X(shaderInputAttachmentArrayDynamicIndexing) X(shaderUniformTexelBufferArrayDynamicIndexing) \
  X(shaderStorageTexelBufferArrayDynamicIndexing) X(shaderUniformBufferArrayNonUniformIndexing) \
  X(shaderSampledImageArrayNonUniformIndexing) X(shaderStorageBufferArrayNonUniformIndexing) \
  X(shaderStorageImageArrayNonUniformIndexing) X(shaderInputAttachmentArrayNonUniformIndexing) \
  X(shaderUniformTexelBufferArrayNonUniformIndexing) X(shaderStorageTexelBufferArrayNonUniformIndexing) \
  X(descriptorBindingUniformBufferUpdateAfterBind) X(descriptorBindingSampledImageUpdateAfterBind) \
  X(descriptorBindingStorageImageUpdateAfterBind) X(descriptorBindingStorageBufferUpdateAfterBind) \
  X(descriptorBindingUniformTexelBufferUpdateAfterBind) X(descriptorBindingStorageTexelBufferUpdateAfterBind) \
  X(descriptorBindingUpdateUnusedWhilePending) X(descriptorBindingPartiallyBound) \
  X(descriptorBindingVariableDescriptorCount) X(runtimeDescriptorArray) X(samplerFilterMinmax) \
  X(scalarBlockLayout) X(imagelessFramebuffer) X(uniformBufferStandardLayout) X(shaderSubgroupExtendedTypes) \
  X(separateDepthStencilLayouts) X(hostQueryReset) X(timelineSemaphore) X(bufferDeviceAddress) \
  X(bufferDeviceAddressCaptureReplay) X(bufferDeviceAddressMultiDevice) X(vulkanMemoryModel) \
  X(vulkanMemoryModelDeviceScope) X(vulkanMemoryModelAvailabilityVisibilityChains) \
  X(shaderOutputViewportIndex) X(shaderOutputLayer) X(subgroupBroadcastDynamicId)
#define ADD_FEATURE(name) fields.push_back({#name, boolean(f.name == VK_TRUE)});
std::string features10(const VkPhysicalDeviceFeatures &f) { Fields fields; FEATURES10(ADD_FEATURE) return object(fields); }
std::string features11(const VkPhysicalDeviceVulkan11Features &f) { Fields fields; FEATURES11(ADD_FEATURE) return object(fields); }
std::string features12(const VkPhysicalDeviceVulkan12Features &f) { Fields fields; FEATURES12(ADD_FEATURE) return object(fields); }
#undef ADD_FEATURE

std::string os_info() {
  utsname u{}; const int ur = uname(&u);
  return object({{"uname_result", std::to_string(ur)}, {"sysname", quote(u.sysname)},
    {"release", quote(u.release)}, {"version", quote(u.version)}, {"machine", quote(u.machine)},
    {"osFullName", quote(OH_GetOSFullName())}, {"displayVersion", quote(OH_GetDisplayVersion())},
    {"osReleaseType", quote(OH_GetOsReleaseType())}, {"sdkApiVersion", std::to_string(OH_GetSdkApiVersion())},
    {"firstApiVersion", std::to_string(OH_GetFirstApiVersion())}, {"abiList", quote(OH_GetAbiList())},
    {"deviceType", quote(OH_GetDeviceType())}, {"manufacturer", quote(OH_GetManufacture())},
    {"productModel", quote(OH_GetProductModel())}});
}
std::string loader_info() {
  Dl_info i{}; dladdr(reinterpret_cast<void *>(vkGetInstanceProcAddr), &i);
  return object({{"linkedLibrary", quote(i.dli_fname)}, {"headerVersion", num(VK_HEADER_VERSION)},
    {"VK_ICD_FILENAMES", quote(std::getenv("VK_ICD_FILENAMES"))},
    {"VK_DRIVER_FILES", quote(std::getenv("VK_DRIVER_FILES"))},
    {"VK_LAYER_PATH", quote(std::getenv("VK_LAYER_PATH"))}});
}

std::string device_info(VkInstance instance, VkPhysicalDevice physical, uint32_t instance_api,
                        bool features2_khr, bool surface_available, bool ohos_surface_available) {
  VkPhysicalDeviceProperties p{}; vkGetPhysicalDeviceProperties(physical, &p);
  std::fprintf(stderr, "device: %s api=%s vendor=0x%04x device=0x%04x\n", p.deviceName, version(p.apiVersion).c_str(), p.vendorID, p.deviceID);
  std::vector<VkExtensionProperties> ext;
  VkResult er = enumerate(ext, [physical](uint32_t *n, VkExtensionProperties *a) { return vkEnumerateDeviceExtensionProperties(physical, nullptr, n, a); });
  const bool ext_valid = er == VK_SUCCESS;
  const uint32_t effective = std::min(p.apiVersion, instance_api);
  auto getf2 = reinterpret_cast<PFN_vkGetPhysicalDeviceFeatures2>(vkGetInstanceProcAddr(instance,
    instance_api >= VK_API_VERSION_1_1 ? "vkGetPhysicalDeviceFeatures2" : (features2_khr ? "vkGetPhysicalDeviceFeatures2KHR" : "")));
  auto getp2 = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties2>(vkGetInstanceProcAddr(instance,
    instance_api >= VK_API_VERSION_1_1 ? "vkGetPhysicalDeviceProperties2" : (features2_khr ? "vkGetPhysicalDeviceProperties2KHR" : "")));
  // Vulkan11Features and Vulkan12Features aggregate structures were both introduced in Vulkan 1.2.
  // Never chain them for a 1.0/1.1 effective device, nor combine them with duplicate promoted structs.
  const bool aggregate_queried = getf2 && effective >= VK_API_VERSION_1_2;
  const bool dynamic_queried = getf2 && (effective >= VK_API_VERSION_1_3 || (ext_valid && has(ext, VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME)));
  const bool provoking_queried = getf2 && ext_valid && has(ext, VK_EXT_PROVOKING_VERTEX_EXTENSION_NAME);
  VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
  VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES};
  VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
  VkPhysicalDeviceDynamicRenderingFeatures dynamic{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES};
  VkPhysicalDeviceProvokingVertexFeaturesEXT provoking{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROVOKING_VERTEX_FEATURES_EXT};
  VkPhysicalDeviceShaderDrawParametersFeatures draw{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DRAW_PARAMETERS_FEATURES};
  VkPhysicalDeviceTimelineSemaphoreFeatures timeline{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
  VkPhysicalDeviceBufferDeviceAddressFeatures address{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES};
  bool draw_queried = aggregate_queried, timeline_queried = aggregate_queried, address_queried = aggregate_queried;
  void **tail = &f2.pNext;
  auto chain = [&tail](auto &f) { *tail = &f; tail = &f.pNext; };
  if (aggregate_queried) { chain(f11); chain(f12); }
  else if (getf2) {
    // KHR_shader_draw_parameters has no feature struct on Vulkan 1.0; its promoted struct is core 1.1.
    draw_queried = effective >= VK_API_VERSION_1_1;
    timeline_queried = ext_valid && has(ext, VK_KHR_TIMELINE_SEMAPHORE_EXTENSION_NAME);
    address_queried = ext_valid && has(ext, VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME);
    if (draw_queried) chain(draw);
    if (timeline_queried) chain(timeline);
    if (address_queried) chain(address);
  }
  if (dynamic_queried) chain(dynamic);
  if (provoking_queried) chain(provoking);
  if (getf2) getf2(physical, &f2); else vkGetPhysicalDeviceFeatures(physical, &f2.features);
  VkPhysicalDeviceFeatures legacy{};
  vkGetPhysicalDeviceFeatures(physical, &legacy);
  const bool features10_consistent = features10(legacy) == features10(f2.features);

  VkPhysicalDeviceDriverProperties dp{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
  bool driver_queried = getp2 && (effective >= VK_API_VERSION_1_2 || (ext_valid && has(ext, VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME)));
  if (driver_queried) {
    VkPhysicalDeviceProperties2 p2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2}; p2.pNext = &dp; getp2(physical, &p2);
  }
  const std::string conformance = std::to_string(dp.conformanceVersion.major) + '.' + std::to_string(dp.conformanceVersion.minor) + '.' +
    std::to_string(dp.conformanceVersion.subminor) + '.' + std::to_string(dp.conformanceVersion.patch);
  Fields fields{{"name", quote(p.deviceName)}, {"deviceType", num(p.deviceType)},
    {"apiVersion", object({{"raw", num(p.apiVersion)}, {"text", quote(version(p.apiVersion))}})},
    {"effectiveApiVersion", quote(version(effective))}, {"vendorID", num(p.vendorID)}, {"vendorIDHex", hex(p.vendorID)},
    {"deviceID", num(p.deviceID)}, {"deviceIDHex", hex(p.deviceID)},
    {"driver", object({{"rawVersion", num(p.driverVersion)}, {"rawVersionHex", hex(p.driverVersion)},
      {"genericVkVersionDecode", quote(version(p.driverVersion))},
      {"versionNote", quote("driverVersion is vendor-specific; genericVkVersionDecode is not a vendor driver version assertion")},
      {"propertiesQueried", boolean(driver_queried)}, {"driverID", driver_queried ? num(dp.driverID) : "null"},
      {"name", driver_queried ? quote(dp.driverName) : "null"}, {"info", driver_queried ? quote(dp.driverInfo) : "null"},
      {"conformanceVersion", driver_queried ? quote(conformance) : "null"}})},
    {"extensionEnumeration", result(er)}, {"extensions", extensions(ext)}, {"features10", features10(f2.features)},
    {"features10CrossCheck", object({{"legacyVsFeatures2Consistent", boolean(features10_consistent)},
      {"legacyMethod", quote("vkGetPhysicalDeviceFeatures (independent direct call)")}, {"legacyFeatures", features10(legacy)}})},
    {"features11Queried", boolean(aggregate_queried)}, {"features11", aggregate_queried ? features11(f11) : "null"},
    {"features12Queried", boolean(aggregate_queried)}, {"features12", aggregate_queried ? features12(f12) : "null"},
    {"dynamicRenderingQueried", boolean(dynamic_queried)}, {"dynamicRendering", dynamic_queried ? boolean(dynamic.dynamicRendering) : "null"},
    {"provokingVertexQueried", boolean(provoking_queried)}, {"provokingVertexLast", provoking_queried ? boolean(provoking.provokingVertexLast) : "null"},
    {"transformFeedbackPreservesProvokingVertex", provoking_queried ? boolean(provoking.transformFeedbackPreservesProvokingVertex) : "null"}};
  const auto shader_draw = aggregate_queried ? f11.shaderDrawParameters : draw.shaderDrawParameters;
  const auto timeline_value = aggregate_queried ? f12.timelineSemaphore : timeline.timelineSemaphore;
  const auto address_value = aggregate_queried ? f12.bufferDeviceAddress : address.bufferDeviceAddress;
  Fields required;
#define REQUIRED10(name) required.push_back({#name, boolean(f2.features.name == VK_TRUE)});
  REQUIRED10(geometryShader) REQUIRED10(vertexPipelineStoresAndAtomics) REQUIRED10(multiViewport)
  REQUIRED10(shaderClipDistance) REQUIRED10(fragmentStoresAndAtomics) REQUIRED10(logicOp)
  REQUIRED10(dualSrcBlend) REQUIRED10(imageCubeArray) REQUIRED10(multiDrawIndirect) REQUIRED10(drawIndirectFirstInstance)
#undef REQUIRED10
  required.push_back({"shaderDrawParameters", draw_queried ? boolean(shader_draw) : "null"});
  required.push_back({"timelineSemaphore", timeline_queried ? boolean(timeline_value) : "null"});
  required.push_back({"bufferDeviceAddress", address_queried ? boolean(address_value) : "null"});
  required.push_back({"dynamicRendering", dynamic_queried ? boolean(dynamic.dynamicRendering) : (getf2 && ext_valid ? "false" : "null")});
  required.push_back({"provokingVertexLast", provoking_queried ? boolean(provoking.provokingVertexLast) : (getf2 && ext_valid ? "false" : "null")});
  std::vector<std::string> missing_features, unknown_features, missing_extensions;
  for (const auto &f : required) { if (f.second == "false") missing_features.push_back(quote(f.first)); else if (f.second == "null") unknown_features.push_back(quote(f.first)); }
  Fields required_ext;
  for (const char *name : {VK_KHR_SWAPCHAIN_EXTENSION_NAME, VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME, VK_EXT_PROVOKING_VERTEX_EXTENSION_NAME}) {
    bool supported = ext_valid && has(ext, name);
    required_ext.push_back({name, ext_valid ? boolean(supported) : "null"});
    if (ext_valid && !supported) missing_extensions.push_back(quote(name));
  }
  required_ext.push_back({VK_KHR_SURFACE_EXTENSION_NAME, boolean(surface_available)});
  required_ext.push_back({VK_OHOS_SURFACE_EXTENSION_NAME, boolean(ohos_surface_available)});
  if (!surface_available) missing_extensions.push_back(quote(VK_KHR_SURFACE_EXTENSION_NAME));
  if (!ohos_surface_available) missing_extensions.push_back(quote(VK_OHOS_SURFACE_EXTENSION_NAME));
  const bool api_ok = effective >= VK_API_VERSION_1_2;
  bool strict_ok = api_ok && features10_consistent && missing_features.empty() && unknown_features.empty() && ext_valid && missing_extensions.empty();
  fields.push_back({"blender522Required", object({{"source", quote("caller-supplied Blender 5.2.2 requirement checklist; this probe does not substitute for Blender runtime validation")},
    {"minimumApi12", boolean(api_ok)}, {"features", object(required)}, {"extensions", object(required_ext)},
    {"missingFeatures", array(missing_features)}, {"unknownFeatures", array(unknown_features)}, {"missingExtensions", array(missing_extensions)},
    {"strictEnumerationPass", boolean(strict_ok)},
    {"dynamicRenderingCore13Available", boolean(effective >= VK_API_VERSION_1_3 && dynamic.dynamicRendering)},
    {"note", quote("strictEnumerationPass requires literal listed extension names, even where functionality is promoted to core")}})});

  uint32_t nq = 0; vkGetPhysicalDeviceQueueFamilyProperties(physical, &nq, nullptr);
  std::vector<VkQueueFamilyProperties> q(nq); if (nq) vkGetPhysicalDeviceQueueFamilyProperties(physical, &nq, q.data()); q.resize(nq);
  std::vector<std::string> queues; uint32_t graphics = UINT32_MAX;
  for (uint32_t j = 0; j < nq; ++j) {
    queues.push_back(object({{"index", num(j)}, {"flags", num(q[j].queueFlags)}, {"count", num(q[j].queueCount)}, {"timestampValidBits", num(q[j].timestampValidBits)}}));
    if (graphics == UINT32_MAX && q[j].queueCount && (q[j].queueFlags & VK_QUEUE_GRAPHICS_BIT)) graphics = j;
  }
  fields.push_back({"queueFamilies", array(queues)});
  if (graphics != UINT32_MAX) {
    float priority = 1.0f;
    VkDeviceQueueCreateInfo qc{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; qc.queueFamilyIndex = graphics; qc.queueCount = 1; qc.pQueuePriorities = &priority;
    VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; dc.queueCreateInfoCount = 1; dc.pQueueCreateInfos = &qc;
    VkDevice device = VK_NULL_HANDLE; VkResult dr = vkCreateDevice(physical, &dc, nullptr, &device);
    log_result("vkCreateDevice(baseline, no extensions/features)", dr);
    fields.push_back({"logicalDeviceBaseline", result(dr)}); if (device) vkDestroyDevice(device, nullptr);
    // Independently attempt the four critical core features and a supported control.
    // A failed vkCreateDevice has no live device to clean up; successful trials are destroyed immediately.
    std::vector<std::string> enable_trials;
    const std::vector<std::pair<const char *, VkBool32 VkPhysicalDeviceFeatures::*>> trial_features{
      {"vertexPipelineStoresAndAtomics", &VkPhysicalDeviceFeatures::vertexPipelineStoresAndAtomics},
      {"multiViewport", &VkPhysicalDeviceFeatures::multiViewport},
      {"logicOp", &VkPhysicalDeviceFeatures::logicOp},
      {"dualSrcBlend", &VkPhysicalDeviceFeatures::dualSrcBlend},
      {"geometryShader", &VkPhysicalDeviceFeatures::geometryShader}};
    for (const auto &trial : trial_features) {
      VkPhysicalDeviceFeatures single{}; single.*(trial.second) = VK_TRUE;
      dc.pEnabledFeatures = &single; device = VK_NULL_HANDLE;
      VkResult tr = vkCreateDevice(physical, &dc, nullptr, &device);
      std::string stage = std::string("vkCreateDevice(single feature ") + trial.first + ')'; log_result(stage.c_str(), tr);
      enable_trials.push_back(object({{"feature", quote(trial.first)}, {"reportedSupported", boolean(legacy.*(trial.second))},
        {"creation", result(tr)}, {"purpose", quote("single-feature cross-check; no rendering workload")}}));
      if (device) vkDestroyDevice(device, nullptr);
    }
    dc.pEnabledFeatures = nullptr;
    fields.push_back({"singleFeatureEnableTrials", array(enable_trials)});
    if (strict_ok && aggregate_queried) {
      VkPhysicalDeviceFeatures2 enabled{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
#define ENABLE10(name) enabled.features.name = VK_TRUE;
      ENABLE10(geometryShader) ENABLE10(vertexPipelineStoresAndAtomics) ENABLE10(multiViewport) ENABLE10(shaderClipDistance)
      ENABLE10(fragmentStoresAndAtomics) ENABLE10(logicOp) ENABLE10(dualSrcBlend) ENABLE10(imageCubeArray)
      ENABLE10(multiDrawIndirect) ENABLE10(drawIndirectFirstInstance)
#undef ENABLE10
      VkPhysicalDeviceVulkan11Features enabled11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES}; enabled11.shaderDrawParameters = VK_TRUE;
      VkPhysicalDeviceVulkan12Features enabled12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES}; enabled12.timelineSemaphore = VK_TRUE; enabled12.bufferDeviceAddress = VK_TRUE;
      VkPhysicalDeviceDynamicRenderingFeatures enabled_dynamic{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES}; enabled_dynamic.dynamicRendering = VK_TRUE;
      VkPhysicalDeviceProvokingVertexFeaturesEXT enabled_provoking{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROVOKING_VERTEX_FEATURES_EXT}; enabled_provoking.provokingVertexLast = VK_TRUE;
      enabled.pNext = &enabled11; enabled11.pNext = &enabled12; enabled12.pNext = &enabled_dynamic; enabled_dynamic.pNext = &enabled_provoking;
      const char *names[]{VK_KHR_SWAPCHAIN_EXTENSION_NAME, VK_KHR_DYNAMIC_RENDERING_EXTENSION_NAME, VK_EXT_PROVOKING_VERTEX_EXTENSION_NAME};
      dc.pNext = &enabled; dc.enabledExtensionCount = 3; dc.ppEnabledExtensionNames = names; device = VK_NULL_HANDLE;
      dr = vkCreateDevice(physical, &dc, nullptr, &device); log_result("vkCreateDevice(Blender required feature set)", dr);
      fields.push_back({"logicalDeviceBlenderRequired", result(dr)}); if (device) vkDestroyDevice(device, nullptr);
    } else fields.push_back({"logicalDeviceBlenderRequired", object({{"attempted", "false"}, {"reason", quote("required capability checklist did not pass")}})});
  } else fields.push_back({"logicalDeviceBaseline", object({{"attempted", "false"}, {"reason", quote("no graphics queue")}})});
  return object(fields);
}

std::string run(uint32_t requested_api, const char *context, int &status) {
  Fields root{{"schemaVersion", "1"}, {"probe", quote("blender-ohos Vulkan native ARM64 probe")},
    {"processContext", quote(context)}, {"processId", num(getpid())},
    {"os", os_info()}, {"loader", loader_info()},
    {"wsi", object({{"verified", "false"}, {"nativeWindowProvided", "false"},
      {"surfaceCreated", "false"}, {"swapchainCreated", "false"},
      {"reason", quote("No OHNativeWindow; surface-extension enumeration is not WSI or presentation validation")}})}};
  uint32_t loader_api = VK_API_VERSION_1_0;
  auto enumerate_version = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkEnumerateInstanceVersion"));
  VkResult vr = enumerate_version ? enumerate_version(&loader_api) : VK_SUCCESS;
  log_result("vkEnumerateInstanceVersion", vr);
  root.push_back({"instanceVersionEnumeration", result(vr)}); root.push_back({"loaderApiVersion", object({{"raw", num(loader_api)}, {"text", quote(version(loader_api))}})});
  std::vector<VkExtensionProperties> ext;
  VkResult er = enumerate(ext, [](uint32_t *n, VkExtensionProperties *a) { return vkEnumerateInstanceExtensionProperties(nullptr, n, a); });
  log_result("vkEnumerateInstanceExtensionProperties", er);
  root.push_back({"instanceExtensionEnumeration", result(er)}); root.push_back({"instanceExtensions", extensions(ext)});
  std::vector<VkLayerProperties> layers;
  VkResult lr = enumerate(layers, [](uint32_t *n, VkLayerProperties *a) { return vkEnumerateInstanceLayerProperties(n, a); });
  std::vector<std::string> layer_json;
  for (const auto &l : layers) layer_json.push_back(object({{"name", quote(l.layerName)}, {"specVersion", quote(version(l.specVersion))}, {"implementationVersion", num(l.implementationVersion)}, {"description", quote(l.description)}}));
  root.push_back({"instanceLayerEnumeration", result(lr)}); root.push_back({"instanceLayers", array(layer_json)});
  bool surface = er == VK_SUCCESS && has(ext, VK_KHR_SURFACE_EXTENSION_NAME);
  bool ohos = er == VK_SUCCESS && has(ext, VK_OHOS_SURFACE_EXTENSION_NAME);
  uint32_t api = requested_api ? requested_api : std::min(loader_api, static_cast<uint32_t>(VK_API_VERSION_1_3));
  root.push_back({"requestedApiVersion", quote(version(api))});
  std::vector<const char *> enabled;
  if (surface) enabled.push_back(VK_KHR_SURFACE_EXTENSION_NAME);
  if (ohos && surface) enabled.push_back(VK_OHOS_SURFACE_EXTENSION_NAME);
  bool features2_khr = api < VK_API_VERSION_1_1 && er == VK_SUCCESS && has(ext, VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME);
  if (features2_khr) enabled.push_back(VK_KHR_GET_PHYSICAL_DEVICE_PROPERTIES_2_EXTENSION_NAME);
  std::vector<std::string> enabled_json; for (auto e : enabled) enabled_json.push_back(quote(e));
  root.push_back({"enabledInstanceExtensions", array(enabled_json)});
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.pApplicationName = "Blender OHOS Vulkan capability probe"; app.applicationVersion = 1; app.pEngineName = "No engine"; app.apiVersion = api;
  VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ci.pApplicationInfo = &app; ci.enabledExtensionCount = enabled.size(); ci.ppEnabledExtensionNames = enabled.data();
  VkInstance instance = VK_NULL_HANDLE; VkResult ir = vkCreateInstance(&ci, nullptr, &instance); log_result("vkCreateInstance", ir);
  root.push_back({"instanceCreation", result(ir)});
  status = 2;
  if (ir != VK_SUCCESS) {
    root.push_back({"status", quote("instance_initialization_failed")}); root.push_back({"devices", "[]"});
    root.push_back({"conclusion", quote("No physical-device capability conclusion: this process/loader environment could not initialize Vulkan. HAP context must be tested independently with default permissions; do not label the GPU unsupported.")});
    return object(root);
  }
  std::vector<VkPhysicalDevice> devices;
  VkResult pr = enumerate(devices, [instance](uint32_t *n, VkPhysicalDevice *a) { return vkEnumeratePhysicalDevices(instance, n, a); });
  log_result("vkEnumeratePhysicalDevices", pr); root.push_back({"physicalDeviceEnumeration", result(pr)});
  std::vector<std::string> reports;
  if (pr == VK_SUCCESS) for (auto d : devices) reports.push_back(device_info(instance, d, api, features2_khr, surface, ohos));
  root.push_back({"devices", array(reports)});
  status = pr == VK_SUCCESS ? (devices.empty() ? 3 : 0) : 2;
  root.push_back({"status", quote(status == 0 ? "physical_devices_enumerated" : status == 3 ? "no_physical_devices_in_this_process" : "physical_device_enumeration_failed")});
  root.push_back({"conclusion", quote(status == 0 ? "Only enumeration and explicitly recorded logical-device tests are verified. No render workload or WSI/presentation was tested; HAP execution was not inferred from this process label." : "Do not infer hardware incompatibility from process-context initialization/enumeration failure; independently test default-permission HAP context.")});
  vkDestroyInstance(instance, nullptr);
  return object(root);
}
} // namespace
extern "C" char *blender_ohos_vulkan_report(uint32_t requested_api_version, const char *process_context, int *enumeration_status) {
  int status = 2;
  std::string report = run(requested_api_version, process_context ? process_context : "unspecified", status);
  if (enumeration_status) *enumeration_status = status;
  char *out = static_cast<char *>(std::malloc(report.size() + 1));
  if (out) std::memcpy(out, report.c_str(), report.size() + 1);
  return out;
}
#ifndef BLENDER_VULKAN_PROBE_NO_MAIN
int main(int argc, char **argv) {
  uint32_t api = 0; const char *context = "terminal_child_process";
  for (int j = 1; j < argc; ++j) {
    if (std::strcmp(argv[j], "--api") == 0 && j + 1 < argc) {
      const char *s = argv[++j];
      if (std::strcmp(s, "1.0") == 0) api = VK_API_VERSION_1_0;
      else if (std::strcmp(s, "1.1") == 0) api = VK_API_VERSION_1_1;
      else if (std::strcmp(s, "1.2") == 0) api = VK_API_VERSION_1_2;
      else if (std::strcmp(s, "1.3") == 0) api = VK_API_VERSION_1_3;
      else { std::fprintf(stderr, "Invalid API; choose 1.0, 1.1, 1.2, or 1.3\n"); return 64; }
    } else if (std::strcmp(argv[j], "--context") == 0 && j + 1 < argc) context = argv[++j];
    else { std::fprintf(stderr, "Usage: vulkan-probe [--api 1.0|1.1|1.2|1.3] [--context label]\n"); return 64; }
  }
  int status = 2; char *report = blender_ohos_vulkan_report(api, context, &status);
  if (!report) { std::fprintf(stderr, "JSON allocation failed\n"); return 70; }
  std::puts(report); std::free(report); return status;
}
#endif
