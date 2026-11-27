// 前端不寫死模型（ADR 0034）：模型裡的名稱只從解析後的 rig 拿（rig/check.js），換模型才不用改前端。rig/ 以外的 renderer、domain、dev 套用。
// 擋四種：Param／Part 開頭的參數 ID 常值；契約的約定名稱常值（'Idle'、'star_eyes'…，清單從 rig/contract.js 算，和語意名稱相同的不算）；
// rig/ 以外直接 import 個別模型的 profile（只能經 rig/index.js 的 rigFor）；程式碼裡出現特定模型的名字（rushia、fullrig、hiyori）。
// 只看常值與識別字，不看註解；字串組出來的名稱抓不到，由 test/model-agnostic.test.js 用換名模型實際跑一次補。
// 'EyeBlink'／'LipSync' 是 Live2D model3.json Groups 的標準名稱，不屬於哪個模型，本來就不在上面任何一類。
import { CONTRACT } from '../rig/contract.js';

const PARAM_ID = /^(Param|Part)[A-Z0-9_]/;
const CHARACTER = /rushia|fullrig|hiyori/i;
const PROFILE_IMPORT = /(^|\/)rig\/(?!(index|check|contract)\.js$)[^/]+$/;
const KINDS = ['motions', 'expressions', 'gestures'];
const SEMANTIC = new Set(KINDS.flatMap(kind => Object.keys(CONTRACT[kind])));
const CONVENTION = new Set(KINDS.flatMap(kind => Object.values(CONTRACT[kind])).filter(name => !SEMANTIC.has(name)));

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: { description: 'rig/ 以外不寫死模型裡的名稱、不直接用個別模型的 profile' },
    schema: [],
    messages: {
      param: "參數 ID 不要寫死（{{text}}）：用 rig.params 的對應（缺的參數加到 rig/contract.js），別的模型的 ID 可能不同",
      convention: "模型裡的名稱不要寫死（{{text}}）：用語意名稱查 rig（rig.motions／rig.expressions／rig.gestures），別的模型的名稱可能不同",
      profile: '不要直接 import 個別模型的 profile（{{text}}）：用 rig/index.js 的 rigFor 加 rig/check.js 的 checkRig',
      character: '程式碼不要出現特定模型的名字（{{text}}）：模型差異寫在 rig/ 的 profile，前端只看解析後的 rig',
    },
  },
  create(context) {
    const report = (node, messageId, text) => context.report({ node, messageId, data: { text: text.slice(0, 60) } });
    const checkString = (node, text) => {
      if (PARAM_ID.test(text)) report(node, 'param', text);
      else if (CONVENTION.has(text)) report(node, 'convention', text);
      else if (CHARACTER.test(text)) report(node, 'character', text);
    };
    const checkSource = (node) => {
      if (node && node.type === 'Literal' && typeof node.value === 'string' && PROFILE_IMPORT.test(node.value)) report(node, 'profile', node.value);
    };
    return {
      ImportDeclaration(node) { checkSource(node.source); },
      ImportExpression(node) { checkSource(node.source); },
      ExportNamedDeclaration(node) { checkSource(node.source); },
      ExportAllDeclaration(node) { checkSource(node.source); },
      Literal(node) {
        if (typeof node.value !== 'string') return;
        const parent = /** @type {any} */ (node).parent;
        if (parent && parent.source === node && /^(Import|Export)/.test(parent.type)) return;   // 模組路徑由 checkSource 看
        checkString(node, node.value);
      },
      TemplateElement(node) { checkString(node, node.value.cooked ?? ''); },
      Identifier(node) { if (CHARACTER.test(node.name)) report(node, 'character', node.name); },
    };
  },
};
