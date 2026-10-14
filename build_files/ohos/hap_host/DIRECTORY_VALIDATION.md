# Directory, network and browser repair — host.17 acceptance

The host.15 HAP declared no requested permissions. Its ordinary Blender file
browser uses POSIX directory paths and the generic Unix XDG bookmark loader;
the separate provider-backed System Files menu does not authorize those paths.
Python `webbrowser` also had no HarmonyOS system-browser controller.

## Implementation

The [module manifest](deveco/entry/src/main/module.json5) now declares INTERNET
and the Desktop, Documents and Download directory permissions. The three directory
permissions have localized reasons and an EntryAbility in-use scene.

[Permissions.ets](deveco/entry/src/main/ets/runtime/Permissions.ets) checks the actual
application token, requests only missing grants, checks again, obtains each path
from Core File Kit `Environment`, then performs stat and a bounded directory list.
Only granted and successfully enumerated paths enter the regenerated app-private
XDG map. The native host sets XDG_CONFIG_HOME before starting any engine thread.
Denied entries are omitted rather than represented by empty paths; generic Unix
fallback shortcuts remain. A warning identifies unavailable grants.

The actual accepted paths are:

```text
XDG_DESKTOP_DIR="/storage/Users/currentUser/Desktop"
XDG_DOCUMENTS_DIR="/storage/Users/currentUser/Documents"
XDG_DOWNLOAD_DIR="/storage/Users/currentUser/Download"
```

Embedded Python skips Blender's desktop portable-CA setup. The
[native host](deveco/entry/src/main/cpp/host_bridge.cc) supplies SSL_CERT_FILE from
the pinned certifi bundle in the relocated runtime, preserving an explicit
existing override. TLS verification is not disabled. No proxy address is embedded
in the product and the user's online-access preference is not changed.

The [startup module](deveco/entry/src/main/cpp/types/libblender_host/blender_hap_documents.py)
registers the default CPython 3.13 browser controller. It initializes the registry
before public registration, preventing Linux `xdg-settings` discovery and process
fallback. It retains the standard audit event, encodes strict UTF-8, and submits
owned URL bytes and their length to an independent native queue. The queue admits
one HTTP/HTTPS URL, at most 8192 bytes, rejects controls/NUL, checks lifecycle state
under its mutex, and clears on shutdown. Ordinary invalid/busy requests do not
fail the Blender engine.

The [ArkTS page](deveco/entry/src/main/ets/pages/Index.ets) consumes requests only
while ready and focused, then calls UIAbilityContext.openLink with
appLinkingOnly=false. SDK failures produce a persistent generic message and a
numeric log code, without logging the URL. Queue acceptance and OS launch are
separate asynchronous results. Explicit external-browser controllers and other
URL schemes are outside this implementation.

## Signing and installed-device evidence

API26 defines Desktop as system_basic/user_grant and Documents/Download as
normal/user_grant; all three have provisionEnable=true. Declaration, profile
approval and runtime grant are separate checks. The user regenerated the profile;
the accepted profile has normal APL and Desktop in allowed-acls. The certificate
public-key fingerprint changed. Do not describe this update as retaining the old
signing certificate. Update installation nevertheless retained the existing UID,
access token and application data on this device. This does not establish
signature-rotation compatibility elsewhere.

The accepted host.17 HAP passed a fresh five-source native build/sign, ArkTS
compilation, official HAP verification, exact runtime-manifest parity and all 94
native-library byte checks. Core bytes remain unchanged:
`5b659f4e3b64c1da64deec60e00d129fd71f8c7b6c7990d0a1d093b774a3c443`.
The installed application reported all four permissions granted and engine ready.
The three-directory SDK stat/list probes generated the paths above.

On 2026-10-14, the user separately confirmed these native GUI checks on
MOR-M2/Maleoon935, API26:

- Desktop, Documents and Download shortcuts: “三个都正常”.
- Help-menu system browser launch: “能打开浏览器”.
- Online extension-list refresh: “能联网刷新”.

The [public acceptance record](host17-release-evidence.json) binds these responses
to the accepted HAP and native inputs. The
[browser regression](tests/test_browser_route.py) passed with the actual bundled
CPython 3.13.13, covering process-discovery prevention, aliases, UTF-8 byte length,
NUL rejection, queue refusal and unregister/re-register behavior.

Automatic console input did not produce a probe receipt. No automated TLS
handshake or automatic FileList selection success is claimed; the native GUI
acceptance above is user observation. Reading a terminal directory or merely
signing a HAP cannot substitute for that observation. Permission revocation,
rejected-grant UI, cross-device support and broad editing/rendering are not newly
accepted. The existing incremental diagnostic Core, strict OFF/libmv OFF, remains;
the earlier fresh full Core build was stopped at the user's request.
