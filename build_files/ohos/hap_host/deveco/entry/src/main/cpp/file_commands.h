/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "creator_ohos_files_path.hh"
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <cstdio>
class FileCommands {
 public:
  struct Ticket {
    BlenderOHOSFileRequest request{};
    BlenderOHOSFileResult result{};
    std::mutex mutex;
    std::condition_variable ready;
    bool done = false;
    /* These two are protected by the broker mutex. */
    bool armed = false, started = false;
    void finish(const BlenderOHOSFileResult &reply) {
      { std::lock_guard<std::mutex> lock(mutex); if (done) { return; } result = reply; done = true; }
      ready.notify_all();
    }
    BlenderOHOSFileResult wait() {
      std::unique_lock<std::mutex> lock(mutex); ready.wait(lock, [&] { return done; }); return result;
    }
  };
  static constexpr size_t capacity = 32;
  void configure(const std::string &config, const std::string &temp) {
    std::lock_guard<std::mutex> lock(mutex_); config_ = config; temp_ = temp;
  }
  int32_t submit(uint32_t operation, const std::string &path, std::shared_ptr<Ticket> &out) {
    auto ticket = std::make_shared<Ticket>();
    std::lock_guard<std::mutex> lock(mutex_);
    if (closed_) { return GHOST_OHOS_CLOSED; }
    if (items_.size() == capacity) { return GHOST_OHOS_FULL; }
    const std::string &root = blender_ohos_files::is_output(operation) ? temp_ : config_;
    if (!blender_ohos_files::marshal(root, operation, path, ticket->request)) { return GHOST_OHOS_INVALID; }
    ticket->request.request_id = ++next_id_; items_.push_back(ticket); out = ticket; return GHOST_OHOS_OK;
  }
  void arm(const std::shared_ptr<Ticket> &ticket) {
    std::lock_guard<std::mutex> lock(mutex_); ticket->armed = true;
  }
  std::shared_ptr<Ticket> take() {
    std::lock_guard<std::mutex> lock(mutex_);
    /* Dispatch commits at started=true under the SAME mutex as close. An
     * earlier host readiness precheck cannot authorize work after that point. */
    if (closed_ || items_.empty() || !items_.front()->armed) { return {}; }
    auto ticket = items_.front(); items_.pop_front(); ticket->started = true; return ticket;
  }
  void cancel(const std::shared_ptr<Ticket> &ticket) {
    std::lock_guard<std::mutex> lock(mutex_);
    for (auto it = items_.begin(); it != items_.end(); ++it) {
      if (*it == ticket) { items_.erase(it); cancelled(ticket); return; }
    }
    /* A running Blender operator finishes with its actual result; no pretend cancellation. */
  }
  template<class PublishAdmission> void close_with_admission(PublishAdmission &&publish) {
    std::lock_guard<std::mutex> lock(mutex_);
    closed_ = true; /* Close linearization point; take() cannot commit after this. */
    /* Publish closing flags/UI state before unlock, so observing admission=false
     * cannot be followed by a new started=true. Production publisher is short. */
    publish();
    while (!items_.empty()) { auto ticket = items_.front(); items_.pop_front(); cancelled(ticket); }
  }
  void close() { close_with_admission([] {}); }
 private:
  static void cancelled(const std::shared_ptr<Ticket> &ticket) {
    BlenderOHOSFileResult result{}; result.struct_size = sizeof(result); result.request_id = ticket->request.request_id;
    result.status = 1; std::snprintf(result.message, sizeof(result.message), "已取消文件操作"); ticket->finish(result);
  }
  std::mutex mutex_;
  std::deque<std::shared_ptr<Ticket>> items_;
  std::string config_, temp_;
  uint64_t next_id_ = 0;
  bool closed_ = false;
};
