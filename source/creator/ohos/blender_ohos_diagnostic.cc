/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "creator_ohos.h"
#include "GHOST_OHOSEngine.h"
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include <sys/stat.h>
/* Separate signed native diagnostic executable; the HAP never spawns this. */
static int32_t no_window(void *, void *) noexcept { return GHOST_OHOS_UNAVAILABLE; }
static bool directory(const char *path)
{
  struct stat st{};
  return path && path[0] == '/' && stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}
struct DiagnosticLaunch { int argc; const char **argv; };
static int32_t diagnostic_run(GHOST_OHOSHost *host, GHOST_OHOSEngine *engine, void *userdata) noexcept
{
  auto *launch = static_cast<DiagnosticLaunch *>(userdata);
  BlenderOHOSSession *session = nullptr;
  int32_t result = blender_ohos_initialize(host, launch->argc, launch->argv, &session);
  if (session) {
    /* Init failure retains partial state. Always request stop on ENGINE, then
     * keep normal scheduled ticks while Pending/Error. No worker/network join. */
    blender_ohos_stop(session);
    while (session) {
      const int32_t pump = blender_ohos_pump(session, 0);
      if (pump == GHOST_OHOS_EMPTY) {
        int32_t exit_code = 0;
        const int32_t teardown = blender_ohos_teardown(&session, &exit_code);
        if (!session && teardown == GHOST_OHOS_OK) {
          result = exit_code;
          break;
        }
      }
      /* Diagnostic has no GUI scheduler. This yields between bounded engine
       * ticks; the interpreter/context remain owned on every error/stall. */
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
      (void)engine;
    }
  }
  return result;
}
int main(int argc, char **argv)
{
  const char *runtime = nullptr, *config = nullptr, *cache = nullptr, *temp = nullptr;
  const char *script = nullptr;
  bool background = false;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "-b") || !strcmp(argv[i], "--background")) { background = true; continue; }
    if (i + 1 >= argc) { fputs("Missing option value\n", stderr); return 2; }
    if (!strcmp(argv[i], "--runtime")) { runtime = argv[++i]; }
    else if (!strcmp(argv[i], "--config")) { config = argv[++i]; }
    else if (!strcmp(argv[i], "--cache")) { cache = argv[++i]; }
    else if (!strcmp(argv[i], "--temp")) { temp = argv[++i]; }
    else if (!strcmp(argv[i], "--python")) { script = argv[++i]; }
    else { fprintf(stderr, "Unsupported diagnostic option: %s\n", argv[i]); return 2; }
  }
  if (!background || !directory(runtime) || !directory(config) || !directory(cache) || !directory(temp) ||
      !script || script[0] != '/') {
    fputs("Usage: blender-ohos-diagnostic -b --runtime ABS --config ABS --cache ABS --temp ABS --python ABS\n"
          "All four existing private directories are required; this diagnostic creates no surface.\n", stderr);
    return 2;
  }
  GHOST_OHOSConfig cfg{};
  cfg.struct_size = sizeof(cfg); cfg.abi_version = GHOST_OHOS_ABI_VERSION; cfg.queue_capacity = 1024;
  cfg.runtime_path = runtime; cfg.config_path = config; cfg.cache_path = cache; cfg.temp_path = temp;
  cfg.callbacks.struct_size = sizeof(cfg.callbacks);
  cfg.callbacks.native_retain = no_window; cfg.callbacks.native_release = no_window;
  GHOST_OHOSHost *host = nullptr;
  int32_t result = ghost_ohos_host_create(&cfg, &host);
  if (result != GHOST_OHOS_OK) { return 3; }
  const char *args[] = {"blender-embedded-diagnostic", "-b", "--factory-startup",
      "--disable-crash-handler", "--disable-abort-handler", "--python-exit-code", "1", "--python", script};
  DiagnosticLaunch launch{int(sizeof(args) / sizeof(args[0])), args};
  GHOST_OHOSEngine *engine = nullptr;
  result = ghost_ohos_engine_start(host, diagnostic_run, &launch, 32 * 1024 * 1024, &engine);
  if (result != GHOST_OHOS_OK) { ghost_ohos_host_destroy(host); return 3; }
  int32_t run_result = GHOST_OHOS_UNAVAILABLE;
  result = ghost_ohos_engine_join(engine, &run_result);
  if (result != GHOST_OHOS_OK) { return 4; }
  if (ghost_ohos_host_destroy(host) != GHOST_OHOS_OK) { return 4; }
  result = run_result;
  fprintf(stderr, "OHOS background diagnostic thread result=%d\n", result);
  return result == 0 ? 0 : (result > 0 && result <= 255 ? result : 1);
}
