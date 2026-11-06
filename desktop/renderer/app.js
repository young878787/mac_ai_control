// 畫面程序：載入 Live2D 模型、依狀態機演出、處理點擊穿透／拖曳／輸入。
import { createController, PEEK_RATIO } from '../state.js';
import { createFixedPhysics } from './physics-clock.js';
import { installDrawableColors } from './drawable-colors.js';
import { createBubbles, createHistory, createInputBox } from './ui.js';

const MODEL_H = 420;                              // 出場時模型高度（px）
const REGION = { x: 380, y: 0, w: 1280, h: 2048 };  // 模型座標中的「全身」範圍（與預覽相同）
const HEAD_FRACTION = 0.30;                       // 模型頂端往下這個比例內算「頭部」
const NEEDED_MOTIONS = ['Idle', 'Surprised', 'Curious', 'Nod', 'Shake', 'Happy'];  // 不載入 Talk
const NEEDED_EXPRESSIONS = ['neutral', 'happy', 'pout', 'sleepy'];
const ALPHA_HIT = 25;                             // 游標下像素 alpha ≥ 這個值（約 10%）算點到角色
const DRAG_PX = 4;
// 幀率分三階：動作或進退場進行中／出場但靜止／潛伏。每幀成本主要是 Electron 在 macOS 合成透明視窗的固定開銷（約 1.3% CPU／fps），所以該動的時候才給高幀率
const FPS = { busy: 30, idleOut: 12, peek: 5 };
const BUSY_TAIL_MS = 700;                         // 事件或動作之後維持高幀率的時間（涵蓋表情淡入淡出）

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
  const motions = {}, expressions = {};
  for (const name of NEEDED_MOTIONS) {
    const item = (settings.FileReferences.Motions || {})[name]?.[0];
    if (!item) throw new Error(`模型缺少動作：${name}`);
    motions[name] = await readJSON(base + item.File);
  }
  for (const name of NEEDED_EXPRESSIONS) {
    const item = (settings.FileReferences.Expressions || []).find(e => e.Name === name);
    if (!item) throw new Error(`模型缺少表情：${name}`);
    expressions[name] = await readJSON(base + item.File);
  }

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
  installDrawableColors(internal);
  internal.motionManager.stopAllMotions();
  internal.renderer.setClippingMaskBufferSize(1024);
  const defaults = Float32Array.from(raw.parameters.defaultValues);
  const ids = Array.from(raw.parameters.ids);
  const idx = (id) => ids.indexOf(id);
  const P = { angleX: idx('ParamAngleX'), angleY: idx('ParamAngleY'), angleZ: idx('ParamAngleZ'), bodyZ: idx('ParamBodyAngleZ'),
    eyeX: idx('ParamEyeBallX'), eyeY: idx('ParamEyeBallY') };
  const physics = createFixedPhysics(PIXI.live2d.CubismPhysics.create(physicsJSON), core, physicsJSON);
  const motionQueue = new PIXI.live2d.CubismMotionManager();
  const expressionQueue = new PIXI.live2d.CubismMotionQueueManager();
  const eyeBlinkIds = settings.Groups?.find(g => g.Name === 'EyeBlink')?.Ids || [];
  const lipSyncIds = settings.Groups?.find(g => g.Name === 'LipSync')?.Ids || [];
  let time = 0, activeMotion = 'Idle';

  function startMotion(name) {
    const json = motions[name];
    if (!json) return;
    const motion = PIXI.live2d.CubismMotion.create(json);
    motion.setIsLoop(!!json.Meta.Loop); motion.setIsLoopFadeIn(false);
    motion.setEffectIds(eyeBlinkIds, lipSyncIds);
    motionQueue.startMotionPriority(motion, false, 3);
    activeMotion = name;
  }
  function startExpression(name) {
    if (!expressions[name]) return;
    expressionQueue.startMotion(PIXI.live2d.CubismExpressionMotion.create(expressions[name]), false, time);
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
  desktop.onCursor((p) => { cursor = p; });

  // ---------- 狀態機與介面 ----------
  const controller = createController();
  const bubbles = createBubbles($('bubbles'), { onHover: (id, on) => controller.hoverBubble(id, on, Date.now()) });
  const history = createHistory($('history'), $('history-list'), $('history-close'), {
    onClose: () => { applyEffects(controller.setHistoryOpen(false, Date.now())); if (!input.isOpen()) desktop.focus(false); } });
  const input = createInputBox($('input-box'), $('input'), {
    onSubmit: (text) => { desktop.send({ type: 'command', text }); applyEffects(controller.userCommand(text, Date.now())); },
    onClose: () => { applyEffects(controller.setInputOpen(false, Date.now())); if (!history.isOpen()) desktop.focus(false); } });
  function toggleInput() {
    if (input.isOpen()) { input.close(); return; }
    applyEffects(controller.setInputOpen(true, Date.now()));
    input.open(); desktop.focus(true); wake();
  }
  function toggleHistory() {
    if (history.isOpen()) { history.close(); return; }
    applyEffects(controller.setHistoryOpen(true, Date.now()));
    history.open(); desktop.focus(true); wake();
  }
  desktop.onToggleInput(toggleInput);
  desktop.onToggleHistory(toggleHistory);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.close(); history.close(); } });
  window.addEventListener('blur', () => { input.close(); history.close(); });

  let poseOut = false, busyUntil = 0;                 // 目前是否出場、高幀率維持到何時（決定幀率）
  const pendingApplied = [];
  function applyEffects(effects) {
    for (const e of effects) {
      if (e.type === 'pose') { poseOut = e.pose === 'out'; setPose(e.pose, e.ms); }
      else if (e.type === 'motion') startMotion(e.group);
      else if (e.type === 'expression') startExpression(e.name);
      else if (e.type === 'send') desktop.send(e.message);
    }
    if (effects.length) { busyUntil = performance.now() + BUSY_TAIL_MS; wake(); }
  }
  desktop.onBus((msg) => {
    if (msg.type === 'partial' && input.isOpen()) input.setHint(msg.text);
    if (msg.type === 'heard' && input.isOpen()) input.setHint('');
    applyEffects(controller.handle(msg, Date.now()));
    pendingApplied.push(msg.id);
    busyUntil = performance.now() + BUSY_TAIL_MS;
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
    applyEffects(controller.click(head ? 'head' : 'body', Date.now()));
  });
  document.addEventListener('contextmenu', (e) => { e.preventDefault(); if (e.target.closest('.interactive') || alphaAt(e.clientX, e.clientY) >= ALPHA_HIT) desktop.menu(); });

  // ---------- 每幀 ----------
  const bubblesEl = $('bubbles'), halo = $('halo'), ring = halo.querySelector('.ring');
  let lastBubbleKey = '', lastBottom = -1, prev = null;
  const partMs = { physics: 0, coreUpdate: 0, render: 0, n: 0 };               // 各階段平均耗時（ms），調效能用
  function renderFrame(now) {
    const dt = prev === null ? 1 / 30 : clamp((now - prev) / 1000, 0, 0.2);
    prev = now; time += dt;
    applyEffects(controller.tick(Date.now()));
    const tilt = updatePose(now);
    raw.parameters.values.set(defaults);
    motionQueue.updateMotion(core, dt);
    expressionQueue.doUpdateMotion(core, time);

    const snap = controller.snapshot(Date.now());
    const headY = topY() + 0.16 * MODEL_H;
    const wantGaze = snap.pose === 'out' && cursor;
    const tx = wantGaze ? clamp((cursor.x - W / 2) / 400, -1, 1) : 0;
    const ty = wantGaze ? clamp((headY - cursor.y) / 400, -1, 1) : 0;
    const follow = 1 - Math.pow(0.85, dt * 30);
    gx += (tx - gx) * follow; gy += (ty - gy) * follow;
    const lean = 1 - clamp(poseT, 0, 1);              // 潛伏時微微歪頭
    const v = raw.parameters.values, mn = raw.parameters.minimumValues, mx = raw.parameters.maximumValues;
    const add = (i, d) => { if (i >= 0) v[i] = clamp(v[i] + d, mn[i], mx[i]); };
    add(P.angleX, 30 * gx); add(P.angleY, 20 * gy); add(P.eyeX, gx); add(P.eyeY, gy);
    add(P.angleZ, 6 * lean + 8 * tilt); add(P.bodyZ, 3 * lean + 5 * tilt);
    const t1 = performance.now();
    physics.evaluate(core, dt);
    const t2 = performance.now();
    core.update();
    const t3 = performance.now();
    model.y = topY() - REGION.y * scale;
    app.render();
    const t4 = performance.now();
    partMs.physics += t2 - t1; partMs.coreUpdate += t3 - t2; partMs.render += t4 - t3; partMs.n++;
    if (motionQueue.isFinished() && activeMotion !== 'Idle') startMotion('Idle');

    const bottom = Math.round(H - topY() + 8);
    if (bottom !== lastBottom) { lastBottom = bottom; bubblesEl.style.bottom = `${bottom - 14}px`; bubblesEl.style.maxHeight = `${Math.max(0, H - bottom + 14)}px`; }
    const key = snap.bubbles.map(b => `${b.id}|${b.kind}|${b.text}`).join('\n');
    if (key !== lastBubbleKey) { lastBubbleKey = key; bubbles.render(snap.bubbles); }
    history.render(snap.history);
    if (snap.session.active) {
      halo.style.display = 'block';
      halo.style.left = `${W / 2 - 70}px`; halo.style.top = `${Math.round(topY() - 22)}px`;
      ring.setAttribute('stroke-dasharray', `${(snap.session.ratio * 100).toFixed(1)} 100`);
    } else halo.style.display = 'none';
    while (pendingApplied.length) desktop.applied(pendingApplied.shift(), Date.now());
    return snap;
  }

  // 內部狀態給自動化驗證用（只讀）
  window.__ui = { snapshot: () => controller.snapshot(Date.now()), poseT: () => poseT, activeMotion: () => activeMotion,
    topY, modelH: MODEL_H, frames: 0, alphaAt, drawables: raw.drawables.count };

  for (let i = 0; i < 120; i++) { raw.parameters.values.set(defaults); physics.evaluate(core, 1 / 60); }  // 讓頭髮物理先穩定
  startMotion('Idle'); startExpression('neutral');
  const frameStats = [];                              // 效能量測：每個實際繪製的幀（與上一幀的間隔、繪製耗時、是否在進退場動畫中）
  window.__ui.perf = () => {
    const p95 = (a) => a.length ? [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * 0.95))] : null;
    const anim = frameStats.filter(f => f.trans);
    return { recent: frameStats.slice(-30).map(f => [Math.round(f.dt), +f.ms.toFixed(1), f.trans ? 1 : 0]), frames: frameStats.length, animFrames: anim.length, animIntervalP95: p95(anim.map(f => f.dt)),
      parts: Object.fromEntries(['physics', 'coreUpdate', 'render'].map(k => [k, partMs.n ? +(partMs[k] / partMs.n).toFixed(2) : null])),
      animIntervalMedian: anim.length ? anim.map(f => f.dt).sort((x, y) => x - y)[anim.length >> 1] : null, renderMsP95: p95(frameStats.map(f => f.ms)) };
  };
  // 幀迴圈：高幀率（動作／進退場中）逐 vsync 用 rAF 輪詢、到時間才畫，間隔最均勻；低幀率改用計時器等到下一幀才叫 rAF，
  // 這樣潛伏時不會以 60 Hz 空轉。事件到達（wake）時不管幀率立刻補一幀。
  let timer = null, rafPending = false, forced = true, lastRender = 0;
  const RAF_LEAD_MS = 8;                              // 計時器提早這麼多喚醒，讓 rAF 對齊到目標的那個 vsync
  const isBusy = () => !!trans || activeMotion !== 'Idle' || performance.now() < busyUntil;
  const currentFps = () => (isBusy() ? FPS.busy : (poseOut ? FPS.idleOut : FPS.peek));
  function request() { if (!rafPending) { rafPending = true; requestAnimationFrame(tick); } }
  function wake() { clearTimeout(timer); forced = true; request(); }
  function tick() {
    rafPending = false;
    const now = performance.now();                    // 不用 rAF 給的時間戳：它是 vsync 時間，可能比 callback 實際執行早很多
    const period = 1000 / currentFps();
    if (!forced && now - lastRender < period - 2) { request(); return; }   // 還沒到時間
    forced = false;
    const interval = now - lastRender, animating = !!trans;
    lastRender = now;
    renderFrame(now);
    frameStats.push({ dt: interval, ms: performance.now() - now, trans: animating });
    if (frameStats.length > 4000) frameStats.shift();
    window.__ui.frames++;
    if (isBusy()) { request(); return; }
    const next = 1000 / currentFps() - (performance.now() - now) - RAF_LEAD_MS;
    clearTimeout(timer);
    timer = setTimeout(request, Math.max(0, next));
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
