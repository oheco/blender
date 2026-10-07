# HarmonyOS glTF compression acceptance

These are acceptance inputs for the parent builder and for a later complete Blender build. All files in this directory are new. This work did not configure or build C++, load a bridge, run bpy, access the network, or modify existing source. Python AST/compile checks passed; the separately requested cleanup candidate's pure Python control-flow contracts passed. Native bridge execution, generated binary fixtures, full bpy acceptance, and HAP loading remain **NOTRUN** until their respective commands run against real staged binaries.

[bridge_abi_acceptance.py](bridge_abi_acceptance.py) loads the actual `libbf_intern_draco_bridge.so` and `libbf_intern_meshopt_bridge.so` with `RTLD_NOW | RTLD_LOCAL`. It requires the native host to report `HarmonyOS/aarch64`, little endian, and 64-bit pointers. It inspects ELF64/ET_DYN/EM_AARCH64, binds the actual exported C ABI, and fails on missing symbols or dependencies. There is no alternate CPU/platform profile. Signing and dependency audits belong to the parent builder.

The source/API manifest fingerprints the real addon operators, library resolver, compression loaders/encoders, mesh importer, relevant bridge headers/implementations, and resource-path implementation. It describes source lineage; it does not claim that the DSOs reproduce unmodified bridge source when the parent builder applies separately recorded standard-header patches.

The fixture is an indexed warped patch with eight attribute vertices, six distinct positions, four oriented triangles, shared indices, a discontinuous UV seam, smoothly varying custom unit normals, and a named metallic/roughness material. Geometry comparison matches full position/normal/UV tuples, allows vertex and triangle reorderings and cyclic triangle rotations, and checks multiplicity and winding. It cannot pass by comparing counts alone. Bounds and finite/unit-normal checks are independent assertions.

| Stage | Position maximum component error | Normal maximum component error | UV maximum component error |
| --- | ---: | ---: | ---: |
| Draco bridge roundtrip (16/12/14 bits) | 0.00035 | 0.0015 | 0.00025 |
| Meshopt addon-equivalent EXPONENTIAL 12 bits | 0.0025 | 0.0025 | 0.000002 |
| Fixture import → bpy compressed export → reimport | 0.006 | 0.006 | 0.0006 |

The native test poisons caller inputs after Draco setters and after decode, then verifies owned mesh data. It copies encoded output twice, checks copied arrays after release, and tracks one release for every successfully created handle. Exact-size writes have 32-byte canaries on each side; read-only inputs are checked for mutations. Allocation multiplication and returned lengths are capped at 16 MiB before Python allocates or calls C. Deliberately undersized Draco copy destinations and dangling/null/double-release handles are never passed to this unbounded C ABI.

Meshopt exercises vertex, triangle-index, and nontriangle sequence codecs, 16/32-bit index conversion, EXPONENTIAL geometry filtering, and OCTAHEDRAL/QUATERNION filter roundtrips. Small-capacity encoders must return zero without crossing guards. Corrupt empty, invalid-header and truncated buffers must return failure and preserve allocation guards; partial writes inside the permitted decode output are allowed on failure. Draco tests invalid component type, unknown attribute metadata/copy, empty/truncated/nonsense/wrong-magic streams, and both sequential and reorderable indexed encoding. Repeated cycles check ownership bookkeeping; they are not a leak-sanitizer or long-duration RSS claim.

Run the native stage only after the parent has linked, signed and installed both bridges. The fixture directory may be absent or already exist **empty**; Unicode and spaces are supported. The JSON report path must be absent. Every report contains independent top-level `result` and `status` values, each `PASS` or `FAIL`; a successful command returns zero. Existing files are never overwritten.

