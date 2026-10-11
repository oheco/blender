#!/usr/bin/env python3
"""Compile, self-sign and execute isolated resize regression cases on HarmonyOS.

Pass a native C++ compiler (the Blender signing-aware compiler launcher is supported).
This verifies shared geometry/retirement policy code, not on-device WSI performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {result.stdout}")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", required=True, help="Native C++ compiler or signing-aware launcher")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if platform.system() != "HarmonyOS" or platform.machine() != "aarch64":
        raise SystemExit("Run this regression suite natively on HarmonyOS aarch64")
    compiler = shutil.which(args.cxx)
    signer = shutil.which("binary-sign-tool")
    readelf = shutil.which("llvm-readelf")
    if not compiler or not signer or not readelf:
        raise SystemExit("Native C++ compiler, binary-sign-tool and llvm-readelf must be available")
    temporary_root = Path(os.environ["TMPDIR"]).resolve(strict=True)
    repository = Path(__file__).resolve().parents[3]
    tests = [Path(__file__).with_name("resize_geometry_test.cc")]
    retirement_test = Path(__file__).with_name("resize_retirement_test.cc")
    if retirement_test.is_file():
        tests.append(retirement_test)
    tool_env = os.environ.copy()
    tool_env.pop("LD_LIBRARY_PATH", None)
    tool_env.pop("LD_PRELOAD", None)
    results = []
    with tempfile.TemporaryDirectory(prefix="blender-resize-regression-", dir=temporary_root) as name:
        owned = Path(name).resolve(strict=True)
        if owned.parent != temporary_root or not owned.name.startswith("blender-resize-regression-"):
            raise RuntimeError("Unexpected owned test directory")
        for source in tests:
            unsigned = owned / source.stem
            signed = owned / (source.stem + "-signed")
            run([compiler, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                 "-I" + str(repository / "source/blender/gpu"),
                 "-I" + str(repository / "intern/ghost/intern"),
                 str(source), "-o", str(unsigned)])
            run([signer, "sign", "-inFile", str(unsigned), "-outFile", str(signed),
                 "-selfSign", "1"], env=tool_env)
            sections = run([readelf, "--sections", str(signed)], env=tool_env)
            if ".codesign" not in sections:
                raise RuntimeError("Native test lacks a .codesign section")
            signed.chmod(0o700)
            output = run([str(signed)])
            results.append({"test": source.name, "source_sha256": digest(source),
                            "binary_sha256": digest(signed), "output": output,
                            "status": "passed"})
            print(output)
        # Check the exact owned absolute cleanup target again before TemporaryDirectory removes it.
        if Path(name).resolve(strict=True) != owned or owned.parent != temporary_root:
            raise RuntimeError("Owned test directory changed before cleanup")
    receipt = {"native_system": list(platform.uname()), "compiler": compiler,
               "tests": results, "scope": "Native shared-code regression; no WSI/device acceptance"}
    if args.receipt:
        if args.receipt.exists():
            raise SystemExit("Refusing to overwrite an existing test receipt")
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    else:
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
