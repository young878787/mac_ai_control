// 注意力導演測試：情境與優先順序（§4.4）、游標（§4.5）、聆聽（§4.6）、閒置劇本、點頭覆蓋層。node --test test/
import test from 'node:test';
import assert from 'node:assert/strict';
import { CONTEXTS, CURSOR_PX, IDLE_ACTIONS, createAttention } from '../domain/attention.js';
import { createAmbient } from '../renderer/animation/ambient.js';

const AXES = ['headX', 'headY', 'headZ', 'bodyX', 'bodyY', 'bodyZ', 'gazeX', 'gazeY'];
const POSE = AXES.slice(0, 6);
const T0 = 1_000_000;
const EPS = 1e-3;   // 計畫數值 round 到 4 位，區間比較留一點餘裕

/** 測試用的帶 seed 亂數（mulberry32），domain 不 import animation，所以這裡自己寫 */
function mulberry32(seed) {
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
function rig(seed, init = {}) {
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

const inRange = (v, low, high, eps = EPS) => v >= low - eps && v <= high + eps;
const posePart = (target) => Object.fromEntries(POSE.map(a => [a, target[a]]));
const samePose = (a, b) => POSE.every(axis => a[axis] === b[axis]);
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
function assertPlans(rows, label = '') {
  const ambient = createAmbient(7);
  let last = 0;
  for (const row of rows) {
    assertValidPlan(row.plan, label);
    assert.equal(row.plan.sequence, last + 1, `${label} sequence 要遞增`);
    last = row.plan.sequence;
    assert.equal(ambient.apply(row.plan, row.t), true, `${label} ambient 拒收 ${JSON.stringify(row.plan)}`);
  }
}

// ---------- 常數與契約 ----------

test('常數：情境順序、六個閒置劇本（第二版的動作不在）、游標換算', () => {
  assert.deepEqual([...CONTEXTS], ['peek', 'listening', 'typing', 'click', 'thinking', 'responding', 'cursor', 'idle']);
  assert.deepEqual(IDLE_ACTIONS, {
    rest_front: [3.0, 0], glance_aside: [2.6, 0], weight_shift: [1.8, 6], look_down: [1.2, 12], think_up: [1.1, 12], curious_tilt: [0.8, 14],
  });
  for (const second of ['hum_sway', 'hop', 'stretch']) assert.equal(second in IDLE_ACTIONS, false, second);
  assert.equal(CURSOR_PX, 400);
  assert.ok(Object.isFrozen(CONTEXTS) && Object.isFrozen(IDLE_ACTIONS));
});

// ---------- 可重現與合法性 ----------

/** 十分鐘的隨機事件劇本：模式、動作、輸入框、打字、點擊、游標、點頭，全部由 eventSeed 決定 */
function fuzzRun(attSeed, eventSeed, minutes = 10) {
  const ev = mulberry32(eventSeed);
  const r = rig(attSeed);
  const rows = [];
  const contexts = new Set();
  let cx = 0, cy = 0;
  for (let k = 0; k < minutes * 60 * 20; k++) {   // 50 ms 一格
    const t = r.now + 50;
    const roll = ev();
    if (roll < 0.004) r.st.pose = r.st.pose === 'out' ? 'peek' : 'out';
    else if (roll < 0.012) r.st.mode = ['idle', 'listening', 'executing', 'success', 'failure'][Math.floor(ev() * 5)];
    else if (roll < 0.02) r.st.motion = ['idle', 'nod', 'talk', 'shake', 'idle'][Math.floor(ev() * 5)];
    else if (roll < 0.024) r.st.inputOpen = !r.st.inputOpen;
    else if (roll < 0.04) r.att.typing(t);
    else if (roll < 0.044) r.att.click((ev() - 0.5) * 900, (ev() - 0.5) * 900, t);
    else if (roll < 0.048) r.att.heard(ev() < 0.7 ? 'command' : 'ignored', t);
    else if (roll < 0.2) { cx += (ev() - 0.5) * 80; cy += (ev() - 0.5) * 80; r.att.cursor(cx, cy, t); }
    const row = r.tick(50);
    contexts.add(row.context);
    if (row.plan) rows.push(row);
  }
  return { rows, contexts };
}

test('隨機事件十分鐘：每份計畫都合法、sequence 遞增、ambient 都肯收；八個情境都出現過', () => {
  const seen = new Set();
  for (const seed of [1, 2, 3]) {
    const { rows, contexts } = fuzzRun(seed, seed * 101);
    assert.ok(rows.length > 100, `seed ${seed} 只有 ${rows.length} 份計畫`);
    assertPlans(rows, `seed ${seed}`);
    for (const c of contexts) seen.add(c);
  }
  assert.deepEqual([...seen].sort(), [...CONTEXTS].sort());
});

test('同一個 seed 可重現，不同 seed 不同', () => {
  const a = fuzzRun(5, 77, 3), b = fuzzRun(5, 77, 3), c = fuzzRun(6, 77, 3);
  assert.deepEqual(a.rows, b.rows);
  assert.notDeepEqual(a.rows.map(r => r.plan.target), c.rows.map(r => r.plan.target));
});

test('不合法的輸入（NaN 時間、NaN 游標）不丟錯也不產生計畫', () => {
  const r = rig(1);
  r.run(2000);
  const before = r.att.snapshot();
  const res = r.att.update(NaN, r.st);
  assert.equal(res.plan, null);
  r.att.cursor(NaN, 3, r.now); r.att.click(1, NaN, r.now); r.att.typing(NaN); r.att.heard('command', NaN);
  assert.deepEqual(r.att.snapshot().target, before.target);
  assert.equal(r.tick().plan === null || typeof r.tick().plan === 'object', true);
});

// ---------- 1 潛伏（peek） ----------

test('peek：不產生計畫、target 回中性；離開時依新情境在同一次 update 出計畫', () => {
  const r = rig(2);
  r.run(8000);   // 閒置一陣子，目標不在中性
  assert.notDeepEqual(posePart(r.att.snapshot().target), posePart({ headX: 0, headY: 0, headZ: 0, bodyX: 0, bodyY: 0, bodyZ: 0 }));
  r.st.pose = 'peek'; r.st.mode = 'listening';   // peek 優先於一切
  r.att.typing(r.now); r.att.click(10, 10, r.now); r.st.inputOpen = true;
  const rows = r.run(30000, { each: (t) => { r.att.cursor((t % 300), 0, t); } });
  assert.equal(rows.length, 0);
  const snap = r.att.snapshot();
  assert.equal(snap.context, 'peek');
  assert.ok(AXES.every(a => snap.target[a] === 0));

  const listening = rig(2, { pose: 'peek' });
  listening.run(1000);
  listening.st.pose = 'out'; listening.st.mode = 'listening';
  const row = listening.tick();
  assert.equal(row.context, 'listening');
  assert.ok(row.plan, '離開 peek 的同一次 update 就有計畫');

  const idle = rig(2, { pose: 'peek' });
  idle.run(1000);
  idle.st.pose = 'out';
  const idleRow = idle.tick();
  assert.equal(idleRow.context, 'idle');
  assert.ok(idleRow.plan && idle.att.snapshot().action, '離開 peek 回閒置：直接開劇本，不另外看使用者');
});

test('peek 時 sway 不縮小、游標與點擊不會把情境拉走', () => {
  const r = rig(2, { pose: 'peek', mode: 'listening' });
  const row = r.tick();
  assert.equal(row.context, 'peek'); assert.equal(row.swayGain, 1);
});

// ---------- 2 聆聽 ----------

test('聆聽：進入時頭身＝目前目標 ×0.6 ± 0.065、視線回正、歪頭一次、moveMs 900～1500、swayGain 0.35', () => {
  for (let seed = 1; seed <= 30; seed++) {
    const r = rig(seed);
    r.run(15000);
    const before = r.att.snapshot().target;
    r.st.mode = 'listening';
    const row = r.tick();
    assert.equal(row.context, 'listening'); assert.equal(row.swayGain, 0.35);
    assert.ok(row.plan, `seed ${seed}：同一次 update 就出計畫`);
    const p = row.plan.target;
    assert.deepEqual([p.gazeX, p.gazeY], [0, 0]);
    assert.ok(inRange(row.plan.moveMs, 900, 1500), `moveMs ${row.plan.moveMs}`);
    for (const axis of POSE) {
      if (axis === 'headZ') continue;
      assert.ok(Math.abs(p[axis] - 0.6 * before[axis]) <= 0.065 + EPS, `seed ${seed} ${axis}`);
    }
    const tilt = Math.abs(p.headZ - 0.6 * before.headZ);
    assert.ok(tilt >= 0.1 - 0.065 - EPS && tilt <= 0.2 + 0.065 + EPS, `seed ${seed} 歪頭 ${tilt}`);
  }
});

test('聆聽：歪頭左右都會出現', () => {
  const signs = new Set();
  for (let seed = 1; seed <= 30; seed++) {
    const r = rig(seed, { pose: 'peek' });
    r.tick();
    r.st.pose = 'out'; r.st.mode = 'listening';
    signs.add(Math.sign(r.tick().plan.target.headZ));
  }
  assert.ok(signs.has(1) && signs.has(-1));
});

test('聆聽兩分鐘：情境一直是 listening、不出現劇本、不理游標；每 moveMs＋2.5～4.8 s 重新規劃、歪頭只有第一次', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const r = rig(seed);
    r.run(10000);
    r.st.mode = 'listening';
    const contexts = new Set();
    const rows = [];
    let x = 0;
    for (let k = 0; k < 6000; k++) {   // 120 s，游標一直大幅移動
      x += 37;
      r.att.cursor(x % 700 - 350, (x * 3) % 600 - 300, r.now + 20);
      const row = r.tick(20);
      contexts.add(row.context);
      assert.equal(row.swayGain, 0.35);
      if (row.plan) rows.push(row);
    }
    assert.deepEqual([...contexts], ['listening']);
    assert.ok(rows.length >= 120 / 6, `seed ${seed}：${rows.length} 份`);
    let prev = rows[0];
    assert.deepEqual([prev.plan.target.gazeX, prev.plan.target.gazeY], [0, 0]);
    let tilted = 0;
    for (const row of rows.slice(1)) {
      assert.deepEqual([row.plan.target.gazeX, row.plan.target.gazeY], [0, 0], '聆聽只看正前方');
      const gap = row.t - prev.t;
      assert.ok(gap >= prev.plan.moveMs + 2500 - 20 && gap <= prev.plan.moveMs + 4800 + 20, `重新規劃間隔 ${gap}（moveMs ${prev.plan.moveMs}）`);
      assert.ok(inRange(row.plan.moveMs, 900, 1500));
      for (const axis of POSE) if (Math.abs(row.plan.target[axis] - 0.6 * prev.plan.target[axis]) > 0.065 + EPS) tilted++;
      prev = row;
    }
    assert.equal(tilted, 0, '重新規劃不再加歪頭');
  }
});