```sh
acceptance=/storage/Users/currentUser/dev/ohos/blender/build_files/ohos/deps_gltf_sources/acceptance
blender_source=/storage/Users/currentUser/dev/ohos/blender
staged_prefix=/absolute/owned/native/install/prefix
gltf_acceptance_tmp=$(mktemp -d "${TMPDIR%/}/gltf-acceptance.XXXXXX")
trap 'rm -rf "$gltf_acceptance_tmp"' EXIT

PYTHONDONTWRITEBYTECODE=1 python3 "$acceptance/bridge_abi_acceptance.py" \
  --source-root "$blender_source" \
  --lib-dir "$staged_prefix/lib" \
  --fixtures "$gltf_acceptance_tmp/压缩 fixtures" \
  --report "$gltf_acceptance_tmp/native-bridge-report.json"
```

The native stage generates correct required `KHR_draco_mesh_compression` and `EXT_meshopt_compression` documents and sidecars using the actual loaded libraries, plus corrupt copies, an unsupported required extension, invalid JSON, a source manifest, and a hashed fixture manifest. Draco accessor bufferViews are omitted and extension attributes use actual returned unique IDs. Meshopt has one compressed stream per attribute/index accessor, aligned compressed offsets, correct decoded byte lengths and strides, `ATTRIBUTES`/`TRIANGLES` modes, and EXPONENTIAL filters for positions/normals. A separate omitted fallback buffer is explicitly marked `fallback: true`, with the extension required. EXT uses index encoding version 1 and vertex encoding version 0, matching the real addon. No canned compressed bytes or uncompressed geometry fallback are used to satisfy the tests.

Keep the generated artifacts and native report in the parent's owned evidence directory before the temporary directory is cleaned if they are needed for later stages. Fixture manifests use local filenames and hashes; they remain valid after relocating the complete fixture directory. Library/source paths in manifests are lineage evidence, not required paths for the later process.

[bpy_import_acceptance.py](bpy_import_acceptance.py) and [bpy_export_acceptance.py](bpy_export_acceptance.py) are separate postlink scripts. They require real `bpy`, a dedicated `--background --factory-startup` process, the expected sealed addon origin and source hashes, the actual unchanged resolver's DLL paths, and identical native-tested bridge SHA-256 values. Optional `--addon-core-dir` adds only the sealed addon parent to Python's search path. The scripts do not monkeypatch operators or compression loaders.

```sh
blender_binary=/absolute/real/final/blender
addon_root="$staged_prefix/share/blender-gltf/scripts/addons_core/io_scene_gltf2"
# Set this to the directory returned by the real addon resolver; see layout rules below.
resolved_bridge_dir=/absolute/real/loader/resolved/library/directory

PYTHONDONTWRITEBYTECODE=1 "$blender_binary" \
  --background --factory-startup --python-exit-code 1 \
  --python "$acceptance/bpy_import_acceptance.py" -- \
  --fixtures "$gltf_acceptance_tmp/压缩 fixtures" \
  --expect-addon-root "$addon_root" \
  --addon-core-dir "$(dirname "$addon_root")" \
  --expect-lib-dir "$resolved_bridge_dir" \
  --report "$gltf_acceptance_tmp/bpy-import-report.json"

PYTHONDONTWRITEBYTECODE=1 "$blender_binary" \
  --background --factory-startup --python-exit-code 1 \
  --python "$acceptance/bpy_export_acceptance.py" -- \
  --fixtures "$gltf_acceptance_tmp/压缩 fixtures" \
  --expect-addon-root "$addon_root" \
  --addon-core-dir "$(dirname "$addon_root")" \
  --expect-lib-dir "$resolved_bridge_dir" \
  --output "$gltf_acceptance_tmp/再次压缩 exports" \
  --report "$gltf_acceptance_tmp/bpy-export-report.json"
```

The real calls are `bpy.ops.import_scene.gltf(filepath=..., import_shading='NORMALS', merge_vertices=False)` and `bpy.ops.export_scene.gltf(export_format='GLTF_SEPARATE', export_draco_mesh_compression_enable=... or export_meshopt_compression_enable=..., export_meshopt_extension='EXT_meshopt_compression', ...)`. Import acceptance reads real mesh corner normals and UV vectors, checks custom normals, topology/seams/bounds, material assignment and Principled shader values, and converts Blender coordinates and UV v back to the source glTF conventions before comparing.

