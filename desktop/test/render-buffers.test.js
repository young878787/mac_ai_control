import test from 'node:test';
import assert from 'node:assert/strict';
import { installRenderBuffers, installStateCache, nextRenderFrame } from '../renderer/render-buffers.js';

// 假的 WebGL 與假的 pixi-live2d-display 繪製：只記錄呼叫，驗證省略規則與「包裝函式只裝一次」
const PROGRAM = { program: 1 }, LOCATION = { location: 1 };
const CULL = 2884, TEX2D = 3553, TEXTURE0 = 33984;

function makeGl() {
  const gl = { CULL_FACE: CULL, TEXTURE_2D: TEX2D, TEXTURE0, ARRAY_BUFFER: 34962, log: [] };
  for (const name of ['useProgram', 'enable', 'disable', 'frontFace', 'activeTexture', 'bindTexture', 'uniform1i', 'bufferData', 'bufferSubData']) {
    gl[name] = (...args) => gl.log.push([name, ...args]);
  }
  return gl;
}
// 之後對這些屬性的指派次數（包裝函式只裝一次，繪製時不能再換掉 gl 的方法）
function countAssignments(gl, names) {
  const sets = { n: 0 };
  for (const name of names) {
    let value = gl[name];
    Object.defineProperty(gl, name, { get: () => value, set: (v) => { value = v; sets.n++; }, configurable: true });
  }
  return sets;
}
const count = (gl, name, pick = () => true) => gl.log.filter(([n, ...rest]) => n === name && pick(...rest)).length;

class FakeShader {
  constructor(gl) { this.gl = gl; this.buffers = []; }
  setupShaderProgram(owner, textureId, vertexCount, vertexArray, indexArray, uvArray, bufferData) {
    const gl = this.gl;
    gl.useProgram(PROGRAM);
    gl.activeTexture(TEXTURE0);
    gl.bindTexture(TEX2D, textureId);
    gl.uniform1i(LOCATION, 0);
    this.buffers.push(bufferData);
    gl.bufferData(gl.ARRAY_BUFFER, vertexArray, 35048);
    gl.bufferData(gl.ARRAY_BUFFER, uvArray, 35048);
    gl.bufferData(34963, indexArray, 35048);
  }
}
globalThis.PIXI = { live2d: { CubismShader_WebGL: FakeShader } };

function setup() {
  const gl = makeGl(), shader = new FakeShader(gl);
  const drawables = [0, 1].map(i => ({ v: new Float32Array(8).fill(i), uv: new Float32Array(8), i: new Uint16Array(6), tex: { tex: i } }));
  const renderer = { gl, drawn: 0 };
  // 假的 doDrawModel：每個 drawable 先設剔除與繞向、畫、再 useProgram(null)，像 pixi-live2d-display 的 drawMesh
  renderer.doDrawModel = function () {
    for (const d of drawables) {
      gl.disable(CULL); gl.frontFace(2305);
      shader.setupShaderProgram(renderer, d.tex, 4, d.v, d.i, d.uv, { vertex: null, uv: null, index: null });
      gl.useProgram(null);
    }
  };
  const drawBoth = () => { for (const d of drawables) shader.setupShaderProgram(renderer, d.tex, 4, d.v, d.i, d.uv, { vertex: null, uv: null, index: null }); };
  return { gl, shader, renderer, drawables, drawBoth, model: { renderer } };
}

// ---------- O1 ----------
test('O1：UV 與索引只上傳一次；頂點第一次 bufferData、之後每幀一次 bufferSubData', () => {
  const { gl, shader, drawables, drawBoth, model } = setup();
  installRenderBuffers(model);
  const [a, b] = drawables;
  nextRenderFrame(); drawBoth(); drawBoth();   // 同一幀畫兩次（遮罩＋本體）
  for (const d of drawables) {
    assert.equal(count(gl, 'bufferData', (_t, data) => data === d.v), 1);
    assert.equal(count(gl, 'bufferData', (_t, data) => data === d.uv), 1);
    assert.equal(count(gl, 'bufferData', (_t, data) => data === d.i), 1);
  }
  assert.equal(count(gl, 'bufferSubData'), 0);
  nextRenderFrame(); drawBoth(); drawBoth();
  assert.equal(count(gl, 'bufferData'), 6);   // 沒有新的 bufferData
  assert.equal(count(gl, 'bufferSubData', (_t, _o, data) => data === a.v), 1);
  assert.equal(count(gl, 'bufferSubData', (_t, _o, data) => data === b.v), 1);
  assert.equal(count(gl, 'bufferSubData', (_t, _o, data) => data === a.uv || data === a.i), 0);
  // 每個 drawable 有自己的 buffer 物件，而且每幀同一個
  const [bufferOfA, bufferOfB] = shader.buffers;   // 每次 drawBoth 依序畫 a、b
  assert.notEqual(bufferOfA, bufferOfB);
  assert.ok(shader.buffers.every((x, i) => x === (i % 2 === 0 ? bufferOfA : bufferOfB)));
});

