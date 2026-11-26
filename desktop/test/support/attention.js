// 注意力導演測試共用：rig（導演＋假的畫面狀態＋時鐘）、計畫合法性檢查、數值區間。從 attention.test.js 搬出來，兩個測試檔共用。
// node --test 會載入 test/ 底下所有 .js：這個檔沒有 test()，整個檔算成 1 個通過的項目（計數 +1），不會失敗。
import assert from 'node:assert/strict';
import { createAttention } from '../../domain/attention.js';
import { createAmbient } from '../../renderer/animation/ambient.js';

export const AXES = ['headX', 'headY', 'headZ', 'bodyX', 'bodyY', 'bodyZ', 'gazeX', 'gazeY'];
export const POSE = AXES.slice(0, 6);
const T0 = 1_000_000;
export const EPS = 1e-3;   // 計畫數值 round 到 4 位，區間比較留一點餘裕

/** 測試用的帶 seed 亂數（mulberry32），domain 不 import animation，所以這裡自己寫 */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 導演＋假的畫面狀態＋時鐘。tick 推進時間並呼叫 update；run 跑一段時間，回傳有送出計畫的那幾列 */
export function rig(seed, init = {}) {
  const att = createAttention({ random: mulberry32(seed) });
  const st = { pose: 'out', mode: 'idle', motion: 'idle', inputOpen: false, ...init };
  let now = T0;
  const r = {
    att, st,
    get now() { return now; },
    tick(ms = 20) {
      now += ms;
      return { t: now, ...att.update(now, st) };
    },
    /** each(下一個 tick 的時間) 在 tick 之前呼叫，用來餵游標、打字之類的事件 */
    run(ms, { step = 20, each } = {}) {
      const out = [];
      const end = now + ms;
      while (now < end) {
        each?.(now + step);
        const row = r.tick(step);
        if (row.plan) out.push(row);
      }
      return out;
    },
    /** 跑到 fn(row) 成立為止（最多 maxMs），回傳那一列；沒成立就丟錯 */
    until(fn, maxMs, step = 20) {
      const end = now + maxMs;
      while (now < end) {
        const row = r.tick(step);
        if (fn(row)) return row;
      }
      throw new Error(`${maxMs} ms 內沒有出現預期的狀態`);
    },
  };
  return r;
}

export const inRange = (v, low, high, eps = EPS) => v >= low - eps && v <= high + eps;
export const posePart = (target) => Object.fromEntries(POSE.map(a => [a, target[a]]));
export const samePose = (a, b) => POSE.every(axis => a[axis] === b[axis]);
const isRound4 = (v) => Math.round(v * 1e4) / 1e4 === v && !Object.is(v, -0);

function assertValidPlan(plan, label = '') {
  assert.ok(Number.isSafeInteger(plan.sequence) && plan.sequence > 0, `${label} sequence`);
  assert.ok(Number.isFinite(plan.moveMs) && plan.moveMs >= 100 && plan.moveMs <= 3000, `${label} moveMs ${plan.moveMs}`);
  assert.deepEqual(Object.keys(plan.target).sort(), [...AXES].sort(), `${label} 軸`);
  for (const axis of AXES) {
    const v = plan.target[axis];
    assert.ok(Number.isFinite(v) && v >= -1 && v <= 1 && isRound4(v), `${label} ${axis}=${v}`);
  }
}

/** 逐份檢查：合法、sequence 逐份加一、而且 ambient.apply 都肯收 */
export function assertPlans(rows, label = '') {
  const ambient = createAmbient(7);
  let last = 0;
  for (const row of rows) {
    assertValidPlan(row.plan, label);
    assert.equal(row.plan.sequence, last + 1, `${label} sequence 要遞增`);
    last = row.plan.sequence;
    assert.equal(ambient.apply(row.plan, row.t), true, `${label} ambient 拒收 ${JSON.stringify(row.plan)}`);
  }
}

export const settledRanges = (p) => inRange(p.headX, -0.18, 0.18) && inRange(p.headY, -0.15, 0.15) && inRange(p.headZ, -0.15, 0.15)
  && inRange(p.bodyX, -0.2, 0.2) && inRange(p.bodyY, -0.15, 0.15) && inRange(p.bodyZ, -0.2, 0.2);
