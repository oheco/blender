# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with the bundled CPython 3.13, isolated from bpy and the user's session."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import webbrowser

assert sys.version_info[:2] == (3, 13), 'Use the pinned HAP Python runtime'
startup = Path(__file__).resolve().parents[1] / 'deveco/entry/src/main/cpp/types/libblender_host/blender_hap_documents.py'

# These environment values exercise every standard-browser discovery branch.
os.environ.update(DISPLAY=':browser-route-test', WAYLAND_DISPLAY='test', TERM='xterm',
                  BROWSER='forbidden-external-browser %s')

def forbid_process(*args, **kwargs):
    raise AssertionError('A HAP browser call attempted external-process discovery/launch')

subprocess.check_output = forbid_process
subprocess.Popen = forbid_process
shutil.which = forbid_process

bpy = types.ModuleType('bpy')
props = types.ModuleType('bpy.props')
props.EnumProperty = lambda **kwargs: None
bpy_types = types.ModuleType('bpy.types')
bpy_types.Menu = type('Menu', (), {})
bpy_types.Operator = type('Operator', (), {})
bpy.types = bpy_types
bpy.types.TOPBAR_MT_file = types.SimpleNamespace(append=lambda fn: None, remove=lambda fn: None)
bpy.utils = types.SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
sys.modules.update({'bpy': bpy, 'bpy.props': props, 'bpy.types': bpy_types})

spec = importlib.util.spec_from_file_location('hap_browser_route_under_test', startup)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Native:
    def __init__(self):
        self.calls = []
        self.result = 0

    def blender_hap_open_url(self, payload, length):
        assert isinstance(payload, bytes) and length == len(payload)
        self.calls.append(payload)
        return self.result

    def blender_hap_request_document(self, operation):
        assert operation == 1
        return 0

native = Native()
module._native_host = lambda: native
captured_open = webbrowser.open
module.register()
assert webbrowser._tryorder == ['ohos-hap']

urls = ['https://www.blender.org/?utm_source=blender-5.2.2',
        'http://127.0.0.1:3080/', 'https://example.invalid/中文']
for call, url in zip((webbrowser.open, webbrowser.open_new, webbrowser.open_new_tab), urls):
    assert call(url) is True
assert captured_open(urls[0]) is True
assert webbrowser.get().open_new_tab(urls[1]) is True
assert webbrowser.get('ohos-hap').open(urls[2], new=2, autoraise=False) is True
assert native.calls == [url.encode('utf-8') for url in urls + urls]
assert module._request(1) == 0

before = len(native.calls)
try:
    webbrowser.open('https://example.invalid/\x00suffix')
except webbrowser.Error:
    pass
else:
    raise AssertionError('Embedded NUL reached the C ABI')
assert len(native.calls) == before
native.result = 3
try:
    webbrowser.open(urls[0])
except webbrowser.Error:
    pass
else:
    raise AssertionError('Native queue refusal was silently reported as success')

module.unregister()
assert webbrowser._tryorder == []
assert webbrowser.open(urls[0]) is False
module.register()
assert webbrowser._tryorder == ['ohos-hap']
module.unregister()
print('PASS: pinned Python browser discovery, UTF-8 byte length, aliases, queue refusal, NUL and lifecycle')