test('O1：包裝函式只裝一次，繪製之外原樣轉呼叫', () => {
  const { gl, drawBoth, model } = setup();
  installRenderBuffers(model);
  nextRenderFrame(); drawBoth();
  const wrapped = gl.bufferData, sets = countAssignments(gl, ['bufferData']);
  nextRenderFrame(); drawBoth(); nextRenderFrame(); drawBoth();
  assert.equal(gl.bufferData, wrapped);        // 之後的繪製不再換掉它
  assert.equal(sets.n, 0);
  gl.log.length = 0;
  const data = new Float32Array(2);
  gl.bufferData(34962, data, 35048);           // 不在 setupShaderProgram 裡：不省略
  gl.bufferData(34962, data, 35048);
  assert.equal(count(gl, 'bufferData'), 2);
});

test('O1：沒登記的 renderer 不受影響', () => {
  const { gl, shader, drawables } = setup();
  installRenderBuffers({ renderer: {} });
  const d = drawables[0];
  const owner = { other: true };
  nextRenderFrame();
  shader.setupShaderProgram(owner, d.tex, 4, d.v, d.i, d.uv, { vertex: null });
  shader.setupShaderProgram(owner, d.tex, 4, d.v, d.i, d.uv, { vertex: null });
  assert.equal(count(gl, 'bufferData'), 6);    // 兩次都完整上傳
});

// ---------- O2 ----------
test('O2：一次繪製內相同的程式、剔除、繞向、貼圖、sampler 只送一次，結束後 useProgram(null) 一次', () => {
  const { gl, renderer, model } = setup();
  installStateCache(model);
  renderer.doDrawModel();
  assert.deepEqual(gl.log.filter(([n]) => n === 'useProgram'), [['useProgram', PROGRAM], ['useProgram', null]]);
  assert.equal(count(gl, 'disable'), 1);
  assert.equal(count(gl, 'frontFace'), 1);
  assert.equal(count(gl, 'activeTexture'), 1);
  assert.equal(count(gl, 'uniform1i'), 1);
  assert.equal(count(gl, 'bindTexture'), 2);   // 兩個 drawable 的貼圖不同，各綁一次
});

test('O2：貼圖相同就不重綁；每次繪製重新開始記，不沿用上一次的狀態', () => {
  const { gl, renderer, drawables, model } = setup();
  installStateCache(model);
  drawables[1].tex = drawables[0].tex;
  renderer.doDrawModel();
  assert.equal(count(gl, 'bindTexture'), 1);
  const first = gl.log.length;
  renderer.doDrawModel();
  assert.equal(gl.log.length, first * 2);      // 第二次繪製送出和第一次一模一樣的呼叫
});

test('O2：gl 的方法只包一次；繪製之外原樣轉呼叫', () => {
  const { gl, renderer, model } = setup();
  installStateCache(model);
  renderer.doDrawModel();
  const names = ['useProgram', 'enable', 'disable', 'frontFace', 'activeTexture', 'bindTexture', 'uniform1i'];
  const wrapped = names.map(n => gl[n]), sets = countAssignments(gl, names);
  renderer.doDrawModel(); renderer.doDrawModel();
  assert.deepEqual(names.map(n => gl[n]), wrapped);   // 沒有每次繪製換掉
  assert.equal(sets.n, 0);
  gl.log.length = 0;
  gl.useProgram(null); gl.useProgram(PROGRAM); gl.useProgram(PROGRAM);
  gl.disable(CULL); gl.disable(CULL);
  gl.uniform1i(LOCATION, 0); gl.uniform1i(LOCATION, 0);
  assert.equal(count(gl, 'useProgram'), 3);
  assert.equal(count(gl, 'disable'), 2);
  assert.equal(count(gl, 'uniform1i'), 2);
});

test('O2：繪製丟例外時也恢復成原樣轉呼叫', () => {
  const { gl, renderer, model } = setup();
  const original = renderer.doDrawModel;
  renderer.doDrawModel = function () { original.call(this); throw new Error('boom'); };
  installStateCache(model);
  assert.throws(() => renderer.doDrawModel(), /boom/);
  gl.log.length = 0;
  gl.useProgram(PROGRAM); gl.useProgram(PROGRAM);
  assert.equal(count(gl, 'useProgram'), 2);
});
