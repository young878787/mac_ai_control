import test from 'node:test';
import assert from 'node:assert/strict';
import { createProcedural, motionBlinks } from '../renderer/animation/procedural.js';

const DT = 1 / 30;   // 計算幀 30 Hz（O5 開著時）
// 假模型的參數：[鍵, 預設, 最小, 最大]。bodyX 預設不是 0、眉毛預設不是 0，才看得出「回預設」和「回 0」的差別
const SPEC = [
  ['angleX', 0, -40, 40], ['angleY', 0, -40, 40], ['angleZ', 0, -40, 40],
  ['bodyX', 0.5, -10, 10], ['bodyY', 0, -10, 10], ['bodyZ', 0, -10, 10],
  ['eyeX', 0, -1, 1], ['eyeY', 0, -1, 1], ['eyeOpen', 1, 0, 1], ['eyeROpen', 1, 0, 1],
  ['browLY', 0.2, -1, 1], ['browRY', 0.2, -1, 1], ['browLX', 0.1, -1, 1], ['browRX', 0.1, -1, 1],
  ['browLForm', 0.15, -3, 3], ['browRForm', 0.15, -3, 3], ['browLAngle', -0.1, -3, 3], ['browRAngle', -0.1, -3, 3],
  ['breath', 0, 0, 1],
];
const POSE = ['angleX', 'angleY', 'angleZ', 'bodyX', 'bodyY', 'bodyZ'];
const GAZE = ['eyeX', 'eyeY'];
const LIDS = ['eyeOpen', 'eyeROpen'];
const BROW_POS = ['browLY', 'browRY', 'browLX', 'browRX'];
const BROW_SHAPE = ['browLForm', 'browRForm', 'browLAngle', 'browRAngle'];
const EXTRA = 2;   // 不在 index 裡的參數（手臂、嘴）：程序層不得動

/** 組出假模型：index、defaults／minimums／maximums，以及沒被程序層認得的兩個多餘參數（手臂在 N、嘴在 N+1） */
function model(skip = []) {
  const n = SPEC.length;
  const index = {};
  const defaults = new Float32Array(n + EXTRA), minimums = new Float32Array(n + EXTRA).fill(-10), maximums = new Float32Array(n + EXTRA).fill(10);
  SPEC.forEach(([key, d, lo, hi], i) => { index[key] = skip.includes(key) ? -1 : i; defaults[i] = d; minimums[i] = lo; maximums[i] = hi; });
  return { index, defaults, minimums, maximums, n };
}
const make = (m, seed = 7, blinkMotions = new Set(['talk'])) => createProcedural({ index: m.index, defaults: m.defaults, minimums: m.minimums, maximums: m.maximums, seed, blinkMotions });
const slotOf = (m, key) => SPEC.findIndex(s => s[0] === key);

/**
 * 跑 frames 幀，每幀先還原預設值、再用 motionValues（鍵 → 值）模擬動作寫入、手臂與嘴也寫成動作的值，才呼叫 apply。
 * 回傳每一幀 values 的副本。clock 讓呼叫端接續時間。
 */
function run(proc, m, frames, state, motionValues, clock = { ms: 1000 }) {
  const out = [];
  const values = new Float32Array(m.defaults.length);
  for (let f = 0; f < frames; f++) {
    values.set(m.defaults);
    for (const [key, v] of Object.entries(motionValues)) { const i = slotOf(m, key); if (i >= 0) values[i] = v; }
    values[m.n] = 0.9;       // 手臂
    values[m.n + 1] = 0.4;   // 嘴
    clock.ms += DT * 1000;
    proc.apply(values, DT, clock.ms, state);
    out.push(Float32Array.from(values));
  }
  return out;
}
const at = (m, key) => slotOf(m, key);
const close = (a, b, eps, msg) => assert.ok(Math.abs(a - b) <= eps, `${msg ?? ''} ${a} 與 ${b} 差 ${Math.abs(a - b)}`);

