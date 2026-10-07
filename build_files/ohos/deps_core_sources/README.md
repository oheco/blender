# Core dependency source handoff

This directory records the six fixed core dependencies and their separately sealed source patches. The complete original archives are in the repository's ordinary Git source registry. This is a source handoff and a build-recipe candidate; the final portable offline dependency builder is not completed by this work.

## Source registry

| Dependency | Fixed upstream baseline | Registry directory | Parts | Original bytes |
|---|---|---|---:|---:|
| Eigen | 8a1083e9bf41b91fdea6546681f806154efdc25a | `tpr/sources/eigen-8a1083e9bf41b91fdea6546681f806154efdc25a` | 1 | 3,024,705 |
| Embree | 4.4.1 / v4.4.1 | `tpr/sources/embree-4.4.1` | 3 | 71,461,018 |
| Abseil | 20250814.1 | `tpr/sources/abseil-20250814.1` | 1 | 2,235,716 |
| Ceres | 0c70ed3a1a2d6ba47c06c7e8b3b040880bc474db | `tpr/sources/ceres-0c70ed3a1a2d6ba47c06c7e8b3b040880bc474db` | 1 | 3,476,732 |
| glog | 0.4.0 / 96a2f23dca4cc7180821ca5f32e526314395d26a | `tpr/sources/glog-0.4.0` | 1 | 201,153 |
| gflags | 2.2.1 / 46f73f88b18aee341538c0dfc22b1710a6abedef | `tpr/sources/gflags-2.2.1` | 1 | 97,058 |

Each directory's manifest fixes the URL, version/commit, unchanged archive SHA-256, size, original named license paths and per-part hashes. The four primary inputs match the formal Blender versions file's hashes. The glog/gflags commits come from the formal vendored libraries' upstream baseline READMEs; their official commit-archive SHA-256 values are locally frozen source digests, not fabricated entries in the formal versions file.

Embree parts are 33,554,432 + 33,554,432 + 4,352,154 bytes, all <=32 MiB. Concatenation reconstructs the original ZIP byte for byte, with SHA-256 `643b87490b207475872b0721019ebe3947feb9104f36c25795455516e4e85b82` and formal MD5 `6e2eecafb312d8cf1f1ff555702637cf`. Existing source registry entries and attributes were preserved. The parts use ordinary Git attributes with filter/diff/merge/text unset; no new LFS input is required.

## Original notices and source patches

[Provenance](./provenance.json) identifies the original source paths and exact hashes of 23 named license/copyright/author/notice files mirrored under each registry directory's licenses subtree. All other embedded copyright text and every complete original source file remain in the unchanged archives. License fields identify the primary dependency license, not a claim that every bundled source has that one license.

For example, Eigen also retains the original GPL version2 text for its BTL benchmark sources and its Apache/BSD/MINPACK copying files. Embree retains the MIT ImGui tutorial notice and all its original DPCPP/OIDN/TBB/oneAPI notices even though tutorials/SYCL are disabled in the current development configuration. glog retains both copyright holders in its original COPYING and its AUTHORS/CONTRIBUTORS files. Do not drop original license differences when assembling the final runtime-selected notice bundle.

[Provenance](./provenance.json) separately records three adaptations, their original patch hashes, optional replay-copy hashes, and each changed source file's before/after hash:

- The exact formal Eigen TBB patch. Its replay copy has the same bytes/hash.
- The exact formal Embree patch. Its replay copy only adds the missing final newline; `git apply --recount` respects the actual short final hunk. The original patch bytes are preserved.
- The OHOS-only Embree scheduler patch: actually supported current-thread sched affinity replaces unavailable pthread affinity APIs. Affinity is set inside the new worker, rather than guessing a tid from a pthread handle. The unused internal force-cancel destroyThread operation explicitly fails because the SDK lacks pthread_cancel; the selected TBB path has no caller.

The three patches were applied to independent pristine source file copies, checked against every sealed after-hash, then reversed and checked against original file hashes. The original archives never contain local source changes. Ceres, Abseil, gflags and glog have no additional core source patch in this handoff.

## Source-only offline use

Run from a checkout root:

```sh
python3 build_files/ohos/deps_core_sources/materialize_core.py --verify-only
python3 build_files/ohos/deps_core_sources/materialize_core.py \
  --output-dir "$XDG_CACHE_HOME/blender-ohos-original-core-archives"
```

The [source materializer](./materialize_core.py) resolves all registry/notice/patch paths from its own repository root, checks their identities and hashes, and delegates reconstruction to the existing [vendor archive tool](../vendor_archive.py). It accepts private XDG_CACHE_HOME/TMPDIR outputs, refuses conflicting contents, and performs no download, extraction, configuration, compilation, installation, signing or package modification. It can select a subset with `--libs`.

The six source archives were independently reconstructed under TMPDIR, compared byte for byte with the six frozen original files, and their 6,570 original file hashes were re-read and matched against the complete original member inventory. The 23 notice mirrors and three forward/reverse patch sequences were verified. This proves source delivery integrity, not the final dependency or Blender build.

## Future build integration

[Candidate recipe](./recipe.candidate.json) records the relevant native development configuration and unresolved integration work. Its symbolic paths are requirements for a future repository-owned builder, not runnable developer paths. In particular, the future builder must:

1. Supply and verify the real native Clang20/lld20 toolchain, SDK libc++15004 __n1/runtime inputs, signer, CMake/Ninja and separately frozen real TBB closure.
2. Extract complete pristine sources to private case-sensitive directories, apply the recorded patch sequences with digest checks, and generate target configuration using genuine signed native compiler/linker/run probes.
3. Provide the actually verified whole-archive feature to Ceres and its downstream consumers so Abseil flag registration is retained. Do not delete Ceres' WHOLE_ARCHIVE expression or preset a successful probe.
4. Build/install into new caller-owned prefixes under nice10, j2, compile2/link1 pools and lld threads2; finish relocatable metadata and runtime-selected notice packaging.
5. Run native Eigen/Embree/Ceres/logging, shared PIC/ABI/exception and moved-prefix consumers, then validate actual Blender Cycles CPU/libmv and HAP usage separately.

The current development choices retain Embree's native ARM NEON, ray masks/filters and TBB scheduler; NEON2x, SYCL, ISPC and tutorials are not enabled. Ceres retains EigenSparse/Schur/custom BLAS; CUDA/SuiteSparse/LAPACK/METIS/Apple Accelerate are not enabled. Preserve these explicit capability boundaries instead of treating the presence of complete source archives as a proof of all optional backend support.

Development evidence elsewhere may contain absolute `/storage/Users/currentUser/...` or `/data/storage/el2/base/haps/entry/cache/...` locations. Those identify this device's prior experiments and artifact receipts. They are not routes the future portable builder may depend on. This directory's provenance uses repository-relative source paths and the candidate requires caller-declared private paths and development prerequisites.
