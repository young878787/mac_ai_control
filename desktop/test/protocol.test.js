// app:// 協定的路徑白名單：能開的檔案、不能開的檔案、路徑穿越。
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { resolveAppFile } from '../protocol.js';

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
