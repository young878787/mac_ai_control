// 桌面角色的狀態機：後端事件＋時間 → 要做的演出（動作、表情、進退場、泡泡）。
// 純邏輯：不碰 DOM、pixi、Electron；時間一律由呼叫端傳入（毫秒），方便用 node --test 驗證。

import { CHOICES_MAX, EXPAND_REASONS } from '../shared/contract.js';

// 演出一律用語意名稱（驚訝、點頭…），不知道是哪個模型；語意名稱對應到模型裡的動作／表情／手勢名稱是 rig/ 的事。
// 新增語意名稱要同步在 rig/ 的 profile 補對應（rig/check.js 的 checkRig 會檢查）。
export const MOTIONS = Object.freeze({
  idle: 'idle', surprised: 'surprised', curious: 'curious', nod: 'nod', shake: 'shake', happy: 'happy', talk: 'talk',
});  // talk 只是說話的樣子（沒有聲音、不對嘴）；不用 Blink：它的頭身曲線全是 0，會把 Idle 的姿勢歸零，Idle 本身就會眨眼
export const EXPRESSIONS = Object.freeze({
  neutral: 'neutral', happy: 'happy', pout: 'pout', sleepy: 'sleepy', surprised: 'surprised', starEyes: 'starEyes', poutLeft: 'poutLeft',
});
// 手勢層（手臂切換）：和臉部表情分開的第二個表情佇列，三個互斥；none＝收回手臂
export const GESTURES = Object.freeze({ armLeftUp: 'armLeftUp', armRightUp: 'armRightUp', chinRest: 'chinRest', none: 'none' });

export const EXPRESSION_MS = 2000;       // 臉部表情（happy／pout／surprised…）維持時間
export const GESTURE_MS = 1500;          // 揮手、舉手維持時間
export const THINK_MS = 2500;            // 執行超過這麼久還沒有結果就托腮
export const TALK_MIN_MS = 1500;         // 有文字回覆時的說話動作長度：依字數，限制在這個範圍
export const TALK_MAX_MS = 4000;
export const TALK_PER_CHAR_MS = 120;
export const SLEEPY_MS = 1500;           // 會話結束的睏倦表情，之後退場
export const IDLE_RETREAT_MS = 15000;    // 非會話觸發（打字、點擊）時，最後一個事件後這麼久退場
export const APPEAR_MS = 600;            // 出場動畫
export const RETREAT_MS = 800;           // 退場動畫
export const PEEK_RATIO = 0.3;           // 潛伏時露出模型高度的比例
export const BUBBLE_MAX = 3;
export const BUBBLE_MIN_MS = 3000;
export const BUBBLE_MAX_MS = 12000;
export const BUBBLE_PER_CHAR_MS = 150;
export const PARTIAL_BUBBLE_MS = 4000;   // partial 泡泡的保險期限：沒有後續事件也會消失
export const PENDING_BUBBLE_MS = 30000;
export const QUICK_MS = 5000;            // 點角色彈出的快捷泡泡，沒有操作這麼久就收起來
export const EXPAND_NOTICE = '詳細內容在對話視窗';   // 後端要求聊天室展開（result.expand）時，角色泡泡只放這句，內容在聊天室
export { CHOICES_MAX };

export function bubbleLifetime(text) {
  return Math.min(BUBBLE_MAX_MS, Math.max(BUBBLE_MIN_MS, BUBBLE_MIN_MS + BUBBLE_PER_CHAR_MS * [...text].length));
}

/** 整理 result 事件的 choices：只留顯示需要的欄位，格式不對回傳 null（UI 不信任後端內容） */
function cleanChoices(c) {
  if (!c || !Array.isArray(c.items) || !Number.isInteger(c.list_id)) return null;
  const items = c.items.slice(0, CHOICES_MAX).filter(i => i && Number.isInteger(i.n) && i.n >= 1 && i.n <= CHOICES_MAX)
    .map(i => ({ n: i.n, name: String(i.name ?? ''), dir: String(i.dir ?? ''), kind: String(i.kind ?? '') }));
  if (!items.length) return null;
  return { listId: c.list_id, query: String(c.query ?? ''), total: Number.isInteger(c.total) ? c.total : items.length,
    defaultAction: c.default_action === 'open' ? 'open' : 'reveal', items };
}

/** 把 result 事件整理成一段短文字：有回覆用回覆；否則取各步驟的記錄（失敗的優先），最多兩行 */
export function summarize(result) {
  const replies = (result.replies || []).map(s => String(s).trim()).filter(Boolean);
  if (replies.length) return replies.join('\n');
  const logs = [];
  for (const step of result.steps || []) for (const line of step.logs || []) if (String(line).trim()) logs.push(String(line).trim());
  const failed = logs.filter(l => /^(❌|⚠️)/.test(l));
  const chosen = (result.ok ? logs : (failed.length ? failed : logs)).slice(-2);
  const text = chosen.join('\n');
  if (!text) return result.ok ? '完成' : '失敗';
  return [...text].length > 80 ? [...text].slice(0, 79).join('') + '…' : text;
}

