# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender File menu entry for OHOS document providers; normal selectors remain usable."""
import bpy
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

def _request(operation):
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
    return _host.blender_hap_request_document(int(operation))

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
    bpy.utils.register_class(WM_OT_ohos_document)
    bpy.utils.register_class(TOPBAR_MT_ohos_documents)
    bpy.types.TOPBAR_MT_file.append(_file_menu)

def unregister():
    bpy.types.TOPBAR_MT_file.remove(_file_menu)
    bpy.utils.unregister_class(TOPBAR_MT_ohos_documents)
    bpy.utils.unregister_class(WM_OT_ohos_document)
