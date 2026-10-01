# GPU compatibility backports and validation status

Base: Blender v5.2.2, `d13f752e3b9c4f8c261cda552b1021f8bcc0382c`.
These are source adaptations, **not yet a completed/validated Blender renderer**.

## Reviewed upstream changes adapted to 5.2

1. Vertex-stage stores/atomics made optional:
   https://github.com/blender/blender/commit/09417042a1fad5d831c73746f1a1fb2c2989c6b9
   - Query and expose the actual feature value.
   - Do not request a false Vulkan feature.
   - Developer DebugDraw lifetime feedback cannot run without that capability.
   - This is not disabling mesh normal, selection or sculpt overlays.

2. EEVEE single-viewport shadow fallback:
   https://github.com/blender/blender/commit/f7d5dec272609b848a2141f04f4c208165fb051c
   - The actual code uses the largest shadow viewport with corresponding
     projection/render-map sizing; its commit message's old per-view description
     is not the implementation.
   - Preserve the 5.2 `NO_VIEWPORT_INDEX` BSL bridge and eliminate viewport-index
     assignments in the unsupported specialization.
   - Render-map sentinel initialization and a force-false compute test have been
     added, including poisoned unused cells, independent host expectations and
     bounded counter reads. The native DRAW_TEST still needs to run. A native-
     multi-viewport test skip is not fallback validation.

3. Editor inversion-border policy:
   https://github.com/blender/blender/commit/8add4a562880385e3449b75c3a20a5ff3aed74fb
   - Migrate built-in visibility borders from fixed-function XOR to INVERT blend.
   - Keep the deprecated/native XOR interface capability-gated.
   - INVERT is a visibility policy, not arbitrary bitwise XOR equivalence. In
     particular, alpha preservation and sRGB behavior differ. Vulkan fixed
     logic operations are not applied to floating-point or sRGB attachments.

The original patches were inspected and adapted rather than blindly applied:
5.2 BSL builtin selection and nearby APIs differ from the later upstream code.
Other platforms retain their existing native paths.

## Dual-source blending — still gated

The ordinary 5.2.2 native `dualSrcBlend` requirement remains the default. The
current Maleoon driver reports false and rejects enabling it; neither fact is
changed by the source adaptations. A limited experimental integration is now
present, gated by `BLENDER_VK_TILE_BLEND_EXPERIMENT=1`, the exact verified Maleoon
935 B312 cohort and the logical device actually enabling tile COLOR read access.
It preserves the false native feature and reports a LIMITED support level.

A coherent `VK_EXT_shader_tile_image` path is under development. Signed native
experiments have verified the device feature can be enabled, and single-sample
RGBA8 overlap tests implement `src0 + destination * src1` without native dual
blending. Separate signed experiments also verified finite-normal single-sample
RGBA16F and RGBA32F: 19 cases per format compare precise separate multiplication
and addition against per-primitive CPU storage quantization and ordinary reference
blending, with exact raw results and repeat validation. The explicit FP32 FMA
probe did not match its true-fused CPU model; that failure is retained and the
candidate does not use `fma`. Original material S0/S1 arithmetic is not forced
precise; only the isolated final blend operations carry NoContraction.

The initial format whitelist is RGBA8/RGBA16F/RGBA32F. Unsupported targets produce
an explicit pipeline error rather than an illegal draw. sRGB, nonfinite/overflow/
subnormal boundaries, MSAA, MRT, depth/discard combinations, all Blender passes,
HAP permissions and WSI are **not** inferred from these isolated experiments.
Full Blender compilation and integrated renderer validation remain outstanding.

The alternative of snapshot plus single fullscreen compositing is only valid
for the audited single-hit core resolve paths; a generic snapshot would be wrong
for multiple overlapping fragments in one draw. Two-pass multiply/add adds a
storage-format rounding step and is not a strict generic equivalent.

## Release gate

- Build the complete adapted Blender with the real native toolchain.
- Run the new fallback tests, including stale render-map poison/sentinel checks.
- Exercise Workbench, EEVEE shadows, materials, transparency, volumes and the
  built-in game-modeling workflow on the actual device.
- Validate ordinary HAP/NativeWindow lifecycle and presentation separately.
- Keep native hardware support distinct from verified emulation support.

No feature is advertised as supported merely because a version/capability check
has been removed. No usable release is declared by this document.
