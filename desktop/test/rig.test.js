// 模型對契約檢查（ADR 0033）：契約涵蓋 domain 的每個語意名稱；名稱照 profile（選填）或約定；動作、參數必要，表情、手勢選用；profile 改不了等級。
// checkRig 是純函式，這裡用精簡的 model3.json 結構。
import test from 'node:test';
import assert from 'node:assert/strict';
import { MOTIONS, EXPRESSIONS, GESTURES } from '../domain/character.js';
import { checkRig } from '../rig/check.js';
import { CONTRACT, DEFAULT_LAYOUT } from '../rig/contract.js';
import fullrig03 from '../rig/fullrig03.js';
import hiyori from '../rig/hiyori.js';
import { rigFor } from '../rig/index.js';

const vocab = {
  motions: Object.values(MOTIONS), expressions: Object.values(EXPRESSIONS),
  gestures: Object.values(GESTURES).filter(g => g !== GESTURES.none),
};
const SQUARE = { width: 2048, height: 2048 };
const ALL_PARAMS = [...Object.values(CONTRACT.params), 'ParamArmLA'];

/** 照約定命名的模型（RushiaHD 的形狀）：契約的每個動作、表情、手勢都有 */
const conventional = () => ({ FileReferences: {
  Motions: Object.fromEntries([...Object.values(CONTRACT.motions), 'Blink'].map(n => [n, [{ File: `${n}.motion3.json` }]])),
  Expressions: [...Object.values(CONTRACT.expressions), ...Object.values(CONTRACT.gestures)].map(Name => ({ Name, File: `${Name}.exp3.json` })),
} });
/** hiyori_pro 的形狀：只有這幾個動作群組，沒有任何表情 */
const hiyoriShape = () => ({ FileReferences: { Motions: Object.fromEntries(
  ['Idle', 'Flick', 'FlickDown', 'FlickUp', 'Tap', 'Tap@Body', 'Flick@Body'].map(n => [n, [{ File: `${n}.motion3.json` }]])) } });
const check = (model3, profile, { paramIds = ALL_PARAMS, canvas = SQUARE, words = vocab } = {}) =>
  checkRig({ file: 'm.model3.json', model3, paramIds, canvas }, words, profile);

test('契約：剛好涵蓋 domain 的每個語意名稱，不用 Blink，19 個參數', () => {
  assert.deepEqual(Object.keys(CONTRACT.motions).sort(), vocab.motions.slice().sort());
  assert.deepEqual(Object.keys(CONTRACT.expressions).sort(), vocab.expressions.slice().sort());
  assert.deepEqual(Object.keys(CONTRACT.gestures).sort(), vocab.gestures.slice().sort());
  assert.ok(!Object.values(CONTRACT.motions).includes('Blink'), 'Blink 的頭身曲線全是 0，會把 Idle 的姿勢歸零');
  assert.equal(Object.keys(CONTRACT.params).length, 19);
});

test('照約定命名、沒有 profile：通過，警告只有一條「沒有 rig profile」，名稱照約定、版面用整張畫布', () => {
  const { rig, warnings } = check(conventional(), null, { canvas: { width: 2976, height: 4175 } });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /m\.model3\.json 沒有 rig profile.*desktop\/rig\/.*rig\/index\.js/);
  assert.deepEqual(rig.motions, CONTRACT.motions);
  assert.deepEqual(rig.params, CONTRACT.params);
  assert.deepEqual(rig.once, []);
  assert.deepEqual(rig.layout, { ...DEFAULT_LAYOUT, region: { x: 0, y: 0, w: 2048, h: 2048 * 4175 / 2976 } });
});

test('RushiaHD（fullrig03）：通過、沒有警告，名稱照約定、版面用 profile 的', () => {
  const { rig, warnings } = check(conventional(), fullrig03);
  assert.deepEqual(warnings, []);
  assert.deepEqual(rig.motions, CONTRACT.motions);
  assert.deepEqual(rig.expressions, CONTRACT.expressions);
  assert.deepEqual(rig.gestures, CONTRACT.gestures);
  assert.equal(rig.layout, fullrig03.layout);
});

