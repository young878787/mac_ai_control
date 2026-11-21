// 聊天室畫面：依主程序送來的紀錄顯示對話、收發指令。文字一律用 textContent，不解析成 HTML。
/** @type {(id: string) => any} */
const $ = (id) => document.getElementById(id);
const log = $('log'), live = $('live'), box = $('box'), form = $('form'), send = $('send'), empty = $('empty');
const FAIL = new Set(['exec_failed', 'llm_unavailable', 'llm_error']);
const SOURCE = { voice: '🎙️ 語音', typed: '⌨️ 打字', click: '🖱️ 點選' };
const icon = (status) => (status === 'ok' ? '✅' : status === 'skipped' ? '⏭️' : FAIL.has(status) ? '❌' : '⚠️');
const routeOf = (st) => (st.kind === 'fast' ? '本機規則' : st.route === 'cloud' ? '雲端' : '本機模型');
const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${Math.round(ms)} ms`);

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

function renderEntry(entry) {
  const row = el('div', `msg ${entry.role}${entry.role === 'assistant' && !entry.ok ? ' fail' : ''}`);
  if (entry.role === 'user') {
    row.append(el('span', 'tag', SOURCE[entry.source] || ''), el('div', 'body', entry.text));
    return row;
  }
  row.append(el('div', 'body', entry.text));
  if (entry.steps.length) {
    const d = el('details', 'steps');
    d.append(el('summary', '', `步驟 ${entry.steps.length}${entry.totalMs != null ? `・共 ${fmtMs(entry.totalMs)}` : ''}`));
    for (const st of entry.steps) {
      const s = el('div', 'step'), head = el('div', 'head');
      head.append(el('span', '', icon(st.status)), el('span', 'chip', routeOf(st)), el('span', '', st.text), el('span', 'ms', fmtMs(st.ms)));
      s.append(head);
      if (st.logs.length) s.append(el('pre', 'logs', st.logs.join('\n')));
      d.append(s);
    }
    row.append(d);
  }
  return row;
}

// ---------- 紀錄：紀錄一旦寫入就不會變，所以只追加新的、移除被擠掉的 ----------
const rows = new Map();
let pendingRow = null;
function nearBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 40; }
function applyState({ entries, pending, partial }) {
  const stick = nearBottom();
  const ids = new Set(entries.map(e => e.id));
  for (const [id, row] of rows) if (!ids.has(id)) { row.remove(); rows.delete(id); }
  for (const e of entries) if (!rows.has(e.id)) { const row = renderEntry(e); rows.set(e.id, row); log.append(row); }
  if (pendingRow) { pendingRow.remove(); pendingRow = null; }
  if (pending) { pendingRow = el('div', 'msg assistant pending'); pendingRow.append(el('div', 'body', '…')); log.append(pendingRow); }
  empty.hidden = entries.length > 0 || pending;
  applyLive({ partial });
  if (stick) log.scrollTop = log.scrollHeight;
}
function applyLive({ partial }) {
  live.hidden = !partial;
  live.textContent = partial ? `🎙️ ${partial}` : '';
}

// ---------- 輸入 ----------
function fit() { box.style.height = 'auto'; box.style.height = `${Math.min(box.scrollHeight, 120)}px`; }
function submit() {
  const text = box.value.trim();
  if (!text) return;
  window.chat.send(text);
  box.value = ''; fit(); updateSend();
}
function updateSend() { send.disabled = !box.value.trim(); }
form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
box.addEventListener('input', () => { fit(); updateSend(); });
box.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); }   // 輸入法選字時的 Enter 不送出
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') window.chat.close(); });
$('close').addEventListener('click', () => window.chat.close());

// ---------- 互動：滑鼠移上去、點進去都算有互動（主動展開就不會自動收回）；點進去才要鍵盤焦點 ----------
let told = false;   // 每次展開只通知主程序一次（它只需要知道「有沒有互動過」）
const interact = () => { if (!told) { told = true; window.chat.interact(); } };
document.addEventListener('mouseover', interact);
document.addEventListener('pointerdown', () => { interact(); window.chat.focus(); });
box.addEventListener('focus', interact);

// ---------- 展開／收合動畫（CSS，視窗本身不動）：展開後量第一幀畫出來的時間給主程序記錄 ----------
window.chat.onShow(({ auto, side }) => {
  document.documentElement.style.setProperty('--origin', side === 'right' ? 'left bottom' : 'right bottom');
  document.documentElement.style.setProperty('--from-x', side === 'right' ? '-28px' : '28px');
  told = false;
  const t0 = performance.now();
  requestAnimationFrame(() => {
    document.body.classList.remove('hidden');
    requestAnimationFrame(() => window.chat.painted(performance.now() - t0));
  });
  log.scrollTop = log.scrollHeight;
  if (!auto) { window.chat.focus(); box.focus(); }   // 手動打開＝想打字；主動展開不拿焦點（前景 App 不能變）
});
window.chat.onHide(() => {
  document.body.classList.add('hidden');
  box.blur();
  setTimeout(() => window.chat.hidden(), 170);
});
window.chat.onState(applyState);
window.chat.onLive(applyLive);
updateSend();
window.chat.ready();

// 除錯探針（main/debug.js 的 debug_chat_* 用）：唯讀的畫面狀態，以及在輸入框填字並送出（走真正的 submit 路徑）
window.__chat = {
  probe: () => ({ hidden: document.body.classList.contains('hidden'), rows: document.querySelectorAll('#log .msg').length,
    text: log.innerText, details: document.querySelectorAll('details.steps').length, live: live.textContent }),
  submit: (text) => { box.value = text; box.dispatchEvent(new Event('input')); form.requestSubmit(); },
};
