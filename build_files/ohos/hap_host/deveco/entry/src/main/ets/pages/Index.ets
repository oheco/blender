// SPDX-License-Identifier: GPL-2.0-or-later
import native from 'libblender_host.so';
import { common } from '@kit.AbilityKit';
import { display, window } from '@kit.ArkUI';
import { prepareRuntime } from '../runtime/Runtime';
import { prepareUserDirectories } from '../runtime/Permissions';
import { AssetExchange, AssetOperation, assetInfo } from '../runtime/AssetExchange';
import { applicationIconDiagnostic } from '../runtime/IconDiagnostics';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { BusinessError } from '@kit.BasicServicesKit';

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
  private browserBusy: boolean = false;
  private automaticClose: boolean = false;
  private appWindow: window.Window | undefined = undefined;
  private controller: XComponentController = new XComponentController();
  private displayChanged: (id: number) => void = (_) => this.updateGeometry();
  private densityChanged: (density: number) => void = (_) => this.updateGeometry();
  private resizeTimer: number = -1;
  private statusListener: boolean = false;
  private lastWindowStatus: window.WindowStatusType | undefined = undefined;
  private statusChanged: (status: window.WindowStatusType) => void = (status) => {
    if (status === this.lastWindowStatus) { return; }
    this.lastWindowStatus = status;
    const reason = status === window.WindowStatusType.MAXIMIZE ||
      status === window.WindowStatusType.FULL_SCREEN ? 1 :
      status === window.WindowStatusType.FLOATING ? 2 : 0;
    this.updateGeometry(reason);
  };
  private geometryChanged: (options: window.RectChangeOptions) => void = (options) => {
    const reason = options.reason === window.RectChangeReason.MAXIMIZE ? 1 :
      options.reason === window.RectChangeReason.RECOVER ? 2 : 0;
    this.updateGeometry(reason);
  };
  private stopResizeSampling(): void {
    if (this.resizeTimer !== -1) { clearInterval(this.resizeTimer); this.resizeTimer = -1; }
  }
  private updateGeometry(reason: number = 0): void {
    if (this.disposed || this.closing || this.appWindow === undefined) { return; }
    const properties = this.appWindow.getWindowProperties();
    const ui = this.getUIContext();
    const node = ui.getFrameNodeById('blender-surface');
    if (node !== null) {
      const position = node.getGlobalPositionOnDisplay();
      const surface = this.controller.getXComponentSurfaceRect();
      native.imeGeometry(properties.id, ui.vp2px(position.x) + (surface.offsetX ?? 0),
        ui.vp2px(position.y) + (surface.offsetY ?? 0));
    }
    // Sample throughout a mode transition. Only distinct sizes restart native quiet time.
    if (properties.windowRect.width <= 0 || properties.windowRect.height <= 0) {
      this.stopResizeSampling(); return;
    }
    const pending = native.resizeObservation(properties.windowRect.width, properties.windowRect.height, reason);
    if (pending && this.resizeTimer === -1) {
      this.resizeTimer = setInterval(() => this.updateGeometry(), 16);
    } else if (!pending) { this.stopResizeSampling(); }
  }
  aboutToAppear(): void { this.prepare(); }
  aboutToDisappear(): void {
    this.disposed = true;
    this.appWindow?.off('windowRectChange', this.geometryChanged);
    this.appWindow?.off('displayIdChange', this.displayChanged);
    this.appWindow?.off('systemDensityChange', this.densityChanged);
    if (this.statusListener) { this.appWindow?.off('windowStatusChange', this.statusChanged); }
    this.stopResizeSampling();
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
      this.appWindow.on('displayIdChange', this.displayChanged);
      this.appWindow.on('systemDensityChange', this.densityChanged);
      try {
        this.lastWindowStatus = this.appWindow.getWindowStatus();
        this.appWindow.on('windowStatusChange', this.statusChanged);
        this.statusListener = true;
      } catch (_) { /* Rectangle notifications remain available when SessionManager is unsupported. */ }
      this.updateGeometry();
      const paths = await prepareRuntime(context);
      if (this.disposed) { return; }
      this.message = '正在请求桌面、文档和下载目录的访问权限…';
      this.assetMessage = await prepareUserDirectories(context, paths.config);
      if (this.disposed) { return; }
      const screen = display.getDefaultDisplaySync();
      native.configure(paths.runtime, paths.config, paths.cache, paths.temp,
        screen.densityPixels, Math.round(screen.densityDPI));
      this.exchange = new AssetExchange(context, paths);
      this.mounted = true; // Native callback now receives the actual borrowed OHNativeWindow.
      this.message = '正在启动 Blender…';
      this.timer = setInterval(() => {
        const state: number = native.state();
        const wasReady = this.ready;
        this.ready = state === 2;
        if (this.ready) {
          this.updateGeometry();
          this.message = '';
          if (!wasReady) { this.requestSurfaceFocus(); }
          if (!this.assetBusy) {
            const operation: number = native.takeDocumentRequest();
            if (operation >= 1 && operation <= 6) { this.asset(operation as AssetOperation); }
          }
          if (!this.assetBusy && !this.browserBusy) { this.consumeURL(context); }
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
    this.stopResizeSampling();
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
    } finally {
      this.assetBusy = false;
      setTimeout(() => this.requestSurfaceFocus(), 0);
    }
  }
  private consumeURL(context: common.UIAbilityContext): void {
    try {
      if (!this.appWindow?.isFocused()) { return; }
      const target: string = native.takeURLRequest();
      if (target.length > 0) { this.openURL(context, target); }
    } catch (error) {
      hilog.error(0xB10, 'BlenderBrowser', 'Browser request polling failed');
    }
  }
  private async openURL(context: common.UIAbilityContext, target: string): Promise<void> {
    if (this.disposed || this.closing || this.browserBusy) { return; }
    this.browserBusy = true;
    try {
      await context.openLink(target, { appLinkingOnly: false });
      hilog.info(0xB10, 'BlenderBrowser', 'System browser launch accepted');
    } catch (error) {
      hilog.error(0xB10, 'BlenderBrowser', 'System browser launch failed code=%{public}d', (error as BusinessError).code);
      if (!this.disposed) { this.assetMessage = '无法打开系统浏览器，请稍后重试。'; }
    } finally { this.browserBusy = false; }
  }
  private requestSurfaceFocus(): void {
    if (!this.ready || !this.mounted || this.assetBusy || this.closing || this.disposed) { return; }
    try { this.getUIContext().getFocusController().requestFocus('blender-surface'); }
    catch (_) { /* A pointer press retries after asynchronous surface mounting. */ }
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
        XComponent({ id: 'blender-surface', type: XComponentType.SURFACE, libraryname: 'blender_host', controller: this.controller })
          .id('blender-surface').focusable(true).defaultFocus(true).focusOnTouch(true)
          .enabled(!this.assetBusy && !this.closing)
          // The native callback owns physical keys for this libraryname-backed surface.
          .onKeyEvent((event: KeyEvent) => this.printable(event))
          .onMouse((event: MouseEvent) => {
            if (event.action === MouseAction.Press) { this.requestSurfaceFocus(); }
          })
          .onAreaChange(() => this.updateGeometry())
          .width('100%').layoutWeight(1)
      } else { Blank().layoutWeight(1) }
    }.width('100%').height('100%').backgroundColor('#202124')
  }
}
