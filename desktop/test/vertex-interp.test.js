import test from 'node:test';
import assert from 'node:assert/strict';
import { createInterpolator } from '../renderer/vertex-interp.js';

test('計算幀畫中點、中間幀畫最新值', () => {
  const a = new Float32Array([0, 0]), b = new Float32Array([1]);
  const it = createInterpolator([a, b]);
  a.set([2, 4]); b[0] = 0;          // Core 算出新的一幀
  it.computed();
  assert.deepEqual([...a], [1, 2]); assert.deepEqual([...b], [0.5]);
  it.between();
  assert.deepEqual([...a], [2, 4]); assert.deepEqual([...b], [0]);
});

test('restore：Core 更新前陣列回到真值（Core 沒重寫的 drawable 不會留下內插值）', () => {
  const a = new Float32Array([0]);
  const it = createInterpolator([a]);
  a[0] = 10; it.computed();          // 陣列現在是 5（中點）
  it.restore();
  assert.equal(a[0], 10);
  it.computed();                     // Core 沒動這個 drawable：前後都是 10
  assert.equal(a[0], 10);
});
