// SPDX-License-Identifier: GPL-2.0-or-later
import { common } from '@kit.AbilityKit';
import { fileIo as fs, hash } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import native from 'libblender_host.so';

interface RuntimeFile { path: string; blob: string; sha256: string; size: number; }
interface NativeBinding { path: string; library: string; }
interface Manifest {
  schema: number; blenderVersion: string; pythonVersion: string; id: string;
  files: RuntimeFile[]; native: NativeBinding[];
}
interface Marker { id: string; nativeRoot: string; }
export interface HostPaths { runtime: string; config: string; cache: string; temp: string; }
function exists(path: string): boolean { try { return fs.accessSync(path); } catch (_) { return false; } }
function mkdir(path: string): void { if (!exists(path)) { fs.mkdirSync(path, true); } }
function relative(path: string): void {
  if (path.length === 0 || path.startsWith('/') || /[\u0000-\u001f]/.test(path)) { throw new Error('Invalid runtime path'); }
  path.split('/').forEach((part: string) => {
    if (part.length === 0 || part === '.' || part === '..') { throw new Error('Runtime path escapes private root'); }
  });
}
function parent(path: string): string { return path.substring(0, path.lastIndexOf('/')); }
function cleanup(path: string): void {
  if (!exists(path)) { return; }
  if (fs.lstatSync(path).isDirectory()) { fs.rmdirSync(path); } else { fs.unlinkSync(path); }
}
function write(path: string, text: string): void {
  if (exists(path)) { throw new Error('Owned staging file already exists'); }
  const file = fs.openSync(path, fs.OpenMode.CREATE | fs.OpenMode.WRITE_ONLY | fs.OpenMode.NOFOLLOW);
  try { fs.writeSync(file.fd, text); } finally { fs.closeSync(file); }
}
async function rootToken(context: common.UIAbilityContext, root: string): Promise<string> {
  const file = `${context.tempDir}/blender-native-root-${util.generateRandomUUID(true)}.txt`;
  try { write(file, root); return (await hash.hash(file, 'sha256')).toLowerCase(); }
  finally { cleanup(file); }
}
async function copyBlob(context: common.UIAbilityContext, row: RuntimeFile, target: string): Promise<void> {
  const raw = await context.resourceManager.getRawFd(row.blob);
  try {
    if (raw.length !== row.size) { throw new Error(`Resource length mismatch: ${row.path}`); }
    fs.lseek(raw.fd, raw.offset, fs.WhenceType.SEEK_SET);
    mkdir(parent(target));
    if (exists(target)) { throw new Error('Duplicate owned staging destination'); }
    const file = fs.openSync(target, fs.OpenMode.CREATE | fs.OpenMode.WRITE_ONLY | fs.OpenMode.NOFOLLOW);
    try {
      const buffer = new ArrayBuffer(1024 * 1024);
      let copied = 0;
      while (copied < row.size) {
        const count = await fs.read(raw.fd, buffer, { length: Math.min(buffer.byteLength, row.size - copied) });
        if (count === 0) { throw new Error(`Unexpected resource EOF: ${row.path}`); }
        let offset = 0;
        while (offset < count) {
          const written = await fs.write(file.fd, buffer.slice(offset, count));
          if (written === 0) { throw new Error('Runtime write made no progress'); }
          offset += written;
        }
        copied += count;
      }
    } finally { fs.closeSync(file); }
    if ((await hash.hash(target, 'sha256')).toLowerCase() !== row.sha256) { throw new Error(`Resource SHA-256 mismatch: ${row.path}`); }
  } finally { await context.resourceManager.closeRawFd(row.blob); }
}
let operation: Promise<HostPaths> | undefined;
async function prepare(context: common.UIAbilityContext): Promise<HostPaths> {
  const bytes = await context.resourceManager.getRawFileContent('runtime-manifest.json');
  const manifest = JSON.parse(new util.TextDecoder().decodeWithStream(bytes)) as Manifest;
  if (manifest.schema !== 1 || manifest.blenderVersion !== '5.2' || manifest.pythonVersion !== '3.13' ||
      !/^[a-f0-9]{64}$/.test(manifest.id) || !Array.isArray(manifest.files) || !Array.isArray(manifest.native) ||
      manifest.files.length === 0 || manifest.files.length > 100000 || manifest.native.length > 65536) {
    throw new Error('Invalid Blender/Python 3.13 runtime manifest');
  }
  const names = new Set<string>();
  manifest.files.forEach((row: RuntimeFile) => {
    relative(row.path);
    if (names.has(row.path) || !/^[a-f0-9]{64}$/.test(row.sha256) || row.blob !== `runtime/${row.sha256}` ||
        !Number.isSafeInteger(row.size) || row.size < 0 || row.size > 2 ** 32) { throw new Error('Invalid runtime resource entry'); }
    names.add(row.path);
  });
  manifest.native.forEach((row: NativeBinding) => {
    relative(row.path);
    if (names.has(row.path) || !/^lib[A-Za-z0-9_.-]+\.so$/.test(row.library)) { throw new Error('Invalid native module mapping'); }
    names.add(row.path);
  });
  const nativeRoot = context.applicationInfo.nativeLibraryPath;
  if (nativeRoot.length === 0 || !nativeRoot.startsWith('/') || /[\u0000-\u001f\u007f]/.test(nativeRoot)) {
    throw new Error('HAP native library directory is unavailable');
  }
  const base = `${context.filesDir}/blender/runtimes`;
  mkdir(base);
  const destination = `${base}/${manifest.id}-${await rootToken(context, nativeRoot)}`;
  const marker = `${destination}/.ready`;
  const valid = (): boolean => {
    if (!exists(marker)) { return false; }
    try {
      const ready = JSON.parse(fs.readTextSync(marker)) as Marker;
      return ready.id === manifest.id && ready.nativeRoot === nativeRoot;
    } catch (_) { return false; }
  };
  if (!valid()) {
    if (exists(destination)) { throw new Error('An incomplete published runtime requires inspection'); }
    const staging = `${context.tempDir}/blender-runtime-${util.generateRandomUUID(true)}`;
    try {
      mkdir(staging);
      for (const row of manifest.files) { await copyBlob(context, row, `${staging}/${row.path}`); }
      // The unobserved ArkUI array proxies never cross NAPI: create ordinary new arrays.
      const paths: string[] = []; const libraries: string[] = [];
      manifest.native.forEach((row: NativeBinding) => { paths.push(row.path); libraries.push(row.library); });
      native.bindLibraries(staging, nativeRoot, paths, libraries);
      // The OS-installed directory owns ELF files. Only a path registry goes into
      // app data; CPython's site bootstrap loads extensions directly from the HAP.
      let registry = `BLENDER_HAP_NATIVE_V1\n${nativeRoot}\n`;
      manifest.native.forEach((row: NativeBinding) => { registry += `${row.path}\t${row.library}\n`; });
      write(`${staging}/5.2/python/lib/python3.13/site-packages/blender_hap_native_paths.tsv`, registry);
      if (!exists(`${staging}/5.2/python/lib/python3.13/encodings/__init__.py`) ||
          !exists(`${staging}/5.2/scripts/startup`) || !exists(`${staging}/5.2/datafiles`)) {
        throw new Error('Published runtime lacks required Blender/Python content');
      }
      write(`${staging}/.ready`, JSON.stringify({ id: manifest.id, nativeRoot: nativeRoot } as Marker));
      try { fs.renameSync(staging, destination); }
      catch (error) { if (!valid()) { throw error as Error; } }
      if (!valid()) { throw new Error('Runtime publication failed'); }
    } finally { cleanup(staging); }
  }
  const paths: HostPaths = { runtime: destination, config: `${context.filesDir}/blender/config`,
    cache: `${context.cacheDir}/blender`, temp: `${context.tempDir}/blender` };
  mkdir(paths.config); mkdir(paths.cache); mkdir(paths.temp);
  return paths;
}
export async function prepareRuntime(context: common.UIAbilityContext): Promise<HostPaths> {
  if (operation !== undefined) { return operation; }
  operation = prepare(context);
  try { return await operation; } finally { operation = undefined; }
}
