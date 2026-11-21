// 臉部程序：眨眼與眼皮／眉毛事件。移植自 AI_VT_V2 的 BlinkRuntime.ts／FaceIdleRuntime.ts，差異：
//   去掉 wink（不抽 winkLeft／winkRight）；眉毛被禁止時改成 400 ms 淡出（參考是直接歸零會跳）；
//   眼皮基準固定為 1（表情基準由 procedural 合成）；亂數與時間全部由呼叫端注入，不用 Math.random。
// 只輸出數值，不碰模型參數，不依賴 DOM／PIXI。

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const ease = (value) => value * value * (3 - 2 * value);

/** 眉毛事件的強度（正為上揚、負為下壓）換成 BrowForm／BrowAngle 的增益；參考在舊 rig 上調的，換 fullrig03 要目視確認 */
export const BROW_FORM_GAIN = 6.5;
export const BROW_ANGLE_GAIN = 6;
/** 眨眼間隔（秒）：閒置沿用參考 1～4；聆聽 3～6（平均 1.8 倍，L5 要求 ≥ 1.3 倍，留餘裕；手感） */
export const BLINK_INTERVAL = Object.freeze({ idle: [1, 4], listening: [3, 6] });

const DOUBLE_CHANCE = 0.075;           // 連眨兩下的機率（參考）
const DOUBLE_COOLDOWN_MS = 8000;       // 連眨之後多久內不再連眨（參考）
const MAX_FRAME_GAP_MS = 1000;         // 相鄰兩幀超過這個間隔（或時間倒退）視為中斷，重新排程（參考）
/** @type {[number, number]} */
const EVENT_GAP_MS = [3000, 7000];     // 眼皮、眉毛事件之間的間隔（參考）
const BROW_FADE_MS = 400;              // 眉毛被禁止時，從當下權重淡出到 0 的時間（新增）
const GAZE_BLINK_DISTANCE = 0.5;       // 視線轉移距離 ≥ 這個值才可能順便眨眼（手感）
const GAZE_BLINK_CHANCE = 0.6;         // 大幅轉移視線時順便眨眼的機率（手感）

/**
 * 產生帶 seed 的亂數（mulberry32），回傳 () => [0, 1)。ambient.js 有同樣的實作，這裡不 import 以免兩邊綁在一起。
 * @param {number} seed
 * @returns {() => number}
 */
