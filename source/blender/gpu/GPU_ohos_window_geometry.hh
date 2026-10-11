/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <algorithm>
#include <cmath>

namespace blender {
/** Source pixels use Blender's bottom-left origin; native client pixels use top-left. */
struct GPUOHOSWindowGeometry {
  int draw_width, draw_height, client_width, client_height;

  bool valid() const
  {
    return draw_width > 0 && draw_height > 0 && client_width > 0 && client_height > 0;
  }

  bool from_client(int &x, int &y) const
  {
    if (!valid()) {
      return false;
    }
    x = int(std::floor(double(x) * draw_width / client_width));
    y = draw_height - 1 - int(std::floor(double(y) * draw_height / client_height));
    return true;
  }

  bool to_client(int &x, int &y) const
  {
    if (!valid()) {
      return false;
    }
    x = int(std::floor(double(x) * client_width / draw_width));
    y = int(std::floor(double(draw_height - y - 1) * client_height / draw_height));
    return true;
  }

  bool ime_to_client(int &x, int &y, int &width, int &height) const
  {
    if (!valid()) {
      return false;
    }
    /* IME anchors describe edges, unlike the pixel centers used for cursor coordinates. */
    x = int(std::floor(double(x) * client_width / draw_width));
    y = int(std::ceil(double(draw_height - y) * client_height / draw_height));
    width = std::max(1, int(std::ceil(double(width) * client_width / draw_width)));
    height = std::max(1, int(std::ceil(double(height) * client_height / draw_height)));
    return true;
  }
};
}  // namespace blender
