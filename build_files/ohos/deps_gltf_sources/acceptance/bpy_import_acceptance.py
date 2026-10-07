# SPDX-License-Identifier: Apache-2.0
"""Postlink real bpy import acceptance. Invoke with Blender --python-exit-code 1."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True

# Blender --python does not promise the script directory is on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bpy_acceptance_common import arguments, finish, negative_imports, positive_import


def run(args, report, manifest, libraries):
    report["positive_imports"] = {}
    for kind in ("draco", "meshopt"):
        evidence, _ = positive_import(args.fixtures, kind)
        report["positive_imports"][kind] = evidence
    report["negative_imports"] = negative_imports(args.fixtures)


if __name__ == "__main__":
    args = arguments("import")
    if finish(args, "import", run):
        raise RuntimeError("bpy compressed import acceptance failed; inspect the JSON report")
