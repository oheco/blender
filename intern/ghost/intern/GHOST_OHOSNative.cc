/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_OHOSNative.h"
#include <native_window/external_window.h>
#include <multimodalinput/oh_key_code.h>
#include <multimodalinput/oh_input_manager.h>
#include <cmath>
#include <database/pasteboard/oh_pasteboard.h>
#include <database/udmf/udmf.h>
#include <database/udmf/uds.h>
#include <cstdlib>
#include <cstring>
#include <memory>
extern "C" int32_t ghost_ohos_native_retain(void *, void *window) noexcept {
  return window ? OH_NativeWindow_NativeObjectReference(window) : GHOST_OHOS_INVALID;
}
extern "C" int32_t ghost_ohos_native_release(void *, void *window) noexcept {
  /* Paired ONLY with NativeObjectReference, never destroy an XComponent borrowed window. */
  return window ? OH_NativeWindow_NativeObjectUnreference(window) : GHOST_OHOS_INVALID;
}
extern "C" int32_t ghost_ohos_native_geometry(void *window, uint32_t *width, uint32_t *height) noexcept {
  if (!window || !width || !height) return GHOST_OHOS_INVALID;
  int32_t w = 0, h = 0;
  const int32_t result = OH_NativeWindow_NativeWindowHandleOpt(
      static_cast<OHNativeWindow *>(window), GET_BUFFER_GEOMETRY, &h, &w);
  if (result != 0) return result;
  if (w <= 0 || h <= 0) return GHOST_OHOS_UNAVAILABLE;
  *width = uint32_t(w); *height = uint32_t(h); return GHOST_OHOS_OK;
}
extern "C" char *ghost_ohos_native_clipboard_get(void) noexcept {
  std::unique_ptr<OH_Pasteboard, decltype(&OH_Pasteboard_Destroy)> board(OH_Pasteboard_Create(), OH_Pasteboard_Destroy);
  if (!board || !OH_Pasteboard_HasType(board.get(), "text/plain")) return nullptr;
  int status = 0;
  std::unique_ptr<OH_UdmfData, decltype(&OH_UdmfData_Destroy)> data(OH_Pasteboard_GetData(board.get(), &status), OH_UdmfData_Destroy);
  if (!data || status != 0) return nullptr;
  std::unique_ptr<OH_UdsPlainText, decltype(&OH_UdsPlainText_Destroy)> plain(OH_UdsPlainText_Create(), OH_UdsPlainText_Destroy);
  if (!plain) return nullptr;
  const int count = OH_UdmfData_GetRecordCount(data.get());
  for (int index = 0; index < count; ++index) {
    OH_UdmfRecord *record = OH_UdmfData_GetRecord(data.get(), unsigned(index)); /* borrowed */
    if (!record || OH_UdmfRecord_GetPlainText(record, plain.get()) != 0) continue;
    const char *text = OH_UdsPlainText_GetContent(plain.get());
    if (!text) continue;
    constexpr size_t max_bytes = 16 * 1024 * 1024;
    const size_t len = strnlen(text, max_bytes);
    if (len == max_bytes) return nullptr;
    char *copy = static_cast<char *>(std::malloc(len + 1));
    if (copy) std::memcpy(copy, text, len + 1);
    return copy;
  }
  return nullptr;
}
extern "C" int32_t ghost_ohos_native_clipboard_put(const char *text) noexcept {
  if (!text) return GHOST_OHOS_INVALID;
  std::unique_ptr<OH_Pasteboard, decltype(&OH_Pasteboard_Destroy)> board(OH_Pasteboard_Create(), OH_Pasteboard_Destroy);
  std::unique_ptr<OH_UdmfData, decltype(&OH_UdmfData_Destroy)> data(OH_UdmfData_Create(), OH_UdmfData_Destroy);
  std::unique_ptr<OH_UdmfRecord, decltype(&OH_UdmfRecord_Destroy)> record(OH_UdmfRecord_Create(), OH_UdmfRecord_Destroy);
  std::unique_ptr<OH_UdsPlainText, decltype(&OH_UdsPlainText_Destroy)> plain(OH_UdsPlainText_Create(), OH_UdsPlainText_Destroy);
  if (!board || !data || !record || !plain) return GHOST_OHOS_NO_MEMORY;
  int status = OH_UdsPlainText_SetContent(plain.get(), text);
  if (status == 0) status = OH_UdmfRecord_AddPlainText(record.get(), plain.get());
  if (status == 0) status = OH_UdmfData_AddRecord(data.get(), record.get());
  if (status == 0) status = OH_Pasteboard_SetData(board.get(), data.get());
  return status;
}
extern "C" int32_t ghost_ohos_native_button(int32_t button) noexcept {
  switch (button) {
    case MOUSE_BUTTON_LEFT: return GHOST_OHOS_BUTTON_LEFT;
    case MOUSE_BUTTON_MIDDLE: return GHOST_OHOS_BUTTON_MIDDLE;
    case MOUSE_BUTTON_RIGHT: return GHOST_OHOS_BUTTON_RIGHT;
    case MOUSE_BUTTON_BACK: return GHOST_OHOS_BUTTON_BACK;
    case MOUSE_BUTTON_FORWARD: return GHOST_OHOS_BUTTON_FORWARD;
    default: return 0;
  }
}
namespace {
GHOST_OHOSEvent input_event(uint64_t id, uint64_t generation, uint32_t type) {
  GHOST_OHOSEvent event{}; event.struct_size = sizeof(event); event.type = type;
  event.window_id = id; event.generation = generation; return event;
}
}
extern "C" int32_t ghost_ohos_native_decode_key(const Input_KeyEvent *native,
    uint64_t id, uint64_t generation, int32_t repeat, GHOST_OHOSEvent *out) noexcept {
  if (!native || !out || !id || !generation) return GHOST_OHOS_INVALID;
  const int32_t action = OH_Input_GetKeyEventAction(native);
  if (action != KEY_ACTION_DOWN && action != KEY_ACTION_UP && action != KEY_ACTION_CANCEL) return GHOST_OHOS_EMPTY;
  *out = input_event(id, generation, GHOST_OHOS_EVENT_KEY);
  out->value = OH_Input_GetKeyEventKeyCode(native);
  out->pressed = action == KEY_ACTION_DOWN; out->repeat = out->pressed && repeat;
  return GHOST_OHOS_OK;
}
extern "C" int32_t ghost_ohos_native_decode_mouse(const Input_MouseEvent *native,
    uint64_t id, uint64_t generation, int32_t origin_x, int32_t origin_y,
    float wheel_x_factor, float wheel_y_factor, GHOST_OHOSEvent *out) noexcept {
  if (!native || !out || !id || !generation || !std::isfinite(wheel_x_factor) ||
      !std::isfinite(wheel_y_factor)) return GHOST_OHOS_INVALID;
  const int32_t action = OH_Input_GetMouseEventAction(native);
  if (action == MOUSE_ACTION_CANCEL) { *out = input_event(id, generation, GHOST_OHOS_EVENT_CANCEL_INPUT); return GHOST_OHOS_OK; }
  if (action != MOUSE_ACTION_MOVE && action != MOUSE_ACTION_BUTTON_DOWN &&
      action != MOUSE_ACTION_BUTTON_UP && action != MOUSE_ACTION_AXIS_UPDATE) return GHOST_OHOS_EMPTY;
  const int64_t x = int64_t(OH_Input_GetMouseEventDisplayX(native)) - origin_x;
  const int64_t y = int64_t(OH_Input_GetMouseEventDisplayY(native)) - origin_y;
  if (x < INT32_MIN || x > INT32_MAX || y < INT32_MIN || y > INT32_MAX) return GHOST_OHOS_INVALID;
  *out = input_event(id, generation, GHOST_OHOS_EVENT_MOTION); out->x = int32_t(x); out->y = int32_t(y);
  if (action == MOUSE_ACTION_BUTTON_DOWN || action == MOUSE_ACTION_BUTTON_UP) {
    out->type = GHOST_OHOS_EVENT_BUTTON; out->value = ghost_ohos_native_button(OH_Input_GetMouseEventButton(native));
    if (!out->value) return GHOST_OHOS_EMPTY;
    out->pressed = action == MOUSE_ACTION_BUTTON_DOWN;
  }
  else if (action == MOUSE_ACTION_AXIS_UPDATE) {
    out->type = GHOST_OHOS_EVENT_WHEEL;
    const int32_t axis = OH_Input_GetMouseEventAxisType(native);
    const float value = OH_Input_GetMouseEventAxisValue(native);
    if (!std::isfinite(value)) return GHOST_OHOS_INVALID;
    if (axis == MOUSE_AXIS_SCROLL_VERTICAL) out->wheel_y = value * wheel_y_factor;
    else if (axis == MOUSE_AXIS_SCROLL_HORIZONTAL) out->wheel_x = value * wheel_x_factor;
    else return GHOST_OHOS_EMPTY;
    if (!std::isfinite(out->wheel_x) || !std::isfinite(out->wheel_y)) return GHOST_OHOS_INVALID;
  }
  return GHOST_OHOS_OK;
}
extern "C" int32_t ghost_ohos_native_decode_touch(const Input_TouchEvent *native,
    uint64_t id, uint64_t generation, GHOST_OHOSEvent *out) noexcept {
  if (!native || !out || !id || !generation) return GHOST_OHOS_INVALID;
  *out = input_event(id, generation, GHOST_OHOS_EVENT_TOUCH);
  switch (OH_Input_GetTouchEventAction(native)) {
    case TOUCH_ACTION_DOWN: out->value = GHOST_OHOS_TOUCH_DOWN; break;
    case TOUCH_ACTION_MOVE: out->value = GHOST_OHOS_TOUCH_MOVE; break;
    case TOUCH_ACTION_UP: out->value = GHOST_OHOS_TOUCH_UP; break;
    case TOUCH_ACTION_CANCEL: out->value = GHOST_OHOS_TOUCH_CANCEL; break;
    default: return GHOST_OHOS_EMPTY;
  }
  out->touch_id = OH_Input_GetTouchEventFingerId(native);
  out->x = OH_Input_GetTouchEventWindowX(native); out->y = OH_Input_GetTouchEventWindowY(native);
  return out->touch_id < 0 ? GHOST_OHOS_INVALID : GHOST_OHOS_OK;
}
extern "C" int32_t ghost_ohos_native_key(int32_t code) noexcept {
  if (code >= KEYCODE_A && code <= KEYCODE_Z) return 'A' + code - KEYCODE_A;
  if (code >= KEYCODE_0 && code <= KEYCODE_9) return '0' + code - KEYCODE_0;
  if (code >= KEYCODE_F1 && code <= KEYCODE_F12) return GHOST_OHOS_KEY_F1 + code - KEYCODE_F1;
  if (code >= KEYCODE_NUMPAD_0 && code <= KEYCODE_NUMPAD_9) return GHOST_OHOS_KEY_NUMPAD0 + code - KEYCODE_NUMPAD_0;
  switch (code) {
    case KEYCODE_DEL: return GHOST_OHOS_KEY_BACKSPACE;
    case KEYCODE_TAB: return GHOST_OHOS_KEY_TAB;
    case KEYCODE_ENTER: return GHOST_OHOS_KEY_ENTER;
    case KEYCODE_ESCAPE: return GHOST_OHOS_KEY_ESCAPE;
    case KEYCODE_SHIFT_LEFT: return GHOST_OHOS_KEY_LSHIFT;
    case KEYCODE_SHIFT_RIGHT: return GHOST_OHOS_KEY_RSHIFT;
    case KEYCODE_CTRL_LEFT: return GHOST_OHOS_KEY_LCTRL;
    case KEYCODE_CTRL_RIGHT: return GHOST_OHOS_KEY_RCTRL;
    case KEYCODE_ALT_LEFT: return GHOST_OHOS_KEY_LALT;
    case KEYCODE_ALT_RIGHT: return GHOST_OHOS_KEY_RALT;
    case KEYCODE_META_LEFT: return GHOST_OHOS_KEY_LMETA;
    case KEYCODE_META_RIGHT: return GHOST_OHOS_KEY_RMETA;
    case KEYCODE_CAPS_LOCK: return GHOST_OHOS_KEY_CAPSLOCK;
    case KEYCODE_NUM_LOCK: return GHOST_OHOS_KEY_NUMLOCK;
    case KEYCODE_SCROLL_LOCK: return GHOST_OHOS_KEY_SCROLLLOCK;
    case KEYCODE_DPAD_LEFT: return GHOST_OHOS_KEY_LEFT;
    case KEYCODE_DPAD_RIGHT: return GHOST_OHOS_KEY_RIGHT;
    case KEYCODE_DPAD_UP: return GHOST_OHOS_KEY_UP;
    case KEYCODE_DPAD_DOWN: return GHOST_OHOS_KEY_DOWN;
    case KEYCODE_INSERT: return GHOST_OHOS_KEY_INSERT;
    case KEYCODE_FORWARD_DEL: return GHOST_OHOS_KEY_DELETE;
    case KEYCODE_MOVE_HOME: return GHOST_OHOS_KEY_HOME;
    case KEYCODE_MOVE_END: return GHOST_OHOS_KEY_END;
    case KEYCODE_PAGE_UP: return GHOST_OHOS_KEY_PAGEUP;
    case KEYCODE_PAGE_DOWN: return GHOST_OHOS_KEY_PAGEDOWN;
    case KEYCODE_SYSRQ: return GHOST_OHOS_KEY_PRINTSCREEN;
    case KEYCODE_BREAK: return GHOST_OHOS_KEY_PAUSE;
    case KEYCODE_MENU: return GHOST_OHOS_KEY_MENU;
    case KEYCODE_NUMPAD_DOT: return GHOST_OHOS_KEY_NUMPAD_PERIOD;
    case KEYCODE_NUMPAD_ENTER: return GHOST_OHOS_KEY_NUMPAD_ENTER;
    case KEYCODE_NUMPAD_ADD: return GHOST_OHOS_KEY_NUMPAD_PLUS;
    case KEYCODE_NUMPAD_SUBTRACT: return GHOST_OHOS_KEY_NUMPAD_MINUS;
    case KEYCODE_NUMPAD_MULTIPLY: return GHOST_OHOS_KEY_NUMPAD_MULTIPLY;
    case KEYCODE_NUMPAD_DIVIDE: return GHOST_OHOS_KEY_NUMPAD_DIVIDE;
    case KEYCODE_SPACE: return ' ';
    case KEYCODE_COMMA: return ',';
    case KEYCODE_PERIOD: return '.';
    case KEYCODE_GRAVE: return '`';
    case KEYCODE_MINUS: return '-';
    case KEYCODE_EQUALS: return '=';
    case KEYCODE_LEFT_BRACKET: return '[';
    case KEYCODE_RIGHT_BRACKET: return ']';
    case KEYCODE_BACKSLASH: return '\\';
    case KEYCODE_SEMICOLON: return ';';
    case KEYCODE_APOSTROPHE: return '\'';
    case KEYCODE_SLASH: return '/';
    case KEYCODE_PLUS: return '+';
    default: return GHOST_OHOS_KEY_UNKNOWN;
  }
}
