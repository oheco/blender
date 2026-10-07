# Independent completed ShaderC source-closure receipt adapter

This separately sealed adapter bridges the frozen Vulkan full-receipt schema to the frozen geometry prerequisite schema. It changes neither builder or receipt. Its only accepted revision is Vulkan source lock `d3ff77af2e3ba566706dcb8f583b37674f39a31527a6c0f58dbc876ce8ecd4c0`. The inspected Vulkan full receipt has four fields and deliberately lacks `new_full_native_acceptance`; the adapter may add that field only to a NEW packet after checking complete independently finished native source/library evidence.

The current deliverable is SOURCE ONLY. Adapter native acceptance is NOT RUN. No C/C++ compilation, linking, SDK/compiler probe execution, live partial prefix inspection or network occurs during its short validation.

## Source-only inspection

`inspect` reads the adapter seal and nine reviewed immutable Vulkan source/schema files. It never opens the declared native receipt, prepared source or prefix trees, and creates only a NOTREADY inspection packet. Use an absent output beneath private XDG_CACHE_HOME. All temporary staging stays beneath TMPDIR; existing outputs, aliases, input overlap and resume are rejected.

```sh
python3 build_files/ohos/dependency_receipts/shaderc.py inspect \
  --source-root "$PWD/build_files/ohos/deps_vulkan_sources" \
  --source-lock-sha256 d3ff77af2e3ba566706dcb8f583b37674f39a31527a6c0f58dbc876ce8ecd4c0 \
  --output "$XDG_CACHE_HOME/shaderc-receipts/source-inspection-1" \
  --tmp-dir "$TMPDIR"
```

Run from the Blender repository root. An inspection packet has no accepted boolean and cannot satisfy geometry.

## Required actual executor envelope

The parent must collect the independently completed native full job and create a separate real executor envelope from its actual result. Pending jobs do not qualify. Pin the envelope bytes with `--job-receipt-sha256`; all paths are explicit absolute canonical paths. Required exact fields, with no extra or duplicate fields:

```json
{
  "schema_version": 1,
  "kind": "dsh-completed-native-vulkan-full",
  "status": "completed",
  "job_id": "bash-<actual-collected-job-id>",
  "exit_code": 0,
  "command": ["<actual-python>", "<selected-source-root>/builder/builder.py", "full", "<every-actual-argument-in-order>"],
  "cwd": "<actual-executor-working-directory>",
  "source_root": "<selected-frozen-source-root>",
  "receipt_root": "<actual-completed-native-work-root>",
  "prefix": "<actual-canonical-native-prefix>",
  "input_lock_sha256": "d3ff77af2e3ba566706dcb8f583b37674f39a31527a6c0f58dbc876ce8ecd4c0",
  "stdout_log": "<new-regular-file-containing-actual-full-job-stdout>",
  "stdout_sha256": "<actual-stdout-file-sha256>",
  "command_log_dir": "<receipt-root>/logs/<actual-full-run-UUID>"
}
```

The actual full argv must explicitly select root, prefix, SDK, resource directory and signer. Archive/readelf/nm tools must match explicit argv or the pinned SDK defaults. The final nonempty stdout line must be `PASS actual requested stage: full`; that marker alone cannot qualify. The one full-run log UUID is mandatory to prevent earlier resumed/partial run logs from masquerading as this completion.

## Future native receipt adaptation

Only after the real completed exit0 envelope exists, explicitly initialize the following variables from that job and its prerequisite receipt: GPU_SOURCE_ROOT, GPU_FULL_ROOT, GPU_PREFIX, GPU_JOB_RECEIPT, GPU_JOB_RECEIPT_SHA256, GPU_SDK_ROOT, GPU_RESOURCE_DIR, GPU_AR, GPU_AR_SHA256, GPU_NM, GPU_NM_SHA256, GPU_READELF, GPU_READELF_SHA256, GPU_SIGNER, GPU_SIGNER_SHA256. No default old cache, foreign input directory or inferred partially built prefix is used.

```sh
python3 build_files/ohos/dependency_receipts/shaderc.py accept \
  --source-root "${GPU_SOURCE_ROOT:?}" \
  --source-lock-sha256 d3ff77af2e3ba566706dcb8f583b37674f39a31527a6c0f58dbc876ce8ecd4c0 \
  --receipt-root "${GPU_FULL_ROOT:?}" --prefix "${GPU_PREFIX:?}" \
  --job-receipt "${GPU_JOB_RECEIPT:?}" --job-receipt-sha256 "${GPU_JOB_RECEIPT_SHA256:?}" \
  --sdk-root "${GPU_SDK_ROOT:?}" --resource-dir "${GPU_RESOURCE_DIR:?}" \
  --ar "${GPU_AR:?}" --ar-sha256 "${GPU_AR_SHA256:?}" \
  --nm "${GPU_NM:?}" --nm-sha256 "${GPU_NM_SHA256:?}" \
  --readelf "${GPU_READELF:?}" --readelf-sha256 "${GPU_READELF_SHA256:?}" \
  --signer "${GPU_SIGNER:?}" --signer-sha256 "${GPU_SIGNER_SHA256:?}" \
  --output "$XDG_CACHE_HOME/shaderc-receipts/accepted-vulkan-1" --tmp-dir "$TMPDIR"
```

Missing, pending, unknown, changed or partial evidence is rejected before any accepted packet is published. The adapter reads frozen source seal/archive parts, all eight current complete prepared source inventories and original archives, root ownership, real compiler/SDK15 `15004/std::__n1` prerequisite bytes and exact resource overlay, whole current prefix bytes, original signed consumers/DSOs, actual archive AArch64 ELF64 REL members and defined compiler/optimizer/validator implementations, PIC compile databases and source origins. Its native audit executes only the explicitly hashed readonly ar/nm/readelf/signature-display tools after the completed-job gate. It does not execute consumers or compilers.

The pinned builder deliberately cleans both original/moved SPIRV temporary fixture directories and the migrated consumers/prefix. Their historical execution remains evidence from its five generated SPIRV SHA/size rows, four original/moved CONFIG/PC routes, 36 actual consumer JSON/runtime exit0 logs and 52 signed consumer/PIC ELF audit log sets. The adapter validates those actual command/stdout/signature records and cleaned scope; it does not claim to reread deleted files or rerun native migration. Current original consumers, installed DSOs and archives are independently reread and audited. No GPU pixels, WSI, device allocation, Blender/bpy, HAP, PIC dlopen/static C++ runtime ownership or release is accepted.

Success publishes NEW regular files `shaderc-accepted.json`, `prefix-files.json` and `evidence.json`; the accepted file is published last. The prefix layout and underlying source-lock hash satisfy geometry's existing binding branch. Pass the existing canonical GPU_PREFIX, the NEW adapter receipt and its actual SHA to geometry's `--shader-prefix`, `--shader-receipt`, `--shader-receipt-sha256`. The evidence packet records exact underlying paths/hashes, executor stdout, source groups, runtime records and current readonly artifact commands. Original source/native inputs remain readonly; no post-sign ELF mutation occurs.

Exit0 `inspect` means only a NOTREADY source contract was saved. Exit3 means NOTREADY actual native completion; exit2 means rejected schema/ownership/bytes/evidence. Only exit0 `accept` can emit an accepted packet. Existing outputs are never adopted or replaced.

Receipt schemas are derived from the pinned GPL-2.0-or-later Blender OHOS Vulkan builder/audit/toolchain/source/consumer sources whose nine review SHA256 values are retained in the adapter contract. Adapter implementation is independently authored GPL-2.0-or-later and imports only the Python standard library.
