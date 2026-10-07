# Complete portable offline Vulkan library builder candidate

This NEW builder owns only its selected private root/prefix and temporary staging.
It does not import development scripts or read an old accepted library prefix.
Every future library comes from the eight complete ordinary-part source archives
in this repository. **New full native verification is NOT_RUN.** Earlier accepted
SDK15 library tests are prototype evidence; they do not accept this pipeline or
its Clang20 frontend, new CMAKE/PC adapters or relocated prefix.

The eight inputs are Vulkan-Headers/VUL1.4.341, Shaderc2025.4, glslang exact
commitd213562e35573012b6348b2d584457c3704ac09b, SPIRV-Headers/Reflect SDK1.4.341.0,
SPIRV-Tools2026.1 and VMA3.2.1. Full original archive SHA and Git PAX commit,
6088 original regular-file records and all19 original notice texts are retained.
Only the separately frozen two-file VUL patch borrows opaque OHOS native pointers;
no new/delete/ref/unref/native handle ownership is added. Real pNext allocations
remain owned normally. Source, version, registry, type layout and kernel APIs are
not fabricated. The archive package includes every original file, not just build
or public headers. Inputs/pins/licenses/patch before-after SHA are sealed.

Shaderc builds one complete Headers -> Tools -> glslang -> combined graph; no
standalone binary or accepted compiler archive substitutes for it. The selected
Headers/Tools pins are explicit recipe overrides relative to shaderc DEPS, with
compatibility established only by future functional tests. Upstream tests/WGSL/
fuzzing would need extra locked googletest/abseil/re2/effcee/tint/protobuf sources.
Those unsupported profile extensions are disabled. Vulkan/SPIRV full public
headers/registries and VMA's genuine upstream header Config package install.
VUL dependency updates/codegen, Reflect executables and VMA docs/samples are OFF
because they can write source trees or invoke unpinned tools/downloads. Bytecode
is disabled; source SHA sentinels run around every real configure/build/install/
test. SOURCE_DATE_EPOCH and UTC fallback time are explicit deterministic inputs.

Native prerequisites are caller-explicit Clang20, SDK26.0.0.35-Beta with SDK15
libc++15004 __n1, genuine resource20 headers with exact SDK15 crt/builtins overlay,
ld.lld preserving its invocation basename, Python/CMake>=3.28/Ninja/Git/pkgconf/
ctest/signer and existing system Vulkan loader. AR/RANLIB/readelf/nm may be passed
explicitly; otherwise exact SDK paths are recorded and hashed. The compiler
launcher invokes explicit Python, proper g++ driver mode and static libc++/
libc++abi/experimental, signs final linked ELF bytes with cleared LD environment
and never signs static archives. The source/archive/work tree is selected via
--root; --prefix and --tmp-dir are independently caller-selected. Cache paths
must be under XDG_CACHE_HOME; temporary files/directories exclusively TMPDIR.
No outputs use HOME/hmdfs for privacy or case/permission semantics.

Stages: verify-inputs/materialize/prepare/plan/full/acceptance/audit/migrate.
verify-inputs needs no tools; materialize needs a new root; prepare also requires
explicit Git. plan validates actual tool versions/macros/-###, writes a reviewed
command plan and creates no C++ object or native runtime PASS. full is an explicit
parent-owned long native action; it first runs signed genuine native probes, then
six groups covering eight full sources. It installs real outputs before metadata
and signatures/ABI/closure audits and actual consumers. First root/prefix must
be absent. Existing owned same-lock root requires --resume. A changed sealed
input requires a NEW root; old receipts remain history and stale PASS pointers
are removed before a new full attempt.

```sh
VULKAN_BUILDER=build_files/ohos/deps_vulkan_sources/builder/builder.py
VULKAN_ROOT="$XDG_CACHE_HOME/portable-vulkan-review"
VULKAN_PREFIX="$VULKAN_ROOT/prefix"
python3 "$VULKAN_BUILDER" verify-inputs
python3 "$VULKAN_BUILDER" materialize --root "$VULKAN_ROOT" --prefix "$VULKAN_PREFIX" --tmp-dir "$TMPDIR"
python3 "$VULKAN_BUILDER" prepare --resume --root "$VULKAN_ROOT" --prefix "$VULKAN_PREFIX" --tmp-dir "$TMPDIR" --git "$(command -v git)"
# Parent full: all --sdk-root/--cc/--cxx/--lld/--resource-dir/--signer/--python/
# --cmake/--ninja/--git/--pkgconf/--ctest/--loader arguments are required.
# Read the handoff's concrete fresh-full-command.json and parent-full.sh.
```

Relative prefix-owned Shaderc combined/Reflect adapters are labeled recipe-added;
upstream exports for Vulkan/Tools/glslang/VMA remain genuine. Original generated
PC/CMake metadata is SHA-journaled before relocation repair. Shaderc upstream PC
reports2023.8.1 because its parser ignores newer undated CHANGES lines; the recipe
source-pin PC explicitly advertises2025.4 separately. Headers project1.5.5 versus
SDKtag1.4.341.0, Tools PC2026.1.1 versus tag2026.1 and glslang project15.4.0 versus
exact commit are preserved separately. Standard pc variable syntax delegates
one native pkgconf shell escape; additional PC quotes double-escape spaces.
Both public pkgconfig locations are isolated from ambient metadata. Advertised
static entry is shaderc_combined, not incomplete upstream shaderc_static.pc.

Future fresh CMAKE/PC consumers compile/validate/disassemble/optimize compute and
tile GLSL to Vulkan1.3/SPIRV1.6, check extension/capability/color-read and coherent
mode, Reflect descriptor/push/workgroup/tile-output data, malformed inputs and
independent threaded compiler state. Reflect's macro/header dependency must
propagate through imports; no fallback macro or blanket prefix/source include
is supplied. Separate public glslang/Tools/VMA packages and exact immutable
Blender Find modules are exercised. VUL tests real LayerSettings and borrowed
pointer/pNext lifecycle; independent symbol audit detects native ref/unref APIs.
All archive members are real AArch64 REL with SDK15 ABI; small upstream glslang
compatibility stubs are retained without invented implementation/size rules.

BUILD_SHARED_LIBS=OFF still creates upstream Shaderc/Tools DSOs; every installed
DSO is signed/audited, must preserve exact signed build bytes, and may need only
libc with no dynamic C++/RPATH/TEXTREL. Complete static groups pass whole-archive
PIC -z text --no-undefined modules. They are never dlopened, and duplicated
static C++ runtime ownership remains separate integration work. All supported
prefix metadata rejects absolute old cache/work/source routes. Prefix byte/link
inventory must remain identical when copied to a Unicode/space TMP location;
both fresh CMAKE/PC consumers actually compile/link/run there before migration
can PASS. Temporary fixtures and moved prefix are cleaned.

The existing caller system loader is external: no bundled upstream loader,
ICD replacement or injection. Its future instance/device enumeration records
observed facts only. **No GPU device allocation/dispatch/draw/pipeline/readback/
pixels, runtime vkCreateSurfaceOHOS, native buffer lifetime, VMA device allocation,
Blender/HAP/DevEco, publication or formal installation is accepted by this
library profile.** Native full or migration failures preserve real exits/logs and
never imply higher-level feature success.
