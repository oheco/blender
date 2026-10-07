# Builder and adaptation lineage

The independent signing/private-filesystem/source-extraction helpers are
GPL-2.0-or-later snapshots of Blender OHOS core/volume builder helpers. Their
original SPDX statements are retained, and the new modules preserve this license.
Frozen volume files are read only and are not future imports/dependencies.
Blender's FindShaderC/FindVulkan retain their original BSD-3-Clause statements.

The functional Shaderc/SPIRV/Reflect, VUL borrowed-handle and system-loader
fixtures derive from the prior independently accepted Blender OHOS Vulkan
library work. The new candidate copies source and uses its own roots and adapters;
it never imports old scripts or reuses old compiled archives. The borrowed-handle
patch preserves the pinned upstream generator/source and existing Android/QNX
abstract-pointer policy; it is recorded separately with exact original/after SHA.

Complete original upstream archives and every original license/copyright/notice
remain unchanged under the new ordinary-part tpr entries. Header license text
inside individual source files is also retained by complete inventories. Formal
Blender license labels are recorded as baseline context, not substituted for the
full mixed glslang/SPIRV-Headers/Vulkan-header original declarations. SDK source
and compiler inputs are external prerequisites; their original runtime notice is
copied on full installation. No source/registry/global version or SDK is patched
in place by this candidate.
