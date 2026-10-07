/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "host_bridge.h"
#include "GHOST_OHOSNative.h"
#include "xcomponent_input.h"
#include "ime_input.h"
#include <cstdio>
#include <hilog/log.h>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <thread>
#include <sys/stat.h>
namespace {
bool directory(const std::string &path)
{
  struct stat st{};
  return !path.empty() && path[0] == '/' && path.size() < GHOST_OHOS_PATH_MAX &&
      stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}
int32_t command(void *, const GHOST_OHOSCommand *cmd, uint64_t *) noexcept
{
  try {
    if (cmd->window_id != BlenderHostBridge::window_id) return GHOST_OHOS_UNAVAILABLE;
    if (cmd->type == GHOST_OHOS_COMMAND_IME_END) return blender_ime_end();
    if (cmd->type == GHOST_OHOS_COMMAND_IME_BEGIN && cmd->text && cmd->width && cmd->height) {
      int32_t x = 0, y = 0; char extra;
      if (std::sscanf(cmd->text, "%d,%d%c", &x, &y, &extra) != 2) return GHOST_OHOS_INVALID;
      return blender_ime_begin(x, y, cmd->width, cmd->height);
    }
    /* Secondary native windows still require a separate asynchronous coordinator.
     * Blender temporary editors use the upstream same-window display policy. */
    return GHOST_OHOS_UNAVAILABLE;
  } catch (...) { return GHOST_OHOS_CALLBACK_FAILED; }
}
}
BlenderHostBridge &blender_host_bridge()
{
  /* Process lifetime singleton; explicit shutdown joins all threads before HAP removal. */
  static BlenderHostBridge bridge;
  return bridge;
}
void BlenderHostBridge::fail(int32_t code, const char *message) noexcept
{
  result_ = code != 0 ? code : GHOST_OHOS_CALLBACK_FAILED;
  state_ = -1;
  OH_LOG_Print(LOG_APP, LOG_ERROR, 0xB10, "BlenderHost", "%{public}s (%{public}d)", message, code);
}
void BlenderHostBridge::begin_shutdown()
{
  files_.close_with_admission([&] {
    /* Broker close and dispatch commit serialize before any closing flag/UI
     * publication. An already-started ticket retains its actual operator result. */
    accepting_ = false; stop_requested_ = true;
    int current = state_.load();
    while ((current == 1 || current == 2) && !state_.compare_exchange_weak(current, 5)) {}
  });
  blender_ime_end();
}
int32_t BlenderHostBridge::configure(const std::string &runtime, const std::string &config,
    const std::string &cache, const std::string &temp, float scale, uint32_t dpi)
{
  std::lock_guard<std::mutex> lock(lifetime_);
  if (!accepting_) { return GHOST_OHOS_CLOSED; }
  if (state_ != 0) { return GHOST_OHOS_BUSY; }
  if (!directory(runtime) || !directory(config) || !directory(cache) || !directory(temp) ||
      !std::isfinite(scale) || scale <= 0 || dpi == 0 || dpi > UINT16_MAX) { return GHOST_OHOS_INVALID; }
  GHOST_OHOSConfig cfg{};
  cfg.struct_size = sizeof(cfg); cfg.abi_version = GHOST_OHOS_ABI_VERSION; cfg.queue_capacity = 4096;
  cfg.runtime_path = runtime.c_str(); cfg.config_path = config.c_str();
  cfg.cache_path = cache.c_str(); cfg.temp_path = temp.c_str();
  cfg.callbacks.struct_size = sizeof(cfg.callbacks); cfg.callbacks.native_retain = ghost_ohos_native_retain;
  cfg.callbacks.native_release = ghost_ohos_native_release; cfg.callbacks.command = command;
  int32_t result = ghost_ohos_host_create(&cfg, &host_);
  if (result != GHOST_OHOS_OK) { return result; }
  retry_ = std::make_unique<ReliableInput>(host_);
  files_.configure(config, temp);
  scale_ = scale; dpi_ = dpi;
  pthread_attr_t attributes;
  if (pthread_attr_init(&attributes) != 0) {
    ghost_ohos_host_destroy(host_); host_ = nullptr; retry_.reset(); return GHOST_OHOS_UNAVAILABLE;
  }
  state_ = 1; /* Publish before retry thread can report a failure. */
  const int stack = pthread_attr_setstacksize(&attributes, 1024 * 1024);
  const int created = stack == 0 ? pthread_create(&retry_thread_, &attributes, retry_entry, this) : stack;
  pthread_attr_destroy(&attributes);
  if (created != 0) { ghost_ohos_host_destroy(host_); host_ = nullptr; retry_.reset(); return GHOST_OHOS_UNAVAILABLE; }
  retry_started_ = true;
  return GHOST_OHOS_OK;
}
void *BlenderHostBridge::retry_entry(void *opaque) noexcept
{
  auto *self = static_cast<BlenderHostBridge *>(opaque);
  try {
    while (!self->retry_stop_) {
      const int32_t result = self->retry_->flush();
      if (result != GHOST_OHOS_OK && result != GHOST_OHOS_FULL) {
        self->fail(result, "Reliable input retry failed"); break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
  }
  catch (...) { self->fail(GHOST_OHOS_CALLBACK_FAILED, "Retry coordinator exception"); }
  return nullptr;
}
bool BlenderHostBridge::surface(uint64_t &generation, void *window)
{
  std::lock_guard<std::mutex> lock(surface_mutex_);
  if (!window_ || detaching_ || (window && window != window_)) { return false; }
  generation = generation_; return true;
}
int32_t BlenderHostBridge::attach(void *window, uint32_t width, uint32_t height)
{
  if (!accepting_) { return GHOST_OHOS_CLOSED; }
  std::lock_guard<std::mutex> replacement(surface_lifecycle_);
  std::lock_guard<std::mutex> lifecycle(lifetime_);
  std::lock_guard<std::mutex> surface(surface_mutex_);
  if (!host_ || !window || window_ || state_ < 0 || state_ >= 3) { return GHOST_OHOS_BUSY; }
  GHOST_OHOSWindowInfo info{};
  info.window_id = window_id; info.native_window = window; info.width = width; info.height = height;
  info.dpi = dpi_; info.scale = scale_;
  int32_t result = ghost_ohos_host_attach(host_, &info, &generation_);
  if (result != GHOST_OHOS_OK) { fail(result, "Surface attach failed"); return result; }
  window_ = window; detaching_ = false;
  ghost_ohos_host_set_main_window(host_, window_id);
  if (!engine_) {
    result = ghost_ohos_engine_start(host_, run, this, 32 * 1024 * 1024, &engine_);
    if (result != GHOST_OHOS_OK) { fail(result, "Engine pthread creation failed"); }
  }
  return result;
}
int32_t BlenderHostBridge::resize(void *window, uint32_t width, uint32_t height)
{
  uint64_t generation;
  if (!surface(generation, window)) { return GHOST_OHOS_UNAVAILABLE; }
  GHOST_OHOSEvent event{}; event.type = GHOST_OHOS_EVENT_RESIZE; event.generation = generation;
  event.width = width; event.height = height; event.scale = scale_; event.dpi = dpi_;
  return input(event);
}
int32_t BlenderHostBridge::input(GHOST_OHOSEvent event)
{
  if (event.type == GHOST_OHOS_EVENT_FOCUS) {
    const int32_t rc = blender_ime_focus(event.pressed != 0, event.generation);
    if (rc != GHOST_OHOS_OK) return rc;
  }
  std::lock_guard<std::mutex> lock(surface_mutex_);
  const bool lifecycle = event.type == GHOST_OHOS_EVENT_RESIZE || event.type == GHOST_OHOS_EVENT_FOCUS;
  if (!accepting_ && !lifecycle) { return GHOST_OHOS_CLOSED; }
  if (!window_ || detaching_ || core_done_) { return GHOST_OHOS_UNAVAILABLE; }
  /* Preserve generation supplied by native callbacks; reject stale events after reconstruction. */
  if (event.generation && event.generation != generation_) { return GHOST_OHOS_UNAVAILABLE; }
  event.struct_size = sizeof(event); event.window_id = window_id; event.generation = generation_;
  const int32_t result = retry_->submit(&event, 1);
  if (result != GHOST_OHOS_OK) { fail(result, "Reliable input storage exhausted or invalid"); }
  return result;
}
int32_t BlenderHostBridge::key_pair(int32_t code, uint64_t generation, int32_t count)
{
  std::lock_guard<std::mutex> lock(surface_mutex_);
  if (!accepting_) return GHOST_OHOS_CLOSED;
  if (!window_ || detaching_ || core_done_ || generation != generation_) return GHOST_OHOS_UNAVAILABLE;
  if (count < 0 || size_t(count) > ReliableInput::capacity / 2) {
    fail(GHOST_OHOS_INVALID, "IME key batch exceeds reliable storage capacity");
    return GHOST_OHOS_INVALID;
  }
  if (!count) return GHOST_OHOS_OK;
  try {
    std::vector<GHOST_OHOSEvent> events(size_t(count) * 2);
    for (size_t i = 0; i < events.size(); i += 2) {
      auto &down = events[i]; down.struct_size = sizeof(down); down.window_id = window_id;
      down.generation = generation; down.type = GHOST_OHOS_EVENT_KEY;
      down.value = code; down.pressed = 1;
      events[i + 1] = down; events[i + 1].pressed = 0;
    }
    const int32_t result = retry_->submit(events.data(), events.size());
    if (result != GHOST_OHOS_OK) fail(result, "IME key batch rejected by reliable storage");
    return result;
  } catch (...) {
    fail(GHOST_OHOS_NO_MEMORY, "IME key batch allocation failed");
    return GHOST_OHOS_NO_MEMORY;
  }
}
int32_t BlenderHostBridge::text(const std::string &text, uint64_t generation)
{
  if (!accepting_) { return GHOST_OHOS_CLOSED; }
  std::lock_guard<std::mutex> lock(surface_mutex_);
  if (!window_ || detaching_ || state_ < 0 || state_ >= 3) { return GHOST_OHOS_UNAVAILABLE; }
  if (generation && generation != generation_) return GHOST_OHOS_UNAVAILABLE;
  std::vector<GHOST_OHOSEvent> events;
  int32_t result = blender_utf8_commit(text, window_id, generation_, events);
  if (result == GHOST_OHOS_OK) { result = retry_->submit(events.data(), events.size()); }
  if (result != GHOST_OHOS_OK) { fail(result, "Text commit rejected or reliable storage exhausted"); }
  return result;
}
int32_t BlenderHostBridge::focus(bool focused)
{
  GHOST_OHOSEvent event{}; event.type = GHOST_OHOS_EVENT_FOCUS; event.pressed = focused;
  return input(event);
}
void BlenderHostBridge::foreground(bool active)
{
  foreground_ = active;
  focus(active);
}
int32_t BlenderHostBridge::detach()
{
  std::lock_guard<std::mutex> replacement(surface_lifecycle_);
  uint64_t retiring_generation;
  const int32_t ime_result = surface(retiring_generation) ? blender_ime_retire(retiring_generation) : blender_ime_end();
  if (ime_result != GHOST_OHOS_OK) return ime_result;
  std::lock_guard<std::mutex> lifecycle(lifetime_);
  uint64_t generation;
  {
    std::lock_guard<std::mutex> surface(surface_mutex_);
    if (!window_) { return GHOST_OHOS_OK; }
    generation = generation_;
    if (!engine_) {
      /* Startup failure has no GPU lease. Destroy serially while UI still owns the window. */
      retry_stop_ = true;
      if (retry_started_) { pthread_join(retry_thread_, nullptr); retry_started_ = false; }
      int32_t result = ghost_ohos_host_destroy(host_);
      if (result != GHOST_OHOS_OK) { return result; }
      host_ = nullptr; window_ = nullptr; return GHOST_OHOS_OK;
    }
    if (!detaching_) {
      int32_t result = retry_->request_detach(window_id, generation);
      if (result != GHOST_OHOS_OK) { fail(result, "Detach queue exhausted"); return result; }
      detaching_ = true; /* Stop accepting new input before the detach transition. */
    }
  }
  const int32_t result = ghost_ohos_host_wait_detached(host_, window_id, generation, 5000);
  if (result != GHOST_OHOS_OK) { fail(result, "Surface detach ACK timed out; keep XComponent mounted"); return result; }
  std::lock_guard<std::mutex> surface(surface_mutex_);
  window_ = nullptr; detaching_ = false;
  return GHOST_OHOS_OK;
}
int32_t BlenderHostBridge::submit_file(uint32_t operation, const std::string &path,
    std::shared_ptr<FileCommands::Ticket> &ticket)
{
  if (!accepting_ || state_ != 2) { return GHOST_OHOS_CLOSED; }
  return files_.submit(operation, path, ticket);
}
int32_t BlenderHostBridge::shutdown()
{
  begin_shutdown();
  std::lock_guard<std::mutex> coordinator(shutdown_coordinator_);
  int32_t result = detach();
  if (result != GHOST_OHOS_OK) { return result; } /* Never join/remove before the ACK. */
  GHOST_OHOSEngine *owned_engine = nullptr;
  { std::lock_guard<std::mutex> lifecycle(lifetime_);
    if (state_ == 4) { return GHOST_OHOS_OK; }
    owned_engine = engine_;
  }
  if (owned_engine) {
    ghost_ohos_engine_request_stop(owned_engine);
    /* Worker-only wait. Keep lifetime_ free for native-window callbacks and do
     * not grant final engine exit until physical core teardown consumed session. */
    { std::unique_lock<std::mutex> lock(core_done_mutex_);
      core_done_ready_.wait(lock, [&] { return core_done_.load(); }); }
    finalize_ = true;
    int32_t run_result = 0;
    result = ghost_ohos_engine_join(owned_engine, &run_result);
    if (result != GHOST_OHOS_OK) { return result; }
    { std::lock_guard<std::mutex> lifecycle(lifetime_); engine_ = nullptr; }
    if (run_result != 0) { result_ = run_result; }
  }
  std::lock_guard<std::mutex> lifecycle(lifetime_);
  retry_stop_ = true;
  if (retry_started_) { pthread_join(retry_thread_, nullptr); retry_started_ = false; }
  if (host_) {
    result = ghost_ohos_host_destroy(host_);
    if (result != GHOST_OHOS_OK) { fail(result, "Host destroy found retained leases/references"); return result; }
    host_ = nullptr;
  }
  retry_.reset(); state_ = 4;
  return GHOST_OHOS_OK; /* Physical shutdown status; actual Blender exit/error is result(). */
}
int32_t BlenderHostBridge::run(GHOST_OHOSHost *host, GHOST_OHOSEngine *engine, void *opaque) noexcept
{
  auto *self = static_cast<BlenderHostBridge *>(opaque);
  BlenderOHOSSession *session = nullptr;
  int32_t result = GHOST_OHOS_OK, last_reported = GHOST_OHOS_OK;
  bool stop_sent = false;
  const auto error = [&](int32_t code, const char *message) {
    if (result == GHOST_OHOS_OK) { result = code; }
    if (last_reported != code) { self->fail(code, message); last_reported = code; }
    self->begin_shutdown();
  };
  try {
    bool startup_ready = true;
#ifdef BLENDER_OHOS_MALEOON_TILE_EXPERIMENT
    /* Request only; the core still verifies the actual device and enabled features.
     * Preserve every explicit preset, including "0" and empty/other values. */
    const bool gate_preset = std::getenv("BLENDER_VK_TILE_BLEND_EXPERIMENT") != nullptr;
    if (!gate_preset && ::setenv("BLENDER_VK_TILE_BLEND_EXPERIMENT", "1", 0) != 0) {
      startup_ready = false;
      error(GHOST_OHOS_CALLBACK_FAILED, "Tile trial request failed; core not initialized");
    }
    else {
      OH_LOG_Print(LOG_APP, LOG_WARN, 0xB10, "BlenderHost",
                   "LIMITED tile trial request; preset=%{public}d; HAP/WSI unverified",
                   gate_preset ? 1 : 0);
    }
#endif
    if (startup_ready) {
      const char *argv[] = {"blender-embedded", "--disable-crash-handler", "--disable-abort-handler", "--gpu-backend", "vulkan"};
      const int32_t initialized = blender_ohos_initialize(host, 5, argv, &session);
      if (initialized != GHOST_OHOS_OK) { error(initialized, "Blender initialize failed; retaining session for drain"); }
      else { int expected = 1; self->state_.compare_exchange_strong(expected, 2); }
    }
  }
  catch (...) { error(GHOST_OHOS_CALLBACK_FAILED, "Initialize exception; retaining returned session"); }
  /* C ABI rev2: an error/stop request is never permission to consume a session.
   * DNS/owned callbacks may keep this bounded engine pump alive indefinitely. */
  while (session) {
    try {
      if (self->stop_requested_ || self->state_ < 0 || ghost_ohos_engine_stop_requested(engine)) {
        self->begin_shutdown();
        if (!stop_sent) {
          const int32_t stopping = blender_ohos_stop(session);
          if (stopping == GHOST_OHOS_OK) { stop_sent = true; }
          else { error(stopping, "Core stop/begin failed; session remains owned"); }
        }
      }
      const int32_t tick = blender_ohos_pump(session, self->foreground_.load() && self->accepting_.load());
      if (tick == GHOST_OHOS_EMPTY) {
        self->begin_shutdown();
        int32_t exit_code = 0;
        const int32_t teardown = blender_ohos_teardown(&session, &exit_code);
        if (teardown < 0) { error(teardown, "Core teardown deferred by lifecycle error"); }
        else if (teardown == BLENDER_OHOS_DRAIN_ERROR) { error(GHOST_OHOS_CALLBACK_FAILED, "Core teardown drain error; retain ownership"); }
        else if (teardown != GHOST_OHOS_OK && teardown != BLENDER_OHOS_DRAINING) {
          error(GHOST_OHOS_CALLBACK_FAILED, "Unknown teardown status; preserve any retained session");
        }
        if (!session) {
          if (result == GHOST_OHOS_OK) { result = exit_code; }
        }
        /* Pending/Error leaves session non-null and returns to another pump tick. */
      }
      else if (tick == BLENDER_OHOS_DRAINING || tick == BLENDER_OHOS_DRAIN_ERROR) { /* creator rev2 DRAINING / DRAIN_ERROR */
        self->begin_shutdown();
        if (tick == BLENDER_OHOS_DRAIN_ERROR) { error(GHOST_OHOS_CALLBACK_FAILED, "Extension drain error; preserve Python/WM/session"); }
      }
      else if (tick < 0) { error(tick, "Core pump error; session remains owned for drain"); }
      else if (tick != GHOST_OHOS_OK) { error(GHOST_OHOS_CALLBACK_FAILED, "Unknown creator pump status; preserve session"); }
      else if (self->accepting_ && !stop_sent && self->state_ == 2) {
        /* Only ordinary READY WM ticks dispatch new assets after earlier input. */
        GHOST_OHOSStats stats{};
        if (self->foreground_ && self->retry_->pending() == 0 &&
            ghost_ohos_host_get_stats(host, &stats) == GHOST_OHOS_OK && stats.pending == 0) {
          if (auto ticket = self->files_.take()) {
            BlenderOHOSFileResult reply{};
            blender_ohos_file_command(session, &ticket->request, &reply);
            ticket->finish(reply);
          }
        }
      }
    }
    catch (...) { error(GHOST_OHOS_CALLBACK_FAILED, "Engine tick exception; preserve session and keep draining"); }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  self->files_.close();
  if (result != GHOST_OHOS_OK) { self->fail(result, "Blender exited after physical teardown"); }
  else if (self->state_ >= 0) { self->state_ = 3; }
  { std::lock_guard<std::mutex> lock(self->core_done_mutex_); self->core_done_ = true; }
  self->core_done_ready_.notify_all();
  /* After teardown retain the bound engine just long enough to ACK late detach.
   * UI sees state 3/error and runs async shutdown while XComponent remains mounted. */
  while (!self->finalize_) {
    GHOST_OHOSEvent event{};
    const int32_t popped = ghost_ohos_host_pop(host, &event, 16);
    if (popped == GHOST_OHOS_OK && event.type == GHOST_OHOS_EVENT_DETACHED) {
      int32_t ack = GHOST_OHOS_BUSY;
      while (!self->finalize_ && ack != GHOST_OHOS_OK) {
        ack = ghost_ohos_host_ack_detach(host, event.window_id, event.generation);
        if (ack != GHOST_OHOS_OK) {
          self->fail(ack, "Native release failed after engine teardown");
          std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
      }
    }
  }
  return result;
}
