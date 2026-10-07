# Portable native offline color builder candidate

This new namespace supplies `verify-inputs`, `materialize`, `prepare`, `plan`,
`full`, `acceptance`, `audit`, and `migrate`. It has no fresh full native PASS.
Parent owns the long native build and Blender integration after slots are free.

The registry contains all 14 original color archives and reuses seven identical
complete prerequisite archives without modifying their registry entries. There
are 21 full source inventories, 11,324 file/link records, 1,442 explicit directory
records and 66 exact original notice records. The 55,338,242-byte OpenImageIO
archive is stored as 33,554,432 + 21,783,810 original-byte parts; reconstruction
retains SHA256 0fc59b8e2708ded02d3793b8f3331f037ed49bfcde38158f05ac0e876ebb85b7.
No source license, copyright or SPDX declaration is rewritten. Mixed third-party
licenses, OCIO documentation CC BY 3.0, OIIO relicensing notices, JPEG's IJG/BSD/
Zlib declarations and original Zstd registry/formal labels remain distinct.

Build order is Zlib, PNG, JPEG, fmt, Imath, TBB, libdeflate, OpenJPH, yaml-cpp,
pystring, Expat, minizip-ng, TIFF, robin-map, pugixml, OpenJPEG, WebP, OpenEXR,
OpenColorIO and OpenImageIO. Every selected library is built independently from
complete pinned source in the caller's new root. Zstd remains a complete pinned
available archive, excluded because selected TIFF/minizip Zstd codecs are OFF.
No copied partial old prefix, accepted native library or old builder is used as
a library input. pkgconf is an explicitly declared immutable development tool.

For bounded source IO, `prepare --source NAME` publishes one complete group
with its exact input seal receipt; it refuses partial/unowned extracted trees.
Repeated groups resume only after complete path/type/mode/hash verification.
`sources.json` exists only when all 20 groups verify. The final source closure
and nonbuilt Zstd original archive are still checked before any full native run.

All six patches are byte-for-byte copies of evidenced original adaptations.
Each file has exact original and patched hashes; pystring's real Blender CMake
wrapper is an explicitly added file. OpenJPH call_once fixes and the final OIIO
DDS Z-slice/depth/error propagation and safe span-coordinate/stride forwarding
are retained. Patches are checked, applied and reversed on separate complete
TMPDIR trees. OpenEXR's static libdeflate discovery stays intact. Zlib's local
static CMake driver runs actual upstream native header/type/function checks on
unchanged source copies without renaming original zconf.h. This is a new CMake
route; historical accepted Zlib used configure/Make.

Baseline OCIO/OIIO/EXR/Imath/Half/color conversion/texture functions remain
available. EXR NONE/ZIP/PIZ/HTJ2K, PNG/JPEG/TIFF/WebP/OpenJPEG, OCIO matrix/inverse/
Look/LUT, OIIO ImageBuf/color conversion/IOProxy/DDS and TextureSystem are enabled.
TIFF optional codecs match the formal OFF set. OCIO SIMD remains ON with actual
native architecture/probe decisions; no Apple SSE2NEON download or false probe
success is inserted. PNG_TESTS remains ON, but upstream creates no tests for
static-only PNG. Full records that limitation and gates real PNG roundtrip and
IOProxy behavior through all four native color consumers; zero tests never
establish a PASS. JPEG preserves
actual accepted SIMD/TurboJPEG/REQUIRE_SIMD and WITH_JPEG8=OFF; official Blender
WITH_JPEG8=ON is a disclosed ABI selection difference. Static PIC rather than
shared libraries, and deferred Python bindings/CLI/HEIF, match the accepted C++
color scope. Those omissions are not desktop feature parity or future Blender
acceptance. Whole PIC module links under -z,text; it is signed and audited but
never dlopened because separate static C++ runtime ownership is not accepted.

The retained original color consumer must run its real 45,184 checks in each
CMAKE/PC and moved CMAKE/PC execution. Additional consumers cover every finite
Half encoding including signed zero/subnormal values, real HALF EXR storage,
Imath arithmetic, negative/HDR tiled MIP generation and filtered TextureSystem
lookups/error paths, and independent TBB worker/reduce/exception/allocator use.
These new tests have not been compiled or run in this short candidate task.

