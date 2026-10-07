/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "xcomponent_input.h"
#include <cmath>
#include <cstring>
#include <limits>
namespace {
GHOST_OHOSEvent event(uint32_t type, uint64_t id, uint64_t generation)
{
  GHOST_OHOSEvent out{}; out.struct_size = sizeof(out); out.type = type;
  out.window_id = id; out.generation = generation; return out;
}
bool coordinate(float value, int32_t &out)
{
  if (!std::isfinite(value) || double(value) < INT32_MIN || double(value) > INT32_MAX) { return false; }
  out = int32_t(std::lround(value)); return true;
}
}
int32_t blender_xcomponent_mouse(const OH_NativeXComponent_MouseEvent &native,
    uint64_t id, uint64_t generation, GHOST_OHOSEvent &out)
{
  out = event(GHOST_OHOS_EVENT_MOTION, id, generation);
  if (!coordinate(native.x, out.x) || !coordinate(native.y, out.y)) { return GHOST_OHOS_INVALID; }
  if (native.action == OH_NATIVEXCOMPONENT_MOUSE_MOVE) { return GHOST_OHOS_OK; }
  if (native.action != OH_NATIVEXCOMPONENT_MOUSE_PRESS && native.action != OH_NATIVEXCOMPONENT_MOUSE_RELEASE) {
    return GHOST_OHOS_EMPTY; /* API15 has no XComponent mouse-wheel or cancel action. */
  }
  out.type = GHOST_OHOS_EVENT_BUTTON;
  switch (native.button) {
    case OH_NATIVEXCOMPONENT_LEFT_BUTTON: out.value = GHOST_OHOS_BUTTON_LEFT; break;
    case OH_NATIVEXCOMPONENT_MIDDLE_BUTTON: out.value = GHOST_OHOS_BUTTON_MIDDLE; break;
    case OH_NATIVEXCOMPONENT_RIGHT_BUTTON: out.value = GHOST_OHOS_BUTTON_RIGHT; break;
    case OH_NATIVEXCOMPONENT_BACK_BUTTON: out.value = GHOST_OHOS_BUTTON_BACK; break;
    case OH_NATIVEXCOMPONENT_FORWARD_BUTTON: out.value = GHOST_OHOS_BUTTON_FORWARD; break;
    default: return GHOST_OHOS_EMPTY;
  }
  out.pressed = native.action == OH_NATIVEXCOMPONENT_MOUSE_PRESS;
  return GHOST_OHOS_OK;
}
int32_t blender_xcomponent_touch(const OH_NativeXComponent_TouchEvent &native,
    uint64_t id, uint64_t generation, GHOST_OHOSEvent &out)
{
  out = event(GHOST_OHOS_EVENT_TOUCH, id, generation);
  if (native.id < 0 || !coordinate(native.x, out.x) || !coordinate(native.y, out.y)) { return GHOST_OHOS_INVALID; }
  out.touch_id = native.id;
  switch (native.type) {
    case OH_NATIVEXCOMPONENT_DOWN: out.value = GHOST_OHOS_TOUCH_DOWN; break;
    case OH_NATIVEXCOMPONENT_MOVE: out.value = GHOST_OHOS_TOUCH_MOVE; break;
    case OH_NATIVEXCOMPONENT_UP: out.value = GHOST_OHOS_TOUCH_UP; break;
    case OH_NATIVEXCOMPONENT_CANCEL: out.value = GHOST_OHOS_TOUCH_CANCEL; break;
    default: return GHOST_OHOS_EMPTY;
  }
  return GHOST_OHOS_OK;
}
int32_t blender_utf8_commit(std::string_view text, uint64_t id, uint64_t generation,
    std::vector<GHOST_OHOSEvent> &out)
{
  out.clear();
  if (text.empty() || text.size() > 1024 * 1024) { return GHOST_OHOS_INVALID; }
  auto chunk = event(GHOST_OHOS_EVENT_TEXT, id, generation);
  for (size_t offset = 0; offset < text.size();) {
    const size_t start = offset;
    const auto lead = static_cast<unsigned char>(text[offset++]);
    uint32_t scalar = lead, continuation = 0, minimum = 0;
    if (!lead) { return GHOST_OHOS_INVALID; }
    if (lead < 0x80) {}
    else if (lead >= 0xc2 && lead <= 0xdf) { scalar = lead & 31; continuation = 1; minimum = 0x80; }
    else if (lead >= 0xe0 && lead <= 0xef) { scalar = lead & 15; continuation = 2; minimum = 0x800; }
    else if (lead >= 0xf0 && lead <= 0xf4) { scalar = lead & 7; continuation = 3; minimum = 0x10000; }
    else { return GHOST_OHOS_INVALID; }
    if (offset + continuation > text.size()) { return GHOST_OHOS_INVALID; }
    for (uint32_t j = 0; j < continuation; ++j) {
      const auto next = static_cast<unsigned char>(text[offset++]);
      if ((next & 0xc0) != 0x80) { return GHOST_OHOS_INVALID; }
      scalar = (scalar << 6) | (next & 63);
    }
    if (scalar < minimum || scalar > 0x10ffff || (scalar >= 0xd800 && scalar <= 0xdfff)) { return GHOST_OHOS_INVALID; }
    const size_t bytes = offset - start;
    if (chunk.text_bytes + bytes >= GHOST_OHOS_TEXT_MAX) { out.push_back(chunk); chunk.text_bytes = 0; }
    std::memcpy(chunk.text + chunk.text_bytes, text.data() + start, bytes);
    chunk.text_bytes += uint32_t(bytes); chunk.text[chunk.text_bytes] = '\0';
  }
  if (chunk.text_bytes) { out.push_back(chunk); }
  return GHOST_OHOS_OK;
}
