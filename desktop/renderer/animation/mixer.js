// 參數混合器：每個受控參數一個臨界阻尼彈簧，換「主人」（待機曲線／反應動作）時保留目前位置與速度，並限制自主動作的角度上限。
// 移植自 AI_VT_V2 的 ParameterMixer.ts；差異：不碰模型物件（呼叫端給 channels、拿回傳值自己寫）、用語意鍵名而非 Param 名稱。
// 純邏輯：不碰 DOM／PIXI，時間（dtS）由呼叫端注入。

/** 自主動作的角度上限（度；eyeX／eyeY 是正規化值）：目標只會落在 neutral ± LIMIT 內，再夾回模型的原始範圍（參考專案實測的手感） */
export const LIMITS = Object.freeze({ angleX: 35, angleY: 24, angleZ: 23, bodyX: 8, bodyY: 7, bodyZ: 9, eyeX: 0.85, eyeY: 1 });

/** 彈簧自然頻率（rad/s）：頭身 18 慢而柔，眼球 120 幾乎瞬間跟上（掃視要快）；eyeX／eyeY 用 eye，其餘 pose */
export const OMEGA = Object.freeze({ pose: 18, eye: 120 });

const EYE_KEYS = new Set(['eyeX', 'eyeY']);
const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));

/**
 * @param {Record<string, { neutral: number, min: number, max: number }>} channels 模型的預設值與原始範圍；不在 LIMITS 的鍵忽略
 */
export function createMixer(channels) {
  /** @type {Map<string, { neutral: number, low: number, high: number, rawLow: number, rawHigh: number, omega: number, x: number, v: number }>} */
  const state = new Map();
  for (const [key, limit] of Object.entries(LIMITS)) {
    const channel = channels?.[key];
    if (!channel) continue;
    const { neutral, min, max } = channel;
    if (![neutral, min, max].every(Number.isFinite)) continue;
    state.set(key, {
      neutral, rawLow: min, rawHigh: max,
      low: Math.max(min, neutral - limit), high: Math.min(max, neutral + limit),
      omega: EYE_KEYS.has(key) ? OMEGA.eye : OMEGA.pose, x: neutral, v: 0,
    });
  }

  return {
    /** 這個鍵是否受控 */
    has: (key) => state.has(key),

    /** 正規化 q（-1～1）換成參數值：正側走 neutral→high、負側走 neutral→low，各自用自己的行程；未知鍵回 0 */
    normalized(key, q) {
      const c = state.get(key);
      if (!c) return 0;
      const n = Number.isFinite(q) ? clamp(q, -1, 1) : 0;
      return c.neutral + n * (n >= 0 ? c.high - c.neutral : c.neutral - c.low);
    },

    /** 往 target 走一步並回傳新位置；autonomous 夾角度上限、否則只夾原始範圍；immediate 直接到位；未知鍵或非有限 target 回 0 且不改狀態 */
    write(key, target, dtS, { autonomous = true, immediate = false } = {}) {
      const c = state.get(key);
      if (!c || !Number.isFinite(target)) return 0;
      const goal = clamp(target, autonomous ? c.low : c.rawLow, autonomous ? c.high : c.rawHigh);
      if (immediate) {
        c.x = goal;
        c.v = 0;
        return c.x;
      }
      const dt = Number.isFinite(dtS) && dtS > 0 ? dtS : 0;
      // 臨界阻尼彈簧的解析解：切成幾幀走結果都一樣
      const delta = c.x - goal;
      const j = c.v + c.omega * delta;
      const decay = Math.exp(-c.omega * dt);
      c.x = goal + (delta + j * dt) * decay;
      c.v = (c.v - c.omega * j * dt) * decay;
      const bounded = clamp(c.x, c.rawLow, c.rawHigh);
      if (bounded !== c.x) c.v = 0;
      c.x = bounded;
      return c.x;
    },

    value: (key) => state.get(key)?.x ?? 0,
    velocity: (key) => state.get(key)?.v ?? 0,

    /** 直接設位置、速度歸零（初始化用） */
    set(key, x) {
      const c = state.get(key);
      if (!c || !Number.isFinite(x)) return;
      c.x = x;
      c.v = 0;
    },
  };
}
