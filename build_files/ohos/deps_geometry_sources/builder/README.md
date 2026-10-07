# Offline OHOS geometry source builder

This is a frozen, independently sealed source candidate. **Fresh native full, Blender modifiers and installed HAP are NOT RUN.** Complete original OpenSubdiv v3_7_0, GMP6.3.0 and manifold3.5.2 archives are new formal entries. The immutable existing TBB v2022.3.0 archive is reused and compiled from source inside each new geometry root. No old/partial TBB prefix is an input.

The source lock records original URL/version/SHA-256/size, Blender's formal upstream hash, every regular source file and preserved notice mirrors. OpenSubdiv's 40,736,075-byte original occupies two ordinary parts, each at most32MiB. No source pruning, network download, Git LFS or copied generated platform configuration is used. The separate GMP OHOS triplet/documentation patch has exact three-file before/after hashes and full snapshots. The GNU Make4.4.1 complete formal source entry is a sealed tool dependency.

Run from any copied repository, including a private Unicode/space path. The standalone builder resolves source paths relative to itself and its repository root; it never imports another builder/work directory. Choose an absent private cache build root and prefix, and a real native private TMPDIR. Existing output requires the exact root/prefix/TMP/input-lock ownership marker and `--resume`, with a nonblocking process lock. Normal native system shell/file utilities remain external OS prerequisites.

```zsh
python3 build_files/ohos/deps_geometry_sources/builder/builder.py verify-inputs
GEOM_SCRIPT="$PWD/build_files/ohos/deps_geometry_sources/builder/builder.py"
GEOM_ROOT="$XDG_CACHE_HOME/geometry-native-fresh-review"
GEOM_TMP="$TMPDIR"
GEOM_SDK="/storage/Users/currentUser/.oheco/packages/ohos-sdk-native/26.0.0.35-Beta"
GEOM_LLVM="/data/storage/el2/base/haps/entry/cache/blender-ohos-toolchain/install-llvm20"
# Explicitly selected already accepted signed native GNU Make tool. No tmux route.
GEOM_MAKE="/data/storage/el2/base/haps/entry/cache/blender-ohos-geometry/tools/make"
GEOM_MAKE_SHA="a888ae0d94554dc9e28502565c439ceece267cda15ac79dea4706b044703b864"
# Explicit already accepted read-only source-closure prerequisite for real GLSL tests.
# The portable project can construct its replacement with the formal GPU builder first.
GEOM_SHADER_PREFIX="/data/storage/el2/base/haps/entry/cache/blender-ohos-deps-vulkan/install"
GEOM_SHADER_RECEIPT="/storage/Users/currentUser/dev/ohos/blender-ohos-work/deps-vulkan/results/library-acceptance.json"
GEOM_SHADER_RECEIPT_SHA="486de2ead27ccfc64bb8a49634ca4c8998854b0998226bf0b50ae7bb7c041fc9"
GEOM_ARGS=(
  --root "$GEOM_ROOT" --tmp-dir "$GEOM_TMP" --jobs 2 --runtime-timeout 180
  --sdk-root "$GEOM_SDK" --cc "$GEOM_LLVM/bin/clang" --cxx "$GEOM_LLVM/bin/clang++"
  --lld "$GEOM_LLVM/bin/ld.lld" --resource-dir "$GEOM_LLVM/lib/clang/20"
  --signer "/storage/Users/currentUser/.oheco/bin/binary-sign-tool"
  --python "/storage/Users/currentUser/.oheco/packages/python3/3.14.7-ohos.1/bin/python3"
  --cmake "/storage/Users/currentUser/.oheco/bin/cmake"
  --ninja "/storage/Users/currentUser/.oheco/bin/ninja"
  --git "/storage/Users/currentUser/.oheco/bin/git"
  --pkgconf "/data/storage/el2/base/haps/entry/cache/blender-ohos-deps-base/tools/bin/pkgconf"
  --ctest "/storage/Users/currentUser/.oheco/bin/ctest"
  --shell "/usr/bin/sh" --make "$GEOM_MAKE" --make-sha256 "$GEOM_MAKE_SHA"
  --shader-prefix "$GEOM_SHADER_PREFIX" --shader-receipt "$GEOM_SHADER_RECEIPT"
  --shader-receipt-sha256 "$GEOM_SHADER_RECEIPT_SHA"
)
# Short IO/source-only gates; these do not compile C/C++:
python3 "$GEOM_SCRIPT" materialize --root "$GEOM_ROOT" --tmp-dir "$GEOM_TMP"
python3 "$GEOM_SCRIPT" prepare --resume --root "$GEOM_ROOT" --tmp-dir "$GEOM_TMP" \
  --git "/storage/Users/currentUser/.oheco/bin/git"
# Versions, actual SDK preprocessor macros and -### linker plan only: no object/link/runtime.
python3 "$GEOM_SCRIPT" plan --resume "${GEOM_ARGS[@]}"
# The following belongs to the parent's scheduled future native build. NOT RUN here:
python3 "$GEOM_SCRIPT" full --resume "${GEOM_ARGS[@]}"
# Recheck actual existing native artifacts without manufacturing a full PASS:
python3 "$GEOM_SCRIPT" acceptance --resume "${GEOM_ARGS[@]}"
python3 "$GEOM_SCRIPT" audit --resume "${GEOM_ARGS[@]}"
python3 "$GEOM_SCRIPT" migrate --resume "${GEOM_ARGS[@]}"
```

