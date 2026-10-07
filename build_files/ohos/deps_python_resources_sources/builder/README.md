# Sealed pure-resource orchestration candidate

This small wrapper freezes and calls the existing pure-resource public functions
verify_registry, materialize (via prepare), prepare, verify_cache, assemble and
verify_resources. It does not replace extraction/resource algorithms, source
versions, package bytes, original notice handling or the existing eight-source
lock. All old twelve top-level recipe/source files, old inventories/metadata/
notices, vendor helper and all eight complete registry input files are immutable
repository-relative sealed inputs. Its own builder/guardtests/README bytes are
sealed too. Existing source lock SHA256 is
27aeac5e6fd312c672d09d31c6d937d3fa0bb9b77080017617ff75d4e444536d.

The selected archives total 2,408,470 original bytes and 643 pristine source
files; the output has 193 original resource/metadata/notice files and exactly
eight distribution metadata directories. There is no new tpr registration,
source patch, pip, network, compiler/SDK probe, CPython/NumPy/native core rebuild
or automatic adoption of old accepted output roots. Outputs and input/resource
manifests retain the original source lock and eight exact source inventories.
Each wrapper receipt binds the complete recipe seal, eight source/tree hashes,
output resources.json SHA/size and site inventory seal.

CLI stages are verify-inputs, copy-inputs, prepare, assemble, full and audit.
--repo, --root (orchestration state), --cache (complete sources), --resources
(pure data output) and --tmp are explicit. State/cache/resources must be
independent sibling paths below private XDG_CACHE_HOME or TMPDIR, on the same
filesystem, and disjoint from repository and the explicit temporary subtree.
TMPDIR itself may be used as --tmp when outputs are under XDG_CACHE_HOME.
Existing state needs --resume and its exact input lock and output path binding.
New state refuses existing source/resource outputs. Existing complete outputs
may be reused only through their exact wrapper-owned state after original API
verification; partial source/resource outputs are preserved and refused.

The wrapper state has its own marker and nonblocking lock; it leaves the original
source/resource marker/layout unchanged. Attempt directories and request/result
receipts are new immutable files, numbered monotonically. Every data-stage
attempt preserves all current success pointers in history before checking
frozen inputs. Failure writes FAIL and never leaves a current full/native PASS.
Changed inputs require a new reviewed input lock and fresh output roots.

full means complete source preparation, assembly and source/resource data
verification. Without --terminal-python it records terminal_native_checked=false
and core/application/bpy/HAP NOT_RUN. Only an explicitly supplied terminal
CPython3.13.13 may run the existing isolated -I -B -S check_native.py under full
or audit, with actual stdout/stderr/exit/interpreter SHA and selected payload
binding. Its scope remains independent terminal pure validation. Old native13
receipts and native90/93 payloads are never inherited into new acceptance.
The child implementing this wrapper runs only short data/IO guards; parent owns
future terminal native checks and real bpy/shared runtime/HAP acceptance.

```sh
REPO=/path/to/complete/repository
BUILDER="$REPO/build_files/ohos/deps_python_resources_sources/builder/builder.py"
STATE="$XDG_CACHE_HOME/your-new-pure-builder/state"
SOURCES="$XDG_CACHE_HOME/your-new-pure-builder/sources"
PURE="$XDG_CACHE_HOME/your-new-pure-builder/pure"
PYTHON=/explicit/existing/python3
"$PYTHON" -I -B -S "$BUILDER" verify-inputs --repo "$REPO"
"$PYTHON" -I -B -S "$BUILDER" prepare --repo "$REPO" --root "$STATE" --cache "$SOURCES" --resources "$PURE" --tmp "$TMPDIR"
"$PYTHON" -I -B -S "$BUILDER" assemble --resume --repo "$REPO" --root "$STATE" --cache "$SOURCES" --resources "$PURE" --tmp "$TMPDIR"
"$PYTHON" -I -B -S "$BUILDER" audit --resume --repo "$REPO" --root "$STATE" --cache "$SOURCES" --resources "$PURE" --tmp "$TMPDIR"
# Parent future: fresh independent paths, complete pure data plus explicit terminal gate.
# "$PYTHON" -I -B -S "$BUILDER" full ... --terminal-python /explicit/accepted/python3.13
# No SDK or native library paths are implicit pure resource recipe inputs.
```

copy-inputs requires a NEW --destination below TMPDIR. It copies every sealed
repository-relative file plus inputs.lock.json, verifies the whole mini closure
and removes its own failed copy on exceptions. The caller must clean successful
temporary copies. No full Blender checkout, old native prefix or workbench data
is needed. guardtests.py uses TemporaryDirectory and only short original/Unicode
source/data replay, frozen recipe/helper/lock/archive tamper, unsafe/case
extraction, output overlap/ownership/lock and failure receipt checks; it does
not run the old full acceptance harness or terminal13 native check.

DevEco source recipe entry is now builder/builder.py with builder/inputs.lock.json.
All sibling Python files are declared in sealed_files. Presence and a valid
frozen declaration mean prepared recipe; formal fresh rebuild/application
acceptance needs the separate parent receipts.
