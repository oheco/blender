// SPDX-License-Identifier: GPL-2.0-or-later
import native from 'libblender_host.so';
import { common } from '@kit.AbilityKit';
import { display, window } from '@kit.ArkUI';
import { prepareRuntime } from '../runtime/Runtime';
import { AssetExchange, AssetOperation, assetInfo } from '../runtime/AssetExchange';
import { applicationIconDiagnostic } from '../runtime/IconDiagnostics';

@Entry
@Component
struct Index {
  @State mounted: boolean = false;
  @State message: string = '正在准备 Blender…';
  @State closing: boolean = false;
  @State ready: boolean = false;
  @State assetBusy: boolean = false;
  @State assetMessage: string = '';
  @State iconDiagnostic: string = '';
  private exchange: AssetExchange | undefined = undefined;
  private timer: number = -1;
  private disposed: boolean = false;
  private automaticClose: boolean = false;
  private appWindow: window.Window | undefined = undefined;
  private surfaceLeft: number = 0;
  private surfaceTop: number = 0;
  private geometryChanged: (options: window.RectChangeOptions) => void = () => this.updateGeometry();
  private updateGeometry(): void {
    if (this.disposed || this.appWindow === undefined) { return; }
    const properties = this.appWindow.getWindowProperties();
    native.imeGeometry(properties.id, properties.windowRect.left + this.surfaceLeft,
      properties.windowRect.top + this.surfaceTop);
  }
  aboutToAppear(): void { this.prepare(); }
  aboutToDisappear(): void {
    this.disposed = true;
    this.appWindow?.off('windowRectChange', this.geometryChanged);
    this.exchange?.close();
    if (this.timer !== -1) { clearInterval(this.timer); this.timer = -1; }
    native.shutdown().catch((error: Error) => { this.message = error.message; });
  }
  private async prepare(): Promise<void> {
    try {
      const context = this.getUIContext().getHostContext() as common.UIAbilityContext;
      this.iconDiagnostic = applicationIconDiagnostic(context);
      this.appWindow = await window.getLastWindow(context);
      if (this.disposed) { return; }
      this.appWindow.on('windowRectChange', this.geometryChanged);
      this.updateGeometry();
      const paths = await prepareRuntime(context);
      if (this.disposed) { return; }
      const screen = display.getDefaultDisplaySync();
      native.configure(paths.runtime, paths.config, paths.cache, paths.temp,
        screen.densityPixels, Math.round(screen.densityDPI));
      this.exchange = new AssetExchange(context, paths);
      this.mounted = true; // Native callback now receives the actual borrowed OHNativeWindow.
      this.message = '正在启动 Blender…';
      this.timer = setInterval(() => {
        const state: number = native.state();
        this.ready = state === 2;
        if (this.ready) {
          this.message = '';
          if (!this.assetBusy) {
            const operation: number = native.takeDocumentRequest();
            if (operation >= 1 && operation <= 6) { this.asset(operation as AssetOperation); }
          }
        }
        if (state === 5 && !this.closing) { this.message = 'Blender 正在等待任务与资源所有权释放…'; }
        if ((state === 3 || state === 5 || state < 0) && !this.automaticClose) {
          this.automaticClose = true;
          this.close(state >= 0);
        }
      }, 100);
    } catch (error) {
      this.message = `无法启动 Blender：${(error as Error).message}`;
      native.shutdown().catch((failure: Error) => { this.message += `；${failure.message}`; });
    }
  }
  private async close(terminate: boolean): Promise<void> {
    if (this.closing) { return; }
    this.closing = true;
    this.ready = false;
    this.exchange?.close();
    this.message = 'Blender 正在等待任务与资源所有权释放…';
    try {
      // XComponent remains mounted until native detach ACK, teardown and join finish.
      await native.shutdown();
      this.mounted = false;
      if (this.timer !== -1) { clearInterval(this.timer); this.timer = -1; }
      this.message = native.result() === 0 ? 'Blender 已关闭' : `Blender 已关闭（退出结果 ${native.result()}）`;
      if (terminate) {
        await (this.getUIContext().getHostContext() as common.UIAbilityContext).terminateSelf();
      }
    } catch (error) {
      this.message = `Blender 关闭失败（${native.result()}）：${(error as Error).message}`;
      this.closing = false; // Retain the mounted owner if the detach ACK failed.
    }
  }
  private async asset(operation: AssetOperation): Promise<void> {
    if (this.assetBusy || this.closing || !this.ready || this.exchange === undefined) { return; }
    this.assetBusy = true;
    const label = assetInfo(operation).label;
    try {
      const finished = await this.exchange.run(operation, (status: string) => {
        if (!this.disposed) { this.assetMessage = status; }
      });
      if (!this.disposed) { this.assetMessage = finished ? `${label}完成` : `已取消${label}`; }
    } catch (error) {
      if (!this.disposed) { this.assetMessage = `${label}失败：${(error as Error).message}`; }
    } finally { this.assetBusy = false; }
  }
  private printable(event: KeyEvent): void {
    // SDK26 common.d.ts: modifier query accepts only Ctrl/Alt/Shift; Meta is
    // the separate metaKey property. keyText is a KEY NAME, not composed text.
    // Native IME commits already carry composed text for the focused field.
    if (native.imeActive()) { return; }
    if (event.type !== KeyType.Down || event.metaKey !== 0 || event.getModifierKeyState === undefined ||
        event.getModifierKeyState(['Ctrl']) || event.getModifierKeyState(['Alt'])) { return; }
    const value = event.keyCode === 2050 ? 32 : (event.unicode ?? 0); // SDK KEY_SPACE=2050.
    if (value < 32 || value > 126) { return; }
    try { native.commitText(String.fromCodePoint(value)); } catch (error) { this.message = (error as Error).message; }
  }
  build() {
    Column() {
      if (this.message.length > 0) { Text(this.message).fontColor(Color.White).padding(8) }
      if (this.assetMessage.length > 0) { Text(this.assetMessage).fontColor(Color.White).padding(8) }
      if (this.mounted) {
        XComponent({ id: 'blender-surface', type: XComponentType.SURFACE, libraryname: 'blender_host' })
          .id('blender-surface').focusable(true).defaultFocus(true)
          .enabled(!this.assetBusy && !this.closing)
          .onKeyEvent((event: KeyEvent) => this.printable(event))
          .onAreaChange((_previous: Area, area: Area) => {
            const left: number = Number(area.globalPosition.x);
            const top: number = Number(area.globalPosition.y);
            if (!Number.isFinite(left) || !Number.isFinite(top)) { return; }
            this.surfaceLeft = this.getUIContext().vp2px(left);
            this.surfaceTop = this.getUIContext().vp2px(top);
            this.updateGeometry();
          })
          .width('100%').layoutWeight(1)
      } else { Blank().layoutWeight(1) }
    }.width('100%').height('100%').backgroundColor('#202124')
  }
}
