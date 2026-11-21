// 動作軌跡指標（量測用）：輸入 renderer 的 __ui.motionTrace('stop') 輸出，純函式，不碰 DOM／Electron。
// 閒置指標（B3）與聆聽指標（D6）的定義、門檻見 docs/plans/completed/2026-11-20-humanize.md §7.1。
// 命令列（給 scripts/ui_motion.py 用）：node dev/motion-metrics.js idle|listen <trace.json> → 印出 { metrics, checks } 的 JSON
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

export const IDLE_LIMITS = Object.freeze({ slowFraction: 0.15, longestStillS: 2, saccade90Ms: 90, loopRepeat: 0.5, cursorSkipFraction: 0.1 });
export const LISTEN_LIMITS = Object.freeze({ arriveMs: 150, arriveRounds: 0.9, holdFraction: 0.95, headSpeedRatio: 0.5, blinkRateRatio: 1.3 });

const SLOW_DEG_S = 1;          // 頭部合成角速度（AngleX／Y／Z）低於這個值算「靜止」
const CENTER_EPS = 0.001;      // 視線 |X|、|Y| 都小於這個值算「剛好在正中」
const NEAR_CENTER = 0.15;      // 視線離正中這麼近算「正視」（聆聽）
const SHIFT = { start: 1.0, end: 0.3, minDistance: 0.1, settle: 0.9 };   // 眼球速度（單位／秒）：超過 start 開始、連續兩段低於 end 結束；位移 ≥ minDistance 才算掃視；到位＝走完 settle 比例
const BLINK_BELOW = 0.5;       // 眼皮開度低於這個值算閉眼
const GAP_S = 0.25;            // 相鄰兩列相隔超過這麼久（停格、階段之間）不算連續，不參與速度與時間計算
const HOLD_FROM_MS = 300;      // 聆聽：進入後這麼久起才算「持續正視」（之前由到位時間 L1 管）
const HEAD_FROM_MS = 1500;     // 聆聽：進入後這麼久起才比頭部速度（頭回正需要 0.9～1.5 秒）
const AFTER_MS = 2000;         // 聆聽結束後這麼久內不算閒置（轉換期）
const CURSOR_SKIP_MS = 3000;   // 閒置：游標每次移動後這麼久內不算（視線會跟游標；P3 之後 1.5～3 秒失去興趣）
const NEEDED = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion'];

const median = (a) => {
  if (!a.length) return null;
  const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const percentile = (a, q) => {
  if (!a.length) return null;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor(s.length * q))];
};

/** 把 trace 的列轉成逐欄陣列 */
export function columns(trace) {
  for (const name of NEEDED) if (!trace.cols.includes(name)) throw new Error(`軌跡缺少欄位：${name}`);
  const pick = (name) => trace.rows.map(r => r[trace.cols.indexOf(name)]);
  const out = { t: pick('t'), x: pick('angleX'), y: pick('angleY'), z: pick('angleZ'), ex: pick('eyeX'), ey: pick('eyeY'), eo: pick('eyeOpen'), motion: pick('motion') };
  if (trace.cols.includes('context')) out.context = pick('context');   // P3 才有的選用欄位（注意力導演的情境索引，名稱表在 trace.contexts）
  return out;
}

/** 只留 pred(i) 為真的列 */
function keepRows(c, pred) {
  const keep = c.t.map((_, i) => i).filter(pred);
  return Object.fromEntries(Object.entries(c).map(([k, v]) => [k, keep.map(i => v[i])]));
}

/** 第 i 列相對上一列的間隔（秒）；不連續（重複、倒退、超過 GAP_S）回傳 0 */
function stepS(c, i) {
  const dt = (c.t[i] - c.t[i - 1]) / 1000;
  return dt > 0 && dt <= GAP_S ? dt : 0;
}
const headSpeed = (c, i, dt) => Math.hypot(c.x[i] - c.x[i - 1], c.y[i] - c.y[i - 1], c.z[i] - c.z[i - 1]) / dt;

/** 每個連續區間的頭部合成角速度（度／秒） */
export function headSpeeds(c) {
  const out = [];
  for (let i = 1; i < c.t.length; i++) { const dt = stepS(c, i); if (dt) out.push(headSpeed(c, i, dt)); }
  return out;
}

