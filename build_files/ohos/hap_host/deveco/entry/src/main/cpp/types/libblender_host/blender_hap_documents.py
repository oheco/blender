# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender File menu entry for OHOS document providers; normal selectors remain usable."""
import bpy
import sys
import webbrowser
from bpy.props import EnumProperty
from bpy.types import Menu, Operator

_OPERATIONS = (
    ('1', '打开系统 .blend 文件…', '通过系统文件选择器打开文档'),
    ('2', '保存副本到系统文件…', '通过系统文件选择器保存 .blend 副本'),
    ('3', '从系统文件导入 OBJ…', '导入 OBJ 几何'),
    ('4', '导出 OBJ 到系统文件…', '导出 OBJ 几何'),
    ('5', '从系统文件导入 GLB…', '导入 GLB'),
    ('6', '导出 GLB 到系统文件…', '导出 GLB'),
)
_host = None

def _native_host():
    global _host
    if _host is None:
        import ctypes
        import os
        import blender_hap_native
        registry = os.path.join(os.path.dirname(blender_hap_native.__file__), 'blender_hap_native_paths.tsv')
        with open(registry, encoding='utf-8') as stream:
            header = stream.readline().rstrip('\n')
            native_root = stream.readline().rstrip('\n')
        if header != 'BLENDER_HAP_NATIVE_V1' or not os.path.isabs(native_root):
            raise RuntimeError('原生库注册表无效')
        _host = ctypes.CDLL(os.path.join(native_root, 'libblender_host.so'))
        _host.blender_hap_request_document.argtypes = [ctypes.c_uint32]
        _host.blender_hap_request_document.restype = ctypes.c_int32
        _host.blender_hap_open_url.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        _host.blender_hap_open_url.restype = ctypes.c_int32
    return _host


def _request(operation):
    return _native_host().blender_hap_request_document(int(operation))


class _OHOSBrowser(webbrowser.BaseBrowser):
    def open(self, url, new=0, autoraise=True):
        sys.audit('webbrowser.open', url)
        if not isinstance(url, str) or '\x00' in url:
            raise webbrowser.Error('无效的网页链接')
        payload = url.encode('utf-8', 'strict')
        if _native_host().blender_hap_open_url(payload, len(payload)) != 0:
            raise webbrowser.Error('无法请求系统浏览器，请稍后重试')
        return True


def _register_browser():
    # Pinned CPython 3.13 register() otherwise probes xdg-settings before adding
    # a preferred controller. This HAP routes default browser calls through ArkUI.
    with webbrowser._lock:
        webbrowser._tryorder = []
        webbrowser._os_preferred_browser = None
        webbrowser.register('ohos-hap', None, _OHOSBrowser('ohos-hap'), preferred=True)


def _unregister_browser():
    with webbrowser._lock:
        webbrowser._browsers.pop('ohos-hap', None)
        # Keep an initialized list even during shutdown/reload, avoiding probes.
        webbrowser._tryorder = [name for name in (webbrowser._tryorder or [])
                               if name != 'ohos-hap']

class WM_OT_ohos_document(Operator):
    bl_idname = 'wm.ohos_document'
    bl_label = '系统文件'
    bl_description = '选择可授权给 Blender 使用的系统文档'
    operation: EnumProperty(items=_OPERATIONS)

    def execute(self, context):
        try:
            if _request(self.operation) != 0:
                self.report({'ERROR'}, '文件操作尚未就绪，请等待当前操作完成')
                return {'CANCELLED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}

class TOPBAR_MT_ohos_documents(Menu):
    bl_label = '系统文件'

    def draw(self, context):
        for operation, label, description in _OPERATIONS:
            self.layout.operator('wm.ohos_document', text=label).operation = operation

def _file_menu(self, context):
    self.layout.separator()
    self.layout.menu('TOPBAR_MT_ohos_documents')

def register():
    _register_browser()
    bpy.utils.register_class(WM_OT_ohos_document)
    bpy.utils.register_class(TOPBAR_MT_ohos_documents)
    bpy.types.TOPBAR_MT_file.append(_file_menu)

def unregister():
    bpy.types.TOPBAR_MT_file.remove(_file_menu)
    bpy.utils.unregister_class(TOPBAR_MT_ohos_documents)
    bpy.utils.unregister_class(WM_OT_ohos_document)
    _unregister_browser()
