# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage/run the signed OHOS native diagnostic without touching user configuration.

Host-side Python stdlib only. Does not build, patch, strip or sign any ELF. All
staging, child CWD/HOME/config/cache/temp and evidence live under the original
application TMPDIR on its real filesystem. Reports/artifacts survive normal
payload cleanup. A new stage gets a new root and cannot reuse stale evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback

STAGES = ("model", "reopen", "obj", "glb", "cycles")
KIND = "blender-ohos-acceptance-v1"
HERE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def absolute_input(value):
    path = Path(value)
    require(path.is_absolute(), "Input must be absolute: " + str(path))
    return path.resolve(strict=True)


def private_parent():
    value = os.environ.get("TMPDIR")
    require(value is not None, "TMPDIR must explicitly name the application's real temporary filesystem")
    parent = absolute_input(value)
    require(parent.is_dir() and str(parent) != "/tmp", "Invalid private TMPDIR")
    return parent


def elf(path):
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def walk_files(folder):
    """Do not traverse directory symlinks or permit external symlink targets."""
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in sorted(dirs):
            path = Path(current) / name
            require(not path.is_symlink(), "Directory symlink is not accepted: " + str(path))
        dirs.sort()
        for name in sorted(files):
            yield Path(current) / name


class Stager:
    def __init__(self, args, root):
        self.args, self.root = args, root
        self.files = []
        self.elf_info = {}
        self.libs = {}
        self.library_index = {}
        self.readelf = absolute_input(args.readelf)
        self.manifest = {"kind": KIND, "schema": 1, "root": str(root), "status": "STAGING",
                         "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "blender": "5.2.2", "python": "3.13.13",
                         "scope": "terminal diagnostic with genuine unversioned shared Python; installed HAP remains unverified",
                         "signed_bytes": "copied unchanged and SHA256 checked; input signature validity must be supplied by the native build/sign step",
                         "mandatory_stages": list(STAGES), "files": self.files,
                         "elf": self.elf_info, "system_dependencies": {}, "inputs": {}}

    def copy(self, source, destination, role):
        source = source.resolve(strict=True)
        require(source.is_file(), "Not a regular input: " + str(source))
        source_stat = source.stat()
        before = digest(source)
        if destination.exists():
            require(digest(destination) == before, "Conflicting staged destination: " + str(destination))
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        # HOME/hmdfs modes are not trusted. Set private copy modes on the real TMPDIR filesystem.
        destination.chmod(0o700 if source_stat.st_mode & 0o111 else 0o600)
        require(digest(source) == before and digest(destination) == before,
                "Source changed or signed bytes changed during copy: " + str(source))
        self.files.append({"path": str(destination.relative_to(self.root)), "source": str(source),
                           "role": role, "bytes": source_stat.st_size, "sha256": before,
                           "source_mode": oct(stat.S_IMODE(source_stat.st_mode)),
                           "staged_mode": oct(stat.S_IMODE(destination.stat().st_mode))})
        return destination

    def tree(self, source, destination, role, allow_elf=False):
        source = absolute_input(source)
        require(source.is_dir(), "Missing resource tree: " + str(source))
        for path in walk_files(source):
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(source), "External resource symlink: " + str(path))
            require(allow_elf or not elf(resolved), "ELF in Blender data/scripts needs explicit native handling: " + str(path))
            self.copy(path, destination / relative, role)

    def inspect_elf(self, path):
        path = path.resolve(strict=True)
        if path in self.elf_info_cache:
            return self.elf_info_cache[path]
        require(elf(path), "Expected actual ELF: " + str(path))
        result = subprocess.run([str(self.readelf), "--file-header", "--dynamic", "--sections", str(path)],
                                env={"PATH": "/system/bin:/system/xbin", "LANG": "C", "LC_ALL": "C"},
                                capture_output=True, text=True, check=False)
        require(result.returncode == 0, "readelf failed on " + str(path) + ": " + result.stderr)
        require("AArch64" in result.stdout and "ELF64" in result.stdout,
                "Not ELF64 AArch64: " + str(path))
        values = {name: re.findall(r"\(" + name + r"\).*?\[([^\]]+)\]", result.stdout)
                  for name in ("NEEDED", "SONAME", "RPATH", "RUNPATH")}
        require(len(values["SONAME"]) <= 1, "Multiple SONAME tags: " + str(path))
        require(".codesign" in result.stdout, "Unsigned ELF input; sign in native build step first: " + str(path))
        info = {"needed": values["NEEDED"], "soname": values["SONAME"][0] if values["SONAME"] else None,
                "rpath": values["RPATH"], "runpath": values["RUNPATH"],
                "codesign_section_present": True, "ohos_ident_section_present": ".note.ohos.ident" in result.stdout}
        self.elf_info_cache[path] = info
        return info

    def index(self, folders):
        for folder in folders:
            folder = absolute_input(folder)
            require(folder.is_dir(), "Native library search path is not a directory: " + str(folder))
            for path in walk_files(folder):
                if ".so" not in path.name:
                    continue
                if not elf(path):
                    continue  # SDK linker scripts are not copied as native ELF.
                resolved = path.resolve(strict=True)
                previous = self.library_index.get(path.name)
                if previous and digest(previous) != digest(resolved):
                    raise RuntimeError("Ambiguous native library filename: " + path.name)
                self.library_index[path.name] = resolved

    def stage_lib(self, source, names=()):
        info = self.inspect_elf(source)
        names = set(names) | {source.name}
        if info["soname"]:
            names.add(info["soname"])
        staged = []
        if info["soname"] and info["soname"].startswith("libpython3.13"):
            # Retain the accepted prefix's lib location: its unchanged $ORIGIN/../.. stays private.
            folder = self.root / "runtime/5.2/python/lib"
        elif info["soname"] == "libblender_core.so":
            folder = self.root / "lib"
        else:
            # Keep a prefix-like depth for existing signed $ORIGIN/../.. dependency RPATHs.
            folder = self.root / "lib/deps/lib"
        for name in sorted(names):
            require(Path(name).name == name and name not in {".", ".."}, "Unsafe ELF dependency name: " + name)
            destination = self.copy(source, folder / name, "native-library")
            self.libs[name] = destination
            staged.append(destination)
        return staged

    def audit_rpath(self, path, info):
        resolved_dirs = []
        for value in info["rpath"] + info["runpath"]:
            for entry in value.split(":"):
                require(entry and ("$ORIGIN" in entry or "${ORIGIN}" in entry),
                        "Nonisolated RPATH requires relink then resign; will not edit signed ELF: " +
                        str(path) + " => " + repr(entry))
                expanded = entry.replace("${ORIGIN}", str(path.parent)).replace("$ORIGIN", str(path.parent))
                require("$" not in expanded, "Unsupported RPATH token: " + entry)
                candidate = Path(expanded).resolve()
                require(candidate.is_relative_to(self.root), "RPATH escaped private root: " + str(candidate))
                resolved_dirs.append(str(candidate.relative_to(self.root)))
        return resolved_dirs

    def finish(self):
        self.elf_info_cache = {}
        args, root = self.args, self.root
        runtime = root / "runtime" / "5.2"
        python = absolute_input(args.python_root)
        data = absolute_input(args.datafiles)
        scripts = absolute_input(args.scripts)
        addon = absolute_input(args.cycles_addon)
        diagnostic, core = absolute_input(args.diagnostic), absolute_input(args.core)
        require(elf(diagnostic) and elf(core), "Link first: diagnostic and core must both be actual ELF files")
        require(os.access(diagnostic, os.X_OK), "Diagnostic source is not executable")
        require((data / "colormanagement" / "config.ocio").is_file(),
                "Use runtime datafiles, not build-generated *.c: colormanagement/config.ocio missing")
        require((scripts / "modules" / "bpy" / "__init__.py").is_file(), "Missing real Blender Python scripts")
        require((scripts / "addons_core/io_scene_gltf2/__init__.py").is_file(), "Missing bundled glTF add-on")
        require((addon / "__init__.py").is_file(), "Missing actual Cycles add-on")
        pylib = python / "lib" / "python3.13"
        require((pylib / "encodings/__init__.py").is_file() and (pylib / "lib-dynload").is_dir(),
                "Missing Python 3.13 stdlib/lib-dynload")
        require((pylib / "site-packages/numpy/__init__.py").is_file() and
                (pylib / "site-packages/requests/__init__.py").is_file(), "Use accepted NumPy+requests assembled prefix")
        self.manifest["inputs"] = {"diagnostic": str(diagnostic), "core": str(core),
                                   "datafiles": str(data), "scripts": str(scripts),
                                   "cycles_addon": str(addon), "python_root": str(python),
                                   "native_lib_dirs": args.native_lib_dir,
                                   "system_lib_dirs": args.system_lib_dir,
                                   "readelf": str(self.readelf), "source_root": args.source_root}
        self.tree(data, runtime / "datafiles", "blender-datafiles")
        self.tree(scripts, runtime / "scripts", "blender-scripts")
        cycles_target = runtime / "scripts/addons_core/cycles"
        # Installed trees can already contain the same add-on; copy() rejects differing bytes.
        self.tree(addon, cycles_target, "cycles-addon")
        self.tree(pylib, runtime / "python/lib/python3.13", "python-stdlib-packages", allow_elf=True)
        diagnostic_copy = self.copy(diagnostic, root / "bin/blender-ohos-diagnostic", "diagnostic")
        diagnostic_copy.chmod(0o700)
        self.copy(HERE / "bpy_acceptance.py", root / "acceptance/bpy_acceptance.py", "acceptance-script")
        self.copy(HERE / "diagnostic_driver.py", root / "acceptance/diagnostic_driver.py", "acceptance-driver")
        self.copy(HERE / "README.md", root / "reports/acceptance-README.md", "acceptance-contract")
        self.copy(HERE / "api_sources.json", root / "reports/api_sources.json", "acceptance-api-catalog")
        self.index([python / "lib", core.parent] + args.native_lib_dir)
        core_info = self.inspect_elf(core)
        require(core_info["soname"] == "libblender_core.so", "Core SONAME is not libblender_core.so")
        self.stage_lib(core, ("libblender_core.so",))
        for value in args.dynamic_library:
            dynamic = absolute_input(value)
            require(dynamic.is_file() and not dynamic.is_symlink(), "Explicit regular signed dynamic artifact required")
            self.stage_lib(dynamic, (dynamic.name,))
        shared = list((python / "lib").glob("libpython3.13.so*"))
        require(len(shared) == 1 and shared[0].name == "libpython3.13.so",
                "Require exactly the genuine unversioned Python runtime; versioned aliases are rejected")
        require(self.inspect_elf(shared[0])["soname"] == "libpython3.13.so",
                "Python library must be built with SONAME libpython3.13.so")
        for path in shared:
            self.copy(path, runtime / "python/lib" / path.name, "python-shared-runtime")
            self.stage_lib(path.resolve(strict=True), (path.name,))
        system = {}
        for directory in args.system_lib_dir:
            directory = absolute_input(directory)
            require(directory.is_dir(), "System SDK library directory missing")
            for path in directory.iterdir():
                if path.is_file() and ".so" in path.name:
                    system[path.name] = str(path)
        pending = [diagnostic_copy] + [path for path in walk_files(root / "runtime") if elf(path)]
        pending += list(self.libs.values())
        checked = set()
        while pending:
            path = pending.pop()
            if path in checked:
                continue
            checked.add(path)
            info = self.inspect_elf(path)
            info = dict(info)
            info["resolved_rpath_dirs"] = self.audit_rpath(path, info)
            info["sha256"] = digest(path)
            self.elf_info[str(path.relative_to(root))] = info
            for needed in info["needed"]:
                require(Path(needed).name == needed, "NEEDED has an absolute/relative path: " + needed)
                if needed.startswith("libpython"):
                    require(needed == "libpython3.13.so", "Foreign/versioned Python dependency: " + needed)
                if needed in self.libs:
                    continue
                # The shared Python must always come from the accepted assembled prefix.
                if needed.startswith("libpython"):
                    candidate = self.library_index.get(needed)
                    require(candidate and candidate.is_relative_to(python), "Foreign/missing Python runtime: " + needed)
                elif needed in system:
                    self.manifest["system_dependencies"][needed] = system[needed]
                    continue
                else:
                    candidate = self.library_index.get(needed)
                require(candidate is not None, "Unresolved NEEDED: " + needed + " (required by " + str(path) + ")")
                pending.extend(self.stage_lib(candidate, (needed,)))
        python_sonames = {info["soname"] for info in self.elf_info.values()
                          if info["soname"] and info["soname"].startswith("libpython3.13")}
        require(python_sonames == {"libpython3.13.so"}, "Require one genuine unversioned Python SONAME staged")
        self.manifest["python_soname"] = next(iter(python_sonames))
        self.manifest["ld_library_path"] = ["lib", "lib/deps/lib", "runtime/5.2/python/lib"]
        source = absolute_input(args.source_root)
        snapshot_path = absolute_input(args.source_snapshot)
        require(digest(snapshot_path) == args.source_snapshot_sha256, "Caller source snapshot SHA changed")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        require(snapshot.get("schema") == 1 and snapshot.get("kind") == "editor-source-snapshot" and
                snapshot.get("scope") == "complete", "Complete caller source snapshot required")
        self.manifest["source_commit"] = snapshot.get("source_commit")
        self.manifest["source_tree_sha256"] = snapshot["tree_sha256"]
        self.manifest["source_snapshot_sha256"] = args.source_snapshot_sha256
        # Copy actual entry/patch APIs into evidence, including local modifications.
        for relative in ("source/creator/ohos/blender_ohos_diagnostic.cc",
                         "source/creator/CMakeLists.txt", "source/creator/creator.cc",
                         "source/blender/python/intern/bpy_interface.cc",
                         "source/blender/blenkernel/intern/appdir.cc",
                         "source/blender/blenkernel/BKE_blender_version.h"):
            self.copy(source / relative, root / "reports/source" / relative, "formal-source-evidence")
        if args.cmake_cache:
            self.copy(absolute_input(args.cmake_cache), root / "reports/CMakeCache.txt", "native-build-config")
        self.manifest["status"] = "STAGED_NOT_RUN"
        write_json(root / "stage-manifest.json", self.manifest)
        checksums = "".join(item["sha256"] + "  " + item["path"] + "\n" for item in self.files)
        (root / "reports/staged-SHA256SUMS").write_text(checksums, encoding="utf-8")
        return self.manifest


