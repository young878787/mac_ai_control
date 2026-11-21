import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeIdle, analyzeListening, blinkStarts, columns, cursorSpans, gazeShifts, headSpeeds, judgeIdle, judgeListening, listeningWindows, loopRepeat } from '../dev/motion-metrics.js';

const COLS = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion'];
const smooth = (u) => { const c = Math.min(1, Math.max(0, u)); return 3 * c * c - 2 * c * c * c; };

// 合成軌跡：從 0 秒起每 1/hz 秒一列；fn(秒) 回傳 { x, y, z, ex, ey, eo, m }，沒給的欄位用預設（視線 (0.3, 0.2)，不在正中）
// 確定性亂數的慢雜訊（約 0.7 秒一個節點、線性內插），回傳 (秒) → -0.5～0.5；不會週期重複
function slowNoise(seed = 12345) {
  const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296 - 0.5);
  const noise = Array.from({ length: 4000 }, rnd);
  return (s) => { const k = Math.floor(s * 1.5), f = s * 1.5 - k; return noise[k] * (1 - f) + noise[k + 1] * f; };
}

function make(seconds, fn, { hz = 60, marks = [], skip = () => false } = {}) {
  const rows = [];
  for (let k = 0; k <= Math.round(seconds * hz); k++) {
    const sec = k / hz;
    if (skip(sec)) continue;
    const v = fn(sec);
    rows.push([+(sec * 1000).toFixed(3), v.x ?? 0, v.y ?? 0, v.z ?? 0, v.ex ?? 0.3, v.ey ?? 0.2, v.eo ?? 1, v.m ?? 0]);
  }
  return { cols: COLS, motions: ['Idle', 'Nod'], rows, marks, cursor: { px: 0, moves: 0 }, halfRate: false, truncated: false };
}

test('頭部完全不動 → 全程靜止、最長靜止＝整段', () => {
  const m = analyzeIdle(make(10, () => ({})));
  assert.equal(m.slowFraction, 1);
  assert.ok(Math.abs(m.longestStillS - 10) < 0.02);
  assert.equal(m.headSpeedP95, 0);
});

test('頭部持續擺動（5° 0.5 Hz，峰速約 15.7°/s）→ 只有折返點附近算靜止', () => {
  const m = analyzeIdle(make(20, (s) => ({ x: 5 * Math.sin(Math.PI * s) })));
  assert.ok(m.slowFraction > 0.03 && m.slowFraction < 0.06, `slowFraction ${m.slowFraction}`);
  assert.ok(m.longestStillS < 0.15);
});

test('視線 |X|、|Y| 都小於 0.001 才算剛好在正中', () => {
  assert.equal(analyzeIdle(make(5, () => ({ ex: 0, ey: 0 }))).centerFraction, 1);
  assert.equal(analyzeIdle(make(5, () => ({ ex: 0.0005, ey: 0.02 }))).centerFraction, 0);   // 只有一軸在 0 不算
  assert.equal(analyzeIdle(make(5, () => ({}))).centerFraction, 0);
});

test('掃視：0.4 單位 60 ms 走完 → 偵測到一次，90% 到位用內插（真值 42.8 ms，60 Hz 取樣的線性內插約 48.7 ms）', () => {
  const c = columns(make(3, (s) => ({ ex: 0.4 * smooth((s - 1) / 0.06) })));
  const shifts = gazeShifts(c);
  assert.equal(shifts.length, 1);
  assert.ok(Math.abs(shifts[0].distance - 0.4) < 1e-6);
  assert.ok(Math.abs(shifts[0].t90Ms - 48.7) < 1.5, `t90 ${shifts[0].t90Ms}`);
});

test('掃視：太慢（Idle 那種慢曲線）、位移太小（微掃視）都不算', () => {
  assert.equal(gazeShifts(columns(make(10, (s) => ({ ex: 0.1 * s })))).length, 0);                       // 0.1 單位／秒
  assert.equal(gazeShifts(columns(make(3, (s) => ({ ex: 0.05 * smooth((s - 1) / 0.04) })))).length, 0);   // 位移 0.05 < 0.1
  assert.equal(analyzeIdle(make(10, (s) => ({ ex: 0.1 * s }))).saccade90Ms, null);
});

test('掃視：兩次不同長度 → 中位數取兩者平均；30 Hz 取樣的格子較粗', () => {
  const fn = (s) => ({ ex: 0.4 * smooth((s - 1) / 0.04) + 0.4 * smooth((s - 3) / 0.08) });
  const fine = analyzeIdle(make(5, fn));
  assert.equal(fine.shiftCount, 2);
  assert.ok(fine.saccade90Ms > 25 && fine.saccade90Ms < 70, `fine ${fine.saccade90Ms}`);
  const coarse = analyzeIdle(make(5, fn, { hz: 30 }));
  assert.equal(coarse.shiftCount, 2);
  assert.ok(coarse.saccade90Ms >= fine.saccade90Ms - 1);
});

