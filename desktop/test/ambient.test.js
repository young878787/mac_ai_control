import test from 'node:test';
import assert from 'node:assert/strict';
import { AXES, createAmbient, createRandom } from '../renderer/animation/ambient.js';

const POSE = AXES.filter(a => !a.startsWith('gaze'));
const ZERO = Object.fromEntries(AXES.map(a => [a, 0]));
const plan = (sequence, target = {}, moveMs = 1000) => ({ sequence, moveMs, target: { ...ZERO, ...target } });
const close = (a, b, eps = 1e-6, msg = '') => assert.ok(Math.abs(a - b) <= eps, `${msg} ${a} 與 ${b} 差 ${Math.abs(a - b)}`);

/** 以固定幀率跑 seconds 秒，回傳每一幀的輸出（第 k 幀的時間＝k×1000/hz 毫秒；第 0 幀 dt＝0，之後 dt＝1/hz，所以漂移時鐘和 nowMs 同步）。events[k](now, amb) 在第 k 幀的 update 之前執行；opts 可以是物件或 (k) → 物件 */
function drive(amb, { hz = 60, seconds = 1, events = {}, opts = {} } = {}) {
  const out = [];
  for (let k = 0; k <= Math.round(seconds * hz); k++) {
    const now = k * 1000 / hz;
    events[k]?.(now, amb);
    out.push(amb.update(k === 0 ? 0 : 1 / hz, now, typeof opts === 'function' ? opts(k) : opts));
  }
  return out;
}
const stdev = (xs) => { const m = xs.reduce((s, x) => s + x, 0) / xs.length; return Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / xs.length); };

test('createRandom：[0,1)、同一個 seed 逐次相同、不同 seed 不同', () => {
  const a = createRandom(123), b = createRandom(123), c = createRandom(124);
  const xs = Array.from({ length: 1000 }, a);
  assert.deepEqual(xs, Array.from({ length: 1000 }, b));
  assert.ok(xs.every(x => x >= 0 && x < 1));
  assert.notDeepEqual(xs.slice(0, 10), Array.from({ length: 10 }, c));
  assert.ok(Math.abs(xs.reduce((s, x) => s + x, 0) / xs.length - 0.5) < 0.05);
});

test('輸出恰好是八個軸，值都在 ±1 內', () => {
  const rows = drive(createAmbient(1), { seconds: 5 });
  assert.deepEqual(Object.keys(rows[0]).sort(), [...AXES].sort());
  for (const row of rows) for (const axis of AXES) assert.ok(Math.abs(row[axis]) <= 1, axis);
});

// ---- 計畫格式 ----

test('無效計畫被拒絕（apply 回 false、目標不變），合格的接受', () => {
  const a = createAmbient(1);
  assert.equal(a.apply(plan(1, { headX: 0.5 }), 0), true);
  const keep = a.target();
  assert.equal(keep.headX, 0.5);
  const bad = [
    ['sequence 沒遞增（等於上一個）', plan(1, { headX: 0.1 })],
    ['sequence 比上一個小', plan(0, { headX: 0.1 })],
    ['sequence 非整數', plan(2.5, { headX: 0.1 })],
    ['sequence 不是數字', plan('3', { headX: 0.1 })],
    ['moveMs 太小', plan(3, { headX: 0.1 }, 99)],
    ['moveMs 太大', plan(3, { headX: 0.1 }, 3001)],
    ['moveMs NaN', plan(3, { headX: 0.1 }, Number.NaN)],
    ['目標超過 +1', plan(3, { gazeX: 1.01 })],
    ['目標超過 -1', plan(3, { bodyZ: -1.01 })],
    ['目標 NaN', plan(3, { headY: Number.NaN })],
    ['缺軸', { sequence: 3, moveMs: 1000, target: Object.fromEntries(AXES.slice(1).map(x => [x, 0])) }],
    ['多餘的軸', { sequence: 3, moveMs: 1000, target: { ...ZERO, extra: 0 } }],
    ['沒有 target', { sequence: 3, moveMs: 1000 }],
    ['null', null],
    ['不是物件', 'plan'],
  ];
  for (const [name, p] of bad) {
    assert.equal(a.apply(p, 10), false, name);
    assert.deepEqual(a.target(), keep, `${name}：目標不應改變`);
  }
  assert.equal(a.apply(plan(2, { headX: -0.5 }), 10), true);     // 被拒的不會佔用 sequence
  assert.equal(a.apply(plan(3, { headX: 1, gazeY: -1 }, 100), 20), true);   // 邊界值合格
  assert.equal(a.apply(plan(4, {}, 3000), 30), true);
  assert.equal(a.apply(plan(5), Number.NaN), false);              // 時間不是有限數
});

