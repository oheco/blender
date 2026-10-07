#define VK_USE_PLATFORM_OHOS
#include <vulkan/utility/vk_safe_struct.hpp>
#include <vulkan/layer/vk_layer_settings.h>
#include <vulkan/vk_enum_string_helper.h>
#include <cstdio>
#include <cstdint>
#include <cstring>

int main()
{
  VkSurfaceCreateInfoOHOS info{VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS};
  info.window=reinterpret_cast<OHNativeWindow *>(uintptr_t(0x1234)); // Copy-only fixture, never a runtime surface call.
  vku::safe_VkSurfaceCreateInfoOHOS safe(&info), copied(safe);
  if(safe.sType!=VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS || copied.window!=info.window || safe.flags!=0 || safe.pNext) return 1;
  if(std::strcmp(string_VkStructureType(info.sType),"VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS")) return 2;
  VkPhysicalDeviceShaderTileImageFeaturesEXT tile{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_TILE_IMAGE_FEATURES_EXT};
  tile.shaderTileImageColorReadAccess=VK_TRUE;
  VkPhysicalDeviceFeatures2 features{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2}; features.pNext=&tile;
  vku::safe_VkPhysicalDeviceFeatures2 safefeatures(&features);
  auto *copiedtile=reinterpret_cast<const VkPhysicalDeviceShaderTileImageFeaturesEXT *>(safefeatures.pNext);
  if(!copiedtile || copiedtile==&tile || copiedtile->sType!=tile.sType || !copiedtile->shaderTileImageColorReadAccess) return 3;
  VkBool32 expected=VK_TRUE;
  VkLayerSettingEXT setting{"VK_LAYER_OHECO_library_fixture","fixture_bool",VK_LAYER_SETTING_TYPE_BOOL32_EXT,1,&expected};
  VkLayerSettingsCreateInfoEXT create{VK_STRUCTURE_TYPE_LAYER_SETTINGS_CREATE_INFO_EXT}; create.settingCount=1; create.pSettings=&setting;
  VkuLayerSettingSet set{};
  if(vkuCreateLayerSettingSet(setting.pLayerName,&create,nullptr,nullptr,&set)!=VK_SUCCESS) return 4;
  if(!vkuHasLayerSetting(set,setting.pSettingName)) return 5;
  VkBool32 actual=VK_FALSE; uint32_t count=1;
  if(vkuGetLayerSettingValues(set,setting.pSettingName,VKU_LAYER_SETTING_TYPE_BOOL32,&count,&actual)!=VK_SUCCESS || count!=1 || actual!=expected) return 6;
  vkuDestroyLayerSettingSet(set,nullptr);
  std::puts("{\"safeStructOHOSSurface\":true,\"safePNextTileFeatureCopy\":true,\"enumStringOHOS\":true,\"layerSettingsLibrary\":true}");
  return 0;
}
