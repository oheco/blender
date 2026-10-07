/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_OHOSHost.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <new>
#include <thread>
#include <vector>

struct GHOST_OHOSHost {
  struct Surface {
    GHOST_OHOSWindowInfo info{};
    bool present = false, detaching = false, leased = false;
  };
  std::mutex mutex;
  std::condition_variable ready, detached;
  std::vector<GHOST_OHOSEvent> queue;
  std::array<Surface, GHOST_OHOS_MAX_WINDOWS> surfaces{};
  GHOST_OHOSPaths paths{};
  GHOST_OHOSCallbacks callbacks{};
  GHOST_OHOSStats stats{};
  size_t head = 0, count = 0;
  uint64_t main_id = 0, generation = 0, sequence = 0;
  bool bound = false, run_pinned = false, run_used = false;
  uint64_t wake_generation = 0;
  std::thread::id engine;
  Surface *surface(uint64_t id) {
    for (auto &s : surfaces) if (s.present && s.info.window_id == id) return &s;
    return nullptr;
  }
  bool owner() const { return bound && engine == std::this_thread::get_id(); }
  int32_t enqueue(GHOST_OHOSEvent event) {
    event.sequence = ++sequence;
    event.timestamp_ms = ghost_ohos_monotonic_ms();
    if (count) {
      auto &tail = queue[(head + count - 1) % queue.size()];
      const bool transient = event.type == GHOST_OHOS_EVENT_MOTION ||
                             event.type == GHOST_OHOS_EVENT_RESIZE ||
                             (event.type == GHOST_OHOS_EVENT_TOUCH && event.value == GHOST_OHOS_TOUCH_MOVE);
      if (transient && tail.type == event.type && tail.window_id == event.window_id &&
          tail.generation == event.generation && tail.value == event.value &&
          tail.touch_id == event.touch_id) {
        tail = event;
        ++stats.coalesced;
        ++stats.accepted;
        ready.notify_one();
        return GHOST_OHOS_OK;
      }
    }
    if (count == queue.size()) { ++stats.full; return GHOST_OHOS_FULL; }
    queue[(head + count) % queue.size()] = event;
    ++count;
    ++stats.accepted;
    ready.notify_one();
    return GHOST_OHOS_OK;
  }
};
namespace {
std::atomic<GHOST_OHOSHost *> installed{nullptr};
template<typename F> int32_t guard(F &&fn) noexcept {
  try { return fn(); }
  catch (const std::bad_alloc &) { return GHOST_OHOS_NO_MEMORY; }
  catch (...) { return GHOST_OHOS_CALLBACK_FAILED; }
}
bool path_copy(char *dst, const char *src) {
  if (!src || src[0] != '/') return false;
  const size_t n = strnlen(src, GHOST_OHOS_PATH_MAX);
  if (!n || n == GHOST_OHOS_PATH_MAX) return false;
  for (size_t i = 0; i < n; ++i) if (static_cast<unsigned char>(src[i]) < 32) return false;
  for (size_t i = 0; i < n;) {
    while (i < n && src[i] == '/') ++i;
    const size_t start = i;
    while (i < n && src[i] != '/') ++i;
    if (i - start == 2 && src[start] == '.' && src[start + 1] == '.') return false;
  }
  std::memcpy(dst, src, n + 1);
  return true;
}
bool utf8_valid(const char *text, uint32_t n) {
  for (uint32_t i = 0; i < n;) {
    const auto c = static_cast<unsigned char>(text[i++]);
    if (c == 0) return false;
    if (c < 0x80) continue;
    uint32_t cp = 0, extra = 0, minimum = 0;
    if (c >= 0xc2 && c <= 0xdf) { cp = c & 31; extra = 1; minimum = 0x80; }
    else if (c >= 0xe0 && c <= 0xef) { cp = c & 15; extra = 2; minimum = 0x800; }
    else if (c >= 0xf0 && c <= 0xf4) { cp = c & 7; extra = 3; minimum = 0x10000; }
    else return false;
    if (i + extra > n) return false;
    while (extra--) {
      const auto next = static_cast<unsigned char>(text[i++]);
      if ((next & 0xc0) != 0x80) return false;
      cp = (cp << 6) | (next & 63);
    }
    if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return false;
  }
  return true;
}
bool geometry_valid(const GHOST_OHOSWindowInfo &w) {
  return w.width > 0 && w.height > 0 && w.width <= INT32_MAX && w.height <= INT32_MAX &&
         w.dpi > 0 && w.dpi <= UINT16_MAX && std::isfinite(w.scale) && w.scale > 0.0f;
}
}
extern "C" uint64_t ghost_ohos_monotonic_ms(void) noexcept {
  return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
extern "C" int32_t ghost_ohos_host_create(const GHOST_OHOSConfig *config, GHOST_OHOSHost **out) noexcept {
  if (!out) return GHOST_OHOS_INVALID;
  *out = nullptr;
  if (!config || config->struct_size != sizeof(*config) ||
      config->abi_version != GHOST_OHOS_ABI_VERSION || config->queue_capacity < 4 ||
      config->queue_capacity > 65536 || config->callbacks.struct_size != sizeof(config->callbacks) ||
      !config->callbacks.native_retain || !config->callbacks.native_release) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    GHOST_OHOSPaths paths{};
    if (!path_copy(paths.runtime, config->runtime_path) || !path_copy(paths.config, config->config_path) ||
        !path_copy(paths.cache, config->cache_path) || !path_copy(paths.temp, config->temp_path)) return GHOST_OHOS_INVALID;
    auto *host = new GHOST_OHOSHost;
    try { host->queue.resize(config->queue_capacity); }
    catch (...) { delete host; throw; }
    host->callbacks = config->callbacks;
    host->paths = paths;
    *out = host;
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_destroy(GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::unique_lock<std::mutex> lock(host->mutex);
    if (installed.load() == host || host->run_pinned) return GHOST_OHOS_BUSY;
    for (auto &s : host->surfaces) if (s.leased) return GHOST_OHOS_BUSY;
    for (auto &s : host->surfaces) if (s.present) {
      if (host->callbacks.native_release(host->callbacks.userdata, s.info.native_window) != 0) return GHOST_OHOS_CALLBACK_FAILED;
      s.present = false;
    }
    lock.unlock();
    delete host;
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_bind_engine(GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (host->bound && !host->owner()) return GHOST_OHOS_WRONG_THREAD;
    host->bound = true; host->engine = std::this_thread::get_id();
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_is_engine(const GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(const_cast<GHOST_OHOSHost *>(host)->mutex);
    return host->owner() ? GHOST_OHOS_OK : GHOST_OHOS_WRONG_THREAD;
  });
}
extern "C" int32_t ghost_ohos_host_begin_run(GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (host->run_pinned || host->run_used || host->bound) return GHOST_OHOS_BUSY;
    host->run_pinned = true; host->run_used = true; return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_end_run(GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (!host->run_pinned) return GHOST_OHOS_INVALID;
    host->run_pinned = false; return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_wake(GHOST_OHOSHost *host) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    ++host->wake_generation; host->ready.notify_one(); return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_attach(GHOST_OHOSHost *host, const GHOST_OHOSWindowInfo *info, uint64_t *gen) noexcept {
  if (!host || !info || !gen || !info->window_id || !info->native_window || !geometry_valid(*info)) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (host->surface(info->window_id)) return GHOST_OHOS_BUSY;
    if (host->count == host->queue.size()) { ++host->stats.full; return GHOST_OHOS_FULL; }
    GHOST_OHOSHost::Surface *slot = nullptr;
    for (auto &s : host->surfaces) if (!s.present) { slot = &s; break; }
    if (!slot) return GHOST_OHOS_FULL;
    if (host->callbacks.native_retain(host->callbacks.userdata, info->native_window) != 0) return GHOST_OHOS_CALLBACK_FAILED;
    slot->info = *info; slot->info.generation = ++host->generation;
    slot->present = true; slot->detaching = false; slot->leased = false;
    *gen = slot->info.generation;
    if (!host->main_id) host->main_id = info->window_id;
    GHOST_OHOSEvent event{};
    event.struct_size = sizeof(event); event.type = GHOST_OHOS_EVENT_ATTACHED;
    event.window_id = info->window_id; event.generation = *gen;
    return host->enqueue(event);
  });
}
extern "C" int32_t ghost_ohos_host_detach(GHOST_OHOSHost *host, uint64_t id, uint64_t gen) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    auto *s = host->surface(id);
    if (!s || s->info.generation != gen) return GHOST_OHOS_UNAVAILABLE;
    if (s->detaching) return GHOST_OHOS_BUSY;
    GHOST_OHOSEvent event{}; event.struct_size = sizeof(event);
    event.type = GHOST_OHOS_EVENT_DETACHED; event.window_id = id; event.generation = gen;
    const int32_t result = host->enqueue(event);
    if (result == GHOST_OHOS_OK) s->detaching = true;
    return result;
  });
}
extern "C" int32_t ghost_ohos_host_push(GHOST_OHOSHost *host, const GHOST_OHOSEvent *event) noexcept {
  if (!host || !event || event->struct_size != sizeof(*event) ||
      event->type < GHOST_OHOS_EVENT_RESIZE || event->type > GHOST_OHOS_EVENT_CANCEL_INPUT) return GHOST_OHOS_INVALID;
  if (event->type == GHOST_OHOS_EVENT_TEXT &&
      (!event->text_bytes || event->text_bytes >= GHOST_OHOS_TEXT_MAX ||
       !utf8_valid(event->text, event->text_bytes))) return GHOST_OHOS_INVALID;
  if (event->type == GHOST_OHOS_EVENT_WHEEL &&
      (!std::isfinite(event->wheel_x) || !std::isfinite(event->wheel_y) ||
       std::fabs(event->wheel_x) > 10000 || std::fabs(event->wheel_y) > 10000)) return GHOST_OHOS_INVALID;
  if (event->type == GHOST_OHOS_EVENT_BUTTON && (event->value < 1 || event->value > 5)) return GHOST_OHOS_INVALID;
  if (event->type == GHOST_OHOS_EVENT_TOUCH &&
      (event->value < GHOST_OHOS_TOUCH_DOWN || event->value > GHOST_OHOS_TOUCH_CANCEL || event->touch_id < 0)) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    auto *s = host->surface(event->window_id);
    if (!s && event->type == GHOST_OHOS_EVENT_CLOSE) {
      for (auto &record : host->surfaces)
        if (record.info.window_id == event->window_id && record.info.generation == event->generation) { s = &record; break; }
    }
    if (!s || s->info.generation != event->generation ||
        (s->detaching && event->type != GHOST_OHOS_EVENT_CLOSE)) return GHOST_OHOS_UNAVAILABLE;
    GHOST_OHOSWindowInfo geometry = s->info;
    if (event->type == GHOST_OHOS_EVENT_RESIZE) {
      geometry.width = event->width; geometry.height = event->height;
      geometry.dpi = event->dpi; geometry.scale = event->scale;
      if (!geometry_valid(geometry)) return GHOST_OHOS_INVALID;
    }
    auto copy = *event;
    copy.text[copy.type == GHOST_OHOS_EVENT_TEXT ? copy.text_bytes : 0] = '\0';
    const int32_t result = host->enqueue(copy);
    if (result == GHOST_OHOS_OK && event->type == GHOST_OHOS_EVENT_RESIZE) s->info = geometry;
    return result;
  });
}
extern "C" int32_t ghost_ohos_host_pop(GHOST_OHOSHost *host, GHOST_OHOSEvent *out, uint32_t timeout) noexcept {
  if (!host || !out || timeout > 60000) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::unique_lock<std::mutex> lock(host->mutex);
    if (!host->owner()) return GHOST_OHOS_WRONG_THREAD;
    const uint64_t wake = host->wake_generation;
    if (!host->count && timeout) host->ready.wait_for(lock, std::chrono::milliseconds(timeout), [&]() { return host->count != 0 || host->wake_generation != wake; });
    if (!host->count) return GHOST_OHOS_EMPTY;
    *out = host->queue[host->head]; host->head = (host->head + 1) % host->queue.size(); --host->count;
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_acquire_window(GHOST_OHOSHost *host, uint64_t id, GHOST_OHOSWindowInfo *out) noexcept {
  if (!host || !out) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (!host->owner()) return GHOST_OHOS_WRONG_THREAD;
    auto *s = host->surface(id);
    if (!s || s->detaching) return GHOST_OHOS_UNAVAILABLE;
    if (s->leased) return GHOST_OHOS_BUSY;
    s->leased = true; *out = s->info;
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_release_window(GHOST_OHOSHost *host, uint64_t id, uint64_t gen) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (!host->owner()) return GHOST_OHOS_WRONG_THREAD;
    auto *s = host->surface(id);
    if (!s || s->info.generation != gen || !s->leased) return GHOST_OHOS_UNAVAILABLE;
    s->leased = false;
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_ack_detach(GHOST_OHOSHost *host, uint64_t id, uint64_t gen) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    if (!host->owner()) return GHOST_OHOS_WRONG_THREAD;
    auto *s = host->surface(id);
    if (!s || s->info.generation != gen || !s->detaching) return GHOST_OHOS_UNAVAILABLE;
    if (s->leased) return GHOST_OHOS_BUSY;
    if (host->callbacks.native_release(host->callbacks.userdata, s->info.native_window) != 0) return GHOST_OHOS_CALLBACK_FAILED;
    s->present = false; s->info.native_window = nullptr;
    if (host->main_id == id) host->main_id = 0;
    host->detached.notify_all();
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_wait_detached(GHOST_OHOSHost *host, uint64_t id, uint64_t gen, uint32_t timeout) noexcept {
  if (!host || !id || !gen || timeout > 60000) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::unique_lock<std::mutex> lock(host->mutex);
    const auto done = [&]() { auto *s = host->surface(id); return !s || s->info.generation != gen; };
    if (done()) return GHOST_OHOS_OK;
    if (host->owner()) return GHOST_OHOS_WRONG_THREAD; /* Never block consumer on itself. */
    return host->detached.wait_for(lock, std::chrono::milliseconds(timeout), done) ? GHOST_OHOS_OK : GHOST_OHOS_BUSY;
  });
}
extern "C" int32_t ghost_ohos_host_set_main_window(GHOST_OHOSHost *host, uint64_t id) noexcept {
  if (!host) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex);
    auto *s = host->surface(id);
    if (!s || s->detaching) return GHOST_OHOS_UNAVAILABLE;
    host->main_id = id; return GHOST_OHOS_OK;
  });
}
extern "C" uint64_t ghost_ohos_host_main_window(GHOST_OHOSHost *host) noexcept {
  if (!host) return 0;
  try { std::lock_guard<std::mutex> lock(host->mutex); return host->main_id; }
  catch (...) { return 0; }
}
extern "C" int32_t ghost_ohos_host_get_window(GHOST_OHOSHost *host, uint64_t id, GHOST_OHOSWindowInfo *out) noexcept {
  if (!host || !out) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex); auto *s = host->surface(id);
    if (!s || s->detaching) return GHOST_OHOS_UNAVAILABLE;
    *out = s->info; out->native_window = nullptr; /* Non-leased snapshot cannot expose a live pointer. */
    return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_get_paths(GHOST_OHOSHost *host, GHOST_OHOSPaths *out) noexcept {
  if (!host || !out) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t { std::lock_guard<std::mutex> lock(host->mutex); *out = host->paths; return GHOST_OHOS_OK; });
}
extern "C" int32_t ghost_ohos_host_get_stats(GHOST_OHOSHost *host, GHOST_OHOSStats *out) noexcept {
  if (!host || !out) return GHOST_OHOS_INVALID;
  return guard([&]() -> int32_t {
    std::lock_guard<std::mutex> lock(host->mutex); *out = host->stats; out->pending = host->count; return GHOST_OHOS_OK;
  });
}
extern "C" int32_t ghost_ohos_host_command(GHOST_OHOSHost *host, const GHOST_OHOSCommand *cmd, uint64_t *id) noexcept {
  if (!host || !cmd || cmd->struct_size != sizeof(*cmd)) return GHOST_OHOS_INVALID;
  if (ghost_ohos_host_is_engine(host) != GHOST_OHOS_OK) return GHOST_OHOS_WRONG_THREAD;
  if (!host->callbacks.command) return GHOST_OHOS_UNAVAILABLE;
  return guard([&]() -> int32_t { return host->callbacks.command(host->callbacks.userdata, cmd, id); });
}
extern "C" int32_t ghost_ohos_host_install(GHOST_OHOSHost *host) noexcept {
  if (host && ghost_ohos_host_is_engine(host) != GHOST_OHOS_OK) return GHOST_OHOS_WRONG_THREAD;
  GHOST_OHOSHost *old = installed.load();
  if (old && ghost_ohos_host_is_engine(old) != GHOST_OHOS_OK) return GHOST_OHOS_WRONG_THREAD;
  if (old && host && old != host) return GHOST_OHOS_BUSY;
  return installed.compare_exchange_strong(old, host) ? GHOST_OHOS_OK : GHOST_OHOS_BUSY;
}
extern "C" GHOST_OHOSHost *ghost_ohos_host_installed(void) noexcept { return installed.load(); }
