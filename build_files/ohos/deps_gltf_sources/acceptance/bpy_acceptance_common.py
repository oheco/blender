# SPDX-License-Identifier: Apache-2.0
"""Helpers used only inside the final real Blender process, never a bpy substitute."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib
import io
import json
import logging
from pathlib import Path
import struct
import sys
import traceback

from gltf_acceptance_common import (
    SCHEMA, EXTENSIONS, SOURCE_FILES, TOLERANCES, checked_size, compare_geometry,
    cpu_gate, elf_gate, geometry, material, require, sha256_file, unit, unpack_rows, write_json_new,
)


def arguments(stage):
    parser = argparse.ArgumentParser(description=f"Real bpy {stage} compression acceptance, postlink only")
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True, help="new JSON report file")
    parser.add_argument("--expect-addon-root", type=Path, required=True,
                        help="exact sealed io_scene_gltf2 addon directory; mismatched origins fail")
    parser.add_argument("--expect-lib-dir", type=Path, required=True,
                        help="actual bridge directory returned by the unchanged addon dll_path")
    parser.add_argument("--addon-core-dir", type=Path, help="optional sealed addons_core directory added to Python search path")
    parser.add_argument("--expect-machine", choices=("aarch64",), default="aarch64")
    parser.add_argument("--allow-draco-errorcleanup-patch", action="store_true",
                        help="accept only the exact separately recorded candidate loader SHA-256")
    if stage == "export":
        parser.add_argument("--output", type=Path, required=True, help="new or empty compressed export output directory")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(argv)


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def gate(args, report):
    import bpy
    import addon_utils

    report["runtime"] = cpu_gate(args.expect_machine)
    require(bpy.app.background, "run with --background in a dedicated Blender process")
    require(bpy.app.debug_value != 100, "debug_value 100 disables real glTF coordinate conversion")
    report["blender"] = {"version": bpy.app.version_string, "binary": bpy.app.binary_path,
                         "build_hash": bpy.app.build_hash.decode("utf-8", errors="replace"),
                         "background": bpy.app.background}
    require(not args.report.exists(), f"report already exists: {args.report}")
    fixtures = args.fixtures.resolve(strict=True)
    manifest = read_json(fixtures / "fixture-manifest.json")
    require(manifest.get("schema") == SCHEMA, "unknown fixture schema")
    require(manifest["geometry"] == json.loads(json.dumps(geometry())), "fixture expected geometry changed")
    for name, expected in manifest["files"].items():
        path = (fixtures / name).resolve(strict=True)
        require(path.parent == fixtures, "fixture artifact path escapes its directory")
        require(path.stat().st_size == expected["bytes"] and sha256_file(path) == expected["sha256"],
                f"fixture artifact hash mismatch: {name}")
    source = read_json(fixtures / "source-manifest.json")
    require(set(source["files"]) == set(SOURCE_FILES), "fixture source manifest file set changed")
    if args.addon_core_dir:
        core = args.addon_core_dir.resolve(strict=True)
        require(core / "io_scene_gltf2" == args.expect_addon_root.resolve(strict=True),
                "--addon-core-dir must be the parent of --expect-addon-root")
        sys.path.insert(0, str(core))
    addon_utils.enable("io_scene_gltf2", default_set=False, persistent=False)
    addon = importlib.import_module("io_scene_gltf2")
    addon_root = Path(addon.__file__).resolve(strict=True).parent
    require(addon_root == args.expect_addon_root.resolve(strict=True),
            f"source gate: addon loaded from {addon_root}, expected {args.expect_addon_root}")
    require(bpy.ops.import_scene.gltf.get_rna_type().identifier == "IMPORT_SCENE_OT_gltf", "real import operator missing")
    require(bpy.ops.export_scene.gltf.get_rna_type().identifier == "EXPORT_SCENE_OT_gltf", "real export operator missing")
    export_properties = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    for name in ("export_draco_mesh_compression_enable", "export_draco_position_quantization",
                 "export_draco_normal_quantization", "export_draco_texcoord_quantization",
                 "export_meshopt_compression_enable", "export_meshopt_extension"):
        require(name in export_properties, f"real export operator property missing: {name}")
    patched = False
    prefix = "scripts/addons_core/io_scene_gltf2/"
    source_hashes = {}
    candidate_relative = prefix + "blender/imp/draco_compression_extension.py"
    patch_record = None
    if args.allow_draco_errorcleanup_patch:
        patch_record = read_json(Path(__file__).parent / "patches" / "draco-errorcleanup-record.json")
        require(source["files"][candidate_relative] == patch_record["before_sha256"],
                "candidate patch does not target this fixture source baseline")
    for name, digest in source["files"].items():
        if not name.startswith(prefix):
            continue
        installed = addon_root / name[len(prefix):]
        actual_digest = sha256_file(installed)
        if actual_digest != digest:
            require(patch_record is not None and name == candidate_relative and
                    actual_digest == patch_record["after_sha256"], f"installed source hash mismatch: {name}")
            patched = True
        source_hashes[name] = actual_digest
    # Verify the imported loader modules too, so a preloaded module cannot come from another addon.
    for suffix in ("io.com.library", "io.exp.draco", "io.exp.meshopt", "io.imp.gltf2_io_binary_meshopt",
                   "blender.imp.draco_compression_extension"):
        module = importlib.import_module("io_scene_gltf2." + suffix)
        require(Path(module.__file__).resolve(strict=True).is_relative_to(addon_root),
                f"compression module origin mismatch: {suffix}")
    library = importlib.import_module("io_scene_gltf2.io.com.library")
    libraries = {}
    for kind, stem, label in (("draco", "draco", "Draco"), ("meshopt", "meshopt", "MeshOptimizer")):
        path = library.dll_path("bf_intern_" + stem + "_bridge", label)
        require(path is not None, f"addon could not resolve {label} library path")
        actual_path = path.resolve(strict=True)
        expected_path = (args.expect_lib_dir / path.name).resolve(strict=True)
        require(actual_path == expected_path, f"loader path gate: resolved {actual_path}, expected {expected_path}")
        evidence = elf_gate(actual_path, args.expect_machine)
        require(evidence["sha256"] == manifest["libraries"][kind]["sha256"],
                f"postlink library differs from native ABI-tested {kind} binary")
        libraries[kind] = evidence
    report["source_gate"] = {"addon_root": str(addon_root), "installed_hashes": source_hashes,
                             "draco_errorcleanup_candidate_applied": patched,
                             "resource_paths": {name: bpy.utils.resource_path(name) for name in ("SYSTEM_LIBS", "LOCAL")}}
    report["libraries"] = libraries
    report["fixture_manifest_sha256"] = sha256_file(fixtures / "fixture-manifest.json")
    bpy.context.scene.unit_settings.scale_length = 1.0
    return manifest, libraries


def clear_scene():
    import bpy
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for meshes in (bpy.data.meshes, bpy.data.materials):
        for block in list(meshes):
            if block.users == 0:
                meshes.remove(block)


@contextmanager
def capture_errors():
    records = []

    class Recorder(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                records.append(record.getMessage())

    logger = logging.getLogger("glTFImporter_errors")
    handler = Recorder()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def import_operator(path):
    import bpy
    stdout, stderr = io.StringIO(), io.StringIO()
    outcome = {"filepath": str(path), "result": None, "exception": None}
    with capture_errors() as records, redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            result = bpy.ops.import_scene.gltf(filepath=str(path), import_shading="NORMALS", merge_vertices=False,
                                               import_select_created_objects=True)
            outcome["result"] = sorted(result)
        except RuntimeError as error:
            # bpy can raise RuntimeError for an ERROR report even when execute returns CANCELLED.
            outcome["exception"] = str(error)
    outcome["logged_errors"] = list(records)
    outcome["stdout"] = stdout.getvalue()
    outcome["stderr"] = stderr.getvalue()
    return outcome


def require_imported(outcome):
    require(outcome["exception"] is None and outcome["result"] == ["FINISHED"],
            f"real import operator failed: {outcome}")
    require(not outcome["logged_errors"], f"positive import logged an error: {outcome['logged_errors']}")


def nondegenerate_triangles():
    import bpy
    count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        obj.data.calc_loop_triangles()
        for tri in obj.data.loop_triangles:
            a, b, c = [obj.matrix_world @ obj.data.vertices[i].co for i in tri.vertices]
            if (b - a).cross(c - a).length > 1e-8:
                count += 1
    return count


def snapshot_geometry():
    import bpy
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    require(len(meshes) == 1, f"expected one imported mesh object, found {len(meshes)}")
    obj = meshes[0]
    mesh = obj.data
    require(len(mesh.vertices) == 8 and len(mesh.polygons) == 4, "mesh vertex/face counts differ from fixture")
    require(all(len(poly.vertices) == 3 for poly in mesh.polygons), "fixture mesh is not triangular")
    require(mesh.has_custom_normals, "imported custom normal data was discarded")
    require(len(mesh.uv_layers) == 1, "imported UV layer missing or duplicated")
    require(len(mesh.materials) == 1 and mesh.materials[0] is not None, "material slot missing or duplicated")
    require(all(poly.material_index == 0 for poly in mesh.polygons), "material assignment changed")
    mesh.calc_loop_triangles()
    require(len(mesh.corner_normals) == len(mesh.loops), "corner normal count mismatch")
    layer = mesh.uv_layers[0]
    require(len(layer.uv) == len(mesh.loops), "UV corner count mismatch")
    actual = {"POSITION": [], "NORMAL": [], "TEXCOORD_0": [], "indices": []}
    vertex_ids = {}
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    for tri in mesh.loop_triangles:
        for loop_id in tri.loops:
            vertex_id = mesh.loops[loop_id].vertex_index
            normal = normal_matrix @ mesh.corner_normals[loop_id].vector
            normal.normalize()
            uv = tuple(float(x) for x in layer.uv[loop_id].vector)
            key = (vertex_id, tuple(float(x) for x in normal), uv)
            if key not in vertex_ids:
                vertex_ids[key] = len(actual["POSITION"])
                p = obj.matrix_world @ mesh.vertices[vertex_id].co
                # Inverse of the source's glTF -> Blender (x,-z,y); UV source flips v.
                actual["POSITION"].append((float(p.x), float(p.z), -float(p.y)))
                actual["NORMAL"].append(unit((float(normal.x), float(normal.z), -float(normal.y))))
                actual["TEXCOORD_0"].append((uv[0], 1.0 - uv[1]))
            actual["indices"].append(vertex_ids[key])
    require(nondegenerate_triangles() == 4, "import produced degenerate geometry")
    return actual, obj


def check_material(obj):
    expected = material()
    mat = obj.data.materials[0]
    require(mat.name.startswith(expected["name"]), "imported material name changed")
    require(mat.use_nodes and mat.node_tree is not None, "PBR shader nodes missing")
    shaders = [node for node in mat.node_tree.nodes if node.type == "BSDF_PRINCIPLED"]
    require(len(shaders) == 1, "expected exactly one Principled PBR shader")
    shader = shaders[0]
    require(not shader.inputs["Base Color"].is_linked, "unexpected base color texture")
    got = {"baseColorFactor": list(shader.inputs["Base Color"].default_value),
           "metallicFactor": float(shader.inputs["Metallic"].default_value),
           "roughnessFactor": float(shader.inputs["Roughness"].default_value)}
    for name, value in expected["pbrMetallicRoughness"].items():
        error = (max(abs(a - b) for a, b in zip(value, got[name])) if isinstance(value, list)
                 else abs(value - got[name]))
        require(error <= 0.000002, f"material {name} changed: {got[name]}")
    require(not mat.use_backface_culling, "doubleSided material flag changed")
    return {"name": mat.name, "pbrMetallicRoughness": got, "doubleSided": True}


def positive_import(fixtures, kind, tolerance=None):
    clear_scene()
    outcome = import_operator(Path(fixtures) / f"{kind}.gltf")
    require_imported(outcome)
    actual, obj = snapshot_geometry()
    evidence = compare_geometry(actual, geometry(), tolerance or TOLERANCES[kind])
    evidence["operator"] = outcome
    evidence["material"] = check_material(obj)
    return evidence, obj


def negative_imports(fixtures):
    evidence = {}
    cases = (("meshopt-corrupt.gltf", "Meshopt decoding failed"),
             ("unsupported-required.gltf", "is not available on this addon version"),
             ("invalid-json.gltf", "json error"))
    for name, text in cases:
        clear_scene()
        outcome = import_operator(Path(fixtures) / name)
        require(outcome["result"] == ["CANCELLED"] or outcome["exception"] is not None,
                f"invalid fixture accepted by real import operator: {name}")
        messages = "\n".join(outcome["logged_errors"]) + outcome["stderr"] + outcome["stdout"] + (outcome["exception"] or "")
        require(text in messages, f"expected diagnostic absent for {name}: {messages}")
        require(nondegenerate_triangles() == 0, f"invalid fixture created usable geometry: {name}")
        outcome["nondegenerate_triangles"] = 0
        evidence[name] = outcome
    clear_scene()
    outcome = import_operator(Path(fixtures) / "draco-corrupt.gltf")
    require(any("Draco Decoder: Unable to decode" in message for message in outcome["logged_errors"]),
            "corrupt Draco did not reach the actual decoder error path")
    require(nondegenerate_triangles() == 0, "corrupt Draco fabricated usable geometry")
    outcome["nondegenerate_triangles"] = 0
    outcome["source_contract"] = "Draco loader logs ERROR then returns; it may finish with zero-filled degenerate geometry"
    evidence["draco-corrupt.gltf"] = outcome
    clear_scene()
    # A failure must not poison later real imports or cached meshopt decoders.
    recovery, _ = positive_import(fixtures, "meshopt")
    evidence["valid_import_after_errors"] = recovery
    return evidence


def export_operator(path, kind):
    import bpy
    settings = {"filepath": str(path), "export_format": "GLTF_SEPARATE", "use_selection": True,
                "export_normals": True, "export_texcoords": True, "export_materials": "EXPORT",
                "export_yup": True, "export_animations": False,
                "export_draco_mesh_compression_enable": kind == "draco",
                "export_meshopt_compression_enable": kind == "meshopt",
                "export_meshopt_extension": EXTENSIONS["meshopt"]}
    if kind == "draco":
        settings.update(export_draco_mesh_compression_level=6, export_draco_position_quantization=16,
                        export_draco_normal_quantization=12, export_draco_texcoord_quantization=14,
                        export_draco_color_quantization=10, export_draco_generic_quantization=12)
    result = bpy.ops.export_scene.gltf(**settings)
    require(result == {"FINISHED"}, f"real export operator failed: {result}")
    require(Path(path).is_file(), "real exporter did not write the glTF")
    return {"result": sorted(result), "settings": settings}


def decode_export(path, kind, libraries):
    """Independently decode real bpy output through the same actual bridge C ABI."""
    from bridge_abi_acceptance import DracoBridge, MeshoptBridge, load_bridge

    path = Path(path).resolve(strict=True)
    document = read_json(path)
    extension = EXTENSIONS[kind]
    require(extension in document.get("extensionsUsed", []) and extension in document.get("extensionsRequired", []),
            "export silently omitted the requested compression extension")
    require(EXTENSIONS["meshopt" if kind == "draco" else "draco"] not in document.get("extensionsUsed", []),
            "export activated both compression codecs")
    require(len(document["meshes"]) == 1 and len(document["meshes"][0]["primitives"]) == 1,
            "export split the expected single mesh primitive")
    primitive = document["meshes"][0]["primitives"][0]
    require(primitive.get("mode", 4) == 4, "export changed primitive triangle mode")
    require(set(("POSITION", "NORMAL", "TEXCOORD_0")).issubset(primitive["attributes"]), "export lost an attribute")
    require("indices" in primitive and "material" in primitive, "export lost indices or material")
    exported_material = document["materials"][primitive["material"]]
    expected_material = material()
    require(exported_material.get("doubleSided", False), "export lost doubleSided material")
    require(exported_material["name"].startswith(expected_material["name"]), "export lost material name")
    pbr = exported_material["pbrMetallicRoughness"]
    for name, expected in expected_material["pbrMetallicRoughness"].items():
        got = pbr[name]
        error = max(abs(a - b) for a, b in zip(got, expected)) if isinstance(expected, list) else abs(got - expected)
        require(error <= 0.000002, f"exported material {name} changed")
    buffers = {}

    def read_buffer(index):
        if index in buffers:
            return buffers[index]
        info = document["buffers"][index]
        uri = info.get("uri")
        require(isinstance(uri, str) and not uri.startswith("data:") and ":" not in uri,
                "acceptance requires local GLTF_SEPARATE sidecar buffers")
        from urllib.parse import unquote
        binary = (path.parent / unquote(uri)).resolve(strict=True)
        require(binary.is_relative_to(path.parent), "exported buffer path escapes output directory")
        checked_size(info["byteLength"])
        data = binary.read_bytes()
        require(len(data) == info["byteLength"], "exported buffer byteLength mismatch")
        buffers[index] = data
        return data

    library_info = libraries[kind]
    require(library_info["elf_machine"] == 183, "postlink bridge must be AArch64")
    machine = "aarch64"
    lib, loaded = load_bridge(Path(library_info["path"]).parent, kind, machine)
    require(loaded["sha256"] == library_info["sha256"], "bridge changed during bpy acceptance")
    if kind == "draco":
        bridge = DracoBridge(lib)
        ext = primitive["extensions"][extension]
        require(set(("POSITION", "NORMAL", "TEXCOORD_0")).issubset(ext["attributes"]), "Draco extension lost semantic IDs")
        view = document["bufferViews"][ext["bufferView"]]
        buffer = read_buffer(view["buffer"])
        start, length = view.get("byteOffset", 0), view["byteLength"]
        require(0 <= start and length > 0 and start + length <= len(buffer), "Draco stream range exceeds buffer")
        for index in list(primitive["attributes"].values()) + [primitive["indices"]]:
            require("bufferView" not in document["accessors"][index], "Draco export kept an uncompressed accessor fallback")
        index_type = document["accessors"][primitive["indices"]]["componentType"]
        require(index_type in (5123, 5125), "unexpected Draco index component type")
        actual = bridge.decode(buffer[start:start + length], ext["attributes"], index_type)
        require(bridge.created == bridge.released, "postlink independent Draco decode leaked its handle")
        compressed_bytes = length
    else:
        bridge = MeshoptBridge(lib)
        views = {}
        compressed_bytes = 0

        def decoded_view(index):
            nonlocal compressed_bytes
            if index in views:
                return views[index]
            view = document["bufferViews"][index]
            ext = view["extensions"][extension]
            count, stride = ext["count"], ext["byteStride"]
            require(view["byteLength"] == checked_size(count, stride), "meshopt decoded byteLength mismatch")
            fallback = document["buffers"][view["buffer"]]
            require(fallback.get("extensions", {}).get(extension, {}).get("fallback") is True,
                    "meshopt export omitted the fallback buffer marker")
            source = read_buffer(ext["buffer"])
            start, length = ext.get("byteOffset", 0), ext["byteLength"]
            require(start >= 0 and length > 0 and start + length <= len(source), "meshopt compressed stream exceeds buffer")
            compressed_bytes += length
            data = bridge.decode(source[start:start + length], count, stride, ext["mode"], ext.get("filter"))
            views[index] = data
            return data

        actual = {}
        for semantic, width in (("POSITION", 3), ("NORMAL", 3), ("TEXCOORD_0", 2), ("indices", 1)):
            index = primitive["indices"] if semantic == "indices" else primitive["attributes"][semantic]
            accessor = document["accessors"][index]
            view = document["bufferViews"][accessor["bufferView"]]
            data = decoded_view(accessor["bufferView"])
            component = accessor["componentType"]
            require(component in ((5123, 5125) if semantic == "indices" else (5126,)), "unexpected exported component type")
            component_size = 2 if component == 5123 else 4
            stride = view.get("byteStride", width * component_size)
            offset, count = accessor.get("byteOffset", 0), accessor["count"]
            require(offset >= 0 and count > 0 and offset + (count - 1) * stride + width * component_size <= len(data),
                    "exported accessor range exceeds decoded buffer")
            rows = [data[offset + i * stride:offset + i * stride + width * component_size] for i in range(count)]
            if semantic == "indices":
                actual[semantic] = [struct.unpack("<H" if component_size == 2 else "<I", row)[0] for row in rows]
            else:
                actual[semantic] = unpack_rows(b"".join(rows), width)
    require(compressed_bytes > 0, "export contains no compressed data")
    evidence = compare_geometry(actual, geometry(), TOLERANCES["roundtrip"])
    position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
    for side in ("min", "max"):
        require(len(position_accessor[side]) == 3, f"exported position accessor lacks {side} bounds")
        require(max(abs(a - b) for a, b in zip(position_accessor[side], evidence["bounds"][side])) <= 0.006,
                f"exported {side} metadata differs from decoded positions")
    evidence.update({"extension": extension, "compressed_bytes": compressed_bytes,
                     "gltf_sha256": sha256_file(path), "material": exported_material})
    return evidence


def finish(args, stage, runner):
    report = {"schema": SCHEMA, "stage": "bpy-" + stage, "status": "FAIL", "result": "FAIL"}
    result = 1
    try:
        manifest, libraries = gate(args, report)
        runner(args, report, manifest, libraries)
        report["status"] = report["result"] = "PASS"
        result = 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    try:
        write_json_new(args.report, report)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps({"stage": report["stage"], "status": report["status"], "report": str(args.report)}, sort_keys=True))
    return result