/** 掃視：眼球快速移動並停下的一段。回傳 [{ startMs, distance, t90Ms }]；t90Ms 從「開始變快之前那一列」算起（起點最多高估一個取樣間隔），到位時間用線性內插 */
export function gazeShifts(c) {
  const out = [];
  const speed = (k) => { const dt = stepS(c, k); return dt ? Math.hypot(c.ex[k] - c.ex[k - 1], c.ey[k] - c.ey[k - 1]) / dt : 0; };
  let i = 1;
  while (i < c.t.length) {
    if (speed(i) <= SHIFT.start) { i++; continue; }
    const s = i - 1;
    let end = i, quiet = 0;
    for (let k = i + 1; k < c.t.length; k++) {
      if (speed(k) < SHIFT.end) { if (++quiet >= 2) break; } else { quiet = 0; end = k; }
    }
    const dist = (k) => Math.hypot(c.ex[k] - c.ex[s], c.ey[k] - c.ey[s]);
    const distance = dist(end);
    if (distance >= SHIFT.minDistance) {
      let m = s + 1;
      while (m < end && dist(m) < SHIFT.settle * distance) m++;
      const lo = dist(m - 1), hi = dist(m);   // 90% 落在這兩列之間：線性內插，減少取樣間隔（30 Hz＝33 ms）造成的高估
      const at = hi > lo ? c.t[m - 1] + Math.min(1, Math.max(0, (SHIFT.settle * distance - lo) / (hi - lo))) * (c.t[m] - c.t[m - 1]) : c.t[m];
      out.push({ startMs: c.t[s], distance, t90Ms: at - c.t[s] });
    }
    i = end + 1;
  }
  return out;
}

/** 眨眼開始的時間（ms）：眼皮開度由 ≥ 門檻掉到 < 門檻；不連續的地方不接 */
export function blinkStarts(c) {
  const out = [];
  for (let i = 1; i < c.t.length; i++) {
    if (stepS(c, i) && c.eo[i - 1] >= BLINK_BELOW && c.eo[i] < BLINK_BELOW) out.push(c.t[i]);
  }
  return out;
}

/**
 * 循環重複度：頭部位置（三軸合併）的自相關在 5～40 秒延遲內的最大值，回傳 { r, lagS }；軌跡不到 60 秒回傳 null。
 * 完全重複的循環（原生 Idle 每 8 秒一輪）接近 1；不重複的漂移低很多。參考專案的自相關從 2 秒起算，慢動作本身就相關，分不出好壞，所以這裡從 5 秒起算。
 */
export function loopRepeat(c, skip = []) {
  const HZ = 10, LAG_S = [5, 40];
  if (c.t.length < 2 || (c.t[c.t.length - 1] - c.t[0]) / 1000 < 60) return null;
  const n = Math.floor((c.t[c.t.length - 1] - c.t[0]) / 1000 * HZ);
  const grid = (arr) => {
    const out = new Float64Array(n);
    for (let k = 0, j = 0; k < n; k++) {
      const t = c.t[0] + k * 1000 / HZ;
      while (j + 2 < c.t.length && c.t[j + 1] < t) j++;
      const w = Math.min(1, Math.max(0, (t - c.t[j]) / ((c.t[j + 1] - c.t[j]) || 1)));
      out[k] = arr[j] + (arr[j + 1] - arr[j]) * w;
    }
    let sum = 0, cnt = 0;
    for (let k = 0; k < n; k++) if (valid[k]) { sum += out[k]; cnt++; }
    const mean = cnt ? sum / cnt : 0;
    return out.map(v => v - mean);
  };
  const valid = Array.from({ length: n }, (_, k) => !inside(skip, c.t[0] + k * 1000 / HZ));
  const axes = [c.x, c.y, c.z].map(grid);
  let best = { r: -Infinity, lagS: null };
  for (let lag = LAG_S[0] * HZ; lag <= LAG_S[1] * HZ; lag++) {
    let xy = 0, xx = 0, yy = 0;
    for (const a of axes) for (let k = 0; k + lag < n; k++) {
      if (!valid[k] || !valid[k + lag]) continue;
      xy += a[k] * a[k + lag]; xx += a[k] * a[k]; yy += a[k + lag] * a[k + lag];
    }
    const r = xx > 0 && yy > 0 ? xy / Math.sqrt(xx * yy) : 0;
    if (r > best.r) best = { r, lagS: lag / HZ };
  }
  return best;
}

