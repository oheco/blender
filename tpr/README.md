# Fixed third-party source inputs

This directory holds **complete original source archives**, not installed libraries
or pointers to a download that a formal build would perform. Archives are stored
as deterministic <=32 MiB ordinary Git parts because this public upstream-mirror
fork cannot upload new Git LFS objects and GitHub rejects >100 MiB ordinary files.
The original archive is unchanged; its URL, version, SHA-256, license references,
size, and each part's SHA-256 are recorded in its `manifest.json`.

This representation also preserves case-sensitive archive entries without trying
to extract them into the case-insensitive hmdfs checkout. Extraction/building use
a real application-private cache filesystem.

```sh
python3 build_files/ohos/vendor_archive.py verify tpr/sources/cpython-3.13.13
python3 build_files/ohos/vendor_archive.py materialize tpr/sources/cpython-3.13.13 \
  --output "$XDG_CACHE_HOME/blender-ohos-sources/Python-3.13.13.tar.xz"
```

The reassembler verifies every part and the original digest, rejects unsafe names,
symlinks, conflicting outputs, and partially published sources. Temporary files
are owned under `TMPDIR`; final source-cache publication uses a private-filesystem
lock and atomic rename. Existing conflicting cache content is never overwritten.

All original licenses and copyrights are retained inside each complete archive.
`license_files` in the manifest identifies their source paths. Local adaptations
are separate reviewed patches and reproducible build recipes under
`build_files/ohos`; do not overwrite a pristine original archive to hide patches.

SDKs and necessary preinstalled development tools are declared external build
prerequisites, not application runtime dependencies. A binary-only prerequisite
must be labeled as such; it is not a substitute for a runtime dependency's source.

The registry is still being populated. Its presence does not mean all Blender
libraries, native tests, GUI or final DevEco packaging are already completed.
