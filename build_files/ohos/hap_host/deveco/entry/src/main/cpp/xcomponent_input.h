/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_OHOSHost.h"
#include <ace/xcomponent/native_interface_xcomponent.h>
#include <string_view>
#include <vector>
/* Real XComponent types; coordinates are element-relative native physical px.
 * ArkUI wheel bridge converts vp separately and passes explicit detents. */
int32_t blender_xcomponent_mouse(const OH_NativeXComponent_MouseEvent &, uint64_t, uint64_t, GHOST_OHOSEvent &);
int32_t blender_xcomponent_touch(const OH_NativeXComponent_TouchEvent &, uint64_t, uint64_t, GHOST_OHOSEvent &);
int32_t blender_utf8_commit(std::string_view, uint64_t, uint64_t, std::vector<GHOST_OHOSEvent> &);
