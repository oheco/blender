/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "ime_input.h"
#include "host_bridge.h"
#include <inputmethod/inputmethod_controller_capi.h>
#include <multimodalinput/oh_key_code.h>
#include <hilog/log.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <string>
namespace {
struct Session {
  std::atomic<bool> accepting{true};
  uint64_t generation = 0;
  int32_t os_window = 0;
  double left = 0, top = 0, width = 1, height = 1;
  InputMethod_TextEditorProxy *editor = nullptr;
  InputMethod_AttachOptions *options = nullptr;
  InputMethod_InputMethodProxy *proxy = nullptr;
};
std::mutex control, callback_mutex;
std::shared_ptr<Session> current;
int32_t os_window = 0;
double surface_left = 0, surface_top = 0;
int32_t caret_x = 0, caret_y = 0;
uint32_t caret_width = 1, caret_height = 1;
bool focused = true;
uint64_t retired_generation = 0;
std::atomic<bool> attached{false};
std::shared_ptr<Session> session(InputMethod_TextEditorProxy *editor)
{
  std::lock_guard<std::mutex> lock(callback_mutex);
  return current && current->editor == editor && current->accepting ? current : nullptr;
}
void report(int32_t code, const char *operation)
{
  if (code) OH_LOG_Print(LOG_APP, LOG_ERROR, 0xB10, "BlenderIME", "%{public}s: %{public}d", operation, code);
}
void key(const std::shared_ptr<Session> &s, int32_t code, int32_t count = 1)
{
  if (!s) return;
  std::lock_guard<std::mutex> callback_lock(callback_mutex);
  if (current != s || !s->accepting) return;
  report(blender_host_bridge().key_pair(code, s->generation, count), "key batch");
}
void config(InputMethod_TextEditorProxy *editor, InputMethod_TextConfig *value)
{
  auto s = session(editor); if (!s) return;
  int32_t id; double left, top, width, height;
  { std::lock_guard<std::mutex> lock(callback_mutex);
    if (current != s || !s->accepting) return;
    id = s->os_window; left = s->left; top = s->top; width = s->width; height = s->height;
  }
  // As in the Godot port, pre-edit remains in the system candidate UI.
  // Do not claim support for arbitrary field selection or inline preview.
  report(OH_TextConfig_SetInputType(value, IME_TEXT_INPUT_TYPE_TEXT), "input type");
  report(OH_TextConfig_SetEnterKeyType(value, IME_ENTER_KEY_DONE), "enter type");
  report(OH_TextConfig_SetPreviewTextSupport(value, false), "preview policy");
  report(OH_TextConfig_SetWindowId(value, id), "window ID");
  InputMethod_CursorInfo *cursor = nullptr;
  if (OH_TextConfig_GetCursorInfo(value, &cursor) == IME_ERR_OK && cursor) {
    report(OH_CursorInfo_SetRect(cursor, left, top, width, height), "initial cursor");
  }
}
void insert(InputMethod_TextEditorProxy *editor, const char16_t *text, size_t count)
{
  try {
    auto s = session(editor); if (!s || !text || count > 8192) return;
    std::string utf8; utf8.reserve(count * 3);
    for (size_t i = 0; i < count; ++i) {
      uint32_t c = text[i];
      if (c >= 0xd800 && c <= 0xdbff) {
        if (++i == count || text[i] < 0xdc00 || text[i] > 0xdfff) return;
        c = 0x10000 + ((c - 0xd800) << 10) + (text[i] - 0xdc00);
      } else if (c >= 0xdc00 && c <= 0xdfff) return;
      if (c == 0) return;
      if (c < 0x80) utf8 += char(c);
      else if (c < 0x800) { utf8 += char(0xc0 | (c >> 6)); utf8 += char(0x80 | (c & 63)); }
      else if (c < 0x10000) { utf8 += char(0xe0 | (c >> 12)); utf8 += char(0x80 | ((c >> 6) & 63)); utf8 += char(0x80 | (c & 63)); }
      else { utf8 += char(0xf0 | (c >> 18)); utf8 += char(0x80 | ((c >> 12) & 63)); utf8 += char(0x80 | ((c >> 6) & 63)); utf8 += char(0x80 | (c & 63)); }
    }
    if (!utf8.empty()) {
      std::lock_guard<std::mutex> callback_lock(callback_mutex);
      if (current == s && s->accepting) report(blender_host_bridge().text(utf8, s->generation), "text commit");
    }
  } catch (...) { report(GHOST_OHOS_NO_MEMORY, "text callback"); }
}
void forward(InputMethod_TextEditorProxy *editor, int32_t n) { key(session(editor), KEYCODE_FORWARD_DEL, n); }
void backward(InputMethod_TextEditorProxy *editor, int32_t n) { key(session(editor), KEYCODE_DEL, n); }
void enter(InputMethod_TextEditorProxy *editor, InputMethod_EnterKeyType) { key(session(editor), KEYCODE_ENTER); }
void move(InputMethod_TextEditorProxy *editor, InputMethod_Direction d)
{
  const int32_t code = d == IME_DIRECTION_LEFT ? KEYCODE_DPAD_LEFT : d == IME_DIRECTION_RIGHT ? KEYCODE_DPAD_RIGHT :
      d == IME_DIRECTION_UP ? KEYCODE_DPAD_UP : d == IME_DIRECTION_DOWN ? KEYCODE_DPAD_DOWN : 0;
  if (code) key(session(editor), code);
}
void status(InputMethod_TextEditorProxy *, InputMethod_KeyboardStatus) {}
void selection(InputMethod_TextEditorProxy *, int32_t, int32_t) {}
void extend(InputMethod_TextEditorProxy *, InputMethod_ExtendAction) {}
void surrounding(InputMethod_TextEditorProxy *, int32_t, char16_t *, size_t *length) { if (length) *length = 0; }
int32_t index(InputMethod_TextEditorProxy *) { return 0; }
int32_t private_command(InputMethod_TextEditorProxy *, InputMethod_PrivateCommand *[], size_t) { return IME_ERR_UNDEFINED; }
int32_t preview(InputMethod_TextEditorProxy *, const char16_t *, size_t, int32_t, int32_t) { return IME_ERR_UNDEFINED; }
void finish_preview(InputMethod_TextEditorProxy *) {}
int32_t end_locked()
{
  std::shared_ptr<Session> s;
  { std::lock_guard<std::mutex> lock(callback_mutex); s = current; if (s) s->accepting = false; }
  if (!s) { attached = false; return GHOST_OHOS_OK; }
  if (s->proxy) {
    report(OH_InputMethodProxy_HideKeyboard(s->proxy), "hide");
    const int32_t rc = OH_InputMethodController_Detach(s->proxy);
    if (rc != IME_ERR_OK) { report(rc, "detach; retain SDK-owned session"); return GHOST_OHOS_UNAVAILABLE; }
    s->proxy = nullptr;
  }
  attached = false;
  { std::lock_guard<std::mutex> lock(callback_mutex); current.reset(); }
  if (s->options) OH_AttachOptions_Destroy(s->options);
  if (s->editor) OH_TextEditorProxy_Destroy(s->editor);
  return GHOST_OHOS_OK;
}
int32_t cursor_locked()
{
  if (!current || !current->accepting || !current->proxy) return GHOST_OHOS_OK;
  const double left = surface_left + caret_x, top = surface_top + caret_y;
  { std::lock_guard<std::mutex> lock(callback_mutex);
    current->left = left; current->top = top;
    current->width = caret_width; current->height = caret_height;
    current->os_window = os_window;
  }
  auto *cursor = OH_CursorInfo_Create(left, top, caret_width, caret_height);
  if (!cursor) return GHOST_OHOS_NO_MEMORY;
  const int32_t rc = OH_InputMethodProxy_NotifyCursorUpdate(current->proxy, cursor);
  OH_CursorInfo_Destroy(cursor); report(rc, "cursor update");
  return rc == IME_ERR_OK ? GHOST_OHOS_OK : GHOST_OHOS_UNAVAILABLE;
}
}
bool blender_ime_active() { return attached; }
void blender_ime_geometry(int32_t id, double left, double top)
{
  std::lock_guard<std::mutex> lock(control);
  if (os_window == id && surface_left == left && surface_top == top) return;
  if (current && current->os_window != id) {
    if (end_locked() != GHOST_OHOS_OK) return;
  }
  os_window = id; surface_left = left; surface_top = top;
  cursor_locked();
}
int32_t blender_ime_focus(bool value, uint64_t expected_generation)
{
  std::lock_guard<std::mutex> lock(control);
  if (expected_generation) {
    uint64_t live;
    if (!blender_host_bridge().surface(live) || live != expected_generation) return GHOST_OHOS_UNAVAILABLE;
  }
  if (value && retired_generation) {
    uint64_t live;
    if (blender_host_bridge().surface(live) && live <= retired_generation) return GHOST_OHOS_OK;
  }
  focused = value;
  return value ? GHOST_OHOS_OK : end_locked();
}
int32_t blender_ime_retire(uint64_t generation)
{
  std::lock_guard<std::mutex> lock(control);
  if (generation > retired_generation) retired_generation = generation;
  return end_locked();
}
int32_t blender_ime_end()
{
  std::lock_guard<std::mutex> lock(control); return end_locked();
}
int32_t blender_ime_begin(int32_t x, int32_t y, uint32_t width, uint32_t height)
{
  std::lock_guard<std::mutex> lock(control);
  uint64_t generation;
  if (!focused || !blender_host_bridge().accepting() || os_window <= 0 ||
      !blender_host_bridge().surface(generation) || generation <= retired_generation) return GHOST_OHOS_UNAVAILABLE;
  caret_x = x; caret_y = y; caret_width = width; caret_height = height;
  if (current && (!current->accepting || current->generation != generation)) {
    if (end_locked() != GHOST_OHOS_OK) return GHOST_OHOS_UNAVAILABLE;
  }
  if (!current) {
    auto s = std::make_shared<Session>(); s->generation = generation; s->os_window = os_window;
    s->left = surface_left + x; s->top = surface_top + y; s->width = width; s->height = height;
    s->editor = OH_TextEditorProxy_Create(); s->options = OH_AttachOptions_Create(true);
    { std::lock_guard<std::mutex> callback_lock(callback_mutex); current = s; }
    if (!s->editor || !s->options) { end_locked(); return GHOST_OHOS_NO_MEMORY; }
#define REGISTER(Name, Function) if (OH_TextEditorProxy_Set##Name##Func(s->editor, Function) != IME_ERR_OK) { end_locked(); return GHOST_OHOS_UNAVAILABLE; }
    REGISTER(GetTextConfig, config) REGISTER(InsertText, insert)
    REGISTER(DeleteForward, forward) REGISTER(DeleteBackward, backward)
    REGISTER(SendKeyboardStatus, status) REGISTER(SendEnterKey, enter)
    REGISTER(MoveCursor, move) REGISTER(HandleSetSelection, selection)
    REGISTER(HandleExtendAction, extend) REGISTER(GetLeftTextOfCursor, surrounding)
    REGISTER(GetRightTextOfCursor, surrounding) REGISTER(GetTextIndexAtCursor, index)
    REGISTER(ReceivePrivateCommand, private_command) REGISTER(SetPreviewText, preview)
    REGISTER(FinishTextPreview, finish_preview)
#undef REGISTER
    const int32_t rc = OH_InputMethodController_Attach(s->editor, s->options, &s->proxy);
    if (rc != IME_ERR_OK) { report(rc, "attach"); end_locked(); return GHOST_OHOS_UNAVAILABLE; }
    attached = true;
    OH_LOG_Print(LOG_APP, LOG_INFO, 0xB10, "BlenderIME", "Attached to Blender editable field in OS window %{public}d", os_window);
  }
  return cursor_locked();
}