/** 區間 [[起, 迄], …]（已排序、不重疊）是否包含 t */
function inside(spans, t) {
  for (const [a, b] of spans) { if (t < a) return false; if (t <= b) return true; }
  return false;
}

/** 游標每次移動後 CURSOR_SKIP_MS 內合併成排除區間 */
export function cursorSpans(trace) {
  const at = (trace.cursor && trace.cursor.at) || [];
  const out = [];
  for (const t of [...at].sort((x, y) => x - y)) {
    const last = out[out.length - 1];
    if (last && t <= last[1]) last[1] = Math.max(last[1], t + CURSOR_SKIP_MS);
    else out.push([t, t + CURSOR_SKIP_MS]);
  }
  return out;
}

const connectedSeconds = (c) => { let s = 0; for (let i = 1; i < c.t.length; i++) s += stepS(c, i); return s; };

/** 閒置指標（B3）。trace＝一段只有閒置的軌跡 */
export function analyzeIdle(trace) {
  const all = columns(trace), skip = cursorSpans(trace);
  const c = keepRows(all, (i) => !inside(skip, all.t[i]));
  const idleIndex = trace.motions.indexOf('Idle');
  let seconds = 0, slow = 0, run = 0, longest = 0, center = 0, eyeMaxSpeed = 0;
  for (let i = 1; i < c.t.length; i++) {
    const dt = stepS(c, i);
    if (!dt) { run = 0; continue; }
    seconds += dt;
    if (headSpeed(c, i, dt) < SLOW_DEG_S) { slow += dt; run += dt; longest = Math.max(longest, run); } else run = 0;
    if (Math.abs(c.ex[i]) < CENTER_EPS && Math.abs(c.ey[i]) < CENTER_EPS) center += dt;
    eyeMaxSpeed = Math.max(eyeMaxSpeed, Math.hypot(c.ex[i] - c.ex[i - 1], c.ey[i] - c.ey[i - 1]) / dt);
  }
  const shifts = gazeShifts(c), starts = blinkStarts(c);
  const intervals = starts.slice(1).map((t, i) => (t - starts[i]) / 1000);
  return {
    seconds, frames: c.t.length, halfRate: !!trace.halfRate,
    nonIdleFrames: c.motion.filter(m => m !== idleIndex).length,
    cursorPx: trace.cursor ? trace.cursor.px : null, truncated: !!trace.truncated,
    cursorMoves: trace.cursor ? trace.cursor.moves : null, cursorTimes: !!(trace.cursor && trace.cursor.at),
    cursorSkipS: (connectedSeconds(all) - seconds) || 0, cursorSkipFraction: connectedSeconds(all) ? 1 - seconds / connectedSeconds(all) : null,
    slowFraction: seconds ? slow / seconds : null, longestStillS: longest, centerFraction: seconds ? center / seconds : null,
    headSpeedP50: median(headSpeeds(c)), headSpeedP95: percentile(headSpeeds(c), 0.95),
    loopRepeat: loopRepeat(all, skip), eyeMaxSpeed, shiftCount: shifts.length, saccade90Ms: median(shifts.map(s => s.t90Ms)),
    blinkCount: starts.length, blinkIntervalMedianS: median(intervals),
  };
}

