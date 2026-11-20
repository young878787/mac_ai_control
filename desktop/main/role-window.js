// 角色視窗：透明、置頂、可穿透點擊、出現在所有桌面空間；位置（水平拖曳）記在 settings。
import { BrowserWindow, screen } from 'electron';
import path from 'node:path';
import { forwardConsole } from './console.js';
import { loadSettings, saveSettings } from './settings.js';

export const WIN_W = 360, WIN_H = 560;
const MARGIN_RIGHT = 24, X_SLACK = 60;
const GAZE_POLL_MS = 33;             // 出場時把游標位置傳給畫面（視線用）的間隔

/** @param {{ root: string }} opts root＝desktop/ */
export function createRoleWindow({ root }) {
  /** @type {BrowserWindow | null} */
  let win = null;
  let ignoring = true;               // 目前是否讓滑鼠穿透
  let dragStartX = 0;
  let gazeOn = false;                // 畫面要求傳游標位置（出場時）

  const alive = () => !!win && !win.isDestroyed();
  function clampX(x, b) { return Math.round(Math.min(b.x + b.width - WIN_W + X_SLACK, Math.max(b.x - X_SLACK, x))); }
  function place() {
    const b = screen.getPrimaryDisplay().bounds;
    const saved = loadSettings().x;
    const x = clampX(Number.isFinite(saved) ? saved : b.x + b.width - WIN_W - MARGIN_RIGHT, b);
    win.setBounds({ x, y: b.y + b.height - WIN_H, width: WIN_W, height: WIN_H });
  }

  return {
    get win() { return win; },
    get ignoring() { return ignoring; },
    alive,
    create() {
      win = new BrowserWindow({
        width: WIN_W, height: WIN_H, show: false, transparent: true, frame: false, hasShadow: false, resizable: false,
        skipTaskbar: true, type: 'panel', backgroundColor: '#00000000', enableLargerThanScreen: true,
        webPreferences: { preload: path.join(root, 'preload', 'role.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true,
          backgroundThrottling: false },
      });
      win.setAlwaysOnTop(true, 'screen-saver');
      win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
      win.setIgnoreMouseEvents(true, { forward: true });
      place();
      forwardConsole(win.webContents, 'renderer');
      win.loadURL('app://ui/renderer/role/index.html');
    },
    place() { if (win) place(); },
    /** 畫面載入完成：顯示（不搶焦點） */
    ready() { if (win && !win.isVisible()) win.showInactive(); },
    setIgnore(ignore) {
      if (!win || ignore === ignoring) return;
      ignoring = ignore;
      win.setIgnoreMouseEvents(ignore, { forward: true });
    },
    setGaze(on) { gazeOn = !!on; },
    /** 輸入框開著時要拿鍵盤焦點，關了就還給前景 App */
    focus(on) {
      if (!win) return;
      if (on) win.focus(); else win.blur();
    },
    dragStart() { dragStartX = win.getBounds().x; },
    dragMove(dx) {
      const b = screen.getPrimaryDisplay().bounds;
      win.setPosition(clampX(dragStartX + dx, b), win.getBounds().y);
    },
    dragEnd() { saveSettings({ x: win.getBounds().x }); },
    /** 開始背景工作：出場時固定間隔把游標位置（相對視窗）傳給畫面；螢幕設定改變時重新擺位 */
    start() {
      setInterval(() => {
        if (!gazeOn || !alive()) return;
        const p = screen.getCursorScreenPoint(), b = win.getBounds();
        win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y });
      }, GAZE_POLL_MS);
      screen.on('display-metrics-changed', () => { if (win) place(); });
    },
  };
}
