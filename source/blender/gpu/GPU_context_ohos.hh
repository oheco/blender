/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

namespace blender {
struct GPUContext;

/** OHOS window composition uses a drawing extent independent of the WSI extent. */
enum class GPUWindowDraw {
  Full,
  Cached,
  /** Leave the last native buffer with the compositor; no acquire, submit or redraw. */
  Hold,
  Retry,
};

enum class GPUWindowDrawPolicy {
  Immediate,
  Resize,
  PresentOnly,
};

/** Call before/after acquire; cached frames leave screen/region redraw requests pending. */
GPUWindowDraw GPU_context_ohos_prepare_window(
    GPUContext *context, int width, int height, GPUWindowDrawPolicy policy, bool acquired);
/** Keep presenting while WSI settlement or retirement needs an acquisition witness. */
bool GPU_context_ohos_needs_redraw(const GPUContext *context);
/** Mark a complete window composition, before submitting it through GHOST. */
void GPU_context_ohos_window_drawn(GPUContext *context);
/** Composition size used by layout/input, also available during a full redraw. */
bool GPU_context_ohos_window_draw_size(const GPUContext *context, int &width, int &height);
}  // namespace blender