export function judgeIdle(m) {
  const pct = (v) => (v === null ? '無資料' : `${(v * 100).toFixed(2)}%`);
  return [
    { name: '游標移動後排除的時間', pass: m.cursorPx === 0 || (m.cursorTimes && m.cursorSkipFraction !== null && m.cursorSkipFraction <= IDLE_LIMITS.cursorSkipFraction),
      detail: !m.cursorPx ? '游標沒有移動' : !m.cursorTimes ? `移動 ${m.cursorPx.toFixed(0)} px，但軌跡沒有移動時間，無法排除` : `移動 ${m.cursorMoves} 次、${m.cursorPx.toFixed(0)} px，排除 ${m.cursorSkipS.toFixed(1)} s（${pct(m.cursorSkipFraction)}）≤ ${IDLE_LIMITS.cursorSkipFraction * 100}%` },
    { name: 'Idle 以外的動作不出現', pass: m.nonIdleFrames === 0, detail: `${m.nonIdleFrames} 幀` },
    { name: '頭部 < 1°/s 的時間佔比', pass: m.slowFraction !== null && m.slowFraction <= IDLE_LIMITS.slowFraction, detail: `${pct(m.slowFraction)} ≤ ${IDLE_LIMITS.slowFraction * 100}%` },
    { name: '最長連續靜止', pass: m.longestStillS <= IDLE_LIMITS.longestStillS, detail: `${m.longestStillS.toFixed(2)} s ≤ ${IDLE_LIMITS.longestStillS} s` },
    { name: '視線剛好在正中的時間', pass: m.centerFraction === 0, detail: `${pct(m.centerFraction)} = 0%` },
    { name: '循環重複度（5～40 秒延遲的最大自相關）', pass: m.loopRepeat !== null && m.loopRepeat.r < IDLE_LIMITS.loopRepeat,
      detail: m.loopRepeat === null ? '軌跡不到 60 秒，無資料' : `r = ${m.loopRepeat.r.toFixed(2)}（延遲 ${m.loopRepeat.lagS} s）< ${IDLE_LIMITS.loopRepeat}` },
    { name: '掃視 90% 到位時間中位數', pass: m.saccade90Ms !== null && m.saccade90Ms <= IDLE_LIMITS.saccade90Ms,
      detail: m.saccade90Ms === null ? `沒有偵測到掃視（眼球最大速度 ${m.eyeMaxSpeed.toFixed(2)}/s，門檻 ${SHIFT.start}/s）` : `${m.saccade90Ms.toFixed(0)} ms ≤ ${IDLE_LIMITS.saccade90Ms} ms（${m.shiftCount} 次）` },
  ];
}

/** 聆聽的時間窗：第一個 partial 到下一個 heard；沒有 heard 就到軌跡結束 */
export function listeningWindows(trace, endMs) {
  const out = [];
  let start = null;
  for (const m of trace.marks) {
    if (m.type === 'partial' && start === null) start = m.t;
    else if (m.type === 'heard' && start !== null) { out.push([start, m.t]); start = null; }
  }
  if (start !== null) out.push([start, endMs]);
  return out;
}

/** 聆聽指標（D6）。trace＝含多輪聆聽（partial … heard）與輪與輪之間閒置的軌跡 */
export function analyzeListening(trace) {
  const c = columns(trace);
  const wins = listeningWindows(trace, c.t.length ? c.t[c.t.length - 1] : 0);
  const near = (i) => Math.hypot(c.ex[i], c.ey[i]) <= NEAR_CENTER;
  const arriveMs = wins.map(([a, b]) => { const i = c.t.findIndex((t, k) => t >= a && t < b && near(k)); return i < 0 ? Infinity : c.t[i] - a; });
  let nearS = 0, totalS = 0, listenSeconds = 0, listenBlinks = 0, ctxRows = 0, ctxListening = 0;
  const listeningIndex = c.context && trace.contexts ? trace.contexts.indexOf('listening') : -1;   // 舊軌跡沒有情境欄，L3 不判
  const listenSpeeds = [];
  for (const [a, b] of wins) {
    for (let i = 1; i < c.t.length; i++) if (c.t[i] >= a + HOLD_FROM_MS && c.t[i] < b) { const dt = stepS(c, i); totalS += dt; if (near(i)) nearS += dt; }
    if (listeningIndex >= 0) for (let i = 0; i < c.t.length; i++) if (c.t[i] >= a + HOLD_FROM_MS && c.t[i] < b && c.context[i] >= 0) { ctxRows++; if (c.context[i] === listeningIndex) ctxListening++; }   // -1＝程序層關閉，不算
    listenSpeeds.push(...headSpeeds(keepRows(c, i => c.t[i] >= a + HEAD_FROM_MS && c.t[i] < b)));
    const w = keepRows(c, i => c.t[i] >= a && c.t[i] < b);
    listenBlinks += blinkStarts(w).length;
    listenSeconds += (b - a) / 1000;
  }
  const idle = keepRows(c, i => !wins.some(([a, b]) => c.t[i] >= a && c.t[i] < b + AFTER_MS));
  const idleSeconds = connectedSeconds(idle), idleBlinks = blinkStarts(idle).length;
  const idleRate = idleSeconds ? idleBlinks / idleSeconds : null, listenRate = listenSeconds ? listenBlinks / listenSeconds : null;
  const idleP95 = percentile(headSpeeds(idle), 0.95), listenP95 = percentile(listenSpeeds, 0.95);
  return {
    rounds: wins.length, arriveMs, arriveWithinLimit: arriveMs.filter(ms => ms <= LISTEN_LIMITS.arriveMs).length,
    holdFraction: totalS ? nearS / totalS : null,
    contextRows: ctxRows, contextFraction: ctxRows ? ctxListening / ctxRows : null,
    listenHeadSpeedP95: listenP95, idleHeadSpeedP95: idleP95,
    listenBlinkCount: listenBlinks, listenSeconds, idleBlinkCount: idleBlinks, idleSeconds,
    blinkRateRatio: idleRate === null || listenRate === null ? null : (listenRate === 0 ? Infinity : idleRate / listenRate),
    cursorPx: trace.cursor ? trace.cursor.px : null, truncated: !!trace.truncated,
  };
}

