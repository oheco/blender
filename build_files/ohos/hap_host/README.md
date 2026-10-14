# Embedded HarmonyOS editor host source

This directory started as the native/ArkTS source and application resources for the installed `5.2.2-ohos.host.6` test build. The latest public host source includes the accepted host.12 resize repair and the host.15 keyboard/IME repair (minimum API 22; native key route restored after the host.14 feedback) on top of that historical snapshot. See [keyboard/IME repair evidence](INPUT_VALIDATION.md) for the narrow checks and device-upgrade status. [host-source-snapshot.json](host-source-snapshot.json) remains the unchanged host.6 inventory; its source hashes do not describe the later repair. The source-only template still requires runtime payloads and signing configuration. The public `5.2.2-ohos.17` preview supplies a relocatable DevEco project with the accepted prebuilt native/runtime inputs, omitting personal signing material; see [release usage and limits](RELEASE_HOST17.md). The [host.15 release](RELEASE_HOST15.md) remains a historical record.

The accepted host.17 adds Desktop/Documents/Download startup authorization and SDK-resolved XDG paths, INTERNET permission, the relocated certifi CA path and the system HTTP/HTTPS browser bridge. The regenerated profile approves Desktop in its ACL. Its signing public key changed; the update retained UID, token and data on the tested device. The user confirmed all three directory shortcuts, browser opening and online extension-list refresh. See [access repair and acceptance](DIRECTORY_VALIDATION.md) and the [release evidence](host17-release-evidence.json). The public revision is `5.2.2-ohos.17`; prior release artifacts remain immutable.

On MOR-M2, `5.2.2-ohos.host.12` was installed with `hdc install -r`, preserving the existing UID, signing certificate identity and application data. The user accepted maximize/recover and the settled layout wait: **“等待已合适，仍然完全不卡”**. Resize is no longer deferred. This is qualitative acceptance on that device. The subsequent host.14 IME and host.15 shortcuts received user confirmation; full editing, cross-display candidate placement, picker and full rendering acceptance remain open. The packaged Core was built incrementally in the diagnostic embedded profile, strict OFF/libmv OFF. The fresh full Core build was stopped at the user's request to avoid broad testing/acceptance; no successful fresh full Core build is claimed. This does not establish complete port acceptance, a public release or software catalog delivery. See [resize validation and reproduction](RESIZE_VALIDATION.md) for source links, artifact hashes, evidence and limits.

The bridge embeds the real Blender Core and a single shared CPython 3.13 runtime. It serializes surface ownership, forwards bounded input batches, integrates the native IME, uses in-window temporary editors, and exposes provider-backed document exchange from Blender's File menu. No replacement CLI process is launched inside the application.

## Preparing a build

The [deveco build profile](deveco/build-profile.json5) deliberately omits signing configuration; the tree omits native libraries, generated rawfile payloads, build caches, node_modules, SDKs, official attached tools, signing credentials and the HAP itself. The [entry build profile](deveco/entry/build-profile.json5) has explicit native source/Core placeholders. These public templates retain the historical Hvigor 6.26.4 / SDK 26 profile. The host.12 packaging helper uses pinned official Hvigor/plugin 7.26.8 and API 26 Release SDK 26.0.0.105 in a private copy, preserving the prebuilt native inputs from the original native SDK.

Before building, supply the actual patched Blender source, signed `libblender_core.so`, all signed native dependencies and generated runtime resources. The existing portable exporter has separate evidence gates; copying this source snapshot does not satisfy those gates. Install the specified SDK and tools locally, replace `/ABSOLUTE/...` values in the entry build profile, and configure your own signing material using DevEco. Preserve final signed library bytes; do not strip or copy an additional CPython runtime into the application. Build serially.

## Resize implementation

The [ArkTS page](deveco/entry/src/main/ets/pages/Index.ets) observes maximize/recover and window geometry, sampling every 16 ms while a mode transition is pending. [Core-owned transition state](../../../intern/ghost/intern/GHOST_OHOSWindowTransition.hh) waits 250 ms after the last distinct valid size for maximize/recover; [ordinary resize settlement](../../../source/blender/gpu/GPU_ohos_resize_settlement.hh) waits 120 ms. Duplicate sizes and duplicate active mode notifications do not extend the deadline; invalid sizes do not rearm it. A reversal starts a new transition.

With a complete compatible image, `Hold` keeps the last native buffer scaled to the client bounds and skips window acquire, submit/present, WSI rebuilding and screen/region layout. Input and IME coordinates follow that retained composition, and IME repositioning still runs. After quiet, Blender commits the latest layout. The ABI belongs to Core, with a rebuilt and signed host checked against its exports; scoped render cleanup also covers early `Hold`/retry returns. This quiet-period heuristic is not a public compositor-animation completion guarantee. [RESIZE_VALIDATION.md](RESIZE_VALIDATION.md) describes the acquisition-race path and remaining validation gates.

## Application icon

The application and launcher EntryAbility both reference [blender_app_icon.json](deveco/AppScope/resources/base/media/blender_app_icon.json), with a 1024×1024 transparent PNG foreground and a 1024×1024 opaque `#202124` background. The foreground preserves the three paths and official colors from [Blender's upstream SVG](../../../release/freedesktop/icons/scalable/apps/blender.svg); the system applies its own icon mask. Startup uses the foreground PNG. Both resource scopes are identical, accounting for AppScope resource precedence.

The four small PNG assets are distributed as ordinary Git blobs by this owned fork. Explicit attributes apply only to these derived launcher assets; upstream Blender LFS paths/OIDs are unchanged. The image generator uses HarmonyOS native drawing and Python's standard library, with no downloaded image dependency. Historical host.6 asset hashes and source-copy hashes are recorded in [host-source-snapshot.json](host-source-snapshot.json).

The hidden `iconDiagnostic` state verifies the real ResourceManager `LayeredDrawableDescriptor`, decoded layers and composed PixelMap. On the current device, all three images were 1024×1024; official orange and blue pixels were preserved and `valid=true`. Direct launcher pixel/appearance inspection was not performed. This is narrower than desktop visual acceptance.

## Historical host.6 test build record

The following record describes host.6, before the host.12 resize repair. Its build, user-signature verification, update installation and application launch succeeded on MOR-M2, and Blender initialized to `ready=true`.

- Bundle/version: `org.oheco.blender`, versionCode 6, `5.2.2-ohos.host.6`.
- Signed HAP: 419554876 bytes, SHA-256 `014d87ea359904f8c549d1143aa24bbaeb0369c0bb1f7bb8b2fbe6dff98b0a91`.
- Core: SHA-256 `f25b4b153abe4572a930a171a1255019f860af6f8066754f8646c47ebdd4ed8c`, diagnostic/non-strict/libmv OFF.
- Package: 94 DSOs; 93 prior signed inputs and 5340 resources checked.
- Native tile-blend trial remains limited to the reviewed Maleoon cohort; full GPU acceptance is pending.
- At host.6, the user had deferred further resize repairs. That Core retained the existing swapchain extent/SUBOPTIMAL corrections; black-screen behavior during resizing had not been accepted as fixed. This historical deferral is superseded by the host.12 acceptance above.

No certificates, private keys or passwords are included. [host-source-snapshot.json](host-source-snapshot.json) describes the historical copied source bytes; it is not a replacement for native build or installation evidence. The later source patch and hash inventory exist outside the repository and are linked from [RESIZE_VALIDATION.md](RESIZE_VALIDATION.md). The old Git HEAD embedded in the installed incremental Core does not capture the complete dirty source changes.
