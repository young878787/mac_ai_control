// 臉部程序（眨眼＋眼皮／眉毛事件）：間隔、眨眼時間、連眨、聆聽限制、不出現 wink、各種禁止開關、眉毛淡出、視線轉移眨眼、可重現。
// 所有亂數都用 seed（mulberry32），時間用注入的毫秒數，不 sleep。
import test from 'node:test';
import assert from 'node:assert/strict';
import { BLINK_INTERVAL, createBlink, createFace, createFaceIdle } from '../renderer/animation/face.js';

/** 帶 seed 的亂數（mulberry32），回傳 () => [0, 1) */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const mean = (xs) => sum(xs) / xs.length;
const MIN_BLINK_MS = 70 + 20 + 120;     // 一次眨眼最短／最長的總時間
const MAX_BLINK_MS = 120 + 60 + 200;

/** 單獨跑 createBlink，dtMs 毫秒一幀；回傳每次眨眼的 { start, closing, closed, opening }（毫秒，誤差約 dtMs） */
function blinkEvents(seed, seconds, { range = BLINK_INTERVAL.idle, dtMs = 1 } = {}) {
  const blink = createBlink(rng(seed));
  blink.setIntervalRange(range[0], range[1]);
  const out = [];
  let state = 'idle';
  let start = 0, closeEnd = 0, zeroEnd = 0;
  for (let t = 0; t <= seconds * 1000; t += dtMs) {
    const v = blink.update(dtMs / 1000, t);
    if (state === 'idle') { if (v < 1) { state = 'closing'; start = t - dtMs; } }
    else if (state === 'closing') { if (v === 0) { state = 'zero'; closeEnd = t; } }
    else if (state === 'zero') { if (v > 0) { state = 'opening'; zeroEnd = t; } }
    else if (v === 1) {
      out.push({ start, closing: closeEnd - start, closed: zeroEnd - closeEnd, opening: t - zeroEnd });
      state = 'idle';
    }
  }
  return out;
}

/** 相鄰眨眼開始時間差；< 1 秒的是連眨的第二下，排除 */
const gapsOf = (events) => events.slice(1).map((e, i) => e.start - events[i].start).filter(g => g >= 1000);

/** 跑 createFace：每幀 1000/hz 毫秒，ctx 由 (t 毫秒) → ctx 決定；回傳每幀 { t, ...輸出 } */
function simFace({ seed = 1, hz = 60, seconds = 60, ctx = () => ({}), face = createFace(rng(seed)) } = {}) {
  const frames = [];
  const step = 1000 / hz;
  for (let k = 0; k <= seconds * hz; k++) {
    const t = k * step;
    frames.push({ t, ...face.update(1 / hz, t, ctx(t)) });
  }
  return frames;
}

/** 眨眼次數：blink 由 1 掉到 < 1 算一次 */
const blinkStarts = (frames) => frames.filter((f, i) => i > 0 && f.blink < 1 && frames[i - 1].blink === 1).map(f => f.t);

const WINK = new Set(['winkLeft', 'winkRight']);

test('眨眼間隔：閒置 1～4 秒、聆聽 3～6 秒（相鄰眨眼開始時間差＝間隔＋一次眨眼的時間）', () => {
  for (const [name, range] of Object.entries(BLINK_INTERVAL)) {
    const gaps = [];
    for (const seed of [1, 2, 3]) gaps.push(...gapsOf(blinkEvents(seed, 1500, { range })));
    const lo = range[0] * 1000 + MIN_BLINK_MS - 3, hi = range[1] * 1000 + MAX_BLINK_MS + 3;
    assert.ok(gaps.length > 300, `${name} 只量到 ${gaps.length} 個間隔`);
    assert.ok(gaps.every(g => g >= lo && g <= hi), `${name} 間隔超出 ${lo}～${hi}：${Math.min(...gaps)}～${Math.max(...gaps)}`);
    // 範圍真的用滿（不是固定值）：平均＝區間中點＋平均眨眼時間 295 ms
    const mid = (range[0] + range[1]) / 2 * 1000 + 295;
    assert.ok(Math.abs(mean(gaps) - mid) < 150, `${name} 平均 ${mean(gaps).toFixed(0)}，預期約 ${mid}`);
    assert.ok(Math.min(...gaps) < lo + 300 && Math.max(...gaps) > hi - 300, `${name} 沒有用滿範圍`);
  }
  assert.deepEqual(BLINK_INTERVAL, { idle: [1, 4], listening: [3, 6] });
});