test('眨眼：開度掉到 0.5 以下算一次，間隔取開始時間差', () => {
  const eo = (s) => ((s % 3 > 2 && s % 3 < 2.1) ? 0 : 1);   // 每 3 秒眨一次，從 2 秒起
  const c = columns(make(12, (s) => ({ eo: eo(s) })));
  assert.equal(blinkStarts(c).length, 4);
  const m = analyzeIdle(make(12, (s) => ({ eo: eo(s) })));
  assert.equal(m.blinkCount, 4);
  assert.ok(Math.abs(m.blinkIntervalMedianS - 3) < 0.05);
});

test('不連續的地方（停格、階段之間）不算速度、不算時間、不接眨眼', () => {
  const t = make(10, (s) => ({ x: s < 5 ? 0 : 30, eo: s < 5 ? 1 : 0 }), { skip: (s) => s > 4.5 && s < 5 });   // 少了半秒：x 與眼皮在缺口兩側不同
  const m = analyzeIdle(t);
  assert.equal(m.headSpeedP95, 0);                                  // 缺口兩側差 30°，但那一段不算
  assert.equal(blinkStarts(columns(t)).length, 0);
  assert.ok(m.seconds < 9.6 && m.seconds > 9.4, `seconds ${m.seconds}`);
});

test('judgeIdle：有 Idle 以外的動作、游標動過但沒有移動時間（舊格式）、沒有掃視、不到 60 秒 → 對應項目 FAIL', () => {
  const t = make(5, () => ({ m: 1 }));
  t.cursor = { px: 12, moves: 3 };
  const byName = Object.fromEntries(judgeIdle(analyzeIdle(t)).map(c => [c.name, c]));
  assert.equal(byName['Idle 以外的動作不出現'].pass, false);
  assert.equal(byName['游標移動後排除的時間'].pass, false);
  assert.match(byName['游標移動後排除的時間'].detail, /沒有移動時間/);
  assert.equal(byName['循環重複度（5～40 秒延遲的最大自相關）'].pass, false);
  assert.equal(byName['掃視 90% 到位時間中位數'].pass, false);
  assert.match(byName['掃視 90% 到位時間中位數'].detail, /沒有偵測到掃視/);
  assert.equal(byName['頭部 < 1°/s 的時間佔比'].pass, false);      // 完全不動＝100% 靜止
});

test('columns：缺欄位要明確報錯', () => {
  const t = make(1, () => ({}));
  t.cols = t.cols.filter(c => c !== 'eyeOpen');
  assert.throws(() => columns(t), /eyeOpen/);
});

test('listeningWindows：partial 開始、heard 結束；沒有 heard 就到軌跡結束，連續的 partial 不重複開窗', () => {
  const marks = [{ t: 1000, type: 'partial' }, { t: 1500, type: 'partial' }, { t: 3000, type: 'heard' }, { t: 5000, type: 'partial' }];
  assert.deepEqual(listeningWindows({ marks }, 9000), [[1000, 3000], [5000, 9000]]);
});

// 聆聽軌跡：每輪 partial 在 +0 s、heard 在 +6 s、輪長 16 s；視線在聆聽前停在 (0.8, 0)，聆聽開始 settleMs 後掃回 (0.05, 0.02)
function listening({ rounds = 3, arrive = 0.05, settle = true, headA = 2, listenHeadA = 0.5, blinkEvery = 2.5, listenBlinkEvery = 5 } = {}) {
  const marks = [], span = 16;
  for (let r = 0; r < rounds; r++) { marks.push({ t: r * span * 1000 + 2000, type: 'partial' }, { t: r * span * 1000 + 8000, type: 'heard', status: 'ignored' }); }
  const fn = (s) => {
    const r = Math.floor(s / span), local = s - r * span, inWin = local >= 2 && local < 8;
    const ex = inWin && settle ? 0.8 + (0.05 - 0.8) * smooth((local - 2) / arrive) : 0.8;
    const amp = inWin ? listenHeadA : headA, every = inWin ? listenBlinkEvery : blinkEvery;
    return { ex, ey: inWin && settle ? 0.02 * smooth((local - 2) / arrive) : 0, x: amp * Math.sin(2 * Math.PI * 0.4 * s), eo: ((s % every) > every - 0.1 ? 0 : 1) };
  };
  return make(rounds * span, fn, { marks });
}

