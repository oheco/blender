# Volume dependency source-only handoff

This is a **source-only prepared** registry and candidate recipe, not a completed
native offline builder. It retains complete original OpenVDB 13.0.0/NanoVDB,
Blosc 1.21.1, FFTW 3.3.10, TBB v2022.3.0, Imath 3.2.2, Zlib 1.3.1 and Zstd 1.5.7
archives. Matching existing registry entries are verified and reused without
rewriting their manifests, source parts or notices. New sources use ordinary Git
parts through the existing vendor_archive utility, with unchanged original bytes,
fixed official archive hashes and supplemental SHA256.

The provenance and every archive/patch/notice reference resolve from the
repository root. Callers supply a real private case-sensitive source cache; no
absolute development-cache path is build routing. A future native builder must
receive the actual SDK, toolchain and signing wrappers as declared inputs.

From the repository root, reconstruct all complete pristine archives offline:

```sh
python3 build_files/ohos/deps_volume_sources/materialize_volume.py \
  --destination "$XDG_CACHE_HOME/blender-volume-source-inputs"
```

The materializer only verifies and reconstructs original archives. It does not
configure, compile, install, rewrite upstream archive bytes, or substitute a
prebuilt system library. `provenance.json` and `recipe.candidate.json` explicitly
retain `offline_builder_completed: false`. Inventory files hash every original
regular file and preserve symlink targets; local source patches have separately
verified forward/reverse file hashes and remain outside pristine archives.

The selected core closure is TBB + Imath half + Blosc + Zlib + real native
pthreads. Blosc retains builtin blosclz and bundled LZ4 1.9.3. Zstd is a fixed
available source input; the formal Blosc codec profile does not enable it.
Delayed loading is OFF in the formal Blender recipe; upstream only requires
Boost when that option is ON. ZIP/BLOSC volume IO, mesh-to-volume/volume-to-mesh,
all explicit template instantiations, NanoVDB CPU interop, FFTW double/float and
separate pthread archives remain selected. FFTW's stale CMake 3.3.9 version and
missing thread exports are repaired with a separate exact source patch matching
the fixed 3.3.10 archive; numerical kernels and actual probe results are intact.

All named original license/copyright/notice files are mirrored separately and
also retained inside the complete archives. Blosc's LICENSES directory is mirrored
in full, including filenames that do not start with LICENSE. Formal OpenVDB
metadata and archive LICENSE text still declare MPL-2.0, while inspected OpenVDB
13 core and NanoVDB source SPDX headers declare Apache-2.0. Both original
statements are retained, with original source header mirrors and explicit
provenance for the standard Apache text reused from TBB; no original declaration
is silently rewritten. Original Blender and native source patches are retained.

The complete builder and actual runtime acceptance remain work for the persistent
parent task: native Clang20/SDK15 static/PIC signed compilation; real dependency
and function probes; portable installed CMake/pkgconf metadata with sealed input
lineage; staged and migrated CMake/pkgconf consumers for compressed VDB/NanoVDB
IO, mesh-volume conversion and both FFT precisions; and Blender GN/ocean/glare
and HAP end-to-end verification. Python/nanobind/NumPy wrappers, GPU/Vulkan/CUDA,
AX/LLVM, ARM NEON FFT optimization and PIC dlopen runtime ownership remain separate
integration or unverified scope. Source byte/hash success does not accept those
features or imply a completed application build.