test('聆聽的平均眨眼間隔 ÷ 閒置 ≥ 1.3（L5）：createFace 切換 listening 後的眨眼率', () => {
  const rate = (listening) => {
    let n = 0, s = 0;
    for (const seed of [11, 12, 13]) { n += blinkStarts(simFace({ seed, seconds: 1500, ctx: () => ({ listening }) })).length; s += 1500; }
    return n / s;
  };
  const idleRate = rate(false), listeningRate = rate(true);
  assert.ok(idleRate / listeningRate >= 1.3, `閒置率 ${idleRate.toFixed(3)} ÷ 聆聽率 ${listeningRate.toFixed(3)} = ${(idleRate / listeningRate).toFixed(2)}`);
});

test('切進聆聽後，下一次眨眼至少隔 3 秒；切回閒置後回到 1～4 秒', () => {
  for (const seed of [21, 22, 23, 24]) {
    const frames = simFace({ seed, seconds: 600, ctx: (t) => ({ listening: t >= 60000 && t < 360000 }) });
    const starts = blinkStarts(frames);
    // toggle 前後 1 秒內的可能是進行中事件（含連眨第二下），從 +1 秒起第一個才是新間隔排出來的
    const afterOn = starts.find(t => t >= 61000);
    assert.ok(afterOn >= 63000, `seed ${seed}：切進聆聽後第一次眨眼在 ${afterOn - 60000} ms`);
    const inside = starts.filter(t => t >= 63000 && t < 360000);
    assert.ok(inside.slice(1).every((t, i) => t - inside[i] < 1000 || t - inside[i] >= 3000 + MIN_BLINK_MS - 20), `seed ${seed}：聆聽中出現過短的間隔`);
    const afterOff = starts.find(t => t >= 361000);
    assert.ok(afterOff >= 361000 && starts.filter(t => t >= 361000).length > 40, `seed ${seed}：切回閒置後眨眼沒有恢復`);
  }
});

test('眨眼時間：閉 70～120、停 20～60、開 120～200 ms（從輸出曲線量，誤差 ±3 ms），且範圍用滿', () => {
  const events = [1, 2].flatMap(seed => blinkEvents(seed, 1500));
  assert.ok(events.length > 800);
  const check = (key, lo, hi) => {
    const values = events.map(e => e[key]);
    assert.ok(Math.min(...values) >= lo - 3 && Math.max(...values) <= hi + 3, `${key} ${Math.min(...values)}～${Math.max(...values)} 超出 ${lo}～${hi}`);
    assert.ok(Math.min(...values) < lo + (hi - lo) * 0.1 && Math.max(...values) > hi - (hi - lo) * 0.1, `${key} 沒有用滿範圍`);
  };
  check('closing', 70, 120);
  check('closed', 20, 60);
  check('opening', 120, 200);
});

