/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef GHOST_OHOS_HOST_H
#define GHOST_OHOS_HOST_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
#  define GHOST_OHOS_NOEXCEPT noexcept
extern "C" {
#else
#  define GHOST_OHOS_NOEXCEPT
#endif
#if defined(__GNUC__) || defined(__clang__)
#  define GHOST_OHOS_EXPORT __attribute__((visibility("default")))
#else
#  define GHOST_OHOS_EXPORT
#endif
#define GHOST_OHOS_ABI_VERSION 1u
#define GHOST_OHOS_PATH_MAX 4096u
#define GHOST_OHOS_TEXT_MAX 256u
#define GHOST_OHOS_MAX_WINDOWS 16u
typedef struct GHOST_OHOSHost GHOST_OHOSHost;
typedef enum GHOST_OHOSResult {
  GHOST_OHOS_OK = 0, GHOST_OHOS_EMPTY = 1, GHOST_OHOS_FULL = 2,
  GHOST_OHOS_INVALID = -1, GHOST_OHOS_UNAVAILABLE = -2, GHOST_OHOS_WRONG_THREAD = -3,
  GHOST_OHOS_BUSY = -4, GHOST_OHOS_CLOSED = -5, GHOST_OHOS_NO_MEMORY = -6,
  GHOST_OHOS_CALLBACK_FAILED = -7
} GHOST_OHOSResult;
typedef enum GHOST_OHOSEventType {
  GHOST_OHOS_EVENT_ATTACHED = 1, GHOST_OHOS_EVENT_DETACHED,
  GHOST_OHOS_EVENT_RESIZE, GHOST_OHOS_EVENT_FOCUS, GHOST_OHOS_EVENT_CLOSE,
  GHOST_OHOS_EVENT_MOTION, GHOST_OHOS_EVENT_BUTTON, GHOST_OHOS_EVENT_WHEEL,
  GHOST_OHOS_EVENT_KEY, GHOST_OHOS_EVENT_TEXT, GHOST_OHOS_EVENT_TOUCH,
  GHOST_OHOS_EVENT_CANCEL_INPUT
} GHOST_OHOSEventType;
typedef enum GHOST_OHOSButton {
  GHOST_OHOS_BUTTON_LEFT = 1, GHOST_OHOS_BUTTON_MIDDLE = 2, GHOST_OHOS_BUTTON_RIGHT = 3,
  GHOST_OHOS_BUTTON_BACK = 4, GHOST_OHOS_BUTTON_FORWARD = 5
} GHOST_OHOSButton;
typedef enum GHOST_OHOSTouchAction {
  GHOST_OHOS_TOUCH_DOWN = 1, GHOST_OHOS_TOUCH_MOVE = 2,
  GHOST_OHOS_TOUCH_UP = 3, GHOST_OHOS_TOUCH_CANCEL = 4
} GHOST_OHOSTouchAction;
/* Native OHOS key codes, not Linux/Android key numbers. Key TEXT must be separately
 * committed through EVENT_TEXT; do not duplicate IME commits in physical KEY events. */
typedef struct GHOST_OHOSEvent {
  uint32_t struct_size, type;
  uint64_t window_id, generation, sequence, timestamp_ms;
  int32_t x, y, value, pressed, repeat, touch_id;
  float wheel_x, wheel_y, scale;
  uint32_t width, height, dpi, text_bytes;
  char text[GHOST_OHOS_TEXT_MAX];
} GHOST_OHOSEvent;
typedef struct GHOST_OHOSWindowInfo {
  uint64_t window_id, generation;
  void *native_window; /* OHNativeWindow; held until engine releases its lease. */
  uint32_t width, height, dpi;
  float scale; /* pixel / logical unit; input coordinates are already physical pixels. */
} GHOST_OHOSWindowInfo;
typedef struct GHOST_OHOSPaths {
  char runtime[GHOST_OHOS_PATH_MAX], config[GHOST_OHOS_PATH_MAX];
  char cache[GHOST_OHOS_PATH_MAX], temp[GHOST_OHOS_PATH_MAX];
} GHOST_OHOSPaths;
typedef enum GHOST_OHOSCommandType {
  GHOST_OHOS_COMMAND_CREATE = 1, GHOST_OHOS_COMMAND_TITLE, GHOST_OHOS_COMMAND_SIZE,
  GHOST_OHOS_COMMAND_STATE, GHOST_OHOS_COMMAND_ORDER, GHOST_OHOS_COMMAND_DESTROY,
  /* Additive commands preserve ABI1 layout. BEGIN text is "x,y" in physical
   * client pixels (top-left), width/height the caret rectangle; END has no text. */
  GHOST_OHOS_COMMAND_IME_BEGIN, GHOST_OHOS_COMMAND_IME_END
} GHOST_OHOSCommandType;
typedef struct GHOST_OHOSCommand {
  uint32_t struct_size, type;
  uint64_t window_id, parent_id;
  uint32_t width, height;
  int32_t value;
  const char *text; /* borrowed UTF8, valid only for the callback duration */
} GHOST_OHOSCommand;
typedef struct GHOST_OHOSCallbacks {
  uint32_t struct_size;
  void *userdata;
  /* These run under the host lifetime lock. Must not reenter host or throw.
   * The UI owner must NOT destroy/unreference its XComponent window until
   * detach -> engine destroys Vulkan context -> wait_detached returns OK.
   * SDK ref/unref are NON-thread-safe; this ordering is mandatory. */
  int32_t (*native_retain)(void *userdata, void *window);
  int32_t (*native_release)(void *userdata, void *window);
  /* Engine thread only, never under the host lock. Marshal ArkUI operations
   * asynchronously; never invoke bpy/GHOST on UI. CREATE may return only an
   * ALREADY ATTACHED surface ID, otherwise return UNAVAILABLE (not success). */
  int32_t (*command)(void *userdata, const GHOST_OHOSCommand *, uint64_t *created_id);
} GHOST_OHOSCallbacks;
typedef struct GHOST_OHOSConfig {
  uint32_t struct_size, abi_version, queue_capacity;
  const char *runtime_path, *config_path, *cache_path, *temp_path;
  GHOST_OHOSCallbacks callbacks;
} GHOST_OHOSConfig;
typedef struct GHOST_OHOSStats { uint64_t accepted, coalesced, full, pending; } GHOST_OHOSStats;

GHOST_OHOS_EXPORT int32_t ghost_ohos_host_create(const GHOST_OHOSConfig *, GHOST_OHOSHost **) GHOST_OHOS_NOEXCEPT;
/* Stop producers, dispose GHOST on engine thread, join engine, then destroy.
 * BUSY leaves the host untouched when window leases still exist. No implicit thread creation. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_destroy(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_bind_engine(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_is_engine(const GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
/* Launcher pins the host across pthread startup/join, preventing destruction. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_begin_run(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_end_run(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_wake(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_attach(GHOST_OHOSHost *, const GHOST_OHOSWindowInfo *, uint64_t *generation) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_detach(GHOST_OHOSHost *, uint64_t id, uint64_t generation) GHOST_OHOS_NOEXCEPT;
/* Full lifecycle/input transitions are never silently dropped: FULL means retry
 * in the host bridge. Only consecutive same-window motion/resize/touch-MOVE are coalesced.
 * generation prevents old XComponent callbacks targeting a reused ID. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_push(GHOST_OHOSHost *, const GHOST_OHOSEvent *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_pop(GHOST_OHOSHost *, GHOST_OHOSEvent *, uint32_t timeout_ms) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_acquire_window(GHOST_OHOSHost *, uint64_t id, GHOST_OHOSWindowInfo *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_release_window(GHOST_OHOSHost *, uint64_t id, uint64_t generation) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_ack_detach(GHOST_OHOSHost *, uint64_t id, uint64_t generation) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_wait_detached(GHOST_OHOSHost *, uint64_t id, uint64_t generation, uint32_t timeout_ms) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_set_main_window(GHOST_OHOSHost *, uint64_t id) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT uint64_t ghost_ohos_host_main_window(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_get_window(GHOST_OHOSHost *, uint64_t id, GHOST_OHOSWindowInfo *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_get_paths(GHOST_OHOSHost *, GHOST_OHOSPaths *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_get_stats(GHOST_OHOSHost *, GHOST_OHOSStats *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_command(GHOST_OHOSHost *, const GHOST_OHOSCommand *, uint64_t *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT uint64_t ghost_ohos_monotonic_ms(void) GHOST_OHOS_NOEXCEPT;
/* Before GHOST factory creation: install explicitly on engine thread. Clear only
 * after System and SystemPaths are disposed. Lifetime owned by the embedding host. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_host_install(GHOST_OHOSHost *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT GHOST_OHOSHost *ghost_ohos_host_installed(void) GHOST_OHOS_NOEXCEPT;
#ifdef __cplusplus
}
#endif
#endif
