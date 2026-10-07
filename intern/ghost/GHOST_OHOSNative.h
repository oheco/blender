/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef GHOST_OHOS_NATIVE_H
#define GHOST_OHOS_NATIVE_H
#include "GHOST_OHOSHost.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Printable physical keys use ASCII upper-case. Special keys are stable ABI
 * values independent of GHOST_TKey and independent of OHOS native key numbers. */
typedef enum GHOST_OHOSKey {
  GHOST_OHOS_KEY_UNKNOWN = 0,
  GHOST_OHOS_KEY_BACKSPACE = 256, GHOST_OHOS_KEY_TAB, GHOST_OHOS_KEY_ENTER, GHOST_OHOS_KEY_ESCAPE,
  GHOST_OHOS_KEY_LSHIFT, GHOST_OHOS_KEY_RSHIFT, GHOST_OHOS_KEY_LCTRL, GHOST_OHOS_KEY_RCTRL,
  GHOST_OHOS_KEY_LALT, GHOST_OHOS_KEY_RALT, GHOST_OHOS_KEY_LMETA, GHOST_OHOS_KEY_RMETA,
  GHOST_OHOS_KEY_CAPSLOCK, GHOST_OHOS_KEY_NUMLOCK, GHOST_OHOS_KEY_SCROLLLOCK,
  GHOST_OHOS_KEY_LEFT, GHOST_OHOS_KEY_RIGHT, GHOST_OHOS_KEY_UP, GHOST_OHOS_KEY_DOWN,
  GHOST_OHOS_KEY_INSERT, GHOST_OHOS_KEY_DELETE, GHOST_OHOS_KEY_HOME, GHOST_OHOS_KEY_END,
  GHOST_OHOS_KEY_PAGEUP, GHOST_OHOS_KEY_PAGEDOWN, GHOST_OHOS_KEY_PRINTSCREEN,
  GHOST_OHOS_KEY_PAUSE, GHOST_OHOS_KEY_MENU,
  GHOST_OHOS_KEY_NUMPAD0 = 320, GHOST_OHOS_KEY_NUMPAD9 = 329,
  GHOST_OHOS_KEY_NUMPAD_PERIOD, GHOST_OHOS_KEY_NUMPAD_ENTER, GHOST_OHOS_KEY_NUMPAD_PLUS,
  GHOST_OHOS_KEY_NUMPAD_MINUS, GHOST_OHOS_KEY_NUMPAD_MULTIPLY, GHOST_OHOS_KEY_NUMPAD_DIVIDE,
  GHOST_OHOS_KEY_F1 = 352, GHOST_OHOS_KEY_F12 = 363
} GHOST_OHOSKey;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_retain(void *userdata, void *window) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_release(void *userdata, void *window) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_geometry(void *window, uint32_t *width, uint32_t *height) GHOST_OHOS_NOEXCEPT;
/* malloc result; caller frees. READ_PASTEBOARD permission/interaction checked by
 * OS. No hidden clipboard cache substitutes for a denied native read. */
GHOST_OHOS_EXPORT char *ghost_ohos_native_clipboard_get(void) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_clipboard_put(const char *text) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_key(int32_t native_code) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_button(int32_t native_button) GHOST_OHOS_NOEXCEPT;
struct Input_KeyEvent;
struct Input_MouseEvent;
struct Input_TouchEvent;
/* Decode ephemeral NDK event data into an OWNED POD event before enqueue/retry.
 * Mouse display coordinates need explicit physical-pixel client origin.
 * Signed axis-to-detent factors are a host policy, not guessed native units.
 * EMPTY means begin/end/unsupported source action with no discrete event. */
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_decode_key(const struct Input_KeyEvent *,
    uint64_t id, uint64_t generation, int32_t repeat, GHOST_OHOSEvent *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_decode_mouse(const struct Input_MouseEvent *,
    uint64_t id, uint64_t generation, int32_t origin_x, int32_t origin_y,
    float wheel_x_factor, float wheel_y_factor, GHOST_OHOSEvent *) GHOST_OHOS_NOEXCEPT;
GHOST_OHOS_EXPORT int32_t ghost_ohos_native_decode_touch(const struct Input_TouchEvent *,
    uint64_t id, uint64_t generation, GHOST_OHOSEvent *) GHOST_OHOS_NOEXCEPT;
#ifdef __cplusplus
}
#endif
#endif
