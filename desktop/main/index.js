// Electron 主程序的組裝根：只負責把各模組接起來並啟動。
// 與後端的連線由主程序持有（token 不進畫面程序），事件再轉給畫面。各模組職責：
//   bus 連線與重連｜role-window 角色視窗｜chat-window 聊天室視窗｜ipc 畫面來的訊息｜telemetry ui.jsonl｜debug 開發用鉤子｜app-protocol 檔案白名單
import { app, globalShortcut, protocol } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { UI_MESSAGE_TYPES } from '../shared/contract.js';
import { appRoutes, resolveAppFile } from './app-protocol.js';
import { createBus } from './bus.js';
import { createChatWindow } from './chat-window.js';
import { createDebug } from './debug.js';
import { setupIpc } from './ipc.js';
import { loadSettings } from './settings.js';
import { createRoleWindow } from './role-window.js';
import { createTelemetry } from './telemetry.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');   // desktop/
const BUS_URL = process.env.MAC_AI_UI_URL || '';
const MODEL_PATH = process.env.MAC_AI_UI_MODEL || '';
const LOG_PATH = process.env.MAC_AI_UI_LOG || '';
const RUN_ID = process.env.MAC_AI_RUN_ID || '';   // 後端這次啟動的編號，ui.jsonl 每行帶著，才能和 latency／commands 對起來
const CAPTURE_DIR = process.env.MAC_AI_UI_CAPTURE_DIR || '';   // 開發用：後端送 debug_capture 時把視窗存成 PNG
const DEFAULT_SHORTCUT = 'Alt+Space';
const CHAT_TICK_MS = 500;
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
  '.png': 'image/png', '.css': 'text/css', '.moc3': 'application/octet-stream' };

protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
const { roots: ROOTS, libFiles: LIB_FILES } = appRoutes(ROOT, MODEL_PATH);

const state = { paused: false };   // 後端目前是否暫停聽指令（右鍵選單的勾選用）
const telemetry = createTelemetry({ logPath: LOG_PATH, runId: RUN_ID });
const role = createRoleWindow({ root: ROOT });
const cw = createChatWindow({ root: ROOT, role });
const debug = createDebug({ captureDir: CAPTURE_DIR, role, cw });
const bus = createBus({
  url: BUS_URL,
  allowedTypes: new Set(UI_MESSAGE_TYPES),
  onOpen: () => telemetry.connected(),
  onClose: () => telemetry.flush(),
  onMessage(msg, recv) {
    if (msg.type.startsWith('debug_')) { debug.hook(msg); return; }
    if (msg.type === 'hello' || msg.type === 'paused') state.paused = msg.type === 'hello' ? !!msg.paused : !!msg.value;
    telemetry.record(msg, recv);
    if (role.alive()) role.win.webContents.send('bus', msg);
    cw.handleBus(msg, recv);
  },
});

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
  setupIpc({ role, cw, bus, telemetry, state, modelPath: MODEL_PATH });
  role.create();
  cw.create();
  bus.connect();
  const shortcut = loadSettings().shortcut || DEFAULT_SHORTCUT;
  if (!globalShortcut.register(shortcut, () => { if (role.alive()) role.win.webContents.send('toggle-input'); })) console.error(`快捷鍵 ${shortcut} 無法註冊（可能被其他程式占用）`);
  role.start();
  setInterval(() => cw.tick(), CHAT_TICK_MS);   // 主動展開沒人理就收回；過期的即時文字清掉
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
