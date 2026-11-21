// 注意力導演：依情境與優先順序決定角色看哪裡、頭身往哪擺，輸出 ambient 的「計畫」（正規化 -1～1，只說座標、不知道 rig 參數）。
// 改寫自 AI_VT_V2 的 avatar_runtime.py（閒置劇本、聆聽、_side／_settled／_saccade／_sample_gaze），加上桌面專屬情境（打字、點擊、思考、回應、游標）。
// 純邏輯：不碰 DOM／PIXI；時間一律由呼叫端注入（毫秒），亂數一律由呼叫端注入（帶 seed 的 random）。第二版的 hum_sway／hop／stretch 不移植（B6）。
// 座標慣例：gazeX／headX 正＝畫面右，gazeY／headY 正＝上；游標 dx 右正、dy 上正（相對頭部中心的 px）。

import { MOTIONS } from './character.js';

/** 情境，數字小的優先（peek 最高）；軌跡的 context 欄存這個陣列的索引 */
export const CONTEXTS = Object.freeze(['peek', 'listening', 'typing', 'click', 'thinking', 'responding', 'cursor', 'idle']);
/** 閒置劇本：[權重, 冷卻秒]，同參考專案；hum_sway／hop／stretch 是第二版（B6），不得出現 */
export const IDLE_ACTIONS = Object.freeze({
  rest_front: [3.0, 0], glance_aside: [2.6, 0], weight_shift: [1.8, 6], look_down: [1.2, 12], think_up: [1.1, 12], curious_tilt: [0.8, 14],
});
export const CURSOR_PX = 400;   // 游標 px → 正規化（同舊做法：除以 400、夾在 ±1）

const AXES = ['headX', 'headY', 'headZ', 'bodyX', 'bodyY', 'bodyZ', 'gazeX', 'gazeY'];
const POSE_AXES = AXES.slice(0, 6);
const MOVE_MS = { min: 100, max: 3000 };            // ambient.apply 接受的 moveMs 範圍；點頭 250 ms 也要能送
const LISTEN_SWAY_GAIN = 0.35;                      // 聆聽時漂移縮小到 35%（手感，§4.6）
// 近期降權與同一劇本不連續的上限（參考專案）：recent 長度 4、每出現一次權重 ×0.3；視線方向歷史長度 2
const RECENT_ACTIONS = 4;
const RECENT_GAZE = 2;
const RECENT_DECAY = 0.3;
// 好奇歪頭相對目前歪斜（headZ，正規化）的最大變化量，約 18 度（參考專案）
const TILT_MAX_DELTA = 0.78;
// 視線方向的範圍 [[x 範圍], [y 範圍]]（參考專案 GAZE_DIRECTION_RANGES）
const GAZE_DIRECTION_RANGES = {
  left: [[-0.75, -0.45], [-0.04, 0.04]],
  right: [[0.45, 0.75], [-0.04, 0.04]],
  up: [[-0.04, 0.04], [0.90, 1.0]],
  down: [[-0.04, 0.04], [-1.0, -0.95]],
  up_left: [[-0.80, -0.55], [0.85, 1.0]],
  up_right: [[0.55, 0.80], [0.85, 1.0]],
  down_left: [[-0.80, -0.55], [-1.0, -0.85]],
  down_right: [[0.55, 0.80], [-1.0, -0.85]],
};
// 聆聽：目標收斂（保留目前偏移的 60% 再加雜訊）、移動時間、重新規劃的停留；歪頭每次聆聽一次（歪頭幅度為手感）
const LISTEN = { keep: 0.6, noise: 0.065, moveMs: [900, 1500], holdS: [2.5, 4.8], tilt: [0.1, 0.2] };
// 打字：看輸入框（視窗下方）、頭微低；停打超過 typingMs 就改看使用者（手感）
const TYPING = { ms: 2000, gaze: [0, -0.7], headY: -0.15, moveMs: [600, 900] };
// 點擊：看點擊處的時間、頭跟的比例、移動時間（手感）
const CLICK = { holdMs: [800, 1200], headRatio: [0.30, 0.45], moveMs: [500, 800] };
// 思考：進入後多久才出第一個計畫（範圍同 think_up）、移動時間、之後每隔多久在那一帶小幅掃視、掃視幅度
const THINKING = { startMs: [300, 600], moveMs: [1100, 1600], lookMs: [1000, 2000], jitter: [0.08, 0.15] };
// 回應：移開視線的長度與位置（Talk 開頭，手感）、移動時間、動作回到 Idle 之後多久結束
const RESPONDING = { awayMs: [300, 500], awayX: [0.3, 0.5], awayY: [-0.1, 0.15], moveMs: [800, 1200], afterMs: [1000, 2000] };
// 游標（§4.5）：吸引（視窗內累計移動 px）、追視（離注視點的距離、掃視間隔）、失去興趣（靜止視窗、靜止 px、靜止多久）、冷卻
const CURSOR = {
  attractWindowMs: 500, attractPx: 60, chaseDistance: 0.08, chaseGapMs: 150,
  stillWindowMs: 300, stillPx: 8, stillMs: [1500, 3000], cooldownMs: 2000, headRatio: [0.30, 0.45], moveMs: [600, 900],
};
// 回到閒置時先看使用者：移動時間、停留（參考專案的 _settled 收尾）
const IDLE_RETURN = { moveMs: [1000, 1500], holdS: [1.1, 2.0] };
// 收到指令的點頭（獨立覆蓋層）：下去的幅度與時間、回來的時間、覆蓋總長（手感）
const NOD = { dip: 0.12, downMs: 250, upMs: 350, spanMs: 600 };

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
/** 夾在 ±limit 並 round 到 4 位（同參考的 _bounded）；+ 0 把 -0 變成 0 */
const bound = (value, limit = 1) => Math.round(clamp(value, -limit, limit) * 1e4) / 1e4 + 0;
const zeroTarget = () => Object.fromEntries(AXES.map(axis => [axis, 0]));

