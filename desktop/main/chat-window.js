// 聊天室視窗：先建好並隱藏，要開的時候用 showInactive（不搶焦點，前景 App 不變）。
// 紀錄與開關的邏輯在 domain/chat-log.js（純邏輯）；這裡只負責視窗與把紀錄推給畫面。展開／收合動畫由畫面內容的 CSS 做（視窗本身不動）。
import { BrowserWindow, screen } from 'electron';
import path from 'node:path';
import { createChat } from '../domain/chat-log.js';
import { forwardConsole } from './console.js';
import { loadSettings, saveSettings } from './settings.js';

const CHAT_W = 420, CHAT_H = 600, CHAT_MIN_W = 320, CHAT_MIN_H = 400, CHAT_MAX = 1400, CHAT_GAP = 8, CHAT_MARGIN_BOTTOM = 16;
const CHAT_HIDE_FALLBACK_MS = 500;   // 收合動畫結束會通知；沒收到也在這時間後隱藏

/** @param {{ root: string, role: ReturnType<typeof import('./role-window.js').createRoleWindow> }} opts */
export function createChatWindow({ root, role }) {
  const chat = createChat();
  /** @type {BrowserWindow | null} */
  let win = null;
  let ready = false, shown = false, hideTimer = null;
  let sentRev = -1, sentPartial = null, lastOpen = null;

  const alive = () => !!win && !win.isDestroyed();

  function size() {
    const saved = loadSettings().chatSize;
    const ok = (v, lo, dflt) => (Number.isFinite(v) ? Math.round(Math.min(CHAT_MAX, Math.max(lo, v))) : dflt);
    return Array.isArray(saved) ? [ok(saved[0], CHAT_MIN_W, CHAT_W), ok(saved[1], CHAT_MIN_H, CHAT_H)] : [CHAT_W, CHAT_H];
  }
  /** 貼在角色視窗旁邊（左邊優先，放不下改右邊），下緣對齊工作區底部；回傳在哪一側（動畫從靠角色的那角長出來） */
  function place() {
    const rb = role.win.getBounds(), wa = screen.getPrimaryDisplay().workArea, [w, h] = win.getSize();
    let side = 'left', x = rb.x - w - CHAT_GAP;
    if (x < wa.x) { side = 'right'; x = rb.x + rb.width + CHAT_GAP; }
    x = Math.round(Math.min(wa.x + wa.width - w, Math.max(wa.x, x)));
    win.setPosition(x, Math.round(Math.max(wa.y, wa.y + wa.height - h - CHAT_MARGIN_BOTTOM)));
    return side;
  }
  /** 紀錄有變才重送整份（上限 200 筆）；只有即時文字變就只送它。聊天室隱藏時不送：它的 renderer 每收到一次就重畫（實測每則結果 20–40 ms CPU） */
  function push() {
    if (!alive() || !ready || !shown) return;
    const snap = chat.snapshot();
    if (snap.rev !== sentRev) {
      sentRev = snap.rev; sentPartial = snap.partial;
      win.webContents.send('chat-state', { entries: snap.entries, pending: snap.pending, partial: snap.partial });
    } else if (snap.partial !== sentPartial) {
      sentPartial = snap.partial;
      win.webContents.send('chat-live', { partial: snap.partial });
    }
  }
  function open(auto, since) {
    if (!alive() || !role.win) return;
    clearTimeout(hideTimer);
    const side = place();
    shown = true;
    sentRev = -1; push();                               // 隱藏期間不推紀錄，展開時一次補上
    if (!win.isVisible()) win.showInactive();           // 不搶焦點：前景 App 會決定 front_action、分頁、看畫面的對象
    lastOpen = { auto, showMs: Date.now() - since, paintMs: null };
    if (ready) win.webContents.send('chat-show', { auto, side });   // 還沒載入完的話，ready 時補送
  }
  function hideNow() {
    clearTimeout(hideTimer);
    if (!shown && alive()) win.hide();
  }
  function close() {
    if (!alive() || !shown) return;
    shown = false;
    win.webContents.send('chat-hide');
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hideNow, CHAT_HIDE_FALLBACK_MS);
  }
  /** 執行 domain/chat-log 回傳的效果（chat-open／chat-close） */
  function run(effects, since = Date.now()) {
    for (const e of effects) {
      if (e.type === 'chat-open') open(e.auto, since);
      else if (e.type === 'chat-close') close();
    }
  }

  return {
    chat,
    get win() { return win; },
    get shown() { return shown; },
    get lastOpen() { return lastOpen; },
    alive,
    create() {
      const [width, height] = size();
      win = new BrowserWindow({
        width, height, minWidth: CHAT_MIN_W, minHeight: CHAT_MIN_H, show: false, transparent: true, frame: false, hasShadow: true, resizable: true,
        fullscreenable: false, skipTaskbar: true, type: 'panel', backgroundColor: '#00000000',
        webPreferences: { preload: path.join(root, 'preload', 'chat.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
      });
      win.setAlwaysOnTop(true, 'floating');
      win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
      win.on('resized', () => { const [w, h] = win.getSize(); saveSettings({ chatSize: [w, h] }); });
      forwardConsole(win.webContents, 'chat');
      win.loadURL('app://ui/renderer/chat/chat.html');
    },
    push,
    run,
    /** 後端事件：交給紀錄，必要時展開／收合，再推紀錄 */
    handleBus(msg, recv) { run(chat.handle(msg, recv), recv); push(); },
    /** 時間流逝：主動展開沒人理就收回；過期的即時文字清掉 */
    tick() { run(chat.tick(Date.now())); push(); },
    toggle() { run(chat.toggle(Date.now())); },
    requestClose() { run(chat.close()); },
    interact() { chat.interact(); },
    /** 成功送到後端的打字指令也記進聊天室 */
    userCommand(text) { chat.userCommand(text, Date.now()); push(); },
    /** 這個 IPC 訊息是不是聊天室視窗自己送的 */
    isSender(webContents) { return alive() && webContents === win.webContents; },
    onReady() {
      ready = true; sentRev = -1;
      push();
      if (shown) win.webContents.send('chat-show', { auto: lastOpen ? lastOpen.auto : true, side: 'left' });
    },
    focus() { if (shown && alive()) win.focus(); },
    hidden() { hideNow(); },
    painted(ms) { if (lastOpen && lastOpen.paintMs === null && Number.isFinite(ms)) lastOpen.paintMs = Math.round(ms * 10) / 10; },
  };
}
