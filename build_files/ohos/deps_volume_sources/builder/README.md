# Portable native offline volume builder candidate

This new namespace implements `verify-inputs`, `materialize`, `prepare`, `plan`,
`full`, `acceptance`, `audit` and `migrate`. No new full native run has been
accepted. Prior development builds and tests are prototype evidence only. Parent
owns the fresh full run and Blender/HAP integration; no commit/publication is
part of this candidate.

Seven complete original archives, their original formal hashes and SHA256,
7944 source inventory records, original notices, original Blender patch and the
two original source adaptations plus a separate exact NanoVDB stream repair are
sealed through repository-relative inputs. Current
OpenVDB 13.0.0 core SPDX headers and NanoVDB 32.9.0 headers declare Apache-2.0,
while original OpenVDB archive LICENSE/formal metadata declare MPL-2.0: both
original statements remain intact and are installed with supplementary notices.

The build order is Zlib 1.3.1, Imath 3.2.2, original TBB 2022.3.0, Blosc 1.21.1,
FFTW 3.3.10 double, FFTW 3.3.10 float, OpenVDB 13.0.0 and NanoVDB headers. Every
built dependency comes from complete pinned source in this new root. TBB's
original archive matches the prior geometry source byte for byte; no TBB source
patch was inferred or fabricated. Upstream warns that static TBB is unsupported;
its worker/reduce/exception/allocator behavior is exercised again in the new
full run. No accepted external TBB/Imath/Zlib prefix is used.

Zstd 1.5.7 remains a verified complete available archive/inventory, excluded
from the selected built closure. Blosc uses only bundled LZ4 and blosclz; Blosc
Zlib/Zstd codecs and external LZ4 are OFF. ZIP and BLOSC VDB/NanoVDB IO, sparse
negative/HDR values, transforms, metadata, CPU mesh/volume conversion, explicit
instantiation and FFTW double/float pthread plans remain enabled. Delayed
loading is OFF, matching Blender's official scope. GPU/AX/Python bindings, FFTW
NEON tuning, PIC module dlopen/runtime ownership and final Blender/HAP are
separate work.

Archive extraction rejects absolute, parent, noncanonical, backslash, duplicate,
case-colliding, file/directory-conflicting, symlink, hardlink and special entries.
All six selected source trees have only original regular files. Zstd's original
archive contains two symlinks: this excluded input is reconstructed and hashed
without creating filesystem symlinks. Patches are applied with exact before/
after source hashes and reversed on separate complete TMPDIR trees. Zlib's
static-only CMake driver executes the upstream native header/function/type
checks and compiles unchanged build copies, preventing the upstream recipe's
rename of the original zconf.h. Original sources remain guarded after builds.

Outputs must be caller-selected **XDG_CACHE_HOME children**. Temporary files and
directories use **TMPDIR** and are cleaned. A new root/prefix must be absent;
`--resume` requires the exact root ownership marker, sealed input lock and
immutable declared prerequisite bytes. HOME permissions/case are never relied
upon. Existing caches, archives, SDK and accepted prefixes are read only.

The native plan/full contract requires explicit paths to SDK26.0.0.35-Beta,
Clang20 C/C++ drivers, **ld.lld** (retain this invocation basename even when it
is a symlink), genuine Clang20 resource headers with the accepted SDK15 crt/
builtins overlay, binary-sign-tool, Python3, CMake>=3.28, Ninja, Git, ctest and a
native pkgconf executable. Tool bytes and complete SDK C++/sysroot/resource
header inventories are recorded. These tools are development prerequisites,
not copied old partial build caches or future library dependencies. There is no
network command, tool installation, global configuration or source download.

The C++ driver uses explicit g++ driver mode, SDK15 libc++15004 `__n1`, static
libc++/libc++abi and libc++experimental. The signing launcher clears inherited
LD_LIBRARY_PATH/LD_PRELOAD before calling the system signer; linked ELF bytes
are signed only after final link and audited before runtime use. Native host
identity, compiler identity, cross status, HAVE results and runtime results are
never overridden. `plan` executes only existing version/preprocessor/-###
queries and writes the exact commands; it does not configure or produce objects.

```sh
BUILDER=build_files/ohos/deps_volume_sources/builder/builder.py
python3 "$BUILDER" verify-inputs
python3 "$BUILDER" materialize --root "$VOLUME_ROOT" --prefix "$VOLUME_PREFIX" --tmp-dir "$TMPDIR"
python3 "$BUILDER" prepare --resume --root "$VOLUME_ROOT" --prefix "$VOLUME_PREFIX" --tmp-dir "$TMPDIR" \
  --git "$VOLUME_GIT"

# Set all VOLUME_* values explicitly to accepted native tools and a new root.
python3 "$BUILDER" plan --resume --root "$VOLUME_ROOT" --prefix "$VOLUME_PREFIX" \
  --tmp-dir "$TMPDIR" --sdk-root "$VOLUME_SDK" --cc "$VOLUME_CC" \
  --cxx "$VOLUME_CXX" --lld "$VOLUME_LLD" --resource-dir "$VOLUME_RESOURCE" \
  --signer "$VOLUME_SIGNER" --python "$VOLUME_PYTHON" --cmake "$VOLUME_CMAKE" \
  --ninja "$VOLUME_NINJA" --git "$VOLUME_GIT" --pkgconf "$VOLUME_PKGCONF" \
  --ctest "$VOLUME_CTEST"
# Parent-owned long action: use the same declaration with stage full and --resume.
# acceptance/audit/migrate also require the same full explicit declaration.
```

Metadata uses only actual new archives and sealed constants, snapshots original
newly generated metadata before changes, and emits relative static CMake/PC
interfaces with Threads/m/dl/Zlib dependencies. CMake paths are quoted; PC
paths use standard variable syntax so native pkgconf performs exactly one shell
escape for spaces and UTF-8 bytes. Short actual grammar tests reject additional
PC quotes that would double-escape the already escaped pcfiledir expansion. Existing
Zstd/development metadata is never edited or inherited. `full` executes real
native SDK/thread/affinity prerequisites, all seven build groups, both CMake and
PC functional consumers, AArch64/archive-member/SDK ABI/static PIC/signature/
no-RPATH/no-TEXTREL audits, and byte-identical moved Unicode/space-prefix fresh
CMake/PC consumers. Runtime timeouts record the actual failure/exit and prevent a
hang from becoming a PASS. The signed whole-archive PIC module is **never
dlopened**; duplicated static C++ runtime ownership is not accepted.

Only after all those stages succeed does `full-native-acceptance.json` exist.
Short source/path/plan evidence does not establish full native runtime success.
The existing development volume consumer's runtime investigation is independent.
This candidate freezes the current finer-trace volume source and final NanoVDB
regression after read. Parent run 918 timed out (60 seconds, actual -9) on a short
corrupt Nano file after valid conversion and ZIP/BLOSC IO. Its exact canonical
patch restores raw-probe stream position/state and rejects incomplete segment
headers. Parent run 945 reproduced original 23-byte failure (5-second timeout,
actual -9) and the patched source passed **1164 real native checks** (actual 0)
for invalid/named/index/all-grid streams, raw probe offsets, normal EOF, failed
streams and valid raw/NONE/ZIP/BLOSC IO. Exact patch/header/test/log/ELF lineage
is retained separately. This new full builder runs that regression again for
CMake/PC and moved prefixes; parent prototype proof does not accept this pipeline.
Any later source repair requires a new sealed lock and fresh root.