/**
 * @typedef {Record<string, number>} Target 八個軸（headX…gazeY）的正規化值
 * @typedef {{ sequence: number, moveMs: number, target: Target }} Plan ambient.apply 吃的計畫
 * @typedef {{ target: Target, moveMs: number | null, delayMs: number }} Step 一個步驟：送出後 delayMs 才輪到下一步；moveMs＝null 沿用上一個（只換眼神）
 * @typedef {{ pose?: string, mode?: string, motion?: string, inputOpen?: boolean }} Inputs
 */

/**
 * @param {{ random: () => number }} options random：[0,1) 的亂數函式（帶 seed，由呼叫端注入）
 */
export function createAttention({ random }) {
  const u = (low, high) => low + (high - low) * random();
  const int = (low, high) => low + Math.floor(random() * (high - low + 1));
  /** @param {number[]} bounds [下限, 上限] */
  const range = (bounds) => u(bounds[0], bounds[1]);

  let sequence = 0;
  /** @type {Target} 最後送出的計畫目標（ambient 目前追的目標） */
  let target = zeroTarget();
  let lastMoveMs = 1000;
  /** @type {string | null} */
  let ctx = null;
  let since = 0;
  let prevMode = 'idle';
  let lastNow = 0;   // 最近一次 update 的時間（劇本在送出步驟時才挑，記錄起點用）
  // 目前情境的步驟佇列：到 nextAt 才送下一步；佇列空了就問 refill 要不要補
  /** @type {Step[]} */
  let queue = [];
  /** @type {(() => Step[]) | null} */
  let refill = null;
  let nextAt = Infinity;
  // 閒置劇本的選擇歷史
  /** @type {string[]} */
  const recent = [];
  /** @type {string[]} */
  const recentGaze = [];
  /** @type {Record<string, number>} */
  const startedAt = {};
  let lastSide = 0;
  /** @type {{ name: string, startedAt: number, serial: number } | null} */
  let action = null;
  let actionSerial = 0;
  // 事件
  /** @type {number | null} */
  let typingAt = null;
  /** @type {{ at: number, durMs: number, dx: number, dy: number } | null} */
  let click = null;
  let clickDirty = false;
  /** @type {{ at: number, started: boolean, returned: boolean, base: number, endAt: number } | null} */
  let nod = null;
  /** @type {{ sawReaction: boolean, endAt: number | null } | null} */
  let responding = null;
  // 游標
  /** @type {{ dx: number, dy: number } | null} */
  let cursorPos = null;
  /** @type {{ t: number, d: number }[]} */
  let moves = [];
  let cursorActive = false;
  let cursorK = 0.37;
  let lastChaseAt = -Infinity;
  /** @type {number | null} */
  let stillSince = null;
  let stillFor = 0;
  let cooldownUntil = -Infinity;

  /**
   * 目前的計畫目標；點頭下去期間，用點頭之前的值，免得別的情境把低頭帶進自己的目標
   * @returns {Target}
   */
  function current() {
    const now = { ...target };
    if (nod && nod.started && !nod.returned) now.headY = nod.base;
    return now;
  }

  /** 把目標整理成合法計畫（4 位、±1、moveMs 夾在 100～3000）並記為最後送出的目標 */
  function emit(next, moveMs) {
    const rounded = {};
    for (const axis of AXES) rounded[axis] = bound(next[axis]);
    lastMoveMs = clamp(Math.round(moveMs ?? lastMoveMs), MOVE_MS.min, MOVE_MS.max);
    target = rounded;
    return { sequence: ++sequence, moveMs: lastMoveMs, target: { ...rounded } };
  }

  // ---------- 參考專案的目標產生（avatar_runtime.py） ----------

  /** 頭身六個值加視線，每個夾在 ±1 */
  function pose(head, body, gaze = [0, 0]) {
    const values = [...head, ...body, ...gaze];
    return Object.fromEntries(AXES.map((axis, i) => [axis, bound(values[i])]));
  }

  /** 接近中性、各軸獨立的姿勢，用在休息與歸位 */
  function settled() {
    return pose([u(-0.18, 0.18), u(-0.15, 0.15), u(-0.15, 0.15)], [u(-0.2, 0.2), u(-0.15, 0.15), u(-0.2, 0.2)]);
  }

  /** 偏好和上次相反，但不禁止重複 */
  function pickSide() {
    const side = lastSide && random() < 0.5 ? -lastSide : (random() < 0.5 ? -1 : 1);
    lastSide = side;
    return side;
  }

  /** 看使用者附近的重新對焦；Y 較寬因為模型垂直動得少 */
  function saccade() {
    const angle = u(0, Math.PI * 2);
    const radius = u(0.12, 0.26);
    return [Math.cos(angle) * radius, Math.sin(angle) * radius * 1.3];
  }

  /** 眼睛方向的歷史獨立於身體方向；剛用過的方向降權 */
  function sampleGaze() {
    const names = Object.keys(GAZE_DIRECTION_RANGES);
    const direction = pickWeighted(names, names.map(name => RECENT_DECAY ** recentGaze.filter(d => d === name).length));
    recentGaze.push(direction);
    if (recentGaze.length > RECENT_GAZE) recentGaze.shift();
    const [xRange, yRange] = GAZE_DIRECTION_RANGES[direction];
    return [range(xRange), range(yRange)];
  }

  function pickWeighted(names, weights) {
    const total = weights.reduce((sum, w) => sum + w, 0);
    let roll = random() * total;
    for (let i = 0; i < names.length; i++) {
      roll -= weights[i];
      if (roll < 0) return names[i];
    }
    return names[names.length - 1];
  }

  /** 移動步驟：頭身走到 pose，delay＝移動時間＋停留 */
  function move(poseTarget, moveRange, holdS) {
    const moveMs = int(moveRange[0], moveRange[1]);
    return { target: poseTarget, moveMs, delayMs: moveMs + u(holdS[0], holdS[1]) * 1000 };
  }

  /** 只換眼神：頭身沿用 base（必須和上一份計畫的頭身完全相同，ambient 才不會重啟頭身曲線），moveMs 沿用上一個 */
  function look(base, gaze, holdS) {
    return { target: { ...base, gazeX: bound(gaze[0]), gazeY: bound(gaze[1]) }, moveMs: null, delayMs: u(holdS[0], holdS[1]) * 1000 };
  }

  /** 挑下一個閒置劇本：冷卻（用 nowMs 計）、同一劇本不連續三次、歪頭不連續、近期出現過的降權 */
  function chooseAction(now) {
    const names = Object.keys(IDLE_ACTIONS).filter(name => {
      const started = startedAt[name];
      return !((started !== undefined && now - started < IDLE_ACTIONS[name][1] * 1000)
        || (recent.length >= 2 && recent[recent.length - 1] === name && recent[recent.length - 2] === name)
        || (name === 'curious_tilt' && recent[recent.length - 1] === name));
    });
    const weights = names.map(name => IDLE_ACTIONS[name][0] * RECENT_DECAY ** recent.filter(r => r === name).length);
    const chosen = pickWeighted(names, weights);
    recent.push(chosen);
    if (recent.length > RECENT_ACTIONS) recent.shift();
    startedAt[chosen] = now;
    return chosen;
  }

  /** 思考的頭身姿勢（think_up 與思考情境共用）：頭微抬、往同側歪 */
  function thinkPose(side, gaze) {
    return pose([side * u(0.1, 0.25), u(0.25, 0.45), side * u(0.15, 0.35)], [u(-0.15, 0.15), u(0.05, 0.2), side * u(0.05, 0.15)], gaze);
  }

  /** 把一個閒置劇本展開成眼睛先動、最後回到看使用者的步驟 */
  function planAction(name) {
    const cur = current();
    if (name === 'rest_front') {
      const rest = settled();
      const steps = [move(rest, [1100, 2000], [0.5, 1.1])];
      for (let i = int(1, 3); i > 0; i--) steps.push(look(rest, saccade(), [0.6, 1.2]));
      return [...steps, look(rest, [0, 0], [1.1, 2.0])];
    }
    if (name === 'glance_aside') {
      const [gx, gy] = sampleGaze();
      const turned = pose([gx * u(0.25, 0.45), gy * u(0.2, 0.35), u(-0.15, 0.15) + gx * 0.12],
        [gx * u(0.1, 0.25), u(-0.1, 0.1), gx * u(0.05, 0.2)], [gx * 0.85, gy * 0.85]);
      const steps = [look(cur, [gx, gy], [0.22, 0.4]), move(turned, [800, 1400], [0.5, 1.4])];
      if (random() < 0.35) steps.push(look(turned, [gx * u(0.4, 0.7), gy * u(0.4, 0.7) + u(-0.2, 0.2)], [0.6, 1.2]));
      return [...steps, look(turned, [0, 0], [0.3, 0.6]), move(settled(), [1000, 1600], [0.6, 1.4])];
    }
    if (name === 'look_down') {
      const gaze = [u(-0.3, 0.3), u(-0.95, -0.7)];
      const down = pose([gaze[0] * 0.4 + u(-0.1, 0.1), u(-0.65, -0.4), u(-0.25, 0.25)],
        [u(-0.15, 0.15), u(-0.3, -0.1), u(-0.15, 0.15)], [gaze[0], gaze[1] * 0.8]);
      const steps = [look(cur, gaze, [0.2, 0.4]), move(down, [1200, 1800], [0.9, 1.9])];
      if (random() < 0.6) steps.push(look(down, [u(-0.45, 0.45), u(-0.9, -0.6)], [0.7, 1.3]));
      // 頭還低著，要和使用者對上眼就得先抬眼睛
      return [...steps, look(down, [0, u(0.35, 0.55)], [0.35, 0.6]), move(settled(), [1000, 1500], [0.6, 1.4])];
    }
    if (name === 'think_up') {
      const side = pickSide();
      const gaze = [side * u(0.45, 0.7), u(0.75, 0.95)];
      const up = thinkPose(side, [gaze[0] * 0.85, gaze[1] * 0.85]);
      const steps = [look(cur, gaze, [0.25, 0.45]), move(up, [1100, 1600], [1.0, 2.0])];
      if (random() < 0.5) steps.push(look(up, [-gaze[0] * u(0.3, 0.6), u(0.7, 0.95)], [0.6, 1.2]));
      return [...steps, look(up, [0, -u(0.2, 0.35)], [0.3, 0.55]), move(settled(), [1000, 1500], [0.6, 1.4])];
    }
    if (name === 'weight_shift') {
      const side = pickSide();
      // 軀幹傾斜，頭反向平衡並繼續面向使用者
      const shifted = pose([-side * u(0.08, 0.22), u(-0.12, 0.12), -side * u(0.1, 0.3)],
        [side * u(0.5, 0.85), u(-0.2, 0.2), side * u(0.45, 0.8)]);
      const steps = [move(shifted, [1400, 2200], [0.8, 1.8])];
      if (random() < 0.8) steps.push(look(shifted, saccade(), [0.7, 1.4]), look(shifted, [0, 0], [0.6, 1.2]));
      return steps;
    }
    // curious_tilt
    const side = pickSide();
    const tilted = pose([u(-0.2, 0.2), u(0.0, 0.2), side * u(0.45, 0.72)], [u(-0.15, 0.15), u(-0.1, 0.1), side * u(0.1, 0.3)]);
    // 從另一側歪過來時，單次歪斜變化量不超過上限
    tilted.headZ = bound(Math.max(cur.headZ - TILT_MAX_DELTA, Math.min(cur.headZ + TILT_MAX_DELTA, tilted.headZ)));
    return [move(tilted, [900, 1400], [0.9, 1.8]), look(tilted, saccade(), [0.5, 1.0]),
      look(tilted, [0, 0], [0.4, 0.8]), move(settled(), [1000, 1500], [0.5, 1.0])];
  }

  // ---------- 各情境的步驟 ----------

  /** 聆聽：頭身各軸＝目前目標 × 0.6 ＋ 雜訊，視線回正；第一次才加歪頭。停留 2.5～4.8 s 後再規劃一次 */
  function listenStep(withTilt) {
    const cur = current();
    /** @type {Target} */
    const next = { ...cur, gazeX: 0, gazeY: 0 };
    for (const axis of POSE_AXES) next[axis] = bound(cur[axis] * LISTEN.keep + u(-LISTEN.noise, LISTEN.noise));
    if (withTilt) next.headZ = bound(next.headZ + (random() < 0.5 ? -1 : 1) * range(LISTEN.tilt));
    const moveMs = int(LISTEN.moveMs[0], LISTEN.moveMs[1]);
    return { target: next, moveMs, delayMs: moveMs + range(LISTEN.holdS) * 1000 };
  }

  /** 打字：看輸入框（視窗下方），頭微低，其他軸保留目前目標 × 0.6 */
  function typingStep() {
    const cur = current();
    const next = { ...cur, gazeX: TYPING.gaze[0], gazeY: TYPING.gaze[1], headY: TYPING.headY };
    for (const axis of POSE_AXES) if (axis !== 'headY') next[axis] = cur[axis] * LISTEN.keep;
    return { target: next, moveMs: int(TYPING.moveMs[0], TYPING.moveMs[1]), delayMs: 0 };
  }

  /** 點擊：視線＝點擊位置（正規化），頭 X／Y 跟 30～45%，其餘保留 */
  function clickStep(dx, dy) {
    const gx = clamp(dx / CURSOR_PX, -1, 1);
    const gy = clamp(dy / CURSOR_PX, -1, 1);
    const ratio = range(CLICK.headRatio);
    return { target: { ...current(), headX: gx * ratio, headY: gy * ratio, gazeX: gx, gazeY: gy }, moveMs: int(CLICK.moveMs[0], CLICK.moveMs[1]), delayMs: 0 };
  }

  /** 思考：往上側看（左右隨機），頭微抬、往同側歪；之後每 1～2 s 在那一帶只換眼神 */
  function thinkingSteps() {
    const side = pickSide();
    const gaze = [side * u(0.45, 0.7), u(0.75, 0.95)];
    const up = thinkPose(side, gaze);
    const sign = () => (random() < 0.5 ? -1 : 1);
    refill = () => [look(current(), [gaze[0] + sign() * range(THINKING.jitter), gaze[1] + sign() * range(THINKING.jitter)], [1, 2])];
    return [{ target: up, moveMs: int(THINKING.moveMs[0], THINKING.moveMs[1]), delayMs: u(1000, 2000) }];
  }

  /** 回應：看使用者；Talk 開頭先移開視線一下再看回來。頭身反正被反應動作接管，只給 _settled() */
  function respondingSteps(motion) {
    const rest = settled();
    const moveMs = int(RESPONDING.moveMs[0], RESPONDING.moveMs[1]);
    if (motion !== MOTIONS.talk) return [{ target: rest, moveMs, delayMs: 0 }];
    const side = pickSide();
    const away = { ...rest, gazeX: bound(side * range(RESPONDING.awayX)), gazeY: bound(range(RESPONDING.awayY)) };
    return [{ target: away, moveMs, delayMs: range(RESPONDING.awayMs) },
      { target: { ...rest, gazeX: 0, gazeY: 0 }, moveMs: null, delayMs: 0 }];
  }

  /** 回到閒置：先看使用者（頭身 _settled()、視線回正），停留之後才跑劇本 */
  function idleReturnStep() {
    return move(settled(), IDLE_RETURN.moveMs, IDLE_RETURN.holdS);
  }

  /** 換情境：清掉舊的步驟佇列，排好新情境的步驟（第一步 nextAt＝now 就是同一次 update 送出） */
  function enter(next, now, motion) {
    const from = ctx;
    ctx = next;
    since = now;
    queue = [];
    refill = null;
    nextAt = Infinity;
    if (next !== 'cursor') cursorActive = false;
    if (next !== 'idle' && next !== 'cursor') { moves = []; stillSince = null; }
    if (next === 'peek') {
      target = zeroTarget();   // 程序層自己回中性；離開時從中性出發
    } else if (next === 'listening') {
      queue = [listenStep(true)];
      refill = () => [listenStep(false)];
      nextAt = now;
    } else if (next === 'typing') {
      queue = [typingStep()];
      nextAt = now;
    } else if (next === 'click' && click) {
      queue = [clickStep(click.dx, click.dy)];
      nextAt = now;
      clickDirty = false;
    } else if (next === 'thinking') {
      queue = thinkingSteps();
      nextAt = now + range(THINKING.startMs);
    } else if (next === 'responding') {
      queue = respondingSteps(motion);
      nextAt = now;
    } else if (next === 'cursor') {
      lastChaseAt = -Infinity;
      stillSince = null;
    } else if (next === 'idle') {
      refill = () => {
        const name = chooseAction(lastNow);
        action = { name, startedAt: lastNow, serial: ++actionSerial };
        return planAction(name);
      };
      if (from !== 'peek') queue = [idleReturnStep()];   // 離開 peek 時不另外看使用者：直接開劇本
      nextAt = now;
    }
  }

  /** 取出到期的下一步 */
  function stepQueue(now) {
    if (now < nextAt) return null;
    if (!queue.length && refill) queue = refill();
    if (!queue.length) { nextAt = Infinity; return null; }
    const step = /** @type {Step} */ (queue.shift());
    nextAt = now + step.delayMs;
    return emit(step.target, step.moveMs);
  }

  // ---------- 追蹤：回應、游標、點頭 ----------

  /** 回應：mode 變成 success／failure 時開始；動作回到 idle 後再 1～2 s 結束（動作再播就重算） */
  function trackResponding(now, poseName, mode, motion) {
    if (poseName === 'peek' || mode === 'listening' || mode === 'executing') {
      responding = null;
      return;
    }
    if (mode !== prevMode && (mode === 'success' || mode === 'failure')) responding = { sawReaction: false, endAt: null };
    if (!responding) return;
    const modeDone = mode !== 'success' && mode !== 'failure';
    if (motion !== MOTIONS.idle) {
      responding.sawReaction = true;
      responding.endAt = null;
    } else if (responding.endAt === null && (responding.sawReaction || modeDone)) {
      responding.endAt = now + range(RESPONDING.afterMs);
    }
    if (responding.endAt !== null && now >= responding.endAt) responding = null;
  }

  const movedSince = (from) => moves.reduce((sum, m) => (m.t >= from ? sum + m.d : sum), 0);

  /** 沒有更高優先的情境時：游標吸引中、失去興趣、或閒置 */
  function cursorOrIdle(now) {
    moves = moves.filter(m => m.t >= now - CURSOR.attractWindowMs);
    if (cursorActive) {
      if (movedSince(now - CURSOR.stillWindowMs) < CURSOR.stillPx) {
        if (stillSince === null) {
          stillSince = now;
          stillFor = range(CURSOR.stillMs);
        } else if (now - stillSince >= stillFor) {
          cursorActive = false;
          cooldownUntil = now + CURSOR.cooldownMs;
          moves = [];
          stillSince = null;
          return 'idle';
        }
      } else stillSince = null;
      return 'cursor';
    }
    if (cursorPos && now >= cooldownUntil && movedSince(now - CURSOR.attractWindowMs) >= CURSOR.attractPx) {
      cursorActive = true;
      cursorK = range(CURSOR.headRatio);
      return 'cursor';
    }
    return 'idle';
  }

  /** 追視：游標離目前注視點 ≥ 0.08 而且距離上次掃視 ≥ 150 ms 才再掃視一次（一連串小跳，不是黏著） */
  function chase(now) {
    if (!cursorPos || now - lastChaseAt < CURSOR.chaseGapMs) return null;
    const gx = clamp(cursorPos.dx / CURSOR_PX, -1, 1);
    const gy = clamp(cursorPos.dy / CURSOR_PX, -1, 1);
    const cur = current();
    if (Math.hypot(gx - cur.gazeX, gy - cur.gazeY) < CURSOR.chaseDistance) return null;
    lastChaseAt = now;
    return emit({ ...cur, headX: gx * cursorK, headY: gy * cursorK, gazeX: gx, gazeY: gy }, int(CURSOR.moveMs[0], CURSOR.moveMs[1]));
  }

  /** 點頭覆蓋層：立即下去、250 ms 後回原值；回傳這一刻要送的計畫（沒有就 null）。覆蓋結束時清掉 */
  function stepNod(now) {
    if (!nod) return null;
    if (nod.started && now >= nod.endAt) { nod = null; return null; }
    if (!nod.started) {
      nod.started = true;
      nod.at = now;
      nod.endAt = now + NOD.spanMs;
      nod.base = target.headY;
      return emit({ ...target, headY: target.headY - NOD.dip }, NOD.downMs);
    }
    if (!nod.returned && now >= nod.at + NOD.downMs) {
      nod.returned = true;
      return emit({ ...target, headY: nod.base }, NOD.upMs);
    }
    return null;
  }

  return {
    /** 游標相對頭部中心的 px（dx 右正、dy 上正）；只有閒置／游標情境才算「移動」 */
    cursor(dx, dy, nowMs) {
      if (![dx, dy, nowMs].every(Number.isFinite)) return;
      const prev = cursorPos;
      cursorPos = { dx, dy };
      if (!prev || (ctx !== 'idle' && ctx !== 'cursor') || nowMs < cooldownUntil) return;
      const d = Math.hypot(dx - prev.dx, dy - prev.dy);
      if (d > 0) moves.push({ t: nowMs, d });
    },
    /** 點到角色的位置（px，座標同 cursor）；0.8～1.2 s 的點擊情境 */
    click(dx, dy, nowMs) {
      if (![dx, dy, nowMs].every(Number.isFinite)) return;
      click = { at: nowMs, durMs: range(CLICK.holdMs), dx, dy };
      clickDirty = true;
    },
    /** 輸入框裡打了一個字 */
    typing(nowMs) {
      if (Number.isFinite(nowMs)) typingAt = nowMs;
    },
    /** 'command' 時排「輕點一下頭」；其他狀態不處理。呼叫端要先讓 controller 處理同一則 heard，再呼叫這裡 */
    heard(status, nowMs) {
      if (status === 'command' && Number.isFinite(nowMs)) nod = { at: nowMs, started: false, returned: false, base: 0, endAt: 0 };
    },
    /** 每幀呼叫一次：決定情境，需要的話回傳這一刻要交給 procedural.plan 的新計畫（一次最多一份） */
    update(nowMs, { pose: poseName, mode, motion, inputOpen } = /** @type {Inputs} */ ({})) {
      if (!Number.isFinite(nowMs)) return { context: ctx ?? 'peek', plan: null, swayGain: ctx === 'listening' ? LISTEN_SWAY_GAIN : 1 };
      const now = nowMs;
      lastNow = now;
      trackResponding(now, poseName, mode, motion);
      let want;
      if (poseName === 'peek') want = 'peek';
      else if (mode === 'listening') want = 'listening';
      else if (inputOpen && typingAt !== null && now - typingAt >= 0 && now - typingAt < TYPING.ms) want = 'typing';
      else if (click && now - click.at >= 0 && now - click.at < click.durMs) want = 'click';
      else if (mode === 'executing') want = 'thinking';
      else if (responding) want = 'responding';
      else want = cursorOrIdle(now);
      prevMode = mode ?? prevMode;

      // peek 與 listening 立即生效並取消點頭覆蓋
      if (want === 'peek' || want === 'listening') {
        if (nod && nod.started && !nod.returned) target = { ...target, headY: nod.base };
        nod = null;
      }
      if (want !== ctx || (want === 'click' && clickDirty)) enter(want, now, motion);

      let plan = stepNod(now);
      if (!plan && !nod) plan = ctx === 'cursor' ? chase(now) : stepQueue(now);   // 覆蓋期間其他情境的計畫延後
      return { context: /** @type {string} */ (ctx), plan, swayGain: ctx === 'listening' ? LISTEN_SWAY_GAIN : 1 };
    },
    /** 除錯用：目前情境、從何時開始、最後送出的目標；action＝最近一次選到的閒置劇本（serial 每選一次加一） */
    snapshot() {
      return { context: ctx ?? 'peek', since, target: { ...target }, action: action && { ...action } };
    },
  };
}
