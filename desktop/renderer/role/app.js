// 畫面程序：載入 Live2D 模型、依狀態機演出、處理點擊穿透／拖曳／輸入。
import { createController, EXPRESSIONS, GESTURES, MOTIONS, PEEK_RATIO } from '../../domain/character.js';
import { CONTEXTS, createAttention } from '../../domain/attention.js';
import { createFixedPhysics } from '../engine/physics-clock.js';
import { createFrameGate } from '../../domain/frame-gate.js';
import { checkRig } from '../../rig/check.js';
import rig from '../../rig/fullrig03.js';
import { installDrawableColors } from '../engine/compat/drawable-colors.js';
import { installRenderBuffers, installStateCache, nextRenderFrame } from '../engine/compat/render-buffers.js';
import { createInterpolator } from '../engine/vertex-interp.js';
import { createRandom } from '../animation/ambient.js';
import { createProcedural, motionBlinks } from '../animation/procedural.js';
import { createBubbles, createChoices, createInputBox, createQuick } from './widgets.js';

const { height: MODEL_H, region: REGION, headFraction: HEAD_FRACTION } = rig.layout;   // 出場高度（px）、模型座標中的「全身」範圍、算「頭部」的比例
// 狀態機只說語意名稱（domain/character.js）；rig/ 的 profile 把它對應到模型裡的名稱。除錯介面（__ui、動作軌跡）仍回報模型裡的名稱
const NEEDED_MOTIONS = Object.values(rig.motions);
const IDLE = rig.motions[MOTIONS.idle];
const NEEDED_EXPRESSIONS = Object.values(rig.expressions);
const OPTIONAL_EXPRESSIONS = Object.values(rig.optional);   // 缺了只警告，狀態機要求時不演
const GESTURE_EXPRESSIONS = Object.values(rig.gestures);    // 手勢層（手臂切換參數），和臉部表情分開排隊
const ALPHA_HIT = 25;                             // 游標下像素 alpha ≥ 這個值（約 10%）算點到角色
const DRAG_PX = 4;
// 幀率（計畫書 §6.2）：要畫就逐 vsync 畫（60 fps）；潛伏時頭髮物理收尾就停格（0 fps），停格期間只用計時器更新狀態機與泡泡
const MIN_FRAME_MS = 12;                          // 120 Hz 螢幕上 rAF 會到 120 次／秒，間隔小於這個值的那次跳過，維持 60 fps
// 收尾判斷（物理輸出每步最大變化量）。2026-11-20 實測：Idle 本身就讓頭髮持續擺動（p95 0.048、最大 0.09），事件造成的擺動不比它大；
// eps 取略高於 Idle p95，等到 Idle 週期中平緩的一段就停格。退場後約 0.73 秒停格，Idle 任意時點最壞 0.8 秒；maxMs 是保險
const SETTLE = { eps: 0.05, holdMs: 500, maxMs: 3000 };
const FROZEN_TICK_MS = 250;                       // 停格期間狀態機與泡泡的更新間隔
const RENDER_OPT = { buffers: true, state: true, halfRate: true };              // 渲染優化開關（計畫書 §6.3，A/B 實測後保留的項目）；有問題時改 false 回到原本的繪製
const TRACE_COLS = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion', 'context'];   // 動作軌跡的欄位（量測用，見 dev/motion-metrics.js）；motion＝NEEDED_MOTIONS 的索引、context＝CONTEXTS 的索引（程序層關閉＝-1）
const PROCEDURAL = true;                          // 程序層（擬人化計畫書 §4）：頭身眼眉由 animation/procedural.js 接手。false＝回到 Idle 全權＋鎖游標的舊做法（A/B 與回退用，計劃書 §10）
const TRACE_MAX = 50000;                          // 軌跡最多記幾列（30 Hz 計算約 28 分鐘），超過就停止記錄並標 truncated

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const easeOutBack = (k) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2); };
const easeInOut = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
const $ = (id) => document.getElementById(id);

async function readJSON(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`讀取失敗：${url} (${res.status})`);
  return res.json();
}