test('聆聽：每次聆聽各歪一次；離開聆聽後回到閒置', () => {
  const r = rig(9);
  r.run(5000);
  let tilts = 0;
  for (let round = 0; round < 3; round++) {
    r.st.mode = 'listening';
    const before = r.att.snapshot().target;
    const row = r.tick();
    if (Math.abs(row.plan.target.headZ - 0.6 * before.headZ) > 0.065 + EPS) tilts++;
    r.run(1000);
    r.st.mode = 'idle';
    assert.equal(r.tick().context, 'idle');
    r.run(3000);
  }
  assert.equal(tilts, 3);
});

// ---------- 3 打字 ----------

test('打字：inputOpen 且 2 s 內有打字 → 視線 (0,-0.7)、headY -0.15、其他軸 ×0.6、moveMs 600～900；停打 2 s 改看使用者', () => {
  for (let seed = 1; seed <= 20; seed++) {
    const r = rig(seed);
    r.run(12000);
    const before = r.att.snapshot().target;
    r.st.inputOpen = true;
    r.att.typing(r.now);
    const row = r.tick();
    assert.equal(row.context, 'typing');
    const p = row.plan.target;
    assert.deepEqual([p.gazeX, p.gazeY, p.headY], [0, -0.7, -0.15]);
    for (const axis of ['headX', 'headZ', 'bodyX', 'bodyY', 'bodyZ']) assert.ok(Math.abs(p[axis] - 0.6 * before[axis]) <= EPS, axis);
    assert.ok(inRange(row.plan.moveMs, 600, 900));
  }
});

test('打字：持續打字不重送計畫；最後一個字後 2 s 離開打字、改看使用者；輸入框關閉立即結束', () => {
  const r = rig(4);
  r.run(5000);
  r.st.inputOpen = true;
  r.att.typing(r.now);
  const entered = r.tick();
  assert.equal(entered.context, 'typing');
  let typedAt = r.now;
  const rows = r.run(6000, { each: (t) => { if (t - typedAt >= 500) { r.att.typing(t); typedAt = t; } } });
  assert.equal(rows.length, 0, '打字期間不重送');
  assert.equal(r.att.snapshot().context, 'typing');
  // 最後一個字在 typedAt；1.9 s 還在打字，2.0 s 之後離開
  r.until(row => row.t >= typedAt + 1900, 3000);
  assert.equal(r.att.snapshot().context, 'typing');
  const leave = r.until(row => row.context !== 'typing', 1000, 10);
  assert.ok(leave.t - typedAt >= 2000 && leave.t - typedAt <= 2000 + 10);
  assert.equal(leave.context, 'idle');
  assert.deepEqual([leave.plan.target.gazeX, leave.plan.target.gazeY], [0, 0]);
  assert.ok(inRange(leave.plan.moveMs, 1000, 1500));

  const closed = rig(4);
  closed.run(2000);
  closed.st.inputOpen = true; closed.att.typing(closed.now);
  assert.equal(closed.tick().context, 'typing');
  closed.st.inputOpen = false;
  assert.equal(closed.tick().context, 'idle');
});

