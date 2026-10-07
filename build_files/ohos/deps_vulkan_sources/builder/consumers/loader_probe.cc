#define VK_USE_PLATFORM_OHOS
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <stdexcept>

static std::string quote(const char *s)
{
  std::string out = "\"";
  for (const unsigned char c : std::string(s ? s : "")) {
    if (c == '"' || c == '\\') { out += '\\'; out += char(c); }
    else if (c < 32) { char b[8]; std::snprintf(b,sizeof(b),"\\u%04x",unsigned(c)); out += b; }
    else out += char(c);
  }
  return out + '"';
}
static void check(VkResult r, const char *where) { if (r != VK_SUCCESS) throw std::runtime_error(std::string(where)+":"+std::to_string(r)); }
int main(int argc, char** argv)
{
  try {
    Dl_info info{};
    if (!dladdr(reinterpret_cast<void *>(vkGetInstanceProcAddr), &info) || !info.dli_fname) throw std::runtime_error("dladdr loader");
    const std::string loader(info.dli_fname);
    if (argc != 2 || loader != argv[1]) throw std::runtime_error("Unexpected runtime loader path: " + loader);
    uint32_t version = VK_API_VERSION_1_0;
    auto enumerate = reinterpret_cast<PFN_vkEnumerateInstanceVersion>(vkGetInstanceProcAddr(VK_NULL_HANDLE,"vkEnumerateInstanceVersion"));
    if (enumerate) check(enumerate(&version), "vkEnumerateInstanceVersion");
    if (version < VK_API_VERSION_1_3) throw std::runtime_error("Vulkan1.3 unavailable");
    VkApplicationInfo application{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    application.pApplicationName = "Blender OHOS fixed dependency native probe";
    application.apiVersion = VK_API_VERSION_1_3;
    VkInstanceCreateInfo create{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; create.pApplicationInfo = &application;
    VkInstance instance{}; check(vkCreateInstance(&create,nullptr,&instance),"vkCreateInstance");
    uint32_t count = 0; check(vkEnumeratePhysicalDevices(instance,&count,nullptr),"vkEnumeratePhysicalDevices count");
    std::vector<VkPhysicalDevice> devices(count); check(vkEnumeratePhysicalDevices(instance,&count,devices.data()),"vkEnumeratePhysicalDevices");
    std::printf("{\"runtime_loader\":%s,\"instance_api\":\"%u.%u.%u\",\"header_api\":\"1.4.%u\",\"devices\":[",quote(loader.c_str()).c_str(),VK_API_VERSION_MAJOR(version),VK_API_VERSION_MINOR(version),VK_API_VERSION_PATCH(version),VK_HEADER_VERSION);
    for (uint32_t i=0;i<count;i++) {
      uint32_t extcount=0; check(vkEnumerateDeviceExtensionProperties(devices[i],nullptr,&extcount,nullptr),"device extensions count");
      std::vector<VkExtensionProperties> exts(extcount); check(vkEnumerateDeviceExtensionProperties(devices[i],nullptr,&extcount,exts.data()),"device extensions");
      bool tile=false; for (auto &ext:exts) if (std::strcmp(ext.extensionName,VK_EXT_SHADER_TILE_IMAGE_EXTENSION_NAME)==0) tile=true;
      VkPhysicalDeviceDriverProperties driver{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
      VkPhysicalDeviceProperties2 props{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2}; props.pNext=&driver;
      vkGetPhysicalDeviceProperties2(devices[i],&props);
      VkPhysicalDeviceShaderTileImageFeaturesEXT tilefeatures{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_TILE_IMAGE_FEATURES_EXT};
      VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2}; if(tile) features.pNext=&tilefeatures;
      vkGetPhysicalDeviceFeatures2(devices[i],&features);
      std::printf("%s{\"name\":%s,\"api\":\"%u.%u.%u\",\"vendorID\":%u,\"deviceID\":%u,\"driverVersion\":%u,\"driverID\":%u,\"driverName\":%s,\"driverInfo\":%s,\"dualSrcBlend\":%s,\"shaderTileImageExtension\":%s,\"shaderTileImageColorReadAccess\":%s,\"shaderTileImageDepthReadAccess\":%s,\"shaderTileImageStencilReadAccess\":%s}",
                  i ? "," : "",quote(props.properties.deviceName).c_str(),VK_API_VERSION_MAJOR(props.properties.apiVersion),VK_API_VERSION_MINOR(props.properties.apiVersion),VK_API_VERSION_PATCH(props.properties.apiVersion),props.properties.vendorID,props.properties.deviceID,props.properties.driverVersion,unsigned(driver.driverID),quote(driver.driverName).c_str(),quote(driver.driverInfo).c_str(),features.features.dualSrcBlend?"true":"false",tile?"true":"false",tilefeatures.shaderTileImageColorReadAccess?"true":"false",tilefeatures.shaderTileImageDepthReadAccess?"true":"false",tilefeatures.shaderTileImageStencilReadAccess?"true":"false");
    }
    std::puts("]}"); vkDestroyInstance(instance,nullptr);
    if (!count) throw std::runtime_error("No physical devices");
    return 0;
  } catch(const std::exception &e) { std::fprintf(stderr,"loader-probe failure: %s\n",e.what()); return 1; }
}
