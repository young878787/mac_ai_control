// ESLint（flat config）：基底 js.configs.recommended，全域變數依層給；自訂規則在 eslint-rules/。
// 型別交給 npm run typecheck（TypeScript 7 沒有 JS compiler API，不用 typescript-eslint）；分層規則交給 test/layers.test.js。
import js from '@eslint/js';
import globals from 'globals';
import local from './eslint-rules/index.js';

/** 畫面程序的全域變數：<script> 載入的套件與 preload 橋接（型別見 types/globals.d.ts） */
const RENDERER_GLOBALS = { PIXI: 'readonly', Live2DCubismCore: 'readonly', desktop: 'readonly', chat: 'readonly' };

export default [
  { ignores: ['node_modules/', 'vendor/'] },
  js.configs.recommended,
  {
    plugins: { local },
    languageOptions: { ecmaVersion: 'latest', sourceType: 'module' },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none', ignoreRestSiblings: true }],
      'local/bus-loopback-only': 'error',
      'local/ipc-channel-literal': 'error',
      'local/secure-web-preferences': 'error',
    },
  },
  // 主程序、開發工具、測試、lint 設定：Node
  { files: ['main/**', 'dev/**', 'test/**', 'eslint-rules/**', 'eslint.config.js'], languageOptions: { globals: globals.node } },
  { files: ['main/**'], rules: { 'local/no-window-show': 'error' } },
  // 前端不寫死模型：模型裡的名稱只在 rig/ 寫（ADR 0034）
  { files: ['renderer/**', 'domain/**', 'dev/**'], rules: { 'local/model-agnostic': 'error' } },
  // 預載腳本：CommonJS（sandbox 下的 require）
  { files: ['preload/**/*.cjs'], languageOptions: { sourceType: 'commonjs', globals: globals.node } },
  // 畫面程序：瀏覽器＋全域載入的套件
  { files: ['renderer/**'], languageOptions: { globals: { ...globals.browser, ...RENDERER_GLOBALS } } },
  // shared／domain／rig 是純邏輯層，不給任何環境全域變數（碰到 window、process 會報 no-undef）
];