// 動作會寫的值：故意放與程序輸出毫不相干的數字，用來驗證「蓋掉」
const MOTION_A = { angleX: 17, angleY: -9, angleZ: 12, bodyX: 5, bodyY: -4, bodyZ: 6, eyeX: 0.9, eyeY: -0.8, eyeOpen: 0.2, eyeROpen: 0.25,
  browLY: 0.9, browRY: -0.7, browLX: 0.8, browRX: -0.6, browLForm: 2, browRForm: -2, browLAngle: 1.5, browRAngle: -1.5, breath: 0.8 };
const MOTION_B = { angleX: -20, angleY: 15, angleZ: -8, bodyX: -6, bodyY: 3, bodyZ: -2, eyeX: -0.5, eyeY: 0.6, eyeOpen: 0.9, eyeROpen: 0.1,
  browLY: -0.4, browRY: 0.5, browLX: -0.9, browRX: 0.7, browLForm: -1, browRForm: 1, browLAngle: -2, browRAngle: 2, breath: 0.8 };

test('Idle：頭身、眼球、眼皮、眉毛的位置與形狀都蓋掉動作的值（輸出跟動作值無關）；手臂、嘴、Breath 保留原值', () => {
  const m = model();
  const a = run(make(m), m, 240, { pose: 'out', motion: 'idle' }, MOTION_A);
  const b = run(make(m), m, 240, { pose: 'out', motion: 'idle' }, MOTION_B);
  for (const key of [...POSE, ...GAZE, ...LIDS, ...BROW_POS, ...BROW_SHAPE]) {
    for (let f = 0; f < a.length; f++) assert.equal(a[f][at(m, key)], b[f][at(m, key)], `${key} 第 ${f} 幀`);
  }
  for (const frame of a) {
    assert.equal(frame[m.n], Math.fround(0.9));
    assert.equal(frame[m.n + 1], Math.fround(0.4));
    assert.equal(frame[at(m, 'breath')], Math.fround(0.8));
  }
  // 眉毛位置固定在預設值；眼皮不會比動作的 0.2 還低以外的任何來源：Idle 沒有眨眼的幀就是 1
  for (const frame of a) for (const key of BROW_POS) assert.equal(frame[at(m, key)], m.defaults[at(m, key)]);
  assert.ok(a.some(f => f[at(m, 'eyeOpen')] === 1), '沒有眨眼的幀眼皮是 1，不是動作的 0.2');
});

test('Idle：頭身與眼球有在動（漂移、微掃視），不是死的', () => {
  const m = model();
  const frames = run(make(m), m, 300, { pose: 'out', motion: 'idle' }, {});
  for (const key of ['angleX', 'angleY', 'bodyZ', 'eyeX', 'eyeY']) {
    const series = frames.map(f => f[at(m, key)]);
    assert.ok(Math.max(...series) - Math.min(...series) > 0.005, `${key} 沒有動`);
  }
  for (const key of POSE) for (const f of frames) assert.ok(Number.isFinite(f[at(m, key)]));
});

test('反應動作：頭身跟動作的值（收斂），眼球仍由程序、手臂與嘴保留', () => {
  const m = model();
  const proc = make(m);
  run(proc, m, 60, { pose: 'out', motion: 'idle' }, {});
  const clock = { ms: 1000 + 60 * DT * 1000 };
  const frames = run(proc, m, 90, { pose: 'out', motion: 'nod' }, MOTION_A, clock);
  const last = frames.at(-1);
  for (const key of POSE) close(last[at(m, key)], MOTION_A[key], 1e-3, key);
  for (const f of frames) { assert.equal(f[m.n], Math.fround(0.9)); assert.equal(f[m.n + 1], Math.fround(0.4)); }

  // 眼球與動作值無關：同 seed、同歷史，只換動作寫的眼球值，輸出一致
  const other = make(m);
  run(other, m, 60, { pose: 'out', motion: 'idle' }, {});
  const frames2 = run(other, m, 90, { pose: 'out', motion: 'nod' }, { ...MOTION_A, eyeX: -0.9, eyeY: 0.9 }, { ms: 1000 + 60 * DT * 1000 });
  for (let f = 0; f < frames.length; f++) for (const key of GAZE) assert.equal(frames[f][at(m, key)], frames2[f][at(m, key)], `${key} 第 ${f} 幀`);
});

