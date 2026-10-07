# Embedded HarmonyOS editor host source snapshot

This directory preserves the native/ArkTS source and application resources used by the installed `5.2.2-ohos.host.6` test build. It is a source snapshot, not a complete redistributable DevEco runtime project or a production release. The actual host.6 build, user-signature verification, update installation and application launch succeeded on the current MOR-M2 device. Blender initialized to `ready=true`; full editing, IME, picker, rendering and resize acceptance remain open.

The bridge embeds the real Blender Core and a single shared CPython 3.13 runtime. It serializes surface ownership, forwards bounded input batches, integrates the native IME, uses in-window temporary editors, and exposes provider-backed document exchange from Blender's File menu. No replacement CLI process is launched inside the application.

## Preparing a build

The `deveco` tree deliberately omits native libraries, generated rawfile payloads, build caches, node_modules, SDKs, official attached tools, signing credentials and the HAP itself. Root signing configuration was removed, and entry native source/Core arguments are explicit placeholders. The remaining public build configuration matches the tested Hvigor 6.26.4 / SDK 26 profile.

Before building, supply the actual patched Blender source, signed `libblender_core.so`, all signed native dependencies and generated runtime resources. The existing portable exporter has separate evidence gates; copying this source snapshot does not satisfy those gates. Install the specified SDK and tools locally, replace `/ABSOLUTE/...` values in the entry build profile, and configure your own signing material using DevEco. Preserve final signed library bytes; do not strip or copy an additional CPython runtime into the application. Build serially.

## Application icon

The application and launcher EntryAbility both reference `blender_app_icon.json`, with a 1024×1024 transparent PNG foreground and a 1024×1024 opaque `#202124` background. The foreground preserves the three paths and official colors from `release/freedesktop/icons/scalable/apps/blender.svg`; the system applies its own icon mask. Startup uses the foreground PNG. Both resource scopes are identical, accounting for AppScope resource precedence.

The four small PNG assets are distributed as ordinary Git blobs by this owned fork. Explicit attributes apply only to these derived launcher assets; upstream Blender LFS paths/OIDs are unchanged. The image generator uses HarmonyOS native drawing and Python's standard library, with no downloaded image dependency. Asset hashes and source-copy hashes are recorded in `host-source-snapshot.json`.

The hidden `iconDiagnostic` state verifies the real ResourceManager `LayeredDrawableDescriptor`, decoded layers and composed PixelMap. On the current device, all three images were 1024×1024; official orange and blue pixels were preserved and `valid=true`. Direct launcher pixel/appearance inspection was not performed. This is narrower than desktop visual acceptance.

## Test build record

- Bundle/version: `org.oheco.blender`, versionCode 6, `5.2.2-ohos.host.6`.
- Signed HAP: 419554876 bytes, SHA-256 `014d87ea359904f8c549d1143aa24bbaeb0369c0bb1f7bb8b2fbe6dff98b0a91`.
- Core: SHA-256 `f25b4b153abe4572a930a171a1255019f860af6f8066754f8646c47ebdd4ed8c`, diagnostic/non-strict/libmv OFF.
- Package: 94 DSOs; 93 prior signed inputs and 5340 resources checked.
- Native tile-blend trial remains limited to the reviewed Maleoon cohort; full GPU acceptance is pending.
- The user explicitly deferred further resize repairs. The current Core retains the existing swapchain extent/SUBOPTIMAL corrections; black-screen behavior during resizing is not accepted as fixed.

No certificates, private keys or passwords are included. `host-source-snapshot.json` describes copied source bytes; it is not a replacement for native build or installation evidence.
