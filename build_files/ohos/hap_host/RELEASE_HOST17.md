# Blender 5.2.2-ohos.17 development preview

This revision fixes access to Desktop/Documents/Download, enables the application's
network permission, configures the relocated Python CA bundle, and routes default
HTTP/HTTPS links to the system browser. The user confirmed all three directory
shortcuts, browser opening and online extension-list refresh on MOR-M2/Maleoon935,
API26. Existing resize, shortcuts and IME repairs are retained.

- [Release](https://github.com/oheco/blender/releases/tag/5.2.2-ohos.17)
- [Source and default branch](https://github.com/oheco/blender/tree/ohos/5.2.2)
- [Release source](https://github.com/oheco/blender/tree/5.2.2-ohos.17)
- [Software directory](https://oheco.org/) — package `blender`

## Downloads

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `blender-5.2.2-ohos.17-signed.hap` | 419669994 | `9a986a6402679b80079895d8760bed0d2e47395e22e34cdddda58e423a1299de` |
| `blender-app-5.2.2-ohos.17.zip` | 143782689 | `536c42361fbffae4dcacc3ce0fccf1de0536c216d49f0151196612541d7edb05` |

The HAP is byte-identical to the installed and accepted package. Its embedded
version remains `5.2.2-ohos.host.17`, versionCode 17; the public filename and tag
use the normalized adaptation version without changing the accepted signature.
The pre-signed HAP has a developer profile limited to its authorized devices.
Other devices should export the project and configure their own signing/profile.

The project archive declares `5.2.2-ohos.17`, contains all 94 signed ARM64 libraries,
5198 rawfile payload members, the host sources, matching ABI headers and license
notices. Personal signing material, SDK installations and build caches are omitted.
The default build compiles ArkTS/resources and preserves the prebuilt native bytes
with `strip: false`.

## Export, build and update

```sh
oo update
oo export blender@5.2.2-ohos.17 app --output './Blender App'
```

Open the exported project in a DevEco version supporting API26, with official
command-line-tools 26.0.0.201, Hvigor/plugin 7.26.8 and SDK26. Minimum compatible API
is 22. Offline builds require the wrapper's pinned package cache prepared first.
Configure your own signing/profile, including approval for Desktop in its ACL and
the applicable Documents/Download provisioning requirements. Grant directory
access at startup. INTERNET has no user-grant dialog; Blender's online-access
preference remains under user control.

`oo export` verifies and unpacks the project; it does not build or install a HAP.
No Blender CLI is supplied. On an authorized developer device:

```sh
hdc -t <device> install -r blender-5.2.2-ohos.17-signed.hap
```

Keep the bundle `org.oheco.blender` and preserve the existing signing configuration
when updating. In this acceptance, the user regenerated signing material and the
certificate public key changed; the update nevertheless retained UID, token and
application data. Do not generalize that result to other devices or uninstall an
existing application merely to change signatures.

## Verification and limits

The [acceptance record](host17-release-evidence.json) and
[access repair details](DIRECTORY_VALIDATION.md) distinguish build checks, token
checks, SDK directory probes and the three native GUI confirmations. The bundled
CPython 3.13.13 browser regression passed, including prevention of Linux process
fallback. Native host compilation used a fresh directory; official HAP verification
and all 94 library/runtime-manifest byte checks passed.

Automatic console input did not generate a diagnostic receipt, so no automated
TLS-handshake or FileList-selection result is claimed. Native GUI networking and
browser use were accepted by the user. Rejected/revoked grants, broad editing and
rendering, cross-display behavior and other devices remain outside this acceptance.
The browser bridge covers default HTTP/HTTPS URLs up to 8192 UTF-8 bytes.

Core SHA-256 stays
`5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443`:
an incrementally built diagnostic embedded Core, strict OFF/libmv OFF. No fresh
full Core build is claimed; that earlier build was stopped at the user's request.
This remains a development preview. Historical releases and URLs stay immutable.

The [preview exporter](../export_host_preview.py) binds the accepted HAP and native
receipts. The repository contains the corresponding source and pinned dependency
inputs; Blender and the host are GPL-2.0-or-later, with third-party notices retained.
