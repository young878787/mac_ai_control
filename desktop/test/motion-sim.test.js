// 擬人化模擬指標測試：導演（attention）＋程序層（procedural：ambient、face、mixer）＋狀態機（controller），
// 用假模型參數與虛擬時鐘照 renderer/role/app.js 的每幀順序跑，軌跡交給 dev/motion-metrics.js 判定（和真實量測 R2／R3 同一份定義）。
// T2：閒置十分鐘無機械感（B3）；T3：連續十輪聆聽（D6）。60 Hz 與 30 Hz（O5：Core 每 2 幀算一次）各跑一次。
import test from 'node:test';
import assert from 'node:assert/strict';
import { CONTEXTS, createAttention } from '../domain/attention.js';
import { createController } from '../domain/character.js';
import { createProcedural } from '../renderer/animation/procedural.js';
import { createRandom } from '../renderer/animation/ambient.js';
import { analyzeIdle, analyzeListening, judgeIdle, judgeListening } from '../dev/motion-metrics.js';

const SEEDS = [11, 2024, 90210];
const RATES = [60, 30];
const WARMUP_MS = 8000;                // 出場後暖機，與 ui_motion.py 相同
const IDLE_MINUTES = 10;
const ROUNDS = 10, LISTEN_MS = 8000, GAP_MS = 10000;   // T3：每輪聆聽 8 s、輪間閒置 10 s
const COLS = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion', 'context'];
const MOTIONS = ['Idle', 'Surprised', 'Curious', 'Nod', 'Shake', 'Happy', 'Talk'];

// 假模型的參數：[鍵, 預設, 最小, 最大]（範圍同計劃書 §3.1：頭 ±45／±30／±30、身 ±10、眼球 ±1、眉毛 ±1）
const SPEC = [
  ['angleX', 0, -45, 45], ['angleY', 0, -30, 30], ['angleZ', 0, -30, 30],
  ['bodyX', 0, -10, 10], ['bodyY', 0, -10, 10], ['bodyZ', 0, -10, 10],
  ['eyeX', 0, -1, 1], ['eyeY', 0, -1, 1], ['eyeOpen', 1, 0, 1], ['eyeROpen', 1, 0, 1],
  ['browLY', 0, -1, 1], ['browRY', 0, -1, 1], ['browLX', 0, -1, 1], ['browRX', 0, -1, 1],
  ['browLForm', 0, -1, 1], ['browRForm', 0, -1, 1], ['browLAngle', 0, -1, 1], ['browRAngle', 0, -1, 1],
  ['breath', 0, 0, 1],
];
const N = SPEC.length;
const index = Object.fromEntries(SPEC.map(([key], i) => [key, i]));
const defaults = Float32Array.from(SPEC.map(s => s[1]));
const minimums = Float32Array.from(SPEC.map(s => s[2]));
const maximums = Float32Array.from(SPEC.map(s => s[3]));
const r4 = (n) => Math.round(n * 1e4) / 1e4;   // app.js recordTrace 的取整

/** 假 Idle 動作：只留 Breath（4 s 週期）與頭身、眼的 8 s 週期曲線；程序層必須把後者蓋掉，只有 Breath 保留 */
function writeIdle(values, ms) {
  const k = 2 * Math.PI * ms / 8000;
  values[index.breath] = 0.5 + 0.5 * Math.sin(2 * Math.PI * ms / 4000);
  values[index.angleX] = 14 * Math.sin(k); values[index.angleY] = 7 * Math.sin(k + 1); values[index.angleZ] = 6 * Math.sin(k + 2);
  values[index.bodyX] = 4 * Math.sin(k); values[index.bodyY] = 3 * Math.sin(k + 1); values[index.bodyZ] = 3 * Math.sin(k + 2);
  values[index.eyeX] = 0.5 * Math.sin(k); values[index.eyeY] = 0.3 * Math.sin(k + 1);
  values[index.eyeOpen] = 1; values[index.eyeROpen] = 1;
}

/**
 * 照 app.js 的計算幀跑一段：重設預設值 → Idle → controller.tick／snapshot → attention.update → procedural.plan → procedural.apply → 記一列。
 * events：[{ at（相對記錄起點的 ms）, msg }]，到時間的 bus 訊息經 controller.handle 送入（heard 之後呼叫 attention.heard，同 app.js 順序）。
 */
