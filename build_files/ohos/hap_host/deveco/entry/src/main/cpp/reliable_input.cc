/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "reliable_input.h"
namespace {
bool coalescible(const GHOST_OHOSEvent &a, const GHOST_OHOSEvent &b)
{
  const bool transient = a.type == GHOST_OHOS_EVENT_MOTION || a.type == GHOST_OHOS_EVENT_RESIZE ||
      (a.type == GHOST_OHOS_EVENT_TOUCH && a.value == GHOST_OHOS_TOUCH_MOVE);
  return transient && a.type == b.type && a.window_id == b.window_id &&
      a.generation == b.generation && a.touch_id == b.touch_id && a.value == b.value;
}
}
int32_t ReliableInput::submit(const GHOST_OHOSEvent *events, size_t count)
{
  if (!events || count == 0 || count > capacity) { return GHOST_OHOS_INVALID; }
  std::lock_guard<std::mutex> lock(mutex_);
  if (count == 1 && size_) {
    Item &last = items_[(head_ + size_ - 1) % capacity];
    if (!last.detach && coalescible(last.event, events[0])) {
      last.event = events[0]; return GHOST_OHOS_OK;
    }
  }
  /* Reserve a complete text batch before accepting any part of the commit. */
  if (count > capacity - size_) { return GHOST_OHOS_FULL; }
  for (size_t i = 0; i < count; ++i) {
    items_[(head_ + size_) % capacity] = Item{events[i], false};
    ++size_;
  }
  return GHOST_OHOS_OK;
}
int32_t ReliableInput::request_detach(uint64_t id, uint64_t generation)
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (size_ == capacity) { return GHOST_OHOS_FULL; }
  Item item{};
  item.detach = true; item.event.window_id = id; item.event.generation = generation;
  items_[(head_ + size_) % capacity] = item;
  ++size_;
  return GHOST_OHOS_OK;
}
int32_t ReliableInput::flush()
{
  std::lock_guard<std::mutex> lock(mutex_);
  while (size_) {
    const Item &item = items_[head_];
    int32_t result = item.detach ?
        ghost_ohos_host_detach(host_, item.event.window_id, item.event.generation) :
        ghost_ohos_host_push(host_, &item.event);
    if (result == GHOST_OHOS_FULL) { return result; }
    if (result != GHOST_OHOS_OK) { return result; }
    head_ = (head_ + 1) % capacity; --size_;
  }
  return GHOST_OHOS_OK;
}
size_t ReliableInput::pending() const
{
  std::lock_guard<std::mutex> lock(mutex_); return size_;
}