test('連眨兩下：符合條件的眨眼中約 7.5%，第二下後 8 秒冷卻內不再連眨', () => {
  const events = blinkEvents(31, 40000, { dtMs: 4 });
  const starts = events.map(e => e.start);
  const isSecond = starts.map((s, i) => i > 0 && s - starts[i - 1] < 1000);
  const pairs = [];                       // [第一下開始, 第二下開始]
  starts.forEach((s, i) => { if (isSecond[i]) pairs.push([starts[i - 1], s]); });
  assert.ok(pairs.length > 100, `連眨只有 ${pairs.length} 次`);
  // 冷卻：下一組連眨的第一下，必須在上一組第二下結束（≥ 開始時間）8 秒之後
  for (let i = 1; i < pairs.length; i++) assert.ok(pairs[i][0] >= pairs[i - 1][1] + 8000, `第 ${i} 組連眨在冷卻內（${pairs[i][0] - pairs[i - 1][1]} ms）`);
  // 比例：只算「確定已過冷卻」的自動眨眼（第二下開始 + 8.4 秒之後，涵蓋第二下最長的眨眼時間）
  let lastSecond = -Infinity, eligible = 0, doubles = 0;
  starts.forEach((s, i) => {
    if (isSecond[i]) { lastSecond = s; return; }
    if (s < lastSecond + 8400) return;
    eligible++;
    if (i + 1 < starts.length && isSecond[i + 1]) doubles++;
  });
  assert.ok(eligible > 3000, `可判定的眨眼只有 ${eligible}`);
  const ratio = doubles / eligible;
  assert.ok(ratio > 0.06 && ratio < 0.09, `連眨比例 ${ratio.toFixed(4)}（${doubles}/${eligible}）`);
  // 第二下與第一下的間隔：第一下結束後 100～180 ms 才開始
  const gaps = pairs.map(([a, b]) => b - a);
  assert.ok(gaps.every(g => g >= MIN_BLINK_MS + 100 - 8 && g <= MAX_BLINK_MS + 180 + 8), `連眨間隔 ${Math.min(...gaps)}～${Math.max(...gaps)}`);
});

test('forceBlink／pause／resume：強制眨眼下一幀開始；暫停期間不自動眨；動作接管眼皮時不排隊', () => {
  const blink = createBlink(rng(5));
  blink.update(0.016, 0);
  blink.forceBlink();
  assert.equal(blink.isIdle(), false);
  const seen = [];
  for (let t = 16; t <= 600; t += 16) seen.push(blink.update(0.016, t));
  assert.ok(Math.min(...seen) === 0 && seen.at(-1) === 1 && blink.isIdle());

  blink.pause();
  assert.equal(blink.isPaused(), true);
  for (let t = 700; t <= 30000; t += 16) assert.equal(blink.update(0.016, t), 1);
  blink.resume();
  const resumed = [];
  for (let t = 30016; t <= 40000; t += 16) resumed.push(blink.update(0.016, t));
  assert.ok(Math.min(...resumed) < 1);

  // allowed=false：回 1，這期間的 forceBlink 不排隊，恢復後也不補播
  const b2 = createBlink(rng(6));
  b2.update(0.016, 0, false);
  b2.forceBlink();
  assert.equal(b2.isIdle(), true);
  assert.equal(b2.update(0.016, 16, false), 1);
  assert.equal(b2.update(0.016, 32, true), 1);
  assert.equal(b2.update(0.016, 48, true), 1);
});

test('聆聽時眼皮事件只有 still／relax／brow，eyeL、eyeR ≥ 0.90（不出現 half／deep／asymmetric）', () => {
  const kinds = new Set();
  let minEye = 1;
  for (const seed of [1, 2, 3]) {
    for (const f of simFace({ seed, seconds: 3600, ctx: () => ({ listening: true }) })) {
      kinds.add(f.kind);
      minEye = Math.min(minEye, f.eyeL, f.eyeR);
    }
  }
  assert.ok([...kinds].every(k => ['still', 'relax', 'brow'].includes(k)), `聆聽時出現 ${[...kinds]}`);
  assert.ok(kinds.has('relax') && kinds.has('brow'), `事件沒有出現過：${[...kinds]}`);   // 避免測試空轉
  assert.ok(minEye >= 0.90 - 1e-12, `聆聽時眼皮最小 ${minEye}`);
});

test('閒置時四種眼皮事件都會出現（確認上一項的限制來自 listening，不是事件太少）', () => {
  const kinds = new Set();
  for (const seed of [1, 2, 3]) for (const f of simFace({ seed, seconds: 3600 })) kinds.add(f.kind);
  for (const k of ['relax', 'half', 'deep', 'asymmetric', 'brow']) assert.ok(kinds.has(k), `閒置沒出現 ${k}：${[...kinds]}`);
});

