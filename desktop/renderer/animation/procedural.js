// 程序層：把 ambient（頭身視線）、face（眨眼與眉眼事件）、mixer（彈簧）接到參數陣列，依計劃書 §4.3 決定每個參數聽誰的。
// 只吃 Float32Array 與「語意鍵 → 參數索引」，不知道模型的參數 ID；索引 -1＝模型沒有這個參數，略過。
// 純邏輯：時間（毫秒 nowMs、秒 dtS）與亂數 seed 由呼叫端注入，不碰 DOM／PIXI。
import { createAmbient, createRandom } from './ambient.js';
import { createFace } from './face.js';
import { createMixer } from './mixer.js';

/** 由 mixer 接手的頭身軸：參數鍵 → ambient 的軸名 */
const POSE_KEYS = Object.freeze({ angleX: 'headX', angleY: 'headY', angleZ: 'headZ', bodyX: 'bodyX', bodyY: 'bodyY', bodyZ: 'bodyZ' });
/** 眼球：參數鍵 → ambient 的軸名 */
const GAZE_KEYS = Object.freeze({ eyeX: 'gazeX', eyeY: 'gazeY' });
/** 眉毛的位置參數：Idle 與潛伏時固定回預設值，反應動作才用動作的值 */
const BROW_POSITION_KEYS = Object.freeze(['browLY', 'browRY', 'browLX', 'browRX']);
/** 眉毛的形狀參數：預設值＋face 的眉毛事件（key → face 輸出的欄位） */
const BROW_SHAPE_KEYS = Object.freeze({ browLForm: 'browLForm', browRForm: 'browRForm', browLAngle: 'browLAngle', browRAngle: 'browRAngle' });
const BLINK_SEGMENT_THRESHOLD = 0.5;   // 動作的眼皮曲線端點低於這個值，視為這個動作自己會眨眼（計劃書 §4.3）

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const seedFrom = (random) => Math.floor(random() * 4294967296) >>> 0;

/**
 * 這個動作自己會不會眨眼：motion3.json 裡眼皮曲線（id＝rig.params.eyeOpen，左眼開合）的端點最小值 < 0.5。
 * 只看端點（起點與每段的終點）；貝茲的控制點只影響形狀，不算（控制點低於 0.5 但端點都不低於就是 false）。
 * Segments 格式：先是起點 (t, v)，之後每段 = 類型 ＋ 點：0 線性、2 stepped、3 inverse stepped 各 1 個點，1 貝茲 3 個點。
 * @param {{ Curves?: { Id: string, Segments?: number[] }[] }} json
 * @param {string} id 曲線的參數 ID（模型裡的名稱）
 * @returns {boolean}
 */
export function motionBlinks(json, id) {
  const segments = json?.Curves?.find(curve => curve.Id === id)?.Segments;
  if (!Array.isArray(segments) || segments.length < 2) return false;
  let minimum = segments[1];
  let i = 2;
  while (i < segments.length) {
    const points = segments[i] === 1 ? 3 : (segments[i] === 0 || segments[i] === 2 || segments[i] === 3) ? 1 : 0;
    if (points === 0) break;   // 不認得的類型：後面無法對齊，已讀到的端點照算
    const end = segments[i + points * 2];
    if (typeof end === 'number') minimum = Math.min(minimum, end);
    i += 1 + points * 2;
  }
  return minimum < BLINK_SEGMENT_THRESHOLD;
}

/**
 * index：rig.params 鍵 → 參數索引（-1＝模型沒有）；seed：同一個 seed 逐幀相同，ambient、face 各自由它推導獨立的亂數流；
 * blinkMotions：自己會眨眼的語意動作名（程序眨眼在這些動作播放時暫停）。
 * @param {{ index: Record<string, number>, defaults: Float32Array, minimums: Float32Array, maximums: Float32Array,
 *           seed: number, blinkMotions: Set<string> }} options
 */
