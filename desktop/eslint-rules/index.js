// 本專案的 ESLint 自訂規則（plugin 名稱 local）：把 AGENTS.md 紅線中能靜態檢查的部分變成 lint。測試在 test/eslint-rules.test.js。
import busLoopbackOnly from './bus-loopback-only.js';
import ipcChannelLiteral from './ipc-channel-literal.js';
import noWindowShow from './no-window-show.js';
import secureWebPreferences from './secure-web-preferences.js';

export default {
  meta: { name: 'local' },
  rules: {
    'bus-loopback-only': busLoopbackOnly,
    'ipc-channel-literal': ipcChannelLiteral,
    'no-window-show': noWindowShow,
    'secure-web-preferences': secureWebPreferences,
  },
};
