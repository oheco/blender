// SPDX-License-Identifier: GPL-2.0-or-later
import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';
import native from 'libblender_host.so';
export default class EntryAbility extends UIAbility {
  private stage: window.WindowStage | undefined = undefined;
  private stageEvent: (event: window.WindowStageEventType) => void = (event) => {
    if (event === window.WindowStageEventType.ACTIVE) { native.windowActive(true); }
    else if (event === window.WindowStageEventType.INACTIVE ||
             event === window.WindowStageEventType.HIDDEN ||
             event === window.WindowStageEventType.PAUSED) { native.windowActive(false); }
  };
  onWindowStageCreate(stage: window.WindowStage): void {
    this.stage = stage;
    stage.on('windowStageEvent', this.stageEvent);
    let active: boolean = false;
    try { active = stage.getMainWindowSync().isFocused(); }
    catch (_) { hilog.warn(0xB10, 'BlenderHost', 'Initial window focus unavailable'); }
    native.windowActive(active);
    stage.loadContent('pages/Index', (error) => {
      if (error.code) { hilog.error(0xB10, 'BlenderHost', 'loadContent failed: %{public}s', error.message); }
    });
  }
  onForeground(): void { native.foreground(true); }
  onBackground(): void { native.foreground(false); }
  onWindowStageDestroy(): void {
    this.stage?.off('windowStageEvent', this.stageEvent);
    this.stage = undefined;
    native.windowActive(false);
  }
  onDestroy(): void {
    native.windowActive(false);
    // Coordinate asynchronously; never join the engine from an ArkUI lifecycle call.
    // Native surface-destroy callback supplies the unexpected-removal ACK fence.
    native.shutdown().catch((error: Error) => hilog.error(0xB10, 'BlenderHost', 'shutdown failed: %{public}s', error.message));
  }
}