Export acceptance covers all four Draco/Meshopt input/output combinations. It inspects the exported JSON to prove the requested extension is used/required, has real compressed data and correct material/accessor metadata, independently decodes it through the native bridges, and reimports it with the real operator for another mesh/material comparison. Reporting `FINISHED` with an uncompressed document cannot pass.

Import error assertions follow the actual source contract. Corrupt meshopt and structural invalid documents must cancel or produce bpy's RuntimeError from an ERROR report, with the expected diagnostic and no nondegenerate geometry. Baseline Draco's loader logs `Draco Decoder: Unable to decode...` then returns; absent bufferViews can yield zero-filled degenerate geometry and an operator result of `FINISHED`. Acceptance records that actual result and requires the specific logged error plus zero usable triangles. It does not describe this baseline as cancellation. A valid import after the failures verifies recovery.

The separate [cleanup patch](patches/0001-draco-decoder-errorcleanup.patch) closes the confirmed Draco early-return/exception handle leak. The [record](patches/draco-errorcleanup-record.json) includes original/after/patch SHA-256 values and nine pure Python extracted-function contracts. It is unapplied to existing source. The parent may apply it only to an owned staged addon candidate. Add `--allow-draco-errorcleanup-patch` to the bpy commands for that candidate; the source gate then permits only this exact after hash for this exact baseline. No other addon hash differences are accepted, and the report records whether the candidate was actually present. Cleanup preserves the baseline diagnostics/operator behavior.

```sh
# Check the candidate record and control-flow contracts; this does not apply the patch.
PYTHONDONTWRITEBYTECODE=1 python3 "$acceptance/patches/check_draco_errorcleanup.py" \
  --source-root "$blender_source"
```

The unchanged UNIX library resolver has two precise layouts:

1. If `bpy.utils.resource_path('SYSTEM_LIBS')` is nonempty, both DSOs must be under `<SYSTEM_LIBS>/scripts/addons_core/io_scene_gltf2/`. For a Unix engine built with PREFIX and BLENDER_INSTALL_LIBDIR, GHOST returns `<PREFIX>/<BLENDER_INSTALL_LIBDIR>/blender/<major.minor>` as SYSTEM_LIBS. `BLENDER_SYSTEM_RESOURCES` changes SYSTEM, **not SYSTEM_LIBS**. A colocated sealed addon directory is sufficient only when the actual engine's SYSTEM_LIBS points to the corresponding parent root.
2. Otherwise the resolver uses `<dirname(bpy.utils.resource_path('LOCAL'))>/lib/`. LOCAL comes from the program directory and the Blender major/minor resource subdirectory. Merely copying DSOs beside an addon imported from `share/blender-gltf` does not change this fallback.

For a later HAP test, install the exact signed libraries and dependencies in the app's native namespace, provision the sealed addon/resources through the real host paths, and copy byte-identical signed bridge DSOs into the exact loader-resolved layout if required. Current GHOST_SystemPathsOHOS does not override `getSystemLibsDir`, so it inherits no SYSTEM_LIBS directory and the existing resolver takes LOCAL's sibling `lib` route. Do not infer that a Python search-path adjustment changes native namespace access. Record SYSTEM_LIBS/LOCAL from the test report and use `--expect-lib-dir` for the resolved app-private/native layout. Keep fixtures/reports in isolated app-private temporary storage, run Python on the engine-owning thread in a dedicated background acceptance session, and set the scripts' arguments after `--` through the host's real Python entry mechanism. If the HAP host cannot provide that execution/session or path layout, record HAP **NOTRUN** with the concrete missing prerequisite; host native success is not HAP evidence.

Any library re-signing that changes bytes requires rerunning native ABI fixture generation against the exact newly signed library pair before bpy acceptance. Treat the native bridge report, real bpy import/export reports, and HAP runtime report as separate gates. A PASS at one stage never stands in for the others.