test('沒有開輸入框時打字事件不算', () => {
  const r = rig(4);
  r.att.typing(r.now);
  assert.equal(r.tick().context, 'idle');
});

// ---------- 4 點擊 ----------

test('點擊：視線＝點擊位置（px/400，夾 ±1）、頭 X／Y 跟 30～45%、其餘保留、moveMs 500～800', () => {
  for (let seed = 1; seed <= 20; seed++) {
    const r = rig(seed);
    r.run(9000);
    const before = r.att.snapshot().target;
    r.att.click(100, -200, r.now + 20);
    const row = r.tick();
    assert.equal(row.context, 'click');
    const p = row.plan.target;
    assert.deepEqual([p.gazeX, p.gazeY], [0.25, -0.5]);
    const ratio = p.headX / 0.25;
    assert.ok(inRange(ratio, 0.30, 0.45, 2e-3), `比例 ${ratio}`);
    assert.ok(Math.abs(p.headY / -0.5 - ratio) < 2e-3);
    for (const axis of ['headZ', 'bodyX', 'bodyY', 'bodyZ']) assert.equal(p[axis], before[axis], axis);
    assert.ok(inRange(row.plan.moveMs, 500, 800));
  }
  const far = rig(1);
  far.att.click(1000, 900, far.now + 20);
  const p = far.tick().plan.target;
  assert.deepEqual([p.gazeX, p.gazeY], [1, 1]);
});

test('點擊：從點擊起算 0.8～1.2 s 結束，之後回閒置並先看使用者', () => {
  const ends = [];
  for (let seed = 1; seed <= 40; seed++) {
    const r = rig(seed);
    r.run(6000);
    const at = r.now + 10;
    r.att.click(50, 50, at);
    r.tick(10);
    assert.equal(r.att.snapshot().context, 'click');
    const row = r.until(x => x.context !== 'click', 2000, 10);
    ends.push(row.t - at);
    assert.equal(row.context, 'idle');
    assert.deepEqual([row.plan.target.gazeX, row.plan.target.gazeY], [0, 0]);
  }
  assert.ok(Math.min(...ends) >= 800 && Math.max(...ends) <= 1200 + 10, `結束時間 ${Math.min(...ends)}～${Math.max(...ends)}`);
  assert.ok(Math.max(...ends) - Math.min(...ends) > 150, '長度有隨機性');
});

test('點擊期間再點一次：重新看新的位置、重新計時', () => {
  const r = rig(3);
  r.run(5000);
  r.att.click(100, 0, r.now + 10);
  r.tick(10);
  r.run(400);
  r.att.click(-200, 0, r.now + 10);
  const row = r.tick(10);
  assert.equal(row.context, 'click');
  assert.equal(row.plan.target.gazeX, -0.5);
});

test('點擊從 peek 出場：起算點仍是點擊的時刻', () => {
  const r = rig(3, { pose: 'peek' });
  r.run(1000);
  const at = r.now + 10;
  r.att.click(0, 100, at);
  r.st.pose = 'out';   // controller.click 同時讓角色出場
  const row = r.tick(10);
  assert.equal(row.context, 'click');
  const end = r.until(x => x.context !== 'click', 2000, 10);
  assert.ok(end.t - at >= 800 && end.t - at <= 1210);
});

// ---------- 5 思考 ----------

test('思考：executing 後 0.3～0.6 s 才出第一個計畫，往上側看（左右隨機）、頭微抬往同側歪、moveMs 1100～1600', () => {
  const sides = new Set();
  const delays = [];
  for (let seed = 1; seed <= 60; seed++) {
    const r = rig(seed);
    r.run(8000);
    r.st.mode = 'executing';
    const t0 = r.now + 10;
    const first = r.tick(10);
    assert.equal(first.context, 'thinking');
    assert.equal(first.plan, null);
    const row = r.until(x => x.plan, 1000, 10);
    delays.push(row.t - t0);
    const p = row.plan.target;
    const side = Math.sign(p.gazeX);
    sides.add(side);
    assert.ok(inRange(Math.abs(p.gazeX), 0.45, 0.7) && inRange(p.gazeY, 0.75, 0.95), `視線 ${p.gazeX},${p.gazeY}`);
    assert.ok(Math.sign(p.headX) === side && inRange(Math.abs(p.headX), 0.1, 0.25));
    assert.ok(inRange(p.headY, 0.25, 0.45));
    assert.ok(Math.sign(p.headZ) === side && inRange(Math.abs(p.headZ), 0.15, 0.35));
    assert.ok(inRange(p.bodyX, -0.15, 0.15) && inRange(p.bodyY, 0.05, 0.2) && Math.sign(p.bodyZ) === side && inRange(Math.abs(p.bodyZ), 0.05, 0.15));
    assert.ok(inRange(row.plan.moveMs, 1100, 1600));
  }
  assert.ok(Math.min(...delays) >= 300 - 10 && Math.max(...delays) <= 600 + 10, `延遲 ${Math.min(...delays)}～${Math.max(...delays)}`);
  assert.ok(sides.has(1) && sides.has(-1));
});

test('思考：之後每 1～2 s 只換眼神（頭身不變、原 gaze ±0.08～0.15）；result 到達才結束', () => {
  for (const seed of [1, 2, 3, 4]) {
    const r = rig(seed);
    r.run(8000);
    r.st.mode = 'executing';
    const rows = r.run(30000);
    assert.ok(rows.length >= 15, `seed ${seed}：${rows.length}`);
    assert.equal(r.att.snapshot().context, 'thinking');
    const [first, ...rest] = rows;
    const base = first.plan.target;
    let prev = first;
    for (const row of rest) {
      const p = row.plan.target;
      assert.ok(samePose(p, base), '頭身不動（ambient 才不會重啟頭身曲線）');
      assert.equal(row.plan.moveMs, first.plan.moveMs, 'moveMs 沿用');
      for (const axis of ['gazeX', 'gazeY']) {
        const diff = Math.abs(p[axis] - base[axis]);
        assert.ok(diff <= 0.15 + EPS && (diff >= 0.08 - EPS || Math.abs(p[axis]) === 1), `${axis} 差 ${diff}`);
      }
      const gap = row.t - prev.t;
      assert.ok(gap >= 1000 - 20 && gap <= 2000 + 20, `間隔 ${gap}`);
      prev = row;
    }
    r.st.mode = 'success'; r.st.motion = 'nod';
    assert.equal(r.tick().context, 'responding');
  }
});

// ---------- 6 回應 ----------

