/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_OHOSEngine.h"
#include <atomic>
#include <new>
#include <pthread.h>
struct GHOST_OHOSEngine {
  GHOST_OHOSHost *host;
  GHOST_OHOSEngineRun run;
  void *userdata;
  pthread_t thread{};
  std::atomic<bool> stop{false};
  int32_t result = GHOST_OHOS_UNAVAILABLE;
};
namespace {
void *worker(void *opaque) noexcept {
  auto *engine = static_cast<GHOST_OHOSEngine *>(opaque);
  engine->result = ghost_ohos_host_bind_engine(engine->host);
  if (engine->result != GHOST_OHOS_OK) return nullptr;
  engine->result = ghost_ohos_host_install(engine->host);
  if (engine->result != GHOST_OHOS_OK) return nullptr;
  try { engine->result = engine->run(engine->host, engine, engine->userdata); }
  catch (...) { engine->result = GHOST_OHOS_CALLBACK_FAILED; }
  /* Callback owns teardown; any leaked window lease makes destroy return BUSY. */
  ghost_ohos_host_install(nullptr);
  return nullptr;
}
}
extern "C" int32_t ghost_ohos_engine_start(GHOST_OHOSHost *host, GHOST_OHOSEngineRun run,
    void *userdata, size_t stack_bytes, GHOST_OHOSEngine **out) noexcept {
  if (!out) return GHOST_OHOS_INVALID;
  *out = nullptr;
  if (!host || !run) return GHOST_OHOS_INVALID;
  if (!stack_bytes) stack_bytes = 32 * 1024 * 1024;
  if (stack_bytes < 8 * 1024 * 1024 || stack_bytes > 256 * 1024 * 1024) return GHOST_OHOS_INVALID;
  auto *engine = new (std::nothrow) GHOST_OHOSEngine;
  if (!engine) return GHOST_OHOS_NO_MEMORY;
  engine->host = host; engine->run = run; engine->userdata = userdata;
  int32_t result = ghost_ohos_host_begin_run(host);
  if (result != GHOST_OHOS_OK) { delete engine; return result; }
  pthread_attr_t attr;
  const int attr_result = pthread_attr_init(&attr);
  if (attr_result == 0) {
    const int stack_result = pthread_attr_setstacksize(&attr, stack_bytes);
    pthread_t created_thread{};
    const int thread_result = stack_result == 0 ? pthread_create(&created_thread, &attr, worker, engine) : stack_result;
    pthread_attr_destroy(&attr);
    if (thread_result == 0) {
      /* The worker may already be inside run(). Only the creator publishes the
       * join handle, before exposing the engine to its lifecycle coordinator. */
      engine->thread = created_thread;
      *out = engine; return GHOST_OHOS_OK;
    }
  }
  ghost_ohos_host_end_run(host); delete engine; return GHOST_OHOS_UNAVAILABLE;
}
extern "C" int32_t ghost_ohos_engine_request_stop(GHOST_OHOSEngine *engine) noexcept {
  if (!engine) return GHOST_OHOS_INVALID;
  engine->stop.store(true); return ghost_ohos_host_wake(engine->host);
}
extern "C" int32_t ghost_ohos_engine_stop_requested(const GHOST_OHOSEngine *engine) noexcept {
  return engine && engine->stop.load() ? 1 : 0;
}
extern "C" int32_t ghost_ohos_engine_join(GHOST_OHOSEngine *engine, int32_t *out) noexcept {
  if (!engine || !out) return GHOST_OHOS_INVALID;
  /* run() can begin before the creator publishes pthread_t. The bound host
   * identity is already valid on the worker; reject self-join without reading
   * that not-yet-published handle (and without a pthread_create output race). */
  if (ghost_ohos_host_is_engine(engine->host) == GHOST_OHOS_OK) return GHOST_OHOS_WRONG_THREAD;
  if (pthread_equal(engine->thread, pthread_self())) return GHOST_OHOS_WRONG_THREAD;
  if (pthread_join(engine->thread, nullptr) != 0) return GHOST_OHOS_UNAVAILABLE;
  *out = engine->result;
  ghost_ohos_host_end_run(engine->host); delete engine; return GHOST_OHOS_OK;
}