test('缺必要動作：拋錯，訊息含缺哪一個、模型有的動作群組、怎麼配置', () => {
  const m = conventional();
  delete m.FileReferences.Motions.Nod;
  assert.throws(() => check(m, fullrig03), (e) => /模型缺少動作：nod（Nod）/.test(e.message) && /模型有的動作群組：Idle、Surprised/.test(e.message)
    && /profile 用 motions／params/.test(e.message));
  assert.throws(() => check(m, null), /desktop\/rig\/ 新增 profile 並在 rig\/index\.js/);   // 沒有 profile：提示新增
});

test('缺必要參數：拋錯並指出是哪一個（含程序層用的眉毛、右眼皮、BodyAngleX）', () => {
  for (const key of ['angleX', 'bodyX', 'eyeROpen', 'browLForm', 'breath']) {
    const ids = ALL_PARAMS.filter(id => id !== CONTRACT.params[key]);
    assert.throws(() => check(conventional(), fullrig03, { paramIds: ids }), new RegExp(`模型缺少參數：${key}（${CONTRACT.params[key]}）`));
  }
});

test('缺任何表情（含 neutral、sleepy）或手勢：只警告，不拋錯', () => {
  const m = conventional();
  m.FileReferences.Expressions = m.FileReferences.Expressions.filter(e => !['neutral', 'sleepy', 'star_eyes', 'chin_rest'].includes(e.Name));
  const { warnings } = check(m, fullrig03);
  assert.equal(warnings.length, 4);
  for (const name of ['neutral', 'sleepy', 'star_eyes', 'chin_rest']) assert.ok(warnings.some(w => w.includes(name)), name);
  assert.ok(warnings.some(w => w.startsWith('模型沒有手勢 chin_rest')));
});

test('hiyori：有 profile 時名稱對應生效、通過，表情 7＋手勢 3 條警告；沒有 profile 就拋錯並列出模型的群組', () => {
  const { rig, warnings } = check(hiyoriShape(), hiyori);
  assert.equal(warnings.length, vocab.expressions.length + vocab.gestures.length);
  assert.equal(rig.motions.idle, 'Idle');
  assert.equal(rig.motions.nod, 'Tap@Body');
  assert.deepEqual(rig.once, hiyori.once);
  assert.throws(() => check(hiyoriShape(), null), (e) => /模型缺少動作：nod（Nod）/.test(e.message) && /Tap@Body/.test(e.message));
});

test('hiyori 的 profile：反應動作只播一次，待機與說話維持循環', () => {
  assert.deepEqual(hiyori.once.slice().sort(), vocab.motions.filter(n => n !== 'idle' && n !== 'talk').sort());
});

test('profile 改不了等級、也不能亂寫：多的 key、契約沒有的名稱、不明的 once、不完整的 layout 都拋錯', () => {
  const cases = [
    [{ optional: { neutral: 'neutral' } }, /profile 不能有 optional/],
    [{ motions: { nood: 'Nod' } }, /motions 有契約沒有的語意名稱：nood/],
    [{ params: { angleX: '' } }, /params\.angleX 要寫模型裡的名稱/],
    [{ once: ['wave'] }, /once 有契約沒有的動作：wave/],
    [{ layout: { height: 420 } }, /layout 要有 region/],
    [{ motions: { toString: 'Nod' } }, /契約沒有的語意名稱：toString/],
  ];
  for (const [profile, expected] of cases) assert.throws(() => check(conventional(), profile), expected, JSON.stringify(profile));
});

test('domain 新增契約沒有的語意名稱：拋錯', () => {
  assert.throws(() => check(conventional(), fullrig03, { words: { ...vocab, motions: [...vocab.motions, 'wave'] } }), /契約沒有這個動作：wave/);
  assert.throws(() => check(conventional(), fullrig03, { words: { ...vocab, expressions: [...vocab.expressions, 'angry'] } }), /契約沒有這個表情：angry/);
  assert.throws(() => check(conventional(), fullrig03, { words: { ...vocab, gestures: [...vocab.gestures, 'clap'] } }), /契約沒有這個手勢：clap/);
});

test('rigFor：依模型檔名挑 profile，沒登錄的回傳 null', () => {
  assert.equal(rigFor('/model/hiyori_pro_t11.model3.json'), hiyori);
  assert.equal(rigFor('/model/RushiaHD.model3.json'), fullrig03);
  assert.equal(rigFor('/model/other.model3.json'), null);
  assert.equal(rigFor('/model/constructor'), null);
});