test('target() 回傳副本；reset 後 sequence 與目標都歸零', () => {
  const a = createAmbient(1);
  a.apply(plan(5, { headY: 0.3 }), 0);
  const t = a.target();
  t.headY = 99;
  assert.equal(a.target().headY, 0.3);
  a.reset();
  assert.deepEqual(a.target(), ZERO);
  assert.equal(a.apply(plan(1, { headY: 0.2 }), 0), true);
});

// ---- 連續性 ----

test('換目標時位置連續：套用當下起，頭晚 80 ms、身體晚 200 ms 才離開原軌跡，眼睛立刻動', () => {
  const hz = 120, at = 120;                                       // 第 120 幀＝1000 ms
  const run = (events) => drive(createAmbient(7), { hz, seconds: 3, events });
  const base = run({});
  const moved = run({ [at]: (now, a) => a.apply(plan(1, { headX: 0.5, bodyX: 0.4, headZ: 0.3, gazeX: 0.6 }), now) });
  const firstDiff = (axis) => moved.findIndex((row, k) => Math.abs(row[axis] - base[k][axis]) > 1e-9);
  for (let k = 0; k <= at; k++) for (const axis of AXES) assert.equal(moved[k][axis], base[k][axis], `套用前 ${axis} 第 ${k} 幀`);
  assert.equal(moved[at].gazeX, base[at].gazeX, '套用那一幀的位置與沒套用時相同（從目前位置出發）');
  assert.equal(firstDiff('gazeX'), at + 1, '眼睛下一幀就動');
  const lagFrames = (ms) => Math.round(ms / 1000 * hz);
  // 頭：套用後約 80 ms 才開始偏離（headX 也含歪頭耦合，所以量 headZ 與 headX 兩者）
  for (const axis of ['headX', 'headZ']) assert.ok(firstDiff(axis) - at >= lagFrames(80) - 1 && firstDiff(axis) - at <= lagFrames(80) + 2, `${axis} 起動在 ${firstDiff(axis) - at} 幀後`);
  assert.ok(firstDiff('bodyX') - at >= lagFrames(200) - 1 && firstDiff('bodyX') - at <= lagFrames(200) + 2, `bodyX 起動在 ${firstDiff('bodyX') - at} 幀後`);
  assert.equal(firstDiff('headY') > at, true);
});

test('換目標時速度連續：移動中途換目標，六個頭身軸在套用與接手的前後沒有速度跳變', () => {
  // 第二份計畫在第一份走到一半時套用（速度不為零）。headZ 兩份同號：headY 含 |歪頭| 耦合，過 0 的折點是參考專案就有的特性，不在這裡測
  const hz = 1000;                                                // 1 ms 一幀：二階差分 ≈ 加速度 × 1e-6，速度跳變 δv 會讓它多出 δv × 1e-3
  const events = {
    1000: (now, a) => a.apply(plan(1, { headX: 0.5, headY: -0.3, headZ: 0.3, bodyX: 0.4, bodyY: 0.3, bodyZ: -0.3 }), now),
    1600: (now, a) => a.apply(plan(2, { headX: -0.3, headY: 0.4, headZ: 0.15, bodyX: -0.2, bodyY: -0.2, bodyZ: 0.4 }), now),
  };
  for (const seed of [7, 8, 9]) {
    const rows = drive(createAmbient(seed), { hz, seconds: 5, events });
    let worst = 0, travelled = 0;
    for (let k = 2; k < rows.length; k++) {
      for (const axis of POSE) {
        if (k >= 900) worst = Math.max(worst, Math.abs(rows[k][axis] - 2 * rows[k - 1][axis] + rows[k - 2][axis]));
        if (k >= 1600 && k <= 1700) travelled += Math.abs(rows[k][axis] - rows[k - 1][axis]);
      }
    }
    // 平順曲線的峰值約 9.4e-6（實測，含歪頭耦合）；0.016／秒以上的速度跳變就會超過 2.5e-5。軸的最大速度約 0.5／秒
    assert.ok(worst < 2.5e-5, `seed ${seed} 二階差分最大 ${worst}`);
    assert.ok(travelled > 0.05, `第二份計畫確實在動（${travelled}）`);
  }
});

