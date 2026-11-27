// 換名模型（ADR 0034）：每個動作、表情、手勢、參數都改了名的模型，經 checkRig 解析後照 renderer/role/app.js 的接法跑
// animator＋程序層＋導演＋狀態機。動作、表情、參數的查表都只認換過的名稱，拿約定名稱（RushiaHD 的名稱）查就拋錯：
// 有地方繞過 rig、寫死或組出模型裡的名稱就會失敗。lint（local/model-agnostic）擋常值，這裡擋執行時的寫死。PIXI 用只記錄呼叫的假物件。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createController, EXPRESSIONS, GESTURES, MOTIONS, PEEK_RATIO } from '../domain/character.js';
import { createAttention } from '../domain/attention.js';
import { checkRig } from '../rig/check.js';
import { CONTRACT } from '../rig/contract.js';
import { createAnimator } from '../renderer/animation/animator.js';
import { createRandom } from '../renderer/animation/ambient.js';
import { createProcedural, motionBlinks } from '../renderer/animation/procedural.js';
import { analyzeIdle } from '../dev/motion-metrics.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const X = (name) => `X_${name}`;
const renamed = (table) => Object.fromEntries(Object.entries(table).map(([key, name]) => [key, X(name)]));

/** 只認得 names 的查表：拿別的名稱查就拋錯 */
function strictTable(entries, what) {
  return new Proxy(Object.fromEntries(entries), {
    get(target, key) {
      if (typeof key === 'string' && !Object.hasOwn(target, key)) throw new Error(`用了模型沒有的${what}：${key}`);
      return target[key];
    },
  });
}
/** 參數 ID 清單：indexOf／includes 拿模型沒有的 ID 就拋錯 */
class StrictIds extends Array {
  static get [Symbol.species]() { return Array; }
  must(id) { if (!super.includes(id)) throw new Error(`用了模型沒有的參數：${id}`); }
  indexOf(id) { this.must(id); return super.indexOf(id); }
  includes(id) { this.must(id); return true; }
}

const GESTURE_PARAM = X('ParamArmLA');
const PARAM_IDS = [...Object.values(renamed(CONTRACT.params)), GESTURE_PARAM];
const profile = {
  motions: renamed(CONTRACT.motions), expressions: renamed(CONTRACT.expressions), gestures: renamed(CONTRACT.gestures), params: renamed(CONTRACT.params),
  layout: { region: { x: 500, y: 100, w: 1000, h: 2400 }, height: 420, headFraction: 0.2, headCenter: 0.12 },
};
const blinkCurve = { Target: 'Parameter', Id: X(CONTRACT.params.eyeOpen), Segments: [0, 1, 0, 0.5, 0, 0, 1, 1] };
const motionJson = (name) => ({ Meta: { Loop: true }, Curves: name === X(CONTRACT.motions.surprised) ? [blinkCurve] : [] });
const expressionJson = (name) => ({ Type: 'Live2D Expression', FadeInTime: 0.3, FadeOutTime: 0.3,
  Parameters: Object.values(profile.gestures).includes(name) ? [{ Id: GESTURE_PARAM, Value: 1, Blend: 'Overwrite' }] : [] });
const model3 = { FileReferences: {
  Motions: Object.fromEntries(Object.values(profile.motions).map(n => [n, [{ File: `${n}.motion3.json` }]])),
  Expressions: [...Object.values(profile.expressions), ...Object.values(profile.gestures)].map(Name => ({ Name, File: `${Name}.exp3.json` })),
}, Groups: [{ Name: 'EyeBlink', Ids: [X(CONTRACT.params.eyeOpen)] }, { Name: 'LipSync', Ids: [] }] };

const created = { motions: [], expressions: [] };
globalThis.PIXI = { live2d: {
  CubismMotionManager: class { updateMotion() {} startMotionPriority() {} isFinished() { return true; } },
  CubismMotionQueueManager: class { doUpdateMotion() {} startMotion() {} },
  CubismMotion: { create: (json) => { created.motions.push(json); return { setIsLoop() {}, setIsLoopFadeIn() {}, setEffectIds() {} }; } },
  CubismExpressionMotion: { create: (json) => { created.expressions.push(json); return {}; } },
} };

function setup() {
  const vocab = { motions: Object.values(MOTIONS), expressions: Object.values(EXPRESSIONS), gestures: Object.values(GESTURES).filter(g => g !== GESTURES.none) };
  const { rig, warnings } = checkRig({ file: 'renamed.model3.json', model3, paramIds: PARAM_IDS, canvas: { width: 2048, height: 3000 } }, vocab, profile);
  const ids = StrictIds.from(PARAM_IDS);
  const n = ids.length;
  const motions = strictTable(Object.values(rig.motions).map(name => [name, motionJson(name)]), '動作');
  const expressions = strictTable([...Object.values(rig.expressions), ...Object.values(rig.gestures)].map(name => [name, expressionJson(name)]), '表情');
  // 以下照 app.js：P、自己會眨眼的語意動作、程序層、導演、animator
  const P = Object.fromEntries(Object.entries(rig.params).map(([key, id]) => [key, ids.indexOf(id)]));
  const blinkMotions = new Set(Object.entries(rig.motions).filter(([, name]) => motionBlinks(motions[name], rig.params.eyeOpen)).map(([semantic]) => semantic));
  const defaults = new Float32Array(n).fill(0), minimums = new Float32Array(n).fill(-30), maximums = new Float32Array(n).fill(30);
  const raw = { parameters: { values: new Float32Array(n), minimumValues: minimums, maximumValues: maximums } };
  const procedural = createProcedural({ index: P, defaults, minimums, maximums, seed: 7, blinkMotions });
  const attention = createAttention({ random: createRandom(9) });
  const eyeBlinkIds = model3.Groups.find(g => g.Name === 'EyeBlink').Ids, lipSyncIds = model3.Groups.find(g => g.Name === 'LipSync').Ids;
  const animator = createAnimator({ rig, idle: MOTIONS.idle, noGesture: GESTURES.none, peekRatio: PEEK_RATIO, size: { w: 360, h: 560 },
    model: { scale: { set() {} } }, internal: { width: 2048 }, core: 'core', raw, defaults, ids, P, motions, expressions, eyeBlinkIds, lipSyncIds,
    physics: { evaluate: () => null }, procedural });
  return { rig, warnings, ids, motions, P, blinkMotions, raw, attention, animator };
}

