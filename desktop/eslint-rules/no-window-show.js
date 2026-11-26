// 紅線 13：聊天室與角色視窗主動顯示只用 showInactive()，不搶焦點（前景 App 決定 front_action、分頁、看畫面的對象）。見 ADR 0012。
// 只在 main/ 啟用（eslint.config.js），不分辨物件型別：主程序裡任何 .show() 都擋，要用請改 showInactive()。

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: '主程序禁止 .show()，視窗顯示一律用 showInactive()' },
    schema: [],
    messages: { useInactive: '不要用 .show()（會搶焦點），改用 showInactive()。見 ADR 0012' },
  },
  create(context) {
    return {
      CallExpression(node) {
        const c = node.callee;
        if (c.type === 'MemberExpression' && !c.computed && c.property.type === 'Identifier' && c.property.name === 'show') {
          context.report({ node: c.property, messageId: 'useInactive' });
        }
      },
    };
  },
};
