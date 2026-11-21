import test from 'node:test';
import assert from 'node:assert/strict';
import { LIMITS, OMEGA, createMixer } from '../renderer/animation/mixer.js';

// 測試用模型範圍：angleX 原始範圍比上限寬（上限先卡）、angleY 比上限窄（原始範圍先卡）、bodyX 不對稱（neutral 不在正中）
const CHANNELS = {
  angleX: { neutral: 0, min: -40, max: 40 },
  angleY: { neutral: 0, min: -20, max: 20 },
  angleZ: { neutral: 0, min: -30, max: 30 },
  bodyX: { neutral: 1, min: -3, max: 12 },
  eyeX: { neutral: 0, min: -1, max: 1 },
  eyeY: { neutral: 0, min: -1, max: 1 },
  notInLimits: { neutral: 0, min: -1, max: 1 },
};
const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} 與 ${b} 差 ${Math.abs(a - b)}`);
const settle = (mixer, key, target, opts, seconds = 5, dt = 1 / 60) => {
  let x = 0;
  for (let t = 0; t < seconds; t += dt) x = mixer.write(key, target, dt, opts);
  return x;
};

test('has：只有模型有、而且在 LIMITS 裡的鍵才受控', () => {
  const m = createMixer(CHANNELS);
  for (const key of ['angleX', 'angleY', 'angleZ', 'bodyX', 'eyeX', 'eyeY']) assert.equal(m.has(key), true, key);
  for (const key of ['notInLimits', 'bodyY', 'nope']) assert.equal(m.has(key), false, key);
  assert.equal(createMixer({}).has('angleX'), false);
});

test('初始位置＝neutral、速度 0', () => {
  const m = createMixer(CHANNELS);
  assert.equal(m.value('bodyX'), 1);
  assert.equal(m.velocity('bodyX'), 0);
  assert.equal(m.value('angleX'), 0);
});

test('角度上限：autonomous 的目標夾在 neutral ± LIMIT，而且不超出原始範圍', () => {
  const m = createMixer(CHANNELS);
  close(settle(m, 'angleX', 100), LIMITS.angleX, 1e-6);      // 上限 35 < 原始 40
  close(settle(m, 'angleX', -100), -LIMITS.angleX, 1e-6);
  close(settle(m, 'angleY', 100), 20, 1e-6);                  // 原始 20 < 上限 24
  close(settle(m, 'angleY', -100), -20, 1e-6);
  close(settle(m, 'bodyX', 100), 1 + LIMITS.bodyX, 1e-6);     // neutral 1 + 8；原始 12 更寬
  close(settle(m, 'bodyX', -100), -3, 1e-6);                  // neutral 1 - 8 = -7，原始最小 -3 先卡
  close(settle(m, 'eyeX', 5), LIMITS.eyeX, 1e-6);
  close(settle(m, 'eyeY', -5), -LIMITS.eyeY, 1e-6);
});

test('autonomous=false：夾在原始範圍，不受角度上限', () => {
  const m = createMixer(CHANNELS);
  close(settle(m, 'angleX', 100, { autonomous: false }), 40, 1e-6);
  close(settle(m, 'angleX', -100, { autonomous: false }), -40, 1e-6);
  close(settle(m, 'angleY', 100, { autonomous: false }), 20, 1e-6);
  close(settle(m, 'eyeX', 5, { autonomous: false }), 1, 1e-6);
  close(settle(m, 'angleX', 36, { autonomous: false }), 36, 1e-6);   // 超過自主上限 35 但在原始範圍內
});

test('normalized：q 限制在 ±1，正側走 neutral→high、負側走 neutral→low，各自的行程', () => {
  const m = createMixer(CHANNELS);
  assert.equal(m.normalized('angleX', 0), 0);
  assert.equal(m.normalized('angleX', 1), 35);
  assert.equal(m.normalized('angleX', -1), -35);
  assert.equal(m.normalized('angleX', 0.5), 17.5);
  assert.equal(m.normalized('angleX', 7), 35);               // 超過 ±1 先夾
  assert.equal(m.normalized('angleX', -7), -35);
  assert.equal(m.normalized('angleY', 1), 20);               // high 被原始範圍夾到 20
  assert.equal(m.normalized('angleY', -0.5), -10);
  // bodyX：neutral 1，high = min(12, 9) = 9，low = max(-3, -7) = -3 → 正負兩側行程不同（8 與 4）
  assert.equal(m.normalized('bodyX', 1), 9);
  assert.equal(m.normalized('bodyX', 0.5), 5);
  assert.equal(m.normalized('bodyX', -1), -3);
  assert.equal(m.normalized('bodyX', -0.5), -1);
  assert.equal(m.normalized('bodyX', 0), 1);
  assert.equal(m.normalized('eyeX', 1), 0.85);
  assert.equal(m.normalized('eyeX', Number.NaN), 0);          // 非有限的 q 視為 0（neutral）
});

test('write 是解析解：切成不同幀率走同一段時間結果相同，也等於閉式解', () => {
  const run = (hz) => {
    const m = createMixer(CHANNELS);
    let x = 0;
    for (let k = 0; k < hz * 0.25; k++) x = m.write('angleX', 20, 1 / hz);
    return [x, m.velocity('angleX')];
  };
  const [x40, v40] = run(40), [x120, v120] = run(120);
  close(x40, x120);
  close(v40, v120);
  // 從靜止出發：x = goal - goal (1 + ωt) e^-ωt
  const w = OMEGA.pose, t = 0.25;
  close(x120, 20 - 20 * (1 + w * t) * Math.exp(-w * t));
});

test('eye 鍵用 ω120 收斂，明顯比 pose 的 ω18 快', () => {
  const m = createMixer({ ...CHANNELS, angleZ: { neutral: 0, min: -1, max: 1 } });
  const e = createMixer(CHANNELS);
  let eye = 0, pose = 0;
  for (let k = 0; k < 6; k++) {                              // 0.1 秒
    eye = e.write('eyeX', 0.8, 1 / 60);
    pose = m.write('angleZ', 0.8, 1 / 60);
  }
  assert.ok(Math.abs(0.8 - eye) < 0.8 * 1e-3, `eye 剩 ${0.8 - eye}`);        // (1+12)e^-12 ≈ 8e-5
  assert.ok(Math.abs(0.8 - pose) > 0.8 * 0.3, `pose 剩 ${0.8 - pose}`);      // (1+1.8)e^-1.8 ≈ 0.46
  assert.equal(OMEGA.eye, 120);
  assert.equal(OMEGA.pose, 18);
});

test('接手時位置與速度連續：中途換目標，下一步的位置與速度變化都在加速度允許的範圍內', () => {
  const dt = 1 / 240;
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 24; k++) m.write('angleX', 30, dt);       // 往 30 走 0.1 秒，速度正在高點
  const x0 = m.value('angleX'), v0 = m.velocity('angleX');
  assert.ok(v0 > 50, `接手前速度 ${v0} 要夠大才有測試意義`);
  const goal = -25;
  const delta = x0 - goal;
  const accel = OMEGA.pose ** 2 * Math.abs(delta) + 2 * OMEGA.pose * Math.abs(v0);   // |x''| 的上限
  const x1 = m.write('angleX', goal, dt);
  const v1 = m.velocity('angleX');
  assert.ok(Math.abs(v1 - v0) <= 1.5 * accel * dt, `速度跳變 ${v1 - v0}`);
  assert.ok(Math.abs(x1 - x0) <= Math.abs(v0) * dt + accel * dt * dt, `位置跳變 ${x1 - x0}`);
  assert.ok(Math.sign(x1 - x0) === Math.sign(v0), '速度方向不會瞬間反向（要先減速）');
});

test('交還（autonomous 切換）也連續：上限換成原始範圍只改目標的夾法，不動位置與速度', () => {
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 30; k++) m.write('angleX', 38, 1 / 120, { autonomous: false });
  const x0 = m.value('angleX'), v0 = m.velocity('angleX');
  assert.ok(x0 > 0 && v0 > 0);
  m.write('angleX', 38, 0, { autonomous: true });             // dt 0：只換夾法，位置速度不變
  assert.equal(m.value('angleX'), x0);
  assert.equal(m.velocity('angleX'), v0);
});

test('immediate：直接到位、速度歸零（仍夾在上限）', () => {
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 10; k++) m.write('angleX', 30, 1 / 60);
  assert.ok(m.velocity('angleX') !== 0);
  assert.equal(m.write('angleX', 12, 1 / 60, { immediate: true }), 12);
  assert.equal(m.value('angleX'), 12);
  assert.equal(m.velocity('angleX'), 0);
  assert.equal(m.write('angleX', 99, 1 / 60, { immediate: true }), 35);                       // autonomous 夾上限
  assert.equal(m.write('angleX', 99, 1 / 60, { immediate: true, autonomous: false }), 40);    // 否則夾原始範圍
});

test('結果夾在原始範圍，被夾時速度歸零', () => {
  const m = createMixer({ angleZ: { neutral: 0, min: -1, max: 1 } });
  m.set('angleZ', 1.5);                                        // set 不夾；下一步仍在範圍外
  assert.equal(m.value('angleZ'), 1.5);
  assert.equal(m.write('angleZ', 1, 1 / 60, { autonomous: false }), 1);
  assert.equal(m.velocity('angleZ'), 0);
  // 隨機換目標的性質測試：位置永遠在原始範圍內；位置貼著邊界時速度為 0
  let seed = 7;
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  for (let k = 0; k < 5000; k++) {
    const x = m.write('angleZ', rnd() * 6 - 3, rnd() * 0.05, { autonomous: false });
    assert.ok(x >= -1 && x <= 1, `x=${x}`);
    if (x === 1 || x === -1) assert.equal(m.velocity('angleZ'), 0);
  }
});

test('set：直接設位置、速度歸零', () => {
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 10; k++) m.write('angleX', 30, 1 / 60);
  m.set('angleX', 5);
  assert.equal(m.value('angleX'), 5);
  assert.equal(m.velocity('angleX'), 0);
  m.set('notInLimits', 3);                                      // 未知鍵不報錯、不產生狀態
  assert.equal(m.value('notInLimits'), 0);
});

test('未知鍵與非有限 target：回傳 0 且不改狀態', () => {
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 10; k++) m.write('angleX', 30, 1 / 60);
  const x = m.value('angleX'), v = m.velocity('angleX');
  assert.equal(m.write('nope', 1, 1 / 60), 0);
  assert.equal(m.write('notInLimits', 1, 1 / 60), 0);
  for (const bad of [Number.NaN, Infinity, -Infinity, undefined, '3']) assert.equal(m.write('angleX', bad, 1 / 60), 0);
  assert.equal(m.value('angleX'), x);
  assert.equal(m.velocity('angleX'), v);
  assert.equal(m.normalized('nope', 1), 0);
  assert.equal(m.value('nope'), 0);
  assert.equal(m.velocity('nope'), 0);
});

test('dt 無效（負數、NaN）視為 0：位置不動', () => {
  const m = createMixer(CHANNELS);
  for (let k = 0; k < 10; k++) m.write('angleX', 30, 1 / 60);
  const x = m.value('angleX');
  assert.equal(m.write('angleX', 30, Number.NaN), x);
  assert.equal(m.write('angleX', 30, -1), x);
});
