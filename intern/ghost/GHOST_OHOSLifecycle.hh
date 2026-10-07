/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_OHOSHost.h"
/* Internal C++ helper, not a new public host C ABI/export. ENGINE only, bounded
 * detach/release/idle ACK/focus/resize; never timer/input/WM dispatch or attach. */
int32_t ghost_ohos_process_lifecycle();