def validate_root(value):
    raw = Path(value)
    require(raw.is_absolute() and not raw.is_symlink(), "Root must be an absolute private directory, not a symlink")
    root = raw.resolve(strict=True)
    require(root.parent == private_parent() and root.name.startswith("blender acceptance-"),
            "Refusing a root not created directly under application TMPDIR")
    require(stat.S_IMODE(root.stat().st_mode) == 0o700, "Private root permissions changed")
    manifest = json.loads((root / "stage-manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("kind") == KIND and manifest.get("root") == str(root), "Not a matching acceptance root")
    return root, manifest


def verify_staged(root, manifest):
    for item in manifest["files"]:
        path = root / item["path"]
        require(not Path(item["path"]).is_absolute() and path.resolve().is_relative_to(root),
                "Manifest path escaped root")
        require(path.is_file() and not path.is_symlink() and digest(path) == item["sha256"],
                "Staged resource missing or modified: " + item["path"])


def cleanup_payload(root):
    for name in ("bin", "lib", "runtime", "sessions", "acceptance"):
        path = root / name
        require(not path.is_symlink(), "Refusing cleanup symlink: " + str(path))
        if path.exists():
            shutil.rmtree(path)


def stage(args):
    root = Path(tempfile.mkdtemp(prefix="blender acceptance-", dir=private_parent()))
    root.chmod(0o700)
    (root / "reports").mkdir(mode=0o700)
    (root / "artifacts").mkdir(mode=0o700)
    stager = None
    try:
        stager = Stager(args, root)
        manifest = stager.finish()
        if args.print_root_only:
            print(root)
        else:
            print(json.dumps({"status": manifest["status"], "root": str(root),
                              "manifest": str(root / "stage-manifest.json"),
                              "files": len(manifest["files"]), "elf_files": len(manifest["elf"])}, indent=2))
        return 0
    except BaseException:
        failed = stager.manifest if stager else {"kind": KIND, "root": str(root)}
        failed["status"] = "STAGING_FAILED"
        failed["traceback"] = traceback.format_exc()
        write_json(root / "stage-manifest.json", failed)
        cleanup_payload(root)
        print("STAGING_FAILED; private evidence: " + str(root), file=sys.stderr)
        raise


def clean_environment(root, stage_name, manifest):
    session = root / "sessions" / stage_name
    for name in ("home", "config", "cache", "temp"):
        (session / name).mkdir(parents=True, mode=0o700, exist_ok=False)
    return {"HOME": str(session / "home"), "XDG_CONFIG_HOME": str(session / "config"),
            "XDG_CACHE_HOME": str(session / "cache"), "XDG_RUNTIME_DIR": str(session / "temp"),
            "TMPDIR": str(session / "temp"), "PATH": "/system/bin:/system/xbin",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
            "LD_LIBRARY_PATH": ":".join(str(root / name) for name in manifest["ld_library_path"]),
            "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "BLIS_NUM_THREADS": "2",
            "OHOS_ACCEPT_ROOT": str(root), "OHOS_ACCEPT_STAGE": stage_name}


def stop_child(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def run_one(root, manifest, stage_name, timeout):
    report_path = root / "reports" / (stage_name + ".json")
    process_path = root / "reports" / (stage_name + ".process.json")
    require(not process_path.exists() and not report_path.exists(), "Stage already has evidence; use a new stage root: " + stage_name)
    environment = clean_environment(root, stage_name, manifest)
    session = root / "sessions" / stage_name
    command = [str(root / "bin/blender-ohos-diagnostic"), "-b",
               "--runtime", str(root / "runtime"), "--config", str(session / "config"),
               "--cache", str(session / "cache"), "--temp", str(session / "temp"),
               "--python", str(root / "acceptance/bpy_acceptance.py")]
    evidence = {"stage": stage_name, "command": command, "cwd": str(session), "env": environment,
                "timeout_seconds": timeout, "status": "RUNNING", "report": str(report_path),
                "log": str(root / "reports" / (stage_name + ".log"))}
    write_json(process_path, evidence)
    process = None
    started = time.monotonic()
    try:
        with Path(evidence["log"]).open("wb") as log:
            process = subprocess.Popen(command, cwd=session, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            evidence["pid"] = process.pid
            write_json(process_path, evidence)
            try:
                evidence["returncode"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                evidence["timed_out"] = True
                stop_child(process)
                evidence["returncode"] = process.returncode
        script = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        evidence["script_status"] = script.get("status") if script else "MISSING"
        valid_report = script and script.get("stage") == stage_name and script.get("root") == str(root)
        evidence["status"] = "PASS" if (valid_report and script.get("status") == "PASS" and
                                             evidence["returncode"] == 0 and not evidence.get("timed_out")) else "FAIL"
        if script is None:
            evidence["reason"] = "Native process failed before producing a bpy report; inspect loader/initialization log"
    except BaseException:
        evidence["status"] = "FAIL"
        evidence["traceback"] = traceback.format_exc()
        raise
    finally:
        if process:
            stop_child(process)
        evidence["seconds"] = time.monotonic() - started
        write_json(process_path, evidence)
    return evidence


def summary(root, manifest):
    stages = {}
    for name in STAGES:
        path = root / "reports" / (name + ".process.json")
        stages[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "NOT_RUN"}
    states = [item["status"] for item in stages.values()]
    status = "PASS" if all(value == "PASS" for value in states) else (
        "FAIL" if any(value in {"FAIL", "BLOCKED"} for value in states) else "INCOMPLETE")
    result = {"schema": 1, "status": status, "scope": "Blender 5.2.2 native background mandatory bpy acceptance",
              "root": str(root), "source_commit": manifest["source_commit"],
              "manifest_sha256": digest(root / "stage-manifest.json"), "stages": stages,
              "unverified": ["HAP/native-lib namespace", "XComponent lifecycle/input/IME",
                             "Vulkan GPU/WSI/presentation/viewport", "Cycles GPU", "device loss"],
              "artifact_hashes": {str(path.relative_to(root)): digest(path)
                                  for path in walk_files(root / "artifacts") if path.is_file()}}
    write_json(root / "reports/summary.json", result)
    return result


def run(args):
    root, manifest = validate_root(args.root)
    require(manifest["status"] == "STAGED_NOT_RUN", "Staging did not complete")
    require((root / "bin/blender-ohos-diagnostic").is_file(), "Payload was cleaned; stage a fresh root")
    lock = root / "run.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    result = None
    try:
        verify_staged(root, manifest)
        for name in args.stages:
            require(not (root / "reports" / (name + ".process.json")).exists() and
                    not (root / "reports" / (name + ".json")).exists(),
                    "Stage already has evidence; stage a fresh root: " + name)
            if name in {"reopen", "obj", "glb"}:
                model_path = root / "reports/model.process.json"
                model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else None
                if not model or model.get("status") != "PASS":
                    write_json(root / "reports" / (name + ".process.json"), {
                        "stage": name, "status": "BLOCKED", "reason": "Mandatory model stage has no successful native exit + report"})
                    print(name + ": BLOCKED (model prerequisite failed)", flush=True)
                    continue
            evidence = run_one(root, manifest, name, args.timeout)
            print(name + ": " + evidence["status"] + " " + evidence["log"], flush=True)
        result = summary(root, manifest)
        return 0 if result["status"] == "PASS" else (1 if result["status"] == "FAIL" else 2)
    except BaseException:
        result = summary(root, manifest)
        result["status"] = "FAIL"
        result["driver_traceback"] = traceback.format_exc()
        write_json(root / "reports/summary.json", result)
        raise
    finally:
        if result is None:
            result = summary(root, manifest)
        if not args.keep_staging:
            cleanup_payload(root)
            result["payload_cleaned"] = True
            write_json(root / "reports/summary.json", result)
        lock.unlink(missing_ok=True)
        print("Evidence: " + str(root / "reports/summary.json"), flush=True)


def clean(args):
    root, _manifest = validate_root(args.root)
    require(not (root / "run.lock").exists(), "A run owns this root; finish/stop it before cleanup")
    if args.remove_evidence:
        shutil.rmtree(root)
    else:
        cleanup_payload(root)
    print("Removed " + ("private root and evidence" if args.remove_evidence else "staged runtime, libraries, scripts and sessions"))
    return 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    stage_parser = commands.add_parser("stage", help="copy/hash resources and audit ELF; never execute Blender")
    for name in ("diagnostic", "core", "python-root", "scripts", "datafiles", "cycles-addon", "source-root", "readelf"):
        stage_parser.add_argument("--" + name, required=True)
    stage_parser.add_argument("--native-lib-dir", action="append", default=[], help="explicit signed dependency library directory, repeatable")
    stage_parser.add_argument("--dynamic-library", action="append", default=[], help="explicit final signed dlopen-only bridge, repeatable")
    stage_parser.add_argument("--system-lib-dir", action="append", required=True, help="actual OHOS SDK system-library directory, repeatable")
    stage_parser.add_argument("--cmake-cache")
    stage_parser.add_argument("--source-snapshot", required=True)
    stage_parser.add_argument("--source-snapshot-sha256", required=True)
    stage_parser.add_argument("--print-root-only", action="store_true")
    run_parser = commands.add_parser("run", help="launch actual signed diagnostic in a clean private process per stage")
    run_parser.add_argument("--root", required=True)
    run_parser.add_argument("--stages", nargs="+", choices=STAGES, default=list(STAGES))
    run_parser.add_argument("--timeout", type=int, default=600, help="seconds per native stage")
    run_parser.add_argument("--keep-staging", action="store_true", help="keep payload for subsequent independent stages/debugging")
    clean_parser = commands.add_parser("clean", help="remove only a marked private acceptance root")
    clean_parser.add_argument("--root", required=True)
    clean_parser.add_argument("--remove-evidence", action="store_true", help="also delete retained reports and user-review artifacts")
    return result


def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    args = parser().parse_args()
    if args.command == "run":
        require(args.timeout > 0 and len(args.stages) == len(set(args.stages)), "Invalid timeout or duplicate stages")
        require(list(args.stages) == [name for name in STAGES if name in args.stages], "Run stages in model/reopen/obj/glb/cycles order")
    try:
        return {"stage": stage, "run": run, "clean": clean}[args.command](args)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
