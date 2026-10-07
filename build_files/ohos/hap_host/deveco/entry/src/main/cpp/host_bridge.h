/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "creator_ohos.h"
#include "GHOST_OHOSEngine.h"
static_assert(BLENDER_OHOS_CREATOR_ABI_VERSION == 2u, "Core and all lifecycle callers must use unpublished creator ABI rev2");
#include "reliable_input.h"
#include "file_commands.h"
#include <atomic>
#include <memory>
#include <mutex>
#include <pthread.h>
#include <string>
#include <unordered_set>
class BlenderHostBridge {
 public:
  int32_t configure(const std::string &runtime, const std::string &config,
                    const std::string &cache, const std::string &temp, float scale, uint32_t dpi);
  int32_t attach(void *window, uint32_t width, uint32_t height);
  int32_t resize(void *window, uint32_t width, uint32_t height);
  int32_t input(GHOST_OHOSEvent event);
  int32_t key_pair(int32_t code, uint64_t generation, int32_t count = 1);
  int32_t text(const std::string &text, uint64_t generation = 0);
  int32_t focus(bool focused);
  void foreground(bool active);
  int32_t submit_file(uint32_t operation, const std::string &path, std::shared_ptr<FileCommands::Ticket> &ticket);
  void arm_file(const std::shared_ptr<FileCommands::Ticket> &ticket) { files_.arm(ticket); }
  void cancel_file(const std::shared_ptr<FileCommands::Ticket> &ticket) { files_.cancel(ticket); }
  void close_files() { files_.close(); } /* short UI-safe queued cancellation; running op finishes */
  void begin_shutdown(); /* UI closes file/input admission; engine performs core stop/drain. */
  bool accepting() const { return accepting_.load(); }
  int32_t detach(); /* coordinator or destruction fence, not engine thread */
  int32_t shutdown(); /* async-work coordinator, never UI join */
  void fail(int32_t code, const char *message) noexcept;
  int state() const { return state_.load(); }
  int result() const { return result_.load(); }
  bool surface(uint64_t &generation, void *window = nullptr);
  static constexpr uint64_t window_id = 1;
 private:
  static int32_t run(GHOST_OHOSHost *, GHOST_OHOSEngine *, void *) noexcept;
  static void *retry_entry(void *) noexcept;
  GHOST_OHOSHost *host_ = nullptr;
  GHOST_OHOSEngine *engine_ = nullptr;
  std::unique_ptr<ReliableInput> retry_;
  FileCommands files_;
  std::mutex lifetime_, surface_mutex_, shutdown_coordinator_, core_done_mutex_;
  // Serializes surface replacement; text/focus/config callbacks never take this gate.
  std::mutex surface_lifecycle_;
  std::condition_variable core_done_ready_;
  void *window_ = nullptr;
  uint64_t generation_ = 0;
  bool detaching_ = false;
  float scale_ = 1;
  uint32_t dpi_ = 160;
  pthread_t retry_thread_{};
  bool retry_started_ = false;
  std::atomic<bool> retry_stop_{false}, foreground_{true}, finalize_{false};
  std::atomic<bool> accepting_{true}, stop_requested_{false}, core_done_{false};
  std::atomic<int> state_{0}, result_{0};
};
BlenderHostBridge &blender_host_bridge();
