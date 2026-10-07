# SPDX-License-Identifier: Apache-2.0
"""Postlink real bpy compressed export and reimport acceptance. No mocks or loader overrides."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bpy_acceptance_common import (
    arguments, check_material, clear_scene, decode_export, export_operator,
    finish, import_operator, positive_import, require_imported, snapshot_geometry,
)
from gltf_acceptance_common import (
    TOLERANCES, artifact_manifest, compare_geometry, empty_output_directory, geometry,
)


def run(args, report, manifest, libraries):
    import bpy
    output = empty_output_directory(args.output)
    report["exports"] = {}
    # Both original compressed imports must survive export with either real codec.
    for source_kind in ("draco", "meshopt"):
        for target_kind in ("draco", "meshopt"):
            imported, obj = positive_import(args.fixtures, source_kind)
            for selected in list(bpy.context.selected_objects):
                selected.select_set(False)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            label = f"{source_kind}-to-{target_kind}"
            path = output / (label + ".gltf")
            operator = export_operator(path, target_kind)
            decoded = decode_export(path, target_kind, libraries)
            clear_scene()
            reimport_operator = import_operator(path)
            require_imported(reimport_operator)
            actual, reimported = snapshot_geometry()
            reimported_evidence = compare_geometry(actual, geometry(), TOLERANCES["roundtrip"])
            reimported_evidence["material"] = check_material(reimported)
            reimported_evidence["operator"] = reimport_operator
            report["exports"][label] = {"source_import": imported, "export_operator": operator,
                                        "independent_native_decode": decoded, "reimport": reimported_evidence}
    report["output"] = {"directory": str(output), "files": artifact_manifest(output)}
    clear_scene()


if __name__ == "__main__":
    args = arguments("export")
    if finish(args, "export", run):
        raise RuntimeError("bpy compressed export acceptance failed; inspect the JSON report")
