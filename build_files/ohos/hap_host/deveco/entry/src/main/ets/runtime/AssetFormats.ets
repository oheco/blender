// SPDX-License-Identifier: GPL-2.0-or-later
import { util } from '@kit.ArkTS';
interface GLBResource { uri?: string; }
interface GLBDocument { buffers?: GLBResource[]; images?: GLBResource[]; }
// Pure container/dependency validation. This does not substitute for a Blender import.
export function glbJSONLength(header: ArrayBuffer, fileSize: number): number {
  if (header.byteLength !== 20) { throw new Error('GLB文件头不完整'); }
  const view = new DataView(header);
  const length = view.getUint32(12, true);
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 ||
      view.getUint32(8, true) !== fileSize || view.getUint32(16, true) !== 0x4e4f534a ||
      length < 2 || length > 8 * 1024 * 1024 || length % 4 !== 0 || length + 20 > fileSize) {
    throw new Error('请选择有效的GLB 2.0单文件资产（描述信息上限8MB）');
  }
  return length;
}
export function glbSelfContained(json: Uint8Array): void {
  const document = JSON.parse(new util.TextDecoder('utf-8', { fatal: true }).decodeWithStream(json)) as GLBDocument;
  const groups: GLBResource[][] = [document.buffers ?? [], document.images ?? []];
  groups.forEach((resources: GLBResource[]) => {
    if (!Array.isArray(resources)) { throw new Error('GLB资源描述无效'); }
    resources.forEach((resource: GLBResource) => {
      if (resource.uri !== undefined && (typeof resource.uri !== 'string' || !resource.uri.startsWith('data:'))) {
        throw new Error('此GLB引用外部文件，请先将贴图和缓冲区嵌入GLB再导入');
      }
    });
  });
}
// Streaming byte filter: retain geometry/UV/normals/names byte-for-byte, remove
// mtllib/usemtl lines, and never follow OBJ material paths or re-encode names.
export class OBJGeometryFilter {
  private mode: number = 0; // 0 prefix, 1 pass, 2 drop until newline
  private prefix: string = '';
  feed(data: Uint8Array, final: boolean = false): Uint8Array {
    const output = new Uint8Array(data.length + this.prefix.length);
    let written = 0;
    for (let i = 0; i < data.length; ++i) {
      const value = data[i];
      if (this.mode === 1) { output[written++] = value; if (value === 10) { this.mode = 0; } continue; }
      if (this.mode === 2) { if (value === 10) { output[written++] = value; this.mode = 0; } continue; }
      this.prefix += String.fromCharCode(value);
      if (this.prefix.length > 4096) { throw new Error('OBJ行前缀过长'); }
      const token = this.prefix.replace(/^[ \t\v\f\r]+/, '').toLowerCase();
      if (/^(mtllib|usemtl)[ \t\v\f\r\n]/.test(token)) {
        this.prefix = ''; this.mode = value === 10 ? 0 : 2;
        if (value === 10) { output[written++] = 10; }
      } else if (value === 10 || (token.length > 0 && !'mtllib'.startsWith(token) && !'usemtl'.startsWith(token))) {
        for (let j = 0; j < this.prefix.length; ++j) { output[written++] = this.prefix.charCodeAt(j); }
        this.prefix = ''; this.mode = value === 10 ? 0 : 1;
      }
    }
    if (final) {
      const token = this.prefix.replace(/^[ \t\v\f\r]+/, '').toLowerCase();
      if (token !== 'mtllib' && token !== 'usemtl') {
        for (let j = 0; j < this.prefix.length; ++j) { output[written++] = this.prefix.charCodeAt(j); }
      }
      this.prefix = ''; this.mode = 0;
    }
    return output.slice(0, written);
  }
}
