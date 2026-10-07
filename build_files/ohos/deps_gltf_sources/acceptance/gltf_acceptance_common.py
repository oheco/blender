# SPDX-License-Identifier: Apache-2.0
"""Standard-library-only geometry, source, ELF and report checks shared by acceptance."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import struct
import sys

SCHEMA = "ohos-gltf-compression-acceptance-v1"
MAX_ALLOCATION = 16 * 1024 * 1024
EXTENSIONS = {"draco": "KHR_draco_mesh_compression", "meshopt": "EXT_meshopt_compression"}
TOLERANCES = {
    "draco": {"POSITION": 0.00035, "NORMAL": 0.0015, "TEXCOORD_0": 0.00025},
    "meshopt": {"POSITION": 0.0025, "NORMAL": 0.0025, "TEXCOORD_0": 0.000002},
    # Two lossy passes: generated fixture import followed by compressed bpy export.
    "roundtrip": {"POSITION": 0.006, "NORMAL": 0.006, "TEXCOORD_0": 0.0006},
}
SOURCE_FILES = (
    "scripts/addons_core/io_scene_gltf2/__init__.py",
    "scripts/addons_core/io_scene_gltf2/io/com/library.py",
    "scripts/addons_core/io_scene_gltf2/io/exp/draco.py",
    "scripts/addons_core/io_scene_gltf2/io/exp/meshopt.py",
    "scripts/addons_core/io_scene_gltf2/io/exp/buffer.py",
    "scripts/addons_core/io_scene_gltf2/io/imp/gltf2_io_binary_meshopt.py",
    "scripts/addons_core/io_scene_gltf2/io/imp/gltf2_io_gltf.py",
    "scripts/addons_core/io_scene_gltf2/io/imp/gltf2_io_binary.py",
    "scripts/addons_core/io_scene_gltf2/io/com/debug.py",
    "scripts/addons_core/io_scene_gltf2/blender/imp/blender_gltf.py",
    "scripts/addons_core/io_scene_gltf2/blender/exp/exporter.py",
    "scripts/addons_core/io_scene_gltf2/blender/imp/draco_compression_extension.py",
    "scripts/addons_core/io_scene_gltf2/blender/imp/mesh.py",
    "intern/draco_bridge/intern/encoder.h",
    "intern/draco_bridge/intern/encoder.cpp",
    "intern/draco_bridge/intern/decoder.h",
    "intern/draco_bridge/intern/decoder.cpp",
    "intern/meshoptimizer_bridge/intern/encoder.h",
    "intern/meshoptimizer_bridge/intern/encoder.cpp",
    "intern/meshoptimizer_bridge/intern/decoder.h",
    "intern/meshoptimizer_bridge/intern/decoder.cpp",
    "source/blender/blenkernel/intern/appdir.cc",
    "source/blender/python/intern/bpy.cc",
    "intern/ghost/intern/GHOST_SystemPathsUnix.cc",
    "intern/ghost/intern/GHOST_SystemPathsOHOS.hh",
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_size(count, stride=1):
    require(isinstance(count, int) and isinstance(stride, int), "allocation dimensions must be integers")
    require(count >= 0 and stride > 0, "negative count or invalid stride")
    size = count * stride
    require(size <= MAX_ALLOCATION, f"allocation exceeds {MAX_ALLOCATION} bytes: {size}")
    return size


def cpu_gate(expected):
    host = os.uname()
    require(expected == "aarch64", "only the native HarmonyOS/aarch64 acceptance profile is permitted")
    require(host.sysname == "HarmonyOS" and host.machine == "aarch64",
            f"native host gate: expected HarmonyOS/aarch64, actual {host.sysname}/{host.machine}")
    require(platform.machine().lower() == "aarch64", "Python process CPU disagrees with native host")
    require(sys.byteorder == "little", "acceptance fixtures and ABI require little endian")
    require(struct.calcsize("P") == 8, "acceptance requires a 64-bit process")
    return {"machine": host.machine, "system": host.sysname, "release": host.release,
            "platform": sys.platform, "python": sys.version, "pointer_bytes": 8}


def elf_gate(path, machine):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        header = stream.read(64)
    require(len(header) == 64 and header[:4] == b"\x7fELF", f"not an ELF library: {path}")
    require(header[4:7] == b"\x02\x01\x01", f"expected ELF64 little endian v1: {path}")
    elf_type, elf_machine = struct.unpack_from("<HH", header, 16)
    require(machine == "aarch64", "only native AArch64 bridge libraries are permitted")
    require(elf_type == 3 and elf_machine == 183, f"ELF gate failed: type={elf_type}, machine={elf_machine}: {path}")
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "elf_machine": elf_machine, "elf_type": elf_type}


def source_manifest(root):
    root = Path(root).resolve(strict=True)
    return {"root": str(root), "files": {name: sha256_file(root / name) for name in SOURCE_FILES}}


def empty_output_directory(path):
    path = Path(path).absolute()
    require(not path.is_symlink(), f"output directory is a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    require(not any(path.iterdir()), f"output directory must be empty: {path}")
    return path.resolve()


def write_json_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def unit(vector):
    length = math.sqrt(sum(float(x) ** 2 for x in vector))
    require(length > 0 and math.isfinite(length), "invalid normal vector")
    return tuple(float(x) / length for x in vector)


def geometry():
    """Warped indexed patch with a UV seam, shared indices and deliberately custom normals."""
    positions = [
        (-1.25, -0.90, -0.25), (0.10, -0.90, 0.12), (-1.25, 1.10, 0.22),
        (0.10, 1.10, 0.48), (1.70, -0.90, -0.18), (1.70, 1.10, 0.31),
        (0.10, -0.90, 0.12), (0.10, 1.10, 0.48),
    ]
    normals = [unit((0.12 + x * 0.025, -0.08 + y * 0.02, 1.0)) for x, y, _ in positions]
    uvs = [(0.05, 0.10), (0.45, 0.10), (0.05, 0.90), (0.45, 0.90),
           (0.95, 0.20), (0.95, 0.80), (0.55, 0.20), (0.55, 0.80)]
    return {"POSITION": positions, "NORMAL": normals, "TEXCOORD_0": uvs,
            "indices": [0, 1, 2, 2, 1, 3, 6, 4, 7, 7, 4, 5]}


def material():
    return {"name": "AcceptanceMaterial", "doubleSided": True,
            "pbrMetallicRoughness": {"baseColorFactor": [0.18, 0.42, 0.73, 1.0],
                                     "metallicFactor": 0.23, "roughnessFactor": 0.61}}


def bounds(positions):
    return {"min": [min(p[i] for p in positions) for i in range(3)],
            "max": [max(p[i] for p in positions) for i in range(3)]}


def pack_rows(rows):
    flat = [float(component) for row in rows for component in row]
    return struct.pack("<" + "f" * len(flat), *flat)


def unpack_rows(data, width):
    require(len(data) % (4 * width) == 0, "float attribute byte length mismatch")
    values = struct.unpack("<" + "f" * (len(data) // 4), data)
    return [tuple(values[i:i + width]) for i in range(0, len(values), width)]


def triangle_key(ids):
    """Preserve winding, permit cyclic index rotation and triangle reordering."""
    ids = tuple(ids)
    return min(ids, ids[1:] + ids[:1], ids[2:] + ids[:2])


def compare_geometry(actual, expected, tolerances):
    count = len(actual["POSITION"])
    require(count == len(expected["POSITION"]), f"vertex count {count} != {len(expected['POSITION'])}")
    require(len(actual["indices"]) == len(expected["indices"]), "index count changed")
    for semantic, width in (("POSITION", 3), ("NORMAL", 3), ("TEXCOORD_0", 2)):
        require(len(actual[semantic]) == count, f"{semantic} count mismatch")
        require(all(len(row) == width and all(math.isfinite(x) for x in row) for row in actual[semantic]),
                f"{semantic} is malformed or nonfinite")
    # Attribute tuple matching distinguishes coincident vertices on opposite sides of the UV seam.
    available = set(range(count))
    mapping = {}
    maximum = {name: 0.0 for name in tolerances}
    for got_id in range(count):
        candidates = []
        for ref_id in sorted(available):
            errors = {name: max(abs(a - b) for a, b in zip(actual[name][got_id], expected[name][ref_id]))
                      for name in tolerances}
            if all(errors[name] <= tolerances[name] for name in tolerances):
                candidates.append((sum(errors[name] / tolerances[name] for name in tolerances), ref_id, errors))
        require(candidates, f"unmatched vertex {got_id}: position={actual['POSITION'][got_id]}, uv={actual['TEXCOORD_0'][got_id]}")
        _, ref_id, errors = min(candidates)
        mapping[got_id] = ref_id
        available.remove(ref_id)
        for name, error in errors.items():
            maximum[name] = max(maximum[name], error)
        normal_length = math.sqrt(sum(x * x for x in actual["NORMAL"][got_id]))
        require(abs(normal_length - 1.0) <= 0.01, f"normal is not unit length: {normal_length}")
    require(len(actual["indices"]) % 3 == 0, "triangle index count is not divisible by three")
    require(all(isinstance(i, int) and 0 <= i < count for i in actual["indices"]), "invalid index")
    got_faces = Counter(triangle_key([mapping[i] for i in actual["indices"][start:start + 3]])
                        for start in range(0, len(actual["indices"]), 3))
    ref_faces = Counter(triangle_key(expected["indices"][start:start + 3])
                        for start in range(0, len(expected["indices"]), 3))
    require(got_faces == ref_faces, "indexed topology, triangle multiplicity or winding changed")
    got_bounds, ref_bounds = bounds(actual["POSITION"]), bounds(expected["POSITION"])
    for side in ("min", "max"):
        require(max(abs(a - b) for a, b in zip(got_bounds[side], ref_bounds[side])) <= tolerances["POSITION"],
                f"{side} bounds changed")
    return {"vertex_count": count, "triangle_count": len(actual["indices"]) // 3,
            "maximum_absolute_error": maximum, "tolerances": tolerances, "bounds": got_bounds,
            "uv_seam_preserved": True, "winding_preserved": True}


def base_document(extension):
    return {"asset": {"version": "2.0", "generator": SCHEMA}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"name": "AcceptancePatch", "mesh": 0}],
            "meshes": [{"name": "AcceptancePatch", "primitives": [{"mode": 4, "material": 0,
                          "indices": 3, "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}}]}],
            "materials": [material()], "extensionsUsed": [extension], "extensionsRequired": [extension]}


def accessors(vertex_count, index_count):
    b = bounds(geometry()["POSITION"])
    return [{"componentType": 5126, "count": vertex_count, "type": "VEC3", **b},
            {"componentType": 5126, "count": vertex_count, "type": "VEC3"},
            {"componentType": 5126, "count": vertex_count, "type": "VEC2"},
            {"componentType": 5123, "count": index_count, "type": "SCALAR"}]


def artifact_manifest(directory):
    return {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size}
            for p in sorted(Path(directory).iterdir()) if p.is_file()}
