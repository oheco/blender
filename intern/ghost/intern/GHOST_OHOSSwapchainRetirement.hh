/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <cstdint>
#include <initializer_list>
#include <limits>
#include <optional>

/* The policy is independent of Vulkan handles so adverse WSI sequences can be tested without
 * inventing a GPU/presentation completion result. The context owns the actual resources/fences. */
struct GHOST_OHOSSwapchainRetirement {
  static constexpr uint32_t max_generations = 8;
  static constexpr uint32_t max_images = 8;
  static constexpr uint64_t max_image_bytes = uint64_t(1024) * 1024 * 1024;
  static constexpr uint64_t settle_ms = 50;

  bool acquired = false;
  bool suboptimal_noted = false;
  std::optional<uint32_t> first_present_image;

  bool note_suboptimal()
  {
    const bool first_notice = !suboptimal_noted;
    suboptimal_noted = true;
    return first_notice;
  }

  void acquired_image()
  {
    acquired = true;
  }
  void queued_present(uint32_t image)
  {
    if (!first_present_image.has_value()) {
      first_present_image = image;
    }
  }
  bool witnesses_ancestors(uint32_t image) const
  {
    return first_present_image.has_value() && first_present_image.value() == image;
  }
  /* Matching the image selects the witness; only completion of THAT acquisition proves it.
   * An old render fence or an unsuccessful acquisition has no bearing on presentation. */
  static bool witness_complete(bool acquisition_succeeded,
                               bool witness_selected,
                               bool acquisition_fence_signaled)
  {
    return acquisition_succeeded && witness_selected && acquisition_fence_signaled;
  }
  static bool host_settled(uint64_t now_ms, uint64_t changed_ms)
  {
    return now_ms >= changed_ms && now_ms - changed_ms >= settle_ms;
  }
  static uint64_t image_bytes(uint32_t width,
                              uint32_t height,
                              uint32_t texel_bytes,
                              uint32_t images)
  {
    uint64_t value = width;
    for (uint32_t factor : {height, texel_bytes, images}) {
      if (factor && value > std::numeric_limits<uint64_t>::max() / factor) {
        return std::numeric_limits<uint64_t>::max();
      }
      value *= factor;
    }
    return value;
  }
  /* Reserve one additional generation AND a same-size recovery payload before retiring
   * oldSwapchain. Only a settled forced recovery (or initial creation) may use that reservation.
   * Bytes bound the color image payload; opaque driver allocation/alignment overhead is not
   * measurable here. */
  static bool admit(uint32_t resident_generations,
                    uint64_t resident_bytes,
                    uint64_t candidate_bytes,
                    bool recovery)
  {
    const uint32_t slots = recovery ? 1 : 2;
    if (resident_generations > max_generations - slots || resident_bytes > max_image_bytes ||
        candidate_bytes > max_image_bytes / slots)
    {
      return false;
    }
    return candidate_bytes * slots <= max_image_bytes - resident_bytes;
  }
};
