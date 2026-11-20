// 開發用除錯鉤子（scripts/ui_demo.py --capture、ui_motion.py）：後端送 debug_* 訊息，這裡存截圖與狀態、注入點擊與輸入。
// 只有設了 MAC_AI_UI_CAPTURE_DIR 才生效，正式執行時後端送來也不理。
// 讀畫面狀態一律呼叫畫面自己暴露的 window.__ui／window.__chat（只讀探針），不在這裡拼 DOM 選擇器：畫面改 id 時不會悄悄壞掉。
import fs from 'node:fs';
import path from 'node:path';

const safeName = (name, fallback) => String(name || fallback).replace(/[^\w.-]/g, '_');

/**
 * @param {{ captureDir: string,
 *           role: ReturnType<typeof import('./role-window.js').createRoleWindow>,
 *           cw: ReturnType<typeof import('./chat-window.js').createChatWindow> }} deps
 */
export function createDebug({ captureDir, role, cw }) {
  const write = (name, ext, data) => {
    fs.mkdirSync(captureDir, { recursive: true });
    fs.writeFileSync(path.join(captureDir, `${name}.${ext}`), data);
  };

  return {
    async hook(msg) {
      const win = role.win;
      if (!captureDir || !win) return;
      if (msg.type === 'debug_toggle_input') win.webContents.send('toggle-input');
      else if (msg.type === 'debug_chat_toggle') cw.toggle();
      else if (msg.type === 'debug_chat_interact') cw.interact();
      else if (msg.type === 'debug_chat_submit' && cw.alive()) {   // 在聊天室輸入框填字並送出（走真正的 submit 路徑）
        await cw.win.webContents.executeJavaScript(`window.__chat.submit(${JSON.stringify(String(msg.text))})`);
      } else if (msg.type === 'debug_chat_capture' && cw.alive()) {   // 聊天室視窗存成 PNG，並記下可見性、位置、焦點與開啟耗時
        const safe = safeName(msg.name, 'chat');
        const img = await cw.win.webContents.capturePage();
        const page = await cw.win.webContents.executeJavaScript('window.__chat.probe()').catch(() => null);
        write(safe, 'png', img.toPNG());
        write(safe, 'json', JSON.stringify({ visible: cw.win.isVisible(), focused: cw.win.isFocused(), bounds: cw.win.getBounds(),
          roleBounds: win.getBounds(), chat: cw.chat.snapshot(), shown: cw.shown, lastOpen: cw.lastOpen, page }));
      } else if (msg.type === 'debug_submit') {   // 在輸入框填字並送出（走真正的 submit 路徑）
        await win.webContents.executeJavaScript(`window.__ui.submitText(${JSON.stringify(String(msg.text))})`);
      } else if (msg.type === 'debug_click') {   // 對網頁注入一次左鍵點擊（不經作業系統，所以不需要輔助使用權限）
        /** @type {('mouseMove' | 'mouseDown' | 'mouseUp')[]} */
        const events = ['mouseMove', 'mouseDown', 'mouseUp'];
        for (const type of events) win.webContents.sendInputEvent({ type, x: msg.x, y: msg.y, button: 'left', clickCount: 1 });
      } else if (msg.type === 'debug_trace') {   // 動作軌跡（scripts/ui_motion.py）：start 開始記錄；stop 把記錄存成 <name>.json
        const safe = safeName(msg.name, 'trace');
        if (msg.cmd === 'start') await win.webContents.executeJavaScript(`window.__ui.motionTrace('start')`).catch(() => null);
        else if (msg.cmd === 'stop') {
          const trace = await win.webContents.executeJavaScript(`window.__ui.motionTrace('stop')`).catch(() => null);
          write(safe, 'json', JSON.stringify(trace));
        }
      } else if (msg.type === 'debug_capture') {
        const safe = safeName(msg.name, 'capture');
        const img = await win.webContents.capturePage();
        const state = await win.webContents.executeJavaScript('window.__ui ? window.__ui.probe() : null').catch(() => null);
        write(safe, 'png', img.toPNG());
        write(safe, 'json', JSON.stringify({ bounds: win.getBounds(), focused: win.isFocused(), ignoring: role.ignoring, state }));
      }
    },
  };
}
