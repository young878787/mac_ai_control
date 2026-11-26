// BrowserWindow 的 webPreferences 必須明寫三個安全設定：畫面程序碰不到 Node／Electron，只能透過 preload 的白名單橋接。
// 不接受省略（依賴預設值）或展開（...opts）帶進來：值要在原始碼上看得到。

const REQUIRED = { contextIsolation: true, nodeIntegration: false, sandbox: true };

const keyName = (p) => (p.key.type === 'Identifier' ? p.key.name : p.key.type === 'Literal' ? String(p.key.value) : null);

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: 'webPreferences 必須明寫 contextIsolation: true、nodeIntegration: false、sandbox: true' },
    schema: [],
    messages: {
      notObject: 'webPreferences 要寫成物件常值，才能檢查安全設定',
      missing: 'webPreferences 缺少 {{key}}: {{value}}',
      wrong: 'webPreferences 的 {{key}} 必須是 {{value}}',
    },
  },
  create(context) {
    return {
      Property(node) {
        if (node.computed || keyName(node) !== 'webPreferences' || node.parent.type !== 'ObjectExpression') return;
        if (node.value.type !== 'ObjectExpression') {
          context.report({ node: node.value, messageId: 'notObject' });
          return;
        }
        const props = new Map();
        for (const p of node.value.properties) if (p.type === 'Property' && !p.computed) props.set(keyName(p), p);
        for (const [key, value] of Object.entries(REQUIRED)) {
          const p = props.get(key);
          if (!p) context.report({ node: node.value, messageId: 'missing', data: { key, value: String(value) } });
          else if (p.value.type !== 'Literal' || p.value.value !== value) {
            context.report({ node: p.value, messageId: 'wrong', data: { key, value: String(value) } });
          }
        }
      },
    };
  },
};