test('換目標時眼睛位置連續（掃視從目前位置出發，不跳）', () => {
  const events = { 100: (now, a) => a.apply(plan(1, { gazeX: 0.7, gazeY: -0.4 }), now) };
  const a = drive(createAmbient(3), { hz: 120, seconds: 2, events });
  const b = drive(createAmbient(3), { hz: 120, seconds: 2 });
  for (const axis of ['gazeX', 'gazeY']) {
    assert.equal(a[100][axis], b[100][axis]);
    // 套用後第一幀只走掃視的第一小步（35～80 ms 走完，120 Hz 一幀 8.3 ms；五次曲線起步很慢）
    assert.ok(Math.abs(a[101][axis] - b[101][axis]) < 0.05, `${axis} 第一步 ${a[101][axis] - b[101][axis]}`);
  }
  assert.ok(Math.abs(a[130].gazeX - b[130].gazeX) > 0.5, '之後確實到位');
});

test('只換眼神的計畫不重啟頭身曲線', () => {
  const hz = 120;
  const first = { 60: (now, a) => a.apply(plan(1, { headX: 0.5, headZ: 0.3, bodyX: 0.4 }), now) };
  const both = { ...first, 150: (now, a) => a.apply(plan(2, { headX: 0.5, headZ: 0.3, bodyX: 0.4, gazeX: -0.6, gazeY: 0.3 }), now) };
  const a = drive(createAmbient(9), { hz, seconds: 4, events: both });
  const b = drive(createAmbient(9), { hz, seconds: 4, events: first });
  for (let k = 0; k < a.length; k++) for (const axis of POSE) assert.equal(a[k][axis], b[k][axis], `${axis} 第 ${k} 幀`);
  assert.ok(Math.abs(a[400].gazeX - b[400].gazeX) > 0.3, '眼神確實換了');
});

// ---- 幀率 ----

test('30／60／120 Hz 跑同一段時間、同一組 apply 時間點，同一時刻的輸出相同', () => {
  const stamps = [];                                              // 每 500 ms 取一次
  const run = (hz) => {
    const a = createAmbient(11);
    const out = {};
    const events = {};
    events[hz * 1] = (now) => a.apply(plan(1, { headX: 0.5, headZ: 0.3, bodyX: 0.4, gazeX: 0.5, gazeY: -0.2 }), now);
    events[hz * 4] = (now) => a.apply(plan(2, { headX: -0.4, headZ: -0.2, bodyX: -0.3, gazeX: -0.6 }, 1500), now);
    drive(a, { hz, seconds: 10, events }).forEach((row, k) => { if ((k * 1000 / hz) % 500 === 0) out[k * 1000 / hz] = row; });
    return out;
  };
  const r30 = run(30), r60 = run(60), r120 = run(120);
  for (const t of Object.keys(r120)) {
    stamps.push(t);
    for (const axis of AXES) {
      close(r30[t][axis], r120[t][axis], 1e-6, `30 vs 120 Hz，${axis}，${t} ms`);
      close(r60[t][axis], r120[t][axis], 1e-6, `60 vs 120 Hz，${axis}，${t} ms`);
    }
  }
  assert.equal(stamps.length, 21);
});

// ---- 掃視 ----

