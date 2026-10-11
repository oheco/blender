# Blender 5.2.2 HarmonyOS host.15 development preview

This is the user-confirmed resize/input development preview, not complete Blender port acceptance. It retains ordinary resize settlement at 120ms and maximize/recover at 250ms, fixes native physical-key ingress for the library-backed XComponent, and preserves the working IME implementation. The user confirmed resize smoothness, IME operation and shortcuts on MOR-M2/Maleoon935, API26.

Release: https://github.com/oheco/blender/releases/tag/5.2.2-ohos.host.15 .
Source/default branch: https://github.com/oheco/blender/tree/ohos/5.2.2 .
Exact release source: https://github.com/oheco/blender/tree/5.2.2-ohos.host.15 .
Software directory: https://oheco.org/ (package `blender`).

## Downloads and signing

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `blender-5.2.2-ohos.host.15-native-key-route-signed.hap` | 419636693 | `ea6f3f99651cb27143034a999d4928bfffc00de5c13bef3ad12b9cb68e54f683` |
| `blender-app-5.2.2-ohos.host.15.zip` | 143827441 | `d4b8653ddfb21f267931f168784ca63457ef98ff5cb39fd0169166008f51366e` |

The HAP is exactly the installed and accepted package, without resigning or modifying its native inputs. **Its profile is a debug/developer profile restricted to seven already-authorized devices. It is not a universally installable production HAP.** No private keys or passwords are supplied. Other devices should use the project ZIP and configure their own signing/profile in DevEco.

The ZIP is a relocatable, self-signable DevEco preview project with all 94 signed ARM64 native libraries, 5197 packaged runtime files, public host sources, matching ABI headers and license notices. It contains no personal signing configuration, certificates, provisioning profiles or SDK installation paths. The default build preserves the prebuilt native inputs (`strip: false`) and only rebuilds ArkTS/resources. The project manifest binds the exact inputs. It does not promise fresh full Core reproducibility or full feature acceptance.

## Use

Export the project from the official index:

```sh
oo update
oo export blender@5.2.2-ohos.host.15 app --output './Blender App'
```

Open the exported directory in DevEco with API26 support. The pinned build tools are official `command-line-tools` 26.0.0.201, Hvigor/plugin 7.26.8 and SDK26; minimum compatible API is 22. Set your signing configuration in DevEco, then build and install. Offline use requires the official wrapper's package cache prepared in advance. The export includes no SDK, build-tool binaries or credentials.

`oo export` downloads, verifies and unpacks the project; it does not build, sign or install a HarmonyOS application. This package has no Blender CLI installation artifact, and `oo install blender` is not an app installer.

For an authorized developer device, upgrade the downloaded pre-signed HAP in place:

```sh
hdc -t <device> install -r blender-5.2.2-ohos.host.15-native-key-route-signed.hap
```

The bundle is `org.oheco.blender`, versionCode 15. Keep the same signing identity when upgrading an existing installation so projects and settings remain accessible. The accepted update preserved the existing UID and certificate fingerprint; no uninstall/data purge was used. A different signing identity cannot replace the existing bundle as a preserving upgrade.

## Validation and limits

[Public device/native evidence](host15-release-evidence.json) records official HAP signature verification, fresh five-translation-unit native host build/signing, 23 Core imports bound, complete 94-library byte parity and preserving device installation. It includes the actual user confirmations. Prior scoped checks covered eight physical-key/modifier cases and the shared resize/retirement helpers. The separately exported project was checked after extraction into a directory containing spaces, using official ArkTS packaging/signature verification while preserving all native bytes and the runtime manifest. Release attachments include the project-validation summary and SHA-256 checksums.

The Core remains the accepted **incremental diagnostic embedded Core, strict OFF/libmv OFF**, SHA-256 `5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443`. Its embedded old Git HEAD is the historical base, not the complete dirty resize source identity; the release commit records the later frozen patch. A fresh full Core build was stopped at the user's request and is not claimed. This release does not restart broad testing.

Complete editing, document-picker, rendering and cross-display candidate-placement acceptance remain open. The Maleoon tile-blend trial is limited to the reviewed cohort; the results do not establish support for every GPU, device or system version. Minimum API22 is the declaration required by the used SDK callbacks, not a device-matrix claim. The source-only template and historical host.6 inventory remain distinct from this populated preview project.

## Re-export

The exporter requires the immutable accepted HAP and successful native/signing receipts. It verifies source hashes and library parity and excludes the private signing project:

```sh
python3 build_files/ohos/export_host_preview.py \
  --hap /path/to/blender-5.2.2-ohos.host.15-native-key-route-signed.hap \
  --hap-receipt /path/to/host15-packaging-receipt.json \
  --host-receipt /path/to/host15-native-receipt.json \
  --output /path/to/new/blender-app-5.2.2-ohos.host.15.zip
```

Existing artifacts are never overwritten. This preview exporter is separate from the full native/Core portable-export acceptance gates. Source and pinned dependency archives are available in the adaptation repository; Blender and the host are GPL-2.0-or-later, and the archive retains third-party notices.