test('反應動作接手：頭身從 Idle 的位置平順走向動作值，接手那一幀不跳；超過自主角度上限的動作值也照走', () => {
  const m = model();
  const proc = make(m);
  const idle = run(proc, m, 90, { pose: 'out', motion: 'idle' }, {});
  const before = idle.at(-1);
  const clock = { ms: 1000 + 90 * DT * 1000 };
  const hit = run(proc, m, 60, { pose: 'out', motion: 'nod' }, { ...MOTION_A, angleX: 38 }, clock);   // 38 > 自主上限 35，但在原始範圍 40 內
  for (const key of POSE) {
    const goal = key === 'angleX' ? 38 : MOTION_A[key];
    const gap = Math.abs(goal - before[at(m, key)]);
    const first = hit[0][at(m, key)];
    assert.ok(Math.abs(first - before[at(m, key)]) <= 0.15 * gap + 1e-6, `${key} 接手第一幀跳了 ${Math.abs(first - before[at(m, key)])}（差距 ${gap}）`);
    assert.notEqual(first, goal, `${key} 第一幀不該直接到位`);
    let maxStep = 0;
    for (let f = 1; f < hit.length; f++) maxStep = Math.max(maxStep, Math.abs(hit[f][at(m, key)] - hit[f - 1][at(m, key)]));
    assert.ok(maxStep <= 0.3 * gap + 1e-6, `${key} 單幀最大位移 ${maxStep}（差距 ${gap}）`);
  }
  close(hit.at(-1)[at(m, 'angleX')], 38, 1e-3, '超過自主上限但在原始範圍內，要走到動作值');
});

test('反應動作交還 Idle：不跳，之後回到程序的自主範圍', () => {
  const m = model();
  const proc = make(m);
  const clock = { ms: 1000 };
  run(proc, m, 60, { pose: 'out', motion: 'idle' }, {}, clock);
  const react = run(proc, m, 90, { pose: 'out', motion: 'nod' }, MOTION_A, clock);
  const held = react.at(-1);
  const back = run(proc, m, 120, { pose: 'out', motion: 'idle' }, MOTION_B, clock);   // 交還後動作寫的值（MOTION_B）應被忽略
  for (const key of POSE) {
    const prev = held[at(m, key)];
    const gap = Math.abs(prev - back.at(-1)[at(m, key)]);
    assert.ok(Math.abs(back[0][at(m, key)] - prev) <= 0.15 * Math.max(gap, 1) + 0.2, `${key} 交還第一幀跳了`);
    let maxStep = 0;
    for (let f = 1; f < back.length; f++) maxStep = Math.max(maxStep, Math.abs(back[f][at(m, key)] - back[f - 1][at(m, key)]));
    assert.ok(maxStep <= 0.3 * Math.max(gap, 1) + 0.3, `${key} 單幀最大位移 ${maxStep}`);
  }
  assert.ok(Math.abs(back.at(-1)[at(m, 'angleX')]) < 35);
});

test('Talk（blinkMotions 有）：程序眨眼不生效，眼皮＝動作的值', () => {
  const m = model();
  const proc = make(m);
  run(proc, m, 30, { pose: 'out', motion: 'idle' }, {});
  const frames = run(proc, m, 1500, { pose: 'out', motion: 'talk' }, { eyeOpen: 0.4, eyeROpen: 0.7 }, { ms: 1000 + 30 * DT * 1000 });   // 50 秒，遠超過眨眼間隔
  for (const f of frames) {
    assert.equal(f[at(m, 'eyeOpen')], Math.fround(0.4));
    assert.equal(f[at(m, 'eyeROpen')], Math.fround(0.7));
  }
});

