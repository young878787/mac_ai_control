// eslint-rules/ 自訂規則：每條用合規／違規案例驗證，確保規則本身有在檢查；最後確認 eslint.config.js 真的有套用到對應的目錄。
import test, { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint, RuleTester } from 'eslint';
import local from '../eslint-rules/index.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

RuleTester.describe = describe;
RuleTester.it = it;
RuleTester.itOnly = it.only;
const tester = new RuleTester({ languageOptions: { ecmaVersion: 'latest', sourceType: 'module' } });
const rule = (name) => local.rules[name];

tester.run('no-window-show', rule('no-window-show'), {
  valid: [
    'win.showInactive();',
    'if (!win.isVisible()) win.showInactive();',
    'win.hide(); win.focus();',
    "const o = { show: 1 }; o['show'];",
  ],
  invalid: [
    { code: 'win.show();', errors: [{ messageId: 'useInactive' }] },
    { code: 'role.win.show(true);', errors: [{ messageId: 'useInactive' }] },
    { code: 'this.chat.win.show(); other.show();', errors: [{ messageId: 'useInactive' }, { messageId: 'useInactive' }] },
  ],
});

const SAFE = 'contextIsolation: true, nodeIntegration: false, sandbox: true';
tester.run('secure-web-preferences', rule('secure-web-preferences'), {
  valid: [
    `new BrowserWindow({ webPreferences: { preload: p, ${SAFE} } });`,
    `new BrowserWindow({ width: 1, webPreferences: { ${SAFE}, backgroundThrottling: false } });`,
    "const o = { prefs: { nodeIntegration: true } };",                                 // 不是 webPreferences
  ],
  invalid: [
    { code: 'new BrowserWindow({ webPreferences: { preload: p } });', errors: 3 },
    { code: 'new BrowserWindow({ webPreferences: { contextIsolation: true, nodeIntegration: true, sandbox: true } });',
      errors: [{ messageId: 'wrong', data: { key: 'nodeIntegration', value: 'false' } }] },
    { code: 'new BrowserWindow({ webPreferences: { contextIsolation: true, nodeIntegration: false } });',
      errors: [{ messageId: 'missing', data: { key: 'sandbox', value: 'true' } }] },
    { code: 'new BrowserWindow({ webPreferences: { contextIsolation: flag, nodeIntegration: false, sandbox: true } });',
      errors: [{ messageId: 'wrong' }] },
    { code: 'new BrowserWindow({ webPreferences: prefs });', errors: [{ messageId: 'notObject' }] },
    { code: `new BrowserWindow({ webPreferences: { ...base, contextIsolation: true, nodeIntegration: false } });`,
      errors: [{ messageId: 'missing' }] },
    { code: `new BrowserWindow({ 'webPreferences': { ${SAFE.replace('sandbox: true', "'sandbox': false")} } });`,
      errors: [{ messageId: 'wrong', data: { key: 'sandbox', value: 'true' } }] },
  ],
});

tester.run('ipc-channel-literal', rule('ipc-channel-literal'), {
  valid: [
    "ipcMain.on('chat:send', (e, t) => {});",
    "ipcMain.handle('config', () => ({}));",
    "ipcRenderer.send('ready'); ipcRenderer.invoke('config');",
    "win.webContents.send('chat-show', { auto });",
    'ipcRenderer.on(channel, cb);',                                                   // 預載腳本的 on() 輔助函式，由 ipc-channels.test.js 掃
    'bus.send({ type: "quit" }); ws.send(JSON.stringify(msg));',                       // 不是 IPC
    'win.webContents.sendInputEvent({ type });',
  ],
  invalid: [
    { code: 'ipcMain.on(CHANNEL, () => {});', errors: [{ messageId: 'literal' }] },
    { code: 'ipcRenderer.send(`chat:${name}`);', errors: [{ messageId: 'literal' }] },
    { code: 'ipcRenderer.invoke("config");', errors: [{ messageId: 'literal' }] },
    { code: "role.win.webContents.send(name, msg);", errors: [{ messageId: 'literal' }] },
    { code: 'ipcMain.handleOnce();', errors: [{ messageId: 'literal' }] },
    { code: 'webContents.send(1);', errors: [{ messageId: 'literal' }] },
  ],
});

tester.run('bus-loopback-only', rule('bus-loopback-only'), {
  valid: [
    "const u = 'ws://127.0.0.1:8766/?token=x';",
    "fetch('http://127.0.0.1:8766/health');",
    'const u = `ws://127.0.0.1:${port}/?token=${token}`;',
    "const s = 'app://ui/renderer/role/app.js';",
    "const t = '不是網址 ws 的字串';",
  ],
  invalid: [
    { code: "const u = 'ws://localhost:8766';", errors: [{ messageId: 'loopback' }] },
    { code: "new WebSocket('wss://example.com/bus');", errors: [{ messageId: 'loopback' }] },
    { code: "fetch('https://api.example.com');", errors: [{ messageId: 'loopback' }] },
    { code: "const u = 'http://127.0.0.1.evil.com/';", errors: [{ messageId: 'loopback' }] },
    { code: 'const u = `ws://${host}:8766`;', errors: [{ messageId: 'loopback' }] },
    { code: "const u = 'HTTP://0.0.0.0:80';", errors: [{ messageId: 'loopback' }] },
  ],
});

test('eslint.config.js 有套用自訂規則：main/ 擋 .show()，其他目錄不擋；各目錄都擋非本機網址', async () => {
  const eslint = new ESLint({ cwd: ROOT });
  const ids = async (code, file) => (await eslint.lintText(code, { filePath: path.join(ROOT, file) }))[0].messages.map(m => m.ruleId);
  assert.deepEqual(await ids('export const f = (win) => win.show();\n', 'main/x.js'), ['local/no-window-show']);
  assert.deepEqual(await ids('export const f = (el) => el.show();\n', 'renderer/role/x.js'), []);
  for (const file of ['main/x.js', 'renderer/role/x.js', 'domain/x.js']) {
    assert.deepEqual(await ids("export const u = 'ws://example.com';\n", file), ['local/bus-loopback-only'], file);
  }
  assert.deepEqual(await ids("const { ipcRenderer } = require('electron');\nipcRenderer.send(\"ready\");\n", 'preload/x.cjs'), ['local/ipc-channel-literal']);
  // 純邏輯層沒有環境全域變數
  assert.deepEqual(await ids('export const w = window.innerWidth;\n', 'domain/x.js'), ['no-undef']);
});
