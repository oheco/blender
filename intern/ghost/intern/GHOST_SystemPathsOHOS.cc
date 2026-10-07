/* SPDX-FileCopyrightText: 2026 Blender OHOS port contributors
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "GHOST_SystemPathsOHOS.hh"
#include <cerrno>
#include <cstring>
#include <sys/stat.h>
#include <utility>
GHOST_SystemPathsOHOS::GHOST_SystemPathsOHOS()
{
  valid_ = ghost_ohos_host_get_paths(ghost_ohos_host_installed(), &paths_) == GHOST_OHOS_OK;
}
const char *GHOST_SystemPathsOHOS::versioned(const char *root, const char *versionstr,
                                           std::map<std::string, std::string> &dirs,
                                           bool create) const
{
  if (!valid_ || !versionstr || !versionstr[0] || std::strlen(versionstr) > 32) return nullptr;
  for (const char *p = versionstr; *p; ++p) if ((*p < '0' || *p > '9') && *p != '.') return nullptr;
  if (versionstr[0] == '.' || std::strstr(versionstr, "..")) return nullptr;
  std::lock_guard<std::mutex> lock(mutex_);
  std::string path = root;
  if (path.back() != '/') path += '/';
  path += versionstr;
  /* Host provisions the private base directories before starting the engine.
   * Do not mkdir/chmod HOME, probe external executables, or fall back to ~/.config. */
  if (create && ::mkdir(path.c_str(), 0700) != 0) {
    struct stat st{};
    if (errno != EEXIST || ::stat(path.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) return nullptr;
  }
  const auto [it, inserted] = dirs.emplace(versionstr, std::move(path));
  (void)inserted;
  return it->second.c_str(); /* std::map insertions preserve existing value addresses. */
}
const char *GHOST_SystemPathsOHOS::getSystemDir(int, const char *versionstr) const
{
  return versioned(paths_.runtime, versionstr, system_dirs_, false);
}
const char *GHOST_SystemPathsOHOS::getUserDir(int, const char *versionstr) const
{
  return versioned(paths_.config, versionstr, user_dirs_, true);
}
std::optional<std::string> GHOST_SystemPathsOHOS::getUserSpecialDir(GHOST_TUserSpecialDirTypes) const
{
  return std::nullopt; /* Public Documents/downloads need host document-picker authorization. */
}
const char *GHOST_SystemPathsOHOS::getBinaryDir() const { return valid_ ? paths_.runtime : nullptr; }
void GHOST_SystemPathsOHOS::addToSystemRecentFiles(const char *) const {}
const char *GHOST_SystemPathsOHOS::getCacheDir() const { return valid_ ? paths_.cache : nullptr; }
const char *GHOST_SystemPathsOHOS::getTempDir() const { return valid_ ? paths_.temp : nullptr; }