test('非自己眨眼的反應動作：眼皮＝動作的值 × 程序眨眼（眨得下去、不眨時＝動作值）', () => {
  const m = model();
  const proc = make(m);
  run(proc, m, 30, { pose: 'out', motion: 'idle' }, {});
  const clock = { ms: 1000 + 30 * DT * 1000 };
  let lowest = 1, full = 0;
  for (let f = 0; f < 1500; f++) {
    const [frame] = run(proc, m, 1, { pose: 'out', motion: 'nod' }, { eyeOpen: 0.4, eyeROpen: 0.8 }, clock);
    const blink = proc.debug().face.blink;
    close(frame[at(m, 'eyeOpen')], 0.4 * blink, 1e-6, '左眼');
    close(frame[at(m, 'eyeROpen')], 0.8 * blink, 1e-6, '右眼');
    lowest = Math.min(lowest, blink);
    if (blink === 1) full++;
  }
  assert.ok(lowest < 0.2, `50 秒內沒有眨眼（最低 ${lowest}）`);
  assert.ok(full > 1000, '大部分時間不在眨眼');
});

test('反應動作期間：眉毛位置交給動作（不動）、形狀＝預設值＋淡出中的眉毛事件', () => {
  const m = model();
  const proc = make(m);
  run(proc, m, 30, { pose: 'out', motion: 'idle' }, {});
  const frames = run(proc, m, 300, { pose: 'out', motion: 'nod' }, MOTION_A, { ms: 1000 + 30 * DT * 1000 });
  for (const f of frames) for (const key of BROW_POS) assert.equal(f[at(m, key)], Math.fround(MOTION_A[key]), key);
  const last = frames.at(-1);
  for (const key of BROW_SHAPE) close(last[at(m, key)], m.defaults[at(m, key)], 1e-6, key);   // 淡出完畢、不排新事件
});

test('潛伏：頭身眼收斂到預設、眼皮 1、眉毛形狀回預設；動作寫什麼都沒用', () => {
  const m = model();
  const proc = make(m);
  const clock = { ms: 1000 };
  run(proc, m, 150, { pose: 'out', motion: 'idle' }, {}, clock);
  for (const motion of ['idle', 'nod']) {
    const frames = run(proc, m, 120, { pose: 'peek', motion }, MOTION_A, clock);
    const last = frames.at(-1);
    for (const key of [...POSE, ...GAZE]) close(last[at(m, key)], m.defaults[at(m, key)], 1e-3, `${motion} ${key}`);
    for (const f of frames.slice(-60)) {
      for (const key of LIDS) assert.equal(f[at(m, key)], 1);
      for (const key of [...BROW_POS, ...BROW_SHAPE]) assert.equal(f[at(m, key)], Math.fround(m.defaults[at(m, key)]), key);
    }
    // 潛伏不漂移：最後 30 幀幾乎不動
    for (const key of POSE) {
      const tail = frames.slice(-30).map(f => f[at(m, key)]);
      assert.ok(Math.max(...tail) - Math.min(...tail) < 1e-3, key);
    }
  }
});

test('潛伏→出場：眼皮、頭身都從中性平順展開，不跳', () => {
  const m = model();
  const proc = make(m);
  const clock = { ms: 1000 };
  run(proc, m, 90, { pose: 'peek', motion: 'idle' }, {}, clock);
  const out = run(proc, m, 90, { pose: 'out', motion: 'idle' }, {}, clock);
  for (const key of POSE) assert.ok(Math.abs(out[0][at(m, key)] - m.defaults[at(m, key)]) < 1, key);
  for (let f = 1; f < out.length; f++) for (const key of POSE) assert.ok(Math.abs(out[f][at(m, key)] - out[f - 1][at(m, key)]) < 3, `${key} 第 ${f} 幀`);
});

