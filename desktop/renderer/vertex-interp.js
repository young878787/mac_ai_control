// O5（計畫書 §6.3，選做）：Core 每 2 幀計算一次（30 Hz），中間那一幀用前後兩次的結果線性內插，畫面維持 60 fps。
// arrays 是 Core 的 drawable 陣列 view（各 drawable 的 vertexPositions 與 opacities），直接寫入讓繪製讀到內插值；
// 顯示比 Core 晚半個計算週期（約 17 ms）。純邏輯，不碰 WebGL。
// 內層迴圈獨立成小函式：寫在 computed 裡會在第一次呼叫就被 OSR 最佳化，之後反覆 deopt（2026-11-20 實測 180 秒 54 次），
// 獨立之後每次呼叫都是一般的最佳化流程
function midpoint(a, p, c) {
  for (let j = 0; j < a.length; j++) a[j] = (p[j] + c[j]) * 0.5;
}

export function createInterpolator(arrays) {
  const prev = arrays.map(a => Float32Array.from(a));
  const curr = arrays.map(a => Float32Array.from(a));
  return {
    /** core.update() 之前：把陣列還原成上次計算的真值（Core 只重寫有變的 drawable，不能留下內插值） */
    restore() { for (let i = 0; i < arrays.length; i++) arrays[i].set(curr[i]); },
    /** core.update() 之後：記下新結果，這一幀畫前後兩次的中點 */
    computed() {
      for (let i = 0; i < arrays.length; i++) {
        const a = arrays[i], p = prev[i], c = curr[i];
        p.set(c); c.set(a);
        midpoint(a, p, c);
      }
    },
    /** 沒有計算的那一幀：畫最新的結果 */
    between() { for (let i = 0; i < arrays.length; i++) arrays[i].set(curr[i]); },
  };
}