Paths and hashes in this example are explicitly declared readonly existing prerequisites, not portable package runtime locations. A new GPU prefix is eligible only after its independent complete source build and genuine full native receipt exist. The exact accepted receipt digest, combined archive and public headers are checked; a partial or metadata-only receipt fails. Only `libshaderc_combined.a` supplies ShaderC/glslang/SPIRV tools, avoiding duplicate Tools archives. It is test-only and is absent from OpenSubdiv's public runtime link interface. SDK15 libc++15004 `std::__n1` headers/static archives and exact compiler-rt overlay combine with the declared real Clang20/lld20 frontend; actual native OS/compiler/macros/probes remain intact.

The `full` stage runs signed native pthread/NEON/exception/affinity probes; its own source TBB worker/atomic/exception/allocator; OpenSubdiv CPU/TBB stencil equivalence and affine checks; real generated GLSL basis compilation, optimization, SPIRV validation and invalid-input rejection; GMP native configure/check plus multiprecision roundtrips and exact rational complex cases; manifold Boolean/topology/genus/invalid/disjoint and lossless raw mesh file export/reimport. It constructs real CMAKE and static pkgconfig consumers, records actual canonical archive link plans, audits AArch64/PIC/SDK15 ABI/signatures/no RPATH/TEXTREL, then repeats both consumer routes after a byte-preserving Unicode/space prefix copy with fresh consumer builds. Sealed byte-identical Blender FindGMP/FindOpenSubdiv copies exercise package discovery in those consumers.

Upstream installed CMAKE/PC metadata is preserved under the owned root before relative adapters. Unused absolute `.la` descriptors are removed during prefix assembly with original SHA/size and explicit derived-absent provenance. Signed ELF and archives are never rewritten. Every native runtime check is time-bounded and temporary fixtures/migration trees are cleaned. GNU Make replaces the old hardcoded tmux/tool route and executes GMP's native probes; no `--host`, cross/HAVE identity or `ac_cv_` success is supplied. Assembly remains off in the accepted Blender ARM64 correctness profile, while multiprecision C/C++ functionality is retained.

Full acceptance is emitted only after those genuine new-root gates succeed. Native acceptance excludes hardware GPU dispatch, manifold CrossSection/Assimp format export/bindings, GMP ARM assembly performance, PIC module dlopen/static C++ runtime ownership, Blender modifier integration, installed HAP and release acceptance. The PIC module is linked and audited, not loaded; runtime ownership belongs to separate shared-core integration. Short fixtures use explicitly labeled nonnative placeholder archives and Threads metadata only; their results cannot stand in for native functionality.
