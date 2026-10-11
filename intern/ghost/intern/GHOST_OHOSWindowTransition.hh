/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstdint>

/** Window attributes can reach their final size before the compositor finishes animating.
 * This is a quiet observation period, not a presentation/animation completion witness. */
class GHOST_OHOSWindowTransition {
 private:
  uint32_t width_ = 0;
  uint32_t height_ = 0;
  uint32_t reason_ = 0;
  int64_t last_change_ms_ = 0;
  bool active_ = false;

 public:
  static constexpr int64_t quiet_period_ms = 250;

  /** reason: 0 = sample, 1 = maximize, 2 = recover. Duplicate notifications do not extend quiet.
   */
  int64_t observe(uint32_t width, uint32_t height, uint32_t reason, int64_t now_ms)
  {
    if (width == 0 || height == 0 || reason > 2) {
      return deadline(now_ms);
    }
    if (active_ && now_ms >= last_change_ms_ + quiet_period_ms) {
      active_ = false;
    }
    if (reason != 0 && (!active_ || reason != reason_)) {
      reason_ = reason;
      last_change_ms_ = now_ms;
      active_ = true;
    }
    if (width != width_ || height != height_) {
      width_ = width;
      height_ = height;
      if (active_) {
        last_change_ms_ = now_ms;
      }
    }
    return deadline(now_ms);
  }

  int64_t deadline(int64_t now_ms) const
  {
    return active_ && now_ms < last_change_ms_ + quiet_period_ms ?
               last_change_ms_ + quiet_period_ms :
               0;
  }
};
