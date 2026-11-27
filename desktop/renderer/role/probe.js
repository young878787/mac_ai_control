// 除錯介面 window.__ui（只讀探針）：給 main/debug.js 的 debug_capture、scripts/ui_demo.py、ui_perf.py、ui_motion.py 讀。
// DOM 選擇器留在這裡，主程序不用知道 id。除錯介面回報模型裡的名稱（不是語意名稱），ui_demo／ui_motion 的比對才不用改。
// 動作軌跡（量測用，見 dev/motion-metrics.js）：motionTrace('start') 之後每個計算幀記錄頭、眼參數，bus 訊息與游標移動一併記下；沒開始就不記錄。
/** @type {(id: string) => any} */
const $ = (id) => document.getElementById(id);
const TRACE_COLS = ['t', 'angleX', 'angleY', 'angleZ', 'eyeX', 'eyeY', 'eyeOpen', 'motion', 'context'];   // 動作軌跡的欄位；motion＝motions 的索引、context＝contexts 的索引（程序層關閉＝-1）
const TRACE_MAX = 50000;                          // 軌跡最多記幾列（30 Hz 計算約 28 分鐘），超過就停止記錄並標 truncated

/**
 * @param {{ rig: any, raw: any, P: Record<string, number>, controller: { snapshot(now: number): any },
 *           animator: ReturnType<typeof import('../animation/animator.js').createAnimator>,
 *           stage: { alphaAt(x: number, y: number): number }, loop: ReturnType<typeof import('../engine/loop.js').createLoop>,
 *           attention: any, context: () => string | null, motions: string[], contexts: readonly string[], halfRate: boolean, modelH: number }} deps
 *   motions：模型裡的動作名稱清單（軌跡的 motion 欄是它的索引）；contexts：注意力導演的情境清單
 */
export function createProbe({ rig, raw, P, controller, animator, stage, loop, attention, context, motions, contexts, halfRate, modelH }) {
  let trace = null;
  let cursor = null;
  const traceMs = (at) => Math.round((at - trace.t0) * 10) / 10;

  const toRig = (snap) => ({ ...snap, expression: rig.expressions[snap.expression] ?? snap.expression,
    gesture: rig.gestures[snap.gesture] ?? snap.gesture });
  const ui = {
    snapshot: () => toRig(controller.snapshot(Date.now())), poseT: animator.poseT, activeMotion: animator.activeMotion,
    activeGesture: animator.activeGesture, topY: animator.topY, modelH, get frames() { return loop.frames; }, alphaAt: stage.alphaAt,
    drawables: raw.drawables.count,
  };
  window.__ui = ui;
  ui.attention = () => (attention ? attention.snapshot() : null);
  ui.perf = () => loop.perf();
  ui.probe = () => {
    const center = (el) => { const b = el.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
    return { snap: ui.snapshot(), poseT: ui.poseT(), motion: ui.activeMotion(), gesture: ui.activeGesture(),
      frames: ui.frames, attention: context(), input: !$('input-box').hidden, quick: !$('quick').hidden, quickBtn: center($('quick-chat')),
      choices: !$('choices').hidden, choicesTitle: $('choices-title').textContent,
      choiceRows: [...document.querySelectorAll('#choices-list li')].map(li => { const r = li.getBoundingClientRect(), b = li.querySelector('button').getBoundingClientRect();
        return { text: li.textContent, x: r.x + 40, y: r.y + r.height / 2, bx: b.x + b.width / 2, by: b.y + b.height / 2 }; }),
      inputFocused: document.activeElement === $('input'), alphaHead: ui.alphaAt(180, ui.topY() + 80),
      alphaCorner: ui.alphaAt(2, 2), perf: ui.perf(), drawables: ui.drawables };
  };
  ui.submitText = (text) => { $('input').value = text; $('input-box').requestSubmit(); };   // 在輸入框填字並送出（走真正的 submit 路徑）
  // start 開始記錄；stop 回傳 { cols, motions, idle, contexts, rows, marks, cursor, halfRate, truncated } 並清空，沒在記錄回傳 null；idle＝待機動作的模型名稱
  ui.motionTrace = (cmd) => {
    if (cmd === 'start') { trace = { t0: performance.now(), rows: [], marks: [], cursor: { px: 0, moves: 0, at: [] }, truncated: false }; return true; }
    if (cmd !== 'stop' || !trace) return null;
    const { rows, marks, cursor: moved, truncated } = trace;
    trace = null;
    return { cols: TRACE_COLS, motions, idle: rig.motions.idle, contexts, rows, marks, cursor: moved, halfRate, truncated };
  };

  return {
    /** 記錄這個計算幀的最終頭、眼參數（動作、表情、視線都已疊上，Core 更新之前） */
    record(now) {
      if (!trace) return;
      if (trace.rows.length >= TRACE_MAX) { trace.truncated = true; return; }
      const v = raw.parameters.values, r = (n) => Math.round(n * 1e4) / 1e4;
      const ctx = context();
      trace.rows.push([traceMs(now), r(v[P.angleX]), r(v[P.angleY]), r(v[P.angleZ]), r(v[P.eyeX]), r(v[P.eyeY]), r(v[P.eyeOpen]), motions.indexOf(animator.activeMotion()), ctx === null ? -1 : contexts.indexOf(ctx)]);
    },
    /** bus 訊息：記一個標記 */
    mark(msg) {
      if (trace) trace.marks.push({ t: traceMs(performance.now()), type: msg.type, status: msg.status, wake: msg.wake, remaining_s: msg.remaining_s });
    },
    /** 游標移動：累計移動距離與時間點（不論有沒有在記錄，都更新上一個位置） */
    cursor(p) {
      if (trace && cursor) { const d = Math.hypot(p.x - cursor.x, p.y - cursor.y); if (d >= 1) { trace.cursor.px += d; trace.cursor.moves++; if (trace.cursor.at.length < TRACE_MAX) trace.cursor.at.push(traceMs(performance.now())); } }
      cursor = p;
    },
  };
}
