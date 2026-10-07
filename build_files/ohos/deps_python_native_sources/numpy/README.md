# NumPy 2.3.4 offline native replay candidate

This module is source-only at delivery. New native configure, compile, linker probes, signing, NumPy runtime tests, Blender, and installed-HAP execution are **NOTRUN**. The copied accepted evidence describes earlier parent runs and is never reused as a new result or an input binary cache.

The accepted newer HAP recipe/receipt uses genuine native CPython 3.13.13 with SOABI `cpython-313-aarch64-linux-ohos` and actual SONAME `libpython3.13.so`. The older versioned NumPy prefix is provenance only. The replay consumes the newly built runtime inside the same fresh owned parent root. It rejects a versioned/aliased library and verifies the actual ELF SONAME; it never renames a versioned binary.

[module.py](module.py) exports the agreed common-parent API:

```python
plan(args, root, runtime_prefix)                 # read-only input/argv plan, no execution or creation
build(args, runner, root, runtime_prefix)        # future explicitly selected native full only
verify_sdk(args)                                # byte-only SDK pin checks, no tool execution
verify_inputs(expected_lock_sha256=None)       # own complete module seal; parent anchors lock SHA externally
```

`root` is the fresh parent root; the module exclusively publishes `root/numpy`. `runtime_prefix` is a distinct NEW prefix such as `root/runtime`. `args.numpy_sources` must map exactly the 12 registry IDs from [sources.lock.json](sources.lock.json) to common-prepared new source directories inside `root`, outside the runtime and NumPy output subtree. `args.numpy_verify_sources()` is the required no-argument common complete source/inventory guard, called before and after full. Common sourceprep reconstructs every complete tar/part inventory, retains licenses, and applies [the original patch](patches/numpy-vendored-meson-ohos.patch) only to new sources with the exact before/after and forward/reverse checks in [patch-record.json](patches/patch-record.json). The module does not extract, patch, mutate, or reuse accepted source trees.

Explicit common arguments are `sdk_root`, `cc`, `cxx`, `lld`, `ar`, `strip`, `ninja`, `signer`, `readelf`, `tmp_dir`, and `jobs` (1 or 2). SDK tools are the explicit accepted SDK15 tools: Clang/Clang++ 15.0.4, `ld.lld` under its correct invocation basename, LLVM ar/strip/readelf. The compiler/static libc++/SDK15 CRT/header/tool byte pins are in [sdk15.lock.json](sdk15.lock.json). SDK libc++ uses C++17 and static linking. LLVM20, forced probe results, fake cross/native machine values, and disabled math/SIMD/threading features are not substitutions. NumPy uses native Ninja, not make. `args.python` may be the parent's immutable bootstrap script interpreter; NumPy launchers and every native identity/Meson/Cython/acceptance command deliberately use `runtime_prefix/bin/python3.13`.

`runner.run(command, label, cwd=None, extra_env=None, timeout=None)` returns stdout and raises on a nonzero command exit. The common Runner must own/log command execution and scrub inherited Python/compiler/linker/loader variables before adding `extra_env` (in particular PYTHONHOME, LD_LIBRARY_PATH/LD_PRELOAD, CC/CXX/CFLAGS/CXXFLAGS/CPPFLAGS/LDFLAGS/LIBS, _PYTHON*, CONFIG_SITE and CONFIG_SHELL). Common ownership must validate/lock the fresh private cache root and explicit private TMPDIR before invoking `build`; module API is not a standalone authorization or ownership bypass. Common input guards anchor [files.lock.json](files.lock.json) externally; full additionally checks this independent module seal before any output mutation. Set `sys.dont_write_bytecode = True` before importing the module (or invoke the caller with `-B`) so imports preserve its frozen file set. Generated launchers live on the private executable filesystem, not HOME/hmdfs. Commands/tool paths are explicit; source helper paths resolve from this new module namespace.

