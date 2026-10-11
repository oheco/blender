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
struct Geometry {
  int32_t window_id = 0;
  uint32_t display_id = 0;
  double left = 0, top = 0, width = 1, height = 1;
  bool operator==(const Geometry &other) const
  {
    return window_id == other.window_id && display_id == other.display_id &&
        left == other.left && top == other.top && width == other.width && height == other.height;
  }
};
struct Session {
  std::atomic<bool> accepting{true};
  uint64_t generation = 0;
  Geometry geometry, notified_cursor;
  bool cursor_notified = false;
  InputMethod_TextEditorProxy *editor = nullptr;
  InputMethod_AttachOptions *options = nullptr;
  InputMethod_InputMethodProxy *proxy = nullptr;
};
std::mutex control, callback_mutex;
std::shared_ptr<Session> current;
int32_t os_window = 0;
uint32_t display_id = 0;
double surface_left = 0, surface_top = 0;
int32_t caret_x = 0, caret_y = 0;
uint32_t caret_width = 1, caret_height = 1;
bool focused = false, requested = false, sync_pending = false;
uint64_t requested_generation = 0, retired_generation = 0;
std::atomic<bool> attached{false};
Geometry requested_geometry_locked()
{
  return {os_window, display_id, surface_left + caret_x, surface_top + caret_y,
          double(caret_width), double(caret_height)};
}
void cancel_request_locked()
{
  requested = false;
  requested_generation = 0;
}
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
  if (!editor || !value) return;
  Geometry geometry;
  { std::lock_guard<std::mutex> lock(callback_mutex);
    if (!current || current->editor != editor || !current->accepting) return;
    geometry = current->geometry;
  }
  // GetTextConfig may run during Attach. Never take control or retain the
  // callback lock while using the SDK's borrowed configuration objects.
  // As in the Godot port, pre-edit remains in the system candidate UI.
  // Do not claim support for arbitrary field selection or inline preview.
  report(OH_TextConfig_SetInputType(value, IME_TEXT_INPUT_TYPE_TEXT), "input type");
  report(OH_TextConfig_SetEnterKeyType(value, IME_ENTER_KEY_DONE), "enter type");
  report(OH_TextConfig_SetPreviewTextSupport(value, false), "preview policy");
  report(OH_TextConfig_SetWindowId(value, geometry.window_id), "window ID");
  InputMethod_CursorInfo *cursor = nullptr;
  if (OH_TextConfig_GetCursorInfo(value, &cursor) == IME_ERR_OK && cursor) {
    report(OH_CursorInfo_SetRect(cursor, geometry.left, geometry.top, geometry.width, geometry.height), "initial cursor");
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
  attached = false; // A retained SDK object after a failed detach no longer accepts text.
  if (!s) { attached = false; return GHOST_OHOS_OK; }
  if (s->proxy) {
    // Detach also hides the panel. HideKeyboard requires focus and can fail
    // during a normal focus handoff; this path releases the whole client.
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
  const auto s = current;
  const Geometry geometry = requested_geometry_locked();
  { std::lock_guard<std::mutex> lock(callback_mutex); s->geometry = geometry; }
  if (s->cursor_notified && s->notified_cursor == geometry) return GHOST_OHOS_OK;
  auto *cursor = OH_CursorInfo_Create(geometry.left, geometry.top, geometry.width, geometry.height);
  if (!cursor) return GHOST_OHOS_NO_MEMORY;
  const int32_t rc = OH_InputMethodProxy_NotifyCursorUpdate(s->proxy, cursor);
  OH_CursorInfo_Destroy(cursor); report(rc, "cursor update");
  if (rc != IME_ERR_OK) return GHOST_OHOS_UNAVAILABLE;
  // Failed notifications leave the last successful tuple intact for retry,
  // including when a subsequent begin/geometry supplies identical coordinates.
  s->notified_cursor = geometry;
  s->cursor_notified = true;
  return GHOST_OHOS_OK;
}
int32_t begin_locked(uint64_t generation)
{
  std::shared_ptr<Session> s;
  try { s = std::make_shared<Session>(); }
  catch (...) { return GHOST_OHOS_NO_MEMORY; }
  s->generation = generation;
  s->editor = OH_TextEditorProxy_Create(); s->options = OH_AttachOptions_Create(true);
  { std::lock_guard<std::mutex> lock(callback_mutex);
    s->geometry = requested_geometry_locked();
    current = s;
  }
  if (!s->editor || !s->options) { end_locked(); return GHOST_OHOS_NO_MEMORY; }
  // Input callbacks submit to the bridge from IPC threads. GetTextConfig is
  // excluded by the SDK and uses only its identity-guarded geometry snapshot.
  const int32_t callback_rc = OH_TextEditorProxy_SetCallbackInMainThread(s->editor, false);
  if (callback_rc != IME_ERR_OK) {
    report(callback_rc, "callback thread"); end_locked(); return GHOST_OHOS_UNAVAILABLE;
  }
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
  // A failed Attach does not grant ownership of its output parameter.
  InputMethod_InputMethodProxy *proxy = nullptr;
  const int32_t rc = OH_InputMethodController_Attach(s->editor, s->options, &proxy);
  if (rc != IME_ERR_OK || !proxy) {
    report(rc != IME_ERR_OK ? rc : IME_ERR_NULL_POINTER, "attach");
    end_locked(); return GHOST_OHOS_UNAVAILABLE;
  }
  s->proxy = proxy;
  attached = true;
  OH_LOG_Print(LOG_APP, LOG_INFO, 0xB10, "BlenderIME", "Attached to Blender editable field in OS window %{public}d", os_window);
  return GHOST_OHOS_OK;
}
int32_t reconcile_locked()
{
  if (!requested) return end_locked();
  uint64_t generation = 0;
  const bool live = blender_host_bridge().surface(generation);
  if ((requested_generation && requested_generation <= retired_generation) ||
      (live && (generation <= retired_generation ||
                (requested_generation && requested_generation != generation)))) {
    // A deferred request belongs to its original surface. Only a new begin
    // observed on the replacement surface may establish fresh field intent.
    cancel_request_locked();
    const int32_t rc = end_locked();
    return rc != GHOST_OHOS_OK ? rc : GHOST_OHOS_UNAVAILABLE;
  }
  if (live && !requested_generation) requested_generation = generation;
  if (!focused || !blender_host_bridge().accepting() || !live || os_window <= 0) {
    // Keep the caret/request pending while focus or usable metadata arrives.
    return end_locked();
  }
  if (current && (!current->accepting || current->generation != generation ||
                  current->geometry.window_id != os_window || current->geometry.display_id != display_id)) {
    // Failed Detach leaves current and every SDK-owned object intact, and
    // prevents any replacement Attach until a later cleanup succeeds.
    const int32_t rc = end_locked();
    if (rc != GHOST_OHOS_OK) return rc;
  }
  if (!current) {
    const int32_t rc = begin_locked(generation);
    if (rc != GHOST_OHOS_OK) return rc;
  }
  return cursor_locked();
}
}
bool blender_ime_active() { return attached; }
void blender_ime_geometry(int32_t id, uint32_t display, double left, double top)
{
  std::lock_guard<std::mutex> lock(control);
  // The UI caller supplies physical coordinates relative to this display.
  // Store them even if cleanup fails, so a later retry uses the latest binding.
  os_window = id; display_id = display; surface_left = left; surface_top = top;
  sync_pending = true;
}
int32_t blender_ime_sync()
{
  std::lock_guard<std::mutex> lock(control);
  if (!sync_pending) return GHOST_OHOS_OK;
  sync_pending = false;
  // UI publishes geometry/focus; the engine performs replacement Attach and
  // cursor RPCs. Failures remain retryable on the next real publication/begin.
  return reconcile_locked();
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
  sync_pending = value;
  return value ? GHOST_OHOS_OK : end_locked();
}
int32_t blender_ime_retire(uint64_t generation)
{
  std::lock_guard<std::mutex> lock(control);
  if (generation > retired_generation) retired_generation = generation;
  cancel_request_locked();
  return end_locked();
}
int32_t blender_ime_end()
{
  std::lock_guard<std::mutex> lock(control);
  cancel_request_locked();
  return end_locked();
}
int32_t blender_ime_begin(int32_t x, int32_t y, uint32_t width, uint32_t height)
{
  std::lock_guard<std::mutex> lock(control);
  caret_x = x; caret_y = y; caret_width = width; caret_height = height;
  requested = true;
  uint64_t generation;
  if (blender_host_bridge().surface(generation)) requested_generation = generation;
  else if (!requested_generation) {
    // A begin arriving without a live surface after retirement cannot become
    // an unbound request that would later attach to a replacement surface.
    requested_generation = current ? current->generation : retired_generation;
  }
  return reconcile_locked();
}