function simulate({ seed, hz, recordMs, events = [] }) {
  const dtMs = 1000 / hz, dtS = 1 / hz;
  const procedural = createProcedural({ index, defaults, minimums, maximums, seed, blinkMotions: new Set(['talk']) });
  const attention = createAttention({ random: createRandom((seed ^ 0x9e3779b9) >>> 0) });
  const controller = createController();
  const values = new Float32Array(N);
  const rows = [], marks = [];
  const queue = [...events].sort((a, b) => a.at - b.at);
  let clock = 0, next = 0;

  const frame = (recording) => {
    const rel = clock - WARMUP_MS;
    while (recording && next < queue.length && queue[next].at <= rel) {
      const { msg } = queue[next++];
      marks.push({ t: r4(rel), type: msg.type, status: msg.status, wake: msg.wake });
      controller.handle(msg, clock);
      if (msg.type === 'heard') attention.heard(msg.status, clock);
    }
    controller.tick(clock);
    const snap = controller.snapshot(clock);
    values.set(defaults);
    writeIdle(values, clock);
    const att = attention.update(clock, { pose: snap.pose, mode: snap.mode, motion: 'idle', inputOpen: false });
    if (att.plan) procedural.plan(att.plan, clock);
    procedural.apply(values, dtS, clock, { pose: snap.pose, motion: 'idle', listening: att.context === 'listening', swayGain: att.swayGain });
    if (recording) rows.push([r4(rel), r4(values[index.angleX]), r4(values[index.angleY]), r4(values[index.angleZ]),
      r4(values[index.eyeX]), r4(values[index.eyeY]), r4(values[index.eyeOpen]), 0, CONTEXTS.indexOf(att.context)]);
    clock += dtMs;
  };

  controller.handle({ type: 'session', remaining_s: 3600 }, clock);   // 讓角色出場，與 ui_motion.py 相同
  while (clock < WARMUP_MS) frame(false);
  while (clock < WARMUP_MS + recordMs) frame(true);
  return { cols: COLS, motions: MOTIONS, contexts: [...CONTEXTS], rows, marks, cursor: { px: 0, moves: 0, at: [] }, halfRate: hz === 30, truncated: false };
}

/** T3 的事件：每輪 8 s 內每秒一則 partial，輪末一則 heard（ignored），輪間閒置 10 s */
function listeningEvents() {
  const events = [];
  for (let round = 0; round < ROUNDS; round++) {
    const start = GAP_MS + round * (LISTEN_MS + GAP_MS);
    for (let s = 0; s < LISTEN_MS / 1000; s++) events.push({ at: start + s * 1000, msg: { type: 'partial', text: '露西婭', wake: true } });
    events.push({ at: start + LISTEN_MS, msg: { type: 'heard', status: 'ignored', text: '露西婭', command: '' } });
  }
  return events;
}
const listeningMs = GAP_MS + ROUNDS * (LISTEN_MS + GAP_MS);

const fmt = (v, d = 2) => (v === null || v === undefined ? '無資料' : Number.isFinite(v) ? v.toFixed(d) : String(v));
const pct = (v) => (v === null ? '無資料' : `${(v * 100).toFixed(2)}%`);
const failures = (checks) => checks.filter(c => c.pass !== true).map(c => `${c.name}：${c.detail}`);

test('T2 閒置十分鐘：五項指標全過（3 個 seed × 60／30 Hz）', () => {
  for (const hz of RATES) for (const seed of SEEDS) {
    const started = performance.now();
    const m = analyzeIdle(simulate({ seed, hz, recordMs: IDLE_MINUTES * 60000 }));
    const checks = judgeIdle(m);
    const label = `seed ${seed} @${hz}Hz`;
    console.log(`T2 ${label}：靜止 ${pct(m.slowFraction)}、最長靜止 ${fmt(m.longestStillS)} s、正中 ${pct(m.centerFraction)}、掃視 90% ${fmt(m.saccade90Ms, 0)} ms（${m.shiftCount} 次）、循環 r ${fmt(m.loopRepeat && m.loopRepeat.r)}、眨眼 ${m.blinkCount} 次（${(performance.now() - started).toFixed(0)} ms）`);
    assert.equal(m.nonIdleFrames, 0, label);
    assert.deepEqual(failures(checks), [], label);
  }
});

test('T2 同一個 seed 跑兩次逐列相同（60 s），不同 seed 不同', () => {
  for (const hz of RATES) {
    const a = simulate({ seed: 7, hz, recordMs: 60000 }), b = simulate({ seed: 7, hz, recordMs: 60000 }), c = simulate({ seed: 8, hz, recordMs: 60000 });
    assert.deepEqual(a.rows, b.rows, `@${hz}Hz`);
    assert.notDeepEqual(a.rows, c.rows, `@${hz}Hz`);
  }
});

test('T3 聆聽十輪：L1～L5 全過（3 個 seed × 60／30 Hz）', () => {
  for (const hz of RATES) for (const seed of SEEDS) {
    const started = performance.now();
    const m = analyzeListening(simulate({ seed, hz, recordMs: listeningMs, events: listeningEvents() }));
    const checks = judgeListening(m);
    const label = `seed ${seed} @${hz}Hz`;
    const [moved, rounds, l1, l2, l3, l4, l5] = checks;
    console.log(`T3 ${label}：${m.rounds} 輪；L1 ${m.arriveWithinLimit}/${m.rounds}（最慢 ${fmt(Math.max(...m.arriveMs), 0)} ms）、L2 ${pct(m.holdFraction)}、L3 ${m.contextFraction === null ? 'null' : pct(m.contextFraction)}（${m.contextRows} 列）、L4 ${fmt(m.listenHeadSpeedP95)}÷${fmt(m.idleHeadSpeedP95)} °/s、L5 ${fmt(m.blinkRateRatio)}（聆聽 ${m.listenBlinkCount}／${fmt(m.listenSeconds, 0)} s，閒置 ${m.idleBlinkCount}／${fmt(m.idleSeconds, 0)} s）（${(performance.now() - started).toFixed(0)} ms）`);
    assert.equal(m.rounds, ROUNDS, label);
    for (const c of [moved, rounds, l1, l2, l3, l4, l5]) assert.equal(c.pass, true, `${label} ${c.name}：${c.detail}`);
  }
});