test('模型沒有的參數（index 為 -1）：不寫、不崩潰，其餘照常運作', () => {
  const skipped = ['angleX', 'bodyX', 'eyeROpen', 'browLForm', 'browRY', 'breath', 'eyeY'];
  const m = model(skipped);
  const proc = make(m);
  const values = new Float32Array(m.defaults.length);
  for (let f = 0; f < 300; f++) {
    values.set(m.defaults);
    values[m.n] = 0.9;
    proc.apply(values, DT, 1000 + f * DT * 1000, { pose: f < 200 ? 'out' : 'peek', motion: f % 90 < 45 ? 'idle' : 'nod' });
  }
  assert.equal(values[m.n], Math.fround(0.9));
  assert.equal(values[-1], undefined);
  assert.equal(Object.keys(values).length, m.defaults.length);
  // 被略過的參數保持 defaults（apply 之前由呼叫端設的值），沒被程序層碰
  values.set(m.defaults);
  proc.apply(values, DT, 1e6, { pose: 'out', motion: 'idle' });
  for (const key of skipped) assert.equal(values[slotOf(m, key)], m.defaults[slotOf(m, key)], key);

  // 完全沒有索引也不崩潰
  const bare = createProcedural({ index: {}, defaults: new Float32Array(3), minimums: new Float32Array(3), maximums: new Float32Array(3), seed: 1, blinkMotions: new Set() });
  const v = new Float32Array(3);
  assert.doesNotThrow(() => { for (let f = 0; f < 100; f++) bare.apply(v, DT, f * 33, { pose: 'out', motion: 'idle' }); });
  assert.deepEqual(Array.from(v), [0, 0, 0]);
});

test('同 seed 逐幀可重現、不同 seed 會不同', () => {
  const m = model();
  const state = (f) => ({ pose: 'out', motion: f % 200 < 150 ? 'idle' : 'nod' });
  const play = (seed) => {
    const proc = make(m, seed);
    const values = new Float32Array(m.defaults.length);
    const rows = [];
    for (let f = 0; f < 900; f++) {
      values.set(m.defaults);
      values[at(m, 'angleX')] = 5;
      proc.apply(values, DT, 1000 + f * DT * 1000, state(f));
      rows.push(Array.from(values));
    }
    return JSON.stringify(rows);
  };
  assert.equal(play(123), play(123));
  assert.notEqual(play(123), play(124));
});

test('plan：格式不合回 false、成功回 true 並讓視線走向新目標', () => {
  const m = model();
  const proc = make(m);
  const stand = { pose: 'out', motion: 'idle' };
  run(proc, m, 10, stand, {});
  const target = (gazeX, gazeY) => ({ headX: 0, headY: 0, headZ: 0, bodyX: 0, bodyY: 0, bodyZ: 0, gazeX, gazeY });
  assert.equal(proc.plan({ sequence: 1, moveMs: 50, target: target(0, 0) }, 2000), false);          // moveMs 太小
  assert.equal(proc.plan({ sequence: 1, moveMs: 800, target: target(2, 0) }, 2000), false);          // 超出 ±1
  assert.equal(proc.plan({ sequence: 1, moveMs: 800, target: target(0.8, 0) }, 2000), true);
  assert.equal(proc.plan({ sequence: 1, moveMs: 800, target: target(-0.8, 0) }, 2000), false);       // sequence 沒有遞增
  const frames = run(proc, m, 60, stand, {}, { ms: 2000 });
  assert.ok(frames.at(-1)[at(m, 'eyeX')] > 0.5, `視線沒有轉向目標：${frames.at(-1)[at(m, 'eyeX')]}`);
});

test('plan：視線轉移距離 ≥ 0.5 時有機會順便眨眼，< 0.5 絕不會（眨眼只可能由 gazeShift 觸發的短時間內）', () => {
  const m = model();
  const target = (gazeX, gazeY) => ({ headX: 0, headY: 0, headZ: 0, bodyX: 0, bodyY: 0, bodyZ: 0, gazeX, gazeY });
  const blinked = (seed, gaze) => {
    const proc = make(m, seed);
    const clock = { ms: 1000 };
    run(proc, m, 5, { pose: 'out', motion: 'idle' }, {}, clock);   // 約 0.17 秒，還沒到第一次自然眨眼（最短間隔 1 秒）
    assert.equal(proc.plan({ sequence: 1, moveMs: 800, target: target(...gaze) }, clock.ms), true);
    const frames = run(proc, m, 8, { pose: 'out', motion: 'idle' }, {}, clock);
    return frames.some(f => f[at(m, 'eyeOpen')] < 0.9);
  };
  let big = 0, small = 0;
  for (let seed = 1; seed <= 30; seed++) { if (blinked(seed, [0.9, 0.3])) big++; if (blinked(seed, [0.2, 0.1])) small++; }
  assert.ok(big >= 8 && big <= 28, `大幅轉移 30 次裡眨了 ${big} 次（預期約 60%）`);
  assert.equal(small, 0);
});

