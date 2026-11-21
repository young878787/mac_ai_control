// 待機動作曲線：頭身走五次曲線（保留接管當下的位置與速度）、眼睛先動頭身依序晚起動、掃視與微掃視、多層漂移。
// 移植自 AI_VT_V2 的 AmbientMotion.ts；不移植 hop、rootY、呼吸週期與 breath 輸出、disconnect。
// 差異：呼吸改吃呼叫端給的 breath、漂移乘 swayGain、微掃視起點帶隨機偏移、計畫格式簡化、亂數一律由 seed 產生。
// 純邏輯：不碰 DOM／PIXI，時間由呼叫端注入。輸出是正規化值（-1～1），換成參數值交給 mixer。
import { LIMITS } from './mixer.js';

/** 八個軸：頭、身體各三個角度，加上視線 X／Y */
export const AXES = Object.freeze(['headX', 'headY', 'headZ', 'bodyX', 'bodyY', 'bodyZ', 'gazeX', 'gazeY']);
const GAZE_AXES = ['gazeX', 'gazeY'];
const POSE_AXES = AXES.filter(axis => !axis.startsWith('gaze'));

const TAU = Math.PI * 2;
const DEFAULT_SEED = 0x2f6e2b1;
const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const spring = (value = 0) => ({ value, velocity: 0 });
const still = (value = 0) => ({ startedAt: 0, duration: 1, from: value, velocity: 0, to: value });

// 眼睛先動，頭、身體依序晚起動；延遲期間沿用前一段曲線，位置與速度都連續（參考專案）
const ONSET_LAG_MS = { head: 80, body: 200 };
// 掃視時間隨距離增加（正規化視線距離）；眼睛幾乎瞬間到位，不走頭身的慢曲線（參考專案）
const SACCADE_MS = { base: 35, perUnit: 50, min: 40, max: 80 };
// 注視期間的微掃視：短促的小跳動，間隔 0.35～1.5 秒；Y 行程較小所以縮幅（參考專案）
export const FIXATION_JITTER = 0.14;
const FIXATION = { minRadius: 0.05, yScale: 0.7, moveMs: 35, gapMs: [350, 1500] };
// 頭部移動分兩種：出發（起步快、衝過目標約 5% 再緩緩回來）與歸位（往中性：起步慢、只微彈約 3%）。
// 過衝是明確的兩段軌跡，所以大小與移動時間無關；身體不過衝，頭比身體明顯（參考專案）
const HEAD_MODES = {
  depart: { skew: 0.25, overshoot: 0.055, settleMs: 520 },
  return: { skew: -0.2, overshoot: 0.03, settleMs: 420 },
};
const MIN_BOUNCE = 0.02;
// 歪頭（headZ）專用：幅度越大越慢、峰值角速度有上限、歸位比出發更慵懶；持住大角度時慢慢沉回一點並加大微動（參考專案）
const ROLL = { maxDegPerSec: 18, stretch: 0.5, full: 0.6, returnStretch: 1.4, holdGain: 1.5, holdFull: 0.4 };
const SAG = { fraction: 0.15, full: 0.4, tauMs: 600 };
// 呼吸帶動頭、身體的上下起伏（正規化，乘上呼吸與 BREATH_GAIN）：係數同參考專案
const BREATH_LIFT = { headY: 0.06, bodyY: 0.08 };
// 呼吸起伏的增益：參考專案的 amplitude 預設 0.25，這裡 breath 是 0～1 的 Idle 呼吸值，0.6 為手感，不對就改這裡
const BREATH_GAIN = 0.6;
// 漂移強度跟隨呼叫端 swayGain 的彈簧頻率（rad/s）：聆聽時降到 0.35，約 0.5 秒內平順過渡
const SWAY_GAIN_OMEGA = 6;
// 確定性漂移：同一個 seed 得到同一條連續曲線，沒有週期。多層帶限雜訊（Hz, 相對權重）；頭身各軸的峰值幅度為正規化值（參考專案）
const SWAY_LAYERS = [[0.23, 1], [0.55, 0.8], [1.05, 0.4]];
const SWAY_WEIGHT = SWAY_LAYERS.reduce((sum, [, weight]) => sum + weight, 0);
const SWAY_AMPLITUDE = { headX: 0.13, headY: 0.15, headZ: 0.13, bodyX: 0.14, bodyY: 0.12, bodyZ: 0.13 };
// 身體的漂移以此延遲傳給頭部對應的軸，頭比身體慢半拍（參考專案）
const BODY_TO_HEAD = { gain: 0.35, lagSec: 0.18 };
// 歪頭時臉跟著轉：把頭 Z 的曲線值耦合到 X（同向轉臉）與 Y（歪向任一側都略抬）；符號與幅度純屬手感（參考專案）
export const TILT_FACE = { x: 0.45, y: 0.35 };
const SWAY_ENVELOPE_HZ = 0.043;
// 兩次 update 相隔超過這麼久視為背景恢復，從最後顯示的位置接續
const RESUME_GAP_MS = 250;