Output roots/prefixes must be caller-selected XDG_CACHE_HOME children; new root
and prefix must be absent. Resume needs an exact ownership marker and sealed
input lock. SDK/tools/header inventories are recorded and must stay identical.
Private case-sensitive storage is verified using TMPDIR; HOME chmod/case/links
are never assumed. All fixtures, patch reversal and moved consumers use TMPDIR
TemporaryDirectory cleanup. The two selected original in-tree links (OpenEXR
fuzz-test directory and Expat README) are allowed only at sealed literal paths
and targets, created after extraction. Other symlinks, hardlinks, specials,
absolute/parent/backslash/noncanonical/duplicate/case/type-colliding archive
entries are rejected. Source bytes, complete paths, types and original modes
remain guarded after builds. Installed relative in-tree symlinks are explicitly
inventoried by target bytes and copied as links; escaping/dangling links fail.

All native tools are explicit: SDK26.0.0.35-Beta, Clang20 C/C++, ld.lld (preserve
that invocation basename), genuine Clang20 resource headers with the accepted
SDK15 crt/builtins overlay, system binary-sign-tool, Python3, CMake>=3.28, Ninja,
Git, ctest and native pkgconf. C++ uses driver-mode=g++, libc++15004 __n1,
-fexperimental-library and static libc++/libc++abi/libc++experimental. No host,
compiler identity, cross status or runtime result is forged. Plan uses only
existing version/preprocessor/-### queries and writes exact future commands.
No SDK/tool installation or mutation, network command or source download exists.

The local signing launcher clears inherited LD_LIBRARY_PATH/LD_PRELOAD, signs
each final linked ELF before native execution, and checks .codesign. Full audits
AArch64 archive members, REL object type, SDK ABI/no __h or pthread_cancel,
actual -fPIC compile records, signed ELF/only libc.so/no RPATH/RUNPATH/TEXTREL,
relative installed CMake/PC/data metadata, exact complete prefix byte/type
inventory, source integrity and byte-identical moved Unicode/space-prefix fresh
CMake/PC links and actual runtime results. Only after all gates pass can
full-native-acceptance.json exist. A full/acceptance/audit/migrate retry moves its old own PASS pointers and
dependent full-acceptance pointers to receipt-history before native queries. Parent must use a fresh root for changed code/lock.

Metadata comes from actual new upstream installs with original bytes preserved
before relative-prefix/static-interface changes. It retains real upstream
exports and required installed OCIO/WebP/robin-map data and finder modules.
PC variables are unquoted: native pkgconf escapes spaces and UTF8 bytes once.
Direct query verification unescapes at byte level before decoding UTF8; normal
native CMake FindPkgConfig handles the real PC interface for fresh consumers.
No fake imported target probe replaces actual linking/running.

```sh
BUILDER=build_files/ohos/deps_color_sources/builder/builder.py
python3 "$BUILDER" verify-inputs
python3 "$BUILDER" materialize --root "$COLOR_ROOT" --prefix "$COLOR_PREFIX" --tmp-dir "$TMPDIR"
python3 "$BUILDER" prepare --resume --root "$COLOR_ROOT" --prefix "$COLOR_PREFIX" --tmp-dir "$TMPDIR" --git "$COLOR_GIT"
python3 "$BUILDER" plan --resume --root "$COLOR_ROOT" --prefix "$COLOR_PREFIX" --tmp-dir "$TMPDIR" \
  --sdk-root "$COLOR_SDK" --cc "$COLOR_CC" --cxx "$COLOR_CXX" --lld "$COLOR_LLD" \
  --resource-dir "$COLOR_RESOURCE" --signer "$COLOR_SIGNER" --python "$COLOR_PYTHON" \
  --cmake "$COLOR_CMAKE" --ninja "$COLOR_NINJA" --git "$COLOR_GIT" \
  --pkgconf "$COLOR_PKGCONF" --ctest "$COLOR_CTEST"
# Parent long action: same declaration, stage full with --resume.
# acceptance/audit/migrate require the same complete explicit declaration.
```

Historical prototype evidence is independent: four staged/moved CMAKE/PC native
45,184-check receipts, 541 header/library hashes, 651 identical staged/moved
files, 101 metadata files and eight signed consumer/PIC receipts. The old raw
built prefix was provenance and did not receive that portability acceptance.
The candidate handoff records genuine short verification and complete CLI,
explicitly separating those historical receipts from this unrun full pipeline.
Blender GUI/rendering/Python/GPU/HAP/release/install remain parent work.
