/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstdint>

namespace blender {

/** Keep the complete composition until client dimensions stop changing. */
class GPUOHOSResizeSettlement {
 private:
  int width_ = 0;
  int height_ = 0;
  int64_t last_change_ms_ = 0;
  bool pending_ = false;

 public:
  static constexpr int64_t quiet_period_ms = 120;

  void observe(int width, int height, int64_t now_ms)
  {
    if (width <= 0 || height <= 0 || (width == width_ && height == height_)) {
      return;
    }
    pending_ = width_ > 0 && height_ > 0;
    width_ = width;
    height_ = height;
    last_change_ms_ = now_ms;
  }

  bool cache_frame(int64_t now_ms) const
  {
    return pending_ && now_ms - last_change_ms_ < quiet_period_ms;
  }

  bool pending() const
  {
    return pending_;
  }

  void drawn()
  {
    pending_ = false;
  }
};

}  // namespace blender