function hashUnit(seed, channel, cell) {
  let h = (seed ^ Math.imul(channel + 1, 0x9e3779b1) ^ Math.imul(cell | 0, 0x85ebca6b)) >>> 0;
  h = Math.imul(h ^ (h >>> 16), 0x7feb352d);
  h = Math.imul(h ^ (h >>> 15), 0x846ca68b);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** 一維梯度雜訊（五次淡化，二階連續），輸出約 -1～1 */
function gradientNoise(seed, channel, x) {
  const cell = Math.floor(x);
  const t = x - cell;
  const fade = t * t * t * (t * (t * 6 - 15) + 10);
  const g0 = hashUnit(seed, channel, cell) * 2 - 1;
  const g1 = hashUnit(seed, channel, cell + 1) * 2 - 1;
  return 2 * (g0 * t * (1 - fade) + g1 * (t - 1) * fade);
}

/** 帶 seed 的亂數產生器（mulberry32 風格），回傳 [0, 1)；同一個 seed 逐次相同 */
export function createRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 臨界阻尼解析解；相同經過時間不受 30／60／120 Hz 分割影響 */
function advance(s, target, omega, dt) {
  const offset = s.value - target;
  const change = (s.velocity + omega * offset) * dt;
  const decay = Math.exp(-omega * dt);
  s.value = target + (offset + change) * decay;
  s.velocity = (s.velocity - omega * change) * decay;
  return s.value;
}

/** 歪頭移動時間：幅度越大越慢，歸位更慵懶，並讓峰值角速度（約 2 倍平均速度）不超過上限 */
function rollDuration(baseMs, amplitude, returning) {
  const stretched = baseMs * (1 + ROLL.stretch * clamp(amplitude / ROLL.full, 0, 1));
  const duration = Math.max(stretched, 2000 * amplitude * LIMITS.angleZ / ROLL.maxDegPerSec);
  return returning ? duration * ROLL.returnStretch : duration;
}

/** 五次曲線保留接管當下的位置與速度；起訖加速度為零，速度呈鐘形 */
function sampleQuintic(segment, nowMs) {
  if (nowMs < segment.startedAt && segment.previous) return sampleQuintic(segment.previous, nowMs);
  const progress = clamp((nowMs - segment.startedAt) / segment.duration, 0, 1);
  if (progress >= 1) return spring(segment.to);
  // 時間扭曲 u(t) = t + k t (1 - t)：端點不變、u' 恆正；起訖速度為零的曲線仍是零
  const skew = segment.skew ?? 0;
  const t = progress + skew * progress * (1 - progress);
  const warp = 1 + skew * (1 - 2 * progress);
  const durationSec = segment.duration / 1000;
  const velocity = segment.velocity * durationSec;
  const delta = segment.to - segment.from;
  const c3 = 10 * delta - 6 * velocity;
  const c4 = -15 * delta + 8 * velocity;
  const c5 = 6 * delta - 3 * velocity;
  const value = segment.from + velocity * t + c3 * t ** 3 + c4 * t ** 4 + c5 * t ** 5;
  const speed = (velocity + 3 * c3 * t ** 2 + 4 * c4 * t ** 3 + 5 * c5 * t ** 4) / durationSec * warp;
  return { value, velocity: speed };
}

/** 加上到位後的沉回；(1 - (1 + x) e^-x) 起始速度為零，接上五次曲線不會產生速度跳變 */
function samplePose(segment, nowMs) {
  if (nowMs < segment.startedAt && segment.previous) return samplePose(segment.previous, nowMs);
  if (segment.after && nowMs >= segment.after.startedAt) return samplePose(segment.after, nowMs);
  const state = sampleQuintic(segment, nowMs);
  const elapsed = nowMs - segment.startedAt - segment.duration;
  if (!segment.sag || elapsed <= 0) return state;
  const x = elapsed / SAG.tauMs;
  const decay = Math.exp(-x);
  return { value: state.value - segment.sag * (1 - (1 + x) * decay),
    velocity: state.velocity - segment.sag * x * decay / (SAG.tauMs / 1000) };
}

const inRange = (value, minimum, maximum) => typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum;

/** 計畫格式：sequence 正整數、moveMs 100～3000、target 八個軸全部在 [-1, 1] 且不多不少 */
function isPlan(plan) {
  if (typeof plan !== 'object' || plan === null) return false;
  if (!Number.isSafeInteger(plan.sequence) || plan.sequence <= 0) return false;
  if (!inRange(plan.moveMs, 100, 3000)) return false;
  const target = plan.target;
  if (typeof target !== 'object' || target === null || Array.isArray(target)) return false;
  return Object.keys(target).length === AXES.length && AXES.every(axis => inRange(target[axis], -1, 1));
}

/**
 * @param {number} seed 決定整條漂移與微掃視曲線；同一個 seed 逐幀相同
 */
export function createAmbient(seed = DEFAULT_SEED) {
  let random, sequence, moveMs, target, poses, gazes, fixation, fixationAtMs, driftSec;
  let bodyGain, gazeGain, enableGain, swayGainState, wasEnabled, lastNowMs, hasUpdated;

  /** 微掃視的起點偏移：半徑同之後的微掃視，所以視線從第一刻起就不會剛好在 (0, 0) */
  function fixationOffset() {
    const angle = random() * TAU;
    const radius = FIXATION.minRadius + random() * (FIXATION_JITTER - FIXATION.minRadius);
    return { gazeX: Math.cos(angle) * radius, gazeY: Math.sin(angle) * radius * FIXATION.yScale };
  }

  function init() {
    random = createRandom(seed);
    sequence = 0;
    moveMs = 1000;
    target = Object.fromEntries(AXES.map(axis => [axis, 0]));
    poses = Object.fromEntries(POSE_AXES.map(axis => [axis, still()]));
    gazes = { gazeX: still(), gazeY: still() };
    const offset = fixationOffset();
    fixation = { gazeX: still(offset.gazeX), gazeY: still(offset.gazeY) };
    fixationAtMs = null;
    driftSec = 0;
    bodyGain = spring(1);
    gazeGain = spring(1);
    enableGain = spring(1);
    swayGainState = spring(1);
    wasEnabled = true;
    lastNowMs = 0;
    hasUpdated = false;
  }

  /** 背景分頁恢復時從最後顯示的位置接續，不跳到牆鐘時間的進度 */
  function sampledAt(nowMs) {
    return hasUpdated && nowMs - lastNowMs > RESUME_GAP_MS ? lastNowMs : nowMs;
  }

  /** 持續的低頻漂移；停住時也不會靜止。頭部另外接收延遲後的身體漂移 */
  function sway(axis, seconds) {
    const own = swayOwn(axis, seconds);
    if (!axis.startsWith('head')) return own;
    return own + BODY_TO_HEAD.gain * swayOwn(axis.replace('head', 'body'), seconds - BODY_TO_HEAD.lagSec);
  }

  function swayOwn(axis, seconds) {
    const index = POSE_AXES.indexOf(axis);
    let sum = 0;
    SWAY_LAYERS.forEach(([hz, weight], layer) => {
      const channel = index * 8 + layer;
      sum += weight * gradientNoise(seed, channel, seconds * hz + hashUnit(seed, channel, 0x5eed) * 64);
    });
    // 幅度本身緩慢起伏，避免全程等強度的機械感
    const envelope = 0.75 + 0.35 * gradientNoise(seed, 99, seconds * SWAY_ENVELOPE_HZ + index * 7.3);
    return SWAY_AMPLITUDE[axis] * clamp(envelope, 0.45, 1.1) * sum / SWAY_WEIGHT;
  }

  // 眼睛只有一個目標，兩軸同時到位；時間依距離，與頭身 moveMs 無關
  function retargetGaze(nowMs, enabled) {
    const at = sampledAt(nowMs);
    const from = GAZE_AXES.map(axis => sampleQuintic(gazes[axis], at).value);
    const to = GAZE_AXES.map(axis => enabled ? target[axis] : 0);
    const distance = Math.hypot(to[0] - from[0], to[1] - from[1]);
    const duration = clamp(SACCADE_MS.base + SACCADE_MS.perUnit * distance, SACCADE_MS.min, SACCADE_MS.max);
    GAZE_AXES.forEach((axis, index) => {
      gazes[axis] = { startedAt: nowMs, duration, from: from[index], velocity: 0, to: to[index] };
    });
  }

  function nextFixationGap() {
    return FIXATION.gapMs[0] + random() * (FIXATION.gapMs[1] - FIXATION.gapMs[0]);
  }

  function scheduleFixation(nowMs) {
    if (fixationAtMs === null) fixationAtMs = nowMs + nextFixationGap();
  }

  // 排程時間不依 frame 取樣，所以 30／60／120 Hz 得到同一串微掃視
  function advanceFixation(nowMs) {
    if (fixationAtMs === null || nowMs < fixationAtMs) return;
    const startedAt = fixationAtMs;
    const angle = random() * TAU;
    const radius = FIXATION.minRadius + random() * (FIXATION_JITTER - FIXATION.minRadius);
    const to = { gazeX: Math.cos(angle) * radius, gazeY: Math.sin(angle) * radius * FIXATION.yScale };
    for (const axis of GAZE_AXES) {
      fixation[axis] = { startedAt, duration: FIXATION.moveMs, velocity: 0,
        from: sampleQuintic(fixation[axis], startedAt).value, to: to[axis] };
    }
    fixationAtMs = startedAt + nextFixationGap();
    if (fixationAtMs < nowMs) fixationAtMs = nowMs + nextFixationGap();
  }

  // 新目標從目前位置與速度起算；停用時收回中性位置
  function retarget(nowMs, enabled, axes = POSE_AXES, staggered = false) {
    const at = sampledAt(nowMs);
    // 背景恢復從最後畫面接續，不延遲起動，避免延遲期間顯示牆鐘進度
    const lagged = staggered && at === nowMs;
    for (const axis of axes) {
      const body = axis.startsWith('body');
      const previous = poses[axis];
      const startedAt = nowMs + (lagged ? ONSET_LAG_MS[body ? 'body' : 'head'] : 0);
      const current = samplePose(previous, lagged ? startedAt : at);
      if (previous.startedAt <= nowMs) previous.previous = undefined;
      const to = enabled ? target[axis] : 0;
      const next = {
        startedAt, duration: moveMs * (body ? 1.45 : 1),
        from: current.value, velocity: current.velocity, to,
        previous: lagged ? previous : undefined,
      };
      if (!body) {
        // 往中性（幅度變小且不跨過 0）算歸位，其餘算出發；兩者曲線與回彈不同
        const returning = Math.abs(to) < Math.abs(current.value) - 0.03
          && (to * current.value >= 0 || Math.abs(to) < 0.05);
        const { skew, overshoot, settleMs } = HEAD_MODES[returning ? 'return' : 'depart'];
        const distance = Math.abs(to - current.value);
        next.skew = skew;
        next.velocity = current.velocity / (1 + skew);
        if (axis === 'headZ') next.duration = rollDuration(next.duration, distance, returning);
        const sag = axis === 'headZ' && !returning ? SAG.fraction * clamp(Math.abs(to) / SAG.full, 0, 1) * to : undefined;
        if (distance >= MIN_BOUNCE) {
          // 先衝到目標之外一點，再從零速度緩緩回到目標；沉回接在回彈之後
          next.to = to + Math.sign(to - current.value) * overshoot * distance;
          next.after = { startedAt: startedAt + next.duration, duration: settleMs,
            from: next.to, velocity: 0, to, sag };
        } else next.sag = sag;
      }
      poses[axis] = next;
    }
  }

  init();

  return {
    /** 套用一份計畫；格式不合或 sequence 沒有比上一份大就回 false。只換眼神的計畫不重啟頭身（只重排目標有變的軸） */
    apply(plan, nowMs) {
      if (!isPlan(plan) || !Number.isFinite(nowMs) || plan.sequence <= sequence) return false;
      sequence = plan.sequence;
      moveMs = plan.moveMs;
      const changed = POSE_AXES.filter(axis => plan.target[axis] !== target[axis]);
      const gazeChanged = GAZE_AXES.some(axis => plan.target[axis] !== target[axis]);
      target = Object.fromEntries(AXES.map(axis => [axis, plan.target[axis]]));
      scheduleFixation(nowMs);
      if (wasEnabled) {
        retarget(nowMs, true, changed, true);
        if (gazeChanged) retargetGaze(nowMs, true);
      }
      return true;
    },

    /** 往前一幀並回傳八個軸的正規化值（-1～1）。enabled=false（潛伏）時收回中性；swayGain 縮放漂移（聆聽時 0.35）；breath 是當幀 Idle 的 ParamBreath（約 0～1） */
    update(dtS, nowMs, { enabled = true, swayGain = 1, breath = 0.5, bodyWeight = 1, gazeWeight = 1 } = {}) {
      const dt = Number.isFinite(dtS) ? clamp(dtS, 0, 0.1) : 0;
      const now = Number.isFinite(nowMs) ? nowMs : lastNowMs;
      // 背景分頁恢復時從最後顯示的位置收尾，不瞬間落地或補播過期跳動
      if (hasUpdated && now - lastNowMs > RESUME_GAP_MS) {
        retarget(now, wasEnabled);
        retargetGaze(now, wasEnabled);
        fixationAtMs = null;
      }
      scheduleFixation(now);
      lastNowMs = now;
      hasUpdated = true;
      if (wasEnabled !== enabled) {
        retarget(now, enabled);
        retargetGaze(now, enabled);
      }
      wasEnabled = enabled;
      driftSec += dt;
      const body = advance(bodyGain, Number.isFinite(bodyWeight) ? clamp(bodyWeight, 0, 1) : 0, 24, dt);
      const gaze = advance(gazeGain, Number.isFinite(gazeWeight) ? clamp(gazeWeight, 0, 1) : 0, 24, dt);
      const gain = advance(enableGain, enabled ? 1 : 0, 18, dt);
      const swayScale = advance(swayGainState, Number.isFinite(swayGain) ? clamp(swayGain, 0, 1) : 1, SWAY_GAIN_OMEGA, dt);
      const lift = (Number.isFinite(breath) ? clamp(breath, 0, 1) : 0.5) - 0.5;
      const result = {};
      // 眼球走短促的掃視，注視時疊加微掃視；頭身走五次曲線
      advanceFixation(now);
      for (const axis of GAZE_AXES) {
        const value = sampleQuintic(gazes[axis], now).value + sampleQuintic(fixation[axis], now).value;
        result[axis] = clamp(value, -1, 1) * gain * gaze;
      }
      const tilt = samplePose(poses.headZ, now).value;
      for (const axis of POSE_AXES) {
        let value = samplePose(poses[axis], now).value;
        if (axis === 'headX') value += TILT_FACE.x * tilt;
        else if (axis === 'headY') value += TILT_FACE.y * Math.abs(tilt);
        // 歪頭大角度持住時微動加大，避免凍結在那個角度
        const hold = axis === 'headZ' ? 1 + ROLL.holdGain * clamp(Math.abs(value) / ROLL.holdFull, 0, 1) : 1;
        value += sway(axis, driftSec) * hold * swayScale + (BREATH_LIFT[axis] ?? 0) * lift * BREATH_GAIN;
        result[axis] = clamp(value, -1, 1) * gain * body;
      }
      return result;
    },

    /** 目前計畫的目標（八個軸的副本） */
    target: () => ({ ...target }),

    /** 回到剛建立的狀態（sequence、曲線、亂數流都重來） */
    reset: init,
  };
}
