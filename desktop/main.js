// Electron 主程序：透明置頂視窗、聊天室視窗、自訂協定提供檔案、連到後端事件匯流排、全域快捷鍵與右鍵選單。
// 與後端的連線由主程序持有（token 不進畫面程序），事件再轉給畫面。
import { app, BrowserWindow, Menu, globalShortcut, ipcMain, protocol, screen } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createChat } from './domain/chat-log.js';
import { resolveAppFile } from './protocol.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUS_URL = process.env.MAC_AI_UI_URL || '';
const MODEL_PATH = process.env.MAC_AI_UI_MODEL || '';
const LOG_PATH = process.env.MAC_AI_UI_LOG || '';
const RUN_ID = process.env.MAC_AI_RUN_ID || '';   // 後端這次啟動的編號，ui.jsonl 每行帶著，才能和 latency／commands 對起來
const CAPTURE_DIR = process.env.MAC_AI_UI_CAPTURE_DIR || '';   // 開發用：後端送 debug_capture 時把視窗存成 PNG
const WIN_W = 360, WIN_H = 560, MARGIN_RIGHT = 24, X_SLACK = 60;
const DEFAULT_SHORTCUT = 'Alt+Space';
const CHAT_W = 420, CHAT_H = 600, CHAT_MIN_W = 320, CHAT_MIN_H = 400, CHAT_MAX = 1400, CHAT_GAP = 8, CHAT_MARGIN_BOTTOM = 16;
const CHAT_HIDE_FALLBACK_MS = 500;   // 收合動畫結束會通知；沒收到也在這時間後隱藏
const CMD_MAX_CHARS = 200;           // 與後端 UI_COMMAND_MAX_CHARS 一致
const ALLOWED_SEND = new Set(['command', 'wake', 'pause', 'quit', 'choose']);
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.png': 'image/png', '.css': 'text/css', '.moc3': 'application/octet-stream' };

protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

// ---------- 自訂協定：只開放這幾個根目錄與兩個函式庫檔 ----------
const ROOTS = [['/renderer/', path.join(HERE, 'renderer')], ['/domain/', path.join(HERE, 'domain')], ['/vendor/', path.join(HERE, 'vendor')],
  ['/model/', MODEL_PATH ? path.dirname(MODEL_PATH) : path.join(HERE, 'no-model')]];
const LIB_FILES = {
  '/lib/pixi.min.js': path.join(HERE, 'node_modules', 'pixi.js', 'dist', 'browser', 'pixi.min.js'),
  '/lib/cubism4.min.js': path.join(HERE, 'node_modules', 'pixi-live2d-display', 'dist', 'cubism4.min.js'),
};

