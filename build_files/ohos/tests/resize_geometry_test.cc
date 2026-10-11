/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_OHOSWindowTransition.hh"
#include "GPU_ohos_resize_settlement.hh"
#include "GPU_ohos_window_geometry.hh"

#include <cstdio>
#include <cstdlib>

using blender::GPUOHOSWindowGeometry;
static int checks = 0;
static void require(bool result, const char *scenario)
{
  checks++;
  if (!result) {
    std::fprintf(stderr, "FAILED: %s\n", scenario);
    std::exit(1);
  }
}

int main()
{
  blender::GPUOHOSResizeSettlement settlement;
  settlement.observe(1000, 500, 0);
  require(!settlement.pending() && !settlement.cache_frame(0), "first frame draws immediately");
  settlement.drawn();
  for (int64_t time = 10; time <= 2000; time += 10) {
    settlement.observe(1000 + int(time), 500 + int(time / 2), time);
    require(settlement.cache_frame(time) && settlement.cache_frame(time + 9),
            "continuous resize never periodically commits a layout");
  }
  settlement.observe(3000, 1500, 2119);
  require(settlement.cache_frame(2119), "same-size presents do not restart quiet deadline");
  require(!settlement.cache_frame(2120) && settlement.pending(),
          "final resize becomes ready exactly after 120 ms quiet");
  settlement.drawn();
  require(!settlement.pending() && !settlement.cache_frame(3000),
          "one complete final frame stops resize maintenance");
  settlement.observe(2000, 1000, 4000);
  settlement.observe(3000, 1500, 4040);
  require(settlement.cache_frame(4159), "return to previous draw size still waits for quiet");
  settlement.observe(0, 0, 4090);
  settlement.observe(3000, 1500, 4110);
  require(!settlement.cache_frame(4160), "invalid and repeated sizes do not postpone settlement");
  settlement.drawn();

  GHOST_OHOSWindowTransition transition;
  require(transition.observe(1000, 500, 0, 0) == 0,
          "ordinary samples do not create an animation hold");
  require(transition.observe(1000, 500, 1, 10) == 260, "maximize arms a quiet observation period");
  require(transition.observe(1000, 500, 1, 150) == 260,
          "duplicate mode events do not restart the observation deadline");
  require(transition.observe(1400, 700, 0, 200) == 450,
          "a late size change postpones commit beyond the start-based deadline");
  require(transition.observe(1400, 700, 0, 300) == 450,
          "polling unchanged dimensions does not postpone commit");
  require(transition.observe(0, 0, 1, 350) == 450,
          "minimized zero sizes do not rearm or erase the valid transition");
  require(transition.observe(1400, 700, 0, 449) == 450 &&
              transition.observe(1400, 700, 0, 450) == 0,
          "transition commits after quiet from its last changed size");
  require(transition.observe(1000, 500, 2, 2000) == 2250,
          "recover starts an independent observation");
  require(transition.observe(1400, 700, 1, 2050) == 2300,
          "rapid reversal replaces the pending target and restarts quiet");
  for (int64_t time = 2060; time < 4000; time += 10) {
    require(transition.observe(1400 + uint32_t(time), 700, 0, time) == time + 250,
            "continuously changing sampled size never periodically releases animation hold");
  }
  require(transition.observe(5390, 700, 0, 4239) == 4240 &&
              transition.observe(5390, 700, 0, 4240) == 0,
          "one final commit follows the last sample rather than transition start");

  struct CursorCase {
    GPUOHOSWindowGeometry geometry;
    int client_x, client_y, draw_x, draw_y;
  };
  const CursorCase cases[] = {
      {{1000, 500, 1000, 500}, 0, 0, 0, 499},
      {{1000, 500, 1000, 500}, 999, 499, 999, 0},
      {{1000, 500, 1000, 500}, -1, 500, -1, -1},
      {{1000, 500, 1200, 600}, 600, 250, 500, 291},
      {{1000, 500, 500, 250}, 123, 37, 246, 425},
      {{1920, 1080, 800, 1200}, 400, 600, 960, 539},
      {{1000, 500, 1200, 600}, -12, -6, -10, 504},
      {{1, 1, 64, 128}, 63, 127, 0, 0},
  };
  for (const CursorCase &item : cases) {
    int x = item.client_x, y = item.client_y;
    require(item.geometry.from_client(x, y), "valid native cursor geometry");
    require(x == item.draw_x && y == item.draw_y,
            "scaled image picking, borders and outside drag");
  }

  int x = 600, y = 250;
  GPUOHOSWindowGeometry{1000, 500, 1200, 600}.from_client(x, y);
  require(x == 500 && y == 291, "cursor while cached image is stretched");
  x = 600;
  y = 250; /* Same physical cursor; no new motion event after layout commit. */
  GPUOHOSWindowGeometry{1200, 600, 1200, 600}.from_client(x, y);
  require(x == 600 && y == 349, "stationary cursor after full resize composition");

  x = 100;
  y = 400;
  int width = 0, height = 0;
  require(GPUOHOSWindowGeometry{1000, 500, 1200, 600}.ime_to_client(x, y, width, height),
          "IME geometry during cached presentation");
  require(x == 120 && y == 120 && width == 1 && height == 1,
          "IME top-left edge mapping and nonempty zero-size widget caret");
  x = 100;
  y = 400;
  width = 20;
  height = 15;
  GPUOHOSWindowGeometry{1000, 500, 1200, 600}.ime_to_client(x, y, width, height);
  require(x == 120 && y == 120 && width == 24 && height == 18,
          "IME caret size follows the visible image");

  for (int client_width = 32; client_width <= 4096; client_width += 17) {
    for (int client_height : {32, 251, 600, 1080, 2160}) {
      const GPUOHOSWindowGeometry geometry{1000, 500, client_width, client_height};
      for (int ix = 0; ix <= 10; ix++) {
        for (int iy = 0; iy <= 10; iy++) {
          const int raw_x = ix * (client_width - 1) / 10;
          const int raw_y = iy * (client_height - 1) / 10;
          x = raw_x;
          y = raw_y;
          geometry.from_client(x, y);
          require(x >= 0 && x < 1000 && y >= 0 && y < 500,
                  "continuous resize picking stays inside source pixels");
          geometry.to_client(x, y);
          require(std::abs(x - raw_x) <= int(std::ceil(double(client_width) / 1000)) &&
                      std::abs(y - raw_y) <= int(std::ceil(double(client_height) / 500)),
                  "native cursor warp round trip is bounded by one source pixel");
        }
      }
    }
  }
  const GPUOHOSWindowGeometry invalid[] = {
      {0, 500, 1200, 600}, {1000, 0, 1200, 600}, {1000, 500, 0, 600}, {1000, 500, 1200, 0}};
  for (const auto &geometry : invalid) {
    x = 4;
    y = 5;
    width = 0;
    height = 0;
    require(!geometry.from_client(x, y) && !geometry.to_client(x, y) &&
                !geometry.ime_to_client(x, y, width, height),
            "minimized/detached zero extents reject coordinate conversion");
    require(x == 4 && y == 5, "invalid geometry leaves input intact");
  }
  std::printf("PASS: %d native resize settlement/cursor/IME checks\n", checks);
}
