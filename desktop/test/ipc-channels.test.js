// IPC 頻道兩邊對應：預載腳本（sandbox，不能共用常數檔）與主程序各寫一次頻道名稱，這裡掃原始碼確認一一對應。
// 打錯一個字不會有任何錯誤，只是收不到訊息，所以用測試擋。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8').replace(/\/\*[\s\S]*?\*\/|(^|[^:])\/\/.*$/gm, '$1');
const collect = (files, re) => new Set(files.flatMap(f => [...read(f).matchAll(re)].map(m => m[1])));
const diff = (a, b) => [...a].filter(x => !b.has(x)).sort();

const PRELOADS = ['preload/role.cjs', 'preload/chat.cjs'];
const MAINS = fs.readdirSync(path.join(ROOT, 'main')).filter(f => f.endsWith('.js')).map(f => `main/${f}`);

const toMain = collect(PRELOADS, /ipcRenderer\.(?:send|invoke)\(\s*'([^']+)'/g);   // 畫面 → 主程序
const handled = collect(MAINS, /ipcMain\.(?:on|handle)\(\s*'([^']+)'/g);
const toRenderer = collect(MAINS, /webContents\.send\(\s*'([^']+)'/g);              // 主程序 → 畫面
const listened = collect(PRELOADS, /\bon\(\s*'([^']+)'\s*\)/g);

test('掃得到頻道（避免正規表示式失效造成空集合比空集合）', () => {
  assert.ok(toMain.size >= 10, `畫面→主程序只掃到 ${toMain.size} 個`);
  assert.ok(toRenderer.size >= 5, `主程序→畫面只掃到 ${toRenderer.size} 個`);
});

test('畫面送出的每個頻道，主程序都有處理，反之亦然', () => {
  assert.deepEqual(diff(toMain, handled), [], '預載腳本送出、但主程序沒處理');
  assert.deepEqual(diff(handled, toMain), [], '主程序處理、但沒有預載腳本會送');
});

test('主程序推送的每個頻道，預載腳本都有接，反之亦然', () => {
  assert.deepEqual(diff(toRenderer, listened), [], '主程序推送、但預載腳本沒接');
  assert.deepEqual(diff(listened, toRenderer), [], '預載腳本接收、但主程序不會推');
});

test('兩個預載腳本互不重疊：聊天室的頻道都有 chat 前綴或 chat- 前綴，角色視窗的沒有', () => {
  const role = collect(['preload/role.cjs'], /ipcRenderer\.(?:send|invoke)\(\s*'([^']+)'/g);
  const chat = collect(['preload/chat.cjs'], /ipcRenderer\.(?:send|invoke)\(\s*'([^']+)'/g);
  assert.deepEqual([...role].filter(c => chat.has(c)), []);
  assert.deepEqual([...chat].filter(c => !c.startsWith('chat:')), [], '聊天室預載腳本只能送 chat: 開頭的頻道');
});
