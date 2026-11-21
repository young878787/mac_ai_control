// Cubism Core 6 把繪製順序從 drawables.renderOrders 搬到 Model.getRenderOrders()（drawable 在前、offscreen 在後）；
// pixi-live2d-display 0.4.0 仍讀舊位置。沒有 offscreen 的模型（如 RushiaHD）前 N 項就是 drawable 的順序，這裡補回去。
export function bridgeRenderOrders(raw) {
  if (raw.drawables.renderOrders || typeof raw.getRenderOrders !== 'function') return;
  if (raw.offscreens && raw.offscreens.count > 0) throw new Error('模型含 offscreen，目前的渲染器不支援');
  const count = raw.drawables.count;
  Object.defineProperty(raw.drawables, 'renderOrders', { get: () => raw.getRenderOrders().subarray(0, count) });
}