const settledRanges = (p) => inRange(p.headX, -0.18, 0.18) && inRange(p.headY, -0.15, 0.15) && inRange(p.headZ, -0.15, 0.15)
  && inRange(p.bodyX, -0.2, 0.2) && inRange(p.bodyY, -0.15, 0.15) && inRange(p.bodyZ, -0.2, 0.2);

test('回應（Nod／Shake）：mode 變 success／failure 時看使用者、頭身 settled；動作回 idle 後 1～2 s 結束，之後先看使用者', () => {
  const ends = [];
  for (let seed = 1; seed <= 40; seed++) {
    const r = rig(seed);
    r.run(5000);
    r.st.mode = 'executing';
    r.run(3000);
    r.st.mode = seed % 2 ? 'success' : 'failure'; r.st.motion = seed % 2 ? 'nod' : 'shake';
    const row = r.tick();
    assert.equal(row.context, 'responding');
    const p = row.plan.target;
    assert.deepEqual([p.gazeX, p.gazeY], [0, 0]);
    assert.ok(settledRanges(p), JSON.stringify(p));
    assert.ok(inRange(row.plan.moveMs, 800, 1200));
    r.run(1500);
    assert.equal(r.att.snapshot().context, 'responding', '動作播放中');
    r.st.motion = 'idle';
    const idleAt = r.now + 20;
    r.tick(20);
    r.run(1000);
    r.st.mode = 'idle';   // 表情 2 s 後復原：mode 回 idle，但還在動作後的 1～2 s 內才算結束
    const end = r.until(x => x.context !== 'responding', 3000, 10);
    ends.push(end.t - idleAt);
    assert.equal(end.context, 'idle');
    assert.deepEqual([end.plan.target.gazeX, end.plan.target.gazeY], [0, 0]);
    assert.ok(inRange(end.plan.moveMs, 1000, 1500));
  }
  assert.ok(Math.min(...ends) >= 1000 && Math.max(...ends) <= 2000 + 30, `結束 ${Math.min(...ends)}～${Math.max(...ends)}`);
  assert.ok(Math.max(...ends) - Math.min(...ends) > 300);
});

test('回應（Talk）：先移開視線 0.3～0.5 s（x ±0.3～0.5、y -0.1～0.15）再看回 (0,0)；mode 先回 idle 但 Talk 還在播就仍是回應', () => {
  for (let seed = 1; seed <= 40; seed++) {
    const r = rig(seed);
    r.run(4000);
    r.st.mode = 'success'; r.st.motion = 'talk';
    const row = r.tick(10);
    assert.equal(row.context, 'responding');
    const away = row.plan.target;
    assert.ok(inRange(Math.abs(away.gazeX), 0.3, 0.5) && inRange(away.gazeY, -0.1, 0.15), `${away.gazeX},${away.gazeY}`);
    assert.ok(settledRanges(away));
    const back = r.until(x => x.plan, 1000, 10);
    assert.ok(back.t - row.t >= 300 - 10 && back.t - row.t <= 500 + 10, `移開 ${back.t - row.t} ms`);
    assert.deepEqual([back.plan.target.gazeX, back.plan.target.gazeY], [0, 0]);
    assert.ok(samePose(back.plan.target, away), '只換眼神');
    assert.equal(back.plan.moveMs, row.plan.moveMs);
  }
  const r = rig(3);
  r.st.mode = 'success'; r.st.motion = 'talk';
  r.tick();
  r.run(2500);
  r.st.mode = 'idle';   // 2 s 後表情復原，Talk 最長 4 s 還在播
  r.run(1500);
  assert.equal(r.att.snapshot().context, 'responding');
});

test('回應：被新的 executing 蓋過就變思考；聆聽、peek 更高；沒有 success／failure 不會進入', () => {
  const r = rig(5);
  r.st.mode = 'success'; r.st.motion = 'nod';
  assert.equal(r.tick().context, 'responding');
  r.st.mode = 'executing'; r.st.motion = 'curious';
  assert.equal(r.tick().context, 'thinking');
  r.st.mode = 'success'; r.st.motion = 'nod';
  assert.equal(r.tick().context, 'responding');
  r.st.mode = 'listening';
  assert.equal(r.tick().context, 'listening');
  r.st.mode = 'idle'; r.st.motion = 'idle';
  assert.equal(r.tick().context, 'idle', '聆聽打斷後不再回到回應');
  r.st.mode = 'success'; r.st.motion = 'nod';
  r.st.pose = 'peek';
  assert.equal(r.tick().context, 'peek');
  const none = rig(5);
  none.st.motion = 'nod';   // 沒有結果事件，只是動作在播
  assert.equal(none.tick().context, 'idle');
});

// ---------- 7 游標 ----------

/** 游標以固定間隔移動：從 (x, y) 開始，每格加 (sx, sy)，每格 stepMs。回傳 { rows(有計畫的), entered(第一次變 cursor 的那列) } */
function drag(r, { x = 0, y = 0, sx = 0, sy = 0, n, stepMs = 50 }) {
  const rows = [];
  let entered = null;
  for (let i = 0; i < n; i++) {
    x += sx; y += sy;
    r.att.cursor(x, y, r.now + stepMs);
    const row = r.tick(stepMs);
    row.pos = [x, y];
    if (!entered && row.context === 'cursor') entered = row;
    if (row.plan) rows.push(row);
  }
  return { rows, entered, x, y };
}

test('游標吸引：0.5 s 內累計 ≥ 60 px 才算；慢慢飄不算', () => {
  const slow = rig(1);
  slow.run(6000);
  slow.att.cursor(0, 0, slow.now);
  const slowDrag = drag(slow, { sx: 9, n: 100, stepMs: 100 });   // 0.5 s 內最多 54 px
  assert.equal(slowDrag.entered, null);

  const fast = rig(1);
  fast.run(6000);
  fast.att.cursor(0, 0, fast.now);
  const burst = drag(fast, { sx: 13, n: 20, stepMs: 100 });   // 0.5 s 內 65 px
  assert.ok(burst.entered);

  const jump = rig(1);
  jump.run(6000);
  jump.att.cursor(0, 0, jump.now);
  jump.att.cursor(70, 0, jump.now + 10);
  assert.equal(jump.tick(10).context, 'cursor', '一次跳 70 px 也算');
});

test('游標吸引：第一份計畫在同一次 update；視線＝游標、頭 X／Y＝游標 ×k（k 0.30～0.45）、頭 Z 與身體保留、moveMs 600～900', () => {
  for (let seed = 1; seed <= 20; seed++) {
    const r = rig(seed);
    r.run(9000);
    const before = r.att.snapshot().target;
    r.att.cursor(0, 0, r.now);
    const { entered } = drag(r, { sx: 40, sy: 20, n: 3, stepMs: 20 });
    assert.ok(entered, `seed ${seed}`);
    assert.ok(entered.plan, '同一次 update 就出計畫');
    const p = entered.plan.target;
    const gx = entered.pos[0] / CURSOR_PX, gy = entered.pos[1] / CURSOR_PX;
    assert.ok(Math.abs(p.gazeX - gx) < EPS && Math.abs(p.gazeY - gy) < EPS);
    const k = p.headX / gx;
    assert.ok(inRange(k, 0.30, 0.45, 2e-3), `k ${k}`);
    assert.ok(Math.abs(p.headY / gy - k) < 5e-3);
    for (const axis of ['headZ', 'bodyX', 'bodyY', 'bodyZ']) assert.equal(p[axis], before[axis], axis);
    assert.ok(inRange(entered.plan.moveMs, 600, 900));
  }
});

