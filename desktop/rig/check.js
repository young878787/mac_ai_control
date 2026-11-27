// 拿模型對契約檢查（ADR 0033）：每個語意名稱的模型名稱＝profile 有寫就用 profile 的，否則用契約的約定名稱；必要或選用一律看契約。
// 回傳解析後的 rig（animator、probe 用的就是它）與警告；缺必要的動作或參數就拋錯，訊息列出模型有的動作群組並提示怎麼配置。純函式（不碰檔案、DOM、PIXI）。
import { CONTRACT, DEFAULT_LAYOUT, REQUIRED } from './contract.js';

const KINDS = { motions: '動作', expressions: '表情', gestures: '手勢', params: '參數' };
const PROFILE_KEYS = [...Object.keys(KINDS), 'layout', 'once'];
const HINT = '在 desktop/rig/ 新增 profile 並在 rig/index.js 依模型檔名登錄（範例 rig/hiyori.js）';

/** profile 只能改名稱與版面：多的 key、契約沒有的語意名稱、不完整的 layout 都算錯 */
function checkProfile(profile, errors) {
  for (const key of Object.keys(profile)) if (!PROFILE_KEYS.includes(key)) errors.push(`profile 不能有 ${key}（只能有 ${PROFILE_KEYS.join('、')}；必要或選用由契約決定）`);
  for (const kind of Object.keys(KINDS)) {
    for (const [name, id] of Object.entries(profile[kind] || {})) {
      if (!Object.hasOwn(CONTRACT[kind], name)) errors.push(`profile 的 ${kind} 有契約沒有的語意名稱：${name}`);
      else if (typeof id !== 'string' || !id) errors.push(`profile 的 ${kind}.${name} 要寫模型裡的名稱`);
    }
  }
  for (const name of profile.once || []) if (!Object.hasOwn(CONTRACT.motions, name)) errors.push(`profile 的 once 有契約沒有的動作：${name}`);
  const l = profile.layout;
  if (l && !(l.region?.w > 0 && l.region?.h > 0 && l.height > 0 && l.headFraction > 0)) errors.push('profile 的 layout 要有 region（x、y、w、h）、height、headFraction');
  if (l && l.headCenter !== undefined && !(typeof l.headCenter === 'number' && l.headCenter > 0 && l.headCenter < 1)) errors.push('profile 的 layout.headCenter（選填）要在 0～1 之間');
}

/**
 * @param {{ file: string, model3: { FileReferences?: { Motions?: Record<string, unknown[]>, Expressions?: { Name: string }[] } },
 *           paramIds: string[], canvas: { width: number, height: number } }} model file＝模型檔名（訊息用）；canvas＝模型畫布大小
 * @param {{ motions: string[], expressions: string[], gestures: string[] }} vocab domain 用到的語意名稱
 * @param {any} [profile] 選填：motions／expressions／gestures／params 只寫和約定不同的名稱，layout、once 照 rig/hiyori.js（layout.headCenter 選填）
 * @returns {{ rig: { motions: Record<string,string>, expressions: Record<string,string>, gestures: Record<string,string>,
 *             params: Record<string,string>, layout: any, once: string[] }, warnings: string[] }}
 */
export function checkRig(model, vocab, profile = null) {
  const errors = [], warnings = [];
  for (const kind of ['motions', 'expressions', 'gestures']) {
    for (const name of vocab[kind]) if (!Object.hasOwn(CONTRACT[kind], name)) errors.push(`契約沒有這個${KINDS[kind]}：${name}（加到 rig/contract.js）`);
  }
  if (profile) checkProfile(profile, errors);
  if (errors.length) throw new Error(errors.join('；'));

  const { width, height } = model.canvas;
  const rig = { motions: {}, expressions: {}, gestures: {}, params: {}, once: profile?.once ?? [],
    layout: profile?.layout ? { headCenter: DEFAULT_LAYOUT.headCenter, ...profile.layout }
      : { ...DEFAULT_LAYOUT, region: { x: 0, y: 0, w: 2048, h: 2048 * height / width } } };   // region 以「畫布寬＝2048」表示（animator.js）
  for (const kind of Object.keys(KINDS)) rig[kind] = { ...CONTRACT[kind], ...profile?.[kind] };

  const refs = model.model3.FileReferences || {};
  const motionGroups = Object.keys(refs.Motions || {}).filter(name => refs.Motions[name]?.[0]);
  const expressionNames = new Set((refs.Expressions || []).map(e => e.Name));
  const have = { motions: new Set(motionGroups), expressions: expressionNames, gestures: expressionNames, params: new Set(model.paramIds) };
  for (const kind of Object.keys(KINDS)) {
    for (const [name, id] of Object.entries(rig[kind])) {
      if (have[kind].has(id)) continue;
      if (REQUIRED.includes(kind)) errors.push(`模型缺少${KINDS[kind]}：${name}（${id}）`);
      else warnings.push(`模型沒有${KINDS[kind]} ${id}（${name}），這個演出會略過`);
    }
  }
  if (errors.length) {
    throw new Error(`${model.file}：${errors.join('；')}。模型有的動作群組：${motionGroups.join('、') || '（沒有）'}。` +
      (profile ? '在這個模型的 profile 用 motions／params 把語意名稱對應到模型裡的名稱' : `${HINT}，用 motions／params 把語意名稱對應到模型裡的名稱`));
  }
  if (!profile) warnings.unshift(`${model.file} 沒有 rig profile：名稱照約定（rig/contract.js）、版面用整張畫布；名稱或版面不對時${HINT}`);
  return { rig, warnings };
}
