# Real native bpy acceptance

The real five-stage bpy algorithm and staging/cleanup algorithm are copied from the previously prepared acceptance sources. The bpy program is unchanged. The copied driver replaces PATH/Git source identification with the caller's immutable complete source snapshot, so a portable exported checkout does not need Git metadata. Its stages remain model, reopen, OBJ, ordinary uncompressed GLB, and actual Cycles CPU rendering. Native process exit zero and the matching stage/root PASS report are both required.

The editor entry supplies all source, Python, SDK system-library, diagnostic and final core inputs explicitly. Staging checks signed ELF bytes and relative RPATHs and copies unchanged payloads into a private TMPDIR root. Results survive payload cleanup. This code has not executed native Blender in the candidate verification.

Compressed Draco/meshoptimizer bpy import and export have their own explicit adapter and receipt. They are additional gates. Core dlopen, interactive SDL/window behavior, HAP loader/signature/install, WSI/GPU/input/lifecycle and Cycles GPU acceptance are independent from these background stages.