test('聆聽：視線快速回正、持續正視、頭動得少、眨得少 → L1、L2、L4、L5 都過', () => {
  const m = analyzeListening(listening());
  assert.equal(m.rounds, 3);
  assert.ok(m.arriveMs.every(v => v <= 150), `arrive ${m.arriveMs}`);
  assert.ok(m.holdFraction >= 0.99);
  assert.ok(m.listenHeadSpeedP95 < 0.5 * m.idleHeadSpeedP95, `${m.listenHeadSpeedP95} vs ${m.idleHeadSpeedP95}`);
  assert.ok(m.blinkRateRatio >= 1.3, `ratio ${m.blinkRateRatio}`);
  const checks = judgeListening(m);
  assert.deepEqual(checks.filter(c => c.pass === false), []);
  assert.equal(checks.find(c => c.name.startsWith('L3')).pass, null);   // 沒有情境欄位（舊軌跡）→ 不判
});

// 在 listening() 的軌跡上加 context 欄：聆聽窗內（2～8 s 內）進入 300 ms 後的列，bad(秒) 為真的那一列記成閒置（索引 7），其餘窗內記聆聽（1）、窗外記閒置
const CONTEXTS = ['peek', 'listening', 'typing', 'click', 'thinking', 'responding', 'cursor', 'idle'];
function withContext(trace, bad = () => false) {
  const span = 16000;
  const rows = trace.rows.map(r => {
    const local = r[0] % span, inWin = local >= 2000 && local < 8000;
    return [...r, inWin && !bad(r[0]) ? 1 : 7];
  });
  return { ...trace, cols: [...trace.cols, 'context'], contexts: CONTEXTS, rows };
}

test('L3：聆聽期間情境一直是 listening → 100% PASS', () => {
  const m = analyzeListening(withContext(listening()));
  assert.equal(m.contextFraction, 1);
  assert.ok(m.contextRows > 0);
  assert.equal(judgeListening(m).find(c => c.name.startsWith('L3')).pass, true);
});

test('L3：進入 0.3 秒後中間有一列變成 idle → FAIL；進入 0.3 秒內的 idle 不算', () => {
  const m = analyzeListening(withContext(listening(), (t) => t === 5000));
  assert.ok(m.contextFraction < 1);
  assert.equal(judgeListening(m).find(c => c.name.startsWith('L3')).pass, false);
  const early = analyzeListening(withContext(listening(), (t) => t >= 2000 && t < 2250));
  assert.equal(early.contextFraction, 1);
});

test('L3：沒有 context 欄 → null（SKIP）；程序層關閉（context 全是 -1）也是 null', () => {
  const plain = analyzeListening(listening());
  assert.equal(plain.contextFraction, null);
  assert.equal(judgeListening(plain).find(c => c.name.startsWith('L3')).pass, null);
  const off = withContext(listening());
  off.rows = off.rows.map(r => [...r.slice(0, -1), -1]);
  assert.equal(judgeListening(analyzeListening(off)).find(c => c.name.startsWith('L3')).pass, null);
});

test('聆聽：視線一直不回正 → L1、L2 FAIL；頭動得一樣多 → L4 FAIL；眨得一樣多 → L5 FAIL', () => {
  const m = analyzeListening(listening({ settle: false, listenHeadA: 2, listenBlinkEvery: 2.5 }));
  const byName = Object.fromEntries(judgeListening(m).map(c => [c.name.slice(0, 2), c]));
  assert.equal(byName.L1.pass, false);
  assert.equal(byName.L2.pass, false);
  assert.equal(byName.L4.pass, false);
  assert.equal(byName.L5.pass, false);
});

test('聆聽：L1 允許一輪太慢（9/10 輪以上就過），兩輪太慢不過', () => {
  const ok = { rounds: 10, arriveMs: [50, 60, 70, 80, 90, 100, 110, 120, 130, 400], arriveWithinLimit: 9, holdFraction: 1, listenHeadSpeedP95: 0, idleHeadSpeedP95: 1,
    listenBlinkCount: 1, listenSeconds: 60, idleBlinkCount: 40, idleSeconds: 100, blinkRateRatio: 24, cursorPx: 0 };
  assert.equal(judgeListening(ok).find(c => c.name.startsWith('L1')).pass, true);
  assert.equal(judgeListening({ ...ok, arriveWithinLimit: 8 }).find(c => c.name.startsWith('L1')).pass, false);
});

test('聆聽：沒有任何聆聽輪次 → 明確 FAIL，不拋例外', () => {
  const m = analyzeListening(make(5, () => ({})));
  assert.equal(m.rounds, 0);
  assert.equal(judgeListening(m).find(c => c.name === '有偵測到聆聽輪次').pass, false);
});

