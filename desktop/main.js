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
const CAPTURE_DIR = process.env.MAC_AI_UI_CAPTURE_DIR || '';   // 開發用：後端送 debug_capture 時把視窗存成 PNG
const WIN_W = 360, WIN_H = 560, MARGIN_RIGHT = 24, X_SLACK = 60;
const DEFAULT_SHORTCUT = 'Alt+Space';
const ALLOWED_SEND = new Set(['command', 'wake', 'pause', 'quit']);
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
function logRow(row) {
  if (!LOG_PATH) return;
  fs.appendFile(LOG_PATH, JSON.stringify(row) + '\n', () => {});
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
  ws.addEventListener('open', () => { retryMs = 1000; console.log('bus connected'); });
  ws.addEventListener('message', (ev) => {
    const recv = Date.now();
    let msg;
    try { msg = JSON.parse(String(ev.data)); } catch { return; }
    if (!msg || typeof msg.type !== 'string') return;
    if (msg.type.startsWith('debug_')) { debugHook(msg); return; }
    if (msg.type === 'hello' || msg.type === 'paused') paused = msg.type === 'hello' ? !!msg.paused : !!msg.value;
    logRow({ ev: 'recv', id: msg.id, type: msg.type, ts: msg.ts, recv: recv / 1000 });
    if (win && !win.isDestroyed()) win.webContents.send('bus', msg);
  });
  ws.addEventListener('close', () => { if (bus === ws) bus = null; scheduleReconnect(); });
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
  ipcMain.on('applied', (_e, id, at) => logRow({ ev: 'applied', id, applied: at / 1000 }));
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
