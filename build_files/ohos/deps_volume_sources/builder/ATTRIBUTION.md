# Driver and consumer lineage

The new standalone Python namespace uses GPL-2.0-or-later, following Blender's
repository helper license. io_utils.py, source_tree.py, toolchain.py, audit.py and
builder.py adapt contracts from deps_core_sources/builder. launcher.py and native
HarmonyOS CMake modules are copied from that GPL helper, without importing it or
modifying any sealed core file. vendor_archive.py is a GPL copy of the formal
ordinary-parts utility; it is sealed here and has no runtime work/cache import.

Consumer tests originate from the current development deps-volume/acceptance,
core native/affinity consumers and geometry TBB acceptance. The lineage receipt
in the new handoff records original hashes. The new FFTW test additionally
requires rejection of zero-length plans in both precisions. The new NanoVDB
stream regression preserves its Apache-2.0 header and oheco copyright; its exact
parent-validated source and canonical two-header patch are sealed here. The Zlib CMake
wrapper adapts native checks and the source list from the original Zlib 1.3.1
CMake recipe under its Zlib license; complete original source and notices remain
in the frozen formal archive.

Development scripts native.py, native-toolchain.cmake, install_metadata.py,
reused_metadata.py, stage_prefix.py, accept.py and probe.py were consulted read
only. Their absolute work/cache routing is not copied into this driver. The
copied 206 development prefix inputs and their original/derived metadata locks
are prototype evidence, not build prerequisites. Zstd is excluded from this
profile, so neither its inherited absolute .pc nor its old compiled archive
enters the new prefix. No original metadata/archive/SDK is rewritten.

Original upstream archive manifests retain their own URLs, formal version/hash,
license, copyright and notices. OpenVDB's archive MPL-2.0 and source Apache-2.0
statements are both retained; this driver does not adjudicate or rewrite them.
