// animator：rig.once 的語意動作不管動作檔的 Loop 只播一次；部件切換（pose3）每個計算幀在物理之後呼叫。PIXI 用只記錄呼叫的假物件。
import test from 'node:test';
import assert from 'node:assert/strict';
import { createAnimator } from '../renderer/animation/animator.js';

const loops = [];
globalThis.PIXI = { live2d: {
  CubismMotionManager: class { updateMotion() {} startMotionPriority() {} isFinished() { return false; } },
  CubismMotionQueueManager: class { doUpdateMotion() {} startMotion() {} },
  CubismMotion: { create: () => ({ setIsLoop: (v) => loops.push(v), setIsLoopFadeIn() {}, setEffectIds() {} }) },
  CubismExpressionMotion: { create: () => ({}) },
} };

function make({ once, partPose, calls = [] } = {}) {
  const names = { idle: 'Idle', nod: 'Nod' };
  const rig = { motions: names, once, expressions: {}, gestures: {}, layout: { height: 420, region: { x: 0, y: 0, w: 100, h: 200 } } };
  const parameters = { values: new Float32Array(1), minimumValues: new Float32Array([-1]), maximumValues: new Float32Array([1]) };
  return createAnimator({ rig, idle: 'idle', noGesture: 'none', peekRatio: 0.3, size: { w: 360, h: 560 },
    model: { scale: { set() {} } }, internal: { width: 2048 }, core: 'core', raw: { parameters }, defaults: new Float32Array(1), ids: ['A'],
    P: {}, motions: { Idle: { Meta: { Loop: true } }, Nod: { Meta: { Loop: true } } }, expressions: {}, eyeBlinkIds: [], lipSyncIds: [],
    physics: { evaluate() { calls.push('physics'); return 0.5; } }, procedural: null, partPose });
}

test('rig.once 的動作不循環；其他照動作檔的 Loop', () => {
  loops.length = 0;
  const animator = make({ once: ['nod'] });
  animator.startMotion('nod'); animator.startMotion('idle');
  assert.deepEqual(loops, [false, true]);
});

test('rig 沒有 once：照動作檔的 Loop（原本的行為）', () => {
  loops.length = 0;
  make().startMotion('nod');
  assert.deepEqual(loops, [true]);
});

test('partPose：每個計算幀在物理之後更新一次，回傳值仍是物理的變化量', () => {
  const calls = [];
  const animator = make({ calls, partPose: { updateParameters: (core, dt) => calls.push(`pose ${core} ${dt}`) } });
  assert.equal(animator.compute(0.5, { pose: 'peek' }, 0, null), 0.5);
  assert.deepEqual(calls, ['physics', 'pose core 0.5']);
});

test('沒有 partPose：照常計算', () => {
  assert.equal(make().compute(0.5, { pose: 'peek' }, 0, null), 0.5);
});
