export const configure: (runtime: string, config: string, cache: string, temp: string, scale: number, dpi: number) => void;
export const state: () => number;
export const result: () => number;
export const foreground: (active: boolean) => void;
export const windowActive: (active: boolean) => void;
// Physical keys and modifier snapshot; composed text stays on the IME commit path.
export const keyEvent: (code: number, pressed: boolean, ctrl: boolean, alt: boolean, shift: boolean, meta: boolean) => boolean;
export const commitText: (text: string) => void;
export const imeActive: () => boolean;
export const takeDocumentRequest: () => number;
export const imeGeometry: (osWindowId: number, surfaceScreenLeft: number, surfaceScreenTop: number) => void;
// Sample physical window size; 0=sample, 1=maximize, 2=recover. True while quiet observation is pending.
export const resizeObservation: (width: number, height: number, reason: number) => boolean;
export const wheel: (physicalX: number, physicalY: number, rightDetents: number, upDetents: number) => void;
export const detach: () => Promise<void>;
export const shutdown: () => Promise<void>;
// operation 1..6: open/save-copy blend, import/export geometry OBJ, import/export GLB.
// Engine-thread real operator result: 0 FINISHED, 1 CANCELLED; failures reject.
// workingCopy: config/exchange/<UUID>/source.* input; temp/exchange/<UUID>/asset.* output; never a URI.
export const fileOperation: (operation: number, workingCopy: string) => Promise<number>;
// Validate installed native-library mappings; no symlink/copy of executable ELF into app data.
export const bindLibraries: (staging: string, nativeRoot: string, relativePaths: string[], libraryNames: string[]) => void;