test('debug：回傳上一次取樣', () => {
  const m = model();
  const proc = make(m);
  assert.deepEqual(proc.debug(), { ambient: null, face: null });
  run(proc, m, 3, { pose: 'out', motion: 'idle' }, {});
  const { ambient, face } = proc.debug();
  assert.ok('headX' in ambient && 'gazeY' in ambient);
  assert.ok('blink' in face && 'kind' in face);
});

// ---------- motionBlinks ----------
const curve = (segments, Id = 'ParamEyeLOpen') => ({ Curves: [{ Target: 'Parameter', Id, Segments: segments }] });

test('motionBlinks：線性（類型 0）終點低於 0.5 → true，不低於 → false', () => {
  assert.equal(motionBlinks(curve([0, 1, 0, 0.5, 0.2, 0, 1, 1])), true);
  assert.equal(motionBlinks(curve([0, 1, 0, 0.5, 0.6, 0, 1, 1])), false);
});

test('motionBlinks：貝茲（類型 1）只看端點；控制點低於 0.5 但端點都不低於 → false', () => {
  assert.equal(motionBlinks(curve([0, 1, 1, 0.3, 0.0, 0.6, 0.0, 1, 1])), false);
  assert.equal(motionBlinks(curve([0, 1, 1, 0.3, 1, 0.6, 1, 1, 0.1])), true);
  assert.equal(motionBlinks(curve([0, 1, 1, 0.3, 1, 0.6, 1, 1, 1, 0, 2, 0.2])), true);   // 貝茲後面接線性：確認 7 個數字對齊
});

test('motionBlinks：stepped（類型 2）與 inverse stepped（類型 3）取的是值，不是時間', () => {
  assert.equal(motionBlinks(curve([0, 1, 2, 1, 0.2])), true);
  assert.equal(motionBlinks(curve([0, 1, 3, 1, 0.3])), true);
  assert.equal(motionBlinks(curve([0, 1, 2, 0.1, 1])), false);   // 時間 0.1 很小，但值是 1
  assert.equal(motionBlinks(curve([0, 1, 3, 0.1, 0.9, 2, 0.2, 1])), false);
});

test('motionBlinks：起點低於 0.5 也算；剛好 0.5 不算；混合四種類型', () => {
  assert.equal(motionBlinks(curve([0, 0.2, 0, 1, 1])), true);
  assert.equal(motionBlinks(curve([0, 0.5, 0, 1, 0.5])), false);
  assert.equal(motionBlinks(curve([0, 1, 0, 0.2, 1, 1, 0.4, 1, 0.6, 1, 0.8, 1, 2, 1, 1, 3, 1.2, 0.0])), true);
  assert.equal(motionBlinks(curve([0, 1, 0, 0.2, 1, 1, 0.4, 0, 0.6, 0, 0.8, 1, 2, 1, 1, 3, 1.2, 1])), false);
});

test('motionBlinks：指定曲線 ID、沒有這條曲線或資料殘缺都回 false，不拋錯', () => {
  const json = curve([0, 1, 0, 1, 0.1], 'ParamEyeROpen');
  assert.equal(motionBlinks(json), false);                      // 預設看左眼，這份只有右眼
  assert.equal(motionBlinks(json, 'ParamEyeROpen'), true);
  assert.equal(motionBlinks({ Curves: [] }), false);
  assert.equal(motionBlinks({}), false);
  assert.equal(motionBlinks(null), false);
  assert.equal(motionBlinks(curve([])), false);
  assert.equal(motionBlinks(curve([0, 1, 9, 1, 0.1])), false);   // 不認得的類型：停止解析
});
