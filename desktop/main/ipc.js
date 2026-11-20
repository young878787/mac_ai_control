// 畫面行程 → 主程序的 IPC。頻道名稱只在這裡與 preload/*.cjs 出現（sandbox 的預載腳本不能共用常數檔，test/ipc-channels.test.js 檢查兩邊一致）。
// 聊天室視窗的訊息只認自己的 webContents；送出的文字驗證後才轉給後端（且只能是 command）。
import { app, ipcMain, Menu } from 'electron';
import path from 'node:path';
import { COMMAND_MAX_CHARS } from '../shared/contract.js';
import { WIN_H, WIN_W } from './role-window.js';

/**
 * @param {{ role: ReturnType<typeof import('./role-window.js').createRoleWindow>,
 *           cw: ReturnType<typeof import('./chat-window.js').createChatWindow>,
 *           bus: { send(msg: any): boolean },
 *           telemetry: ReturnType<typeof import('./telemetry.js').createTelemetry>,
 *           state: { paused: boolean },
 *           modelPath: string }} deps
 */
export function setupIpc({ role, cw, bus, telemetry, state, modelPath }) {
  /** 畫面送來的訊息：成功送到後端的打字指令也記進聊天室 */
  function submit(msg) {
    const sent = bus.send(msg);
    if (sent && msg.type === 'command' && typeof msg.text === 'string') cw.userCommand(msg.text);
    return sent;
  }
  function quitAll() {
    bus.send({ type: 'quit' });
    setTimeout(() => app.quit(), 300);
  }

  // ---------- 角色視窗 ----------
  ipcMain.handle('config', () => ({ model: modelPath ? '/model/' + path.basename(modelPath) : '', winW: WIN_W, winH: WIN_H }));
  ipcMain.on('ready', () => role.ready());
  ipcMain.on('send', (_e, msg) => { submit(msg); });
  ipcMain.on('toggle-chat', () => cw.toggle());
  ipcMain.on('set-ignore', (_e, ignore) => role.setIgnore(ignore));
  ipcMain.on('gaze', (_e, on) => role.setGaze(on));
  ipcMain.on('applied', (_e, id, at) => telemetry.applied(id, at));
  ipcMain.on('focus', (_e, on) => role.focus(on));
  ipcMain.on('drag-start', () => role.dragStart());
  ipcMain.on('drag-move', (_e, dx) => role.dragMove(dx));
  ipcMain.on('drag-end', () => role.dragEnd());
  ipcMain.on('menu', () => {
    Menu.buildFromTemplate([
      { label: '聊天室', click: () => cw.toggle() },
      { label: '暫停聽指令', type: 'checkbox', checked: state.paused, click: (item) => bus.send({ type: 'pause', value: item.checked }) },
      { type: 'separator' },
      { label: '結束', click: quitAll },
    ]).popup({ window: role.win });
  });

  // ---------- 聊天室視窗 ----------
  const fromChat = (e) => cw.isSender(e.sender);
  ipcMain.on('chat:ready', (e) => { if (fromChat(e)) cw.onReady(); });
  ipcMain.on('chat:send', (e, text) => {
    if (!fromChat(e) || typeof text !== 'string') return;
    const t = text.trim();
    if (t && [...t].length <= COMMAND_MAX_CHARS) submit({ type: 'command', text: t });
  });
  ipcMain.on('chat:close', (e) => { if (fromChat(e)) cw.requestClose(); });
  ipcMain.on('chat:interact', (e) => { if (fromChat(e)) cw.interact(); });
  ipcMain.on('chat:focus', (e) => { if (fromChat(e)) cw.focus(); });
  ipcMain.on('chat:hidden', (e) => { if (fromChat(e)) cw.hidden(); });
  ipcMain.on('chat:painted', (e, ms) => { if (fromChat(e)) cw.painted(ms); });
}
