/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_Window.hh"
#include "GHOST_OHOSHost.h"
#ifdef WITH_VULKAN_BACKEND
#  include "GHOST_ContextVK.hh"
#endif
#include <set>
class GHOST_SystemOHOS;

class GHOST_WindowOHOS : public GHOST_Window {
 public:
  GHOST_WindowOHOS(GHOST_SystemOHOS *system, GHOST_OHOSHost *host,
                   const GHOST_OHOSWindowInfo &surface, const char *title,
                   GHOST_TWindowState state, const GHOST_GPUSettings &settings,
                   bool is_dialog, const GHOST_ContextParams &params);
  ~GHOST_WindowOHOS() override;
  bool getValid() const override;
  void setTitle(const char *title) override;
  std::string getTitle() const override;
  bool isDialog() const override;
  void getWindowBounds(GHOST_Rect &bounds) const override;
  void getClientBounds(GHOST_Rect &bounds) const override;
  GHOST_TSuccess setClientWidth(uint32_t width) override;
  GHOST_TSuccess setClientHeight(uint32_t height) override;
  GHOST_TSuccess setClientSize(uint32_t width, uint32_t height) override;
  void screenToClient(int32_t x, int32_t y, int32_t &out_x, int32_t &out_y) const override;
  void clientToScreen(int32_t x, int32_t y, int32_t &out_x, int32_t &out_y) const override;
  GHOST_TSuccess setState(GHOST_TWindowState state) override;
  GHOST_TWindowState getState() const override;
  GHOST_TSuccess setOrder(GHOST_TWindowOrder order) override;
  GHOST_TSuccess invalidate() override;
  GHOST_TSuccess hasCursorShape(GHOST_TStandardCursor shape) override;
  uint16_t getDPIHint() override;
  void *getOSWindow() const override;
#ifdef WITH_INPUT_IME
  void beginIME(int32_t x, int32_t y, int32_t w, int32_t h, bool completed) override;
  void endIME() override;
#endif
  uint64_t hostId() const { return surface_.window_id; }
  uint64_t hostGeneration() const { return surface_.generation; }
  void hostResize(const GHOST_OHOSEvent &event);
  GHOST_TSuccess hostDetach();
  GHOST_TSuccess hostAttach(const GHOST_OHOSWindowInfo &surface);
  bool hostAttached() const { return leased_; }
  /* Base touch mapping: first active finger -> left drag; no fake tablet/NDOF.
   * Additional fingers are ignored, not converted to mouse button spam. */
  int32_t touch_id = -1;
  float wheel_remainder = 0.0f, wheel_horizontal_remainder = 0.0f;
  std::set<GHOST_TKey> pressed_keys;
  std::set<GHOST_TButton> pressed_buttons;
 protected:
  GHOST_Context *newDrawingContext(GHOST_TDrawingContextType type) override;
  GHOST_TSuccess setWindowCursorVisibility(bool visible) override;
  GHOST_TSuccess setWindowCursorGrab(GHOST_TGrabCursorMode mode) override;
  GHOST_TSuccess setWindowCursorShape(GHOST_TStandardCursor shape) override;
  GHOST_TSuccess setWindowCustomCursorShape(const uint8_t *, const uint8_t *,
                                           const int[2], const int[2], bool) override;
 private:
  GHOST_SystemOHOS *system_;
  GHOST_OHOSHost *host_;
  GHOST_OHOSWindowInfo surface_;
  GHOST_GPUSettings settings_;
  GHOST_TWindowState state_;
  bool is_dialog_, leased_ = true;
  std::string title_;
#ifdef WITH_VULKAN_BACKEND
  GHOST_ContextVK_WindowInfo window_info_{};
#endif
  GHOST_TSuccess command(uint32_t type, uint32_t width = 0, uint32_t height = 0,
                        int32_t value = 0, const char *text = nullptr);
};
