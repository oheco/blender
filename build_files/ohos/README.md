# Blender 5.2.2 — HarmonyOS ARM64 adaptation

This branch starts at upstream `v5.2.2`, commit
`d13f752e3b9c4f8c261cda552b1021f8bcc0382c`.
It is **work in progress, not a usable Blender release**.

## Product requirements

- Preserve the built-in game-oriented 3D modeling workflow: mesh editing,
  modifiers, sculpting, UVs, material authoring, Geometry Nodes, save/reopen,
  and representative asset exchange.
- Use Vulkan for the editor, Workbench and EEVEE; initially use Cycles CPU.
- Bundle CPython 3.13.13 and its runtime modules. Reuse reviewed OHOS adaptation
  techniques from oheco CPython 3.14, not its binary ABI or configuration cache.
- Keep MCP integration as independently installable/updateable add-ons.
- Do not require subprocess execution or restricted execution permissions for
  the core workflow. Shared-library loading and signatures are separate checks.
- Deliver a complete relocatable DevEco editor project. Native automated
  validation and the user's final project compilation/acceptance are recorded
  separately; neither is silently claimed on behalf of the other.

## Current target and genuine GPU capability gap

A signed native probe was built and executed on HarmonyOS ARM64 using the
real SDK 26.0.0.35-Beta, without a desktop ICD or injected Vulkan layer.
The current device exposes Maleoon 935 / Vulkan 1.3.309 (driver B312).
The unmodified upstream 5.2.2 requirements are not satisfied:

- `vertexPipelineStoresAndAtomics = false`
- `multiViewport = false`
- `logicOp = false`
- `dualSrcBlend = false`

Vulkan 1.2 and 1.3 queries agree. A direct Vulkan 1.0 feature query agrees with
the complete features2 result. Enabling each missing feature in isolation causes
`vkCreateDevice` to return `VK_ERROR_FEATURE_NOT_PRESENT`; the positive control
succeeds. This is not a loader initialization failure.

The user authorized genuine equivalent Vulkan compatibility paths on the current
device. Capability checks must only be changed after the corresponding rendering
path has a real replacement and representative tests. Do not advertise false
features, define an Apple platform macro, or treat deleting checks as compatibility.

The checked-in probe can also be built as a C-ABI shared library for a diagnostic
HAP; terminal execution is **not** default-permission HAP, WSI or rendering proof.

## Toolchain requirement

Upstream requires Clang >= 17 and C++20. The installed native SDK provides
Clang 15.0.4. The pinned native LLVM/Clang 20.1.8 bootstrap is now built, installed
and validated for Blender's required C++20 subset with the SDK's static libc++15
and experimental library. The vendor SDK and global tools are unchanged. This
subset does not promise every C++20 library API. Compiler version and function
checks are not removed or falsified.

All linked configure probes, build-time executables and native test binaries are
signed before execution. Build and source-extraction caches belong on a real
application-private filesystem, not hmdfs HOME when case/permission semantics matter.

## Source resources

The GitHub mirror does not host all upstream LFS objects. This fork's `.lfsconfig`
points at the canonical Blender LFS service. Fetch via the configured network proxy
and verify LFS object SHA-256 before treating runtime files as present. A small LFS
pointer is not a valid startup file. No other platform's precompiled dependency
submodule is accepted as an OHOS dependency.

GitHub does not allow this public mirror fork to upload new LFS objects. All
upstream LFS paths/OIDs remain exactly those of `v5.2.2`; they are fetched from
the canonical Blender service. The small derived OHOS launcher PNGs are ordinary
Git blobs distributed by this fork, with path-specific attributes. Before a
branch push, run `python3 build_files/ohos/verify_upstream_lfs.py`. Only if it
confirms no added, changed or removed LFS objects may that push use command-local
`GIT_LFS_SKIP_PUSH=1`. New binary fixtures require an owned, validated data
distribution; never bypass this guard to hide missing uploads.

## Known runtime process paths requiring adaptation

- Modern Extension repository synchronization/install/upgrade/uninstall can
  invoke a Python CLI through `subprocess.Popen`, including modern offline ZIPs.
- Legacy `.py`/ZIP installation has a separate in-process path; add-on behavior
  after installation still requires individual review.
- Batch file previews launch Blender itself; current-project previews are not
  that subprocess path.
- Desktop `open` / `xdg-open` integration must become native HarmonyOS integration.
- Vulkan shader compilation uses threads and shaderc library calls. The optional
  OpenGL self-spawn shader mode is not the selected backend.

## Native editor integration status

The GHOST OHOS system/window/path factories, bounded input queue, controlled
32 MiB engine thread and native Vulkan surface adapter are integrated in the
source tree. The shared diagnostic Core and real native/ArkTS host have been
built, signed, update-installed and started on the current HarmonyOS device.
The installed host.6 reaches `ready=true`. The tested host sources, IME/document
bridge and verified layered application icon are preserved in
[hap_host](hap_host/README.md). Startup acceptance does not establish complete
editing, IME, provider file operations or GPU/resize behavior.

The first isolated native editor configuration now generates a real Ninja graph
with Python 3.13, NumPy 2.3.4, HarfBuzz/FriBidi, static color/image libraries,
OpenSubdiv, TBB, GMP/manifold and Vulkan/shaderc. Its initial diagnostic profile
uses Cycles' built-in BVH while Embree and libmv's Ceres closure are prepared.
Other dependencies which upstream automatically disables when missing remain
tracked work. This profile is not the final editor feature configuration.

```sh
python3 build_files/ohos/configure_native.py \
  --build-dir "$XDG_CACHE_HOME/blender-ohos-editor-bootstrap" \
  --define WITH_CYCLES_EMBREE=OFF --define WITH_LIBMV=OFF
python3 build_files/ohos/build_native.py \
  --build-dir "$XDG_CACHE_HOME/blender-ohos-editor-bootstrap" --jobs 2
```

These integration drivers currently consume the explicitly prepared local
prefixes. Build logs and source/command receipts are preserved under the private
build directory. They are not a complete relocatable DevEco/offline recipe.

The repaired image/color dependency consumers pass CMake and pkgconf linking,
native execution and relocation into a path with spaces, including volumetric
DDS. Source generation and editor compilation have started; complete Blender
linking, embedded lifetime, GPU rendering, modeling and ordinary-app permissions
still need actual acceptance.

## Initial native diagnostic

```sh
python3 build_files/ohos/probes/build_run.py \
  --result-dir build_files/ohos/probes/results-local-new
python3 build_files/ohos/probes/validate_results.py \
  build_files/ohos/probes/results-local-new
```

Use a **new** result directory. The script resolves the actual SDK, signs binaries,
places compiled artifacts under `XDG_CACHE_HOME`, and preserves JSON/log evidence.
These commands are diagnostics, not a complete Blender build instruction.

No release, functionality support statement, or completed DevEco editor has been
produced at this stage.
