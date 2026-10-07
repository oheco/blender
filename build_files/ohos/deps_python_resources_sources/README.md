# Offline pure Python resource closure

This NEW namespace selects requests 2.33.0, urllib3 2.8.0, idna 3.15, certifi 2026.7.22, charset-normalizer 3.4.1, cattrs 25.1.1, attrs 25.3.0, and typing_extensions 4.14.1. The separate lock leaves old registry entries, locks, and versions.cmake unchanged. CPython 3.13.13 and NumPy 2.3.4 are external accepted native inputs; these scripts never assemble or install their native payloads.

Complete original sdists are stored with the existing ordinary-Git `vendor_archive.py` format. Each lock input seals its repository-relative registry ID, original SHA/size/official URL, manifest, parts, source inventory, official metadata, and complete upstream notices. Extraction takes place on the caller's case-sensitive private filesystem. All scripts use only the standard library and existing repository helper, without pip, build backends, network, compiler, SDK, or evidence/workbench dependencies.

Choose explicit paths; the source cache and pure root must be NEW or already owned by this exact lock. Existing unowned or differently sealed outputs are refused. Completed owned roots may be verified/reused; partially prepared roots are preserved and require a fresh selection. All transient archives, extraction trees, publication locks, and assembly staging directories are under the explicit tmp directory and are cleaned even on exceptions. When XDG_CACHE_HOME/TMPDIR are provided, outputs must be below those caller private roots on the same filesystem as tmp.

```zsh
repo=/path/to/source-repository
builder="$repo/build_files/ohos/deps_python_resources_sources"
cache="$XDG_CACHE_HOME/your-owned-sources"
root="$XDG_CACHE_HOME/your-owned-pure-resources"
python=/path/to/accepted/terminal/python3.13

"$python" -I -B -S "$builder/materialize.py" --repo "$repo" --cache "$cache" --tmp "$TMPDIR"
"$python" -I -B -S "$builder/prepare.py" --repo "$repo" --cache "$cache" --tmp "$TMPDIR"
"$python" -I -B -S "$builder/assemble.py" --repo "$repo" --cache "$cache" --root "$root" --tmp "$TMPDIR"
"$python" -I -B -S "$builder/verify.py" --repo "$repo" --cache "$cache" --root "$root"
"$python" -I -B -S "$builder/check_native.py" --repo "$repo" --root "$root" --tmp "$TMPDIR"
"$python" -I -B -S "$builder/test_guards.py" --repo "$repo" --tmp "$TMPDIR"
```

`resources.py` also exports verify_registry, materialize, prepare, verify_cache, assemble, and verify_resources as public functions accepting Path arguments. The four command entrypoints forward to the same implementation. Every resource manifest uses relative paths and a lock fingerprint; moving an entire owned root does not alter its seal. For independent source-only replay, copy this namespace, the sealed vendor helper, and the eight referenced registry directories into a Unicode/space repository path, then run the commands with new cache/root paths.

`site-packages` contains exactly eight distribution metadata directories plus their original package trees (including cattr/attr upstream aliases and typing_extensions.py). METADATA is original PKG-INFO, and dist-info/licenses contains complete original notices. The assembler checks every copied source byte and rejects native libraries, bytecode, old versions, extra files, links, and noncanonical paths. certifi/cacert.pem is the untouched official 121-public-CA bundle.

The native check explicitly requires the accepted terminal CPython 3.13.13 under -I -B -S. It adds only this new pure site path, verifies import origins/hashes/versions and original metadata/licenses, exercises cattrs on an actual small JSON payload, and checks native SSL contexts against certifi's 121-CA set. Optional --http-meta reads the formal HTTPMetadata module unchanged and roundtrips that actual class. This is terminal pure validation; actual bpy, shared-runtime DSOs, installed HAP, and native93+pure8 runtime assembly remain parent gates.

See SOURCE-ATTRIBUTION.md for source/recipe provenance and exact original license handling. Existing security-candidate real TLS, expired-certificate, netrc, and dual-origin receipts are referenced by exact hashes in the separate handoff; this builder does not repeat advisory/network research.
