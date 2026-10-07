# Native offline core builder candidate

This new namespace supplies the complete parameterized core pipeline for review.
It has not completed a new-root configure/build/native acceptance run. The earlier
development acceptance is independent evidence, not acceptance of this builder.
Existing provenance, the archive materializer, source registry and compiling
source trees are unchanged.

All six core sources come from the repository's frozen ordinary Git registry.
The complete sources are safely extracted onto caller-selected private,
case-sensitive storage. Six inventories seal every original source file. Three
adaptations are applied with exact before/after digests and reversed on separate
complete trees. The original Embree patch remains intact; its sealed replay adds
only the terminal newline and uses `git apply --recount`.

TBB is a declared already-built prerequisite. The caller must explicitly select
an accepted static native TBB2022.3 prefix with genuine `TBB::tbb` and
`TBB::tbbmalloc` targets. The registry archive, original installed version header,
AArch64 archive members, SDK namespace symbols and relative export metadata are
checked. Actual target paths and TBB native task/exception/PIC behavior are
checked during the future full run. A separate TBB source builder remains an
external integration requirement; this candidate does not compile TBB or silently
choose a developer prefix. Its original archive is already frozen in the volume
registry and is reused without another copy.

The caller supplies these native development prerequisites:

- HarmonyOS/aarch64 host and private `XDG_CACHE_HOME`/`TMPDIR`.
- SDK26.0.0.35-Beta layout: sysroot, libc++15004 `__n1` headers, static libc++,
  libc++abi and libc++experimental, LLVM archiver/readelf/nm.
- Real native Clang20 C/C++ and lld20 drivers, plus the declared Clang20 resource
  directory containing genuine Clang20 builtin headers and accepted SDK15
  compiler-rt builtins/crt overlay. Preparing this overlay belongs to the external
  toolchain builder. No SDK or compiler installation is modified here.
- Executable `binary-sign-tool`, native Python3, CMake>=3.28, Ninja, Git and ctest.
  The sign driver clears inherited SDK C++ loader paths for the system signer and
  verifies `.codesign` before publishing every linked ELF. Archives stay unsigned.
- Explicit already-accepted static TBB prefix. SDK/compiler/TBB bytes are
  read-only prerequisites and recorded with their actual hashes.

A repository-native HarmonyOS module, genuine uname processor detection, complete
consumers, current-thread affinity probe, and actual signed whole-archive control
and retention probes are included. No compiler identity, cross status or probe
success is overridden. The retained Ceres `WHOLE_ARCHIVE` feature is enabled only
after real linker-flag and archive-marker checks (plain archive marker0/exit1;
retained archive marker73/exit0).

Run from any checkout. Choose a fresh root and prefix; the helper refuses existing
unowned outputs. All temporary trees are under the declared native TMPDIR. Root,
source/build/toolchain/logs and install paths are caller data. No builder imports
or routes through developer work/cache scripts.

```sh
python3 build_files/ohos/deps_core_sources/builder/builder.py --verify-inputs

# Short complete source/hash/patch stage, no configure or compile.
python3 build_files/ohos/deps_core_sources/builder/builder.py prepare-sources \
  --root "$XDG_CACHE_HOME/core-source-review" --tmp-dir "$TMPDIR"

# Declare every external prerequisite explicitly before a plan/full run.
# CORE_LLVM_RESOURCE is the accepted Clang20 header + SDK15 crt/builtins overlay.
python3 build_files/ohos/deps_core_sources/builder/builder.py plan \
  --root "$CORE_FRESH_ROOT" --prefix "$CORE_FRESH_PREFIX" --tmp-dir "$TMPDIR" \
  --sdk-root "$CORE_SDK_ROOT" --cc "$CORE_CLANG20_CC" --cxx "$CORE_CLANG20_CXX" \
  --lld "$CORE_LLD20" --resource-dir "$CORE_LLVM_RESOURCE" \
  --signer "$CORE_BINARY_SIGN_TOOL" --tbb-prefix "$CORE_ACCEPTED_TBB_PREFIX"

# Parent-owned long action, only after resources are allocated.
# --resume reuses the exact owned plan root and declared immutable input bytes.
python3 build_files/ohos/deps_core_sources/builder/builder.py full --resume \
  --root "$CORE_FRESH_ROOT" --prefix "$CORE_FRESH_PREFIX" --tmp-dir "$TMPDIR" \
  --sdk-root "$CORE_SDK_ROOT" --cc "$CORE_CLANG20_CC" --cxx "$CORE_CLANG20_CXX" \
  --lld "$CORE_LLD20" --resource-dir "$CORE_LLVM_RESOURCE" \
  --signer "$CORE_BINARY_SIGN_TOOL" --tbb-prefix "$CORE_ACCEPTED_TBB_PREFIX"
```

`plan` invokes only existing tool version/preprocessor queries and reads TBB
archives/metadata. It generates the private parameterized toolchain and exact
commands without CMake configure, compilation, linking or executing new native
code. The signing launcher uses C++ driver mode explicitly, even if the declared
C++ executable is a symlink to the common Clang binary.

`full` first builds/runs native prerequisite probes, then Eigen, Abseil, gflags,
glog, Embree and Ceres with compile parallel2/link pool1/lld threads2 and own
process nice10. It preserves the original options, including actual EigenSparse,
Schur, custom BLAS and genuine libmv glog/gflags closure. All three consumer
features are enabled. It installs original core/TBB notices, records original
metadata before making private pkgconfig metadata relative, runs all six native
core tests and audits ELF signatures, AArch64 members, unsupported symbols, PIC
compile evidence and installed source/metadata integrity.

The migration stage copies core and caller-selected TBB prefixes to TMPDIR paths
with spaces, compares every regular file hash, performs fresh native
configure/links/tests and rejects the old package prefixes in include/link lines.
Only the declared TBB closure from that prerequisite prefix is consumed; additional
libraries in the same prefix are not separately validated.

`full-native-acceptance.json` is written only after the new pipeline's actual
native, artifact and migration stages succeed. Its scope is these six libraries
with the explicitly accepted external TBB/SDK/compiler prerequisites on that host.
Blender CPU rendering/libmv/UI, final DevEco package/HAP signing, SDK runtime
license aggregation and release/install validation remain separate parent work.
Every code/input file is frozen by `inputs.lock.json`; changing one requires a
reviewed new lock and a fresh root. Runtime-selected absolute paths in private
receipts identify declared developer prerequisites, not future hardcoded routing.
