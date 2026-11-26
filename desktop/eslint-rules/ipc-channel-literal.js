// IPC 頻道名稱必須是單引號字串常值：test/ipc-channels.test.js 用正規表示式掃 '頻道' 確認兩邊一一對應，
// 用變數、樣板字串或雙引號的話測試掃不到，打錯字也不會有任何錯誤。
// 預載腳本的 ipcRenderer.on(channel, ...) 包在 on('頻道') 輔助函式裡，由測試掃 on('...')，這裡不管。

const CHECKED = {
  ipcMain: new Set(['on', 'once', 'handle', 'handleOnce']),
  ipcRenderer: new Set(['send', 'sendSync', 'invoke']),
  webContents: new Set(['send']),
};

/** a.b.c → 'b'（callee 物件的最後一段名稱） */
function objectName(obj) {
  if (obj.type === 'Identifier') return obj.name;
  if (obj.type === 'MemberExpression' && !obj.computed && obj.property.type === 'Identifier') return obj.property.name;
  return null;
}

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: 'ipcMain／ipcRenderer／webContents.send 的頻道名稱必須是單引號字串常值' },
    schema: [],
    messages: { literal: "IPC 頻道名稱要寫成單引號字串常值（例如 'chat:send'），test/ipc-channels.test.js 才掃得到" },
  },
  create(context) {
    return {
      CallExpression(node) {
        const c = node.callee;
        if (c.type !== 'MemberExpression' || c.computed || c.property.type !== 'Identifier') return;
        const methods = CHECKED[objectName(c.object)];
        if (!methods || !methods.has(c.property.name)) return;
        const arg = node.arguments[0];
        const ok = arg && arg.type === 'Literal' && typeof arg.value === 'string' && arg.raw.startsWith("'");
        if (!ok) context.report({ node: arg || node, messageId: 'literal' });
      },
    };
  },
};
