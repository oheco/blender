# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Blender 5.2.2 bpy acceptance; execute ONLY via diagnostic_driver.py.

No replacement bpy/RNA, no skip paths, no golden tessellation counts. Each process
runs one stage. A report marked PASS is provisional until its native process has
also exited with status zero (the driver combines both pieces of evidence).
"""
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback

STAGES = ("model", "reopen", "obj", "glb", "cycles")
REPORT = {"schema": 1, "status": "RUNNING", "checks": [], "unverified": [
    "HAP loader/signing/install", "XComponent/WSI/presentation", "Vulkan GPU",
    "viewport/interactive input/IME", "Cycles GPU", "device loss/lifecycle"]}
ROOT = Path(os.environ["OHOS_ACCEPT_ROOT"]).resolve()
STAGE = os.environ["OHOS_ACCEPT_STAGE"]
OUT = ROOT / "artifacts"
REPORT_PATH = ROOT / "reports" / (STAGE + ".json")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def record(label, data):
    REPORT["checks"].append({"check": label, "evidence": data})
    write_report()
    print("OHOS_ACCEPT_CHECK", STAGE, label, flush=True)


def write_report():
    target = REPORT_PATH.with_suffix(".json.tmp")
    target.write_text(json.dumps(REPORT, indent=2, allow_nan=False), encoding="utf-8")
    target.replace(REPORT_PATH)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def op(idname, **kwargs):
    """get_rna_type is necessary: getattr(bpy.ops, name) alone invents wrappers."""
    group, name = idname.split(".")
    operator = getattr(getattr(bpy.ops, group), name)
    try:
        operator.get_rna_type()
    except Exception as error:
        raise RuntimeError("Required real operator is not registered: " + idname) from error
    require(operator.poll(), "Required operator poll failed: " + idname)
    result = operator(**kwargs)
    require(result == {"FINISHED"}, f"{idname} returned {result}")
    return result


def activate(obj):
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        op("object.mode_set", mode="OBJECT")
    op("object.select_all", action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def reset_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        op("object.mode_set", mode="OBJECT")
    op("object.select_all", action="SELECT")
    op("object.delete", use_global=False)
    require(len(bpy.context.scene.objects) == 0, "Scene reset did not delete objects")


def cube(name, location=(0, 0, 0), scale=(1, 1, 1)):
    op("mesh.primitive_cube_add", size=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    if scale != (1, 1, 1):
        op("transform.resize", value=scale)
        op("object.transform_apply", location=False, rotation=False, scale=True)
    return obj


def mesh_summary(mesh, matrix=None):
    mesh.calc_loop_triangles()
    coords = [tuple(matrix @ vertex.co) if matrix else tuple(vertex.co)
              for vertex in mesh.vertices]
    require(coords and mesh.loop_triangles, "Empty mesh/triangles")
    require(all(math.isfinite(value) for xyz in coords for value in xyz), "Nonfinite vertex")
    area = volume = 0.0
    for triangle in mesh.loop_triangles:
        a, b, c = [Vector(coords[index]) for index in triangle.vertices]
        area += (b - a).cross(c - a).length * 0.5
        volume += a.dot(b.cross(c)) / 6.0
    require(math.isfinite(area) and area > 0, "Zero or nonfinite mesh surface")
    return {"vertices": len(coords), "edges": len(mesh.edges),
            "polygons": len(mesh.polygons), "triangles": len(mesh.loop_triangles),
            "bounds": [[min(x[i] for x in coords) for i in range(3)],
                       [max(x[i] for x in coords) for i in range(3)]],
            "area": area, "volume": abs(volume)}


def evaluated_summary(obj):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    require(mesh is not None, "Evaluated object returned no mesh: " + obj.name)
    try:
        return mesh_summary(mesh)
    finally:
        evaluated.to_mesh_clear()


def uv_summary(mesh):
    require(mesh.uv_layers.active is not None, "Missing active UV layer")
    uv = [list(item.vector) for item in mesh.uv_layers.active.uv]
    require(len(uv) == len(mesh.loops) and len(uv) > 0, "Incomplete UV face corners")
    require(all(math.isfinite(x) and -1e-4 <= x <= 1.0001 for p in uv for x in p),
            "UV projection escaped finite unit bounds")
    areas = []
    for polygon in mesh.polygons:
        points = [uv[index] for index in polygon.loop_indices]
        twice = sum(a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(points, points[1:] + points[:1]))
        areas.append(abs(twice) * 0.5)
    require(all(area > 1e-9 for area in areas), "Degenerate UV polygon")
    return {"name": mesh.uv_layers.active.name, "corners": uv,
            "polygon_areas": areas}


def near(actual, expected, tolerance=1e-5, where="value"):
    """Compare serialized semantics with numerical tolerance; report first mismatch."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), where + " keys differ")
        for key in expected:
            near(actual[key], expected[key], tolerance, where + "." + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), where + " length differs")
        for i, value in enumerate(expected):
            near(actual[i], value, tolerance, f"{where}[{i}]")
    elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
        require(isinstance(actual, (float, int)) and math.isfinite(actual) and
                math.isclose(actual, expected, abs_tol=tolerance, rel_tol=tolerance),
                f"{where}: {actual!r} != {expected!r}")
    else:
        require(actual == expected, f"{where}: {actual!r} != {expected!r}")