test('查表本身有在擋：約定名稱（RushiaHD 的名稱）一查就拋錯', () => {
  const { ids, motions } = setup();
  assert.throws(() => ids.indexOf(CONTRACT.params.angleX), /模型沒有的參數：ParamAngleX/);
  assert.throws(() => motions[CONTRACT.motions.idle], /模型沒有的動作：Idle/);
});

test('換名模型：checkRig 解析出換過的名稱，參數全部找得到，自己會眨眼的動作從換名的眼皮曲線算出', () => {
  const { rig, warnings, P, blinkMotions } = setup();
  assert.deepEqual(warnings, []);
  assert.deepEqual(rig.motions, profile.motions);
  assert.ok(Object.values(P).every(i => i >= 0), JSON.stringify(P));
  assert.deepEqual([...blinkMotions], [MOTIONS.surprised]);
});

test('換名模型：每個動作、表情、手勢都演得出來，程序層寫進換名的參數，頭部中心用 profile 的 headCenter', () => {
  const { P, raw, attention, animator } = setup();
  const controller = createController();
  created.motions.length = 0; created.expressions.length = 0;
  animator.startMotion(MOTIONS.idle); animator.startExpression(EXPRESSIONS.neutral);
  animator.setPose('out', 1); animator.updatePose(performance.now() + 1e6);
  const angleX = [];
  const frames = (count, now) => {
    for (let k = 0; k < count; k++) {
      animator.advance(1 / 60);
      const ms = animator.time * 1000;
      const snap = { ...controller.snapshot(now + ms), pose: 'out' };
      const a = attention.update(ms, { pose: 'out', mode: snap.mode, motion: animator.activeSemantic(), inputOpen: false });
      animator.compute(1 / 60, snap, 0, a);
      angleX.push(raw.parameters.values[P.angleX]);
      animator.settleIdle();
    }
  };
  frames(120, 0);
  attention.click(40, animator.headCenterY() - 100, animator.time * 1000);
  for (const semantic of Object.values(MOTIONS)) { animator.startMotion(semantic); assert.equal(animator.activeMotion(), profile.motions[semantic]); frames(30, 0); }
  for (const semantic of Object.values(EXPRESSIONS)) { animator.startExpression(semantic); frames(10, 0); }
  for (const semantic of Object.values(GESTURES)) { animator.startGesture(semantic); frames(10, 0); }
  frames(600, 0);

  assert.equal(animator.activeMotion(), profile.motions.idle, '播完回到換名的待機');
  assert.equal(animator.moving(), false, '回到待機就不算在動（待機名稱沒認對會一直重播、停不了格）');
  assert.ok(created.motions.length >= Object.keys(MOTIONS).length);
  assert.equal(created.expressions.length, Object.keys(EXPRESSIONS).length + 1 + Object.keys(GESTURES).length);
  assert.ok(angleX.some(v => Math.abs(v) > 0.1), '程序層有轉頭（寫進換名的 angleX）');
  assert.ok(Math.abs(animator.headCenterY() - animator.topY() - 0.12 * 420) < 1e-9);
});

test('換名模型的動作軌跡：閒置指標用軌跡帶的 idle 名稱；沒帶就拋錯', () => {
  const cols = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion'];
  const rows = Array.from({ length: 600 }, (_, k) => [k * 1000 / 60, 0, 0, 0, 0.3, 0.2, 1, 0]);
  const trace = { cols, motions: [profile.motions.idle, profile.motions.nod], idle: profile.motions.idle, rows, marks: [], cursor: { px: 0, moves: 0 }, halfRate: false, truncated: false };
  assert.equal(analyzeIdle(trace).nonIdleFrames, 0);
  assert.throws(() => analyzeIdle({ ...trace, idle: undefined }), /idle/);
});

test('畫面的 HTML 不寫特定模型的名字（ESLint 不掃 HTML）', () => {
  const dir = path.join(ROOT, 'renderer');
  const html = fs.readdirSync(dir, { recursive: true }).filter(f => String(f).endsWith('.html'));
  assert.ok(html.length > 0);
  for (const file of html) assert.doesNotMatch(fs.readFileSync(path.join(dir, String(file)), 'utf8'), /rushia|fullrig|hiyori/i, String(file));
});
