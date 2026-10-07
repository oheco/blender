/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_WindowOHOS.hh"
#include "GHOST_SystemOHOS.hh"
#include "GHOST_ContextNone.hh"
#include "GHOST_Event.hh"
#include "GHOST_Rect.hh"
#include <memory>
#include <cstdio>
#include <algorithm>
GHOST_WindowOHOS::GHOST_WindowOHOS(GHOST_SystemOHOS *system, GHOST_OHOSHost *host,
                                   const GHOST_OHOSWindowInfo &surface, const char *title,
                                   GHOST_TWindowState state, const GHOST_GPUSettings &settings,
                                   bool is_dialog, const GHOST_ContextParams &params)
    : GHOST_Window(surface.width, surface.height, state, params, false),
      system_(system), host_(host), surface_(surface), settings_(settings),
      state_(state), is_dialog_(is_dialog), title_(title ? title : "Blender")
{
  /* Bounds, cursor/input and Vulkan extent are already physical pixels.
   * WM multiplies all of these by nativePixelSize; multiplying density again is wrong.
   * Keep surface.scale as host metadata and use independent DPIHint for UI scaling. */
  native_pixel_size_ = 1.0f;
#ifdef WITH_VULKAN_BACKEND
  window_info_.size[0] = int(surface.width); window_info_.size[1] = int(surface.height);
#endif
  setDrawingContextType(settings.context_type);
}
GHOST_WindowOHOS::~GHOST_WindowOHOS()
{
  /* Base destructor runs AFTER this destructor: explicitly destroy the Vulkan
   * surface/swapchain while OHNativeWindow is still retained and window_info_
   * still exists, then return the registry lease. */
  hostDetach();
  setDrawingContextType(GHOST_kDrawingContextTypeNone);
  if (leased_) ghost_ohos_host_release_window(host_, hostId(), hostGeneration());
  ghost_ohos_host_ack_detach(host_, hostId(), hostGeneration());
}
bool GHOST_WindowOHOS::getValid() const
{
  return leased_ && surface_.native_window && GHOST_Window::getValid() &&
         drawing_context_type_ == settings_.context_type;
}
GHOST_TSuccess GHOST_WindowOHOS::command(uint32_t type, uint32_t width, uint32_t height,
                                        int32_t value, const char *text)
{
  if (!leased_) return GHOST_kFailure;
  GHOST_OHOSCommand cmd{};
  cmd.struct_size = sizeof(cmd); cmd.type = type; cmd.window_id = hostId();
  cmd.width = width; cmd.height = height; cmd.value = value; cmd.text = text;
  return ghost_ohos_host_command(host_, &cmd, nullptr) == GHOST_OHOS_OK ? GHOST_kSuccess : GHOST_kFailure;
}
void GHOST_WindowOHOS::setTitle(const char *title)
{
  if (!title) return;
  title_ = title;
  command(GHOST_OHOS_COMMAND_TITLE, 0, 0, 0, title_.c_str());
}
std::string GHOST_WindowOHOS::getTitle() const { return title_; }
bool GHOST_WindowOHOS::isDialog() const { return is_dialog_; }
void GHOST_WindowOHOS::getWindowBounds(GHOST_Rect &bounds) const { getClientBounds(bounds); }
void GHOST_WindowOHOS::getClientBounds(GHOST_Rect &bounds) const
{
  bounds.set(0, 0, int32_t(surface_.width), int32_t(surface_.height));
}
GHOST_TSuccess GHOST_WindowOHOS::setClientWidth(uint32_t width) { return setClientSize(width, surface_.height); }
GHOST_TSuccess GHOST_WindowOHOS::setClientHeight(uint32_t height) { return setClientSize(surface_.width, height); }
GHOST_TSuccess GHOST_WindowOHOS::setClientSize(uint32_t width, uint32_t height)
{
  if (!width || !height || width > INT32_MAX || height > INT32_MAX) return GHOST_kFailure;
  /* Accepted requests do not fabricate an actual resize; host sends EVENT_RESIZE. */
  return command(GHOST_OHOS_COMMAND_SIZE, width, height);
}
void GHOST_WindowOHOS::screenToClient(int32_t x, int32_t y, int32_t &out_x, int32_t &out_y) const { out_x = x; out_y = y; }
void GHOST_WindowOHOS::clientToScreen(int32_t x, int32_t y, int32_t &out_x, int32_t &out_y) const { out_x = x; out_y = y; }
GHOST_TSuccess GHOST_WindowOHOS::setState(GHOST_TWindowState state)
{
  const auto result = command(GHOST_OHOS_COMMAND_STATE, 0, 0, int32_t(state));
  if (result == GHOST_kSuccess) state_ = state;
  return result;
}
GHOST_TWindowState GHOST_WindowOHOS::getState() const { return state_; }
GHOST_TSuccess GHOST_WindowOHOS::setOrder(GHOST_TWindowOrder order) { return command(GHOST_OHOS_COMMAND_ORDER, 0, 0, int32_t(order)); }
GHOST_TSuccess GHOST_WindowOHOS::invalidate()
{
  if (!leased_ || ghost_ohos_host_is_engine(host_) != GHOST_OHOS_OK) return GHOST_kFailure;
  return system_->pushEvent(std::make_unique<GHOST_Event>(system_->getMilliSeconds(), GHOST_kEventWindowUpdate, this));
}
GHOST_TSuccess GHOST_WindowOHOS::hasCursorShape(GHOST_TStandardCursor) { return GHOST_kFailure; }
uint16_t GHOST_WindowOHOS::getDPIHint() { return uint16_t(surface_.dpi); }
void *GHOST_WindowOHOS::getOSWindow() const { return surface_.native_window; }
#ifdef WITH_INPUT_IME
void GHOST_WindowOHOS::beginIME(int32_t x, int32_t y, int32_t w, int32_t h, bool completed)
{
  /* WM already converts bottom-left Blender coordinates to top-left native
   * client pixels. The host supplies the real OS window and screen offset. */
  char position[64];
  std::snprintf(position, sizeof(position), "%d,%d", x, y);
  command(GHOST_OHOS_COMMAND_IME_BEGIN, uint32_t(std::max(w, 1)),
          uint32_t(std::max(h, 1)), completed, position);
}
void GHOST_WindowOHOS::endIME()
{
  command(GHOST_OHOS_COMMAND_IME_END);
}
#endif
void GHOST_WindowOHOS::hostResize(const GHOST_OHOSEvent &event)
{
  surface_.width = event.width; surface_.height = event.height;
  surface_.dpi = event.dpi; surface_.scale = event.scale; native_pixel_size_ = 1.0f;
#ifdef WITH_VULKAN_BACKEND
  window_info_.size[0] = int(event.width); window_info_.size[1] = int(event.height);
#endif
}
GHOST_TSuccess GHOST_WindowOHOS::hostDetach()
{
  if (!leased_) return GHOST_kSuccess;
#ifdef WITH_VULKAN_BACKEND
  if (drawing_context_type_ == GHOST_kDrawingContextTypeVulkan) {
    auto *context = static_cast<GHOST_ContextVK *>(getContext());
    if (context->detachOHOSWindow() != GHOST_kSuccess) return GHOST_kFailure;
  }
#endif
  /* Keep the GPU device/context alive until Blender's own GPU teardown. */
  if (ghost_ohos_host_release_window(host_, hostId(), hostGeneration()) != GHOST_OHOS_OK) return GHOST_kFailure;
  leased_ = false; surface_.native_window = nullptr;
  return GHOST_kSuccess;
}
GHOST_TSuccess GHOST_WindowOHOS::hostAttach(const GHOST_OHOSWindowInfo &surface)
{
  if (leased_ || surface.window_id != hostId()) return GHOST_kFailure;
#ifdef WITH_VULKAN_BACKEND
  window_info_.size[0] = int(surface.width); window_info_.size[1] = int(surface.height);
  if (drawing_context_type_ == GHOST_kDrawingContextTypeVulkan) {
    auto *context = static_cast<GHOST_ContextVK *>(getContext());
    if (context->attachOHOSWindow(surface.native_window, &window_info_) != GHOST_kSuccess) return GHOST_kFailure;
  }
#endif
  surface_ = surface; native_pixel_size_ = 1.0f; leased_ = true;
  return GHOST_kSuccess;
}
GHOST_Context *GHOST_WindowOHOS::newDrawingContext(GHOST_TDrawingContextType type)
{
  if (type == GHOST_kDrawingContextTypeNone) return new GHOST_ContextNone(want_context_params_);
#ifdef WITH_VULKAN_BACKEND
  if (type == GHOST_kDrawingContextTypeVulkan && surface_.native_window) {
    /* OHOS platform is explicit; do not smuggle the handle through X11/Wayland fields.
     * API 1.3 is needed for shader-tile-image feature negotiation by the GPU port. */
    auto *context = new GHOST_ContextVK(want_context_params_, GHOST_kVulkanPlatformOHOS,
        nullptr, nullptr, nullptr, nullptr, nullptr, 1, 3, settings_.preferred_device,
        &hdr_info_, surface_.native_window, &window_info_);
    if (context->initializeDrawingContext() == GHOST_kSuccess) return context;
    delete context;
  }
#endif
  return nullptr; /* Desktop OpenGL is not advertised/emulated through GLES. */
}
GHOST_TSuccess GHOST_WindowOHOS::setWindowCursorVisibility(bool) { return GHOST_kFailure; }
GHOST_TSuccess GHOST_WindowOHOS::setWindowCursorGrab(GHOST_TGrabCursorMode mode)
{
  return mode == GHOST_kGrabDisable ? GHOST_kSuccess : GHOST_kFailure;
}
GHOST_TSuccess GHOST_WindowOHOS::setWindowCursorShape(GHOST_TStandardCursor) { return GHOST_kFailure; }
GHOST_TSuccess GHOST_WindowOHOS::setWindowCustomCursorShape(const uint8_t *, const uint8_t *,
                                                          const int[2], const int[2], bool) { return GHOST_kFailure; }
