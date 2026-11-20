// 使用者設定（userData/settings.json）：角色視窗位置、聊天室大小、快捷鍵。檔案不存在或壞掉一律當空設定。
import { app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';

const settingsPath = () => path.join(app.getPath('userData'), 'settings.json');

export function loadSettings() {
  try { return JSON.parse(fs.readFileSync(settingsPath(), 'utf8')); } catch { return {}; }
}

export function saveSettings(patch) {
  const next = { ...loadSettings(), ...patch };
  try { fs.mkdirSync(path.dirname(settingsPath()), { recursive: true }); fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2)); } catch (e) { console.error('settings save failed', e); }
}