test('任何情境都不出現 wink：kind 不是 winkLeft／winkRight，事件造成的 eyeL、eyeR 不低於 0.6', () => {
  const kinds = new Set();
  let minEye = 1;
  const mixed = (t) => {
    const phase = Math.floor(t / 7000) % 6;
    return { listening: phase === 1 || phase === 4, eyesAllowed: phase !== 2, browsAllowed: phase !== 3, blinkAllowed: phase !== 5 };
  };
  for (const ctx of [() => ({}), () => ({ listening: true }), mixed]) {
    for (const seed of [1, 2, 3, 4]) {
      for (const f of simFace({ seed, seconds: 2400, ctx })) {
        kinds.add(f.kind);
        minEye = Math.min(minEye, f.eyeL, f.eyeR);
      }
    }
  }
  assert.ok([...kinds].every(k => !WINK.has(k)), `出現 wink：${[...kinds]}`);
  assert.ok(minEye >= 0.6, `事件讓眼皮降到 ${minEye}`);
  assert.ok(minEye >= 0.64 - 1e-12, `預期最深是 deep 的 0.64，實際 ${minEye}`);   // 左右眼各自 < 0.64 就是不對稱事件超出設計
});

test('createFaceIdle：參考抽到 wink 那一段（≥ 0.92）改成 relax，不出現 wink', () => {
  const idle = createFaceIdle(() => 0.95);
  const kinds = new Set();
  for (let t = 0; t < 40000; t += 16) kinds.add(idle.update(0.016, t, {}).kind);
  assert.ok(kinds.has('relax'), `沒有 relax：${[...kinds]}`);
  assert.ok([...kinds].every(k => !WINK.has(k)));
  assert.throws(() => createFaceIdle(), TypeError);
  assert.throws(() => createBlink(), TypeError);
  assert.throws(() => createFace(), TypeError);
});

test('中途切進聆聽：進行中的 half／deep／asymmetric 立刻改成 relax，從當下值收尾、不跳', () => {
  let tested = 0;
  for (const seed of [1, 2, 3, 4, 5, 6]) {
    const face = createFace(rng(seed));
    const step = 1000 / 60;
    let k = 0, prev = null;
    for (; k < 60 * 3600; k++) {
      const out = face.update(1 / 60, k * step, {});
      if (['half', 'deep', 'asymmetric'].includes(out.kind) && Math.min(out.eyeL, out.eyeR) < 0.85) { prev = out; k++; break; }
    }
    assert.ok(prev, `seed ${seed} 沒有遇到深的眼皮事件`);
    const next = face.update(1 / 60, k * step, { listening: true });
    assert.equal(next.kind, 'relax');
    assert.ok(Math.abs(next.eyeL - prev.eyeL) < 0.03 && Math.abs(next.eyeR - prev.eyeR) < 0.03, '切換瞬間眼皮跳動');
    let last = next.eyeL;
    for (let j = 1; j <= 45; j++) {       // 500 ms 內單調睜開，之後回 1
      const out = face.update(1 / 60, (k + j) * step, { listening: true });
      assert.ok(out.eyeL >= last - 1e-12 && out.eyeL - last < 0.03, `第 ${j} 幀 ${last} → ${out.eyeL}`);
      assert.ok(['relax', 'still', 'brow'].includes(out.kind));
      last = out.eyeL;
    }
    assert.equal(last, 1);
    tested++;
  }
  assert.equal(tested, 6);
});

