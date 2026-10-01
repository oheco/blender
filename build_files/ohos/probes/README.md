# Native Vulkan capability diagnostic

This diagnostic is build-time/development tooling, not an extra executable that
Blender's HAP must launch. It can also be compiled as a shared library exposing
the C ABI in `vulkan_probe.h`, so a separate ordinary-permission diagnostic HAP
can perform the same queries in its own process.

## Native run

```sh
python3 build_files/ohos/probes/build_run.py \
  --native-sdk /absolute/path/to/native-sdk \
  --result-dir build_files/ohos/probes/results-local-new
python3 build_files/ohos/probes/validate_results.py \
  build_files/ohos/probes/results-local-new
python3 build_files/ohos/probes/run_shared.py \
  build_files/ohos/probes/results-local-new/build-info.json
```

Omit `--native-sdk` only when the SDK is installed with oheco and its installed
metadata is available. A full SDK root containing `native/sysroot` is accepted.
Run on HarmonyOS ARM64 with the genuine native compiler and `binary-sign-tool`.
Use a new result directory; outputs never overwrite a previous diagnostic.
Linked executables and the shared library are signed before execution and kept
under `XDG_CACHE_HOME`; compiler, headers, library and artifact hashes are recorded.

The original upstream 5.2.2 requirement checklist is deliberately preserved.
A hardware feature stays false even when this branch gains a software/shader
replacement. The checklist does not prove that every application path is supported.

The validator checks SDK-exact feature fields, API1.3/API1.2 agreement, the
independent direct Vulkan1.0 query, isolated feature-enable device creation,
SHA-256 and null-vs-unqueried handling. It does not turn failed initialization
into a statement about the GPU, and does not count a terminal run as HAP/WSI proof.

## Explicitly unverified here

There is no OHNativeWindow in this terminal diagnostic. Surface creation,
swapchain/presentation, ordinary HAP permissions and rendering correctness require
separate tests. The raw native GPU reports and compatibility pixel experiments
are retained in the development validation records, not silently inferred by this
capability checker.
