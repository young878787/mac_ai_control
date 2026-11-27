// 畫面程序（組裝）：載入 Live2D 模型、依狀態機演出、接 DOM 與後端事件。
// 各件事在別的檔：engine/（舞台、載模型、修補、幀迴圈）、animation/animator.js（動作表情手勢與參數疊加）、
// pointer.js（點擊穿透、拖曳）、probe.js（除錯介面與動作軌跡）、widgets.js（泡泡、選擇清單、輸入框）。
import { createController, EXPRESSIONS, GESTURES, MOTIONS, PEEK_RATIO } from '../../domain/character.js';
import { CONTEXTS, createAttention } from '../../domain/attention.js';
import { createFixedPhysics } from '../engine/physics-clock.js';
import { createFrameGate } from '../../domain/frame-gate.js';
import { checkRig } from '../../rig/check.js';
import { rigFor } from '../../rig/index.js';
import { nextRenderFrame } from '../engine/compat/render-buffers.js';
import { createInterpolator } from '../engine/vertex-interp.js';
import { createLoop } from '../engine/loop.js';
import { loadAssets, loadModel } from '../engine/load-model.js';
import { createStage, waitForCore } from '../engine/stage.js';
import { createAnimator } from '../animation/animator.js';
import { createRandom } from '../animation/ambient.js';
import { createProcedural, motionBlinks } from '../animation/procedural.js';
import { createPointer } from './pointer.js';
import { createProbe } from './probe.js';
import { createBubbles, createChoices, createInputBox, createQuick } from './widgets.js';

// 幀率（計畫書 §6.2）：要畫就逐 vsync 畫（60 fps）；潛伏時頭髮物理收尾就停格（0 fps），停格期間只用計時器更新狀態機與泡泡
const MIN_FRAME_MS = 12;                          // 120 Hz 螢幕上 rAF 會到 120 次／秒，間隔小於這個值的那次跳過，維持 60 fps
// 收尾判斷（物理輸出每步最大變化量）。2026-11-20 實測：Idle 本身就讓頭髮持續擺動（p95 0.048、最大 0.09），事件造成的擺動不比它大；
// eps 取略高於 Idle p95，等到 Idle 週期中平緩的一段就停格。退場後約 0.73 秒停格，Idle 任意時點最壞 0.8 秒；maxMs 是保險
const SETTLE = { eps: 0.05, holdMs: 500, maxMs: 3000 };
const FROZEN_TICK_MS = 250;                       // 停格期間狀態機與泡泡的更新間隔
const RENDER_OPT = { buffers: true, state: true, halfRate: true };              // 渲染優化開關（計畫書 §6.3，A/B 實測後保留的項目）；有問題時改 false 回到原本的繪製
const PROCEDURAL = true;                          // 程序層（擬人化計畫書 §4）：頭身眼眉由 animation/procedural.js 接手。false＝回到 Idle 全權＋鎖游標的舊做法（A/B 與回退用，計劃書 §10）

/** @type {(id: string) => any} */
const $ = (id) => document.getElementById(id);

window.addEventListener('error', (e) => console.error('[ui] error', e.error && e.error.stack || e.message));