def image_evidence(image):
    pixels = list(image.pixels[:])
    require(len(pixels) == image.size[0] * image.size[1] * 4 and pixels, "Missing RGBA pixels")
    require(all(math.isfinite(x) for x in pixels), "Nonfinite texture pixels")
    rgb = [pixels[i] for i in range(len(pixels)) if i % 4 != 3]
    require(max(rgb) - min(rgb) > 0.1, "Texture has no color variation")
    return {"name": image.name, "size": list(image.size), "pixels": pixels,
            "packed_bytes": image.packed_file.size if image.packed_file else 0}


def make_material():
    texture = bpy.data.images.new("GameChecker", width=8, height=8, alpha=True)
    pixels = []
    for y in range(8):
        for x in range(8):
            pixels.extend((0.8, 0.15, 0.04, 1.0) if (x // 2 + y // 2) % 2
                          else (0.05, 0.4, 0.8, 1.0))
    texture.pixels.foreach_set(pixels)
    texture.update()
    texture.filepath_raw = str(OUT / "checker.png")
    texture.file_format = "PNG"
    texture.save()
    require(Path(texture.filepath_raw).stat().st_size > 64, "PNG writer produced no texture")
    # Reload the actual encoded PNG so packed data and pixel comparisons share its color conversion.
    bpy.data.images.remove(texture)
    texture = bpy.data.images.load(str(OUT / "checker.png"), check_existing=False)
    texture.name = "GameChecker"
    texture.pack()
    require(texture.packed_file and texture.packed_file.size > 64, "Texture not packed")
    material = bpy.data.materials.new("GameMaterial")
    material.use_nodes = True
    material.diffuse_color = (0.8, 0.15, 0.04, 1.0)
    material.node_tree.nodes.clear()
    shader = material.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    shader.name = "GameShader"
    shader.inputs["Roughness"].default_value = 0.65
    shader.inputs["Metallic"].default_value = 0.1
    output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "GameOutput"
    sampler = material.node_tree.nodes.new("ShaderNodeTexImage")
    sampler.name = "GameTexture"
    sampler.image = texture
    material.node_tree.links.new(sampler.outputs["Color"], shader.inputs["Base Color"])
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    record("material_texture_encoded_and_packed", image_evidence(texture))
    return material


def make_model():
    reset_scene()
    asset = cube("GameProp", scale=(1.5, 1.0, 0.25))
    before = mesh_summary(asset.data)
    op("object.mode_set", mode="EDIT")
    op("mesh.select_mode", type="FACE")
    op("mesh.select_all", action="DESELECT")
    bm = bmesh.from_edit_mesh(asset.data)
    bm.normal_update()
    top = max(bm.faces, key=lambda face: face.calc_center_median().z)
    require(top.normal.z > 0.9, "Could not select prop top face")
    top.select_set(True)
    bm.faces.active = top
    bmesh.update_edit_mesh(asset.data)
    op("mesh.inset", thickness=0.2, depth=0.0)
    op("mesh.extrude_region_move", TRANSFORM_OT_translate={"value": (0.0, 0.0, 0.5)})
    op("mesh.select_all", action="SELECT")
    op("uv.smart_project", angle_limit=math.radians(66), island_margin=0.03)
    op("object.mode_set", mode="OBJECT")
    after = mesh_summary(asset.data)
    require(after["vertices"] > before["vertices"] and after["polygons"] > before["polygons"],
            "Inset/extrusion did not add topology")
    require(after["bounds"][1][2] > before["bounds"][1][2] + 0.4,
            "Extrusion did not move prop top")
    require(after["volume"] > before["volume"], "Extrusion did not add enclosed volume")
    asset.data.uv_layers.active.name = "GameUV"
    record("game_prop_scale_inset_extrude_uv", {"before": before, "after": after,
                                                "uv": uv_summary(asset.data)})
    bevel = asset.modifiers.new("PropBevel", "BEVEL")
    bevel.width = 0.06
    bevel.segments = 2
    bevel.limit_method = "ANGLE"
    prop_eval = evaluated_summary(asset)
    require(prop_eval["polygons"] > after["polygons"], "Prop bevel had no evaluated effect")
    asset.data.materials.append(make_material())
    activate(asset)
    op("object.duplicate", linked=False)
    duplicate = bpy.context.object
    duplicate.name = "GamePropDuplicate"
    op("transform.translate", value=(4.0, 0.0, 0.0))
    require(duplicate.data != asset.data, "Unlinked duplicate shares mesh data")
    require(abs(duplicate.location.x - asset.location.x - 4.0) < 1e-5,
            "Duplicate translate had no effect")
    record("duplicate_independent_mesh", {"object": duplicate.name,
                                          "location": list(duplicate.location)})

    bevel_obj = cube("BevelProbe", location=(-4.0, 0.0, 0.0))
    baseline = mesh_summary(bevel_obj.data)
    mod = bevel_obj.modifiers.new("RealBevel", "BEVEL")
    mod.width, mod.segments, mod.limit_method = 0.2, 3, "NONE"
    result = evaluated_summary(bevel_obj)
    require(result["polygons"] > baseline["polygons"] and 4 < result["volume"] < 8,
            "Bevel did not round corners in evaluated geometry")
    record("bevel_dependency_graph", {"before": baseline, "evaluated": result})

    subdiv = cube("SubdivProbe", location=(-4.0, 3.0, 0.0))
    baseline = mesh_summary(subdiv.data)
    mod = subdiv.modifiers.new("RealSubdiv", "SUBSURF")
    mod.subdivision_type, mod.levels, mod.render_levels = "CATMULL_CLARK", 2, 2
    result = evaluated_summary(subdiv)
    require(result["vertices"] > baseline["vertices"] and
            result["polygons"] > baseline["polygons"] and 0 < result["volume"] < 8,
            "Subdivision did not refine/round evaluated geometry")
    record("subdiv_dependency_graph", {"before": baseline, "evaluated": result})

    boolean = cube("BooleanProbe", location=(0.0, 4.0, 0.0))
    cutter = cube("BooleanCutter", location=(0.75, 4.0, 0.0), scale=(0.5, 2.0, 2.0))
    cutter.hide_render = True
    cutter.display_type = "WIRE"
    baseline = mesh_summary(boolean.data)
    mod = boolean.modifiers.new("RealBoolean", "BOOLEAN")
    mod.operation, mod.solver, mod.object = "DIFFERENCE", "EXACT", cutter
    result = evaluated_summary(boolean)
    # Analytic box difference: width 1.25, depth/height 2 => volume 5.
    require(math.isclose(result["volume"], 5.0, abs_tol=1e-3),
            "Exact Boolean did not remove the known overlapping box volume")
    near(result["bounds"], [[-1, -1, -1], [0.25, 1, 1]], tolerance=1e-4)
    record("boolean_dependency_graph", {"before": baseline, "evaluated": result})

    geo = cube("GeometryNodesProbe", location=(4.0, 3.0, 0.0))
    tree = bpy.data.node_groups.new("GameTransformGeometry", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    node_in = tree.nodes.new("NodeGroupInput")
    node_out = tree.nodes.new("NodeGroupOutput")
    transform = tree.nodes.new("GeometryNodeTransform")  # exact 5.2 source idname
    transform.name = "GameTransform"
    transform.inputs["Translation"].default_value = (0.25, -0.5, 0.75)
    transform.inputs["Scale"].default_value = (1.5, 0.5, 2.0)
    tree.links.new(node_in.outputs["Geometry"], transform.inputs["Geometry"])
    tree.links.new(transform.outputs["Geometry"], node_out.inputs["Geometry"])
    mod = geo.modifiers.new("RealGeometryNodes", "NODES")
    mod.node_group = tree
    result = evaluated_summary(geo)
    near(result["bounds"], [[-1.25, -1.0, -1.25], [1.75, 0.0, 2.75]])
    require(math.isclose(result["volume"], 12.0, abs_tol=1e-4),
            "GeometryNodes transform did not evaluate scale")
    record("geometry_nodes_dependency_graph", result)

    for obj in bpy.context.scene.objects:
        obj["ohos_acceptance_asset"] = True
    snapshot = scene_snapshot()
    blend = OUT / "game asset.blend"
    op("wm.save_as_mainfile", filepath=str(blend), check_existing=False)
    require(blend.is_file() and blend.stat().st_size > 1024, "Blend writer produced no project")
    (OUT / "scene-before.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False),
                                            encoding="utf-8")
    # Cross-process reopen is a separate mandatory stage; this local reopen also exercises WM reload.
    op("wm.open_mainfile", filepath=str(blend))
    near(scene_snapshot(), snapshot)
    record("save_reopen_same_process", {"blend": str(blend), "sha256": sha256(blend),
                                         "bytes": blend.stat().st_size})


def modifier_snapshot(mod):
    fields = {"BEVEL": ("width", "segments", "limit_method"),
              "SUBSURF": ("levels", "render_levels", "subdivision_type"),
              "BOOLEAN": ("operation", "solver"), "NODES": ()}[mod.type]
    result = {"name": mod.name, "type": mod.type}
    for name in fields:
        result[name] = getattr(mod, name)
    if mod.type == "BOOLEAN":
        result["object"] = mod.object.name
    if mod.type == "NODES":
        tree = mod.node_group
        transform = tree.nodes["GameTransform"]
        result["tree"] = {"name": tree.name,
                          "nodes": sorted(node.bl_idname for node in tree.nodes),
                          "links": sorted([link.from_node.bl_idname, link.from_socket.name,
                                           link.to_node.bl_idname, link.to_socket.name]
                                          for link in tree.links),
                          "translation": list(transform.inputs["Translation"].default_value),
                          "scale": list(transform.inputs["Scale"].default_value)}
    return result


def scene_snapshot():
    result = {"objects": {}, "material": {}, "image": {}}
    for obj in sorted(bpy.context.scene.objects, key=lambda value: value.name):
        require(obj.get("ohos_acceptance_asset") is True or obj.get("ohos_acceptance_asset") == 1,
                "Unexpected object or lost ID property: " + obj.name)
        mesh = obj.data
        item = {"type": obj.type, "location": list(obj.location),
                "rotation": list(obj.rotation_euler), "scale": list(obj.scale),
                "hide_render": obj.hide_render,
                "mesh": mesh_summary(mesh), "evaluated": evaluated_summary(obj),
                "coordinates": [list(vertex.co) for vertex in mesh.vertices],
                "faces": [list(polygon.vertices) for polygon in mesh.polygons],
                "materials": [mat.name for mat in mesh.materials],
                "modifiers": [modifier_snapshot(mod) for mod in obj.modifiers]}
        if obj.name in {"GameProp", "GamePropDuplicate"}:
            item["uv"] = uv_summary(mesh)
        result["objects"][obj.name] = item
    material = bpy.data.materials["GameMaterial"]
    nodes = material.node_tree.nodes
    result["material"] = {"name": material.name, "use_nodes": material.use_nodes,
                          "nodes": sorted(node.bl_idname for node in nodes),
                          "links": sorted([link.from_node.name, link.from_socket.name,
                                           link.to_node.name, link.to_socket.name]
                                          for link in material.node_tree.links),
                          "roughness": nodes["GameShader"].inputs["Roughness"].default_value,
                          "metallic": nodes["GameShader"].inputs["Metallic"].default_value,
                          "texture": nodes["GameTexture"].image.name}
    result["image"] = image_evidence(bpy.data.images["GameChecker"])
    require(result["image"]["packed_bytes"] > 64, "Reopen lost packed texture")
    return result


def reopen():
    blend = OUT / "game asset.blend"
    expected = json.loads((OUT / "scene-before.json").read_text(encoding="utf-8"))
    op("wm.open_mainfile", filepath=str(blend))
    actual = scene_snapshot()
    near(actual, expected)
    (OUT / "scene-after.json").write_text(json.dumps(actual, indent=2, allow_nan=False),
                                           encoding="utf-8")
    record("cross_process_project_reopen", {"blend_sha256": sha256(blend),
                                            "objects": sorted(actual["objects"]),
                                            "compared": "topology/geometry/transforms/UV/modifiers/nodes/material/packed pixels"})


def enable_builtin_addon(name):
    # Only staged Blender scripts are searched. Preferences stay in this private process.
    # glTF/Cycles register() access their addon preferences; ensure that in-memory entry.
    import addon_utils
    module = sys.modules.get(name) if addon_utils.check(name)[1] else None
    if module is None:
        module = addon_utils.enable(name, default_set=True, persistent=True)
    require(module is not None and addon_utils.check(name)[1], "Could not register bundled add-on: " + name)
    require(Path(module.__file__).resolve().is_relative_to(ROOT / "runtime"),
            "Add-on came from outside isolated runtime")
    record("addon_registered_" + name, {"path": module.__file__})


def frozen_export_asset():
    op("wm.open_mainfile", filepath=str(OUT / "game asset.blend"))
    obj = bpy.data.objects["GameProp"]
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph),
                                          preserve_all_data_layers=True, depsgraph=depsgraph)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    frozen = bpy.data.objects.new("RoundtripAsset", mesh)
    bpy.context.collection.objects.link(frozen)
    frozen.matrix_world = obj.matrix_world.copy()
    require(mesh.uv_layers.active is not None, "Evaluated export lost UV")
    require(mesh.materials and mesh.materials[0].name == "GameMaterial", "Export lost material")
    activate(frozen)
    return frozen


