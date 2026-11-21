// 驗證 rig profile：涵蓋 domain 的每個語意名稱，且必要的動作／表情確實在模型的 model3.json 裡。純函式（不碰檔案、DOM、PIXI）。
/**
 * @param {{ motions: Record<string,string>, expressions: Record<string,string>, optional: Record<string,string>, gestures: Record<string,string>, params?: Record<string,string> }} profile
 * @param {{ FileReferences?: { Motions?: Record<string, unknown[]>, Expressions?: { Name: string }[] } }} model3
 * @param {{ motions: string[], expressions: string[], gestures: string[] }} vocab domain 用到的語意名稱
 * @param {string[]} [paramIds] 模型的參數 ID 陣列；有給時 profile.params 的每個 ID 都必須在裡面，沒給就不檢查
 * @returns {{ warnings: string[] }} 缺必要項目拋錯；缺選用表情與手勢只回傳警告
 */
export function checkRig(profile, model3, vocab, paramIds) {
  const errors = [], warnings = [];
  const refs = model3.FileReferences || {};
  const modelExpressions = new Set((refs.Expressions || []).map(e => e.Name));
  const uncovered = (kind, names, table) => { for (const n of names) if (!table[n]) errors.push(`profile 沒有對應的${kind}：${n}`); };
  uncovered('動作', vocab.motions, profile.motions);
  uncovered('表情', vocab.expressions, { ...profile.expressions, ...profile.optional });
  uncovered('手勢', vocab.gestures, profile.gestures);
  for (const [name, rigName] of Object.entries(profile.motions)) if (!refs.Motions?.[rigName]?.[0]) errors.push(`模型缺少動作：${name}（${rigName}）`);
  for (const [name, rigName] of Object.entries(profile.expressions)) if (!modelExpressions.has(rigName)) errors.push(`模型缺少表情：${name}（${rigName}）`);
  for (const [name, rigName] of Object.entries({ ...profile.optional, ...profile.gestures })) {   // 手勢、選用表情：缺了只警告
    if (!modelExpressions.has(rigName)) warnings.push(`模型沒有表情 ${rigName}（${name}），這個演出會略過`);
  }
  if (paramIds) { const have = new Set(paramIds); for (const [name, id] of Object.entries(profile.params || {})) if (!have.has(id)) errors.push(`模型缺少參數：${name}（${id}）`); }
  if (errors.length) throw new Error(errors.join('；'));
  return { warnings };
}
