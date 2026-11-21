// 載入 Live2D 模型：讀 model3／physics JSON、建模型、依固定順序裝修補，並讀動作與表情檔。
import { bridgeRenderOrders } from './compat/render-orders.js';
import { installDrawableColors } from './compat/drawable-colors.js';
import { installRenderBuffers, installStateCache } from './compat/render-buffers.js';

export async function readJSON(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`讀取失敗：${url} (${res.status})`);
  return res.json();
}

/**
 * 載入模型並裝上相容修補。修補的順序有依賴，不可調換：bridgeRenderOrders → installRenderBuffers → installStateCache → installDrawableColors
 * （drawable-colors 包在 render-buffers 外面）。
 * @param {string} url model3.json 的 URL
 * @param {{ app: any }} stage
 * @param {{ buffers: boolean, state: boolean }} options 渲染優化開關
 */
export async function loadModel(url, stage, options) {
  const base = url.slice(0, url.lastIndexOf('/') + 1);
  const settings = await readJSON(url);
  const physicsJSON = await readJSON(base + settings.FileReferences.Physics);
  const model = await PIXI.live2d.Live2DModel.from(url, { autoUpdate: false, autoInteract: false, motionPreload: /** @type {any} */ ('NONE') });
  stage.app.stage.addChild(model);
  const internal = /** @type {any} */ (model.internalModel), core = internal.coreModel, raw = core.getModel();
  bridgeRenderOrders(raw);
  if (options.buffers) installRenderBuffers(internal);   // 必須在 drawable-colors 之前：它包在這一層外面
  if (options.state) installStateCache(internal);
  installDrawableColors(internal);
  internal.motionManager.stopAllMotions();
  internal.renderer.setClippingMaskBufferSize(1024);
  const defaults = Float32Array.from(raw.parameters.defaultValues);
  const ids = Array.from(raw.parameters.ids);
  return { model, internal, core, raw, settings, physicsJSON, base, defaults, ids };
}

/**
 * 讀動作與表情檔（以模型裡的名稱為 key）。動作缺了會拋錯（先用 checkRig 擋），表情缺了就略過。
 * @param {string} base
 * @param {any} settings model3.json
 * @param {{ motions: string[], expressions: string[] }} names
 */
export async function loadAssets(base, settings, names) {
  const motions = {}, expressions = {};
  for (const name of names.motions) motions[name] = await readJSON(base + settings.FileReferences.Motions[name][0].File);
  for (const name of names.expressions) {
    const item = (settings.FileReferences.Expressions || []).find(e => e.Name === name);
    if (item) expressions[name] = await readJSON(base + item.File);
  }
  return { motions, expressions };
}
