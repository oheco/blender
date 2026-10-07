# Builder and consumer provenance

io_utils.py, vendor_archive.py, toolchain.py, launcher.py, source_tree.py,
audit.py, builder.py and the native platform/preflight drivers are adapted from
Blender's existing GPL-2.0-or-later deps_volume_sources/builder and
 deps_core_sources/builder patterns, read only during this task. They are copied
into an independent new namespace and do not import or execute those builders.
The static Zlib CMake driver is adapted from original Zlib 1.3.1 CMake checks;
its SPDX Zlib declaration and pristine upstream notices remain intact.

color_acceptance.cpp is the exact original accepted native color consumer;
its old SHA256 is 46ad504c67c6410c29c92a2718ecf05b8272fe59f6f62838494c5d48c38ef10e.
The current source remains byte identical. Half/Imath/texture acceptance is new
GPL-2.0-or-later consumer code and is not prior native PASS evidence. TBB and
native SDK/affinity consumers are copied existing repository pattern code.

Six source patches are copied byte-for-byte from the immutable original color
source-patches.json. The new sources.lock.json records their exact SHA256,
repository-relative routing and original/patched file hashes; no patch content
or source license/SPDX declaration was fabricated. The pystring wrapper
retains the original Blender/OpenColorIO contributor copyright and BSD-3-Clause
declaration. All complete original archives and all named source notices retain
upstream copyright/license content, including differing bundled declarations.
