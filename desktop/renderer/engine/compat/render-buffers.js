// O1（計畫書 §6.3）：pixi-live2d-display 0.4.0 的每個 drawable 都共用同一組 GL buffer，每次繪製都重新 bufferData 頂點、UV、索引三份。
// 這裡改成每個 drawable 有自己的 buffer：UV 與索引只上傳一次；頂點用 bufferSubData 更新（不重新配置）。
// 不改 node_modules，只包 setupShaderProgram 與 gl.bufferData（和 drawable-colors.js 一樣是 prototype 修補）。
// 包裝函式只在啟動時裝一次；每次繪製只更新下面的模組變數。2026-11-20 實測：每次繪製新建 closure、換掉 gl 的方法，
// 會讓 V8 最佳化好的 drawMesh／bindTexture 在每次 GC 後被打掉重編，造成 CPU 尖峰（見計畫書 §13）。
const states = new WeakMap();
let installed = false, frame = 0;
let wrappedUpload = null;                                         // 已包過 bufferData 的 gl
let curEntry = null, curUv = null, curIndex = null, curVertex = null;   // 目前這次 setupShaderProgram 的 drawable 與它的三份資料
/** 每次 core.update() 之後呼叫：頂點換成新的一幀 */
export function nextRenderFrame() { frame++; }

function wrapBufferData(gl) {
  wrappedUpload = gl;
  const upload = gl.bufferData;                                   // WebGL1／2 都適用：先記下原本的方法
  gl.bufferData = function (target, data, usage) {
    const entry = curEntry;
    if (entry !== null) {
      if (data === curUv) { if (entry.uv) return; entry.uv = true; }
      else if (data === curIndex) { if (entry.index) return; entry.index = true; }
      else if (data === curVertex) {
        // 同一幀裡遮罩和本體各畫一次，只傳一次；頂點數不變，之後用 bufferSubData 覆寫
        if (entry.vertex === frame) return;
        const first = entry.vertex < 0;
        entry.vertex = frame;
        if (!first) { gl.bufferSubData(target, 0, data); return; }
      }
    }
    upload.call(gl, target, data, usage);
  };
}

export function installRenderBuffers(internalModel) {
  const renderer = internalModel.renderer;
  states.set(renderer, new Map());   // vertexPositions（Core 的固定 view）→ 這個 drawable 的 buffer 與上傳紀錄
  if (installed) return;
  const prototype = PIXI.live2d.CubismShader_WebGL.prototype;
  const setupShaderProgram = prototype.setupShaderProgram;
  prototype.setupShaderProgram = function (owner, textureId, vertexCount, vertexArray, indexArray, uvArray, bufferData, opacity, colorBlendMode,
    baseColor, isPremultipliedAlpha, matrix4x4, invertedMask) {
    const entries = states.get(owner);
    if (entries === undefined) {
      return setupShaderProgram.call(this, owner, textureId, vertexCount, vertexArray, indexArray, uvArray, bufferData, opacity, colorBlendMode,
        baseColor, isPremultipliedAlpha, matrix4x4, invertedMask);
    }
    let entry = entries.get(vertexArray);
    if (entry === undefined) {
      entry = { buffers: { vertex: null, uv: null, index: null }, vertex: -1, uv: false, index: false };
      entries.set(vertexArray, entry);
    }
    if (wrappedUpload !== this.gl) wrapBufferData(this.gl);
    curEntry = entry; curUv = uvArray; curIndex = indexArray; curVertex = vertexArray;
    try {
      setupShaderProgram.call(this, owner, textureId, vertexCount, vertexArray, indexArray, uvArray, entry.buffers, opacity, colorBlendMode,
        baseColor, isPremultipliedAlpha, matrix4x4, invertedMask);
    } finally {
      curEntry = null;
    }
  };
  installed = true;
}

// O2（計畫書 §6.3）：繪製模型期間省掉重複的 GL 狀態呼叫。pixi-live2d-display 每個 mesh 都會 useProgram(null)、重設 CULL_FACE／frontFace、
// 重新綁同一張貼圖與 sampler；doDrawModel 執行期間相同的值就不送，結束時把最後的狀態還原成原本會留下的樣子。
// gl 的方法同樣只包一次，平常（cache.on＝false）原樣轉呼叫。
const cache = { on: false, program: undefined, cull: undefined, front: undefined, unit: undefined };   // undefined＝還不知道，第一次一定送
const UNITS = 32;
const textures = new Array(UNITS).fill(undefined);   // 每個貼圖單元目前綁的 TEXTURE_2D
const ints = new Map();                              // uniform1i 的位置 → 最後一次送的值
let wrappedState = null, real = null;

function wrapState(gl) {
  wrappedState = gl;
  const r = real = { useProgram: gl.useProgram, enable: gl.enable, disable: gl.disable, frontFace: gl.frontFace,
    activeTexture: gl.activeTexture, bindTexture: gl.bindTexture, uniform1i: gl.uniform1i };
  const CULL = gl.CULL_FACE, TEX2D = gl.TEXTURE_2D, UNIT0 = gl.TEXTURE0;
  gl.useProgram = function (p) {
    if (cache.on) { if (p === null || p === cache.program) return; cache.program = p; }
    r.useProgram.call(gl, p);
  };
  gl.enable = function (cap) {
    if (cache.on && cap === CULL) { if (cache.cull === true) return; cache.cull = true; }
    r.enable.call(gl, cap);
  };
  gl.disable = function (cap) {
    if (cache.on && cap === CULL) { if (cache.cull === false) return; cache.cull = false; }
    r.disable.call(gl, cap);
  };
  gl.frontFace = function (mode) {
    if (cache.on) { if (mode === cache.front) return; cache.front = mode; }
    r.frontFace.call(gl, mode);
  };
  gl.activeTexture = function (unit) {
    if (cache.on) { if (unit === cache.unit) return; cache.unit = unit; }
    r.activeTexture.call(gl, unit);
  };
  gl.bindTexture = function (target, tex) {
    if (cache.on && target === TEX2D && cache.unit !== undefined) {   // 單元還不知道時一定送
      const n = cache.unit - UNIT0;
      if (n >= 0 && n < UNITS) { if (textures[n] === tex) return; textures[n] = tex; }
    }
    r.bindTexture.call(gl, target, tex);
  };
  gl.uniform1i = function (location, value) {
    if (cache.on) { if (ints.get(location) === value) return; ints.set(location, value); }
    r.uniform1i.call(gl, location, value);
  };
}

export function installStateCache(internalModel) {
  const renderer = internalModel.renderer;
  const doDrawModel = renderer.doDrawModel;
  renderer.doDrawModel = function () {
    const gl = this.gl;
    if (wrappedState !== gl) wrapState(gl);
    cache.program = cache.cull = cache.front = cache.unit = undefined;
    textures.fill(undefined); ints.clear();
    cache.on = true;
    try {
      return doDrawModel.call(this);
    } finally {
      cache.on = false;
      if (cache.program !== undefined) real.useProgram.call(gl, null);   // 原本每個 mesh 畫完都 useProgram(null)，最後狀態維持一樣
    }
  };
}
