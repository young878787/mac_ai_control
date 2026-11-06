// 桌面角色的狀態機：後端事件＋時間 → 要做的演出（動作、表情、進退場、泡泡）。
// 純邏輯：不碰 DOM、pixi、Electron；時間一律由呼叫端傳入（毫秒），方便用 node --test 驗證。

export const MOTIONS = Object.freeze({
  idle: 'Idle', surprised: 'Surprised', curious: 'Curious', nod: 'Nod', shake: 'Shake', happy: 'Happy',
});  // 刻意沒有 Talk：不做嘴型

export const EXPRESSION_MS = 2000;       // happy／pout 維持時間
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
export const HISTORY_MAX = 50;

export function bubbleLifetime(text) {
  return Math.min(BUBBLE_MAX_MS, Math.max(BUBBLE_MIN_MS, BUBBLE_MIN_MS + BUBBLE_PER_CHAR_MS * [...text].length));
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
    expression: { name: 'neutral', until: 0 },
    lastActivityAt: 0,
    retreatAt: null,
    inputOpen: false,
    historyOpen: false,
    bubbles: [],
    history: [],
    nextBubbleId: 1,
  };

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
  function log(kind, text, now) {
    s.history.push({ at: now, kind, text });
    if (s.history.length > HISTORY_MAX) s.history.splice(0, s.history.length - HISTORY_MAX);
  }
  function setExpression(effects, name, now, ms = 0) {
    s.expression = { name, until: ms ? now + ms : 0 };
    effects.push({ type: 'expression', name });
  }
  function appear(effects, now, { greet = true } = {}) {
    s.lastActivityAt = now;
    s.retreatAt = null;
    if (s.pose === 'out') return;
    s.pose = 'out';
    effects.push({ type: 'pose', pose: 'out', ms: APPEAR_MS });
    if (greet) {
      effects.push({ type: 'motion', group: MOTIONS.surprised });
      setExpression(effects, 'happy', now, EXPRESSION_MS);
    }
  }
  function retreat(effects) {
    if (s.pose !== 'out') return;
    s.pose = 'peek';
    s.mode = 'idle';
    effects.push({ type: 'pose', pose: 'peek', ms: RETREAT_MS }, { type: 'motion', group: MOTIONS.idle });
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
    removeBubble('partial');
    if (s.pose === 'out') {
      setExpression(effects, 'sleepy', now, SLEEPY_MS);
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
      log('heard', ev.text, now);
    },
    session(ev, now, effects) {
      if (s.paused && ev.remaining_s > 0) return;
      setSession(effects, ev.remaining_s, now);
      if (ev.remaining_s > 0 && s.pose !== 'out') appear(effects, now);
    },
    executing(ev, now, effects) {
      appear(effects, now, { greet: false });
      s.mode = 'executing';
      effects.push({ type: 'motion', group: MOTIONS.curious });
      bubble(effects, now, { id: 'pending', kind: 'pending', text: '…', ms: PENDING_BUBBLE_MS });
    },
    result(ev, now, effects) {
      appear(effects, now, { greet: false });
      removeBubble('pending');
      const text = summarize(ev);
      if (ev.ok) {
        s.mode = 'success';
        effects.push({ type: 'motion', group: MOTIONS.nod });
        setExpression(effects, 'happy', now, EXPRESSION_MS);
      } else {
        s.mode = 'failure';
        effects.push({ type: 'motion', group: MOTIONS.shake });
        setExpression(effects, 'pout', now, EXPRESSION_MS);
      }
      bubble(effects, now, { kind: ev.ok ? 'result' : 'error', text, ms: bubbleLifetime(text) });
      log(ev.ok ? 'result' : 'error', text, now);
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
        s.expression = { name: 'neutral', until: 0 };
        effects.push({ type: 'expression', name: 'neutral' });
        if (s.mode === 'success' || s.mode === 'failure') s.mode = 'idle';
      }
      if (s.session.active && now >= s.session.endsAt) endSession(effects, now);
      if (s.retreatAt !== null && now >= s.retreatAt) {
        s.retreatAt = null;
        if (!s.inputOpen && !s.historyOpen) retreat(effects);
        else s.lastActivityAt = now;                       // 輸入框／紀錄面板開著：關掉之後再從頭算 15 秒
      } else if (s.pose === 'out' && !s.session.active && s.retreatAt === null && !s.inputOpen && !s.historyOpen &&
                 now - s.lastActivityAt >= IDLE_RETREAT_MS) {
        retreat(effects);
      }
      s.bubbles = s.bubbles.filter(b => b.hoverLeft !== null || now < b.expiresAt);
      return effects;
    },
    /** 使用者操作（點擊、輸入框、紀錄面板、泡泡懸停） */
    click(region, now) {
      const effects = [];
      appear(effects, now, { greet: region === 'head' });
      if (region === 'head') {
        effects.push({ type: 'send', message: { type: 'wake' } });
      } else {
        effects.push({ type: 'motion', group: MOTIONS.happy });
        setExpression(effects, 'happy', now, EXPRESSION_MS);
      }
      return effects;
    },
    userCommand(text, now) {
      const effects = [];
      appear(effects, now, { greet: false });
      log('user', text, now);
      return effects;
    },
    setInputOpen(open, now) {
      s.inputOpen = open;
      s.lastActivityAt = now;
      const effects = [];
      if (open) appear(effects, now, { greet: false });
      return effects;
    },
    setHistoryOpen(open, now) {
      s.historyOpen = open;
      s.lastActivityAt = now;
      const effects = [];
      if (open) appear(effects, now, { greet: false });
      return effects;
    },
    hoverBubble(id, hovering, now) {
      const b = s.bubbles.find(x => x.id === id);
      if (!b) return;
      if (hovering && b.hoverLeft === null) b.hoverLeft = Math.max(0, b.expiresAt - now);
      else if (!hovering && b.hoverLeft !== null) { b.expiresAt = now + b.hoverLeft; b.hoverLeft = null; }
    },
    snapshot(now = 0) {
      return {
        pose: s.pose, mode: s.mode, paused: s.paused, expression: s.expression.name,
        session: {
          active: s.session.active,
          remainingMs: s.session.active ? Math.max(0, s.session.endsAt - now) : 0,
          ratio: s.session.active ? Math.max(0, Math.min(1, (s.session.endsAt - now) / s.session.lengthMs)) : 0,
        },
        bubbles: s.bubbles.map(b => ({ id: b.id, kind: b.kind, text: b.text })),
        history: s.history.slice(),
        fps: s.pose === 'out' ? 30 : 15,
      };
    },
  };
}
