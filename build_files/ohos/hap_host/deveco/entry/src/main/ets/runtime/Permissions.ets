// SPDX-License-Identifier: GPL-2.0-or-later
import { abilityAccessCtrl, bundleManager, common, Permissions } from '@kit.AbilityKit';
import { Environment, fileIo as fs } from '@kit.CoreFileKit';
import { hilog } from '@kit.PerformanceAnalysisKit';

interface UserDirectory {
  permission: Permissions;
  label: string;
  xdgKey: string;
  path: () => string;
}
const USER_DIRECTORIES: UserDirectory[] = [
  { permission: 'ohos.permission.READ_WRITE_DESKTOP_DIRECTORY', label: '桌面',
    xdgKey: 'XDG_DESKTOP_DIR', path: (): string => Environment.getUserDesktopDir() },
  { permission: 'ohos.permission.READ_WRITE_DOCUMENTS_DIRECTORY', label: '文档',
    xdgKey: 'XDG_DOCUMENTS_DIR', path: (): string => Environment.getUserDocumentDir() },
  { permission: 'ohos.permission.READ_WRITE_DOWNLOAD_DIRECTORY', label: '下载',
    xdgKey: 'XDG_DOWNLOAD_DIR', path: (): string => Environment.getUserDownloadDir() }
];

// Finish the OS authorization before mounting XComponent and starting Blender.
// Only actual grants enter the XDG map; picker authorization stays independent.
export async function prepareUserDirectories(context: common.UIAbilityContext, config: string): Promise<string> {
  const manager = abilityAccessCtrl.createAtManager();
  const bundle = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_WITH_APPLICATION);
  const token = bundle.appInfo.accessTokenId;
  const pending: Permissions[] = [];
  USER_DIRECTORIES.forEach((directory: UserDirectory) => {
    if (manager.checkAccessTokenSync(token, directory.permission) !== abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED) {
      pending.push(directory.permission);
    }
  });
  if (pending.length > 0) {
    try {
      const result = await manager.requestPermissionsFromUser(context, pending);
      hilog.info(0xB10, 'BlenderFiles', 'Directory permission results: %{public}s', result.authResults.join(','));
    } catch (error) {
      hilog.warn(0xB10, 'BlenderFiles', 'Directory authorization failed: %{public}s', (error as Error).message);
    }
  }
  const unavailable: string[] = [];
  let directories = '';
  for (const directory of USER_DIRECTORIES) {
    let path = '';
    try {
      const granted = manager.checkAccessTokenSync(token, directory.permission) === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED;
      if (granted) {
        const resolved = directory.path();
        // Blender's XDG parser accepts literal quoted paths, without shell unescaping.
        if (!resolved.startsWith('/') || /["\\\u0000-\u001f\u007f]/.test(resolved) || !fs.statSync(resolved).isDirectory()) {
          throw new Error('系统返回了无效的目录');
        }
        const entries = fs.listFileSync(resolved, { listNum: 1 });
        path = resolved;
        hilog.info(0xB10, 'BlenderFiles', '%{public}s granted; directory probe records: %{public}d', directory.permission, entries.length);
      }
    } catch (error) {
      hilog.warn(0xB10, 'BlenderFiles', '%{public}s unavailable: %{public}s', directory.permission, (error as Error).message);
    }
    if (path.length === 0) { unavailable.push(directory.label); }
    else { directories += `${directory.xdgKey}="${path}"\n`; }
  }
  const root = `${config}/public-directories`;
  if (!fs.accessSync(root)) { fs.mkdirSync(root); }
  const file = fs.openSync(`${root}/user-dirs.dirs`, fs.OpenMode.CREATE | fs.OpenMode.WRITE_ONLY | fs.OpenMode.TRUNC);
  try { fs.writeSync(file.fd, directories); fs.fsyncSync(file.fd); }
  finally { fs.closeSync(file); }
  return unavailable.length > 0 ? `${unavailable.join('、')}目录暂不可用，可通过“文件 → 系统文件”选择文档。` : '';
}
