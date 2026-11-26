// 分層規則：每個檔案只能 import 規定的層，純邏輯層不能碰 DOM／Electron／Node。規則表見 docs/plans/completed/2026-11-20-desktop-layering.md §1。
// 掃原始碼（去掉註解後）；checkFile 也拿故意違規的字串驗證，確保檢查本身有在檢查。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** 路徑前綴 → 層（先比對長的）。沒對到的檔案算違規：新檔要先決定屬於哪一層 */
const LAYER_DIRS = [
  ['renderer/engine/', 'engine'], ['renderer/animation/', 'animation'], ['renderer/role/', 'view'], ['renderer/chat/', 'view'],
  ['shared/', 'shared'], ['rig/', 'rig'], ['domain/', 'domain'], ['main/', 'main'], ['preload/', 'preload'], ['dev/', 'dev'],
];
/** 每層可以 import 的其他層（自己這一層一律可以） */
const MAY_IMPORT = {
  shared: [], rig: [], domain: ['shared'],
  engine: ['rig'], animation: ['engine', 'rig'],
  view: ['shared', 'domain', 'rig', 'engine', 'animation'],
  main: ['domain', 'shared'], preload: [], dev: ['domain', 'shared', 'rig'],
};
const PURE = new Set(['shared', 'rig', 'domain']);                    // 純資料／純邏輯：不碰 DOM、PIXI、Node、Electron
const NO_DOM = new Set(['shared', 'rig', 'domain', 'engine', 'animation']);   // 畫面行程裡只有 view 可以碰 DOM
const NODE_OK = new Set(['main', 'dev']);                              // 可以 import node:*
const ELECTRON_OK = new Set(['main', 'preload']);

const stripComments = (src) => src.replace(/\/\*[\s\S]*?\*\/|(^|[^:\\])\/\/.*$/gm, '$1');
const layerOf = (rel) => (LAYER_DIRS.find(([prefix]) => rel.startsWith(prefix)) || [])[1] || null;

function importsOf(code) {
  const out = [];
  for (const re of [/\b(?:import|export)\s[^;'"]*?\bfrom\s*['"]([^'"]+)['"]/g, /\bimport\s*['"]([^'"]+)['"]/g, /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g,
    /\brequire\(\s*['"]([^'"]+)['"]\s*\)/g]) for (const m of code.matchAll(re)) out.push(m[1]);
  return out;
}

/** 回傳違規說明的陣列（空陣列＝合規）。rel＝相對於 desktop/ 的路徑 */
export function checkFile(rel, source) {
  const layer = layerOf(rel);
  if (!layer) return [`${rel}：不屬於任何層（要放進 ${LAYER_DIRS.map(d => d[0]).join('、')} 其中之一）`];
  const code = stripComments(source);
  const bad = [];
  for (const spec of importsOf(code)) {
    if (spec.startsWith('.')) {
      const target = path.posix.normalize(path.posix.join(path.posix.dirname(rel), spec));
      const to = layerOf(target);
      if (!to) bad.push(`${rel}：import '${spec}' 指到層以外的位置（${target}）`);
      else if (to !== layer && !MAY_IMPORT[layer].includes(to)) bad.push(`${rel}（${layer}）不可以 import ${to} 層：'${spec}'`);
    } else if (spec.startsWith('node:')) {
      if (!NODE_OK.has(layer)) bad.push(`${rel}（${layer}）不可以 import ${spec}`);
    } else if (spec === 'electron' || spec.startsWith('electron/')) {
      if (!ELECTRON_OK.has(layer)) bad.push(`${rel}（${layer}）不可以 import electron`);
    } else bad.push(`${rel}：不明的套件 '${spec}'（畫面套件以全域載入，這裡不 import）`);
  }
  if (NO_DOM.has(layer) && /\b(document|window|localStorage|sessionStorage|navigator)\b/.test(code)) bad.push(`${rel}（${layer}）不可以碰 DOM／瀏覽器全域`);
  if (PURE.has(layer) && /\b(PIXI|Live2DCubismCore|requestAnimationFrame|ipcRenderer|ipcMain|process)\b/.test(code)) bad.push(`${rel}（${layer}）不可以碰 PIXI／Cubism／Electron／process`);
  return bad;
}

