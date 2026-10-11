# HarmonyOS embedded host resize validation

The latest public host source contains the resize repair accepted in `5.2.2-ohos.host.12` on MOR-M2. It extends the historical host.6 source snapshot. [host-source-snapshot.json](host-source-snapshot.json) remains the host.6 inventory and has not been rewritten to imply that its hashes cover the later source. The [README's host.6 record](README.md#historical-host6-test-build-record) preserves that build's results and its then-deferred resize status. The user no longer defers resize.

## Accepted candidate and evidence

The [manual acceptance record](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-host12-manual-acceptance-20261011.json) records **“等待已合适，仍然完全不卡”** for maximize/recover on MOR-M2. Maximize/recover and settled layout latency were accepted qualitatively. There was no numeric FPS or p95 measurement and no proof of compositor-animation completion.

The [installation record](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-host12-device-install-20261011.json) records successful `hdc install -r` for `org.oheco.blender`, versionCode 12, preserving UID `20020190`, the existing signing certificate identity and application data. Its manual-acceptance field was still pending when written; the later manual record supplies the accepted result. The source inventory's pending device field is likewise historical.

| Host.12 input/artifact | Size (bytes) | SHA-256 |
| --- | ---: | --- |
| Signed HAP | 419541873 | `e87e74201e2a935ecd10cc2a3ed5ba0b7f9a07b1facb4173f5c48584a4920633` |
| Signed Core | 224483968 | `5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443` |
| Rebuilt signed host | 3365632 | `e3f49d5dde04005e047505777d5056dee49cf44695eb8bb45752fb529e6c02c7` |

The current local evidence is outside the repository; these links identify the actual records, while the reproduction commands below use caller-supplied paths.

- [Core/host staging record](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-animation250-core-stage-20261011.json): signed inputs and runtime manifest identity.
- [Native host build receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-animation250-host-native-20261011.json): fresh five-source native host build, signing and all Core imports bound, including `ghost_ohos_native_resize_observe`.
- [HAP packaging receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.12-resize-animation250-signed.hap.receipt.json): successful Hvigor packaging and official signature verification; all 94 packaged native libraries and the runtime manifest equal the staged bytes, with the native signatures preserved. Packaging did not rebuild or strip native ELF inputs; the host had been rebuilt separately.
- [Native regression receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-animation250-regression-20261011.json): both signed native suites passed, including 290839 settlement/cursor/IME checks and retirement witness/admission/idle policy checks. These are shared-code regressions, not device WSI performance tests.
- [Source hash inventory](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-animation250-native-source-20261011.json) and [source patch](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/resize-animation250-native-source-20261011.patch): dirty native source against base commit `21fc6c0fc3ba24528f6c1cb7883ccb88ff4fde72`, with patch SHA-256 `af10a4012236a7d31d15f6a3b37077d417959ebd2c2a4bb00386c4ed48f46e8b`. The old Git HEAD embedded in the incremental Core does not capture these complete source changes.

## Implemented policy

The [ArkTS page](deveco/entry/src/main/ets/pages/Index.ets) observes `windowRectChange` and, where supported, `windowStatusChange`. Maximize/full-screen maps to reason 1, recover/floating to reason 2, and ordinary samples to reason 0. It updates the window/client origin for IME geometry and samples every **16 ms** while the native transition remains pending. Sampling stops when quiet expires, dimensions are invalid, the page disappears or shutdown starts; page disposal unregisters its listeners.

The [N-API bridge](deveco/entry/src/main/cpp/napi_init.cc) calls the [Core-owned resize ABI](../../../intern/ghost/GHOST_OHOSNative.h) only while its bridge accepts observations and has an attached surface. The host forwards observations; [Core](../../../intern/ghost/intern/GHOST_OHOSNative.cc) owns the monotonic clock, synchronized transition state and atomic deadline. Deadline expiry cannot erase a newer observation racing it.

| Situation | Rule |
| --- | --- |
| Ordinary client resize | [GPU settlement](../../../source/blender/gpu/GPU_ohos_resize_settlement.hh) waits **120 ms after the last distinct valid size** before committing a new layout. The first frame draws immediately. |
| Maximize/recover | [Window transition](../../../intern/ghost/intern/GHOST_OHOSWindowTransition.hh) arms on the mode event, then waits **250 ms after the last distinct valid sampled size**. A late size change moves the deadline; the wait is not measured only from animation start. |
| Duplicate observation | Unchanged dimensions and duplicate mode notifications during the same active transition do not postpone quiet. |
| Reversal | A different maximize/recover reason starts a new transition and replaces the pending target. |
| Invalid observation | N-API requires positive integral dimensions no larger than `INT32_MAX` and an integral reason 0–2; the Core ABI rejects zero/out-of-range dimensions and invalid reasons. Invalid observations do not mutate or rearm the valid deadline. ArkTS stops sampling on nonpositive dimensions. |
| Continued size changes | Each distinct valid change restarts the applicable quiet interval; there is no periodic intermediate layout commit while changes continue. |

In the [Vulkan context](../../../source/blender/gpu/vulkan/vk_context.cc), a complete image with compatible surface format enables `Hold` before image acquisition. [Window drawing](../../../source/blender/windowmanager/intern/wm_draw.cc) then skips window acquire, submit/present, WSI rebuilding, screen/region layout and redraw-flag clearing. The [native buffer hold/scaling setup](../../../intern/ghost/intern/GHOST_OHOSNative.cc) keeps the last submitted buffer available for scaling to changing client bounds. After quiet, acquisition and one latest full composition can proceed; successful composition clears the pending resize state.

If a transition notification races an already completed acquisition, the context uses `Cached` and presents the retained complete image to consume that acquisition's semaphore. It still leaves layout requests pending. An absent/incompatible complete image requires the full/retry path rather than `Hold`. Scoped `GPU_render_end()` cleanup in window drawing covers early hold/retry exits.

[Shared geometry conversion](../../../source/blender/gpu/GPU_ohos_window_geometry.hh) maps client input and IME caret edges to the retained composition while it is scaled. IME repositioning runs on both `Hold` and `Cached`, and the stationary cursor is remapped when the final layout changes. This preserves coordinate handling; actual Chinese composition and candidate placement remain a separate device acceptance gate.

[OHOS swapchain retirement](../../../intern/ghost/intern/GHOST_ContextVK.cc) retains used generations until a successful reacquisition of the successor's first presented image has a signaled acquisition fence. An old render fence, timeout or empty submission is not a retirement witness. [Admission policy](../../../intern/ghost/intern/GHOST_OHOSSwapchainRetirement.hh) bounds generations and estimated color-image payload, reserves recovery space, and limits persistent SUBOPTIMAL probing per generation. Its 50 ms WSI recovery settlement is separate from the 120/250 ms layout policy. Runtime resize does not use device/queue idle as presentation proof; shutdown/detach keeps the documented limitation when presentation-completion extensions are unavailable.

## Reproduction CLI

Run from the repository root on **native HarmonyOS aarch64**, after inputs are ready and stable. The fresh full Core build was stopped at the user's request to avoid broad testing/acceptance. These reproduction commands are optional instructions; they are not part of the current keyboard/IME task. The commands below are instructions; they were not executed for this documentation update.

Prerequisites are an explicit signed patched Core, the original native SDK used for native compilation, `binary-sign-tool`, a native C++ compiler and `llvm-readelf`; locally prepared official command-line tools 26.0.0.201 (Hvigor/plugin 7.26.8, API 26 Release SDK 26.0.0.105); and a private staged DevEco project with runtime resources, one shared CPython runtime, signed native dependencies and the existing signing identity configured locally. The [public build templates](deveco/build-profile.json5) omit credentials and payloads. Native SDK selection for compilation and Release SDK selection for HAP packaging are distinct.

Set the named variables to your own input locations. `HOST6_HAP` must be the historical trusted baseline with SHA-256 `014d87ea359904f8c549d1143aa24bbaeb0369c0bb1f7bb8b2fbe6dff98b0a91`; the packager enforces that pin. Use a fresh run label so outputs, logs and receipts do not overwrite earlier evidence. The host build directory must be new and beneath `XDG_CACHE_HOME`; temporary owned projects/tests use the real private `TMPDIR` filesystem.

```sh
: "${XDG_CACHE_HOME:?set private cache root}"
: "${TMPDIR:?set private temporary root}"
: "${RESIZE_RUN:?set a fresh run label}"
: "${SIGNED_CORE:?set signed patched Core path}"
: "${NATIVE_SDK:?set original native SDK root}"
: "${NATIVE_CXX:?set native C++ compiler or signing-aware launcher}"
: "${RESIZE_PROJECT:?set private staged DevEco project path}"
: "${HAP_TOOLS:?set official command-line tools root}"
: "${HOST6_HAP:?set trusted historical host6 HAP path}"
resize_evidence="${XDG_CACHE_HOME}/blender-resize-evidence-${RESIZE_RUN}"
resize_build="${XDG_CACHE_HOME}/blender-resize-host-${RESIZE_RUN}"
resize_sdk_view="${XDG_CACHE_HOME}/blender-resize-sdk26-release-view"
resize_hvigor_home="${XDG_CACHE_HOME}/blender-resize-hvigor7"
mkdir -p "${resize_evidence}"

python3 build_files/ohos/tests/run_resize_regression.py \
  --cxx "${NATIVE_CXX}" \
  --receipt "${resize_evidence}/native-regression.json"

python3 build_files/ohos/build_resize_host.py \
  --cpp-dir build_files/ohos/hap_host/deveco/entry/src/main/cpp \
  --core "${SIGNED_CORE}" \
  --sdk "${NATIVE_SDK}" \
  --build-dir "${resize_build}" \
  --receipt "${resize_evidence}/host-native.json"
```

The [regression helper](../tests/run_resize_regression.py) compiles, self-signs and executes the [geometry/settlement suite](../tests/resize_geometry_test.cc) and [retirement suite](../tests/resize_retirement_test.cc) in its owned temporary directory. The [native host helper](../build_resize_host.py) builds all five real host sources serially, checks AArch64/ELF64 and every Core import, signs the resulting host, and records source/input/log hashes.

Before packaging, stage the resulting signed host as the private project's `entry/libs/arm64-v8a/libblender_host.so`, stage the exact signed Core, update the generated runtime manifest to match all native bytes, and use matching repaired ArkTS/native host sources. Set the private AppScope version above host6. These staging steps are prerequisites; the packager does not perform them. Its rebuilt-host receipt check also requires the compiled source/header files and their logs to remain unchanged and available.

```sh
python3 build_files/ohos/build_resize_hap.py \
  --tools "${HAP_TOOLS}" --sdk-view "${resize_sdk_view}" \
  --hvigor-user-home "${resize_hvigor_home}" --prepare-sdk-view

python3 build_files/ohos/build_resize_hap.py \
  --tools "${HAP_TOOLS}" --sdk-view "${resize_sdk_view}" \
  --hvigor-user-home "${resize_hvigor_home}" --validate-tools

python3 build_files/ohos/build_resize_hap.py \
  --tools "${HAP_TOOLS}" --sdk-view "${resize_sdk_view}" \
  --hvigor-user-home "${resize_hvigor_home}" \
  --baseline "${HOST6_HAP}" --project "${RESIZE_PROJECT}" \
  --rebuilt-host-receipt "${resize_evidence}/host-native.json" \
  --output "${resize_evidence}/resize-candidate-signed.hap" \
  --log "${resize_evidence}/hap-build.log"
```

The [HAP helper](../build_resize_hap.py) works offline in an owned private project copy, uses existing local signing configuration without publishing it, and does not modify its `--project`. It creates a new sanitized log and `<output>.receipt.json`; it writes the new HAP only after native/manifest byte parity and the official signature verification pass. Packaging preserves the prebuilt native ELF bytes and signatures. A rebuilt host requires `--rebuilt-host-receipt`; omitting it permits only the pinned host6 host bytes. Packaging success does not establish installed runtime acceptance.

For a device reproduction, preserve the installed signing identity and use update installation with `hdc install -r` on the new signed HAP. Then exercise ordinary dragging, repeated maximize/recover, rapid reversals, minimize/restore, popup/modal interaction, stationary cursor selection and IME composition/candidate placement. Record the device, installed version, UID/certificate continuity, artifact hashes and observations separately from build/regression receipts.

## Remaining limits

The 250 ms maximize/recover wait is a heuristic based on observed geometry, tuned and accepted qualitatively on MOR-M2. No public compositor-completion guarantee was established, and acceptance does not cover every device, refresh rate or system animation. It does not supply numeric smoothness/latency measurements.

Host.12 packages an **incrementally built Core** in the diagnostic embedded profile, **strict OFF/libmv OFF**. The fresh full Core build was **stopped at the user's request** to avoid broad testing/acceptance and **did not succeed**. Incremental compilation, a fresh native host build, shared-code regressions, HAP packaging and the accepted device operation must not be presented as a successful fresh full Core build.

Chinese IME composition/candidate placement and complete editing, picker and rendering acceptance remain open. The Maleoon tile-blend experiment remains limited to the reviewed cohort. Complete port acceptance, public release and software catalog delivery are pending; these records do not establish them.