export function judgeListening(m) {
  const fmt = (v, d = 2) => (v === null ? '無資料' : Number.isFinite(v) ? v.toFixed(d) : String(v));
  const hasContext = m.contextFraction !== undefined && m.contextFraction !== null;
  const l3Pass = hasContext ? m.contextFraction === 1 : null;
  const l3Detail = hasContext ? `${(m.contextFraction * 100).toFixed(1)}%（${m.contextRows} 列）= 100%` : '軌跡沒有情境欄位（舊軌跡或程序層關閉），無法判定';
  return [
    { name: '量測期間游標沒有移動', pass: m.cursorPx === 0, detail: `移動 ${m.cursorPx} px` },
    { name: '有偵測到聆聽輪次', pass: m.rounds > 0, detail: `${m.rounds} 輪` },
    { name: 'L1 到位：每輪進入聆聽後視線回到正中 0.15 內', pass: m.rounds > 0 && m.arriveWithinLimit / m.rounds >= LISTEN_LIMITS.arriveRounds,
      detail: `${m.arriveWithinLimit}/${m.rounds} 輪 ≤ ${LISTEN_LIMITS.arriveMs} ms（需 ≥ ${LISTEN_LIMITS.arriveRounds * 100}%）；各輪 ${m.arriveMs.map(v => fmt(v, 0)).join('、')} ms` },
    { name: 'L2 持續正視：進入 0.3 秒後視線在正中 0.15 內的時間', pass: m.holdFraction !== null && m.holdFraction >= LISTEN_LIMITS.holdFraction,
      detail: `${m.holdFraction === null ? '無資料' : (m.holdFraction * 100).toFixed(1) + '%'} ≥ ${LISTEN_LIMITS.holdFraction * 100}%` },
    { name: 'L3 聆聽期間不跑閒置劇本、不理游標（進入 0.3 秒後情境一直是聆聽）', pass: l3Pass, detail: l3Detail },
    { name: 'L4 頭部速度：進入 1.5 秒後 p95 ÷ 閒置 p95', pass: m.listenHeadSpeedP95 !== null && m.idleHeadSpeedP95 !== null && m.listenHeadSpeedP95 <= LISTEN_LIMITS.headSpeedRatio * m.idleHeadSpeedP95,
      detail: `${fmt(m.listenHeadSpeedP95)} ÷ ${fmt(m.idleHeadSpeedP95)} °/s ≤ ${LISTEN_LIMITS.headSpeedRatio}` },
    { name: 'L5 眨眼：閒置眨眼率 ÷ 聆聽眨眼率（＝平均間隔比）', pass: m.blinkRateRatio !== null && m.idleBlinkCount > 0 && m.blinkRateRatio >= LISTEN_LIMITS.blinkRateRatio,
      detail: `${fmt(m.blinkRateRatio)} ≥ ${LISTEN_LIMITS.blinkRateRatio}（聆聽 ${m.listenBlinkCount} 次／${m.listenSeconds.toFixed(0)} s，閒置 ${m.idleBlinkCount} 次／${m.idleSeconds.toFixed(0)} s）` },
  ];
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [kind, file] = process.argv.slice(2);
  if (!['idle', 'listen'].includes(kind) || !file) { console.error('用法：node dev/motion-metrics.js idle|listen <trace.json>'); process.exit(2); }
  const trace = JSON.parse(fs.readFileSync(file, 'utf8'));
  const [analyze, judge] = kind === 'idle' ? [analyzeIdle, judgeIdle] : [analyzeListening, judgeListening];
  const metrics = analyze(trace);
  console.log(JSON.stringify({ metrics, checks: judge(metrics) }));
}