/** 在 A、B 兩個視線目標間來回換，用 1 ms 取樣量「從 apply 到視線停住」的毫秒數；只採用前後沒有微掃視干擾的那次（停住後 100～250 ms 輸出完全不變） */
function saccadeMs(seed, a, b) {
  const amb = createAmbient(seed);
  let now = 0, seq = 0;
  const tick = () => amb.update(0.001, now++);
  for (let k = 0; k < 500; k++) tick();
  amb.apply(plan(++seq, { gazeX: a[0], gazeY: a[1] }), now);
  for (let k = 0; k < 500; k++) tick();                            // 先走到 A 並停住
  for (let attempt = 0; attempt < 60; attempt++) {
    const [x, y] = attempt % 2 === 0 ? b : a;
    for (let k = 0; k < 137; k++) tick();
    amb.apply(plan(++seq, { gazeX: x, gazeY: y }), now);
    const g = Array.from({ length: 250 }, () => { const o = tick(); return [o.gazeX, o.gazeY]; });
    const final = g[249];
    if (!g.slice(100).every(v => v[0] === final[0] && v[1] === final[1])) continue;
    return g.findIndex(v => v[0] === final[0] && v[1] === final[1]);
  }
  assert.fail('找不到沒有微掃視干擾的取樣窗');
}

test('掃視時間 40～80 ms，隨距離增加：近 40、中 55、遠 80', () => {
  for (const seed of [1, 2, 3]) {
    const near = saccadeMs(seed, [0, 0], [0.05, 0]);               // 距離 0.05 → 37.5 → 夾到 40
    const mid = saccadeMs(seed, [0, 0], [0.4, 0]);                 // 35 + 50×0.4 = 55
    const far = saccadeMs(seed, [-0.5, 0.2], [0.5, 0.2]);          // 35 + 50×1.0 = 85 → 夾到 80
    assert.ok(Math.abs(near - 40) <= 1, `近 ${near}`);
    assert.ok(Math.abs(mid - 55) <= 1, `中 ${mid}`);
    assert.ok(Math.abs(far - 80) <= 1, `遠 ${far}`);
  }
});

// ---- 微掃視與起點 ----

test('視線不會剛好在 (0, 0)：閒置 60 秒，沒有任何一幀兩軸都小於 0.001（多個 seed、含 reset 之後）', () => {
  for (const seed of [1, 2, 3, 42, 999, 0x2f6e2b1]) {
    const a = createAmbient(seed);
    for (const round of [0, 1]) {
      if (round) a.reset();
      const rows = drive(a, { hz: 60, seconds: 60 });
      const centered = rows.filter(r => Math.abs(r.gazeX) < 0.001 && Math.abs(r.gazeY) < 0.001).length;
      assert.equal(centered, 0, `seed ${seed} 第 ${round} 輪有 ${centered} 幀在正中`);
      assert.ok(Math.abs(rows[0].gazeX) >= 0.001 || Math.abs(rows[0].gazeY) >= 0.001, '第一幀就有偏移');
    }
  }
});

test('微掃視：視線在閒置時持續小幅跳動，幅度不超過 FIXATION_JITTER', () => {
  const rows = drive(createAmbient(5), { hz: 60, seconds: 30 });
  const xs = new Set(rows.map(r => r.gazeX.toFixed(6)));
  assert.ok(xs.size >= 15, `只有 ${xs.size} 個不同的 gazeX`);
  for (const r of rows) assert.ok(Math.hypot(r.gazeX, r.gazeY / 0.7) <= 0.14 + 1e-9);
});

// ---- 亂數 ----

test('同一個 seed 逐幀相同（含 apply），不同 seed 不同', () => {
  const events = { 30: (now, a) => a.apply(plan(1, { headX: 0.4, gazeX: -0.5 }), now) };
  const run = (seed) => drive(createAmbient(seed), { hz: 60, seconds: 20, events });
  assert.deepEqual(run(5), run(5));
  assert.notDeepEqual(run(5), run(6));
  const a = createAmbient(5);
  const first = drive(a, { hz: 60, seconds: 5 });
  a.reset();
  assert.deepEqual(drive(a, { hz: 60, seconds: 5 }), first, 'reset 之後重來也相同');
});

// ---- enabled／漂移／呼吸／權重 ----