test('blinkAllowed=false 時 blink 一直是 1；恢復後重新排程眨眼，不補播', () => {
  const frames = simFace({ seed: 41, seconds: 200, ctx: (t) => ({ blinkAllowed: t >= 100000 }) });
  assert.ok(frames.filter(f => f.t < 100000).every(f => f.blink === 1));
  const starts = blinkStarts(frames);
  assert.ok(starts.length > 20 && starts[0] >= 101000, `恢復後第一次眨眼 ${starts[0]}`);
  // 眼皮事件不受影響（blinkAllowed 只管眨眼）
  assert.ok(frames.some(f => f.eyeL < 1));
});

test('eyesAllowed=false 時 eyeL／eyeR 一直是 1（進行中的事件立刻取消），眨眼與眉毛照常', () => {
  const face = createFace(rng(42));
  const step = 1000 / 60;
  let k = 0;
  for (; k < 60 * 3600; k++) if (face.update(1 / 60, k * step, {}).eyeL < 0.9) break;
  const all = [];
  for (let j = 0; j < 60 * 300; j++) all.push(face.update(1 / 60, (k + 1 + j) * step, { eyesAllowed: false }));
  assert.ok(all.every(f => f.eyeL === 1 && f.eyeR === 1));
  assert.ok(all.some(f => f.blink < 1), '眨眼應該照常');
  assert.ok(all.some(f => f.kind === 'brow'), '眉毛事件應該照常');
});

test('enabled=false（潛伏）：blink 1、eyeL／eyeR 1、眉 0、不排事件；恢復後從現在重排', () => {
  const frames = simFace({ seed: 43, seconds: 180, ctx: (t) => ({ enabled: t >= 120000 }) });
  for (const f of frames.filter(f => f.t < 120000)) {
    assert.deepEqual({ ...f, t: 0 }, { t: 0, blink: 1, eyeL: 1, eyeR: 1, browLForm: 0, browRForm: 0, browLAngle: 0, browRAngle: 0, kind: 'still' });
  }
  const after = frames.filter(f => f.t >= 120000);
  assert.ok(blinkStarts(after)[0] >= 121000, '恢復後最快 1 秒才眨眼');
  assert.ok(after.filter(f => f.t < 123000).every(f => f.kind === 'still' && f.eyeL === 1), '恢復後 3 秒內不該有眼皮／眉毛事件');
  assert.ok(after.some(f => f.kind !== 'still'));
  // 進行中被潛伏：立刻中性
  const face = createFace(rng(44));
  let k = 0;
  while (face.update(1 / 60, k++ * (1000 / 60), {}).kind === 'still') if (k > 60 * 600) assert.fail('沒有事件');
  const off = face.update(1 / 60, k * (1000 / 60), { enabled: false });
  assert.equal(off.kind, 'still');
  assert.equal(off.blink, 1);
  assert.equal(off.browLForm, 0);
});

test('browsAllowed 從 true 變 false：眉毛值逐幀遞減不跳，400 ms 後歸 0，之後不排新的眉毛事件', () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const face = createFace(rng(seed));
    const step = 1000 / 60;
    const mag = (o) => [Math.abs(o.browLForm), Math.abs(o.browRForm), Math.abs(o.browLAngle), Math.abs(o.browRAngle)];
    let k = 0, before = null;
    for (; k < 60 * 3600; k++) {
      const o = face.update(1 / 60, k * step, {});
      if (Math.max(...mag(o)) > 0.3) { before = o; k++; break; }
    }
    assert.ok(before, `seed ${seed} 沒有眉毛事件`);
    const frames = [before];
    for (let j = 0; j < 90; j++) frames.push(face.update(1 / 60, (k + j) * step, { browsAllowed: false }));
    let maxStep = 0;
    for (let i = 1; i < frames.length; i++) {
      const a = mag(frames[i - 1]), b = mag(frames[i]);
      for (let c = 0; c < 4; c++) {
        maxStep = Math.max(maxStep, Math.abs(b[c] - a[c]));
        if (i >= 2) assert.ok(b[c] <= a[c] + 1e-12, `seed ${seed} 第 ${i} 幀眉毛回升 ${a[c]} → ${b[c]}`);
      }
    }
    assert.ok(maxStep < 0.08, `seed ${seed} 單幀最大變化 ${maxStep}（直接歸零會是 0.3 以上）`);
    // frames[j+1] 的時間 = flip + j 幀；淡出起點是 frames[1]
    const at400 = frames[1 + Math.ceil(400 / step)];
    assert.deepEqual(mag(at400), [0, 0, 0, 0]);
    // 持續禁止 5 分鐘：沒有任何眉毛，也沒有 brow 事件
    const rest = [];
    for (let j = 0; j < 60 * 300; j++) rest.push(face.update(1 / 60, (k + 90 + j) * step, { browsAllowed: false }));
    assert.ok(rest.every(o => Math.max(...mag(o)) === 0 && o.kind !== 'brow'));
    // 允許後眉毛事件回來
    const back = [];
    for (let j = 0; j < 60 * 300; j++) back.push(face.update(1 / 60, (k + 90 + 60 * 300 + j) * step, {}));
    assert.ok(back.some(o => Math.max(...mag(o)) > 0.3), 'browsAllowed 回 true 後眉毛沒有恢復');
  }
});

