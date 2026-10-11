/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <cstdint>
// Actual ArkUI OS window/display IDs and physical surface origin relative to
// that display. The UI caller subtracts the desktop display origin; no SDK
// window query or density conversion runs in the IME callbacks.
void blender_ime_geometry(int32_t window_id, uint32_t display_id,
                          double physicalScreenLeft, double physicalScreenTop);
// Engine tick reconciles UI-published geometry/focus without synchronous UI queries.
int32_t blender_ime_sync();
int32_t blender_ime_begin(int32_t x, int32_t y, uint32_t width, uint32_t height);
int32_t blender_ime_end();
int32_t blender_ime_retire(uint64_t generation);
bool blender_ime_active();
int32_t blender_ime_focus(bool focused, uint64_t generation = 0);