// ---------- 設定與記錄 ----------
const settingsPath = () => path.join(app.getPath('userData'), 'settings.json');
function loadSettings() {
  try { return JSON.parse(fs.readFileSync(settingsPath(), 'utf8')); } catch { return {}; }
}
function saveSettings(patch) {
  const next = { ...loadSettings(), ...patch };
  try { fs.mkdirSync(path.dirname(settingsPath()), { recursive: true }); fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2)); } catch (e) { console.error('settings save failed', e); }
}
if (LOG_PATH) { try { fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true }); } catch (e) { console.error('log dir create failed', e); } }
function logRow(row) {
  if (!LOG_PATH) return;
  fs.appendFile(LOG_PATH, JSON.stringify(row) + '\n', () => {});
}
// ui.jsonl 一則訊息一行：bus_ms＝後端送出→本程序收到；apply_ms＝收到→畫面套用（null＝沒等到套用）。
// 後端的 id 每次連線重編，所以帶 conn（第幾次連上匯流排）才唯一
let conn = 0;
const pendingRows = new Map();
function isoLocal(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, '0'), off = -d.getTimezoneOffset(), a = Math.abs(off);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${off >= 0 ? '+' : '-'}${p(Math.floor(a / 60))}:${p(a % 60)}`;
}
function flushPendingRows() {
  for (const { recv, ...rest } of pendingRows.values()) logRow({ ...rest, apply_ms: null });
  pendingRows.clear();
}

// ---------- 視窗 ----------
let win = null;
let paused = false;
let ignoring = true;
let dragStartX = 0;
let gazeOn = false;

function clampX(x, b) { return Math.round(Math.min(b.x + b.width - WIN_W + X_SLACK, Math.max(b.x - X_SLACK, x))); }
function placeWindow() {
  const b = screen.getPrimaryDisplay().bounds;
  const saved = loadSettings().x;
  const x = clampX(Number.isFinite(saved) ? saved : b.x + b.width - WIN_W - MARGIN_RIGHT, b);
  win.setBounds({ x, y: b.y + b.height - WIN_H, width: WIN_W, height: WIN_H });
}

function createWindow() {
  win = new BrowserWindow({
    width: WIN_W, height: WIN_H, show: false, transparent: true, frame: false, hasShadow: false, resizable: false,
    skipTaskbar: true, type: 'panel', backgroundColor: '#00000000', enableLargerThanScreen: true,
    webPreferences: { preload: path.join(HERE, 'preload', 'role.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true,
      backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  placeWindow();
  forwardConsole(win.webContents, 'renderer');
  win.loadURL('app://ui/renderer/role/index.html');
}
function forwardConsole(wc, name) {
  wc.on('console-message', (event) => {
    const { level, message } = event;
    if (message.startsWith('Electron Security Warning')) return;  // 只有開發模式才有；CSP 是刻意設的
    if (level === 'error' || level === 'warning' || /^\[ui\]/.test(message)) console.log(`[${name} ${level}] ${message} (${event.sourceId}:${event.lineNumber})`);
  });
  wc.on('render-process-gone', (_e, details) => console.error(`${name} gone`, details));
}

// ---------- 聊天室視窗 ----------
// 紀錄與開關的邏輯在 domain/chat-log.js（純邏輯）；這裡只負責視窗：先建好並隱藏，要開的時候用 showInactive（不搶焦點，前景 App 不變）。
// 展開／收合動畫由畫面內容的 CSS 做（視窗本身不動）
const chat = createChat();
let chatWin = null, chatReady = false, chatShown = false, chatHideTimer = null;
let sentRev = -1, sentPartial = null, lastOpen = null;

function chatSize() {
  const saved = loadSettings().chatSize;
  const ok = (v, lo, dflt) => (Number.isFinite(v) ? Math.round(Math.min(CHAT_MAX, Math.max(lo, v))) : dflt);
  return Array.isArray(saved) ? [ok(saved[0], CHAT_MIN_W, CHAT_W), ok(saved[1], CHAT_MIN_H, CHAT_H)] : [CHAT_W, CHAT_H];
}
function createChatWindow() {
  const [width, height] = chatSize();
  chatWin = new BrowserWindow({
    width, height, minWidth: CHAT_MIN_W, minHeight: CHAT_MIN_H, show: false, transparent: true, frame: false, hasShadow: true, resizable: true,
    fullscreenable: false, skipTaskbar: true, type: 'panel', backgroundColor: '#00000000',
    webPreferences: { preload: path.join(HERE, 'preload', 'chat.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  chatWin.setAlwaysOnTop(true, 'floating');
  chatWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  chatWin.on('resized', () => { const [w, h] = chatWin.getSize(); saveSettings({ chatSize: [w, h] }); });
  forwardConsole(chatWin.webContents, 'chat');
  chatWin.loadURL('app://ui/renderer/chat/chat.html');
}
const chatAlive = () => !!chatWin && !chatWin.isDestroyed();
/** 貼在角色視窗旁邊（左邊優先，放不下改右邊），下緣對齊工作區底部；回傳在哪一側（動畫從靠角色的那角長出來） */
function placeChat() {
  const rb = win.getBounds(), wa = screen.getPrimaryDisplay().workArea, [w, h] = chatWin.getSize();
  let side = 'left', x = rb.x - w - CHAT_GAP;
  if (x < wa.x) { side = 'right'; x = rb.x + rb.width + CHAT_GAP; }
  x = Math.round(Math.min(wa.x + wa.width - w, Math.max(wa.x, x)));
  chatWin.setPosition(x, Math.round(Math.max(wa.y, wa.y + wa.height - h - CHAT_MARGIN_BOTTOM)));
  return side;
}
function openChat(auto, since) {
  if (!chatAlive() || !win) return;
  clearTimeout(chatHideTimer);
  const side = placeChat();
  chatShown = true;
  sentRev = -1; pushChatState();                      // 隱藏期間不推紀錄，展開時一次補上
  if (!chatWin.isVisible()) chatWin.showInactive();   // 不搶焦點：前景 App 會決定 front_action、分頁、看畫面的對象
  lastOpen = { auto, showMs: Date.now() - since, paintMs: null };
  if (chatReady) chatWin.webContents.send('chat-show', { auto, side });   // 還沒載入完的話，ready 時補送
}
function hideChatNow() {
  clearTimeout(chatHideTimer);
  if (!chatShown && chatAlive()) chatWin.hide();
}
function closeChat() {
  if (!chatAlive() || !chatShown) return;
  chatShown = false;
  chatWin.webContents.send('chat-hide');
  clearTimeout(chatHideTimer);
  chatHideTimer = setTimeout(hideChatNow, CHAT_HIDE_FALLBACK_MS);
}
function runChat(effects, since = Date.now()) {
  for (const e of effects) {
    if (e.type === 'chat-open') openChat(e.auto, since);
    else if (e.type === 'chat-close') closeChat();
  }
}
/** 紀錄有變才重送整份（上限 200 筆）；只有即時文字變就只送它。聊天室隱藏時不送：它的 renderer 每收到一次就重畫（實測每則結果 20–40 ms CPU） */
function pushChatState() {
  if (!chatAlive() || !chatReady || !chatShown) return;
  const snap = chat.snapshot();
  if (snap.rev !== sentRev) {
    sentRev = snap.rev; sentPartial = snap.partial;
    chatWin.webContents.send('chat-state', { entries: snap.entries, pending: snap.pending, partial: snap.partial });
  } else if (snap.partial !== sentPartial) {
    sentPartial = snap.partial;
    chatWin.webContents.send('chat-live', { partial: snap.partial });
  }
}

// ---------- 與後端的連線 ----------
let bus = null;
let retryMs = 1000;
function connectBus() {
  if (!BUS_URL) return;
  let ws;
  try { ws = new WebSocket(BUS_URL); } catch (e) { console.error('bus connect failed', e); scheduleReconnect(); return; }
  bus = ws;
  ws.addEventListener('open', () => { retryMs = 1000; conn += 1; console.log('bus connected'); });
  ws.addEventListener('message', (ev) => {
    const recv = Date.now();
    let msg;
    try { msg = JSON.parse(String(ev.data)); } catch { return; }
    if (!msg || typeof msg.type !== 'string') return;
    if (msg.type.startsWith('debug_')) { debugHook(msg); return; }
    if (msg.type === 'hello' || msg.type === 'paused') paused = msg.type === 'hello' ? !!msg.paused : !!msg.value;
    if (LOG_PATH) {
      const row = { ts_iso: isoLocal(recv), run_id: RUN_ID, conn, id: msg.id, type: msg.type, bus_ms: Math.round((recv - msg.ts * 1000) * 10) / 10, recv };
      if (msg.type === 'hello') logRow({ ...row, recv: undefined, apply_ms: null });   // 畫面還沒載入，不會有套用時間
      else pendingRows.set(msg.id, row);
    }
    if (win && !win.isDestroyed()) win.webContents.send('bus', msg);
    runChat(chat.handle(msg, recv), recv);
    pushChatState();
  });
  ws.addEventListener('close', () => { if (bus === ws) bus = null; flushPendingRows(); scheduleReconnect(); });
  ws.addEventListener('error', () => {});
}
function scheduleReconnect() {
  setTimeout(connectBus, retryMs);
  retryMs = Math.min(retryMs * 2, 5000);
}
function sendBus(msg) {
  if (!msg || !ALLOWED_SEND.has(msg.type) || !bus || bus.readyState !== 1) return false;
  bus.send(JSON.stringify(msg));
  return true;
}
/** 畫面送來的訊息：成功送到後端的打字指令也記進聊天室 */
function submit(msg) {
  const sent = sendBus(msg);
  if (sent && msg.type === 'command' && typeof msg.text === 'string') { chat.userCommand(msg.text, Date.now()); pushChatState(); }
  return sent;
}

// 開發用除錯鉤子：只有設了 MAC_AI_UI_CAPTURE_DIR 才生效（scripts/ui_demo.py --capture），正式執行時後端送來也不理
const PAGE_STATE = `window.__ui ? ({ snap: window.__ui.snapshot(), poseT: window.__ui.poseT(), motion: window.__ui.activeMotion(), gesture: window.__ui.activeGesture(),
  frames: window.__ui.frames, input: !document.getElementById('input-box').hidden, quick: !document.getElementById('quick').hidden,
  quickBtn: (() => { const b = document.getElementById('quick-chat').getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })(),
  choices: !document.getElementById('choices').hidden, choicesTitle: document.getElementById('choices-title').textContent,
  choiceRows: [...document.querySelectorAll('#choices-list li')].map(li => { const r = li.getBoundingClientRect(), b = li.querySelector('button').getBoundingClientRect();
    return { text: li.textContent, x: r.x + 40, y: r.y + r.height / 2, bx: b.x + b.width / 2, by: b.y + b.height / 2 }; }),
  inputFocused: document.activeElement === document.getElementById('input'), alphaHead: window.__ui.alphaAt(180, window.__ui.topY() + 80),
  alphaCorner: window.__ui.alphaAt(2, 2), perf: window.__ui.perf(), drawables: window.__ui.drawables }) : null`;
async function debugHook(msg) {
  if (!CAPTURE_DIR || !win) return;
  if (msg.type === 'debug_toggle_input') win.webContents.send('toggle-input');
  else if (msg.type === 'debug_chat_toggle') runChat(chat.toggle(Date.now()));
  else if (msg.type === 'debug_chat_interact') chat.interact();
  else if (msg.type === 'debug_chat_submit' && chatAlive()) {   // 在聊天室輸入框填字並送出（走真正的 submit 路徑）
    await chatWin.webContents.executeJavaScript(`(() => { const b = document.getElementById('box'); b.value = ${JSON.stringify(String(msg.text))}; b.dispatchEvent(new Event('input')); document.getElementById('form').requestSubmit(); })()`);
  } else if (msg.type === 'debug_chat_capture' && chatAlive()) {   // 聊天室視窗存成 PNG，並記下可見性、位置、焦點與開啟耗時
    const safe = String(msg.name || 'chat').replace(/[^\w.-]/g, '_');
    const img = await chatWin.webContents.capturePage();
    const page = await chatWin.webContents.executeJavaScript(`({ hidden: document.body.classList.contains('hidden'), rows: document.querySelectorAll('#log .msg').length,
      text: document.getElementById('log').innerText, details: document.querySelectorAll('details.steps').length, live: document.getElementById('live').textContent })`).catch(() => null);
    fs.mkdirSync(CAPTURE_DIR, { recursive: true });
    fs.writeFileSync(path.join(CAPTURE_DIR, `${safe}.png`), img.toPNG());
    fs.writeFileSync(path.join(CAPTURE_DIR, `${safe}.json`), JSON.stringify({ visible: chatWin.isVisible(), focused: chatWin.isFocused(), bounds: chatWin.getBounds(),
      roleBounds: win.getBounds(), chat: chat.snapshot(), shown: chatShown, lastOpen, page }));
  }
  else if (msg.type === 'debug_submit') {   // 在輸入框填字並送出（走真正的 submit 路徑）
    await win.webContents.executeJavaScript(`(() => { document.getElementById('input').value = ${JSON.stringify(String(msg.text))}; document.getElementById('input-box').requestSubmit(); })()`);
  } else if (msg.type === 'debug_click') {  // 對網頁注入一次左鍵點擊（不經作業系統，所以不需要輔助使用權限）
    for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) win.webContents.sendInputEvent({ type, x: msg.x, y: msg.y, button: 'left', clickCount: 1 });
  }
  else if (msg.type === 'debug_trace') {    // 動作軌跡（scripts/ui_motion.py）：start 開始記錄；stop 把記錄存成 <name>.json
    const safe = String(msg.name || 'trace').replace(/[^\w.-]/g, '_');
    if (msg.cmd === 'start') await win.webContents.executeJavaScript(`window.__ui.motionTrace('start')`).catch(() => null);
    else if (msg.cmd === 'stop') {
      const trace = await win.webContents.executeJavaScript(`window.__ui.motionTrace('stop')`).catch(() => null);
      fs.mkdirSync(CAPTURE_DIR, { recursive: true });
      fs.writeFileSync(path.join(CAPTURE_DIR, `${safe}.json`), JSON.stringify(trace));
    }
  }
  else if (msg.type === 'debug_capture') {
    const safe = String(msg.name || 'capture').replace(/[^\w.-]/g, '_');
    const img = await win.webContents.capturePage();
    const state = await win.webContents.executeJavaScript(PAGE_STATE).catch(() => null);
    fs.mkdirSync(CAPTURE_DIR, { recursive: true });
    fs.writeFileSync(path.join(CAPTURE_DIR, `${safe}.png`), img.toPNG());
    fs.writeFileSync(path.join(CAPTURE_DIR, `${safe}.json`), JSON.stringify({ bounds: win.getBounds(), focused: win.isFocused(), ignoring, state }));
  }
}

function quitAll() {
  sendBus({ type: 'quit' });
  setTimeout(() => app.quit(), 300);
}

// ---------- IPC ----------
function setupIpc() {
  ipcMain.handle('config', () => ({ model: MODEL_PATH ? '/model/' + path.basename(MODEL_PATH) : '', winW: WIN_W, winH: WIN_H }));
  ipcMain.on('ready', () => { if (win && !win.isVisible() ) win.showInactive(); });
  ipcMain.on('send', (_e, msg) => { submit(msg); });
  ipcMain.on('toggle-chat', () => runChat(chat.toggle(Date.now())));
  // 聊天室視窗的訊息：只認自己的 webContents；送出的文字驗證後才轉給後端（且只能是 command）
  const fromChat = (e) => chatAlive() && e.sender === chatWin.webContents;
  ipcMain.on('chat:ready', (e) => {
    if (!fromChat(e)) return;
    chatReady = true; sentRev = -1;
    pushChatState();
    if (chatShown) chatWin.webContents.send('chat-show', { auto: lastOpen ? lastOpen.auto : true, side: 'left' });
  });
  ipcMain.on('chat:send', (_e, text) => {
    if (!fromChat(_e) || typeof text !== 'string') return;
    const t = text.trim();
    if (t && [...t].length <= CMD_MAX_CHARS) submit({ type: 'command', text: t });
  });
  ipcMain.on('chat:close', (e) => { if (fromChat(e)) runChat(chat.close()); });
  ipcMain.on('chat:interact', (e) => { if (fromChat(e)) chat.interact(); });
  ipcMain.on('chat:focus', (e) => { if (fromChat(e) && chatShown) chatWin.focus(); });
  ipcMain.on('chat:hidden', (e) => { if (fromChat(e)) hideChatNow(); });
  ipcMain.on('chat:painted', (e, ms) => { if (fromChat(e) && lastOpen && lastOpen.paintMs === null && Number.isFinite(ms)) lastOpen.paintMs = Math.round(ms * 10) / 10; });
  ipcMain.on('set-ignore', (_e, ignore) => {
    if (!win || ignore === ignoring) return;
    ignoring = ignore;
    win.setIgnoreMouseEvents(ignore, { forward: true });
  });
  ipcMain.on('gaze', (_e, on) => { gazeOn = !!on; });
  ipcMain.on('applied', (_e, id, at) => {
    const row = pendingRows.get(id);
    if (!row) return;
    pendingRows.delete(id);
    const { recv, ...rest } = row;
    logRow({ ...rest, apply_ms: Math.round((at - recv) * 10) / 10 });
  });
  ipcMain.on('focus', (_e, on) => {
    if (!win) return;
    if (on) win.focus(); else win.blur();
  });
  ipcMain.on('drag-start', () => { dragStartX = win.getBounds().x; });
  ipcMain.on('drag-move', (_e, dx) => {
    const b = screen.getPrimaryDisplay().bounds;
    win.setPosition(clampX(dragStartX + dx, b), win.getBounds().y);
  });
  ipcMain.on('drag-end', () => saveSettings({ x: win.getBounds().x }));
  ipcMain.on('menu', () => {
    Menu.buildFromTemplate([
      { label: '聊天室', click: () => runChat(chat.toggle(Date.now())) },
      { label: '暫停聽指令', type: 'checkbox', checked: paused, click: (item) => sendBus({ type: 'pause', value: item.checked }) },
      { type: 'separator' },
      { label: '結束', click: quitAll },
    ]).popup({ window: win });
  });
}

app.whenReady().then(() => {
  if (app.dock) app.dock.hide();
  protocol.handle('app', async (request) => {
    const file = resolveAppFile(request.url, ROOTS, LIB_FILES);
    if (!file) return new Response('forbidden', { status: 403 });
    try {
      const body = await fs.promises.readFile(file);
      return new Response(body, { headers: { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' } });
    } catch { return new Response('not found', { status: 404 }); }
  });
  setupIpc();
  createWindow();
  createChatWindow();
  connectBus();
  const shortcut = loadSettings().shortcut || DEFAULT_SHORTCUT;
  if (!globalShortcut.register(shortcut, () => win && win.webContents.send('toggle-input'))) console.error(`快捷鍵 ${shortcut} 無法註冊（可能被其他程式占用）`);
  setInterval(() => {
    if (!gazeOn || !win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint(), b = win.getBounds();
    win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y });
  }, 33);
  screen.on('display-metrics-changed', () => win && placeWindow());
  setInterval(() => { runChat(chat.tick(Date.now())); pushChatState(); }, 500);   // 主動展開沒人理就收回；過期的即時文字清掉
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
