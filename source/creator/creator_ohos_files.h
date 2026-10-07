/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef BLENDER_CREATOR_OHOS_FILES_H
#define BLENDER_CREATOR_OHOS_FILES_H
#include "creator_ohos.h"
#ifdef __cplusplus
extern "C" {
#endif
#define BLENDER_OHOS_FILE_ABI 1u
#define BLENDER_OHOS_FILE_MESSAGE_MAX 512u
#define BLENDER_OHOS_FILE_PATH_MAX 1024u /* Must equal Blender FILE_MAX; never truncate RNA paths. */
typedef enum BlenderOHOSFileOperation {
  BLENDER_OHOS_OPEN_BLEND = 1, BLENDER_OHOS_SAVE_BLEND = 2,
  BLENDER_OHOS_IMPORT_OBJ = 3, BLENDER_OHOS_EXPORT_OBJ = 4,
  BLENDER_OHOS_IMPORT_GLB = 5, BLENDER_OHOS_EXPORT_GLB = 6
} BlenderOHOSFileOperation;
typedef struct BlenderOHOSFileRequest {
  uint32_t struct_size, abi_version, operation, reserved;
  uint64_t request_id;
  /* Owned UTF8 absolute path: host.config/exchange/<UUID>/source.* for input,
   * host.temp/exchange/<UUID>/asset.* for output.
   * Never a picker URI, Python expression, borrowed JS string or arbitrary argv. */
  char filepath[BLENDER_OHOS_FILE_PATH_MAX];
} BlenderOHOSFileRequest;
typedef struct BlenderOHOSFileResult {
  uint32_t struct_size, operator_flags;
  uint64_t request_id;
  /* 0 FINISHED, 1 CANCELLED, negative real failure (GHOST_OHOS_* values). */
  int32_t status;
  char message[BLENDER_OHOS_FILE_MESSAGE_MAX];
} BlenderOHOSFileResult;
/* Synchronous controlled ENGINE-THREAD API; called at a WM frame boundary only.
 * Uses real registered operators/RNA typed values, contains all C++ exceptions.
 * Return and result.status agree. No modal/file-browser invocation or UI-thread bpy. */
GHOST_OHOS_EXPORT int32_t blender_ohos_file_command(BlenderOHOSSession *,
    const BlenderOHOSFileRequest *, BlenderOHOSFileResult *) GHOST_OHOS_NOEXCEPT;
#ifdef __cplusplus
}
#endif
#endif
