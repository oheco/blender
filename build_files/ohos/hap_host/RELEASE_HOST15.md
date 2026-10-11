# Blender 5.2.2-ohos.15 development preview

The public version follows the shared adaptation format `<upstream>-ohos.<revision>`: **5.2.2-ohos.15**. `host.15` was an internal development-build identifier that was initially carried into publication. The canonical Release, project export and catalog latest now use the normalized name. Historical artifacts and download URLs remain available.

This is the user-confirmed resize/input preview. It retains ordinary resize settlement at 120ms and maximize/recover at 250ms, fixes physical-key ingress for the native XComponent, and preserves the working IME. The user confirmed resize, IME and shortcuts on MOR-M2/Maleoon935, API26. Complete Blender port acceptance remains open.

Release: https://github.com/oheco/blender/releases/tag/5.2.2-ohos.15 .
Source/default branch: https://github.com/oheco/blender/tree/ohos/5.2.2 .
Release source: https://github.com/oheco/blender/tree/5.2.2-ohos.15 .
Software directory: https://oheco.org/ (package `blender`).

## Downloads and signing

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `blender-5.2.2-ohos.15-signed.hap` | 419636693 | `ea6f3f99651cb27143034a999d4928bfffc00de5c13bef3ad12b9cb68e54f683` |
| `blender-app-5.2.2-ohos.15.zip` | 143771688 | `10fd703c38d6bf57272dadd53929d8f35073aed83c12f90b1d7bd90974ec4fff` |

The HAP is byte-identical to the installed and accepted development package; its signed embedded versionName remains the historical `5.2.2-ohos.host.15`, versionCode 15. The public filename and publication version are normalized without resigning the accepted binary. **Its debug/developer profile is restricted to seven already-authorized devices.** Other devices should export the project and configure their own signing/profile in DevEco.

The populated DevEco project now declares `versionName: 5.2.2-ohos.15`, versionCode 15. Its root directory, README and manifest use the same public version. It retains the same 94 signed ARM64 native libraries and 5197 runtime files, public host sources, matching ABI headers and license notices. No personal signing material or SDK installation path is supplied. The default build preserves the prebuilt native inputs (`strip: false`) and only compiles ArkTS/resources.

## Use

```sh
oo update
oo export blender@5.2.2-ohos.15 app --output './Blender App'
```

Open the exported directory in DevEco with API26 support, using official `command-line-tools` 26.0.0.201, Hvigor/plugin 7.26.8 and SDK26. Minimum compatible API is 22. Configure your own signing identity/profile, then build and install. Offline use needs the official wrapper's package cache prepared first. `oo export` downloads, verifies and unpacks the project; it does not build, sign or install a HarmonyOS application. No Blender CLI artifact is supplied.

For an authorized developer device:

```sh
hdc -t <device> install -r blender-5.2.2-ohos.15-signed.hap
```

The bundle remains `org.oheco.blender`. Keep the same signing identity when updating an existing installation to retain projects/settings. The original accepted update preserved UID and certificate identity. The naming correction does not install or uninstall anything on the user's device.

## Evidence and limits

[Original device/native evidence](host15-release-evidence.json) remains a historical record of the accepted build and its embedded name. Official HAP verification passed; the fresh native host build bound 23 Core imports, and all 94 packaged libraries preserved their staged bytes. The user confirmations and prior scoped input/resize checks remain applicable to these unchanged native inputs.

The earlier project passed moved-directory ArkTS/resource packaging, official signing/verification and native byte parity. For the naming correction, the new project archive is checked for CRC, normalized metadata and complete native/runtime byte parity against the historical project. No new runtime feature is claimed and no full Core or broad device test is started.

Core SHA-256 remains `5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443`, an incrementally built diagnostic embedded Core, strict OFF/libmv OFF. Complete editing, picker, rendering and cross-display candidate-placement acceptance remain open. The Maleoon tile-blend trial is limited to the reviewed cohort. API22 is a compatibility declaration, not a device-matrix claim.

## Historical naming

The original [host.15 publication](https://github.com/oheco/blender/releases/tag/5.2.2-ohos.host.15) and all its files remain immutable for existing links and explicit historical exports. Its release title/body identify the normalized public version and redirect readers here. Catalog latest points to `5.2.2-ohos.15`; historical records are retained without rewriting their version/hash pairs.

## Re-export

The preview exporter reads the immutable accepted HAP and successful native/signing receipts, verifies source/library hashes, and emits a newly named project:

```sh
python3 build_files/ohos/export_host_preview.py \
  --hap /path/to/accepted-signed.hap \
  --hap-receipt /path/to/packaging-receipt.json \
  --host-receipt /path/to/native-receipt.json \
  --output /path/to/new/blender-app-5.2.2-ohos.15.zip
```

Existing artifacts are never overwritten. This preview exporter is separate from full Core portable-export acceptance. Blender and the host are GPL-2.0-or-later; the archive retains third-party notices and the adaptation repository provides source and pinned dependency archives.
