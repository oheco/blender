/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_OHOSHost.h"
#include <multimodalinput/oh_key_code.h>
#include <unordered_set>
#include <vector>

/* ArkUI supplies the current modifier snapshot even when focus was gained
 * after a modifier was pressed. Reconcile it before the actual physical key.
 * IME text continues to use EVENT_TEXT separately. */
class KeyboardInput {
 public:
  void clear() { pressed_.clear(); }
  bool meta_held() const { return pressed_.count(KEYCODE_META_LEFT) || pressed_.count(KEYCODE_META_RIGHT); }
  std::vector<GHOST_OHOSEvent> update(int32_t code, bool down,
                                    bool ctrl, bool alt, bool shift, bool meta)
  {
    std::vector<GHOST_OHOSEvent> events;
    events.reserve(9);
    auto append = [&](int32_t key, bool pressed) {
      GHOST_OHOSEvent event{};
      event.type = GHOST_OHOS_EVENT_KEY; event.value = key; event.pressed = pressed;
      if (pressed) event.repeat = !pressed_.insert(key).second;
      else pressed_.erase(key);
      events.push_back(event);
    };
    auto modifier = [&](int32_t left, int32_t right, bool held) {
      if (code == left || code == right) return;
      if (held) {
        if (!pressed_.count(left) && !pressed_.count(right)) append(left, true);
      }
      else {
        if (pressed_.count(left)) append(left, false);
        if (pressed_.count(right)) append(right, false);
      }
    };
    modifier(KEYCODE_CTRL_LEFT, KEYCODE_CTRL_RIGHT, ctrl);
    modifier(KEYCODE_ALT_LEFT, KEYCODE_ALT_RIGHT, alt);
    modifier(KEYCODE_SHIFT_LEFT, KEYCODE_SHIFT_RIGHT, shift);
    modifier(KEYCODE_META_LEFT, KEYCODE_META_RIGHT, meta);
    append(code, down);
    return events;
  }
 private:
  std::unordered_set<int32_t> pressed_;
};
