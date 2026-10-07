/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef BLENDER_CREATOR_OHOS_H
#define BLENDER_CREATOR_OHOS_H
#include "GHOST_OHOSHost.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct BlenderOHOSSession BlenderOHOSSession;
#define BLENDER_OHOS_CREATOR_ABI_VERSION 2u
/* Creator-specific continuations. Neither permits destroying the owned session.
 * They do not change the GHOST queue result enumeration. */
enum { BLENDER_OHOS_DRAINING = 3, BLENDER_OHOS_DRAIN_ERROR = 4 };
/* These are synchronous ENGINE-THREAD callbacks. No exceptions cross this ABI.
 * The host is bound/installed before initialize, and remains installed through teardown.
 * Exactly one Blender lifetime is supported per process; restart requires a new ability process.
 * argv[0] is a host label; resource locations come exclusively from installed host paths.
 * Background diagnostics may supply actual Blender -b/--python arguments. HAP supplies fixed GUI args. */
GHOST_OHOS_EXPORT int32_t blender_ohos_initialize(GHOST_OHOSHost *host, int argc,
    const char **argv, BlenderOHOSSession **session) GHOST_OHOS_NOEXCEPT;
/* ENGINE thread only. OK runs WM; DRAINING/DRAIN_ERROR continue bounded shutdown
 * ticks even after stop/init failure/WM quit. EMPTY alone permits teardown:
 * lifecycle ready=true AND accepting=false. Never break->destroy on non-OK. */
GHOST_OHOS_EXPORT int32_t blender_ohos_pump(BlenderOHOSSession *session, int foreground) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t blender_ohos_stop(BlenderOHOSSession *session) GHOST_OHOS_NOEXCEPT;
/* Pass the owned slot. Pending/Error leaves *session unchanged; successful
 * physical teardown returns OK, clears it and fills the independent exit_code.
 * Return is STATUS ONLY. Never call from ArkUI; queue stop to ENGINE instead. */
GHOST_OHOS_EXPORT int32_t blender_ohos_teardown(BlenderOHOSSession **session, int32_t *exit_code) GHOST_OHOS_NOEXCEPT;
#ifdef __cplusplus
}
#endif
#endif