test('游標追視：連續移動時每 ≥150 ms 掃視一次，k 在吸引時抽一次、整段不變', () => {
  const r = rig(8);
  r.run(9000);
  r.att.cursor(0, 0, r.now);
  let angle = 0;
  const rows = [];
  let entered = null;
  for (let i = 0; i < 300; i++) {   // 10 ms 一格，3 s，繞圈
    angle += 0.04;
    const x = 160 * Math.cos(angle), y = 160 * Math.sin(angle);
    r.att.cursor(x, y, r.now + 10);
    const row = r.tick(10);
    row.pos = [x, y];
    if (row.context === 'cursor') entered ??= row;
    if (row.plan) rows.push(row);
  }
  assert.ok(entered && rows.length >= 15, `${rows.length} 份`);
  const first = rows.find(row => Math.abs(row.pos[0]) > 80);
  const k = first.plan.target.headX / (first.pos[0] / CURSOR_PX);
  assert.ok(inRange(k, 0.30, 0.45, 5e-3));
  for (let i = 0; i < rows.length; i++) {
    const p = rows[i].plan.target;
    const gx = rows[i].pos[0] / CURSOR_PX, gy = rows[i].pos[1] / CURSOR_PX;
    assert.ok(Math.abs(p.gazeX - gx) < EPS && Math.abs(p.gazeY - gy) < EPS);
    assert.ok(Math.abs(p.headX - gx * k) < 2e-3 && Math.abs(p.headY - gy * k) < 2e-3, '頭跟 k 倍');
    assert.ok(inRange(rows[i].plan.moveMs, 600, 900));
    if (i > 0) {
      const gap = rows[i].t - rows[i - 1].t;
      assert.ok(gap >= 150, `掃視間隔 ${gap}`);
      assert.ok(gap <= 200, `游標一直在動，不該拖到 ${gap}`);
    }
  }
});

test('游標追視門檻：離目前注視點 < 0.08 不掃視，≥ 0.08 才掃視', () => {
  const r = rig(8);
  r.run(9000);
  r.att.cursor(0, 0, r.now);
  assert.ok(drag(r, { sx: 30, n: 3, stepMs: 20 }).entered);
  r.run(400);
  r.att.cursor(200, 0, r.now + 20);   // 把視線放在已知的位置 (0.5, 0)
  assert.ok(r.tick(20).plan);
  r.run(400);
  const nudge = (px) => {
    r.att.cursor(px, 0, r.now + 20);
    return r.tick(20);
  };
  assert.equal(nudge(231).plan, null, '31 px＝0.0775 < 0.08');
  const row = nudge(233);   // 離視線 33 px＝0.0825
  assert.ok(row.plan, '33 px＝0.0825 ≥ 0.08');
  assert.ok(Math.abs(row.plan.target.gazeX - 233 / CURSOR_PX) < EPS);
});

/** 游標在原地前後抖動 amount px，每 50 ms 一次，共 n 次 */
function wobble(r, x0, amount, n) {
  let x = x0;
  for (let i = 0; i < n; i++) {
    x += i % 2 ? -amount : amount;
    r.att.cursor(x, 0, r.now + 50);
    r.tick(50);
  }
}

test('失去興趣：游標靜止（0.3 s 內 < 8 px）1.5～3 s 回閒置並先看使用者；小抖動也算靜止，≥ 8 px 不算', () => {
  const ends = [];
  for (let seed = 1; seed <= 30; seed++) {
    const r = rig(seed);
    r.run(9000);
    r.att.cursor(0, 0, r.now);
    const a = drag(r, { sx: 30, n: 6, stepMs: 20 });
    assert.ok(a.entered);
    const lastMove = r.now;
    const end = r.until(x => x.context !== 'cursor', 5000);
    ends.push(end.t - lastMove);
    assert.equal(end.context, 'idle');
    assert.ok(end.plan, '回閒置先看使用者');
    assert.deepEqual([end.plan.target.gazeX, end.plan.target.gazeY], [0, 0]);
    assert.ok(inRange(end.plan.moveMs, 1000, 1500));
  }
  assert.ok(Math.min(...ends) >= 1800 - 20 && Math.max(...ends) <= 3000 + 300 + 40, `${Math.min(...ends)}～${Math.max(...ends)}`);
  assert.ok(Math.max(...ends) - Math.min(...ends) > 500);

  const jitter = rig(2);
  jitter.run(9000);
  jitter.att.cursor(0, 0, jitter.now);
  const j0 = drag(jitter, { sx: 30, n: 6, stepMs: 20 });
  wobble(jitter, j0.x, 1, 200);   // 0.3 s 內最多 6 px
  assert.equal(jitter.att.snapshot().context, 'idle', '微小抖動不算在動');

  const busy = rig(2);
  busy.run(9000);
  busy.att.cursor(0, 0, busy.now);
  const b0 = drag(busy, { sx: 30, n: 6, stepMs: 20 });
  wobble(busy, b0.x, 2, 200);   // 0.3 s 內 12 px，一直算在動
  assert.equal(busy.att.snapshot().context, 'cursor');
});

test('失去興趣後 2 s 冷卻：期間游標動了也不理，過了才會再被吸引', () => {
  const r = rig(6);
  r.run(9000);
  r.att.cursor(0, 0, r.now);
  const a = drag(r, { sx: 30, n: 6, stepMs: 20 });
  const lost = r.until(x => x.context !== 'cursor', 6000);
  const te = lost.t;
  let x = a.x;
  // 冷卻期間（0.5～1.5 s）猛動
  r.run(500);
  const during = drag(r, { x, sx: 25, n: 20, stepMs: 50 });
  x = during.x;
  assert.equal(during.entered, null);
  assert.equal(r.att.snapshot().context, 'idle');
  // 繼續動，跨過冷卻結束（te+2000）：只有冷卻之後的移動才算
  const after = drag(r, { x, sx: 25, n: 60, stepMs: 50 });
  assert.ok(after.entered);
  assert.ok(after.entered.t >= te + 2000 && after.entered.t <= te + 2000 + 300, `冷卻後 ${after.entered.t - te}`);
});

