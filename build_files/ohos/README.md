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
Clang 15.0.4. A separate pinned native LLVM/Clang 20.1.8 bootstrap is being
prepared; the vendor SDK and its globally installed tools are not modified.
The SDK C++ library's actual feature subset must also be tested. Compiler version
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

GitHub does not allow this public mirror fork to upload new LFS objects. Current
source adaptation changes are text-only, and all LFS paths/OIDs remain exactly
those of `v5.2.2`; they are fetched from the canonical service rather than uploaded
to a server where we do not have write access. Before a text-only branch push,
run `python3 build_files/ohos/verify_upstream_lfs.py`. Only if it confirms no added,
changed or removed LFS objects may that push use command-local `GIT_LFS_SKIP_PUSH=1`.
New binary fixtures require an owned, validated data distribution; never bypass
this guard to hide missing uploads.

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
