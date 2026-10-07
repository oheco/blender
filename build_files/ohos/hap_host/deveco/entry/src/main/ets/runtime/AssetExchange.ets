// SPDX-License-Identifier: GPL-2.0-or-later
import { common } from '@kit.AbilityKit';
import { fileIo as fs, picker } from '@kit.CoreFileKit';
import { promptAction } from '@kit.ArkUI';
import { util } from '@kit.ArkTS';
import native from 'libblender_host.so';
import { HostPaths } from './Runtime';
import { glbJSONLength, glbSelfContained, OBJGeometryFilter } from './AssetFormats';
export enum AssetOperation { OpenBlend = 1, SaveBlend = 2, ImportOBJ = 3, ExportOBJ = 4, ImportGLB = 5, ExportGLB = 6 }
interface AssetInfo { label: string; suffix: string; output: boolean; }
export function assetInfo(operation: AssetOperation): AssetInfo {
  switch (operation) {
    case AssetOperation.OpenBlend: return { label: '打开 .blend', suffix: '.blend', output: false };
    case AssetOperation.SaveBlend: return { label: '保存 .blend', suffix: '.blend', output: true };
    case AssetOperation.ImportOBJ: return { label: '导入 OBJ', suffix: '.obj', output: false };
    case AssetOperation.ExportOBJ: return { label: '导出 OBJ', suffix: '.obj', output: true };
    case AssetOperation.ImportGLB: return { label: '导入 GLB', suffix: '.glb', output: false };
    case AssetOperation.ExportGLB: return { label: '导出 GLB', suffix: '.glb', output: true };
    default: throw new Error('无效的资产操作');
  }
}
function chosen(uris: string[]): string | undefined {
  if (uris.length === 0) { return undefined; }
  if (uris.length !== 1 || uris[0].length === 0 || uris[0].length > 16384 || /[\u0000-\u001f\u007f]/.test(uris[0])) {
    throw new Error('文档选择结果无效');
  }
  // Keep the granted URI opaque. Do not decode its filename or turn it into an OS path.
  return uris[0];
}
function exists(path: string): boolean { try { return fs.accessSync(path); } catch (_) { return false; } }
async function directory(path: string): Promise<void> {
  if (!exists(path)) { await fs.mkdir(path, true); }
  const stat = await fs.lstat(path);
  if (!stat.isDirectory() || stat.isSymbolicLink()) { throw new Error('无法准备工作副本'); }
}
async function remove(path: string): Promise<void> {
  if (exists(path)) { await fs.rmdir(path); } // actual SDK recursive one-argument API
}
async function newFile(path: string): Promise<fs.File> {
  if (exists(path)) { throw new Error('工作副本已存在'); }
  return await fs.open(path, fs.OpenMode.CREATE | fs.OpenMode.WRITE_ONLY | fs.OpenMode.NOFOLLOW);
}
async function uriToPrivate(uri: string, path: string): Promise<void> {
  try {
    const source = await fs.open(uri, fs.OpenMode.READ_ONLY);
    try {
      const stat = await fs.stat(source.fd);
      if (!stat.isFile() || stat.size <= 0 || stat.size > 512 * 1024 * 1024) { throw new Error('请选择512MB以内的文件'); }
      const target = await newFile(path);
      try {
        await fs.copyFile(source.fd, target.fd);
        if ((await fs.stat(target.fd)).size !== stat.size) { throw new Error('文档拷贝不完整'); }
        await fs.fsync(target.fd);
      } finally { await fs.close(target); }
    } finally { await fs.close(source); }
  } catch (_) { throw new Error('无法读取所选文档，请检查授权、可用空间和512MB文件上限'); }
}
async function privateToURI(path: string, uri: string): Promise<void> {
  try {
    const source = await fs.open(path, fs.OpenMode.READ_ONLY | fs.OpenMode.NOFOLLOW);
    try {
      const size = (await fs.stat(source.fd)).size;
      if (size <= 0) { throw new Error('没有可保存的输出文件'); }
      const target = await fs.open(uri, fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        await fs.copyFile(source.fd, target.fd);
        if ((await fs.stat(target.fd)).size !== size) { throw new Error('写入文档不完整'); }
        await fs.fsync(target.fd);
      } finally { await fs.close(target); }
    } finally { await fs.close(source); }
  } catch (_) { throw new Error('文档写入失败，请重新选择保存位置；目标文件可能尚未写完'); }
}
async function exact(fd: number, length: number): Promise<ArrayBuffer> {
  const result = new Uint8Array(length);
  let offset = 0;
  while (offset < length) {
    const buffer = new ArrayBuffer(length - offset);
    const count = await fs.read(fd, buffer);
    if (count === 0) { throw new Error('GLB文件不完整'); }
    result.set(new Uint8Array(buffer, 0, count), offset); offset += count;
  }
  return result.buffer;
}
async function validateGLB(path: string): Promise<void> {
  const file = await fs.open(path, fs.OpenMode.READ_ONLY | fs.OpenMode.NOFOLLOW);
  try {
    const size = (await fs.stat(file.fd)).size;
    const length = glbJSONLength(await exact(file.fd, 20), size);
    glbSelfContained(new Uint8Array(await exact(file.fd, length)));
  } finally { await fs.close(file); }
}
async function writeAll(fd: number, bytes: Uint8Array): Promise<void> {
  let offset = 0;
  while (offset < bytes.length) {
    const count = await fs.write(fd, bytes.buffer.slice(offset, bytes.length));
    if (count === 0) { throw new Error('OBJ工作副本写入失败'); }
    offset += count;
  }
}
async function geometryOBJ(raw: string, path: string): Promise<void> {
  const source = await fs.open(raw, fs.OpenMode.READ_ONLY | fs.OpenMode.NOFOLLOW);
  try {
    const target = await newFile(path);
    try {
      const filter = new OBJGeometryFilter();
      const buffer = new ArrayBuffer(64 * 1024);
      while (true) {
        const count = await fs.read(source.fd, buffer);
        if (count === 0) { break; }
        await writeAll(target.fd, filter.feed(new Uint8Array(buffer, 0, count)));
      }
      await writeAll(target.fd, filter.feed(new Uint8Array(0), true));
      await fs.fsync(target.fd);
    } finally { await fs.close(target); }
  } finally { await fs.close(source); }
}
export class AssetExchange {
  private context: common.UIAbilityContext;
  private paths: HostPaths;
  private busy: boolean = false;
  private closed: boolean = false;
  constructor(context: common.UIAbilityContext, paths: HostPaths) { this.context = context; this.paths = paths; }
  close(): void { this.closed = true; }
  async run(operation: AssetOperation, status: (message: string) => void): Promise<boolean> {
    if (this.closed || this.busy) { throw new Error('请等待当前资产操作完成'); }
    const info = assetInfo(operation);
    const id = util.generateRandomUUID(true);
    if (!/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(id)) {
      throw new Error('无法创建工作副本');
    }
    const working = `${info.output ? this.paths.temp : this.paths.config}/exchange/${id}`;
    const temporary = `${this.paths.temp}/asset-${id}`;
    const file = `${working}/${info.output ? 'asset' : 'source'}${info.suffix}`;
    let holdInput = false;
    this.busy = true;
    try {
      const documents = new picker.DocumentViewPicker(this.context);
      if (!info.output) {
        status('请选择要打开或导入的文档');
        const uri = chosen(await documents.select({ maxSelectNumber: 1,
          selectMode: picker.DocumentSelectMode.FILE, fileSuffixFilters: [`${info.label}|${info.suffix}`] }));
        if (uri === undefined || this.closed) { return false; }
        if (operation === AssetOperation.OpenBlend) {
          const decision = await promptAction.showDialog({ title: '打开 Blender 工程',
            message: '当前场景将被替换。请先保存需要保留的更改。',
            buttons: [{ text: '取消', color: '#888888' }, { text: '打开', color: '#3282ff' }] });
          if (decision.index !== 1 || this.closed) { return false; }
        }
        status('正在读取文档');
        await directory(working);
        if (operation === AssetOperation.ImportOBJ) {
          await directory(temporary);
          const raw = `${temporary}/input.obj`;
          await uriToPrivate(uri, raw); await geometryOBJ(raw, file);
        } else { await uriToPrivate(uri, file); }
        if (operation === AssetOperation.ImportGLB) { await validateGLB(file); }
      } else { await directory(working); }
      if (this.closed) { return false; }
      status(`Blender 正在${info.label}`);
      const pending: Promise<number> = native.fileOperation(operation, file);
      // Keep input working copies after dispatch, including partial/error imports:
      // Blender image resources, active .blend path and undo/redo may reference them.
      holdInput = !info.output;
      const result: number = await pending;
      if (result === 1 || this.closed) { return false; }
      if (result !== 0) { throw new Error('Blender文件操作未完成'); }
      if (info.output) {
        if (operation === AssetOperation.ExportGLB) { await validateGLB(file); }
        if (this.closed) { return false; }
        status('请选择保存位置，可保存到其他应用能够访问的文档目录');
        // API15-compatible save options. autoCreateEmptyFile is API23; do not use it.
        const uri = chosen(await documents.save({ newFileNames: [`asset${info.suffix}`],
          fileSuffixChoices: [`${info.label}|${info.suffix}`] }));
        if (uri === undefined || this.closed) { return false; }
        status('正在保存文档'); await privateToURI(file, uri);
      }
      return true;
    } finally {
      // Temporary raw OBJ and export copies have no Blender-owned path references.
      // Cleanup errors surface as failures; release admission even if cleanup fails.
      try { await remove(temporary); if (!holdInput) { await remove(working); } }
      finally { this.busy = false; }
    }
  }
}
