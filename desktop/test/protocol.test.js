// app:// 協定的路徑白名單：能開的檔案、不能開的檔案、路徑穿越。
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { appRoutes, resolveAppFile } from '../main/app-protocol.js';

const HERE = '/srv/desktop';
const ROOTS = [['/renderer/', `${HERE}/renderer`], ['/vendor/', `${HERE}/vendor`], ['/model/', '/models/rushia']];
const LIBS = { '/state.js': `${HERE}/state.js`, '/lib/pixi.min.js': `${HERE}/node_modules/pixi.js/dist/browser/pixi.min.js` };
const r = (url) => resolveAppFile(url, ROOTS, LIBS);

test('白名單內的檔案', () => {
  assert.equal(r('app://ui/renderer/index.html'), `${HERE}/renderer/index.html`);
  assert.equal(r('app://ui/vendor/live2dcubismcore.min.js'), `${HERE}/vendor/live2dcubismcore.min.js`);
  assert.equal(r('app://ui/model/RushiaHD.4096/texture_00.png'), '/models/rushia/RushiaHD.4096/texture_00.png');
  assert.equal(r('app://ui/state.js'), LIBS['/state.js']);
  assert.equal(r('app://ui/lib/pixi.min.js'), LIBS['/lib/pixi.min.js']);
});

test('不在白名單的路徑一律拒絕', () => {
  for (const url of ['app://ui/main.js', 'app://ui/preload.cjs', 'app://ui/package.json', 'app://ui/lib/other.js', 'app://ui/', 'app://ui/node_modules/electron/index.js',
    'app://ui/renderer', 'app://ui/renderer-evil/x.js', 'app://ui/__proto__', 'app://ui/constructor']) assert.equal(r(url), null, url);
});

test('路徑穿越：各種編碼都不能跳出根目錄', () => {
  for (const url of [
    'app://ui/renderer/../main.js', 'app://ui/renderer/%2e%2e/main.js', 'app://ui/renderer/..%2fmain.js', 'app://ui/renderer/%2e%2e%2fmain.js',
    'app://ui/renderer/..%5cmain.js', 'app://ui/model/../../etc/passwd', 'app://ui/model/%2e%2e%2f%2e%2e%2fetc%2fpasswd',
    'app://ui/renderer/a/../../main.js', 'app://ui/renderer/%252e%252e/main.js',
  ]) {
    const got = r(url);
    assert.ok(got === null || got.startsWith(`${HERE}/renderer/`) || got.startsWith('/models/rushia/'), `${url} → ${got}`);
    if (got) {  // 即使回傳了路徑，也必須真的落在根目錄內（正規化之後沒有 .. 區段）
      const root = got.startsWith(HERE) ? `${HERE}/renderer` : '/models/rushia';
      assert.ok(!path.relative(root, path.resolve(got)).split(path.sep).includes('..'), `${url} → ${got}`);
    }
  }
  assert.equal(r('app://ui/renderer/..%2fmain.js'), null);
  assert.equal(r('app://ui/model/..%2f..%2fetc%2fpasswd'), null);
});

test('壞掉的 URL 與編碼', () => {
  assert.equal(r('not a url'), null);
  assert.equal(r('app://ui/renderer/%E0%A4%A'), null);
});

// ---- 真實的白名單設定（appRoutes）：畫面行程只能讀到該讀的 ----
const real = appRoutes('/srv/desktop', '/models/rushia/Rushia.model3.json');
const rr = (url) => resolveAppFile(url, real.roots, real.libFiles);

test('真實設定：畫面需要的檔案可以讀', () => {
  for (const [url, file] of [
    ['app://ui/renderer/role/index.html', '/srv/desktop/renderer/role/index.html'],
    ['app://ui/renderer/chat/chat.html', '/srv/desktop/renderer/chat/chat.html'],
    ['app://ui/domain/character.js', '/srv/desktop/domain/character.js'],
    ['app://ui/domain/frame-gate.js', '/srv/desktop/domain/frame-gate.js'],
    ['app://ui/shared/contract.js', '/srv/desktop/shared/contract.js'],
    ['app://ui/vendor/live2dcubismcore.min.js', '/srv/desktop/vendor/live2dcubismcore.min.js'],
    ['app://ui/model/Rushia.model3.json', '/models/rushia/Rushia.model3.json'],
    ['app://ui/lib/pixi.min.js', '/srv/desktop/node_modules/pixi.js/dist/browser/pixi.min.js'],
    ['app://ui/lib/cubism4.min.js', '/srv/desktop/node_modules/pixi-live2d-display/dist/cubism4.min.js'],
  ]) assert.equal(rr(url), file, url);
});

test('真實設定：main、preload、dev、node_modules 與其他單檔讀不到', () => {
  for (const url of [
    'app://ui/main/index.js', 'app://ui/main/app-protocol.js', 'app://ui/preload/role.cjs', 'app://ui/preload/chat.cjs',
    'app://ui/dev/motion-metrics.js', 'app://ui/node_modules/electron/package.json', 'app://ui/package.json', 'app://ui/test/state.test.js',
    'app://ui/lib/other.js', 'app://ui/domain', 'app://ui/domainx/a.js', 'app://ui/state.js',
    'app://ui/domain/../main/index.js', 'app://ui/domain/%2e%2e/main/index.js', 'app://ui/shared/..%2fmain%2findex.js', 'app://ui/renderer/..%2fpreload%2frole.cjs',
  ]) assert.equal(rr(url), null, url);
});

test('真實設定：沒指定模型時 /model/ 不指向任何真實目錄', () => {
  const none = appRoutes('/srv/desktop', '');
  assert.equal(resolveAppFile('app://ui/model/x.json', none.roots, none.libFiles), '/srv/desktop/no-model/x.json');
});
