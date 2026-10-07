// SPDX-License-Identifier: GPL-2.0-or-later
import { common } from '@kit.AbilityKit';
import { LayeredDrawableDescriptor } from '@kit.ArkUI';
import { image } from '@kit.ImageKit';

interface IconPixels {
  width: number;
  height: number;
  orange: number;
  blue: number;
  transparent: number;
}

function pixels(map: image.PixelMap): IconPixels {
  const info = map.getImageInfoSync();
  const bytes = new ArrayBuffer(map.getPixelBytesNumber());
  map.readPixelsToBufferSync(bytes);
  const data = new Uint8Array(bytes);
  const row = map.getBytesNumberPerRow();
  const rgba = info.pixelFormat === image.PixelMapFormat.RGBA_8888;
  if (!rgba && info.pixelFormat !== image.PixelMapFormat.BGRA_8888) {
    throw new Error('Unsupported icon PixelMap format');
  }
  let orange = 0;
  let blue = 0;
  let transparent = 0;
  for (let y = 0; y < info.size.height; y++) {
    for (let x = 0; x < info.size.width; x++) {
      const p = y * row + x * 4;
      const r = data[p + (rgba ? 0 : 2)];
      const g = data[p + 1];
      const b = data[p + (rgba ? 2 : 0)];
      const a = data[p + 3];
      if (a === 0) { transparent++; }
      if (a === 255 && r === 232 && g === 125 && b === 13) { orange++; }
      if (a === 255 && r === 38 && g === 87 && b === 135) { blue++; }
    }
  }
  return { width: info.size.width, height: info.size.height, orange: orange,
    blue: blue, transparent: transparent };
}

// Keep decode results available in the existing debug UI dump, without adding UI controls.
export function applicationIconDiagnostic(context: common.UIAbilityContext): string {
  try {
    const icon = context.resourceManager.getDrawableDescriptorByName('blender_app_icon', 0, 0);
    if (!(icon instanceof LayeredDrawableDescriptor)) {
      throw new Error('Application icon is not a LayeredDrawableDescriptor');
    }
    const foreground = pixels(icon.getForeground().getPixelMap());
    const background = pixels(icon.getBackground().getPixelMap());
    const composed = pixels(icon.getPixelMap());
    const valid = foreground.width === 1024 && foreground.height === 1024 &&
      background.width === 1024 && background.height === 1024 &&
      foreground.orange > 0 && foreground.blue > 0 && foreground.transparent > 0 &&
      background.transparent === 0 && composed.orange > 0 && composed.blue > 0;
    return JSON.stringify({ valid: valid, layered: true, foreground: foreground,
      background: background, composed: composed });
  } catch (error) {
    return JSON.stringify({ valid: false, error: (error as Error).message });
  }
}