The 12 original source sdists are NumPy 2.3.4, Cython 3.0.11, Meson 1.9.0, meson-python 0.18.0, setuptools 80.9.0, setuptools-scm 9.2.2, packaging 25.0, pyproject-metadata 0.9.1, tomli-w 1.2.0, wheel 0.45.1, build 1.3.0, and pyproject-hooks 1.2.0. All formal entries remain required even when a particular pure tool import is not exercised. The actual backend is **NumPy's complete vendored Meson 1.8.3**, not the separate Meson 1.9.0 sdist. The accepted pure-source `src`/root PYTHONPATH is retained for all tool sdists except NumPy/Meson. No wheel build/install/download or network step is performed. Meson uses `--wrap-mode nodownload`; pip is index-disabled.

[profile.json](profile.json) preserves exactly `-Dblas=none -Dlapack=none -Dallow-noblas=true` with release/no-strip/no-debug/no-bytecompile layout. This is supported by the accepted actual build options and upstream source: `numpy/linalg/meson.build` selects all eight bundled f2c BLAS/LAPACK-lite source files when `not have_lapack`, and both real `lapack_lite` and `_umath_linalg` use them. It retains Highway, native threading and the original CPU baseline/dispatch defaults. There is no external BLAS/LAPACK/Fortran provider; performance may be much slower than optimized OpenBLAS. Full verifies the observed Meson feature profile instead of merely assuming flags took effect.

[sign_compiler.py](sign_compiler.py) derives from the accepted signing launcher and preserves real compiler identity/probes. It signs actual linked ELF probes before return, adds unversioned libpython to OHOS shared-extension links, and adds a Python-prefix RPATH only to native configure executables that explicitly link Python. Meson install/depfixer operates only on unique private temporary copies of never-executed inodes. Exactly 19 final extensions are signed after all edits, audited, and atomically published into `root/numpy/install`. All original source-tool license files and SDK NOTICE are copied into the new install. No final extension has RPATH/RUNPATH, TEXTREL, C++ runtime DSO, or old versioned Python dependency. A failed/partial NumPy subtree is not reused; select a fresh parent root for replay.

[acceptance.py](acceptance.py) performs actual dtype/shape/strided/endian-buffer, bundled LAPACK solve/inverse/SVD/eigh/determinant/singular-error, real/complex pocketfft, random distributions and every real bit-generator family, trig/exp/log, floating-point error/subnormal/rounding, ctypes pointer/ffi callback, Python/C array-interface capsule and PEP3118 shared ownership, regular GIL entry and threaded LAPACK/FFT checks. It imports and binds all 19 extension origins and reads signed AArch64 ELF metadata with [elf.py](elf.py), without subprocess/readelf execution. Native loader enumeration and PyRuntime symbol comparison reject a second/versioned libpython. Runtime origin records both actual package/library paths and the newly generated NumPy build configuration.

Upstream `get_fpu_mode()` returns None on AArch64 (its implemented control-word branch is x86/MSVC). The acceptance uses actual libc `fegetround`, subnormal survival, NumPy ufunc exceptions, and before/after error/rounding preservation; None is not FPU evidence.

Future terminal acceptance is invoked by `build`. For a real packaged HAP, copy this helper, [elf.py](elf.py), and [profile.json](profile.json) together into sealed pure resources; let the common isolated PyConfig/trusted_native_importer configure the actual rawfile/native-lib paths before calling:

```python
result = acceptance.evaluate(
    actual_python_resource_prefix, actual_numpy_resource_prefix,
    actual_native_lib_dir / 'libpython3.13.so',
    context='hap-embedded', native_dir=actual_native_lib_dir,
    module_manifest=actual_layout_manifest)
```

The manifest uses the accepted `{"modules": {"fully.qualified.module": "flat-native-filename.so"}}` structure. The HAP caller owns package signing/installation/app.loadpolicy/launch evidence and binds this returned result to that real process. A terminal flat-layout invocation uses `context='terminal-hap-layout'` and is recorded separately. Neither context text nor a terminal test proves HAP installation. No new or old runtime acceptance was executed while authoring this candidate.
