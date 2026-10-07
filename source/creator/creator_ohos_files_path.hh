/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "creator_ohos_files.h"
#include <cstring>
#include <string_view>
namespace blender_ohos_files {
inline const char *filename(uint32_t operation)
{
  switch (operation) {
    case BLENDER_OHOS_OPEN_BLEND: return "source.blend";
    case BLENDER_OHOS_SAVE_BLEND: return "asset.blend";
    case BLENDER_OHOS_IMPORT_OBJ: return "source.obj";
    case BLENDER_OHOS_EXPORT_OBJ: return "asset.obj";
    case BLENDER_OHOS_IMPORT_GLB: return "source.glb";
    case BLENDER_OHOS_EXPORT_GLB: return "asset.glb";
    default: return nullptr;
  }
}
inline bool is_output(uint32_t operation)
{
  return operation == BLENDER_OHOS_SAVE_BLEND || operation == BLENDER_OHOS_EXPORT_OBJ ||
         operation == BLENDER_OHOS_EXPORT_GLB;
}
inline bool utf8_path(std::string_view text)
{
  if (text.empty() || text.size() >= BLENDER_OHOS_FILE_PATH_MAX || text.front() != '/') { return false; }
  for (size_t i = 0; i < text.size();) {
    const unsigned char lead = static_cast<unsigned char>(text[i++]);
    if (lead < 32 || lead == 127) { return false; }
    if (lead < 0x80) { continue; }
    uint32_t scalar = 0, bytes = 0, minimum = 0;
    if (lead >= 0xc2 && lead <= 0xdf) { scalar = lead & 31; bytes = 1; minimum = 0x80; }
    else if (lead >= 0xe0 && lead <= 0xef) { scalar = lead & 15; bytes = 2; minimum = 0x800; }
    else if (lead >= 0xf0 && lead <= 0xf4) { scalar = lead & 7; bytes = 3; minimum = 0x10000; }
    else { return false; }
    if (i + bytes > text.size()) { return false; }
    while (bytes--) {
      const unsigned char next = static_cast<unsigned char>(text[i++]);
      if ((next & 0xc0) != 0x80) { return false; }
      scalar = (scalar << 6) | (next & 63);
    }
    if (scalar < minimum || scalar > 0x10ffff || (scalar >= 0xd800 && scalar <= 0xdfff)) { return false; }
  }
  size_t begin = 1;
  while (begin < text.size()) {
    size_t end = text.find('/', begin);
    if (end == std::string_view::npos) { end = text.size(); }
    const auto piece = text.substr(begin, end - begin);
    if (piece.empty() || piece == "." || piece == "..") { return false; }
    begin = end + 1;
  }
  return true;
}
inline bool uuid(std::string_view text)
{
  if (text.size() != 36) { return false; }
  for (size_t i = 0; i < text.size(); ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) { if (text[i] != '-') { return false; } }
    else if (!((text[i] >= '0' && text[i] <= '9') || (text[i] >= 'a' && text[i] <= 'f') ||
               (text[i] >= 'A' && text[i] <= 'F'))) { return false; }
  }
  return true;
}
inline bool validate(std::string_view config, uint32_t operation, std::string_view path)
{
  const char *leaf = filename(operation);
  if (!leaf || !utf8_path(config) || !utf8_path(path)) { return false; }
  while (config.size() > 1 && config.back() == '/') { config.remove_suffix(1); }
  if (config.size() <= 1 || path.size() <= config.size() || path.substr(0, config.size()) != config) { return false; }
  path.remove_prefix(config.size());
  constexpr std::string_view prefix = "/exchange/";
  if (path.substr(0, prefix.size()) != prefix) { return false; }
  path.remove_prefix(prefix.size());
  return path.size() > 37 && uuid(path.substr(0, 36)) && path[36] == '/' && path.substr(37) == leaf;
}
inline bool marshal(std::string_view config, uint32_t operation, std::string_view path,
                    BlenderOHOSFileRequest &request)
{
  request = {};
  if (!validate(config, operation, path)) { return false; }
  request.struct_size = sizeof(request); request.abi_version = BLENDER_OHOS_FILE_ABI;
  request.operation = operation;
  std::memcpy(request.filepath, path.data(), path.size());
  request.filepath[path.size()] = '\0';
  return true;
}
}
