// 幀率閘門（計畫書 §6.2）：出場、進退場或有動作時逐 vsync（60 fps）畫；潛伏時畫到頭髮物理收尾就停格（0 fps）。
// 純邏輯、時間由外部注入，不碰 DOM／WebGL。
export const TAIL_MS = 700;   // 有事件之後至少再畫這麼久（涵蓋表情與手勢 0.2～0.5 秒的淡入淡出）

/**
 * eps：物理輸出每步最大變化量低於這個值算「靜止」；holdMs：連續靜止這麼久就停格；
 * maxMs：潛伏後不論是否靜止，最多畫這麼久就停格（避免物理一直有細微擺動而永遠不停）。
 */
export function createFrameGate({ eps, holdMs, maxMs, tailMs = TAIL_MS }) {
  let busyUntil = 0, quietSince = null, peekSince = null, frozen = false;
  return {
    /** 有 effect、後端事件或使用者操作：解除停格，至少再畫 tailMs，收尾重新計算 */
    wake(now) {
      frozen = false; quietSince = null; peekSince = null;
      busyUntil = Math.max(busyUntil, now + tailMs);
    },
    /**
     * 每畫完一幀呼叫。out＝出場中；moving＝進退場中或正在播 Idle 以外的動作；
     * delta＝這幀物理輸出的最大變化量（null＝這幀沒跑物理步長，不影響判斷）。回傳 true＝下一個 vsync 繼續畫
     */
    afterFrame(now, { out, moving, delta }) {
      if (out || moving || now < busyUntil) { quietSince = null; peekSince = null; return true; }
      if (peekSince === null) peekSince = now;
      if (delta !== null) quietSince = delta < eps ? (quietSince ?? now) : null;
      frozen = (quietSince !== null && now - quietSince >= holdMs) || now - peekSince >= maxMs;
      return !frozen;
    },
    get frozen() { return frozen; },
  };
}
