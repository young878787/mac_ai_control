// Electron 主程序：透明置頂視窗、自訂協定提供檔案、連到後端事件匯流排、全域快捷鍵與右鍵選單。
// 與後端的連線由主程序持有（token 不進畫面程序），事件再轉給畫面。
import { app, BrowserWindow, Menu, globalShortcut, ipcMain, protocol, screen } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveAppFile } from './protocol.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUS_URL = process.env.MAC_AI_UI_URL || '';
const MODEL_PATH = process.env.MAC_AI_UI_MODEL || '';
const LOG_PATH = process.env.MAC_AI_UI_LOG || '';
const RUN_ID = process.env.MAC_AI_RUN_ID || '';   // 後端這次啟動的編號，ui.jsonl 每行帶著，才能和 latency／commands 對起來
const CAPTURE_DIR = process.env.MAC_AI_UI_CAPTURE_DIR || '';   // 開發用：後端送 debug_capture 時把視窗存成 PNG
const WIN_W = 360, WIN_H = 560, MARGIN_RIGHT = 24, X_SLACK = 60;
const DEFAULT_SHORTCUT = 'Alt+Space';
const ALLOWED_SEND = new Set(['command', 'wake', 'pause', 'quit', 'choose']);
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.png': 'image/png', '.css': 'text/css', '.moc3': 'application/octet-stream' };

protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

// ---------- 自訂協定：只開放這幾個根目錄與兩個函式庫檔 ----------
const ROOTS = [['/renderer/', path.join(HERE, 'renderer')], ['/vendor/', path.join(HERE, 'vendor')],
  ['/model/', MODEL_PATH ? path.dirname(MODEL_PATH) : path.join(HERE, 'no-model')]];
const LIB_FILES = {
  '/state.js': path.join(HERE, 'state.js'),
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
    webPreferences: { preload: path.join(HERE, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true,
      backgroundThrottling: false },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true, { forward: true });
  placeWindow();
  win.webContents.on('console-message', (event) => {
    const { level, message } = event;
    if (message.startsWith('Electron Security Warning')) return;  // 只有開發模式才有；CSP 是刻意設的
    if (level === 'error' || level === 'warning' || /^\[ui\]/.test(message)) console.log(`[renderer ${level}] ${message} (${event.sourceId}:${event.lineNumber})`);
  });
  win.webContents.on('render-process-gone', (_e, details) => console.error('renderer gone', details));
  win.loadURL('app://ui/renderer/index.html');
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

// 開發用除錯鉤子：只有設了 MAC_AI_UI_CAPTURE_DIR 才生效（scripts/ui_demo.py --capture），正式執行時後端送來也不理
const PAGE_STATE = `window.__ui ? ({ snap: window.__ui.snapshot(), poseT: window.__ui.poseT(), motion: window.__ui.activeMotion(),
  frames: window.__ui.frames, input: !document.getElementById('input-box').hidden, history: !document.getElementById('history').hidden,
  choices: !document.getElementById('choices').hidden, choicesTitle: document.getElementById('choices-title').textContent,
  choiceRows: [...document.querySelectorAll('#choices-list li')].map(li => { const r = li.getBoundingClientRect(), b = li.querySelector('button').getBoundingClientRect();
    return { text: li.textContent, x: r.x + 40, y: r.y + r.height / 2, bx: b.x + b.width / 2, by: b.y + b.height / 2 }; }),
  inputFocused: document.activeElement === document.getElementById('input'), alphaHead: window.__ui.alphaAt(180, window.__ui.topY() + 80),
  alphaCorner: window.__ui.alphaAt(2, 2), perf: window.__ui.perf(), drawables: window.__ui.drawables }) : null`;
async function debugHook(msg) {
  if (!CAPTURE_DIR || !win) return;
  if (msg.type === 'debug_toggle_input') win.webContents.send('toggle-input');
  else if (msg.type === 'debug_toggle_history') win.webContents.send('toggle-history');
  else if (msg.type === 'debug_submit') {   // 在輸入框填字並送出（走真正的 submit 路徑）
    await win.webContents.executeJavaScript(`(() => { document.getElementById('input').value = ${JSON.stringify(String(msg.text))}; document.getElementById('input-box').requestSubmit(); })()`);
  } else if (msg.type === 'debug_click') {  // 對網頁注入一次左鍵點擊（不經作業系統，所以不需要輔助使用權限）
    for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) win.webContents.sendInputEvent({ type, x: msg.x, y: msg.y, button: 'left', clickCount: 1 });
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
  ipcMain.on('send', (_e, msg) => { sendBus(msg); });
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
      { label: '對話紀錄', click: () => win.webContents.send('toggle-history') },
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
  connectBus();
  const shortcut = loadSettings().shortcut || DEFAULT_SHORTCUT;
  if (!globalShortcut.register(shortcut, () => win && win.webContents.send('toggle-input'))) console.error(`快捷鍵 ${shortcut} 無法註冊（可能被其他程式占用）`);
  setInterval(() => {
    if (!gazeOn || !win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint(), b = win.getBounds();
    win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y });
  }, 33);
  screen.on('display-metrics-changed', () => win && placeWindow());
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
