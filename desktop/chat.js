// 聊天室的紀錄與開關狀態機：後端事件＋使用者操作＋時間 → 聊天紀錄、要不要開／關聊天室視窗。
// 純邏輯：不碰 DOM、Electron；時間一律由呼叫端傳入（毫秒），方便用 node --test 驗證。
// 紀錄只放記憶體，重開 App 就清空（不寫硬碟）。後端內容一律不信任：只留顯示需要的欄位並限制長度。

export const CHAT_MAX = 200;                // 紀錄上限（筆）
export const AUTO_CLOSE_MS = 20000;         // 主動展開後沒有互動，這麼久自動收回
export const PARTIAL_MS = 4000;             // 聆聽中的即時文字沒有後續時的保險期限（同角色泡泡）
export const PENDING_MS = 60000;            // 處理中的「…」沒等到結果時的保險期限
export const EXPAND_REASONS = Object.freeze(['screen', 'chat', 'complex']);
const TEXT_MAX = 4000, STEPS_MAX = 20, LOGS_MAX = 30, LINE_MAX = 2000, ANNOUNCED_MAX = 20;

const clip = (v, n) => String(v ?? '').slice(0, n);

/** 整理 result 事件的 steps：只留顯示欄位、限制數量與長度，格式不對的步驟丟掉 */
function cleanSteps(steps) {
  if (!Array.isArray(steps)) return [];
  return steps.slice(0, STEPS_MAX).filter(st => st && typeof st === 'object').map(st => ({
    kind: clip(st.kind, 16), text: clip(st.text, LINE_MAX), status: clip(st.status, 32),
    route: st.route ? clip(st.route, 16) : '',
    ms: Number.isFinite(st.ms) ? st.ms : null,
    logs: (Array.isArray(st.logs) ? st.logs : []).slice(0, LOGS_MAX).map(l => clip(l, LINE_MAX)),
  }));
}

/** 助手回覆的本文：有 replies 用 replies（完整，不截斷）；否則把各步驟的記錄接起來；都沒有就是「完成」 */
export function bodyOf(ev, steps) {
  const replies = (Array.isArray(ev.replies) ? ev.replies : []).map(r => clip(r, TEXT_MAX).trim()).filter(Boolean);
  if (replies.length) return replies.join('\n');
  const logs = steps.flatMap(st => st.logs.map(l => l.trim()).filter(Boolean));
  return logs.length ? clip(logs.join('\n'), TEXT_MAX) : (ev.ok ? '完成' : '失敗');
}

export function createChat() {
  const s = {
    entries: [],
    nextId: 1,
    rev: 0,                  // entries／pending 改變就加一；主程序拿來判斷要不要重送紀錄
    pending: false, pendingAt: 0,
    partial: null, partialAt: 0,
    paused: false, sessionActive: false,
    announced: [],           // 已經記成使用者訊息、還沒等到 executing 的指令文字（executing 對得上就不重複記）
    open: false, auto: false, interacted: false, autoCloseAt: null,
  };

  function push(entry) {
    s.entries.push({ id: s.nextId++, ...entry });
    if (s.entries.length > CHAT_MAX) s.entries.splice(0, s.entries.length - CHAT_MAX);
    s.rev++;
  }
  function announce(text) {
    s.announced.push(text);
    if (s.announced.length > ANNOUNCED_MAX) s.announced.shift();
  }
  function setPending(on, now) {
    if (s.pending === on) { if (on) s.pendingAt = now; return; }
    s.pending = on; s.pendingAt = now; s.rev++;
  }
  function open(auto, now) {
    s.open = true; s.auto = auto; s.interacted = false;
    s.autoCloseAt = auto ? now + AUTO_CLOSE_MS : null;
    return [{ type: 'chat-open', auto }];
  }
  function close() {
    if (!s.open) return [];
    s.open = false; s.auto = false; s.interacted = false; s.autoCloseAt = null;
    return [{ type: 'chat-close' }];
  }
  function expand(now) {
    if (!s.open) return open(true, now);
    if (s.auto && !s.interacted) s.autoCloseAt = now + AUTO_CLOSE_MS;   // 還沒人理它：重新計時
    return [];
  }

  const handlers = {
    hello(ev, now) {
      s.paused = !!ev.paused;
      s.sessionActive = Number(ev.remaining_s) > 0;
      s.partial = null;
      setPending(false, now);   // 重新連線：之前等的結果不會再來
      return [];
    },
    partial(ev, now) {
      if (s.paused || !ev.text) return [];
      if (!s.sessionActive && !ev.wake) return [];            // 沒喚醒、不在會話中：旁人說話
      s.partial = clip(ev.text, 200); s.partialAt = now;
      return [];
    },
    heard(ev, now) {
      s.partial = null;
      if (s.paused || ev.status !== 'command' || !ev.command) return [];   // ignored／armed 不是指令
      const text = clip(ev.command, 200);
      push({ role: 'user', source: 'voice', text, at: now });
      announce(text);
      return [];
    },
    session(ev) {
      s.sessionActive = Number(ev.remaining_s) > 0;
      if (!s.sessionActive) s.partial = null;
      return [];
    },
    paused(ev) {
      s.paused = !!ev.value;
      if (s.paused) s.partial = null;
      return [];
    },
    executing(ev, now) {
      const text = clip(ev.command, 200);
      const at = s.announced.indexOf(text);
      if (at >= 0) s.announced.splice(at, 1);
      else if (text) push({ role: 'user', source: 'click', text, at: now });   // 沒有人先記過：例如清單點選
      setPending(true, now);
      return [];
    },
    result(ev, now) {
      setPending(false, now);
      const steps = cleanSteps(ev.steps);
      push({ role: 'assistant', ok: !!ev.ok, text: bodyOf(ev, steps), steps,
        totalMs: Number.isFinite(ev.total_ms) ? ev.total_ms : null, at: now });
      return EXPAND_REASONS.includes(ev.expand) ? expand(now) : [];
    },
  };

  return {
    /** 處理一則後端事件，回傳要執行的效果（chat-open／chat-close） */
    handle(ev, now) {
      const fn = handlers[ev && ev.type];
      return fn ? fn(ev, now) : [];
    },
    /** 使用者打字送出的指令（已經確定送到後端才呼叫） */
    userCommand(text, now) {
      const t = clip(text, 200).trim();
      if (!t) return [];
      push({ role: 'user', source: 'typed', text: t, at: now });
      announce(t);
      return [];
    },
    /** 快捷泡泡、右鍵選單：開或關（手動開啟不會自動收回） */
    toggle(now) { return s.open ? close() : open(false, now); },
    close() { return close(); },
    /** 滑鼠移上去、點進去：有互動，不再自動收回 */
    interact() {
      if (!s.open) return;
      s.interacted = true; s.autoCloseAt = null;
    },
    /** 時間流逝：主動展開沒人理就收回；過期的即時文字與處理中標記清掉 */
    tick(now) {
      if (s.partial !== null && now - s.partialAt >= PARTIAL_MS) s.partial = null;
      if (s.pending && now - s.pendingAt >= PENDING_MS) setPending(false, now);
      if (s.open && s.auto && !s.interacted && s.autoCloseAt !== null && now >= s.autoCloseAt) return close();
      return [];
    },
    isOpen: () => s.open,
    snapshot() {
      return { rev: s.rev, entries: s.entries.slice(), pending: s.pending, partial: s.partial, open: s.open, auto: s.auto };
    },
  };
}
