// 動畫器：把狀態機的語意名稱演成模型的動作／表情／手勢（三個佇列）、管進退場，並在每個計算幀把所有參數疊好直到物理。
// 語意名稱翻成模型名稱只在這裡做（查 rig profile）；不認得的選用表情照舊略過。時間由呼叫端經 advance(dt) 推進，不讀時鐘。
// 每個計算幀的順序固定：預設值 → 動作 → 程序層（頭身眼眉）→ 表情 → 手勢 → 潛伏歪頭與進退場傾斜 → 物理。
// 之後的軌跡記錄、頂點內插與 core.update() 由呼叫端接手（renderer/role/app.js）。

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const easeOutBack = (k) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2); };
const easeInOut = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);

/**
 * @param {{ rig: any, idle: string, noGesture: string, peekRatio: number, size: { w: number, h: number },
 *           model: any, internal: any, core: any, raw: any, defaults: Float32Array, ids: string[], P: Record<string, number>,
 *           motions: Record<string, any>, expressions: Record<string, any>, eyeBlinkIds: string[], lipSyncIds: string[],
 *           physics: { evaluate(core: any, dt: number): number | null }, procedural: any }} deps
 *   idle／noGesture：domain 的語意名稱（待機動作、收回手勢）；motions／expressions：以模型裡的名稱為 key 的 JSON；
 *   P：參數鍵 → 參數索引（-1＝模型沒有）；procedural：程序層（null＝舊做法：Idle 全權＋游標視線）
 */
export function createAnimator({ rig, idle, noGesture, peekRatio, size, model, internal, core, raw, defaults, ids, P, motions, expressions,
  eyeBlinkIds, lipSyncIds, physics, procedural }) {
  const { height: MODEL_H, region: REGION } = rig.layout;
  const W = size.w;
  const IDLE = rig.motions[idle];
  const gestureNames = Object.values(rig.gestures);

  const motionQueue = new PIXI.live2d.CubismMotionManager();
  const expressionQueue = new PIXI.live2d.CubismMotionQueueManager();
  const gestureQueue = new PIXI.live2d.CubismMotionQueueManager();
  // 收回手勢：把所有手勢用到的參數 Overwrite 回預設值，在同一個佇列裡淡入，舊手勢同時淡出
  const gestureIds = [...new Set(gestureNames.flatMap(n => (expressions[n]?.Parameters || []).map(p => p.Id)))].filter(id => ids.includes(id));
  const fadeOut = Math.max(0, ...gestureNames.map(n => expressions[n]?.FadeOutTime ?? 0));
  const gestureNone = { Type: 'Live2D Expression', FadeInTime: fadeOut, FadeOutTime: fadeOut,
    Parameters: gestureIds.map(id => ({ Id: id, Value: defaults[ids.indexOf(id)], Blend: 'Overwrite' })) };

  let time = 0, activeMotion = IDLE, activeGesture = noGesture;   // 兩者都是模型裡的名稱（除錯介面回報用）
  let activeSemantic = idle;                        // 目前播放的動作的語意名稱（程序層用）

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
    const name = semantic === noGesture ? 'none' : rig.gestures[semantic];
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
  const outY = size.h - MODEL_H, peekY = size.h - MODEL_H * peekRatio;
  let poseT = 0, trans = null;                      // 0＝潛伏、1＝出場
  const setPose = (pose, ms) => { trans = { from: poseT, to: pose === 'out' ? 1 : 0, start: performance.now(), ms }; };
  function updatePose(now) {
    if (!trans) return 0;
    const k = clamp((now - trans.start) / trans.ms, 0, 1);
    const e = trans.to > trans.from ? easeOutBack(k) : easeInOut(k);
    poseT = trans.from + (trans.to - trans.from) * e;
    if (k >= 1) { poseT = trans.to; trans = null; return 0; }
    return Math.sin(Math.PI * k);                    // 過程中身體傾斜用
  }
  const topY = () => peekY + (outY - peekY) * poseT;
  const headCenterY = () => topY() + 0.16 * MODEL_H;   // 頭部中心的 y（視窗座標），游標與點擊的 dy 以它為基準（上為正）

  // ---------- 舊做法的游標視線（沒有程序層時） ----------
  let cursor = null, gx = 0, gy = 0;

  /**
   * 一個計算幀：疊好所有參數，跑物理，回傳物理輸出每步最大變化量（沒跑固定步長則 null）。
   * @param {number} cdt 這個計算幀涵蓋的秒數
   * @param {{ pose: string }} snap 狀態機快照
   * @param {number} tilt updatePose 的傾斜值
   * @param {any} a 注意力導演這一幀的決定（沒有程序層時 null）
   */
  function compute(cdt, snap, tilt, a) {
    raw.parameters.values.set(defaults);
    motionQueue.updateMotion(core, cdt);
    // 程序層接手頭身眼眉（計畫書 §4.3），時鐘用繪製時才前進的虛擬時間；表情與手勢疊在它上面
    if (procedural) {
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
    return physics.evaluate(core, cdt);
  }

  return {
    get time() { return time; },
    advance(dt) { time += dt; },
    startMotion, startExpression, startGesture, setPose, updatePose, compute, topY, headCenterY,
    setCursor(p) { cursor = p; },
    /** 畫之前：模型的 y 跟著進退場 */
    place() { model.y = topY() - REGION.y * scale; },
    /** 非待機動作播完就回到待機 */
    settleIdle() { if (motionQueue.isFinished() && activeMotion !== IDLE) startMotion(idle); },
    animating: () => !!trans,
    moving: () => !!trans || activeMotion !== IDLE,
    poseT: () => poseT,
    activeMotion: () => activeMotion,
    activeGesture: () => activeGesture,
    activeSemantic: () => activeSemantic,
  };
}
