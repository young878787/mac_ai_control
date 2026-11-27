// 依模型檔名挑 rig profile（選填，ADR 0033）。沒登錄的模型回傳 null：checkRig 照契約的約定名稱檢查，並警告要配置。
import fullrig03 from './fullrig03.js';
import hiyori from './hiyori.js';

const BY_MODEL_FILE = { 'RushiaHD.model3.json': fullrig03, 'hiyori_pro_t11.model3.json': hiyori };

/** @param {string} modelUrl model3.json 的路徑或 URL */
export function rigFor(modelUrl) {
  const file = modelUrl.slice(modelUrl.lastIndexOf('/') + 1);
  return Object.hasOwn(BY_MODEL_FILE, file) ? BY_MODEL_FILE[file] : null;
}
