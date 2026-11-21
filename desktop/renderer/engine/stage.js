// 舞台：等 Cubism Core 就緒、建 PIXI 應用程式（不自己跑 ticker，繪製由幀迴圈負責）、讀游標下的像素。

export async function waitForCore() {
  const deadline = performance.now() + 15000;
  for (;;) {
    try { if (typeof Live2DCubismCore !== 'undefined' && Live2DCubismCore.Version.csmGetVersion()) break; } catch { /* 尚未載入 */ }
    if (performance.now() > deadline) throw new Error('Cubism Core 載入逾時');
    await new Promise(r => setTimeout(r, 25));
  }
  const version = Live2DCubismCore.Version.csmGetVersion();
  if ((version >>> 24) < 5) throw new Error(`需要 Cubism Core 5 以上，目前是 ${version >>> 24}.x`);  // moc3 v5 的模型
}

/**
 * @param {{ view: HTMLCanvasElement, width: number, height: number }} options
 */
export function createStage({ view, width, height }) {
  const app = new PIXI.Application({ view, width, height, autoStart: false, backgroundAlpha: 0, antialias: true,
    autoDensity: true, resolution: Math.min(devicePixelRatio || 1, 2), preserveDrawingBuffer: true });
  // PixiJS 6 的 InteractionManager 掛在 Ticker.system，預設會讓 rAF 永遠以 60 Hz 空轉；繪製由我們自己的幀迴圈負責，這裡全部停掉
  for (const t of [PIXI.Ticker.system, PIXI.Ticker.shared, app.ticker]) { t.autoStart = false; t.stop(); }
  const gl = /** @type {any} */ (app.renderer).gl;
  if (!gl) throw new Error('無法建立 WebGL');
  const pixel = new Uint8Array(4);
  /** 視窗座標 (x, y) 那個像素的 alpha（0～255） */
  function alphaAt(x, y) {
    const r = app.renderer.resolution;
    gl.readPixels(Math.floor(x * r), Math.floor((height - y) * r), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return pixel[3];
  }
  return { app, gl, alphaAt, destroy: () => app.destroy(false, { children: true }) };
}
