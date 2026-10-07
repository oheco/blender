/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <cstdint>
// Physical screen geometry and actual ArkUI OS window ID, independent of GHOST ID.
void blender_ime_geometry(int32_t window_id, double left, double top);
int32_t blender_ime_begin(int32_t x, int32_t y, uint32_t width, uint32_t height);
int32_t blender_ime_end();
int32_t blender_ime_retire(uint64_t generation);
bool blender_ime_active();
int32_t blender_ime_focus(bool focused, uint64_t generation = 0);