async function waitForCore() {
  const deadline = performance.now() + 15000;
  for (;;) {
    try { if (window.Live2DCubismCore && Live2DCubismCore.Version.csmGetVersion()) break; } catch { /* 尚未載入 */ }
    if (performance.now() > deadline) throw new Error('Cubism Core 載入逾時');
    await new Promise(r => setTimeout(r, 25));
  }
  const version = Live2DCubismCore.Version.csmGetVersion();
  if ((version >>> 24) < 5) throw new Error(`需要 Cubism Core 5 以上，目前是 ${version >>> 24}.x`);  // moc3 v5 的模型
}

window.addEventListener('error', (e) => console.error('[ui] error', e.error && e.error.stack || e.message));

// Cubism Core 6 把繪製順序從 drawables.renderOrders 搬到 Model.getRenderOrders()（drawable 在前、offscreen 在後）；
// pixi-live2d-display 0.4.0 仍讀舊位置。沒有 offscreen 的模型（如 RushiaHD）前 N 項就是 drawable 的順序，這裡補回去。
function bridgeRenderOrders(raw) {
  if (raw.drawables.renderOrders || typeof raw.getRenderOrders !== 'function') return;
  if (raw.offscreens && raw.offscreens.count > 0) throw new Error('模型含 offscreen，目前的渲染器不支援');
  const count = raw.drawables.count;
  Object.defineProperty(raw.drawables, 'renderOrders', { get: () => raw.getRenderOrders().subarray(0, count) });
}

