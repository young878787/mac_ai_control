// 幀迴圈：要畫的時候每個 vsync 用 rAF 畫一幀（60 fps）；潛伏且頭髮物理收尾後停格，不再叫 rAF，
// 只用低頻計時器推進狀態機與泡泡。任何 effect、後端事件或操作（wake）立刻恢復逐 vsync 繪製。
// 同時記每個實際繪製的幀（ui_perf 用）；perf() 回傳上次讀取以來的統計並清空，方便分階段量。
// 「畫什麼」由呼叫端的 renderFrame 決定，這裡只管何時畫；停格判斷交給注入的 gate（domain/frame-gate.js）。

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * @param {{ gate: { frozen: boolean, wake(now: number): void, afterFrame(now: number, state: object): boolean },
 *           minFrameMs: number, frozenTickMs: number,
 *           renderFrame: (now: number, dt: number) => number | null,
 *           onFrozenTick: (stillFrozen: () => boolean) => void,
 *           animating: () => boolean, out: () => boolean, moving: () => boolean }} options
 *   minFrameMs：120 Hz 螢幕上 rAF 會到 120 次／秒，間隔小於這個值的那次跳過；frozenTickMs：停格期間狀態機與泡泡的更新間隔；
 *   animating／out／moving：進退場中、目前是否出場、動作或進退場是否還在動（給停格判斷與統計）
 */
export function createLoop({ gate, minFrameMs, frozenTickMs, renderFrame, onFrozenTick, animating, out, moving }) {
  let rafPending = false, lastRender = 0, frozenTimer = null, prev = null, frames = 0;
  let frameStats = [];
  const parts = { compute: 0, render: 0, n: 0 };   // 各階段平均耗時（ms），調效能用；renderFrame 累加
  const stillFrozen = () => frozenTimer !== null;

  function request() { if (!rafPending) { rafPending = true; requestAnimationFrame(tick); } }
  function wake() {
    gate.wake(performance.now());
    if (frozenTimer) { clearInterval(frozenTimer); frozenTimer = null; }
    request();
  }
  function frozenTick() { onFrozenTick(stillFrozen); }
  function tick() {
    rafPending = false;
    const now = performance.now();                    // 不用 rAF 給的時間戳：它是 vsync 時間，可能比 callback 實際執行早很多
    if (prev !== null && now - lastRender < minFrameMs) { request(); return; }
    const interval = now - lastRender, resume = prev === null, animatingNow = animating();
    const dt = prev === null ? 1 / 60 : clamp((now - prev) / 1000, 0, 0.2);   // 停格醒來第一幀從暫停處接續，不跳格
    lastRender = now; prev = now;
    const delta = renderFrame(now, dt);
    const outNow = out();
    frameStats.push({ at: now, dt: interval, ms: performance.now() - now, trans: animatingNow, resume, delta, out: outNow });
    if (frameStats.length > 8000) frameStats.shift();
    frames++;
    if (gate.afterFrame(performance.now(), { out: outNow, moving: moving(), delta })) { request(); return; }
    prev = null;                                      // 停格：動作時間不推進，醒來從這裡接續
    if (!frozenTimer && !rafPending) frozenTimer = setInterval(frozenTick, frozenTickMs);
  }

  function perf() {
    const sorted = (a) => [...a].sort((x, y) => x - y);
    const pct = (a, q) => { const s = sorted(a); return s.length ? s[Math.min(s.length - 1, Math.floor(s.length * q))] : null; };
    const steady = frameStats.filter(f => !f.resume), anim = steady.filter(f => f.trans);
    const deltas = frameStats.filter(f => f.delta !== null).map(f => f.delta);
    const t0 = frameStats.length ? frameStats[0].at : 0;
    const result = { frames: frameStats.length, resumes: frameStats.length - steady.length,
      intervalMedian: pct(steady.map(f => f.dt), 0.5), intervalP95: pct(steady.map(f => f.dt), 0.95),
      drops: steady.filter(f => f.dt > 25).length, renderMsP95: pct(frameStats.map(f => f.ms), 0.95),
      animFrames: anim.length, animIntervalMedian: pct(anim.map(f => f.dt), 0.5), animIntervalP95: pct(anim.map(f => f.dt), 0.95),
      parts: Object.fromEntries(['compute', 'render'].map(k => [k, parts.n ? +(parts[k] / parts.n).toFixed(2) : null])),
      frozen: gate.frozen, frozenMs: gate.frozen ? Math.round(performance.now() - lastRender) : 0,
      delta: { p50: pct(deltas, 0.5), p95: pct(deltas, 0.95), max: deltas.length ? Math.max(...deltas) : null },
      deltaTrace: frameStats.map(f => [Math.round(f.at - t0), f.delta === null ? null : +f.delta.toFixed(5), f.out ? 1 : 0]),
      recent: frameStats.slice(-30).map(f => [Math.round(f.dt), +f.ms.toFixed(1), f.trans ? 1 : 0]) };
    frameStats = [];
    Object.assign(parts, { compute: 0, render: 0, n: 0 });
    return result;
  }

  return { wake, perf, parts, get frames() { return frames; } };
}