test('眉毛輸出符號：Form 隨強度、Angle 左負右正（左右符號相反才是對稱）', () => {
  const frames = simFace({ seed: 7, seconds: 3600 });
  const both = frames.filter(f => f.browLForm !== 0 && f.browRForm !== 0 && Math.abs(f.browLForm) > 0.2);
  assert.ok(both.length > 50);
  for (const f of both) {
    assert.ok(f.browLForm * f.browLAngle < 0, 'L：Form 與 Angle 符號應相反');
    assert.ok(f.browRForm * f.browRAngle > 0, 'R：Form 與 Angle 符號應相同');
  }
});

test('gazeShift：距離 < 0.5 不觸發；≥ 0.5 約 60% 觸發；觸發後很快閉眼', () => {
  const trial = (seed, distance, ctx = {}) => {
    const face = createFace(rng(seed));
    const step = 1000 / 60;
    face.update(1 / 60, 0, ctx);
    const triggered = face.gazeShift(distance, 0);
    const again = triggered ? face.gazeShift(0.9, 0) : null;
    const blinks = [];
    for (let k = 1; k <= 54; k++) blinks.push(face.update(1 / 60, k * step, ctx).blink);   // 到 900 ms：自然眨眼最快 1000 ms
    return { triggered, again, blinks };
  };
  let small = 0, big = 0;
  const N = 3000;
  for (let seed = 1; seed <= N; seed++) {
    const a = trial(seed, 0.49);
    if (a.triggered) small++;
    assert.ok(a.blinks.every(v => v === 1), '距離 < 0.5 不該眨眼');
    const b = trial(seed, 0.5);
    if (b.triggered) {
      big++;
      const firstClosed = b.blinks.findIndex(v => v < 0.5);
      assert.ok(firstClosed >= 0 && (firstClosed + 1) * (1000 / 60) <= 150, `觸發後 ${firstClosed} 幀才閉眼`);
      assert.equal(Math.min(...b.blinks), 0);
      assert.equal(b.again, false, '眨眼進行中不再觸發');
    } else assert.ok(b.blinks.every(v => v === 1), '沒觸發就不該眨眼');
  }
  assert.equal(small, 0);
  assert.ok(big / N > 0.5 && big / N < 0.7, `觸發比例 ${big / N}`);
});

test('gazeShift：聆聽中、眨眼不允許、潛伏時不觸發；不合法的輸入回 false', () => {
  for (const ctx of [{ listening: true }, { blinkAllowed: false }, { enabled: false }]) {
    for (let seed = 1; seed <= 300; seed++) {
      const face = createFace(rng(seed));
      face.update(1 / 60, 0, ctx);
      assert.equal(face.gazeShift(0.9, 0), false, JSON.stringify(ctx));
    }
  }
  const face = createFace(rng(1));
  face.update(1 / 60, 0, {});
  assert.equal(face.gazeShift(NaN, 0), false);
  assert.equal(face.gazeShift(0.9, NaN), false);
});