test('headSpeeds：相鄰兩列的度數差（三軸合成）÷ 時間', () => {
  const c = columns(make(1, (s) => ({ x: 3 * s, y: 4 * s })));   // 每秒 (3, 4) → 5°/s
  const v = headSpeeds(c);
  assert.ok(v.length > 50 && v.every(s => Math.abs(s - 5) < 0.01), `speeds ${v.slice(0, 3)}`);   // 時間戳四捨五入到 0.001 ms，速度有 1e-4 等級的誤差
});

test('loopRepeat：8 秒一輪的循環接近 1、延遲落在 8 的倍數；不重複的漂移低很多；不到 60 秒不判', () => {
  const loop = columns(make(120, (s) => ({ x: 8 * Math.sin(2 * Math.PI * s / 8) + 3 * Math.sin(2 * Math.PI * s / 2.67), y: 2 * Math.sin(2 * Math.PI * s / 8 + 1) }), { hz: 30 }));
  const a = loopRepeat(loop);
  assert.ok(a.r > 0.95 && Math.abs(a.lagS % 8) < 0.11, `loop r ${a.r} lag ${a.lagS}`);
  // 不重複：確定性亂數的慢雜訊
  const at = slowNoise();
  const drift = columns(make(300, (s) => ({ x: 20 * at(s), y: 15 * at(s + 1000 / 1.5), z: 10 * at(s + 2000 / 1.5) }), { hz: 30 }));
  const b = loopRepeat(drift);
  assert.ok(b.r < 0.4, `drift r ${b.r}`);
  assert.equal(loopRepeat(columns(make(30, () => ({ x: 1 })))), null);
});

test('游標：每次移動後 3 秒合併成排除區間；排除的時間不算任何閒置指標', () => {
  assert.deepEqual(cursorSpans({ cursor: { px: 9, moves: 3, at: [5000, 1000, 2000] } }), [[1000, 8000]]);
  assert.deepEqual(cursorSpans({ cursor: { px: 9, moves: 2, at: [1000, 9000] } }), [[1000, 4000], [9000, 12000]]);
  assert.deepEqual(cursorSpans({ cursor: { px: 0, moves: 0 } }), []);
  // 100 秒頭部不動，只有游標動過的 10～13 秒在快速擺頭：排除後完全靜止
  const t = make(100, (s) => ({ x: s >= 10 && s <= 13 ? 20 * Math.sin(8 * s) : 0 }));
  t.cursor = { px: 40, moves: 1, at: [10000] };
  const m = analyzeIdle(t);
  assert.equal(m.slowFraction, 1);
  assert.ok(Math.abs(m.cursorSkipS - 3) < 0.05, `skip ${m.cursorSkipS}`);
  const ok = judgeIdle(m).find(c => c.name === '游標移動後排除的時間');
  assert.equal(ok.pass, true);
  assert.match(ok.detail, /移動 1 次/);
  // 排除超過 10%（100 秒裡排除 12 秒）→ 判無效
  t.cursor = { px: 80, moves: 4, at: [10000, 30000, 50000, 70000] };
  assert.equal(judgeIdle(analyzeIdle(t)).find(c => c.name === '游標移動後排除的時間').pass, false);
});

test('循環重複度當通過條件（D7）：r < 0.5 才過；游標排除的時間不參與自相關', () => {
  const loop = make(120, (s) => ({ x: 8 * Math.sin(2 * Math.PI * s / 8), y: 2 * Math.sin(2 * Math.PI * s / 8 + 1) }), { hz: 30 });
  const check = (t) => judgeIdle(analyzeIdle(t)).find(c => c.name === '循環重複度（5～40 秒延遲的最大自相關）');
  assert.equal(check(loop).pass, false);
  assert.match(check(loop).detail, /< 0\.5/);
  // 平常是不重複的小漂移；游標動過的 4 個 3 秒區間（每 8 秒一次）是同樣的大擺動：不排除會被拉高，排除後回到漂移的水準
  const at = slowNoise(777), moves = [20, 28, 36, 44];
  const t = make(120, (s) => ({ x: at(s) + (moves.some(a => s >= a && s <= a + 3) ? 15 * Math.sin(Math.PI * (s % 8 - 4) / 3) : 0) }), { hz: 30 });
  const raw = loopRepeat(columns(t));
  t.cursor = { px: 50, moves: 4, at: moves.map(a => a * 1000) };
  const skipped = analyzeIdle(t).loopRepeat;
  assert.ok(raw.r >= 0.5, `raw r ${raw.r}`);
  assert.ok(skipped.r < 0.5, `skipped r ${skipped.r}`);
});