async function main() {
  await waitForCore();
  const cfg = await desktop.config();
  if (!cfg.model) throw new Error('沒有指定模型（環境變數 MAC_AI_UI_MODEL）');
  const W = cfg.winW, H = cfg.winH;
  const stage = createStage({ view: $('stage'), width: W, height: H });
  const { app } = stage;
  const { model, internal, core, raw, settings, physicsJSON, poseJSON, base, defaults, ids } = await loadModel('app://ui' + cfg.model, stage, RENDER_OPT);
  // 模型對契約檢查（rig/check.js）：名稱照 profile（選填）或約定；缺必要的動作或參數就拋錯，缺表情與手勢、沒有 profile 只警告。
  // 狀態機只說語意名稱（domain/character.js），解析後的 rig 把它對應到模型裡的名稱。除錯介面（__ui、動作軌跡）仍回報模型裡的名稱
  const vocab = { motions: Object.values(MOTIONS), expressions: Object.values(EXPRESSIONS), gestures: Object.values(GESTURES).filter(g => g !== GESTURES.none) };
  const { rig, warnings } = checkRig({ file: cfg.model.slice(cfg.model.lastIndexOf('/') + 1), model3: settings, paramIds: ids,
    canvas: { width: internal.width, height: internal.height } }, vocab, rigFor(cfg.model));
  for (const w of warnings) console.warn(`[ui] ${w}`);
  const { height: MODEL_H, headFraction: HEAD_FRACTION } = rig.layout;   // 出場高度（px）、算「頭部」的比例
  const NEEDED_MOTIONS = Object.values(rig.motions);
  const { motions, expressions } = await loadAssets(base, settings, { motions: NEEDED_MOTIONS,
    expressions: [...Object.values(rig.expressions), ...Object.values(rig.gestures)] });   // 手勢也是表情檔（手臂切換參數），分開排隊
  const P = Object.fromEntries(Object.entries(rig.params).map(([key, id]) => [key, ids.indexOf(id)]));
  // 自己會眨眼的語意動作（眼皮曲線最小值 < 0.5，從 motion3.json 算，不寫死名稱）：這些動作播放時程序眨眼暫停
  const blinkMotions = new Set(Object.entries(rig.motions).filter(([, name]) => motionBlinks(motions[name], rig.params.eyeOpen)).map(([semantic]) => semantic));
  const procedural = PROCEDURAL ? createProcedural({ index: P, defaults, minimums: raw.parameters.minimumValues, maximums: raw.parameters.maximumValues,
    seed: (Math.random() * 2 ** 32) >>> 0, blinkMotions }) : null;
  // 注意力導演（§4.4）：亂數用另一個 seed，時鐘和程序層相同（animator.time * 1000）
  const attention = PROCEDURAL ? createAttention({ random: createRandom((Math.random() * 2 ** 32) >>> 0) }) : null;
  let context = null;                               // 導演最近一次決定的情境名稱（軌跡與除錯用）
  const physics = createFixedPhysics(PIXI.live2d.CubismPhysics.create(physicsJSON), core, physicsJSON);
  const partPose = poseJSON ? PIXI.live2d.CubismPose.create(poseJSON) : null;   // 例如 hiyori 的兩組手臂
  const eyeBlinkIds = settings.Groups?.find(g => g.Name === 'EyeBlink')?.Ids || [];
  const lipSyncIds = settings.Groups?.find(g => g.Name === 'LipSync')?.Ids || [];
  const animator = createAnimator({ rig, idle: MOTIONS.idle, noGesture: GESTURES.none, peekRatio: PEEK_RATIO, size: { w: W, h: H },
    model, internal, core, raw, defaults, ids, P, motions, expressions, eyeBlinkIds, lipSyncIds, physics, procedural, partPose });
  const topY = animator.topY;

  // ---------- 狀態機與介面 ----------
  const controller = createController();
  const bubbles = createBubbles($('bubbles'), { onHover: (id, on) => controller.hoverBubble(id, on, Date.now()) });
  const quick = createQuick($('quick'), $('quick-chat'), {
    onOpen: () => { controller.closeQuick(); desktop.toggleChat(); loop.wake(); },
    onHover: (on) => controller.hoverQuick(on, Date.now()) });
  const choices = createChoices($('choices'), $('choices-title'), $('choices-list'), $('choices-close'), {
    onChoose: (n, action) => applyEffects(controller.choose(n, action, Date.now())),
    onClose: () => applyEffects(controller.closeChoices(Date.now())) });
  const input = createInputBox($('input-box'), $('input'), {
    onSubmit: (text) => { desktop.send({ type: 'command', text }); applyEffects(controller.userCommand(text, Date.now())); },
    onClose: () => { applyEffects(controller.setInputOpen(false, Date.now())); desktop.focus(false); } });
  function toggleInput() {
    if (input.isOpen()) { input.close(); return; }
    applyEffects(controller.setInputOpen(true, Date.now()));
    input.open(); desktop.focus(true); loop.wake();
  }
  $('input').addEventListener('input', () => { if (attention) attention.typing(animator.time * 1000); });
  desktop.onToggleInput(toggleInput);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.close(); controller.closeQuick(); } });
  window.addEventListener('blur', () => { input.close(); });

  let poseOut = false;                                // 目前是否出場（決定要不要停格）
  const pendingApplied = [];
  function applyEffects(effects) {
    for (const e of effects) {
      if (e.type === 'pose') { poseOut = e.pose === 'out'; animator.setPose(e.pose, e.ms); desktop.gaze(poseOut); }
      else if (e.type === 'motion') animator.startMotion(e.name);
      else if (e.type === 'expression') animator.startExpression(e.name);
      else if (e.type === 'gesture') animator.startGesture(e.name);
      else if (e.type === 'send') desktop.send(e.message);
    }
    if (effects.length) loop.wake();
  }

  // ---------- 每幀 ----------
  const bubblesEl = $('bubbles'), halo = $('halo'), ring = halo.querySelector('.ring');
  let lastBubbleKey = '', lastBottom = -1;
  // O5：Core、動作與物理每 2 幀算一次，中間那幀畫內插（RENDER_OPT.halfRate）
  const interp = RENDER_OPT.halfRate ? createInterpolator([...raw.drawables.vertexPositions, raw.drawables.opacities]) : null;
  let computeDt = 0, computeNow = true;
  function renderFrame(now, dt) {
    animator.advance(dt);
    applyEffects(controller.tick(Date.now()));
    const tilt = animator.updatePose(now);
    const snap = controller.snapshot(Date.now());
    computeDt += dt;
    computeNow = !interp || !computeNow;
    let delta = null;
    const t1 = performance.now();
    if (computeNow) {
      const cdt = Math.min(computeDt, 0.2);
      computeDt = 0;
      let a = null;
      if (attention) {
        a = attention.update(animator.time * 1000, { pose: snap.pose, mode: snap.mode, motion: animator.activeSemantic(), inputOpen: input.isOpen() });
        context = a.context;
      }
      delta = animator.compute(cdt, snap, tilt, a);
      probe.record(now);
      if (interp) interp.restore();
      core.update();
      if (interp) interp.computed();
    } else interp.between();
    nextRenderFrame();
    const t3 = performance.now();
    animator.place();
    app.render();
    const t4 = performance.now();
    loop.parts.compute += t3 - t1; loop.parts.render += t4 - t3; loop.parts.n++;
    animator.settleIdle();
    updateUi(snap);
    return delta;
  }
  // 泡泡、快捷鍵、選擇清單、會話光環：每幀更新；停格時由計時器更新
  function updateUi(snap) {
    const bottom = Math.round(H - topY() + 8);
    if (bottom !== lastBottom) { lastBottom = bottom; bubblesEl.style.bottom = `${bottom - 14}px`; bubblesEl.style.maxHeight = `${Math.max(0, H - bottom + 14)}px`; }
    const key = snap.bubbles.map(b => `${b.id}|${b.kind}|${b.text}`).join('\n');
    if (key !== lastBubbleKey) { lastBubbleKey = key; bubbles.render(snap.bubbles); }
    quick.render(snap.quick, topY() + 0.04 * MODEL_H);
    choices.render(snap.choices);
    if (snap.session.active) {
      halo.style.display = 'block';
      halo.style.left = `${W / 2 - 70}px`; halo.style.top = `${Math.round(topY() - 22)}px`;
      ring.setAttribute('stroke-dasharray', `${(snap.session.ratio * 100).toFixed(1)} 100`);
    } else halo.style.display = 'none';
    while (pendingApplied.length) desktop.applied(pendingApplied.shift(), Date.now());
  }

  const loop = createLoop({ gate: createFrameGate(SETTLE), minFrameMs: MIN_FRAME_MS, frozenTickMs: FROZEN_TICK_MS, renderFrame,
    onFrozenTick: (stillFrozen) => {
      const now = Date.now();
      applyEffects(controller.tick(now));               // 有 effect 會 wake，回到逐 vsync 繪製
      if (stillFrozen()) updateUi(controller.snapshot(now));
    },
    animating: animator.animating, out: () => poseOut, moving: animator.moving });
  const probe = createProbe({ rig, raw, P, controller, animator, stage, loop, attention, context: () => context,
    motions: NEEDED_MOTIONS, contexts: CONTEXTS, halfRate: RENDER_OPT.halfRate, modelH: MODEL_H });
  createPointer({ alphaAt: stage.alphaAt, animator, controller, attention, applyEffects, width: W, modelH: MODEL_H, headFraction: HEAD_FRACTION });

  // ---------- 視線與後端事件 ----------
  desktop.onCursor((p) => {
    probe.cursor(p);
    animator.setCursor(p);
    if (attention) attention.cursor(p.x - W / 2, animator.headCenterY() - p.y, animator.time * 1000);
  });
  desktop.onBus((msg) => {
    probe.mark(msg);
    if (msg.type === 'partial' && input.isOpen()) input.setHint(msg.text);
    if (msg.type === 'heard' && input.isOpen()) input.setHint('');
    applyEffects(controller.handle(msg, Date.now()));
    if (attention && msg.type === 'heard') attention.heard(msg.status, animator.time * 1000);   // 要在 controller 處理之後：mode 離開 listening 了，點頭才不會被取消
    pendingApplied.push(msg.id);
    loop.wake();
  });

  for (let i = 0; i < 120; i++) { raw.parameters.values.set(defaults); physics.evaluate(core, 1 / 60); }  // 讓頭髮物理先穩定
  animator.startMotion(MOTIONS.idle); animator.startExpression(EXPRESSIONS.neutral);
  loop.wake();
  window.addEventListener('pagehide', () => stage.destroy(), { once: true });
  console.log('[ui] ready, Cubism Core', Live2DCubismCore.Version.csmGetVersion().toString(16));
  desktop.ready();
}

main().catch((err) => {
  console.error('[ui] 啟動失敗', err && err.stack || err);
  const el = $('status'); el.hidden = false; el.textContent = `啟動失敗：${err && err.message || err}`;
  desktop.ready();
});
