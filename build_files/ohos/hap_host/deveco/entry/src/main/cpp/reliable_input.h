/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_OHOSHost.h"
#include <array>
#include <condition_variable>
#include <mutex>
/* Owned, bounded retry FIFO. Only adjacent motion/resize/same-finger MOVE coalesce.
 * FULL is a terminal visible host error at the SDK callback boundary, not silent loss.
 * No temporary SDK event pointer survives the callback. No UI callback waits on queue space. */
class ReliableInput {
 public:
  static constexpr size_t capacity = 8192;
  explicit ReliableInput(GHOST_OHOSHost *host) : host_(host) {}
  int32_t submit(const GHOST_OHOSEvent *events, size_t count);
  int32_t request_detach(uint64_t id, uint64_t generation);
  int32_t flush();
  size_t pending() const;
 private:
  struct Item { GHOST_OHOSEvent event{}; bool detach = false; };
  GHOST_OHOSHost *host_;
  mutable std::mutex mutex_;
  std::array<Item, capacity> items_{};
  size_t head_ = 0, size_ = 0;
};
