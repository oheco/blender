// SPDX-License-Identifier: GPL-2.0-or-later
import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';
import native from 'libblender_host.so';
export default class EntryAbility extends UIAbility {
  onWindowStageCreate(stage: window.WindowStage): void {
    stage.loadContent('pages/Index', (error) => {
      if (error.code) { hilog.error(0xB10, 'BlenderHost', 'loadContent failed: %{public}s', error.message); }
    });
  }
  onForeground(): void { native.foreground(true); }
  onBackground(): void { native.foreground(false); }
  onDestroy(): void {
    // Coordinate asynchronously; never join the engine from an ArkUI lifecycle call.
    // Native surface-destroy callback supplies the unexpected-removal ACK fence.
    native.shutdown().catch((error: Error) => hilog.error(0xB10, 'BlenderHost', 'shutdown failed: %{public}s', error.message));
  }
}
