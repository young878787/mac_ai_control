import test from 'node:test';
import assert from 'node:assert/strict';
import { createFrameGate, TAIL_MS } from '../renderer/frame-rate.js';

const OPT = { eps: 0.01, holdMs: 500, maxMs: 4000 };
const F = 1000 / 60;
const peek = (delta) => ({ out: false, moving: false, delta });

// 從 start 起每 1/60 秒畫一幀，直到停格或超過 limitMs；回傳停格時間（沒停回傳 null）
function runUntilFrozen(g, start, limitMs, deltaAt) {
  for (let t = start; t <= start + limitMs; t += F) {
    if (!g.afterFrame(t, peek(deltaAt(t)))) return t;
  }
  return null;
}

test('出場中一律繼續畫，不論物理是否靜止', () => {
  const g = createFrameGate(OPT);
  for (let t = 0; t < 10_000; t += F) assert.equal(g.afterFrame(t, { out: true, moving: false, delta: 0 }), true);
  assert.equal(g.frozen, false);
});

test('潛伏但進退場中或有動作 → 繼續畫', () => {
  const g = createFrameGate(OPT);
  for (let t = 0; t < 10_000; t += F) assert.equal(g.afterFrame(t, { out: false, moving: true, delta: 0 }), true);
});

test('事件之後的 TAIL_MS 內不停格（表情淡出中）', () => {
  const g = createFrameGate(OPT);
  g.wake(0);
  assert.equal(g.afterFrame(TAIL_MS - 1, peek(0)), true);
});

test('潛伏且靜止連續 holdMs → 停格；未滿 holdMs 不停', () => {
  const g = createFrameGate(OPT);
  const at = runUntilFrozen(g, 0, 2000, () => 0);
  assert.ok(at !== null && at >= OPT.holdMs && at < OPT.holdMs + 2 * F, String(at));
  assert.equal(g.frozen, true);
});

test('中途有一幀超過 eps → 靜止重新計時', () => {
  const g = createFrameGate(OPT);
  const at = runUntilFrozen(g, 0, 3000, (t) => (t > 400 && t < 420 ? 0.5 : 0));
  assert.ok(at >= 420 + OPT.holdMs, String(at));
});

test('沒跑物理步長的幀（delta null）不打斷也不開始靜止計時', () => {
  const g = createFrameGate(OPT);
  const at = runUntilFrozen(g, 0, 2000, (t) => (Math.round(t / F) % 2 ? null : 0));
  assert.ok(at !== null && at < OPT.holdMs + 3 * F, String(at));
  const h = createFrameGate(OPT);
  assert.equal(runUntilFrozen(h, 0, 2000, () => null), null);   // 全部 null：只靠 maxMs
});

test('物理一直有擺動 → maxMs 到了強制停格', () => {
  const g = createFrameGate(OPT);
  const at = runUntilFrozen(g, 0, 6000, () => 1);
  assert.ok(at >= OPT.maxMs && at < OPT.maxMs + 2 * F, String(at));
});

test('停格後 wake → 解除，需要重新收尾才會再停', () => {
  const g = createFrameGate(OPT);
  runUntilFrozen(g, 0, 2000, () => 0);
  assert.equal(g.frozen, true);
  g.wake(5000);
  assert.equal(g.frozen, false);
  assert.equal(g.afterFrame(5000 + F, peek(0)), true);
  const at = runUntilFrozen(g, 5000 + TAIL_MS, 2000, () => 0);
  assert.ok(at >= 5000 + TAIL_MS + OPT.holdMs, String(at));
});

test('出場期間的靜止不累積：退場後才開始算 holdMs 與 maxMs', () => {
  const g = createFrameGate(OPT);
  for (let t = 0; t < 10_000; t += F) g.afterFrame(t, { out: true, moving: false, delta: 0 });
  assert.equal(g.afterFrame(10_000, peek(0)), true);
  assert.equal(g.afterFrame(10_000 + OPT.holdMs - 1, peek(0)), true);
  assert.equal(g.afterFrame(10_000 + OPT.holdMs, peek(0)), false);
});
