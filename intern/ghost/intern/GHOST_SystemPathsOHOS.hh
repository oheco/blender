/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "GHOST_SystemPaths.hh"
#include "GHOST_OHOSHost.h"
#include <map>
#include <mutex>

class GHOST_SystemPathsOHOS : public GHOST_SystemPaths {
 public:
  GHOST_SystemPathsOHOS();
  ~GHOST_SystemPathsOHOS() override = default;
  const char *getSystemDir(int version, const char *versionstr) const override;
  const char *getUserDir(int version, const char *versionstr) const override;
  std::optional<std::string> getUserSpecialDir(GHOST_TUserSpecialDirTypes type) const override;
  const char *getBinaryDir() const override;
  void addToSystemRecentFiles(const char *filepath) const override;
  const char *getCacheDir() const;
  const char *getTempDir() const;
 private:
  GHOST_OHOSPaths paths_{};
  bool valid_ = false;
  mutable std::mutex mutex_;
  mutable std::map<std::string, std::string> system_dirs_, user_dirs_;
  const char *versioned(const char *root, const char *versionstr,
                       std::map<std::string, std::string> &dirs, bool create) const;
};
