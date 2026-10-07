/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef GHOST_OHOS_ENGINE_H
#define GHOST_OHOS_ENGINE_H
#include "GHOST_OHOSHost.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct GHOST_OHOSEngine GHOST_OHOSEngine;
/* Called ONLY on the explicitly created pthread. All Blender/GHOST/Python init,
 * pumping, and teardown must fit inside this callback. No UI-thread bpy entry.
 * Callback must check stop_requested and dispose System and SystemPaths before
 * returning. This is a host launcher interface, not a claimed Blender entrypoint. */
typedef int32_t (*GHOST_OHOSEngineRun)(GHOST_OHOSHost *, GHOST_OHOSEngine *, void *userdata);
/* One run per host; default 32MiB stack (explicit, not musl's small default).
 * Never launches a GUI or subprocess by itself; starts only the supplied callback. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_engine_start(GHOST_OHOSHost *, GHOST_OHOSEngineRun, void *userdata,
                               size_t stack_bytes, GHOST_OHOSEngine **) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_engine_request_stop(GHOST_OHOSEngine *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_engine_stop_requested(const GHOST_OHOSEngine *) GHOST_OHOS_NOEXCEPT;
/* Join outside ArkUI callbacks on a lifecycle coordinator; do not synchronously
 * wait for UI-marshaled commands from the engine while joining it on UI.
 * Consumes engine handle on success and stores callback/bootstrap result. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_engine_join(GHOST_OHOSEngine *, int32_t *run_result) GHOST_OHOS_NOEXCEPT;
#ifdef __cplusplus
}
#endif
#endif
