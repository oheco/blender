# Keyboard and IME development repair — 2026-10-11

This task follows the user's instruction to avoid broad testing/acceptance. It repairs the host input layer using the actual local Godot editor adaptation as reference. It does not resume the stopped full Core build or initiate publishing/catalog work.

The historical first candidate was **5.2.2-ohos.host.14**, with minimum API **22** (official packaging mapping **6.0.2(22)**). The target MOR-M2 at `10.22.216.66:43773` runs API **26**. Historical host.12 remains the accepted resize package; host.13 was an intermediate preserving upgrade before correcting the inherited API15 declaration.

## Current follow-up: host.15, user-confirmed shortcuts

The user reported that host.14's IME worked but scene keys did not. Inspection found a concrete difference from Godot: Godot's XComponent has no `libraryname`, while Blender's binds a native host. Removing Blender's native key callback left the physical-key ingress disconnected from the native surface.

**5.2.2-ohos.host.15** restores `OH_NativeXComponent_RegisterKeyEventCallback`. It reads SDK Ctrl/Alt/Shift snapshots, retains Meta's actual transitions and uses the existing atomic modifier/key batch. ArkTS no longer forwards physical keys for this native surface, avoiding duplicate delivery; its printable fallback remains. IME source and the accepted Core are byte-identical to the prior stage. Native compilation/signing, ArkTS packaging, official verification and the preserving upgrade succeeded.

The device trace confirms native ESC down/up with `window_active=1`, `surface_focus=1`, generation 1 and queue `result=0`. Automated F3 search was not confirmed because focus changed to another window; guarded attempts skipped injection. The user subsequently confirmed **“我试了一下快捷键正常工作了”** at 2026-10-11 23:49 Asia/Shanghai. This is the actual shortcut-operation confirmation; no broad test/acceptance is claimed.

[Installed host.15 evidence](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/native-key-route-host15-device-20261011.json), [native build receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/native-key-route-host-native-20261011.json), [final signed HAP](/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.15-native-key-route-signed.hap), [packaging receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.15-native-key-route-signed.hap.receipt.json).

Host.15 HAP: **419636693 bytes**, SHA-256 `ea6f3f99651cb27143034a999d4928bfffc00de5c13bef3ad12b9cb68e54f683`. Signed host SHA-256 `ce2bd83d5ba8d066dd3efac57c8b5d4a5e55682971664343d1f1d8811b1d2320`. Minimum API22, UID/signing identity and 120ms/250ms resize waits are retained.

## Historical host.14 changes

- [ArkTS page](deveco/entry/src/main/ets/pages/Index.ets) forwards one physical-key stream with modifier snapshots, consumes mapped keys, requests surface focus after readiness, pointer press and picker completion, and enables focus on touch. The old parallel native key callback is removed in [NAPI](deveco/entry/src/main/cpp/napi_init.cc). IME-composed text retains its separate commit path.
- [Keyboard input helper](deveco/entry/src/main/cpp/keyboard_input.h) reconciles Ctrl/Alt/Shift/Meta before the physical key, preserves left/right modifiers, detects repeats and resets on focus loss. [Host bridge](deveco/entry/src/main/cpp/host_bridge.cc) submits each modifier/key batch atomically to the existing reliable queue. IME cleanup errors no longer suppress the core focus-loss event that releases pressed keys.
- [Ability](deveco/entry/src/main/ets/entryability/EntryAbility.ets) observes actual window ACTIVE/INACTIVE and queries initial focus. Effective focus requires both window activation and native XComponent focus; application foreground alone no longer asserts focus.
- [IME session](deveco/entry/src/main/cpp/ime_input.cc) uses Detach alone, separates inactive input from retained SDK ownership on cleanup failure, validates Attach output and preserves the latest live-generation field/caret request across blur. Explicit end and surface retirement cancel that intent. GetTextConfig remains identity guarded; callback threads are explicitly IPC rather than switched to ArkUI.
- Window/display metadata is queried on ArkUI before taking IME locks. Surface position comes from FrameNode global display position plus XComponent pixel offsets. Candidate coordinates are `surfaceDesktopPx - displayOriginPx + caretClientPx`; caret dimensions are not density-scaled again. Window/display migration rebinds the session. The engine reconciles UI publications after READY ticks; cursor dedup commits only successful full-tuple notifications, allowing subsequent publications to retry failures.
- [Build profile](deveco/build-profile.json5) declares API22. [Packager](../build_resize_hap.py) preserves the reviewed minimum, supporting historical API15 and current API22 through the pinned official SDK mapping.

## Narrow verification and artifacts

- [Focused check receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/keyboard-ime-focused-check-20261011.json): **8 native physical-key/modifier cases PASS**, using the actual helper, including modifiers held before focus, missing modifier-up, right Ctrl, repeat and reset.
- [Native host receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/keyboard-ime-host-native-20261011.json): five translation units compiled on native HarmonyOS ARM64, signed host ELF, all **23** Core imports bound.
- [Final signed HAP](/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.14-keyboard-ime-api22-signed.hap) and [packaging receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/artifacts/blender-5.2.2-ohos.host.14-keyboard-ime-api22-signed.hap.receipt.json): ArkTS and official HAP signing/verification passed; **94 native libraries** preserve staged bytes and runtime-manifest parity.
- HAP size: **419615007 bytes**. SHA-256: `f26134a8529891a7bd2f6eb58ad1191017cbb224f1325f3738185980239de6bf`.
- Signed host SHA-256: `e70349e46a31a8d835881fe01125440023785e28103e95a19a896e694a6f21a7`.
- Retained host.12 Core SHA-256: `5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443`. The [host.13 installation record](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/keyboard-ime-host13-device-install-20261011.json) confirms all **20** frozen Core/GPU/window source files are unchanged. Ordinary resize remains **120ms**; maximize/recover remains **250ms**.

## Device evidence limits

The [final host.14 device receipt](/storage/Users/currentUser/dev/ohos/blender-ohos-work/state/keyboard-ime-host14-device-install-20261011.json) records successful `hdc install -r`, installed version **14**, preserved bundle `org.oheco.blender`, UID **20020190**, existing signing fingerprint and successful ability launch. Installation/version/identity and native process/window startup are the device checks in this task. Chinese composition, candidate placement after cross-display movement, and actual human keyboard feedback are **not visually verified** and are not claimed as complete acceptance. Existing selection/surrounding-text and inline-preview limitations are unchanged.