test('游標只在閒置時才會吸引：聆聽、打字、點擊、思考、回應期間的移動不觸發', () => {
  const states = {
    listening: (r) => { r.st.mode = 'listening'; },
    typing: (r) => { r.st.inputOpen = true; },
    click: (r) => { r.att.click(40, 40, r.now + 20); },
    thinking: (r) => { r.st.mode = 'executing'; },
    responding: (r) => { r.st.mode = 'success'; r.st.motion = 'nod'; },
  };
  for (const [name, setup] of Object.entries(states)) {
    const r = rig(4);
    r.run(6000);
    r.att.cursor(0, 0, r.now);
    setup(r);
    const typingTimer = name === 'typing' ? (t) => r.att.typing(t) : null;
    let x = 0;
    for (let i = 0; i < 20; i++) {   // 1 s 猛動
      x += 30;
      r.att.cursor(x, 0, r.now + 50);
      typingTimer?.(r.now + 50);
      const row = r.tick(50);
      assert.notEqual(row.context, 'cursor', `${name} 期間不該被游標吸引`);
      assert.notEqual(row.context, 'idle', `${name}：這 1 s 還在原本的情境`);
    }
    // 情境結束後（游標已停）回到閒置，沒有被吸引
    r.st.mode = 'idle'; r.st.motion = 'idle'; r.st.inputOpen = false;
    const end = r.until(row => row.context === 'idle', 8000, 50);
    assert.equal(end.context, 'idle', name);
  }
  const peek = rig(4, { pose: 'peek' });
  peek.att.cursor(0, 0, peek.now);
  for (let i = 0; i < 20; i++) { peek.att.cursor(i * 30, 0, peek.now + 50); assert.equal(peek.tick(50).context, 'peek'); }
});

// ---------- 8 閒置 ----------

test('閒置：從其他情境回來，第一步先看使用者（頭身 settled、視線 (0,0)、moveMs 1000～1500），停留 1.1～2.0 s 後才跑劇本', () => {
  for (let seed = 1; seed <= 30; seed++) {
    const r = rig(seed, { mode: 'listening' });
    r.run(3000);
    r.st.mode = 'idle';
    const row = r.tick();
    assert.equal(row.context, 'idle');
    const p = row.plan.target;
    assert.deepEqual([p.gazeX, p.gazeY], [0, 0]);
    assert.ok(settledRanges(p));
    assert.ok(inRange(row.plan.moveMs, 1000, 1500));
    assert.equal(r.att.snapshot().action, null, '還沒開始劇本');
    const next = r.until(x => x.plan, 5000);
    const gap = next.t - row.t;
    assert.ok(gap >= row.plan.moveMs + 1100 - 20 && gap <= row.plan.moveMs + 2000 + 20, `間隔 ${gap}（moveMs ${row.plan.moveMs}）`);
    assert.ok(r.att.snapshot().action);
  }
});

/** 閒置跑 minutes 分鐘（100 ms 一格），把計畫依劇本分組；groups[i] = { name, startedAt, plans, prev（劇本開始前最後一份計畫） } */
function idleGroups(seed, minutes) {
  const r = rig(seed);
  const groups = [];
  let serial = 0;
  let last = null;
  const rows = [];
  for (let k = 0; k < minutes * 600; k++) {
    const row = r.tick(100);
    assert.equal(row.context, 'idle');
    assert.equal(row.swayGain, 1);
    const action = r.att.snapshot().action;
    if (action && action.serial !== serial) {
      serial = action.serial;
      groups.push({ name: action.name, startedAt: action.startedAt, plans: [], prev: last });
    }
    if (row.plan) {
      rows.push(row);
      last = row.plan;
      if (action) groups[groups.length - 1].plans.push(row.plan);
    }
  }
  return { groups, rows };
}

const gazeOf = (p) => [p.target.gazeX, p.target.gazeY];
const bodyOnly = (a, b) => samePose(a.target, b.target);
const asDirection = ([x, y]) => [[[-0.75, -0.45], [-0.04, 0.04]], [[0.45, 0.75], [-0.04, 0.04]], [[-0.04, 0.04], [0.9, 1]], [[-0.04, 0.04], [-1, -0.95]],
  [[-0.8, -0.55], [0.85, 1]], [[0.55, 0.8], [0.85, 1]], [[-0.8, -0.55], [-1, -0.85]], [[0.55, 0.8], [-1, -0.85]]]
  .some(([xr, yr]) => inRange(x, xr[0], xr[1]) && inRange(y, yr[0], yr[1]));
const saccadeOk = ([x, y]) => { const radius = Math.hypot(x, y / 1.3); return radius >= 0.12 - EPS && radius <= 0.26 + EPS; };

test('閒置一小時：只出現六個劇本、冷卻被遵守、同一劇本不連續三次、歪頭不連續、計畫合法', () => {
  const { groups, rows } = idleGroups(11, 60);
  assertPlans(rows);
  const names = groups.map(g => g.name);
  assert.deepEqual([...new Set(names)].sort(), Object.keys(IDLE_ACTIONS).sort(), '六個劇本都出現過，沒有別的');
  assert.ok(groups.length > 200, `${groups.length} 個劇本`);
  const last = {};
  groups.forEach((g, i) => {
    const cooldown = IDLE_ACTIONS[g.name][1] * 1000;
    if (last[g.name] !== undefined) assert.ok(g.startedAt - last[g.name] >= cooldown, `${g.name} 冷卻 ${g.startedAt - last[g.name]} < ${cooldown}`);
    last[g.name] = g.startedAt;
    if (i >= 2) assert.ok(!(names[i - 1] === g.name && names[i - 2] === g.name), `${g.name} 連續三次`);
    if (i >= 1 && g.name === 'curious_tilt') assert.notEqual(names[i - 1], 'curious_tilt');
  });
  const count = (n) => names.filter(x => x === n).length;
  assert.ok(count('rest_front') > count('curious_tilt') && count('glance_aside') > count('curious_tilt'), '權重高的出現得比較多');
  for (const n of Object.keys(IDLE_ACTIONS)) assert.ok(count(n) > 5, `${n} 只出現 ${count(n)} 次`);
});

