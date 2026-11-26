// 注意力導演測試（一）：常數、可重現與合法性、情境 1～6（peek、聆聽 §4.6、打字、點擊、思考、回應）。游標、閒置劇本、點頭覆蓋層、優先順序在 attention-cursor-idle.test.js。node --test test/
import test from 'node:test';
import assert from 'node:assert/strict';
import { CONTEXTS, CURSOR_PX, IDLE_ACTIONS } from '../domain/attention.js';
import { AXES, POSE, EPS, mulberry32, rig, inRange, posePart, samePose, assertPlans, settledRanges } from './support/attention.js';

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