function mulberry(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * @param {() => number} random
 * @returns {(minimum: number, maximum: number) => number}
 */
function makeBetween(random) {
  if (typeof random !== 'function') throw new TypeError('random 必須是函式（亂數要由呼叫端注入）');
  return (minimum, maximum) => {
    const value = random();
    return minimum + (Number.isFinite(value) ? clamp(value, 0, 1) : 0.5) * (maximum - minimum);
  };
}

/**
 * 眨眼：只產生眼睛開合係數（1 睜開、0 閉上）。
 * @param {() => number} random
 */
export function createBlink(random) {
  const between = makeBetween(random);
  let intervalMin = BLINK_INTERVAL.idle[0];
  let intervalMax = BLINK_INTERVAL.idle[1];
  let nowMs = 0;
  let hasUpdated = false;
  let wasAllowed = true;
  let wasAutomaticEnabled = true;
  let paused = false;
  let pauseUntilMs = null;
  let nextBlinkAtMs = Infinity;
  let secondAtMs = null;
  let doubleCooldownUntilMs = -Infinity;
  /** @type {{ startedAtMs: number, closingMs: number, closedMs: number, openingMs: number, kind: 'auto' | 'second' | 'forced', double: boolean, pauseAfterMs: number } | null} */
  let event = null;
  let pendingForcePauseMs = null;

  function createEvent(startedAtMs, kind, pauseAfterMs = 0) {
    return {
      startedAtMs,
      closingMs: between(70, 120),
      closedMs: between(20, 60),
      openingMs: between(120, 200),
      kind,
      double: kind === 'auto' && startedAtMs >= doubleCooldownUntilMs && between(0, 1) < DOUBLE_CHANCE,
      pauseAfterMs,
    };
  }
  function schedule(fromMs) { nextBlinkAtMs = fromMs + between(intervalMin, intervalMax) * 1000; }
  function clearEvent() {
    event = null;
    secondAtMs = null;
    pendingForcePauseMs = null;
    nextBlinkAtMs = Infinity;
  }

  return {
    /** 暫停自動眨眼；durationS > 0 時到時自動恢復 */
    pause(durationS = 0) {
      clearEvent();
      paused = true;
      pauseUntilMs = Number.isFinite(durationS) && durationS > 0 ? nowMs + durationS * 1000 : null;
    },
    resume() {
      clearEvent();
      paused = false;
      pauseUntilMs = null;
      schedule(nowMs);
    },
    /** 下一幀強制眨一次；pauseAfterS > 0 時眨完後暫停那麼久。動作自己接管眼皮（allowed=false）時不排隊 */
    forceBlink(pauseAfterS = 0) {
      if (!wasAllowed) return;
      pendingForcePauseMs = Number.isFinite(pauseAfterS) ? Math.max(0, pauseAfterS) * 1000 : 0;
    },
    /** 改眨眼間隔（秒）；沒有事件進行中就從目前時間重排 */
    setIntervalRange(minInterval, maxInterval) {
      if (!Number.isFinite(minInterval) || !Number.isFinite(maxInterval) || minInterval < 0 || maxInterval < minInterval) return;
      intervalMin = minInterval;
      intervalMax = maxInterval;
      if (!event && secondAtMs === null) schedule(nowMs);
    },
    getIntervalRange() { return { min: intervalMin, max: intervalMax }; },
    isPaused() { return paused; },
    /** 連眨間隙與尚未取樣的強制眨眼也算占用眼皮 */
    isIdle() { return event === null && secondAtMs === null && pendingForcePauseMs === null; },
    /**
     * @param {number} dtS
     * @param {number} nowMsIn 虛擬時鐘（毫秒）
     * @param {boolean} [allowed] false＝動作自己控制眼皮，回 1 並清掉事件
     * @param {boolean} [automatic] false＝不自動眨（明確的強制眨眼仍會執行）
     * @returns {number} 0～1
     */
    update(dtS, nowMsIn, allowed = true, automatic = true) {
      const dtMs = Number.isFinite(dtS) ? Math.max(0, dtS) * 1000 : 0;
      const now = Number.isFinite(nowMsIn) ? nowMsIn : nowMs + dtMs;
      const interrupted = hasUpdated && (now < nowMs || now - nowMs > MAX_FRAME_GAP_MS || dtMs > MAX_FRAME_GAP_MS);
      const first = !hasUpdated;
      if (pauseUntilMs !== null && (first || now < nowMs)) pauseUntilMs += first ? now : now - nowMs;
      nowMs = now;
      hasUpdated = true;

      if (paused && pauseUntilMs !== null && now >= pauseUntilMs) {
        paused = false;
        pauseUntilMs = null;
        schedule(now);
      }

      if (!allowed) {
        clearEvent();
        wasAllowed = false;
        wasAutomaticEnabled = automatic;
        return 1;
      }
      if (interrupted || !wasAllowed) {
        clearEvent();
        schedule(now);
        wasAllowed = true;
        wasAutomaticEnabled = automatic;
        return 1;
      }
      wasAllowed = true;
      if (first) schedule(now);

      // 自動效果關閉不阻擋明確的單眨指令，但不保留自動雙眨
      if (!automatic) {
        if (event?.kind !== 'forced') event = null;
        secondAtMs = null;
        nextBlinkAtMs = Infinity;
      } else if (!wasAutomaticEnabled) schedule(now);
      wasAutomaticEnabled = automatic;

      if (pendingForcePauseMs !== null) {
        const pauseAfterMs = pendingForcePauseMs;
        clearEvent();
        event = createEvent(now, 'forced', pauseAfterMs);
      }

      // 強制單眨可在暫停中完成，完成後仍保留原暫停狀態
      if (paused && event?.kind !== 'forced') return 1;

      // 跨越階段邊界時保留實際結束時間，眨眼節奏才不會依幀率漂移
      for (let transitions = 0; transitions < 8; transitions += 1) {
        if (!event) {
          if (secondAtMs !== null) {
            if (now < secondAtMs) return 1;
            event = createEvent(secondAtMs, 'second');
            secondAtMs = null;
          } else {
            if (paused || !automatic || now < nextBlinkAtMs) return 1;
            event = createEvent(nextBlinkAtMs, 'auto');
          }
        }

        const current = event;
        const elapsed = Math.max(0, now - current.startedAtMs);
        if (elapsed < current.closingMs) return 1 - ease(elapsed / current.closingMs);
        if (elapsed < current.closingMs + current.closedMs) return 0;
        const openingElapsed = elapsed - current.closingMs - current.closedMs;
        if (openingElapsed < current.openingMs) return ease(openingElapsed / current.openingMs);

        const endedAt = current.startedAtMs + current.closingMs + current.closedMs + current.openingMs;
        event = null;
        if (current.kind === 'forced' && current.pauseAfterMs > 0) {
          paused = true;
          pauseUntilMs = endedAt + current.pauseAfterMs;
          nextBlinkAtMs = Infinity;
          return 1;
        }
        if (current.double) secondAtMs = endedAt + between(100, 180);
        else {
          if (current.kind === 'second') doubleCooldownUntilMs = endedAt + DOUBLE_COOLDOWN_MS;
          if (automatic) schedule(endedAt);
          else nextBlinkAtMs = Infinity;
        }
      }
      // 零間隔搭配極低更新頻率也不補播無限個過期事件
      clearEvent();
      schedule(now);
      return 1;
    },
  };
}

/**
 * @typedef {'still' | 'relax' | 'half' | 'deep' | 'asymmetric' | 'brow'} FaceIdleKind
 * @typedef {{ eyeL: number, eyeR: number, browLForm: number, browRForm: number, browLAngle: number, browRAngle: number, kind: FaceIdleKind }} FaceIdleSample
 * @typedef {{ enabled?: boolean, eyesAllowed?: boolean, browsAllowed?: boolean, listening?: boolean, blinkPaused?: boolean }} FaceIdleContext
 */

/** @returns {FaceIdleSample} */
const neutral = () => ({ eyeL: 1, eyeR: 1, browLForm: 0, browRForm: 0, browLAngle: 0, browRAngle: 0, kind: 'still' });

/** 事件權重：閉合段 0→1，持住 1，張開段 1→0（smoothstep） */
function eventWeight(event, nowMs) {
  const elapsed = Math.max(0, nowMs - event.startedAtMs);
  if (elapsed < event.closingMs) return ease(elapsed / event.closingMs);
  if (elapsed < event.closingMs + event.holdMs) return 1;
  return 1 - ease(clamp((elapsed - event.closingMs - event.holdMs) / event.openingMs, 0, 1));
}

/**
 * 眼皮與眉毛的自主事件：同時只持有一個事件，回傳相對表情基準的乘數／偏移，不累加模型參數。
 * 抽到參考的 wink 那一段（≥ 0.92）改成 relax，亂數消耗順序和參考在 wink 不可用時相同。
 * @param {() => number} random
 */
export function createFaceIdle(random) {
  const between = makeBetween(random);
  let nowMs = 0;
  let hasUpdated = false;
  let wasEnabled = false;
  let nextEventAtMs = Infinity;
  let eyesResumeAtMs = -Infinity;
  /** @type {{ kind: FaceIdleKind, startedAtMs: number, closingMs: number, holdMs: number, openingMs: number, eyeL: number, eyeR: number, browL: number, browR: number, eyes: boolean, brows: boolean } | null} */
  let event = null;
  /** 眉毛被禁止時的淡出：保存當下的眉毛值（已乘權重）與起點，BROW_FADE_MS 內 smoothstep 回 0 */
  let fade = null;

  function createEvent(startedAtMs, context, eyesAllowed) {
    const pick = between(0, 1);
    /** @type {FaceIdleKind} */
    let kind = pick < 0.12 ? 'still' : pick < 0.42 ? 'relax' : pick < 0.62 ? 'half'
      : pick < 0.7 ? 'deep' : pick < 0.82 ? 'asymmetric' : pick < 0.92 ? 'brow' : 'relax';
    if (kind === 'still') return null;

    // 聆聽中只加淺放鬆
    const shallow = !!context.listening;
    if (shallow && (kind === 'half' || kind === 'deep' || kind === 'asymmetric')) kind = 'relax';

    let eyeL = 1;
    let eyeR = 1;
    if (kind === 'relax') eyeL = eyeR = between(shallow ? 0.90 : 0.84, shallow ? 0.96 : 0.94);
    else if (kind === 'half') eyeL = eyeR = between(0.78, 0.88);
    else if (kind === 'deep') eyeL = eyeR = between(0.64, 0.75);
    else if (kind === 'asymmetric') {
      const relaxed = between(0.74, 0.88);
      const open = between(0.92, 1);
      if (between(0, 1) < 0.5) { eyeL = relaxed; eyeR = open; }
      else { eyeL = open; eyeR = relaxed; }
    }

    const created = {
      kind, startedAtMs,
      closingMs: between(700, 1400),
      holdMs: between(600, kind === 'deep' ? 1200 : 2000),
      openingMs: between(800, 1600),
      eyeL, eyeR, browL: 0, browR: 0,
      eyes: eyesAllowed && kind !== 'brow',
      brows: false,
    };
    // 淡出中不排新的眉毛事件（否則兩個眉毛值會疊加）
    if (context.browsAllowed !== false && !fade && (kind === 'brow' || between(0, 1) < 0.35)) {
      const amount = between(0.06, 0.14);
      const delta = between(0, 1) < 0.5 ? -amount : amount;
      const side = between(0, 1);
      if (side < 0.8) created.browL = delta;
      if (side >= 0.2) created.browR = delta;
      created.brows = true;
    }
    if (!created.eyes && !created.brows) return null;
    return created;
  }

  /** 把進行中的眉毛事件轉成淡出 */
  function startBrowFade(now) {
    const weight = eventWeight(event, now);
    fade = { startedAtMs: now, browL: event.browL * weight, browR: event.browR * weight };
    event.brows = false;
  }

  function cancelEyes(nowIn) {
    const now = Number.isFinite(nowIn) ? nowIn : nowMs;
    // 明確眨眼／暫停後，至少等一個完整新間隔才再排自主眼皮
    eyesResumeAtMs = now + between(...EVENT_GAP_MS);
    if (event) {
      event.eyes = false;
      if (!event.brows) event = null;
    }
    if (!event) nextEventAtMs = eyesResumeAtMs;
  }

  return {
    reset() {
      nowMs = 0;
      hasUpdated = false;
      wasEnabled = false;
      nextEventAtMs = Infinity;
      eyesResumeAtMs = -Infinity;
      event = null;
      fade = null;
    },
    /** 眼皮改由別人接管（明確眨眼、動作）時呼叫：取消事件裡的眼皮部分，並延後下一次 */
    cancelEyes,
    /**
     * @param {number} dtS
     * @param {number} nowMsIn
     * @param {FaceIdleContext} [context]
     * @returns {FaceIdleSample}
     */
    update(dtS, nowMsIn, context = {}) {
      const enabled = context.enabled !== false;
      const eyesAllowedIn = context.eyesAllowed !== false;
      const browsAllowed = context.browsAllowed !== false;
      const listening = !!context.listening;
      const dtMs = Number.isFinite(dtS) ? Math.max(0, dtS) * 1000 : 0;
      const now = Number.isFinite(nowMsIn) ? nowMsIn : nowMs + dtMs;
      const interrupted = hasUpdated && (now < nowMs || now - nowMs > MAX_FRAME_GAP_MS || dtMs > MAX_FRAME_GAP_MS);
      if (now < nowMs) {
        eyesResumeAtMs += now - nowMs;
        if (fade) fade.startedAtMs += now - nowMs;
      }
      nowMs = now;
      hasUpdated = true;

      if (!enabled) {
        event = null;
        fade = null;
        nextEventAtMs = Infinity;
        wasEnabled = false;
        return neutral();
      }
      if (!wasEnabled || interrupted) {
        event = null;
        fade = null;
        nextEventAtMs = now + between(...EVENT_GAP_MS);
        wasEnabled = true;
        return neutral();
      }

      const eyesAllowed = eyesAllowedIn && !context.blinkPaused;
      if (event) {
        if (event.eyes && !eyesAllowed) cancelEyes(now);
        if (event && event.brows && !browsAllowed) startBrowFade(now);
        if (event && !event.eyes && !event.brows) {
          event = null;
          nextEventAtMs = now + between(...EVENT_GAP_MS);
        }
      }

      if (event) {
        const endedAt = event.startedAtMs + event.closingMs + event.holdMs + event.openingMs;
        if (now >= endedAt) {
          event = null;
          nextEventAtMs = endedAt + between(...EVENT_GAP_MS);
        }
      }
      if (event?.eyes && listening && (event.kind === 'half' || event.kind === 'deep' || event.kind === 'asymmetric')) {
        // 切進聆聽：從當下值收尾一次，避免瞬間睜開或每幀重新開始回復
        const weight = eventWeight(event, now);
        event.eyeL = 1 + (event.eyeL - 1) * weight;
        event.eyeR = 1 + (event.eyeR - 1) * weight;
        event.browL *= weight;
        event.browR *= weight;
        event.startedAtMs = now;
        event.closingMs = 0;
        event.holdMs = 0;
        event.openingMs = 500;
        event.kind = 'relax';
      }
      if (!event && now >= nextEventAtMs) {
        const startedAt = nextEventAtMs;
        event = createEvent(startedAt, { listening, browsAllowed }, eyesAllowed && now >= eyesResumeAtMs);
        if (!event) nextEventAtMs = startedAt + between(...EVENT_GAP_MS);
      }

      let browL = 0;
      let browR = 0;
      if (event?.brows) {
        const weight = eventWeight(event, now);
        browL = event.browL * weight;
        browR = event.browR * weight;
      }
      if (fade) {
        const remain = 1 - ease(clamp((now - fade.startedAtMs) / BROW_FADE_MS, 0, 1));
        if (now - fade.startedAtMs >= BROW_FADE_MS) fade = null;
        else { browL += fade.browL * remain; browR += fade.browR * remain; }
      }
      const weight = event ? eventWeight(event, now) : 0;
      return {
        eyeL: event?.eyes ? 1 + (event.eyeL - 1) * weight : 1,
        eyeR: event?.eyes ? 1 + (event.eyeR - 1) * weight : 1,
        browLForm: browL * BROW_FORM_GAIN,
        browRForm: browR * BROW_FORM_GAIN,
        browLAngle: -browL * BROW_ANGLE_GAIN,
        browRAngle: browR * BROW_ANGLE_GAIN,
        kind: event?.eyes ? event.kind : (event?.brows || fade) ? 'brow' : 'still',
      };
    },
  };
}

/**
 * 臉部組合：眨眼＋眼皮／眉毛事件。程序層只用這個。
 * 從 random 抽三個 seed，建立三條互相獨立的亂數流（眨眼、臉部事件、視線轉移眨眼的機率）；
 * 視線轉移那條獨立出來，是為了讓 gazeShift 的呼叫時機不會改變眨眼與臉部事件的序列。
 * @param {() => number} random
 */
export function createFace(random) {
  if (typeof random !== 'function') throw new TypeError('random 必須是函式（亂數要由呼叫端注入）');
  const nextSeed = () => Math.floor(random() * 4294967296) >>> 0;
  const blink = createBlink(mulberry(nextSeed()));
  const faceIdle = createFaceIdle(mulberry(nextSeed()));
  const gazeRandom = mulberry(nextSeed());
  let wasListening = false;
  let last = { enabled: true, listening: false, blinkAllowed: true };

  return {
    /**
     * @param {number} dtS
     * @param {number} nowMs
     * @param {{ enabled?: boolean, listening?: boolean, blinkAllowed?: boolean, eyesAllowed?: boolean, browsAllowed?: boolean }} [ctx]
     */
    update(dtS, nowMs, ctx = {}) {
      const { enabled = true, listening = false, blinkAllowed = true, eyesAllowed = true, browsAllowed = true } = ctx;
      last = { enabled, listening, blinkAllowed };
      // 潛伏時眨眼視同「不允許」：回 1 並清事件，恢復時從現在重排
      const blinkValue = blink.update(dtS, nowMs, enabled && blinkAllowed, true);
      if (listening !== wasListening) {
        wasListening = listening;
        const [min, max] = listening ? BLINK_INTERVAL.listening : BLINK_INTERVAL.idle;
        blink.setIntervalRange(min, max);   // 放在 update 之後：重排用的是這一幀的時間，與幀率無關
      }
      const sample = faceIdle.update(dtS, nowMs, { enabled, listening, eyesAllowed, browsAllowed, blinkPaused: blink.isPaused() });
      return {
        blink: blinkValue,
        eyeL: sample.eyeL,
        eyeR: sample.eyeR,
        browLForm: sample.browLForm,
        browRForm: sample.browRForm,
        browLAngle: sample.browLAngle,
        browRAngle: sample.browRAngle,
        kind: sample.kind,
      };
    },
    /**
     * 大幅轉移視線時順便眨眼。聆聽中、潛伏、眨眼不允許、眨眼進行中都不觸發。
     * @param {number} distance 視線轉移距離（正規化單位）
     * @param {number} nowMs
     * @returns {boolean} 是否觸發
     */
    gazeShift(distance, nowMs) {
      if (!Number.isFinite(distance) || !Number.isFinite(nowMs)) return false;
      if (distance < GAZE_BLINK_DISTANCE) return false;
      if (last.listening || !last.enabled || !last.blinkAllowed || !blink.isIdle()) return false;
      if (gazeRandom() >= GAZE_BLINK_CHANCE) return false;
      blink.forceBlink(0);
      return true;
    },
  };
}
