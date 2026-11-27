// rig profile：涵蓋 domain 的每個語意名稱，且必要的動作／表情確實在模型裡（checkRig 是純函式，這裡用精簡的 model3.json 結構）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { MOTIONS, EXPRESSIONS, GESTURES } from '../domain/character.js';
import { checkRig } from '../rig/check.js';
import rig from '../rig/fullrig03.js';
import hiyori from '../rig/hiyori.js';
import { rigFor } from '../rig/index.js';

const vocab = {
  motions: Object.values(MOTIONS), expressions: Object.values(EXPRESSIONS),
  gestures: Object.values(GESTURES).filter(g => g !== GESTURES.none),
};
/** 依 profile 組出一份「模型有這些動作與表情」的精簡 model3.json */
const model3 = () => ({ FileReferences: {
  Motions: Object.fromEntries(Object.values(rig.motions).map(n => [n, [{ File: `${n}.motion3.json` }]])),
  Expressions: [...Object.values(rig.expressions), ...Object.values(rig.optional), ...Object.values(rig.gestures)].map(Name => ({ Name, File: `${Name}.exp3.json` })),
} });

test('fullrig03 的 profile 涵蓋 domain 的每個語意名稱、模型也都有：通過且沒有警告', () => {
  assert.deepEqual(checkRig(rig, model3(), vocab), { warnings: [] });
});

test('少一個必要動作：拋錯並指出是哪一個', () => {
  const m = model3();
  delete m.FileReferences.Motions.Nod;
  assert.throws(() => checkRig(rig, m, vocab), /模型缺少動作：nod（Nod）/);
});

test('少一個必要表情：拋錯', () => {
  const m = model3();
  m.FileReferences.Expressions = m.FileReferences.Expressions.filter(e => e.Name !== 'sleepy');
  assert.throws(() => checkRig(rig, m, vocab), /模型缺少表情：sleepy/);
});

test('少一個選用表情或手勢：只回傳警告，不拋錯', () => {
  const m = model3();
  m.FileReferences.Expressions = m.FileReferences.Expressions.filter(e => e.Name !== 'star_eyes' && e.Name !== 'chin_rest');
  const { warnings } = checkRig(rig, m, vocab);
  assert.equal(warnings.length, 2);
  assert.ok(warnings.some(w => w.includes('star_eyes')) && warnings.some(w => w.includes('chin_rest')));
});

/** 依 profile.params 組出「模型有這些參數」的 ID 陣列（再多一個無關的） */
const paramIds = () => [...Object.values(rig.params), 'ParamArmLA'];

test('給 paramIds：profile.params 的每個 ID 都在模型裡 → 通過', () => {
  assert.deepEqual(checkRig(rig, model3(), vocab, paramIds()), { warnings: [] });
});

test('給 paramIds：模型缺一個參數 → 拋錯並指出是哪一個（含程序層用的眉毛、右眼皮、BodyAngleX）', () => {
  for (const key of ['angleX', 'bodyX', 'eyeROpen', 'browLForm', 'breath']) {
    const ids = paramIds().filter(id => id !== rig.params[key]);
    assert.throws(() => checkRig(rig, model3(), vocab, ids), new RegExp(`模型缺少參數：${key}（${rig.params[key]}）`));
  }
});

test('沒給 paramIds：不檢查參數（行為和原本相同）', () => {
  assert.deepEqual(checkRig(rig, model3(), vocab), { warnings: [] });
  assert.deepEqual(checkRig({ ...rig, params: { ...rig.params, bogus: 'ParamNotInModel' } }, model3(), vocab), { warnings: [] });
});

test('domain 新增一個 profile 沒有對應的語意名稱：拋錯', () => {
  assert.throws(() => checkRig(rig, model3(), { ...vocab, motions: [...vocab.motions, 'wave'] }), /沒有對應的動作：wave/);
  assert.throws(() => checkRig(rig, model3(), { ...vocab, expressions: [...vocab.expressions, 'angry'] }), /沒有對應的表情：angry/);
  assert.throws(() => checkRig(rig, model3(), { ...vocab, gestures: [...vocab.gestures, 'clap'] }), /沒有對應的手勢：clap/);
});

test('profile 本身：語意名稱沒有多餘的、不載入 Blink、版面與參數齊全', () => {
  assert.deepEqual(Object.keys(rig.motions).sort(), vocab.motions.slice().sort());
  assert.deepEqual([...Object.keys(rig.expressions), ...Object.keys(rig.optional)].sort(), vocab.expressions.slice().sort());
  assert.deepEqual(Object.keys(rig.gestures).sort(), vocab.gestures.slice().sort());
  assert.ok(!Object.values(rig.motions).includes('Blink'), 'Blink 的頭身曲線全是 0，會把 Idle 的姿勢歸零');
  for (const k of ['angleX', 'angleY', 'angleZ', 'bodyZ', 'eyeX', 'eyeY', 'eyeOpen',
    'bodyX', 'bodyY', 'eyeROpen', 'browLY', 'browRY', 'browLX', 'browRX', 'browLForm', 'browRForm', 'browLAngle', 'browRAngle', 'breath']) assert.ok(rig.params[k], k);
  assert.ok(rig.layout.height > 0 && rig.layout.headFraction > 0 && rig.layout.region.h > 0);
});

/** hiyori_pro 的形狀：只有這幾個動作群組，沒有任何表情 */
const hiyoriModel3 = () => ({ FileReferences: { Motions: Object.fromEntries(
  ['Idle', 'Flick', 'FlickDown', 'FlickUp', 'Tap', 'Tap@Body', 'Flick@Body'].map(n => [n, [{ File: `${n}.motion3.json` }]])) } });

test('hiyori 的 profile：模型沒有任何表情也通過，表情與手勢只回傳警告', () => {
  const { warnings } = checkRig(hiyori, hiyoriModel3(), vocab, Object.values(hiyori.params));
  assert.equal(warnings.length, vocab.expressions.length + vocab.gestures.length);
});

test('hiyori 的 profile：語意名稱齊全、版面與參數齊全；反應動作只播一次，待機與說話維持循環', () => {
  assert.deepEqual(Object.keys(hiyori.motions).sort(), vocab.motions.slice().sort());
  assert.deepEqual(Object.keys(hiyori.optional).sort(), vocab.expressions.slice().sort());
  assert.deepEqual(Object.keys(hiyori.gestures).sort(), vocab.gestures.slice().sort());
  assert.deepEqual(Object.keys(hiyori.params).sort(), Object.keys(rig.params).sort());
  assert.deepEqual(hiyori.once.slice().sort(), vocab.motions.filter(n => n !== 'idle' && n !== 'talk').sort());
  assert.ok(hiyori.layout.height > 0 && hiyori.layout.headFraction > 0 && hiyori.layout.region.h > 0);
});

test('rigFor：依模型檔名挑 profile，沒列到的模型沿用 fullrig03', () => {
  assert.equal(rigFor('/model/hiyori_pro_t11.model3.json'), hiyori);
  assert.equal(rigFor('/model/RushiaHD.model3.json'), rig);
  assert.equal(rigFor('/model/other.model3.json'), rig);
});
