# SPDX-License-Identifier: Apache-2.0
"""Generate/check a NEW candidate patch and test release control flow using pure Python.

This never imports bpy, loads a bridge DSO, applies a patch, or changes addon source.
The fake bridge below proves the extracted candidate's finally semantics only.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
import difflib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import textwrap

TARGET = "scripts/addons_core/io_scene_gltf2/blender/imp/draco_compression_extension.py"
PATCH_NAME = "0001-draco-decoder-errorcleanup.patch"
RECORD_NAME = "draco-errorcleanup-record.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def candidate_text(original):
    anchor = "    decoder = dll.decoderCreate()\n"
    release = "    dll.decoderRelease(decoder)\n"
    require(original.count(anchor) == 1 and original.count(release) == 1, "unexpected Draco source baseline")
    require(original.endswith(release), "Draco success release is not the final function statement")
    prefix, tail = original.split(anchor)
    body = tail[:-len(release)]
    candidate = prefix + anchor + "    try:\n" + textwrap.indent(body, "    ")
    candidate += "    finally:\n        dll.decoderRelease(decoder)\n"
    compile(candidate, TARGET + " (candidate)", "exec")
    return candidate


class Call:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakeBridge:
    def __init__(self, scenario):
        self.scenario = scenario
        self.handle = object()
        self.creates = 0
        self.releases = []
        callbacks = {
            "decoderCreate": self.create, "decoderRelease": self.release,
            "decoderDecode": lambda *args: scenario != "decode_failure",
            "decoderGetVertexCount": lambda *args: 8,
            "decoderGetIndexCount": lambda *args: 12,
            "decoderReadIndices": lambda *args: scenario != "indices_failure",
            "decoderGetIndicesByteLength": lambda *args: 24,
            "decoderCopyIndices": self.copy_indices,
            "decoderReadAttribute": lambda *args: scenario != "attribute_conversion_failure",
            "decoderGetAttributeByteLength": lambda *args: 96,
            "decoderCopyAttribute": lambda *args: None,
            "decoderAttributeIsNormalized": lambda *args: False,
        }
        for name, callback in callbacks.items():
            setattr(self, name, Call(callback))

    def create(self):
        if self.scenario == "create_exception":
            raise RuntimeError("injected create exception")
        self.creates += 1
        return self.handle

    def release(self, handle):
        require(handle is self.handle, "released the wrong native handle")
        self.releases.append(handle)

    def copy_indices(self, *args):
        if self.scenario == "copy_exception":
            raise RuntimeError("injected copy exception")


def contract_cases(candidate):
    parsed = ast.parse(candidate, filename=TARGET)
    function = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "decode_primitive")
    final_try = function.body[-1]
    require(isinstance(final_try, ast.Try) and len(final_try.finalbody) == 1, "candidate lacks one final release")
    release = final_try.finalbody[0]
    require(isinstance(release, ast.Expr) and isinstance(release.value, ast.Call) and
            isinstance(release.value.func, ast.Attribute) and release.value.func.attr == "decoderRelease",
            "finally block does not release decoder")
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and
             isinstance(node.func, ast.Attribute) and node.func.attr == "decoderRelease"]
    require(len(calls) == 1, "candidate has multiple native release sites")
    returns = [node for node in ast.walk(final_try) if isinstance(node, ast.Return)]
    require(len(returns) == 4, "candidate error returns changed")
    extracted = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(extracted)
    code = compile(extracted, TARGET + " (contract extraction)", "exec")
    results = {}
    scenarios = ("success", "decode_failure", "indices_failure", "missing_primitive_attribute",
                 "attribute_conversion_failure", "buffer_exception", "copy_exception",
                 "missing_extension_exception", "create_exception")
    for scenario in scenarios:
        bridge = FakeBridge(scenario)
        errors = []
        accessors = [SimpleNamespace(count=8, component_type=5126, type="VEC3", buffer_view=None),
                     SimpleNamespace(count=8, component_type=5126, type="VEC3", buffer_view=None),
                     SimpleNamespace(count=8, component_type=5126, type="VEC2", buffer_view=None),
                     SimpleNamespace(count=12, component_type=5123, type="SCALAR", buffer_view=None)]
        primitive = SimpleNamespace(name="ContractPrimitive", indices=3,
                    attributes={"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                    extensions={"KHR_draco_mesh_compression": {"bufferView": 0,
                                "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}}})
        if scenario == "missing_primitive_attribute":
            del primitive.attributes["NORMAL"]
        elif scenario == "missing_extension_exception":
            primitive.extensions = {}
        gltf = SimpleNamespace(data=SimpleNamespace(buffers=[object()], buffer_views=[], accessors=accessors),
                               buffers={}, log=SimpleNamespace(error=errors.append, warning=lambda message: None))

        def get_buffer(*args):
            if scenario == "buffer_exception":
                raise RuntimeError("injected buffer exception")
            return b"DRACO-contract"

        namespace = {name: getattr(ctypes, name) for name in (
            "c_void_p", "c_bool", "c_size_t", "c_uint32", "c_char_p")}
        namespace.update(cdll=SimpleNamespace(LoadLibrary=lambda path: bridge),
                         dll_path=lambda *args: Path("unused-contract-library"),
                         BinaryData=SimpleNamespace(get_buffer_view=get_buffer),
                         BufferView=SimpleNamespace(from_dict=lambda value: SimpleNamespace(**value)))
        exec(code, namespace)
        raised = None
        try:
            namespace["decode_primitive"](gltf, primitive)
        except (RuntimeError, KeyError) as error:
            raised = type(error).__name__
        expect_exception = scenario.endswith("exception")
        require((raised is not None) == expect_exception, f"exception behavior changed in {scenario}")
        expected_handles = 0 if scenario == "create_exception" else 1
        require(bridge.creates == expected_handles and len(bridge.releases) == expected_handles,
                f"create/release ownership failed in {scenario}")
        if scenario.endswith("failure") or scenario == "missing_primitive_attribute":
            require(len(errors) == 1, f"original diagnostic behavior changed in {scenario}")
        results[scenario] = {"result": "PASS", "creates": bridge.creates, "releases": len(bridge.releases),
                             "raised": raised, "logged_errors": errors}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[5])
    parser.add_argument("--generate", action="store_true", help="create absent patch/record in this script's directory")
    args = parser.parse_args()
    source = (args.source_root / TARGET).resolve(strict=True)
    original = source.read_text(encoding="utf-8")
    candidate = candidate_text(original)
    cases = contract_cases(candidate)
    patch = "".join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                       fromfile="a/" + TARGET, tofile="b/" + TARGET))
    record = {"schema": "ohos-draco-errorcleanup-candidate-v1", "result": "PASS", "applied": False,
              "target": TARGET, "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
              "after_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
              "patch": PATCH_NAME, "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
              "source_evidence": {"create_line": 57, "failure_return_lines": [66, 83, 108, 119],
                                  "success_release_line": 138},
              "change": "enclose all statements after decoderCreate in try/finally; one release on every return or exception",
              "validation": "pure Python extracted-function contract; fake bridge only; native and bpy runtime NOTRUN",
              "contracts": cases}
    destination = Path(__file__).resolve().parent
    if args.generate:
        with (destination / PATCH_NAME).open("x", encoding="utf-8") as stream:
            stream.write(patch)
        with (destination / RECORD_NAME).open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
    else:
        require((destination / PATCH_NAME).read_text(encoding="utf-8") == patch, "recorded patch differs from current source candidate")
        with (destination / RECORD_NAME).open(encoding="utf-8") as stream:
            require(json.load(stream) == record, "candidate record differs from source or contract results")
    print(json.dumps({"result": "PASS", "contracts": len(cases), "before_sha256": record["before_sha256"],
                      "after_sha256": record["after_sha256"], "patch_sha256": record["patch_sha256"],
                      "source_modified": False}, sort_keys=True))


if __name__ == "__main__":
    main()