test('各個閒置劇本的步驟形狀（rest_front、glance_aside、look_down、think_up、weight_shift、curious_tilt）', () => {
  const byName = {};
  for (const seed of [21, 22, 23]) for (const g of idleGroups(seed, 30).groups.slice(0, -1)) (byName[g.name] ??= []).push(g);
  for (const name of Object.keys(IDLE_ACTIONS)) assert.ok((byName[name] ?? []).length >= 10, `${name} 樣本太少`);

  for (const { plans: ps } of byName.rest_front) {
    assert.ok(ps.length >= 3 && ps.length <= 5, `rest_front 長度 ${ps.length}`);
    assert.deepEqual(gazeOf(ps[0]), [0, 0]);
    assert.ok(inRange(ps[0].moveMs, 1100, 2000) && settledRanges(ps[0].target));
    ps.slice(1).forEach(p => assert.ok(bodyOnly(p, ps[0]), '之後只換眼神'));
    ps.slice(1, -1).forEach(p => assert.ok(saccadeOk(gazeOf(p)), `微對焦 ${gazeOf(p)}`));
    assert.deepEqual(gazeOf(ps[ps.length - 1]), [0, 0]);
  }
  for (const { plans: ps, prev } of byName.glance_aside) {
    assert.ok(ps.length === 4 || ps.length === 5, `glance_aside 長度 ${ps.length}`);
    assert.ok(bodyOnly(ps[0], prev), '眼先動：頭身維持原樣');
    assert.ok(asDirection(gazeOf(ps[0])), `方向 ${gazeOf(ps[0])}`);
    assert.ok(inRange(ps[1].moveMs, 800, 1400));
    assert.ok(Math.abs(ps[1].target.gazeX - ps[0].target.gazeX * 0.85) < 2e-4);
    assert.ok(Math.sign(ps[1].target.headX) === Math.sign(ps[0].target.gazeX) || Math.abs(ps[1].target.headX) < EPS);
    if (ps.length === 5) assert.ok(bodyOnly(ps[2], ps[1]));
    assert.deepEqual(gazeOf(ps[ps.length - 2]), [0, 0]);
    assert.ok(bodyOnly(ps[ps.length - 2], ps[1]));
    assert.ok(inRange(ps[ps.length - 1].moveMs, 1000, 1600) && settledRanges(ps[ps.length - 1].target));
  }
  for (const { plans: ps, prev } of byName.look_down) {
    assert.ok(ps.length === 4 || ps.length === 5, `look_down 長度 ${ps.length}`);
    assert.ok(bodyOnly(ps[0], prev));
    assert.ok(inRange(ps[0].target.gazeY, -0.95, -0.7) && inRange(ps[0].target.gazeX, -0.3, 0.3));
    assert.ok(inRange(ps[1].moveMs, 1200, 1800) && inRange(ps[1].target.headY, -0.65, -0.4) && inRange(ps[1].target.bodyY, -0.3, -0.1));
    if (ps.length === 5) assert.ok(inRange(ps[2].target.gazeY, -0.9, -0.6) && bodyOnly(ps[2], ps[1]));
    const raise = ps[ps.length - 2];
    assert.ok(raise.target.gazeX === 0 && inRange(raise.target.gazeY, 0.35, 0.55) && bodyOnly(raise, ps[1]), '頭還低，先抬眼睛');
    assert.ok(inRange(ps[ps.length - 1].moveMs, 1000, 1500) && settledRanges(ps[ps.length - 1].target));
  }
  for (const { plans: ps, prev } of byName.think_up) {
    assert.ok(ps.length === 4 || ps.length === 5, `think_up 長度 ${ps.length}`);
    assert.ok(bodyOnly(ps[0], prev));
    const side = Math.sign(ps[0].target.gazeX);
    assert.ok(inRange(Math.abs(ps[0].target.gazeX), 0.45, 0.7) && inRange(ps[0].target.gazeY, 0.75, 0.95));
    assert.ok(inRange(ps[1].moveMs, 1100, 1600) && inRange(ps[1].target.headY, 0.25, 0.45) && Math.sign(ps[1].target.headZ) === side);
    if (ps.length === 5) assert.ok(inRange(ps[2].target.gazeY, 0.7, 0.95) && Math.sign(ps[2].target.gazeX) === -side);
    const down = ps[ps.length - 2];
    assert.ok(down.target.gazeX === 0 && inRange(down.target.gazeY, -0.35, -0.2) && bodyOnly(down, ps[1]));
    assert.ok(inRange(ps[ps.length - 1].moveMs, 1000, 1500) && settledRanges(ps[ps.length - 1].target));
  }
  for (const { plans: ps } of byName.weight_shift) {
    assert.ok(ps.length === 1 || ps.length === 3, `weight_shift 長度 ${ps.length}`);
    const t = ps[0].target;
    assert.ok(inRange(ps[0].moveMs, 1400, 2200));
    assert.ok(inRange(Math.abs(t.bodyX), 0.5, 0.85) && Math.sign(t.bodyZ) === Math.sign(t.bodyX) && inRange(Math.abs(t.bodyZ), 0.45, 0.8));
    assert.ok(Math.sign(t.headX) === -Math.sign(t.bodyX) && inRange(Math.abs(t.headZ), 0.1, 0.3), '頭反向平衡');
    if (ps.length === 3) {
      assert.ok(bodyOnly(ps[1], ps[0]) && bodyOnly(ps[2], ps[0]));
      assert.ok(saccadeOk(gazeOf(ps[1])));
      assert.deepEqual(gazeOf(ps[2]), [0, 0]);
    }
  }
  for (const { plans: ps, prev } of byName.curious_tilt) {
    assert.equal(ps.length, 4);
    const t = ps[0].target;
    assert.ok(inRange(ps[0].moveMs, 900, 1400) && inRange(Math.abs(t.headZ), 0, 0.72) && inRange(t.headY, 0, 0.2));
    assert.ok(Math.abs(t.headZ - prev.target.headZ) <= 0.78 + EPS, '單次歪斜變化量不超過上限');
    assert.ok(saccadeOk(gazeOf(ps[1])) && bodyOnly(ps[1], ps[0]));
    assert.deepEqual(gazeOf(ps[2]), [0, 0]);
    assert.ok(bodyOnly(ps[2], ps[0]));
    assert.ok(inRange(ps[3].moveMs, 1000, 1500) && settledRanges(ps[3].target));
  }
});

test('只換眼神的計畫：頭身 target 和上一份完全相同（同一個數值）', () => {
  const { rows } = idleGroups(31, 20);
  let gazeOnly = 0;
  for (let i = 1; i < rows.length; i++) {
    const same = samePose(rows[i].plan.target, rows[i - 1].plan.target);
    if (same) { gazeOnly++; assert.equal(rows[i].plan.moveMs, rows[i - 1].plan.moveMs, 'moveMs 沿用'); }
  }
  assert.ok(gazeOnly > 100, `只換眼神的計畫 ${gazeOnly} 份`);
});

// ---------- 輕點一下頭（覆蓋層） ----------

/** 打字情境沒有後續計畫，最適合看點頭：回傳 { r, base（點頭前的 headY） } */
function typingRig(seed) {
  const r = rig(seed);
  r.run(5000);
  r.st.inputOpen = true;
  r.att.typing(r.now);
  r.tick();
  return { r, base: r.att.snapshot().target };
}

