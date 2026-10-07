/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Included once at the end of creator.cc, after the private session definition. */
#pragma once
#ifdef WITH_GHOST_OHOS_EMBEDDED
#include "creator_ohos_files_path.hh"
#include "BKE_report.hh"
#include "BLI_path_utils.hh"
#include "DNA_windowmanager_types.h"
#include "RNA_access.hh"
#include "BPY_extern_run.hh"
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
namespace blender_ohos_files {
using namespace blender;
static_assert(BLENDER_OHOS_FILE_PATH_MAX == FILE_MAX);
struct Directory {
  int fd = -1;
  Directory(const char *base, std::string_view path) {
    int root = open(base, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (root < 0) { return; }
    int exchange = openat(root, "exchange", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    close(root); if (exchange < 0) { return; }
    std::string_view config(base);
    while (config.size() > 1 && config.back() == '/') { config.remove_suffix(1); }
    char name[37]{}; std::memcpy(name, path.data() + config.size() + 10, 36);
    fd = openat(exchange, name, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW); close(exchange);
  }
  ~Directory() { if (fd >= 0) { close(fd); } }
  bool regular(const char *leaf, bool input) const {
    struct stat st{};
    return fd >= 0 && fstatat(fd, leaf, &st, AT_SYMLINK_NOFOLLOW) == 0 && S_ISREG(st.st_mode) &&
           st.st_size > 0 && (!input || uint64_t(st.st_size) <= 512ULL * 1024 * 1024);
  }
  bool absent(const char *leaf) const {
    struct stat st{};
    return fd >= 0 && fstatat(fd, leaf, &st, AT_SYMLINK_NOFOLLOW) < 0 && errno == ENOENT;
  }
};
struct ContextWindow {
  bContext *C;
  explicit ContextWindow(bContext *context) : C(context) {
    if (!CTX_wm_window(C)) {
      wmWindowManager *wm = CTX_wm_manager(C);
      if (wm && wm->windows.first) { CTX_wm_window_set(C, static_cast<wmWindow *>(wm->windows.first)); }
    }
  }
  /* Open changes Main/WM/window ownership: never restore a saved pre-open pointer. */
  ~ContextWindow() { CTX_wm_window_set(C, nullptr); }
};
struct Properties {
  PointerRNA value;
  explicit Properties(wmOperatorType *ot) : value(WM_operator_properties_create_ptr(ot)) {}
  ~Properties() { WM_operator_properties_free(&value); }
};
struct Reports {
  ReportList *value = MEM_new<ReportList>("OHOS file command reports");
  Reports() { BKE_reports_init(value, RPT_STORE | RPT_OP_HOLD); }
  ~Reports() {
    /* WM takes this heap allocation for registered undo/redo or modal operators.
     * Never pass a stack ReportList: wm_operator_finished sets RPT_FREE. */
    if (!(value->flag & RPT_FREE)) { BKE_reports_free(value); MEM_delete(value); }
  }
};
inline const char *operator_name(uint32_t operation) {
  switch (operation) {
    case BLENDER_OHOS_OPEN_BLEND: return "WM_OT_open_mainfile";
    case BLENDER_OHOS_SAVE_BLEND: return "WM_OT_save_as_mainfile";
    case BLENDER_OHOS_IMPORT_OBJ: return "WM_OT_obj_import";
    case BLENDER_OHOS_EXPORT_OBJ: return "WM_OT_obj_export";
    case BLENDER_OHOS_IMPORT_GLB: return "IMPORT_SCENE_OT_gltf";
    case BLENDER_OHOS_EXPORT_GLB: return "EXPORT_SCENE_OT_gltf";
    default: return nullptr;
  }
}
}
extern "C" int32_t blender_ohos_file_command(BlenderOHOSSession *session,
    const BlenderOHOSFileRequest *request, BlenderOHOSFileResult *out) noexcept
{
  using namespace blender;
  using namespace blender_ohos_files;
  if (!out) { return GHOST_OHOS_INVALID; }
  *out = {}; out->struct_size = sizeof(*out); out->request_id = request ? request->request_id : 0;
  auto finish = [&](int32_t status, const char *message) {
    out->status = status; std::snprintf(out->message, sizeof(out->message), "%s", message); return status;
  };
  if (!session || ghost_ohos_host_is_engine(session->host) != GHOST_OHOS_OK ||
      ghost_ohos_host_installed() != session->host) {
    return finish(GHOST_OHOS_WRONG_THREAD, "文件操作必须由Blender引擎执行");
  }
  if (!session->ready || !session->context || session->stop || session->background) {
    return finish(GHOST_OHOS_CLOSED, "Blender已关闭或尚未准备好");
  }
  if (!request || request->struct_size != sizeof(*request) || request->abi_version != BLENDER_OHOS_FILE_ABI ||
      request->reserved || !std::memchr(request->filepath, 0, sizeof(request->filepath))) {
    return finish(GHOST_OHOS_INVALID, "无效的文件请求");
  }
  try {
    GHOST_OHOSPaths paths{};
    const bool output = is_output(request->operation);
    const char *root = output ? paths.temp : paths.config;
    if (ghost_ohos_host_get_paths(session->host, &paths) != GHOST_OHOS_OK ||
        !validate(root, request->operation, request->filepath)) {
      return finish(GHOST_OHOS_INVALID, "无效的工作副本");
    }
    const char *leaf = filename(request->operation);
    Directory directory(root, request->filepath);
    if (output ? !directory.absent(leaf) : !directory.regular(leaf, true)) {
      return finish(GHOST_OHOS_INVALID, "无法访问工作副本，或文件超过512MB");
    }
    ContextWindow context(session->context);
    wmWindow *window = CTX_wm_window(session->context);
    if (!window || !window->runtime || window->runtime->modalhandlers.first) {
      return finish(GHOST_OHOS_BUSY, "请先完成Blender当前的工具或对话框操作");
    }
    const char *name = operator_name(request->operation);
    wmOperatorType *ot = WM_operatortype_find(name, false);
    if (!ot && (request->operation == BLENDER_OHOS_IMPORT_GLB || request->operation == BLENDER_OHOS_EXPORT_GLB)) {
      /* Fixed startup expression only. No filenames, URIs or user data become Python source.
       * BPY's real C API bridge establishes context/GIL on this ENGINE thread. */
      const char *imports[] = {"addon_utils", nullptr};
      if (!BPY_run_string_eval(session->context, imports,
          "addon_utils.enable('io_scene_gltf2', default_set=False, persistent=True)")) {
        return finish(GHOST_OHOS_UNAVAILABLE, "无法启用Blender的GLB导入导出组件");
      }
      ot = WM_operatortype_find(name, false); /* enable may return None without raising. */
    }
    if (!ot || !ot->exec) { return finish(GHOST_OHOS_UNAVAILABLE, "此格式的Blender导入导出组件不可用"); }
    if (!WM_operator_poll(session->context, ot)) {
      return finish(GHOST_OHOS_BUSY, "当前Blender状态不能执行此文件操作");
    }
    Properties properties(ot);
    RNA_string_set(&properties.value, "filepath", request->filepath);
    if (request->operation == BLENDER_OHOS_OPEN_BLEND) {
      RNA_boolean_set(&properties.value, "load_ui", false);
      RNA_boolean_set(&properties.value, "use_scripts", false);
    }
    else if (request->operation == BLENDER_OHOS_SAVE_BLEND) {
      RNA_boolean_set(&properties.value, "copy", true);
      RNA_boolean_set(&properties.value, "relative_remap", false);
    }
    else if (request->operation == BLENDER_OHOS_EXPORT_OBJ) {
      RNA_boolean_set(&properties.value, "export_materials", false);
      RNA_boolean_set(&properties.value, "export_animation", false);
    }
    else if (request->operation == BLENDER_OHOS_EXPORT_GLB) {
      RNA_enum_set_identifier(session->context, &properties.value, "export_format", "GLB");
    }
    Reports reports;
    const bool undo = request->operation == BLENDER_OHOS_IMPORT_OBJ || request->operation == BLENDER_OHOS_IMPORT_GLB;
    const wmOperatorStatus status = WM_operator_call_py(session->context, ot, wm::OpCallContext::ExecDefault,
                                                       &properties.value, reports.value, undo);
    out->operator_flags = uint32_t(status);
    if (BKE_reports_contain(reports.value, RPT_ERROR)) {
      return finish(GHOST_OHOS_CALLBACK_FAILED, "Blender无法完成文件操作，请检查文件格式或可用空间");
    }
    if (status & OPERATOR_FINISHED) {
      if (output && !directory.regular(leaf, false)) {
        return finish(GHOST_OHOS_CALLBACK_FAILED, "Blender未生成有效的输出文件");
      }
      return finish(GHOST_OHOS_OK, "Blender文件操作已完成");
    }
    if (status & OPERATOR_CANCELLED) { return finish(1, "Blender取消了文件操作"); }
    return finish(GHOST_OHOS_CALLBACK_FAILED, "Blender文件操作未完成");
  }
  catch (...) { return finish(GHOST_OHOS_CALLBACK_FAILED, "Blender文件操作发生异常"); }
}
#endif