test('同一個 seed 逐幀可重現；不同 seed 結果不同', () => {
  const ctx = (t) => ({ listening: Math.floor(t / 9000) % 3 === 1, browsAllowed: Math.floor(t / 13000) % 4 !== 2, eyesAllowed: Math.floor(t / 11000) % 5 !== 3 });
  const a = simFace({ seed: 77, seconds: 180, ctx });
  const b = simFace({ seed: 77, seconds: 180, ctx });
  assert.deepEqual(a, b);
  const c = simFace({ seed: 78, seconds: 180, ctx });
  assert.notDeepEqual(a.map(f => f.blink), c.map(f => f.blink));
  // gazeShift 也可重現
  const run = () => {
    const face = createFace(rng(5));
    const out = [];
    for (let k = 0; k < 600; k++) {
      if (k % 30 === 0) out.push(face.gazeShift(0.8, k * (1000 / 60)));
      out.push(face.update(1 / 60, k * (1000 / 60), {}).blink);
    }
    return out;
  };
  assert.deepEqual(run(), run());
});

/** 比對 30 Hz 與 60 Hz 在同一時刻（60 Hz 的偶數幀）的輸出；keys 指定要比的欄位。回傳比對幀數與有動作的幀數 */
function compareRates(seed, ctx, seconds, keys) {
  const f60 = simFace({ seed, hz: 60, seconds, ctx });
  const f30 = simFace({ seed, hz: 30, seconds, ctx });
  assert.equal(f60.length, f30.length * 2 - 1);
  let moved = 0;
  f30.forEach((f, i) => {
    const g = f60[i * 2];
    assert.ok(Math.abs(f.t - g.t) < 1e-6);
    if (keys.includes('kind')) assert.equal(f.kind, g.kind, `seed ${seed} t=${f.t} kind`);
    for (const key of keys.filter(k => k !== 'kind')) {
      assert.ok(Math.abs(f[key] - g[key]) <= 1e-9, `seed ${seed} t=${f.t} ${key}：${f[key]} vs ${g[key]}`);
    }
    if (f.blink < 1 || f.eyeL < 1 || f.browLForm !== 0) moved++;
  });
  return { compared: f30.length, moved };
}
const ALL_KEYS = ['kind', 'blink', 'eyeL', 'eyeR', 'browLForm', 'browRForm', 'browLAngle', 'browRAngle'];

test('30／60 Hz 更新：同一時刻的輸出相同（容許 1e-9）——各種固定情境', () => {
  const contexts = [{}, { listening: true }, { browsAllowed: false }, { eyesAllowed: false }, { blinkAllowed: false }];
  for (const fixed of contexts) {
    for (const seed of [91, 92, 93]) {
      const { compared, moved } = compareRates(seed, () => fixed, 240, ALL_KEYS);
      assert.ok(compared > 7000);
      if (fixed.blinkAllowed !== false && fixed.eyesAllowed !== false) assert.ok(moved > 300, `${JSON.stringify(fixed)} 只有 ${moved} 幀有動作`);
    }
  }
});

test('30／60 Hz 更新：listening、blinkAllowed 在整點切換時，眨眼輸出仍相同', () => {
  // 切換點取在 3 秒的整數倍，30 Hz 與 60 Hz 在同一個幀時間上看到同樣的 ctx；
  // 眼皮／眉毛事件是在「第一個超過排定時間的幀」才依當時的 ctx 抽型態，ctx 剛好在那附近切換時兩種幀率可能抽到不同型態，所以只比眨眼
  const ctx = (t) => {
    const phase = Math.floor((t + 1e-6) / 3000) % 6;
    return { listening: phase === 2 || phase === 3, blinkAllowed: phase !== 5 };
  };
  for (const seed of [91, 92, 93]) {
    const { compared, moved } = compareRates(seed, ctx, 240, ['blink']);
    assert.ok(compared > 7000 && moved > 300);
  }
});