function listSources(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    const rel = dir ? `${dir}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (['node_modules', 'vendor', 'test', 'eslint-rules'].includes(entry.name)) continue;   // eslint-rules：lint 工具，不是程式的一層
      out.push(...listSources(rel));
    } else if (/\.(c|m)?js$/.test(entry.name) && rel !== 'eslint.config.js') out.push(rel);
  }
  return out;
}

test('desktop/ 底下每個程式檔都符合分層規則', () => {
  const files = listSources('');
  assert.ok(files.length >= 15, `只掃到 ${files.length} 個檔案，掃描可能失效`);
  const bad = files.flatMap(rel => checkFile(rel, fs.readFileSync(path.join(ROOT, rel), 'utf8')));
  assert.deepEqual(bad, []);
});

test('檢查本身有效：故意違規的來源會被抓到', () => {
  const cases = [
    ['domain/x.js', "import { a } from '../renderer/engine/stage.js';", 'import engine'],
    ['domain/x.js', "import { a } from '../main/bus.js';", 'import main'],
    ['domain/x.js', "import fs from 'node:fs';", 'node:'],
    ['domain/x.js', "export const h = () => document.getElementById('a');", 'DOM'],
    ['domain/x.js', "const m = new PIXI.Container();", 'PIXI'],
    ['domain/x.js', "import { app } from 'electron';", 'electron'],
    ['shared/x.js', "import { a } from '../domain/character.js';", 'import domain'],
    ['rig/x.js', "import { a } from '../shared/contract.js';", 'import shared'],
    ['renderer/engine/x.js', "import { a } from '../../domain/character.js';", 'import domain'],
    ['renderer/engine/x.js', "window.addEventListener('x', f);", 'DOM'],
    ['renderer/animation/x.js', "import { a } from '../../domain/character.js';", 'import domain'],
    ['renderer/role/x.js', "import { a } from '../../main/bus.js';", 'import main'],
    ['renderer/role/x.js', "import fs from 'node:fs';", 'node:'],
    ['main/x.js', "import { a } from '../renderer/role/app.js';", 'import view'],
    ['main/x.js', "import { a } from '../rig/fullrig03.js';", 'import rig'],
    ['dev/x.js', "import { a } from '../main/bus.js';", 'import main'],
    ['preload/x.cjs', "const { a } = require('../domain/character.js');", 'import domain'],
    ['stray.js', "export const a = 1;", '不屬於任何層'],
    ['domain/x.js', "import {\n  a,\n  b,\n} from '../renderer/engine/stage.js';", 'import engine'],
    ['domain/x.js', "export * from '../main/bus.js';", 'import main'],
  ];
  for (const [rel, src, expect] of cases) {
    const got = checkFile(rel, src);
    assert.ok(got.length > 0 && got.some(m => m.includes(expect)), `${rel}：「${src}」應該違規（${expect}），得到 ${JSON.stringify(got)}`);
  }
});

test('檢查本身有效：合規的來源不會誤報', () => {
  const cases = [
    ['domain/x.js', "import { a } from '../shared/contract.js';\nimport { b } from './character.js';"],
    ['domain/x.js', "// document window PIXI 只出現在註解\n/* import fs from 'node:fs'; */\nexport const a = 1;"],
    ['renderer/engine/x.js', "import { a } from '../../rig/fullrig03.js';\nexport const s = new PIXI.Container();"],
    ['renderer/animation/x.js', "import { a } from '../engine/stage.js';\nimport { b } from '../../rig/fullrig03.js';"],
    ['renderer/role/app.js', "import { a } from '../../domain/character.js';\nimport { b } from '../engine/stage.js';\nconst x = document.body;"],
    ['renderer/chat/chat-app.js', "const x = document.body; window.chat.ready();"],
    ['main/index.js', "import fs from 'node:fs';\nimport { app } from 'electron';\nimport { a } from '../domain/chat-log.js';\nimport { b } from '../shared/contract.js';"],
    ['dev/x.js', "import fs from 'node:fs';\nimport { a } from '../domain/character.js';"],
    ['preload/role.cjs', "const { contextBridge, ipcRenderer } = require('electron');"],
    ['domain/x.js', "const url = 'app://ui/domain/x.js';"],
  ];
  for (const [rel, src] of cases) assert.deepEqual(checkFile(rel, src), [], rel);
});