test('enabled=false（潛伏）：輸出收斂到 0；重新 enabled 後回到計畫目標', () => {
  const a = createAmbient(4);
  a.apply(plan(1, { headX: 0.5, bodyX: 0.4, gazeX: 0.6 }), 0);
  const warm = drive(a, { hz: 60, seconds: 3 });
  assert.ok(Math.abs(warm[warm.length - 1].gazeX) > 0.4);
  // 接著 1.5 秒的潛伏：drive 的時間從 0 起算，這裡手動接續
  let now = 3000, last;
  for (let k = 0; k < 90; k++, now += 1000 / 60) last = a.update(1 / 60, now, { enabled: false });
  for (const axis of AXES) assert.ok(Math.abs(last[axis]) < 1e-4, `${axis} = ${last[axis]}`);
  for (let k = 0; k < 240; k++, now += 1000 / 60) last = a.update(1 / 60, now, { enabled: true });
  assert.ok(last.headX > 0.2 && Math.abs(last.gazeX - 0.6) < 0.15, `headX ${last.headX} gazeX ${last.gazeX}`);
});

test('swayGain 0.35：固定目標下漂移的標準差明顯小於 1（比例 < 0.6，且不是 0）', () => {
  const sd = (gain, axis) => stdev(drive(createAmbient(8), { hz: 60, seconds: 60, opts: { swayGain: gain } }).slice(300).map(r => r[axis]));
  for (const axis of ['headX', 'headY', 'headZ', 'bodyX', 'bodyY', 'bodyZ']) {
    const ratio = sd(0.35, axis) / sd(1, axis);
    assert.ok(ratio < 0.6 && ratio > 0.2, `${axis} 比例 ${ratio}`);
  }
});

test('swayGain 是平滑過渡：突然從 1 切到 0.35 不會讓輸出跳變', () => {
  const rows = drive(createAmbient(8), { hz: 120, seconds: 20, opts: (k) => ({ swayGain: k < 1200 ? 1 : 0.35 }) });
  for (let k = 1; k < rows.length; k++) for (const axis of POSE) assert.ok(Math.abs(rows[k][axis] - rows[k - 1][axis]) < 0.01, `${axis} 第 ${k} 幀`);
});

test('breath：只影響 headY、bodyY，量 = LIFT × (breath − 0.5) × 0.6；0.5 時不影響', () => {
  const at = (breath) => createAmbient(2).update(1 / 60, 0, { breath });
  const mid = at(0.5), hi = at(1), lo = at(0);
  close(hi.headY - mid.headY, 0.06 * 0.5 * 0.6, 1e-12);
  close(hi.bodyY - mid.bodyY, 0.08 * 0.5 * 0.6, 1e-12);
  close(lo.headY - mid.headY, -0.06 * 0.5 * 0.6, 1e-12);
  for (const axis of AXES.filter(x => x !== 'headY' && x !== 'bodyY')) assert.equal(hi[axis], mid[axis], axis);
  assert.deepEqual(at(), mid);                                    // 預設 0.5
});

test('bodyWeight／gazeWeight 為 0 時對應輸出收斂到 0', () => {
  const rows = drive(createAmbient(6), { hz: 60, seconds: 3, opts: { bodyWeight: 0, gazeWeight: 0 } });
  const last = rows[rows.length - 1];
  for (const axis of AXES) assert.ok(Math.abs(last[axis]) < 1e-6, `${axis} = ${last[axis]}`);
});

// ---- 背景恢復 ----

test('更新間隔超過 250 ms（背景分頁）：從最後顯示的位置接續，不跳到計畫終點', () => {
  const a = createAmbient(12);
  a.apply(plan(1, { headX: 0.6, bodyX: 0.5, gazeX: 0.5 }), 0);
  let now = 0, last;
  for (; now <= 500; now += 1000 / 60) last = a.update(1 / 60, now);
  assert.ok(Math.abs(last.headX - 0.6) > 0.3, '還在路上');
  const resumed = a.update(1 / 60, now + 5000);                  // 隔了 5 秒才回來
  assert.ok(Math.abs(resumed.headX - last.headX) < 0.05, `headX ${last.headX} → ${resumed.headX}`);
  assert.ok(Math.abs(resumed.bodyX - last.bodyX) < 0.05, `bodyX ${last.bodyX} → ${resumed.bodyX}`);
  let later = resumed;                                            // 之後照樣走到目標附近
  for (let t = now + 5000; t < now + 8000; t += 1000 / 60) later = a.update(1 / 60, t);
  assert.ok(Math.abs(later.headX - 0.6) < 0.3, `headX ${later.headX}`);
});
