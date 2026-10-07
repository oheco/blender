/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_SystemOHOS.hh"
#include "GHOST_OHOSLifecycle.hh"
#include "GHOST_WindowOHOS.hh"
#include "GHOST_OHOSNative.h"
#include "GHOST_ContextNone.hh"
#include "GHOST_Event.hh"
#include "GHOST_EventButton.hh"
#include "GHOST_EventCursor.hh"
#include "GHOST_EventKey.hh"
#include "GHOST_EventWheel.hh"
#include "GHOST_TimerManager.hh"
#include "GHOST_WindowManager.hh"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <stdexcept>
namespace {
GHOST_TKey ghost_key(int32_t native)
{
  const int32_t key = ghost_ohos_native_key(native);
  if (key >= ' ' && key <= '~') return GHOST_TKey(key);
  if (key >= GHOST_OHOS_KEY_F1 && key <= GHOST_OHOS_KEY_F12) return GHOST_TKey(GHOST_kKeyF1 + key - GHOST_OHOS_KEY_F1);
  if (key >= GHOST_OHOS_KEY_NUMPAD0 && key <= GHOST_OHOS_KEY_NUMPAD9) return GHOST_TKey(GHOST_kKeyNumpad0 + key - GHOST_OHOS_KEY_NUMPAD0);
  switch (key) {
    case GHOST_OHOS_KEY_BACKSPACE: return GHOST_kKeyBackSpace;
    case GHOST_OHOS_KEY_TAB: return GHOST_kKeyTab;
    case GHOST_OHOS_KEY_ENTER: return GHOST_kKeyEnter;
    case GHOST_OHOS_KEY_ESCAPE: return GHOST_kKeyEsc;
    case GHOST_OHOS_KEY_LSHIFT: return GHOST_kKeyLeftShift;
    case GHOST_OHOS_KEY_RSHIFT: return GHOST_kKeyRightShift;
    case GHOST_OHOS_KEY_LCTRL: return GHOST_kKeyLeftControl;
    case GHOST_OHOS_KEY_RCTRL: return GHOST_kKeyRightControl;
    case GHOST_OHOS_KEY_LALT: return GHOST_kKeyLeftAlt;
    case GHOST_OHOS_KEY_RALT: return GHOST_kKeyRightAlt;
    case GHOST_OHOS_KEY_LMETA: return GHOST_kKeyLeftOS;
    case GHOST_OHOS_KEY_RMETA: return GHOST_kKeyRightOS;
    case GHOST_OHOS_KEY_CAPSLOCK: return GHOST_kKeyCapsLock;
    case GHOST_OHOS_KEY_NUMLOCK: return GHOST_kKeyNumLock;
    case GHOST_OHOS_KEY_SCROLLLOCK: return GHOST_kKeyScrollLock;
    case GHOST_OHOS_KEY_LEFT: return GHOST_kKeyLeftArrow;
    case GHOST_OHOS_KEY_RIGHT: return GHOST_kKeyRightArrow;
    case GHOST_OHOS_KEY_UP: return GHOST_kKeyUpArrow;
    case GHOST_OHOS_KEY_DOWN: return GHOST_kKeyDownArrow;
    case GHOST_OHOS_KEY_INSERT: return GHOST_kKeyInsert;
    case GHOST_OHOS_KEY_DELETE: return GHOST_kKeyDelete;
    case GHOST_OHOS_KEY_HOME: return GHOST_kKeyHome;
    case GHOST_OHOS_KEY_END: return GHOST_kKeyEnd;
    case GHOST_OHOS_KEY_PAGEUP: return GHOST_kKeyUpPage;
    case GHOST_OHOS_KEY_PAGEDOWN: return GHOST_kKeyDownPage;
    case GHOST_OHOS_KEY_PRINTSCREEN: return GHOST_kKeyPrintScreen;
    case GHOST_OHOS_KEY_PAUSE: return GHOST_kKeyPause;
    case GHOST_OHOS_KEY_MENU: return GHOST_kKeyApp;
    case GHOST_OHOS_KEY_NUMPAD_PERIOD: return GHOST_kKeyNumpadPeriod;
    case GHOST_OHOS_KEY_NUMPAD_ENTER: return GHOST_kKeyNumpadEnter;
    case GHOST_OHOS_KEY_NUMPAD_PLUS: return GHOST_kKeyNumpadPlus;
    case GHOST_OHOS_KEY_NUMPAD_MINUS: return GHOST_kKeyNumpadMinus;
    case GHOST_OHOS_KEY_NUMPAD_MULTIPLY: return GHOST_kKeyNumpadAsterisk;
    case GHOST_OHOS_KEY_NUMPAD_DIVIDE: return GHOST_kKeyNumpadSlash;
    default: return GHOST_kKeyUnknown;
  }
}
GHOST_TButton ghost_button(int32_t button)
{
  switch (button) {
    case GHOST_OHOS_BUTTON_LEFT: return GHOST_kButtonMaskLeft;
    case GHOST_OHOS_BUTTON_MIDDLE: return GHOST_kButtonMaskMiddle;
    case GHOST_OHOS_BUTTON_RIGHT: return GHOST_kButtonMaskRight;
    case GHOST_OHOS_BUTTON_BACK: return GHOST_kButtonMaskButton4;
    case GHOST_OHOS_BUTTON_FORWARD: return GHOST_kButtonMaskButton5;
    default: return GHOST_kButtonMaskNone;
  }
}
}
GHOST_SystemOHOS::GHOST_SystemOHOS() : host_(ghost_ohos_host_installed())
{
  if (!host_ || ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK)
    throw std::runtime_error("OHOS requires an installed host on the controlled engine thread");
}
GHOST_SystemOHOS::~GHOST_SystemOHOS()
{
  while (!windows_.empty()) disposeWindow(windows_.begin()->second);
}
GHOST_TSuccess GHOST_SystemOHOS::init()
{
  return ghost_ohos_host_is_engine(host_) == GHOST_OHOS_OK ? GHOST_System::init() : GHOST_kFailure;
}
uint64_t GHOST_SystemOHOS::getMilliSeconds() const { return ghost_ohos_monotonic_ms(); }
GHOST_TCapabilityFlag GHOST_SystemOHOS::getCapabilities() const
{
  /* The host attaches its native IME to the active Blender editable field.
   * Pre-edit stays in the native candidate UI; committed UTF8 uses EVENT_TEXT.
   * Other unimplemented desktop capabilities remain unadvertised. */
#ifdef WITH_INPUT_IME
  return GHOST_kCapabilityInputIME;
#else
  return GHOST_TCapabilityFlag(0);
#endif
}
uint8_t GHOST_SystemOHOS::getNumDisplays() const { return 1; /* Host-managed primary view only. */ }
void GHOST_SystemOHOS::getMainDisplayDimensions(uint32_t &width, uint32_t &height) const
{
  GHOST_OHOSWindowInfo info{};
  if (ghost_ohos_host_get_window(host_, ghost_ohos_host_main_window(host_), &info) == GHOST_OHOS_OK) {
    width = info.width; height = info.height;
  }
  else { width = height = 0; }
}
void GHOST_SystemOHOS::getAllDisplayDimensions(uint32_t &width, uint32_t &height) const { getMainDisplayDimensions(width, height); }
GHOST_IWindow *GHOST_SystemOHOS::createWindow(const char *title, int32_t, int32_t,
    uint32_t width, uint32_t height, GHOST_TWindowState state, GHOST_GPUSettings settings,
    bool exclusive, bool is_dialog, const GHOST_IWindow *parent)
{
  if (exclusive || ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return nullptr;
  uint64_t id = ghost_ohos_host_main_window(host_);
  GHOST_OHOSWindowInfo surface{};
  if (windows_.empty() && !parent) {
    if (ghost_ohos_host_acquire_window(host_, id, &surface) != GHOST_OHOS_OK) return nullptr;
  }
  else {
    GHOST_OHOSCommand cmd{}; cmd.struct_size = sizeof(cmd); cmd.type = GHOST_OHOS_COMMAND_CREATE;
    cmd.width = width; cmd.height = height; cmd.value = int32_t(state); cmd.text = title;
    if (parent) {
      auto found = std::find_if(windows_.begin(), windows_.end(), [&](const auto &item) { return item.second == parent; });
      if (found == windows_.end()) return nullptr;
      cmd.parent_id = found->first;
    }
    if (ghost_ohos_host_command(host_, &cmd, &id) != GHOST_OHOS_OK ||
        ghost_ohos_host_acquire_window(host_, id, &surface) != GHOST_OHOS_OK) return nullptr;
  }
  try {
    const GHOST_ContextParams params = GHOST_CONTEXT_PARAMS_FROM_GPU_SETTINGS(settings);
    std::unique_ptr<GHOST_WindowOHOS> window(new GHOST_WindowOHOS(this, host_, surface,
                                                       title, state, settings, is_dialog, params));
    if (!window->getValid()) return nullptr;
    const auto [it, inserted] = windows_.emplace(id, window.get());
    if (!inserted) return nullptr;
    if (window_manager_->addWindow(window.get()) != GHOST_kSuccess) { windows_.erase(it); return nullptr; }
    auto *result = window.release();
    pushEvent(std::make_unique<GHOST_Event>(getMilliSeconds(), GHOST_kEventWindowSize, result));
    return result;
  }
  catch (...) {
    /* Constructor failure has no derived destructor; return the surface lease.
     * release_window is idempotently rejected if an RAII window already released it. */
    const auto registered = windows_.find(id);
    if (registered != windows_.end()) {
      GHOST_System::disposeWindow(registered->second);
      windows_.erase(registered);
    }
    ghost_ohos_host_release_window(host_, surface.window_id, surface.generation);
    return nullptr;
  }
}
GHOST_TSuccess GHOST_SystemOHOS::disposeWindow(GHOST_IWindow *window)
{
  if (ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return GHOST_kFailure;
  const auto found = std::find_if(windows_.begin(), windows_.end(), [&](const auto &item) { return item.second == window; });
  if (found == windows_.end()) return GHOST_kFailure;
  const uint64_t id = found->first;
  const auto result = GHOST_System::disposeWindow(window);
  if (result == GHOST_kSuccess) {
    windows_.erase(id);
    if (cursor_window_ == id) { cursor_known_ = false; cursor_window_ = 0; }
    GHOST_OHOSCommand cmd{}; cmd.struct_size = sizeof(cmd); cmd.type = GHOST_OHOS_COMMAND_DESTROY; cmd.window_id = id;
    ghost_ohos_host_command(host_, &cmd, nullptr);
  }
  return result;
}
GHOST_IContext *GHOST_SystemOHOS::createOffscreenContext(GHOST_GPUSettings settings)
{
  if (ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return nullptr;
  const GHOST_ContextParams params = GHOST_CONTEXT_PARAMS_FROM_GPU_SETTINGS_OFFSCREEN(settings);
#ifdef WITH_VULKAN_BACKEND
  if (settings.context_type == GHOST_kDrawingContextTypeVulkan) {
    auto *context = new GHOST_ContextVK(params, GHOST_kVulkanPlatformHeadless,
        nullptr, nullptr, nullptr, nullptr, nullptr, 1, 3, settings.preferred_device);
    if (context->initializeDrawingContext() == GHOST_kSuccess) return context;
    delete context;
    return nullptr;
  }
#endif
  if (settings.context_type == GHOST_kDrawingContextTypeNone) return new GHOST_ContextNone(params);
  return nullptr;
}
GHOST_TSuccess GHOST_SystemOHOS::disposeContext(GHOST_IContext *context)
{
  if (ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return GHOST_kFailure;
  delete context; return GHOST_kSuccess;
}
GHOST_TSuccess GHOST_SystemOHOS::getCursorPosition(int32_t &x, int32_t &y) const
{
  x = cursor_x_; y = cursor_y_; return cursor_known_ ? GHOST_kSuccess : GHOST_kFailure;
}
GHOST_TSuccess GHOST_SystemOHOS::setCursorPosition(int32_t, int32_t) { return GHOST_kFailure; }
GHOST_IWindow *GHOST_SystemOHOS::getWindowUnderCursor(int32_t, int32_t)
{
  const auto it = windows_.find(cursor_window_); return it == windows_.end() ? nullptr : it->second;
}
GHOST_TSuccess GHOST_SystemOHOS::getModifierKeys(GHOST_ModifierKeys &keys) const
{
  keys.clear();
  for (int i = 0; i < GHOST_kModifierKeyNum; ++i) {
    const auto mask = GHOST_TModifierKey(i);
    const auto code = GHOST_ModifierKeys::getModifierKeyCode(mask);
    for (const auto &item : windows_) if (item.second->pressed_keys.count(code)) { keys.set(mask, true); break; }
  }
  return GHOST_kSuccess;
}
GHOST_TSuccess GHOST_SystemOHOS::getButtons(GHOST_Buttons &buttons) const
{
  buttons.clear();
  for (const auto &item : windows_) for (const auto button : item.second->pressed_buttons) buttons.set(button, true);
  return GHOST_kSuccess;
}
char *GHOST_SystemOHOS::getClipboard(bool selection) const
{
  return !selection && ghost_ohos_host_is_engine(host_) == GHOST_OHOS_OK ? ghost_ohos_native_clipboard_get() : nullptr;
}
void GHOST_SystemOHOS::putClipboard(const char *buffer, bool selection) const
{
  if (!selection && buffer && ghost_ohos_host_is_engine(host_) == GHOST_OHOS_OK) ghost_ohos_native_clipboard_put(buffer);
}
bool GHOST_SystemOHOS::setConsoleWindowState(GHOST_TConsoleWindowState) { return false; }
void GHOST_SystemOHOS::buttonEvent(GHOST_WindowOHOS *window, GHOST_TButton button, bool pressed, uint64_t time)
{
  if (button == GHOST_kButtonMaskNone) return;
  if (pressed) window->pressed_buttons.insert(button); else window->pressed_buttons.erase(button);
  pushEvent(std::make_unique<GHOST_EventButton>(time, pressed ? GHOST_kEventButtonDown : GHOST_kEventButtonUp,
                                               window, button, GHOST_TABLET_DATA_NONE));
}
void GHOST_SystemOHOS::releaseInput(GHOST_WindowOHOS *window, uint64_t time)
{
  for (const auto key : window->pressed_keys)
    pushEvent(std::make_unique<GHOST_EventKey>(time, GHOST_kEventKeyUp, window, key, false));
  window->pressed_keys.clear();
  const auto buttons = window->pressed_buttons;
  for (const auto button : buttons) buttonEvent(window, button, false, time);
  window->touch_id = -1; window->wheel_remainder = window->wheel_horizontal_remainder = 0;
}
bool GHOST_SystemOHOS::handleEvent(const GHOST_OHOSEvent &event)
{
  const auto found = windows_.find(event.window_id);
  GHOST_WindowOHOS *window = found == windows_.end() ? nullptr : found->second;
  if (event.type == GHOST_OHOS_EVENT_ATTACHED && window && !window->hostAttached()) {
    GHOST_OHOSWindowInfo surface{};
    if (ghost_ohos_host_acquire_window(host_, event.window_id, &surface) != GHOST_OHOS_OK) return false;
    if (surface.generation != event.generation || window->hostAttach(surface) != GHOST_kSuccess) {
      ghost_ohos_host_release_window(host_, surface.window_id, surface.generation);
      pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowClose, window));
      return true;
    }
    pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowSize, window));
    pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowDPIHintChanged, window));
    window->invalidate();
    return true; /* Device and GHOST context/callbacks survive XComponent recreation. */
  }
  if (window && window->hostGeneration() != event.generation) return false;
  if (event.type == GHOST_OHOS_EVENT_DETACHED) {
    if (window) {
      releaseInput(window, event.timestamp_ms);
      window_manager_->setWindowInactive(window);
      if (window->hostDetach() != GHOST_kSuccess) {
        pending_detach_acks_[event.window_id] = event.generation;
        /* Keep the native reference until WM/GPU teardown destroys the context. */
        pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowClose, window));
        return true;
      }
      pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowDeactivate, window));
    }
    if (ghost_ohos_host_ack_detach(host_, event.window_id, event.generation) != GHOST_OHOS_OK)
      pending_detach_acks_[event.window_id] = event.generation;
    if (cursor_window_ == event.window_id) cursor_known_ = false;
    return true;
  }
  if (window && event.type == GHOST_OHOS_EVENT_CLOSE) {
    pushEvent(std::make_unique<GHOST_Event>(event.timestamp_ms, GHOST_kEventWindowClose, window));
    return true; /* Logical close remains meaningful while surface is suspended. */
  }
  if (!window || !window->getValid()) return false;
  const uint64_t time = event.timestamp_ms;
  const auto motion = [&]() {
    cursor_x_ = event.x; cursor_y_ = event.y; cursor_window_ = event.window_id; cursor_known_ = true;
    pushEvent(std::make_unique<GHOST_EventCursor>(time, GHOST_kEventCursorMove, window,
                                                event.x, event.y, GHOST_TABLET_DATA_NONE));
  };
  switch (event.type) {
    case GHOST_OHOS_EVENT_ATTACHED: return false; /* Creation is explicit, not a UI-thread GHOST call. */
    case GHOST_OHOS_EVENT_RESIZE: {
      const uint16_t old_dpi = window->getDPIHint();
      window->hostResize(event);
      pushEvent(std::make_unique<GHOST_Event>(time, GHOST_kEventWindowSize, window));
      if (old_dpi != event.dpi)
        pushEvent(std::make_unique<GHOST_Event>(time, GHOST_kEventWindowDPIHintChanged, window));
      return true;
    }
    case GHOST_OHOS_EVENT_FOCUS:
      if (event.pressed) window_manager_->setActiveWindow(window);
      else { releaseInput(window, time); window_manager_->setWindowInactive(window); }
      pushEvent(std::make_unique<GHOST_Event>(time, event.pressed ? GHOST_kEventWindowActivate : GHOST_kEventWindowDeactivate, window));
      return true;
    case GHOST_OHOS_EVENT_CANCEL_INPUT: releaseInput(window, time); return true;
    case GHOST_OHOS_EVENT_MOTION: motion(); return true;
    case GHOST_OHOS_EVENT_BUTTON:
      motion(); buttonEvent(window, ghost_button(event.value), event.pressed != 0, time); return true;
    case GHOST_OHOS_EVENT_WHEEL: {
      motion(); window->wheel_remainder += event.wheel_y;
      const int vertical = int(window->wheel_remainder); window->wheel_remainder -= float(vertical);
      if (vertical) pushEvent(std::make_unique<GHOST_EventWheel>(time, window, GHOST_kEventWheelAxisVertical, vertical));
      window->wheel_horizontal_remainder += event.wheel_x;
      const int horizontal = int(window->wheel_horizontal_remainder);
      window->wheel_horizontal_remainder -= float(horizontal);
      if (horizontal) pushEvent(std::make_unique<GHOST_EventWheel>(time, window, GHOST_kEventWheelAxisHorizontal, horizontal));
      return true;
    }
    case GHOST_OHOS_EVENT_KEY: {
      const auto key = ghost_key(event.value);
      if (key == GHOST_kKeyUnknown) return false;
      if (event.pressed) window->pressed_keys.insert(key); else window->pressed_keys.erase(key);
      pushEvent(std::make_unique<GHOST_EventKey>(time, event.pressed ? GHOST_kEventKeyDown : GHOST_kEventKeyUp,
                                               window, key, event.pressed && event.repeat));
      return true;
    }
    case GHOST_OHOS_EVENT_TEXT:
      /* Queue already validates scalar UTF8 and rejects overlong/surrogate/NUL.
       * GHOST's key buffer is 6 bytes: emit one scalar with padded storage. */
      for (uint32_t offset = 0; offset < event.text_bytes;) {
        const auto lead = static_cast<unsigned char>(event.text[offset]);
        const uint32_t n = lead < 0x80 ? 1 : lead < 0xe0 ? 2 : lead < 0xf0 ? 3 : 4;
        char utf8[6]{}; std::memcpy(utf8, event.text + offset, n); offset += n;
        pushEvent(std::make_unique<GHOST_EventKey>(time, GHOST_kEventKeyDown, window, GHOST_kKeyUnknown, false, utf8));
        pushEvent(std::make_unique<GHOST_EventKey>(time, GHOST_kEventKeyUp, window, GHOST_kKeyUnknown, false));
      }
      return true;
    case GHOST_OHOS_EVENT_TOUCH:
      if (event.value == GHOST_OHOS_TOUCH_DOWN && window->touch_id == -1) {
        window->touch_id = event.touch_id; motion(); buttonEvent(window, GHOST_kButtonMaskLeft, true, time);
      }
      else if (event.touch_id == window->touch_id) {
        motion();
        if (event.value == GHOST_OHOS_TOUCH_UP || event.value == GHOST_OHOS_TOUCH_CANCEL) {
          buttonEvent(window, GHOST_kButtonMaskLeft, false, time); window->touch_id = -1;
        }
      }
      return true;
    default: return false;
  }
}
bool GHOST_SystemOHOS::processEvents(bool waitForEvent)
{
  if (ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return false;
  bool processed = timer_manager_->fireTimers(getMilliSeconds());
  for (auto it = pending_detach_acks_.begin(); it != pending_detach_acks_.end();) {
    const auto result = ghost_ohos_host_ack_detach(host_, it->first, it->second);
    if (result == GHOST_OHOS_OK || result == GHOST_OHOS_UNAVAILABLE) {
      it = pending_detach_acks_.erase(it); processed = true;
    }
    else ++it; /* Never grant ACK when native unreference failed; retry next pump. */
  }
  uint32_t timeout = 0;
  if (waitForEvent && !processed) {
    const uint64_t now = getMilliSeconds(), next = timer_manager_->nextFireTime();
    /* Finite wait also pumps lifecycle without VSync (background/hidden views). */
    timeout = uint32_t(next > now ? std::min<uint64_t>(next - now, 16) : 0);
  }
  GHOST_OHOSEvent event{};
  for (int count = 0; count < 256; ++count) {
    const auto result = ghost_ohos_host_pop(host_, &event, count == 0 ? timeout : 0);
    if (result != GHOST_OHOS_OK) break;
    processed |= handleEvent(event);
  }
  return timer_manager_->fireTimers(getMilliSeconds()) || processed;
}

int32_t GHOST_SystemOHOS::lifecycleDetach(uint64_t id, uint64_t generation)
{
  const auto found = windows_.find(id);
  GHOST_WindowOHOS *window = found == windows_.end() ? nullptr : found->second;
  if (window && window->hostGeneration() == generation) {
    /* No synthetic key/button/WM events while shutting down. */
    window->pressed_keys.clear();
    window->pressed_buttons.clear();
    window->touch_id = -1;
    window->wheel_remainder = window->wheel_horizontal_remainder = 0;
    window_manager_->setWindowInactive(window);
    if (window->hostDetach() != GHOST_kSuccess) {
      return GHOST_OHOS_CALLBACK_FAILED;
    }
  }
  /* hostDetach releases the GPU surface and lease first. A native unreference
   * failure keeps the registry record pending and is retried on the next tick. */
  const auto result = ghost_ohos_host_ack_detach(host_, id, generation);
  if (result == GHOST_OHOS_OK || result == GHOST_OHOS_UNAVAILABLE) {
    if (cursor_window_ == id) { cursor_known_ = false; }
    return GHOST_OHOS_OK;
  }
  return result;
}
int32_t GHOST_SystemOHOS::handleLifecycleEvent(const GHOST_OHOSEvent &event)
{
  if (event.type == GHOST_OHOS_EVENT_DETACHED) {
    /* Record before fallible detach so even a C++ exception retains its retry. */
    pending_detach_acks_[event.window_id] = event.generation;
    const auto result = lifecycleDetach(event.window_id, event.generation);
    if (result == GHOST_OHOS_OK) { pending_detach_acks_.erase(event.window_id); }
    return result;
  }
  const auto found = windows_.find(event.window_id);
  if (found == windows_.end() || found->second->hostGeneration() != event.generation) {
    return GHOST_OHOS_OK;
  }
  auto *window = found->second;
  if (event.type == GHOST_OHOS_EVENT_RESIZE) {
    window->hostResize(event);
  }
  else if (event.type == GHOST_OHOS_EVENT_FOCUS || event.type == GHOST_OHOS_EVENT_CANCEL_INPUT) {
    if (event.type == GHOST_OHOS_EVENT_FOCUS && event.pressed && window->hostAttached()) {
      window_manager_->setActiveWindow(window);
    }
    else {
      window->pressed_keys.clear();
      window->pressed_buttons.clear();
      window->touch_id = -1;
      window->wheel_remainder = window->wheel_horizontal_remainder = 0;
      if (event.type == GHOST_OHOS_EVENT_FOCUS) { window_manager_->setWindowInactive(window); }
    }
  }
  /* ATTACHED never acquires a new lease here; CLOSE/input never invokes WM. */
  return GHOST_OHOS_OK;
}
int32_t GHOST_SystemOHOS::processLifecycleEvents()
{
  if (ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) { return GHOST_OHOS_WRONG_THREAD; }
  bool error = false;
  for (auto it = pending_detach_acks_.begin(); it != pending_detach_acks_.end();) {
    const auto result = lifecycleDetach(it->first, it->second);
    if (result == GHOST_OHOS_OK) { it = pending_detach_acks_.erase(it); }
    else {
      error |= result != GHOST_OHOS_BUSY;
      ++it;
    }
  }
  bool exhausted = false;
  GHOST_OHOSEvent event{};
  for (int count = 0; count < 256; ++count) {
    const auto result = ghost_ohos_host_pop(host_, &event, 0);
    if (result == GHOST_OHOS_EMPTY) { exhausted = true; break; }
    if (result != GHOST_OHOS_OK) { return result; }
    const auto handled = handleLifecycleEvent(event);
    error |= handled != GHOST_OHOS_OK && handled != GHOST_OHOS_BUSY;
  }
  if (error) { return GHOST_OHOS_CALLBACK_FAILED; }
  return exhausted && pending_detach_acks_.empty() ? GHOST_OHOS_OK : GHOST_OHOS_BUSY;
}
int32_t ghost_ohos_process_lifecycle()
{
  GHOST_OHOSHost *host = ghost_ohos_host_installed();
  if (!host || ghost_ohos_host_is_engine(host) != GHOST_OHOS_OK) { return GHOST_OHOS_WRONG_THREAD; }
  if (auto *system = GHOST_ISystem::getSystem()) {
    /* WITH_GHOST_OHOS selects this concrete factory; do not initialize GHOST
     * during failed/raw creator startup merely to drain unleased host records. */
    return static_cast<GHOST_SystemOHOS *>(system)->processLifecycleEvents();
  }
  static std::map<uint64_t, uint64_t> unleased_detach_acks;
  bool error = false;
  for (auto it = unleased_detach_acks.begin(); it != unleased_detach_acks.end();) {
    const auto result = ghost_ohos_host_ack_detach(host, it->first, it->second);
    if (result == GHOST_OHOS_OK || result == GHOST_OHOS_UNAVAILABLE) {
      it = unleased_detach_acks.erase(it);
    }
    else { error |= result != GHOST_OHOS_BUSY; ++it; }
  }
  bool exhausted = false;
  GHOST_OHOSEvent event{};
  for (int count = 0; count < 256; ++count) {
    const auto result = ghost_ohos_host_pop(host, &event, 0);
    if (result == GHOST_OHOS_EMPTY) { exhausted = true; break; }
    if (result != GHOST_OHOS_OK) { return result; }
    if (event.type == GHOST_OHOS_EVENT_DETACHED) {
      unleased_detach_acks[event.window_id] = event.generation;
      const auto ack = ghost_ohos_host_ack_detach(host, event.window_id, event.generation);
      if (ack == GHOST_OHOS_OK || ack == GHOST_OHOS_UNAVAILABLE) {
        unleased_detach_acks.erase(event.window_id);
      }
      else { error |= ack != GHOST_OHOS_BUSY; }
    }
  }
  if (error) { return GHOST_OHOS_CALLBACK_FAILED; }
  return exhausted && unleased_detach_acks.empty() ? GHOST_OHOS_OK : GHOST_OHOS_BUSY;
}