def triangles(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    layer = mesh.uv_layers.active
    require(layer is not None and len(layer.uv) == len(mesh.loops), "Roundtrip lost UV")
    result = []
    for triangle in mesh.loop_triangles:
        points = []
        for vertex_index, loop_index in zip(triangle.vertices, triangle.loops):
            co = obj.matrix_world @ mesh.vertices[vertex_index].co
            points.append(list(co) + list(layer.uv[loop_index].vector))
        result.append(sorted(points, key=lambda point: tuple(point[:3])))
    return result


def compare_roundtrip(original, imported):
    expected = triangles(original)
    actual = [triangle for obj in imported for triangle in triangles(obj)]
    require(len(actual) == len(expected) and expected, "Roundtrip changed triangle count")
    # UV seams can split vertices, face/vertex order can change; match triangle geometry + UV.
    remaining = list(actual)
    for triangle in expected:
        match = next((i for i, other in enumerate(remaining)
                      if any(all(abs(x - y) <= 2e-4 for a, b in zip(triangle, permutation)
                                 for x, y in zip(a, b))
                             for permutation in itertools.permutations(other))), None)
        require(match is not None, "Roundtrip changed a world-space triangle or its UV corners")
        remaining.pop(match)
    before = mesh_summary(original.data, original.matrix_world)
    after = [mesh_summary(obj.data, obj.matrix_world) for obj in imported]
    require(math.isclose(sum(item["area"] for item in after), before["area"], rel_tol=2e-4),
            "Roundtrip surface area differs")
    return {"triangle_count": len(expected), "before": before, "imported": after,
            "triangle_position_uv_tolerance": 2e-4}


def imported_meshes(before):
    meshes = [obj for obj in bpy.context.scene.objects if obj.name not in before and obj.type == "MESH"]
    require(len(meshes) == 1, "Expected one imported mesh, got " + str([obj.name for obj in meshes]))
    return meshes


def roundtrip_obj():
    asset = frozen_export_asset()
    folder = OUT / "obj roundtrip"
    folder.mkdir()
    target = folder / "game prop.obj"
    op("wm.obj_export", filepath=str(target), check_existing=False, export_selected_objects=True,
       apply_modifiers=True, export_uv=True, export_normals=True, export_materials=False,
       export_triangulated_mesh=True)
    require(sorted(path.name for path in folder.iterdir()) == [target.name], "OBJ is not single file")
    text = target.read_text(encoding="utf-8")
    require("\nmtllib " not in "\n" + text, "Single-file OBJ references material sidecar")
    require(any(line.startswith("v ") for line in text.splitlines()) and
            any(line.startswith("vt ") for line in text.splitlines()) and
            any(line.startswith("f ") for line in text.splitlines()), "OBJ lacks geometry/UV")
    names = {obj.name for obj in bpy.context.scene.objects}
    op("wm.obj_import", filepath=str(target))
    imported = imported_meshes(names)
    result = compare_roundtrip(asset, imported)
    result.update({"file": str(target), "sha256": sha256(target), "bytes": target.stat().st_size,
                   "materials": "OBJ intentionally exports geometry/UV only for single-file contract"})
    record("obj_single_file_operator_roundtrip", result)


def inspect_glb(path):
    content = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", content, 0)
    require(magic == b"glTF" and version == 2 and length == len(content), "Invalid GLB header")
    chunks = []
    cursor = 12
    while cursor < length:
        require(cursor + 8 <= length, "Truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", content, cursor)
        cursor += 8
        require(size % 4 == 0 and cursor + size <= length, "Invalid GLB chunk size")
        chunks.append((kind, content[cursor:cursor + size]))
        cursor += size
    require(len(chunks) == 2 and chunks[0][0] == 0x4E4F534A and chunks[1][0] == 0x004E4942,
            "GLB lacks JSON + embedded BIN")
    document = json.loads(chunks[0][1])
    require(document.get("meshes") and document.get("materials") and document.get("images"),
            "GLB lacks mesh/material/image")
    require(all("uri" not in buffer for buffer in document.get("buffers", [])), "External GLB buffer")
    require(all("bufferView" in image and "uri" not in image for image in document["images"]),
            "GLB texture is not embedded")
    require(all("TEXCOORD_0" in primitive["attributes"]
                for mesh in document["meshes"] for primitive in mesh["primitives"]), "GLB lacks UV")
    return {"embedded_bin_bytes": len(chunks[1][1]), "meshes": len(document["meshes"]),
            "materials": len(document["materials"]), "images": len(document["images"])}


def roundtrip_glb():
    enable_builtin_addon("io_scene_gltf2")
    asset = frozen_export_asset()
    folder = OUT / "glb roundtrip"
    folder.mkdir()
    target = folder / "game prop.glb"
    op("export_scene.gltf", filepath=str(target), check_existing=False, export_format="GLB",
       use_selection=True, export_materials="EXPORT", export_image_format="AUTO")
    require(sorted(path.name for path in folder.iterdir()) == [target.name], "GLB is not single file")
    result = inspect_glb(target)
    names = {obj.name for obj in bpy.context.scene.objects}
    op("import_scene.gltf", filepath=str(target), import_pack_images=True)
    imported = imported_meshes(names)
    result["geometry_uv"] = compare_roundtrip(asset, imported)
    require(imported[0].data.materials, "GLB import lost material")
    material = imported[0].data.materials[0]
    require(material.use_nodes, "GLB import lost node material")
    textures = [node.image for node in material.node_tree.nodes
                if node.bl_idname == "ShaderNodeTexImage" and node.image]
    require(textures, "GLB import lost texture binding")
    result["imported_texture"] = image_evidence(textures[0])
    require(tuple(textures[0].size) == (8, 8) and textures[0].packed_file, "GLB texture not packed 8x8")
    near(result["imported_texture"]["pixels"], image_evidence(bpy.data.images["GameChecker"])["pixels"],
         tolerance=3e-3, where="glb_texture_pixels")
    shaders = [node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"]
    require(len(shaders) == 1 and shaders[0].inputs["Base Color"].is_linked,
            "GLB import lost Principled base-color texture link")
    near(shaders[0].inputs["Roughness"].default_value, 0.65, where="glb_roughness")
    near(shaders[0].inputs["Metallic"].default_value, 0.1, where="glb_metallic")
    result.update({"file": str(target), "sha256": sha256(target), "bytes": target.stat().st_size})
    record("glb_single_file_operator_roundtrip", result)


def cycles_render():
    enable_builtin_addon("cycles")
    require(bpy.app.build_options.cycles, "Build has no real Cycles")
    reset_scene()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.seed = 17
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.threads_mode, scene.render.threads = "FIXED", 2
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.filepath = str(OUT / "cycles cpu.png")
    world = bpy.data.worlds.new("AcceptanceWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.025, 0.025, 0.025, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.2
    scene.world = world
    obj = cube("RenderGameProp", location=(0, 0, 0.6), scale=(0.65, 0.65, 0.6))
    obj.data.materials.append(make_material())
    op("mesh.primitive_plane_add", size=200, location=(0, 0, 0))
    floor = bpy.data.materials.new("AcceptanceFloor")
    floor.use_nodes = True
    floor.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.2, 0.2, 0.2, 1)
    floor.node_tree.nodes.get("Principled BSDF").inputs["Roughness"].default_value = 0.8
    bpy.context.object.data.materials.append(floor)
    light = bpy.data.lights.new("AcceptanceKey", type="AREA")
    light.energy, light.size = 500, 3
    light_obj = bpy.data.objects.new("AcceptanceKey", light)
    scene.collection.objects.link(light_obj)
    light_obj.location = (1, -3, 5)
    light_obj.rotation_euler = (Vector((0, 0, 0.5)) - light_obj.location).to_track_quat("-Z", "Y").to_euler()
    camera = bpy.data.cameras.new("AcceptanceCamera")
    camera.type, camera.ortho_scale = "ORTHO", 4.5
    camera_obj = bpy.data.objects.new("AcceptanceCamera", camera)
    scene.collection.objects.link(camera_obj)
    camera_obj.location = (4, -6, 4)
    camera_obj.rotation_euler = (Vector((0, 0, 0.5)) - camera_obj.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera_obj
    op("wm.save_as_mainfile", filepath=str(OUT / "cycles scene.blend"), check_existing=False)
    started = time.monotonic()
    op("render.render", write_still=True)
    target = Path(scene.render.filepath)
    require(target.is_file() and target.stat().st_size > 128, "Cycles produced no encoded render")
    with target.open("rb") as stream:
        header = stream.read(24)
    require(header[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack_from(">II", header, 16) == (64, 64),
            "Render PNG dimensions/header wrong")
    # Render Result's transient pixel API varies; reopen the written result through real Blender I/O.
    rendered = bpy.data.images.load(str(target), check_existing=False)
    require(tuple(rendered.size) == (64, 64), "Decoded render dimensions wrong")
    pixels = list(rendered.pixels[:])
    require(len(pixels) == 64 * 64 * 4 and all(math.isfinite(x) for x in pixels),
            "Render has missing/nonfinite RGBA pixels")
    luminance = [sum(pixels[i:i + 3]) / 3 for i in range(0, len(pixels), 4)]
    alpha = pixels[3::4]
    require(min(alpha) > 0.99, "Opaque render has empty alpha")
    require(sum(x > 0.01 for x in luminance) > len(luminance) // 5, "Render mostly empty/black")
    require(max(luminance) - min(luminance) > 0.05, "Render has no scene variation")
    mean = sum(luminance) / len(luminance)
    variance = sum((x - mean) ** 2 for x in luminance) / len(luminance)
    require(variance > 1e-4, "Render pixel variance is too small")
    record("cycles_cpu_encoded_render_and_pixels", {
        "engine": scene.render.engine, "device": scene.cycles.device,
        "samples": scene.cycles.samples, "threads": scene.render.threads,
        "file": str(target), "sha256": sha256(target), "bytes": target.stat().st_size,
        "decoded_size": list(rendered.size), "rgba_values": len(pixels),
        "luminance_min": min(luminance), "luminance_max": max(luminance),
        "luminance_mean": mean, "luminance_variance": variance,
        "opaque_pixels": sum(a > 0.99 for a in alpha), "seconds": time.monotonic() - started})


def runtime_checks():
    require(ROOT.is_absolute() and OUT.is_dir() and REPORT_PATH.parent.is_dir(), "Missing private output")
    require(STAGE in STAGES, "Unknown acceptance stage")
    require(tuple(bpy.app.version) == (5, 2, 2), "Not Blender 5.2.2: " + bpy.app.version_string)
    require(bpy.app.background, "Diagnostic unexpectedly has a foreground window")
    require(sys.version_info[:3] == (3, 13, 13), "Not accepted shared Python 3.13.13")
    require(sys.executable is None, "Embedded Python probes a process executable")
    require(sys.flags.isolated and not sys.flags.use_environment and sys.flags.no_user_site,
            "Python is not isolated")
    require(Path(sys.prefix).resolve() == ROOT / "runtime/5.2/python", "Wrong Python prefix")
    require(Path(tempfile.gettempdir()).resolve() == ROOT / "sessions" / STAGE / "temp",
            "Python tempfile escaped the private session")
    require(Path(sys.pycache_prefix).resolve().is_relative_to(ROOT / "sessions" / STAGE / "cache"),
            "Python pycache escaped the private session")
    require(Path(bpy.utils.user_resource("CONFIG")).resolve().is_relative_to(ROOT / "sessions" / STAGE / "config"),
            "Blender config escaped the private session")
    import numpy as np
    import requests
    require(Path(np.__file__).resolve().is_relative_to(ROOT / "runtime/5.2/python"), "NumPy from external prefix")
    require(Path(requests.__file__).resolve().is_relative_to(ROOT / "runtime/5.2/python"), "requests from external prefix")
    matrix = np.array([[3., 1.], [1., 2.]])
    inverse = np.linalg.inv(matrix)
    require(np.allclose(matrix @ inverse, np.eye(2)), "Native NumPy linalg failed")
    prepared = requests.Request("GET", "https://example.invalid/model", params={"stage": STAGE}).prepare()
    require("stage=" + STAGE in prepared.url, "requests local preparation failed")
    record("runtime_real_bpy_python_numpy_requests", {
        "blender": bpy.app.version_string, "blender_build_hash": bpy.app.build_hash.decode("ascii", "replace"),
        "python": sys.version, "prefix": sys.prefix, "sys_path": sys.path,
        "isolated": sys.flags.isolated, "tempfile": tempfile.gettempdir(),
        "pycache": sys.pycache_prefix, "numpy": np.__version__, "numpy_file": np.__file__,
        "numpy_inverse": inverse.tolist(), "requests": requests.__version__, "requests_file": requests.__file__,
        "background": bpy.app.background})


def main():
    global bpy, bmesh, Vector
    REPORT.update({"stage": STAGE, "pid": os.getpid(), "root": str(ROOT),
                   "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    write_report()
    started = time.monotonic()
    try:
        import bpy
        import bmesh
        from mathutils import Vector
        runtime_checks()
        {"model": make_model, "reopen": reopen, "obj": roundtrip_obj,
         "glb": roundtrip_glb, "cycles": cycles_render}[STAGE]()
        REPORT["status"] = "PASS"
    except BaseException:
        REPORT["status"] = "FAIL"
        REPORT["traceback"] = traceback.format_exc()
        raise
    finally:
        REPORT["seconds"] = time.monotonic() - started
        write_report()
        print("OHOS_ACCEPT_STAGE", STAGE, REPORT["status"], str(REPORT_PATH), flush=True)


if __name__ == "__main__":
    main()
