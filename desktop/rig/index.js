// 依模型檔名挑 rig profile。沒列到的模型沿用 fullrig03，對不上的話 checkRig 會在啟動時擋下。
import fullrig03 from './fullrig03.js';
import hiyori from './hiyori.js';

const BY_MODEL_FILE = { 'hiyori_pro_t11.model3.json': hiyori };

/** @param {string} modelUrl model3.json 的路徑或 URL */
export function rigFor(modelUrl) {
  return BY_MODEL_FILE[modelUrl.slice(modelUrl.lastIndexOf('/') + 1)] ?? fullrig03;
}