async function main() {
  await waitForCore();
  const cfg = await desktop.config();
  if (!cfg.model) throw new Error('沒有指定模型（環境變數 MAC_AI_UI_MODEL）');
  const W = cfg.winW, H = cfg.winH;
  const modelURL = 'app://ui' + cfg.model;
  const base = modelURL.slice(0, modelURL.lastIndexOf('/') + 1);
  const settings = await readJSON(modelURL);
  const physicsJSON = await readJSON(base + settings.FileReferences.Physics);
  const app = new PIXI.Application({ view: $('stage'), width: W, height: H, autoStart: false, backgroundAlpha: 0, antialias: true,
    autoDensity: true, resolution: Math.min(devicePixelRatio || 1, 2), preserveDrawingBuffer: true });
  // PixiJS 6 的 InteractionManager 掛在 Ticker.system，預設會讓 rAF 永遠以 60 Hz 空轉；繪製由我們自己的幀迴圈負責，這裡全部停掉
  for (const t of [PIXI.Ticker.system, PIXI.Ticker.shared, app.ticker]) { t.autoStart = false; t.stop(); }
  const gl = app.renderer.gl;
  if (!gl) throw new Error('無法建立 WebGL');
  const model = await PIXI.live2d.Live2DModel.from(modelURL, { autoUpdate: false, autoInteract: false, motionPreload: 'NONE' });
  app.stage.addChild(model);
  const internal = model.internalModel, core = internal.coreModel, raw = core.getModel();
  bridgeRenderOrders(raw);
  if (RENDER_OPT.buffers) installRenderBuffers(internal);   // 必須在 drawable-colors 之前：它包在這一層外面
  if (RENDER_OPT.state) installStateCache(internal);
  installDrawableColors(internal);
  internal.motionManager.stopAllMotions();
  internal.renderer.setClippingMaskBufferSize(1024);
  const defaults = Float32Array.from(raw.parameters.defaultValues);
  const ids = Array.from(raw.parameters.ids);
  // profile 涵蓋度與模型內容：缺必要的動作／表情就拋錯，缺選用表情與手勢只警告（rig/check.js）
  const vocab = { motions: Object.values(MOTIONS), expressions: Object.values(EXPRESSIONS), gestures: Object.values(GESTURES).filter(g => g !== GESTURES.none) };
  for (const w of checkRig(rig, settings, vocab, ids).warnings) console.warn(`[ui] ${w}`);
  const motions = {}, expressions = {};   // 以模型裡的名稱為 key
  for (const name of NEEDED_MOTIONS) motions[name] = await readJSON(base + settings.FileReferences.Motions[name][0].File);
  for (const name of [...NEEDED_EXPRESSIONS, ...OPTIONAL_EXPRESSIONS, ...GESTURE_EXPRESSIONS]) {
    const item = (settings.FileReferences.Expressions || []).find(e => e.Name === name);
    if (item) expressions[name] = await readJSON(base + item.File);
  }
  const idx = (id) => ids.indexOf(id);
  const P = Object.fromEntries(Object.entries(rig.params).map(([key, id]) => [key, idx(id)]));
  // 自己會眨眼的語意動作（眼皮曲線最小值 < 0.5，從 motion3.json 算，不寫死名稱）：這些動作播放時程序眨眼暫停
  const blinkMotions = new Set(Object.entries(rig.motions).filter(([, name]) => motionBlinks(motions[name])).map(([semantic]) => semantic));
  const procedural = PROCEDURAL ? createProcedural({ index: P, defaults, minimums: raw.parameters.minimumValues, maximums: raw.parameters.maximumValues,
    seed: (Math.random() * 2 ** 32) >>> 0, blinkMotions }) : null;
  // 注意力導演（§4.4）：亂數用另一個 seed，時鐘和程序層相同（time * 1000）
  const attention = PROCEDURAL ? createAttention({ random: createRandom((Math.random() * 2 ** 32) >>> 0) }) : null;
  let context = null;                               // 導演最近一次決定的情境名稱（軌跡與除錯用）
  const physics = createFixedPhysics(PIXI.live2d.CubismPhysics.create(physicsJSON), core, physicsJSON);
  const motionQueue = new PIXI.live2d.CubismMotionManager();
  const expressionQueue = new PIXI.live2d.CubismMotionQueueManager();
  const gestureQueue = new PIXI.live2d.CubismMotionQueueManager();
  // 收回手勢：把所有手勢用到的參數 Overwrite 回預設值，在同一個佇列裡淡入，舊手勢同時淡出
  const gestureIds = [...new Set(GESTURE_EXPRESSIONS.flatMap(n => (expressions[n]?.Parameters || []).map(p => p.Id)))].filter(id => ids.includes(id));
  const fadeOut = Math.max(0, ...GESTURE_EXPRESSIONS.map(n => expressions[n]?.FadeOutTime ?? 0));
  const gestureNone = { Type: 'Live2D Expression', FadeInTime: fadeOut, FadeOutTime: fadeOut,
    Parameters: gestureIds.map(id => ({ Id: id, Value: defaults[ids.indexOf(id)], Blend: 'Overwrite' })) };
  const eyeBlinkIds = settings.Groups?.find(g => g.Name === 'EyeBlink')?.Ids || [];
  const lipSyncIds = settings.Groups?.find(g => g.Name === 'LipSync')?.Ids || [];
  let time = 0, activeMotion = IDLE, activeGesture = GESTURES.none;   // 兩者都是模型裡的名稱（除錯介面回報用）
  let activeSemantic = MOTIONS.idle;                // 目前播放的動作的語意名稱（程序層用）
  let trace = null;                                 // 動作軌跡（量測用）：motionTrace('start') 之後每個計算幀記錄頭、眼參數，bus 訊息與游標移動一併記下；沒開始就不記錄
  const traceMs = (at) => Math.round((at - trace.t0) * 10) / 10;

  function startMotion(semantic) {
    const name = rig.motions[semantic], json = motions[name];
    if (!json) return;
    const motion = PIXI.live2d.CubismMotion.create(json);
    motion.setIsLoop(!!json.Meta.Loop); motion.setIsLoopFadeIn(false);
    motion.setEffectIds(eyeBlinkIds, lipSyncIds);
    motionQueue.startMotionPriority(motion, false, 3);
    activeMotion = name; activeSemantic = semantic;
  }
  function startExpression(semantic) {
    const json = expressions[rig.expressions[semantic] ?? rig.optional[semantic]];
    if (!json) return;
    expressionQueue.startMotion(PIXI.live2d.CubismExpressionMotion.create(json), false, time);
  }
  function startGesture(semantic) {
    const name = semantic === GESTURES.none ? 'none' : rig.gestures[semantic];
    const json = name === 'none' ? gestureNone : expressions[name];
    if (!json) return;
    gestureQueue.startMotion(PIXI.live2d.CubismExpressionMotion.create(json), false, time);
    activeGesture = name;
  }

  // ---------- 排版與進退場 ----------
  const scale = MODEL_H / REGION.h;
  const unit = internal.width / 2048;
  model.scale.set(scale / unit);
  model.x = W / 2 - (REGION.x + REGION.w / 2) * scale;
  const outY = H - MODEL_H, peekY = H - MODEL_H * PEEK_RATIO;
  let poseT = 0, trans = null;                      // 0＝潛伏、1＝出場
  const setPose = (pose, ms) => { trans = { from: poseT, to: pose === 'out' ? 1 : 0, start: performance.now(), ms }; desktop.gaze(pose === 'out'); };
  function updatePose(now) {
    if (!trans) return 0;
    const k = clamp((now - trans.start) / trans.ms, 0, 1);
    const e = trans.to > trans.from ? easeOutBack(k) : easeInOut(k);
    poseT = trans.from + (trans.to - trans.from) * e;
    if (k >= 1) { poseT = trans.to; trans = null; return 0; }
    return Math.sin(Math.PI * k);                    // 過程中身體傾斜用
  }
  const topY = () => peekY + (outY - peekY) * poseT;

  // ---------- 視線 ----------
  let cursor = null, gx = 0, gy = 0;
  const headCenterY = () => topY() + 0.16 * MODEL_H;   // 頭部中心的 y（視窗座標），游標與點擊的 dy 以它為基準（上為正）
  desktop.onCursor((p) => {
    if (trace && cursor) { const d = Math.hypot(p.x - cursor.x, p.y - cursor.y); if (d >= 1) { trace.cursor.px += d; trace.cursor.moves++; if (trace.cursor.at.length < TRACE_MAX) trace.cursor.at.push(traceMs(performance.now())); } }
    cursor = p;
    if (attention) attention.cursor(p.x - W / 2, headCenterY() - p.y, time * 1000);
  });

  // ---------- 狀態機與介面 ----------
  const controller = createController();
  const bubbles = createBubbles($('bubbles'), { onHover: (id, on) => controller.hoverBubble(id, on, Date.now()) });
  const quick = createQuick($('quick'), $('quick-chat'), {
    onOpen: () => { controller.closeQuick(); desktop.toggleChat(); wake(); },
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
    input.open(); desktop.focus(true); wake();
  }
  $('input').addEventListener('input', () => { if (attention) attention.typing(time * 1000); });
  desktop.onToggleInput(toggleInput);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.close(); controller.closeQuick(); } });
  window.addEventListener('blur', () => { input.close(); });

  let poseOut = false;                                // 目前是否出場（決定要不要停格）
  const pendingApplied = [];
  function applyEffects(effects) {
    for (const e of effects) {
      if (e.type === 'pose') { poseOut = e.pose === 'out'; setPose(e.pose, e.ms); }
      else if (e.type === 'motion') startMotion(e.name);
      else if (e.type === 'expression') startExpression(e.name);
      else if (e.type === 'gesture') startGesture(e.name);
      else if (e.type === 'send') desktop.send(e.message);
    }
    if (effects.length) wake();
  }
  desktop.onBus((msg) => {
    if (trace) trace.marks.push({ t: traceMs(performance.now()), type: msg.type, status: msg.status, wake: msg.wake, remaining_s: msg.remaining_s });
    if (msg.type === 'partial' && input.isOpen()) input.setHint(msg.text);
    if (msg.type === 'heard' && input.isOpen()) input.setHint('');
    applyEffects(controller.handle(msg, Date.now()));
    if (attention && msg.type === 'heard') attention.heard(msg.status, time * 1000);   // 要在 controller 處理之後：mode 離開 listening 了，點頭才不會被取消
    pendingApplied.push(msg.id);
    wake();
  });

  // ---------- 點擊穿透、點擊與拖曳 ----------
  const pixel = new Uint8Array(4);
  function alphaAt(x, y) {
    const r = app.renderer.resolution;
    gl.readPixels(Math.floor(x * r), Math.floor((H - y) * r), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    return pixel[3];
  }
  const interactiveAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    return !!(el && el.closest('.interactive')) || alphaAt(x, y) >= ALPHA_HIT;
  };
  let lastHit = 0, dragging = null;
  document.addEventListener('mousemove', (e) => {
    if (dragging) return;
    const now = performance.now();
    if (now - lastHit < 33) return;
    lastHit = now;
    desktop.setIgnore(!interactiveAt(e.clientX, e.clientY));
  });
  document.addEventListener('mouseleave', () => { if (!dragging) desktop.setIgnore(true); });
  document.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || e.target.closest('.interactive') || alphaAt(e.clientX, e.clientY) < ALPHA_HIT) return;
    dragging = { x: e.screenX, y: e.clientY, moved: false };
    document.body.setPointerCapture?.(e.pointerId);
    desktop.dragStart();
  });
  document.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.screenX - dragging.x;
    if (!dragging.moved && Math.abs(dx) < DRAG_PX) return;
    dragging.moved = true;
    desktop.dragMove(dx);
  });
  document.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    const d = dragging; dragging = null;
    if (d.moved) { desktop.dragEnd(); return; }
    const head = (d.y - topY()) / MODEL_H < HEAD_FRACTION;
    if (attention) attention.click(e.clientX - W / 2, headCenterY() - e.clientY, time * 1000);
    applyEffects(controller.click(head ? 'head' : 'body', Date.now()));
  });
  document.addEventListener('contextmenu', (e) => { e.preventDefault(); if (e.target.closest('.interactive') || alphaAt(e.clientX, e.clientY) >= ALPHA_HIT) desktop.menu(); });

  // ---------- 每幀 ----------
  const bubblesEl = $('bubbles'), halo = $('halo'), ring = halo.querySelector('.ring');
  let lastBubbleKey = '', lastBottom = -1, prev = null;
  const partMs = { compute: 0, render: 0, n: 0 };               // 各階段平均耗時（ms），調效能用
  // O5：Core、動作與物理每 2 幀算一次，中間那幀畫內插（RENDER_OPT.halfRate）
  const interp = RENDER_OPT.halfRate ? createInterpolator([...raw.drawables.vertexPositions, raw.drawables.opacities]) : null;
  let computeDt = 0, computeNow = true;
  function renderFrame(now) {
    const dt = prev === null ? 1 / 60 : clamp((now - prev) / 1000, 0, 0.2);   // 停格醒來第一幀從暫停處接續，不跳格
    prev = now; time += dt;
    applyEffects(controller.tick(Date.now()));
    const tilt = updatePose(now);
    const snap = controller.snapshot(Date.now());
    computeDt += dt;
    computeNow = !interp || !computeNow;
    let delta = null;
    const t1 = performance.now();
    if (computeNow) {
      const cdt = Math.min(computeDt, 0.2);
      computeDt = 0;
      raw.parameters.values.set(defaults);
      motionQueue.updateMotion(core, cdt);
      // 程序層接手頭身眼眉（§4.3），時鐘用繪製時才前進的虛擬時間；表情與手勢疊在它上面
      if (procedural) {
        const a = attention.update(time * 1000, { pose: snap.pose, mode: snap.mode, motion: activeSemantic, inputOpen: input.isOpen() });
        context = a.context;
        if (a.plan) procedural.plan(a.plan, time * 1000);
        procedural.apply(raw.parameters.values, cdt, time * 1000, { pose: snap.pose, motion: activeSemantic, listening: a.context === 'listening', swayGain: a.swayGain });
      }
      expressionQueue.doUpdateMotion(core, time);
      gestureQueue.doUpdateMotion(core, time);

      const lean = 1 - clamp(poseT, 0, 1);              // 潛伏時微微歪頭
      const v = raw.parameters.values, mn = raw.parameters.minimumValues, mx = raw.parameters.maximumValues;
      const add = (i, d) => { if (i >= 0) v[i] = clamp(v[i] + d, mn[i], mx[i]); };
      if (!procedural) {                                // 舊做法：Idle 全權＋游標視線
        const headY = headCenterY();
        const wantGaze = snap.pose === 'out' && cursor;
        const tx = wantGaze ? clamp((cursor.x - W / 2) / 400, -1, 1) : 0;
        const ty = wantGaze ? clamp((headY - cursor.y) / 400, -1, 1) : 0;
        const follow = 1 - Math.pow(0.85, cdt * 30);
        gx += (tx - gx) * follow; gy += (ty - gy) * follow;
        add(P.angleX, 30 * gx); add(P.angleY, 20 * gy); add(P.eyeX, gx); add(P.eyeY, gy);
      }
      add(P.angleZ, 6 * lean + 8 * tilt); add(P.bodyZ, 3 * lean + 5 * tilt);
      delta = physics.evaluate(core, cdt);
      if (trace) recordTrace(now);
      if (interp) interp.restore();
      core.update();
      if (interp) interp.computed();
    } else interp.between();
    nextRenderFrame();
    const t3 = performance.now();
    model.y = topY() - REGION.y * scale;
    app.render();
    const t4 = performance.now();
    partMs.compute += t3 - t1; partMs.render += t4 - t3; partMs.n++;
    if (motionQueue.isFinished() && activeMotion !== IDLE) startMotion(MOTIONS.idle);
    updateUi(snap);
    return delta;
  }
  // 記錄這個計算幀的最終頭、眼參數（動作、表情、視線都已疊上，Core 更新之前）
  function recordTrace(now) {
    if (trace.rows.length >= TRACE_MAX) { trace.truncated = true; return; }
    const v = raw.parameters.values, r = (n) => Math.round(n * 1e4) / 1e4;
    trace.rows.push([traceMs(now), r(v[P.angleX]), r(v[P.angleY]), r(v[P.angleZ]), r(v[P.eyeX]), r(v[P.eyeY]), r(v[P.eyeOpen]), NEEDED_MOTIONS.indexOf(activeMotion), context === null ? -1 : CONTEXTS.indexOf(context)]);
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

  // 內部狀態給自動化驗證用（只讀）
  const toRig = (snap) => ({ ...snap, expression: rig.expressions[snap.expression] ?? rig.optional[snap.expression] ?? snap.expression,
    gesture: rig.gestures[snap.gesture] ?? snap.gesture });   // 除錯介面回報模型裡的名稱（ui_demo／ui_motion 的比對用）
  window.__ui = { snapshot: () => toRig(controller.snapshot(Date.now())), poseT: () => poseT, activeMotion: () => activeMotion,
    activeGesture: () => activeGesture, topY, modelH: MODEL_H, frames: 0, alphaAt, drawables: raw.drawables.count };
  // 除錯探針（唯讀，main/debug.js 的 debug_capture 讀它；DOM 選擇器留在畫面這邊，主程序不用知道 id）
  window.__ui.attention = () => (attention ? attention.snapshot() : null);
  window.__ui.probe = () => {
    const center = (el) => { const b = el.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
    return { snap: window.__ui.snapshot(), poseT: window.__ui.poseT(), motion: window.__ui.activeMotion(), gesture: window.__ui.activeGesture(),
      frames: window.__ui.frames, attention: context, input: !$('input-box').hidden, quick: !$('quick').hidden, quickBtn: center($('quick-chat')),
      choices: !$('choices').hidden, choicesTitle: $('choices-title').textContent,
      choiceRows: [...document.querySelectorAll('#choices-list li')].map(li => { const r = li.getBoundingClientRect(), b = li.querySelector('button').getBoundingClientRect();
        return { text: li.textContent, x: r.x + 40, y: r.y + r.height / 2, bx: b.x + b.width / 2, by: b.y + b.height / 2 }; }),
      inputFocused: document.activeElement === $('input'), alphaHead: window.__ui.alphaAt(180, window.__ui.topY() + 80),
      alphaCorner: window.__ui.alphaAt(2, 2), perf: window.__ui.perf(), drawables: window.__ui.drawables };
  };
  window.__ui.submitText = (text) => { $('input').value = text; $('input-box').requestSubmit(); };   // 在輸入框填字並送出（走真正的 submit 路徑）

  for (let i = 0; i < 120; i++) { raw.parameters.values.set(defaults); physics.evaluate(core, 1 / 60); }  // 讓頭髮物理先穩定
  startMotion(MOTIONS.idle); startExpression(EXPRESSIONS.neutral);
  // 動作軌跡（ui_motion 用）：start 開始記錄；stop 回傳 { cols, motions, contexts, rows, marks, cursor, halfRate, truncated } 並清空，沒在記錄回傳 null
  window.__ui.motionTrace = (cmd) => {
    if (cmd === 'start') { trace = { t0: performance.now(), rows: [], marks: [], cursor: { px: 0, moves: 0, at: [] }, truncated: false }; return true; }
    if (cmd !== 'stop' || !trace) return null;
    const { rows, marks, cursor: moved, truncated } = trace;
    trace = null;
    return { cols: TRACE_COLS, motions: NEEDED_MOTIONS, contexts: CONTEXTS, rows, marks, cursor: moved, halfRate: RENDER_OPT.halfRate, truncated };
  };
  // 效能量測（ui_perf 用）：每個實際繪製的幀；perf() 回傳上次讀取以來的統計並清空，方便分階段量
  let frameStats = [];
  const resetParts = () => Object.assign(partMs, { compute: 0, render: 0, n: 0 });
  window.__ui.perf = () => {
    const sorted = (a) => [...a].sort((x, y) => x - y);
    const pct = (a, q) => { const s = sorted(a); return s.length ? s[Math.min(s.length - 1, Math.floor(s.length * q))] : null; };
    const steady = frameStats.filter(f => !f.resume), anim = steady.filter(f => f.trans);
    const deltas = frameStats.filter(f => f.delta !== null).map(f => f.delta);
    const t0 = frameStats.length ? frameStats[0].at : 0;
    const out = { frames: frameStats.length, resumes: frameStats.length - steady.length,
      intervalMedian: pct(steady.map(f => f.dt), 0.5), intervalP95: pct(steady.map(f => f.dt), 0.95),
      drops: steady.filter(f => f.dt > 25).length, renderMsP95: pct(frameStats.map(f => f.ms), 0.95),
      animFrames: anim.length, animIntervalMedian: pct(anim.map(f => f.dt), 0.5), animIntervalP95: pct(anim.map(f => f.dt), 0.95),
      parts: Object.fromEntries(['compute', 'render'].map(k => [k, partMs.n ? +(partMs[k] / partMs.n).toFixed(2) : null])),
      frozen: gate.frozen, frozenMs: gate.frozen ? Math.round(performance.now() - lastRender) : 0,
      delta: { p50: pct(deltas, 0.5), p95: pct(deltas, 0.95), max: deltas.length ? Math.max(...deltas) : null },
      deltaTrace: frameStats.map(f => [Math.round(f.at - t0), f.delta === null ? null : +f.delta.toFixed(5), f.out ? 1 : 0]),
      recent: frameStats.slice(-30).map(f => [Math.round(f.dt), +f.ms.toFixed(1), f.trans ? 1 : 0]) };
    frameStats = []; resetParts();
    return out;
  };
  // 幀迴圈：要畫的時候每個 vsync 用 rAF 畫一幀（60 fps）；潛伏且頭髮物理收尾後停格，不再叫 rAF，
  // 只用低頻計時器推進狀態機與泡泡。任何 effect、後端事件或操作（wake）立刻恢復逐 vsync 繪製。
  const gate = createFrameGate(SETTLE);
  let rafPending = false, lastRender = 0, frozenTimer = null;
  function request() { if (!rafPending) { rafPending = true; requestAnimationFrame(tick); } }
  function wake() {
    gate.wake(performance.now());
    if (frozenTimer) { clearInterval(frozenTimer); frozenTimer = null; }
    request();
  }
  function frozenTick() {
    const now = Date.now();
    applyEffects(controller.tick(now));                 // 有 effect 會 wake，回到逐 vsync 繪製
    if (frozenTimer) updateUi(controller.snapshot(now));
  }
  function tick() {
    rafPending = false;
    const now = performance.now();                    // 不用 rAF 給的時間戳：它是 vsync 時間，可能比 callback 實際執行早很多
    if (prev !== null && now - lastRender < MIN_FRAME_MS) { request(); return; }
    const interval = now - lastRender, resume = prev === null, animating = !!trans;
    lastRender = now;
    const delta = renderFrame(now);
    frameStats.push({ at: now, dt: interval, ms: performance.now() - now, trans: animating, resume, delta, out: poseOut });
    if (frameStats.length > 8000) frameStats.shift();
    window.__ui.frames++;
    if (gate.afterFrame(performance.now(), { out: poseOut, moving: !!trans || activeMotion !== IDLE, delta })) { request(); return; }
    prev = null;                                      // 停格：動作時間不推進，醒來從這裡接續
    if (!frozenTimer && !rafPending) frozenTimer = setInterval(frozenTick, FROZEN_TICK_MS);
  }
  wake();
  window.addEventListener('pagehide', () => app.destroy(false, { children: true }), { once: true });
  console.log('[ui] ready, Cubism Core', Live2DCubismCore.Version.csmGetVersion().toString(16));
  desktop.ready();
}

main().catch((err) => {
  console.error('[ui] 啟動失敗', err && err.stack || err);
  const el = $('status'); el.hidden = false; el.textContent = `啟動失敗：${err && err.message || err}`;
  desktop.ready();
});