test('heard command：先低頭 0.12（250 ms）、250 ms 後回原值（350 ms）；其他軸不變、情境不變', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const { r, base } = typingRig(seed);
    r.att.heard('command', r.now + 10);
    const rows = [];
    let contexts = new Set();
    for (let i = 0; i < 100; i++) {   // 1 s，邊打字邊看
      r.att.typing(r.now + 10);
      const row = r.tick(10);
      contexts.add(row.context);
      if (row.plan) rows.push(row);
    }
    assert.deepEqual([...contexts], ['typing']);
    assert.equal(rows.length, 2, `seed ${seed}：${rows.length} 份`);
    const [down, up] = rows;
    assert.equal(down.plan.moveMs, 250);
    assert.ok(Math.abs(down.plan.target.headY - (base.headY - 0.12)) < 2e-4);
    for (const axis of AXES.filter(a => a !== 'headY')) assert.equal(down.plan.target[axis], base[axis], axis);
    assert.equal(up.plan.moveMs, 350);
    assert.equal(up.plan.target.headY, base.headY);
    for (const axis of AXES.filter(a => a !== 'headY')) assert.equal(up.plan.target[axis], base[axis], axis);
    assert.ok(up.t - down.t >= 250 && up.t - down.t <= 260, `間隔 ${up.t - down.t}`);
  }
});

test('heard 的其他狀態（ignored、armed）不點頭', () => {
  for (const status of ['ignored', 'armed']) {
    const { r } = typingRig(2);
    r.att.heard(status, r.now + 10);
    assert.equal(r.run(2000, { each: (t) => r.att.typing(t) }).length, 0, status);
  }
});

test('點頭覆蓋期間（600 ms）其他情境的計畫延後到結束才送', () => {
  for (let seed = 1; seed <= 20; seed++) {
    const r = rig(seed);
    r.run(6000);
    r.st.mode = 'listening';
    r.run(1500);
    r.st.mode = 'executing';   // controller 在 heard 之後馬上 executing：思考的第一份計畫 0.3～0.6 s 到期，落在覆蓋期間內
    const startAt = r.now + 10;
    r.att.heard('command', startAt);
    const rows = r.run(1500, { step: 10 });
    const [down, up, ...later] = rows;
    assert.equal(down.plan.moveMs, 250); assert.equal(up.plan.moveMs, 350);
    assert.ok(later.length >= 1);
    assert.ok(later[0].t >= down.t + 600, `延到覆蓋結束才送：${later[0].t - down.t}`);
    assert.ok(later[0].t <= down.t + 600 + 10);
    assert.ok(inRange(Math.abs(later[0].plan.target.gazeX), 0.45, 0.7), '送的是思考的第一份計畫');
    assert.ok(down.t - startAt <= 10);
  }
});

test('點頭：listening 立即生效並取消覆蓋；peek 也取消', () => {
  const { r, base } = typingRig(3);
  r.att.heard('command', r.now + 10);
  const dip = r.tick(10);
  assert.equal(dip.plan.moveMs, 250);
  r.st.mode = 'listening';
  const row = r.tick(10);
  assert.equal(row.context, 'listening');
  assert.ok(row.plan && row.plan.moveMs !== 350, '聆聽的計畫立即送出');
  assert.ok(Math.abs(row.plan.target.headY - 0.6 * base.headY) <= 0.065 + EPS, '以點頭之前的目標計算，不把低頭帶進去');
  const rest = r.run(1500, { step: 10 });
  assert.ok(rest.every(x => x.plan.moveMs !== 350), '回原值的計畫被取消');

  const peek = typingRig(3);
  peek.r.att.heard('command', peek.r.now + 10);
  assert.equal(peek.r.tick(10).plan.moveMs, 250);
  peek.r.st.pose = 'peek';
  const row2 = peek.r.tick(10);
  assert.equal(row2.context, 'peek'); assert.equal(row2.plan, null);
  assert.equal(peek.r.run(1500, { step: 10 }).length, 0);

  const pending = typingRig(3);
  pending.r.att.heard('command', pending.r.now + 10);
  pending.r.st.pose = 'peek';   // 還沒送出就被取消
  pending.r.tick(10);
  pending.r.st.pose = 'out';
  assert.ok(pending.r.run(1500, { step: 10 }).every(x => x.plan.moveMs !== 250 && x.plan.moveMs !== 350));
});

// ---------- 優先順序 ----------

test('優先順序：peek > listening > typing > click > thinking > responding > cursor > idle', () => {
  const make = (state, events = () => {}) => {
    const r = rig(7, { pose: 'peek' });
    r.tick();
    r.st.pose = 'out';
    Object.assign(r.st, state);
    events(r);
    return r.tick().context;
  };
  const typing = (r) => { r.st.inputOpen = true; r.att.typing(r.now + 10); };
  const clicked = (r) => r.att.click(20, 20, r.now + 10);
  const everything = (r) => { typing(r); clicked(r); };
  assert.equal(make({ pose: 'peek', mode: 'listening' }, everything), 'peek');
  assert.equal(make({ mode: 'listening' }, everything), 'listening');
  assert.equal(make({ mode: 'executing' }, everything), 'typing');
  assert.equal(make({ mode: 'executing' }, clicked), 'click');
  assert.equal(make({ mode: 'executing' }), 'thinking');
  assert.equal(make({ mode: 'success', motion: 'nod' }), 'responding');
  assert.equal(make({}), 'idle');

  // 點擊結束後才輪到思考；打字結束後輪到點擊（還在 0.8～1.2 s 內）再輪到思考
  const r = rig(7);
  r.st.mode = 'executing';
  r.att.click(20, 20, r.now + 10);
  assert.equal(r.tick(10).context, 'click');
  assert.equal(r.until(x => x.context !== 'click', 2000, 10).context, 'thinking');

  const typed = rig(7);
  typed.st.mode = 'executing'; typed.st.inputOpen = true;
  typed.att.click(20, 20, typed.now + 10); typed.att.typing(typed.now + 10);
  assert.equal(typed.tick(10).context, 'typing');
  typed.st.inputOpen = false;   // 輸入框關了，點擊視窗還在
  const afterTyping = typed.tick(10);
  assert.ok(afterTyping.context === 'click' || afterTyping.context === 'thinking');
});

test('回應中游標吸引中被蓋過：cursor → responding → idle', () => {
  const r = rig(2);
  r.run(6000);
  r.att.cursor(0, 0, r.now);
  assert.ok(drag(r, { sx: 30, n: 5, stepMs: 20 }).entered);
  r.st.mode = 'success'; r.st.motion = 'nod';
  assert.equal(r.tick().context, 'responding');
  r.st.motion = 'idle'; r.st.mode = 'idle';
  assert.equal(r.until(x => x.context !== 'responding', 3000).context, 'idle');
});

test('snapshot：情境、起始時間、目標（複本）', () => {
  const r = rig(2);
  assert.equal(r.att.snapshot().context, 'peek');
  r.st.mode = 'listening';
  const row = r.tick();
  const snap = r.att.snapshot();
  assert.equal(snap.context, 'listening'); assert.equal(snap.since, row.t);
  assert.deepEqual(snap.target, row.plan.target);
  snap.target.headX = 99;
  assert.notEqual(r.att.snapshot().target.headX, 99);
});
