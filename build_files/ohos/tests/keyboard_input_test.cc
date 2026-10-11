/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "keyboard_input.h"
#include <cassert>
#include <cstdio>
int main()
{
  KeyboardInput keys;
  // A modifier pressed before focus must precede the shortcut's physical key.
  auto events = keys.update(KEYCODE_X, true, true, false, false, false);
  assert(events.size() == 2 && events[0].value == KEYCODE_CTRL_LEFT && events[0].pressed);
  assert(events[1].value == KEYCODE_X && events[1].pressed && !events[1].repeat);
  events = keys.update(KEYCODE_X, true, true, false, false, false);
  assert(events.size() == 1 && events[0].repeat);
  events = keys.update(KEYCODE_X, false, true, false, false, false);
  assert(events.size() == 1 && !events[0].pressed && !events[0].repeat);
  // Missing modifier-up during another app's focus cannot leave Ctrl stuck.
  events = keys.update(KEYCODE_DPAD_LEFT, true, false, false, false, false);
  assert(events.size() == 2 && events[0].value == KEYCODE_CTRL_LEFT && !events[0].pressed);
  assert(events[1].value == KEYCODE_DPAD_LEFT && events[1].pressed);
  keys.clear();
  events = keys.update(KEYCODE_CTRL_RIGHT, true, true, false, false, false);
  assert(events.size() == 1 && events[0].value == KEYCODE_CTRL_RIGHT);
  events = keys.update(KEYCODE_C, true, true, false, false, false);
  assert(events.size() == 1 && events[0].value == KEYCODE_C);
  events = keys.update(KEYCODE_C, false, false, false, false, false);
  assert(events.size() == 2 && events[0].value == KEYCODE_CTRL_RIGHT && !events[0].pressed);
  // Focus reset also resets repeat detection; Shift/Meta are independent.
  keys.clear();
  events = keys.update(KEYCODE_A, true, false, false, true, true);
  assert(events.size() == 3 && events[0].value == KEYCODE_SHIFT_LEFT && events[1].value == KEYCODE_META_LEFT);
  assert(events[2].value == KEYCODE_A && !events[2].repeat && events[2].text_bytes == 0);
  std::puts("PASS: 8 focused physical-key/modifier cases");
}
