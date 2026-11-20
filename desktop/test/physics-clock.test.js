import test from 'node:test';
import assert from 'node:assert/strict';
import { createFixedPhysics } from '../renderer/physics-clock.js';

const STEP = 1 / 60;
const PHYSICS = { PhysicsSettings: [{ Input: [{ Source: { Id: 'In' } }], Output: [{ Destination: { Id: 'Out' }, Weight: 100 }] }] };

// 假的 Core 與 solver：solver 每跑一個步長就把 Out 設成腳本裡的下一個值
function setup(script) {
  const values = new Float32Array([0, 0]);
  const model = { parameters: { ids: ['In', 'Out'], values } };
  const core = { getModel: () => model };
  let n = 0;
  const solver = { initialize() {}, evaluate() { values[1] = script[Math.min(n++, script.length - 1)]; } };
  return { physics: createFixedPhysics(solver, core, PHYSICS), core, values, steps: () => n };
}

test('沒有跑任何物理步長 → 回傳 null（delta 0 或不足一步）', () => {
  const { physics, core, steps } = setup([1]);
  assert.equal(physics.evaluate(core, 0), null);
  assert.equal(physics.evaluate(core, STEP / 2), null);
  assert.equal(steps(), 0);
});

test('一個步長 → 回傳輸出的變化量', () => {
  const { physics, core } = setup([0.5]);
  assert.ok(Math.abs(physics.evaluate(core, STEP) - 0.5) < 1e-6);
});

test('一次跑多個步長 → 回傳其中最大的變化量', () => {
  const { physics, core, steps } = setup([0.5, 0.75, 0.8]);
  const d = physics.evaluate(core, STEP * 3);
  assert.equal(steps(), 3);
  assert.ok(Math.abs(d - 0.5) < 1e-6, String(d));
});

test('輸出不再變化 → 變化量是 0；畫面上的值仍是最後兩步的內插（行為不變）', () => {
  const { physics, core, values } = setup([0.25, 0.25, 0.25]);
  physics.evaluate(core, STEP);
  assert.equal(physics.evaluate(core, STEP), 0);
  assert.ok(Math.abs(values[1] - 0.25) < 1e-6);
});
