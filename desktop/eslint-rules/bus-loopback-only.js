// 紅線 13：匯流排只走本機 127.0.0.1。desktop 原始碼裡寫死的 ws://、wss://、http://、https:// 網址，主機只能是 127.0.0.1。
// 不接受 localhost（可能解析成 ::1 或被 hosts 改掉）；樣板字串的主機是變數時看不出來，一樣擋。

const SCHEME = /^(wss?|https?):\/\//i;
const LOOPBACK = /^(wss?|https?):\/\/127\.0\.0\.1(?=[:/?#]|$)/i;

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: 'desktop 寫死的網址主機只能是 127.0.0.1' },
    schema: [],
    messages: { loopback: '網址主機只能寫死 127.0.0.1（匯流排只綁本機，見 AGENTS.md 紅線 13）：{{url}}' },
  },
  create(context) {
    const check = (node, text) => {
      if (SCHEME.test(text) && !LOOPBACK.test(text)) context.report({ node, messageId: 'loopback', data: { url: text.slice(0, 60) } });
    };
    return {
      Literal(node) { if (typeof node.value === 'string') check(node, node.value); },
      TemplateLiteral(node) { check(node, node.quasis[0].value.cooked ?? ''); },
    };
  },
};