export function createProcedural({ index, defaults, minimums, maximums, seed, blinkMotions }) {
  const slot = (key) => (Number.isInteger(index[key]) && index[key] >= 0 ? index[key] : -1);
  const channels = {};
  for (const key of [...Object.keys(POSE_KEYS), ...Object.keys(GAZE_KEYS)]) {
    const i = slot(key);
    if (i >= 0) channels[key] = { neutral: defaults[i], min: minimums[i], max: maximums[i] };
  }
  const mixer = createMixer(channels);
  const derive = createRandom(seed);
  const ambient = createAmbient(seedFrom(derive));
  const face = createFace(createRandom(seedFrom(derive)));
  let started = false;
  let lastAmbient = null;
  let lastFace = null;

  /** 直接寫值（夾在模型範圍內）；模型沒有這個參數就略過 */
  function put(values, key, value) {
    const i = slot(key);
    if (i >= 0) values[i] = clamp(value, minimums[i], maximums[i]);
  }

  return {
    /** 交給 ambient 一份新計畫；成功時用新舊目標的視線距離通知 face（大幅轉移視線順便眨眼） */
    plan(plan, nowMs) {
      const before = ambient.target();
      if (!ambient.apply(plan, nowMs)) return false;
      const after = ambient.target();
      face.gazeShift(Math.hypot(after.gazeX - before.gazeX, after.gazeY - before.gazeY), nowMs);
      return true;
    },

    /**
     * 每個計算幀在動作佇列更新之後呼叫，就地改 values。
     * @param {Float32Array} values 參數陣列（此時已有這一幀 Idle／反應動作的值）
     * @param {number} dtS
     * @param {number} nowMs 虛擬時鐘（毫秒）
     * @param {{ pose: 'peek' | 'out', motion: string, listening?: boolean, swayGain?: number }} state motion＝目前播放的語意動作名
     */
    apply(values, dtS, nowMs, { pose, motion, listening = false, swayGain = 1 }) {
      const out = pose === 'out';
      const reaction = motion !== 'idle';
      const breathIndex = slot('breath');
      // 第一幀不累加漂移時間（呼叫端的第一個 dt 可能含載入時間）
      const sampled = ambient.update(started ? dtS : 0, nowMs, { enabled: out, swayGain, breath: breathIndex >= 0 ? values[breathIndex] : 0.5 });
      started = true;
      const eyes = face.update(dtS, nowMs, {
        enabled: out, listening,
        blinkAllowed: !(reaction && blinkMotions.has(motion)), eyesAllowed: !reaction, browsAllowed: !reaction,
      });
      lastAmbient = sampled;
      lastFace = eyes;

      // 頭身：潛伏回中性；Idle 聽程序；反應動作的目標改成動作的值（不受角度上限），位置與速度留在 mixer，接手與交還都不跳
      for (const [key, axis] of Object.entries(POSE_KEYS)) {
        const i = slot(key);
        if (i < 0 || !mixer.has(key)) continue;
        if (!out) values[i] = mixer.write(key, mixer.normalized(key, 0), dtS);
        else if (reaction) values[i] = mixer.write(key, values[i], dtS, { autonomous: false });
        else values[i] = mixer.write(key, mixer.normalized(key, sampled[axis]), dtS);
      }

      // 眼球：反應動作沒有這兩條曲線，一律由程序
      for (const [key, axis] of Object.entries(GAZE_KEYS)) {
        const i = slot(key);
        if (i < 0 || !mixer.has(key)) continue;
        values[i] = mixer.write(key, mixer.normalized(key, out ? sampled[axis] : 0), dtS);
      }

      // 眼皮：潛伏 1；Idle 不用動作曲線；反應動作乘上程序眨眼（動作自己會眨眼時 blink 已是 1）
      for (const [key, event] of /** @type {[string, number][]} */ ([['eyeOpen', eyes.eyeL], ['eyeROpen', eyes.eyeR]])) {
        const i = slot(key);
        if (i < 0) continue;
        if (!out) put(values, key, 1);
        else if (reaction) put(values, key, values[i] * eyes.blink);
        else put(values, key, eyes.blink * event);
      }

      // 眉毛位置：反應動作以外回預設值；形狀：預設值＋眉毛事件（潛伏時事件已是 0）
      if (!reaction || !out) for (const key of BROW_POSITION_KEYS) { const i = slot(key); if (i >= 0) put(values, key, defaults[i]); }
      for (const [key, field] of Object.entries(BROW_SHAPE_KEYS)) { const i = slot(key); if (i >= 0) put(values, key, defaults[i] + (out ? eyes[field] : 0)); }
    },

    /** 上一次的取樣（除錯用） */
    debug: () => ({ ambient: lastAmbient, face: lastFace }),
  };
}
