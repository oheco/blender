/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_System.hh"
#include "GHOST_OHOSHost.h"
#include <map>
class GHOST_WindowOHOS;
class GHOST_SystemOHOS : public GHOST_System {
 public:
  GHOST_SystemOHOS();
  ~GHOST_SystemOHOS() override;
  uint64_t getMilliSeconds() const override;
  bool processEvents(bool waitForEvent) override;
  /* ENGINE only. Bounded native lifecycle/lease work without timers/WM dispatch. */
  int32_t processLifecycleEvents();
  uint8_t getNumDisplays() const override;
  void getMainDisplayDimensions(uint32_t &width, uint32_t &height) const override;
  void getAllDisplayDimensions(uint32_t &width, uint32_t &height) const override;
  GHOST_TCapabilityFlag getCapabilities() const override;
  GHOST_IWindow *createWindow(const char *title, int32_t left, int32_t top,
                             uint32_t width, uint32_t height, GHOST_TWindowState state,
                             GHOST_GPUSettings settings, bool exclusive, bool is_dialog,
                             const GHOST_IWindow *parent) override;
  GHOST_TSuccess disposeWindow(GHOST_IWindow *window) override;
  GHOST_IContext *createOffscreenContext(GHOST_GPUSettings settings) override;
  GHOST_TSuccess disposeContext(GHOST_IContext *context) override;
  GHOST_TSuccess getCursorPosition(int32_t &x, int32_t &y) const override;
  GHOST_TSuccess setCursorPosition(int32_t x, int32_t y) override;
  GHOST_IWindow *getWindowUnderCursor(int32_t x, int32_t y) override;
  GHOST_TSuccess getModifierKeys(GHOST_ModifierKeys &keys) const override;
  GHOST_TSuccess getButtons(GHOST_Buttons &buttons) const override;
  char *getClipboard(bool selection) const override;
  void putClipboard(const char *buffer, bool selection) const override;
  bool setConsoleWindowState(GHOST_TConsoleWindowState action) override;
 protected:
  GHOST_TSuccess init() override;
 private:
  GHOST_OHOSHost *host_;
  std::map<uint64_t, GHOST_WindowOHOS *> windows_;
  std::map<uint64_t, uint64_t> pending_detach_acks_;
  int32_t cursor_x_ = 0, cursor_y_ = 0;
  uint64_t cursor_window_ = 0;
  bool cursor_known_ = false;
  bool handleEvent(const GHOST_OHOSEvent &event);
  int32_t lifecycleDetach(uint64_t id, uint64_t generation);
  int32_t handleLifecycleEvent(const GHOST_OHOSEvent &event);
  void releaseInput(GHOST_WindowOHOS *window, uint64_t time);
  void buttonEvent(GHOST_WindowOHOS *window, GHOST_TButton button, bool pressed, uint64_t time);
};
