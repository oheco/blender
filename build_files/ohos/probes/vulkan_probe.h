// SPDX-License-Identifier: MIT
#ifndef BLENDER_OHOS_VULKAN_PROBE_H
#define BLENDER_OHOS_VULKAN_PROBE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// Returns a malloc-owned UTF-8 JSON string; the caller must free() it.
// requested_api_version=0 selects min(loader version, Vulkan 1.3).
// enumeration_status: 0=devices enumerated, 2=initialization/enumeration failed,
// 3=no devices. This does not encode Blender capability compatibility.
// process_context is a caller-supplied label, not an assertion of HAP validation.
char *blender_ohos_vulkan_report(uint32_t requested_api_version,
                               const char *process_context,
                               int *enumeration_status);
#ifdef __cplusplus
}
#endif
#endif