export function createController() {
  const s = {
    pose: 'peek',            // peek＝潛伏在下緣；out＝出場
    mode: 'idle',            // idle／listening／executing／success／failure
    paused: false,
    session: { active: false, endsAt: 0, lengthMs: 60000 },
    expression: { name: EXPRESSIONS.neutral, until: 0 },
    gesture: { name: GESTURES.none, until: 0 },   // until＝0：直到被別的事件收回
    thinkAt: null,           // 執行中：到這個時間還沒結果就托腮
    talkUntil: null,         // 說話動作播到這個時間回到 Idle
    failStreak: 0,           // 連續失敗次數（成功、會話結束、退場時歸零）
    lastActivityAt: 0,
    retreatAt: null,
    inputOpen: false,
    quick: null,             // 快捷泡泡：{ until, hover }；沒開就是 null（until 在 hover 時不計）
    choices: null,           // 搜尋結果清單：{ listId, query, total, defaultAction, items, endsAt }；沒有就是 null
    bubbles: [],
    nextBubbleId: 1,
  };

  /**
   * @param {object[]} effects
   * @param {number} now
   * @param {{ id?: string, kind: string, text: string, ms: number }} spec
   */
  function bubble(effects, now, { id, kind, text, ms }) {
    const existing = id && s.bubbles.find(b => b.id === id);
    if (existing) {
      existing.text = text; existing.expiresAt = now + ms; existing.hoverLeft = null;
      return;
    }
    s.bubbles.push({ id: id || `b${s.nextBubbleId++}`, kind, text, expiresAt: now + ms, hoverLeft: null });
    while (s.bubbles.length > BUBBLE_MAX) {
      const drop = s.bubbles.findIndex(b => b.kind !== 'partial' && b.kind !== 'pending');
      s.bubbles.splice(drop < 0 ? 0 : drop, 1);
    }
  }
  function removeBubble(id) { s.bubbles = s.bubbles.filter(b => b.id !== id); }
  function setExpression(effects, name, now, ms = 0) {
    s.expression = { name, until: ms ? now + ms : 0 };
    effects.push({ type: 'expression', name });
  }
  function setGesture(effects, name, now, ms = 0) {
    if (name === GESTURES.none && s.gesture.name === GESTURES.none) return;
    s.gesture = { name, until: ms ? now + ms : 0 };
    effects.push({ type: 'gesture', name });
  }
  /** 播放動作；說話動作以外的動作會取消說話的結束時間（否則之後會把新動作切回 Idle） */
  function playMotion(effects, name, now, talkMs = 0) {
    s.talkUntil = name === MOTIONS.talk ? now + talkMs : null;
    effects.push({ type: 'motion', name });
  }
  /**
   * greet：'voice'＝語音喚醒／會話開始（驚訝）；'click'＝點頭部（星星眼＋揮手）；false＝不打招呼
   * @param {object[]} effects
   * @param {number} now
   * @param {{ greet?: 'voice' | 'click' | false }} [opts]
   */
  function appear(effects, now, { greet = 'voice' } = {}) {
    s.lastActivityAt = now;
    s.retreatAt = null;
    if (s.pose === 'out') return;
    s.pose = 'out';
    effects.push({ type: 'pose', pose: 'out', ms: APPEAR_MS });
    if (greet) {
      playMotion(effects, MOTIONS.surprised, now);
      if (greet === 'click') {
        setExpression(effects, EXPRESSIONS.starEyes, now, EXPRESSION_MS);
        setGesture(effects, GESTURES.armRightUp, now, GESTURE_MS);
      } else {
        setExpression(effects, EXPRESSIONS.surprised, now, EXPRESSION_MS);
      }
    }
  }
  function retreat(effects, now) {
    if (s.pose !== 'out') return;
    s.pose = 'peek';
    s.mode = 'idle';
    s.thinkAt = null;
    s.failStreak = 0;
    effects.push({ type: 'pose', pose: 'peek', ms: RETREAT_MS });
    playMotion(effects, MOTIONS.idle, now);
    setGesture(effects, GESTURES.none, now);
  }
  function setSession(effects, remainingS, now) {
    const was = s.session.active;
    if (remainingS > 0) {
      s.session.active = true;
      s.session.endsAt = now + remainingS * 1000;
      s.session.lengthMs = Math.max(s.session.lengthMs, remainingS * 1000);
      s.retreatAt = null;
    } else if (was) {
      endSession(effects, now);
    }
  }
  function endSession(effects, now) {
    s.session.active = false;
    s.mode = 'idle';
    s.failStreak = 0;
    removeBubble('partial');
    if (s.pose === 'out') {
      setExpression(effects, EXPRESSIONS.sleepy, now, SLEEPY_MS);
      setGesture(effects, GESTURES.chinRest, now, SLEEPY_MS);
      s.retreatAt = now + SLEEPY_MS;
    }
  }

  const handlers = {
    hello(ev, now, effects) {
      s.paused = !!ev.paused;
      if (ev.session_s) s.session.lengthMs = ev.session_s * 1000;
      if (ev.remaining_s > 0) { appear(effects, now, { greet: false }); setSession(effects, ev.remaining_s, now); }
    },
    partial(ev, now, effects) {
      if (s.paused || !ev.text) return;
      if (!s.session.active && !ev.wake) return;           // 沒喚醒、不在會話中：旁人說話，不反應
      appear(effects, now);
      s.mode = 'listening';
      bubble(effects, now, { id: 'partial', kind: 'partial', text: ev.text, ms: PARTIAL_BUBBLE_MS });
    },
    heard(ev, now, effects) {
      if (s.paused) return;
      removeBubble('partial');
      if (s.mode === 'listening') s.mode = 'idle';
      if (ev.status === 'ignored') return;
      appear(effects, now);
    },
    session(ev, now, effects) {
      if (s.paused && ev.remaining_s > 0) return;
      setSession(effects, ev.remaining_s, now);
      if (ev.remaining_s > 0 && s.pose !== 'out') appear(effects, now);
    },
    executing(ev, now, effects) {
      appear(effects, now, { greet: false });
      s.mode = 'executing';
      s.thinkAt = now + THINK_MS;
      playMotion(effects, MOTIONS.curious, now);
      bubble(effects, now, { id: 'pending', kind: 'pending', text: '…', ms: PENDING_BUBBLE_MS });
    },
    result(ev, now, effects) {
      appear(effects, now, { greet: false });
      removeBubble('pending');
      const text = summarize(ev);
      s.thinkAt = null;
      setGesture(effects, GESTURES.none, now);
      if (ev.ok) {
        s.mode = 'success';
        s.failStreak = 0;
        const replyChars = (ev.replies || []).reduce((n, r) => n + [...String(r).trim()].length, 0);
        if (replyChars) {                                     // 有文字回覆：說話（沒有聲音）
          playMotion(effects, MOTIONS.talk, now, Math.min(TALK_MAX_MS, Math.max(TALK_MIN_MS, replyChars * TALK_PER_CHAR_MS)));
        } else {
          playMotion(effects, MOTIONS.nod, now);
        }
        setExpression(effects, EXPRESSIONS.happy, now, EXPRESSION_MS);
      } else {
        s.mode = 'failure';
        s.failStreak++;
        playMotion(effects, MOTIONS.shake, now);
        setExpression(effects, s.failStreak >= 2 ? EXPRESSIONS.poutLeft : EXPRESSIONS.pout, now, EXPRESSION_MS);
      }
      const list = ev.choices && !ev.choices.closed ? cleanChoices(ev.choices) : null;
      if (list) {                                            // 有清單：清單本身就是輸出，不另外跳泡泡（會被清單蓋住）
        s.choices = { ...list, endsAt: now + Math.max(0, Number(ev.choices.expires_in_s) || 0) * 1000 };
        return;
      }
      if (ev.choices && ev.choices.closed && s.choices && s.choices.listId === ev.choices.list_id) s.choices = null;  // 後端通知：清單已用掉或被新搜尋取代
      const shown = EXPAND_REASONS.includes(ev.expand) ? EXPAND_NOTICE : text;   // 聊天室會主動展開：完整內容在那裡
      bubble(effects, now, { kind: ev.ok ? 'result' : 'error', text: shown, ms: bubbleLifetime(shown) });
    },
    paused(ev, now, effects) {
      s.paused = !!ev.value;
      if (s.paused) {
        removeBubble('partial');
        if (s.session.active) endSession(effects, now);
        s.retreatAt = now + (s.pose === 'out' ? SLEEPY_MS : 0);
      }
    },
    stt() {},
  };

  return {
    /** 處理一則後端事件，回傳要執行的演出（effects） */
    handle(ev, now) {
      const effects = [];
      const fn = handlers[ev && ev.type];
      if (fn) fn(ev, now, effects);
      return effects;
    },
    /** 時間流逝：表情復原、會話倒數結束、退場、泡泡到期 */
    tick(now) {
      const effects = [];
      if (s.expression.until && now >= s.expression.until) {
        s.expression = { name: EXPRESSIONS.neutral, until: 0 };
        effects.push({ type: 'expression', name: EXPRESSIONS.neutral });
        if (s.mode === 'success' || s.mode === 'failure') s.mode = 'idle';
      }
      if (s.gesture.until && now >= s.gesture.until) setGesture(effects, GESTURES.none, now);
      if (s.thinkAt !== null && now >= s.thinkAt) {
        s.thinkAt = null;
        if (s.mode === 'executing') setGesture(effects, GESTURES.chinRest, now);   // 直到結果出來
      }
      if (s.talkUntil !== null && now >= s.talkUntil) playMotion(effects, MOTIONS.idle, now);
      if (s.session.active && now >= s.session.endsAt) endSession(effects, now);
      if (s.retreatAt !== null && now >= s.retreatAt) {
        s.retreatAt = null;
        if (!s.inputOpen && !s.choices) retreat(effects, now);
        else s.lastActivityAt = now;                       // 輸入框／清單開著：關掉之後再從頭算 15 秒
      } else if (s.pose === 'out' && !s.session.active && s.retreatAt === null && !s.inputOpen && !s.choices &&
                 now - s.lastActivityAt >= IDLE_RETREAT_MS) {
        retreat(effects, now);
      }
      if (s.choices && now >= s.choices.endsAt) { s.choices = null; s.lastActivityAt = now; }
      if (s.quick && !s.quick.hover && now >= s.quick.until) s.quick = null;
      s.bubbles = s.bubbles.filter(b => b.hoverLeft !== null || now < b.expiresAt);
      return effects;
    },
    /** 使用者操作（點擊、輸入框、紀錄面板、泡泡懸停） */
    click(region, now) {
      const effects = [];
      appear(effects, now, { greet: region === 'head' ? 'click' : false });
      if (region === 'head') {
        effects.push({ type: 'send', message: { type: 'wake' } });
      } else {
        playMotion(effects, MOTIONS.happy, now);
        setExpression(effects, EXPRESSIONS.happy, now, EXPRESSION_MS);
        setGesture(effects, GESTURES.armLeftUp, now, GESTURE_MS);
      }
      s.quick = s.quick ? null : { until: now + QUICK_MS, hover: false };   // 點一下彈出快捷泡泡，再點一下收起
      return effects;
    },
    userCommand(text, now) {
      const effects = [];
      appear(effects, now, { greet: false });
      return effects;
    },
    setInputOpen(open, now) {
      s.inputOpen = open;
      s.lastActivityAt = now;
      const effects = [];
      if (open) appear(effects, now, { greet: false });
      return effects;
    },
    /** 快捷泡泡：用掉（點了「聊天室」）或按 Esc 就收起；滑鼠停在上面不計時，移開後再給一段時間 */
    closeQuick() { s.quick = null; },
    hoverQuick(hovering, now) {
      if (!s.quick) return;
      s.quick.hover = hovering;
      if (!hovering) s.quick.until = now + QUICK_MS;
    },
    /** 點選清單的第 n 筆；action 沒給就用清單的預設動作。清單要等後端回報成功才會關（失敗時留著可以重選） */
    choose(n, action, now) {
      const effects = [];
      if (!s.choices || now >= s.choices.endsAt || !s.choices.items.some(i => i.n === n)) return effects;
      s.lastActivityAt = now;
      effects.push({ type: 'send', message: { type: 'choose', list_id: s.choices.listId, n, action: action || s.choices.defaultAction } });
      return effects;
    },
    closeChoices(now) {
      s.choices = null;
      s.lastActivityAt = now;
      return [];
    },
    hoverBubble(id, hovering, now) {
      const b = s.bubbles.find(x => x.id === id);
      if (!b) return;
      if (hovering && b.hoverLeft === null) b.hoverLeft = Math.max(0, b.expiresAt - now);
      else if (!hovering && b.hoverLeft !== null) { b.expiresAt = now + b.hoverLeft; b.hoverLeft = null; }
    },
    snapshot(now = 0) {
      return {
        pose: s.pose, mode: s.mode, paused: s.paused, expression: s.expression.name, gesture: s.gesture.name,
        session: {
          active: s.session.active,
          remainingMs: s.session.active ? Math.max(0, s.session.endsAt - now) : 0,
          ratio: s.session.active ? Math.max(0, Math.min(1, (s.session.endsAt - now) / s.session.lengthMs)) : 0,
        },
        bubbles: s.bubbles.map(b => ({ id: b.id, kind: b.kind, text: b.text })),
        choices: s.choices && { listId: s.choices.listId, query: s.choices.query, total: s.choices.total,
          defaultAction: s.choices.defaultAction, items: s.choices.items.map(i => ({ ...i })), remainingMs: Math.max(0, s.choices.endsAt - now) },
        quick: !!s.quick,
      };
    },
  };
}
