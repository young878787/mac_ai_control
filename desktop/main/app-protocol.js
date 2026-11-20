// app:// 自訂協定的路徑解析：只回傳白名單內的檔案路徑，其餘一律 null（主程序據此回 403）。純函式，方便測試路徑穿越。
import path from 'node:path';

/**
 * 白名單：哪些 URL 路徑可以讀到哪些檔案。**畫面行程能讀到的就只有這裡列的**；main/、preload/、dev/、node_modules 都不在內。
 * root＝desktop/ 的絕對路徑；modelPath＝Live2D 模型的 model3.json（沒有就不開放 /model/）。
 * @returns {{ roots: [string, string][], libFiles: Record<string, string> }}
 */
export function appRoutes(root, modelPath) {
  return {
    roots: [
      ['/renderer/', path.join(root, 'renderer')],
      ['/domain/', path.join(root, 'domain')],
      ['/rig/', path.join(root, 'rig')],
      ['/shared/', path.join(root, 'shared')],
      ['/vendor/', path.join(root, 'vendor')],
      ['/model/', modelPath ? path.dirname(modelPath) : path.join(root, 'no-model')],
    ],
    libFiles: {   // 只開放這兩個單檔
      '/lib/pixi.min.js': path.join(root, 'node_modules', 'pixi.js', 'dist', 'browser', 'pixi.min.js'),
      '/lib/cubism4.min.js': path.join(root, 'node_modules', 'pixi-live2d-display', 'dist', 'cubism4.min.js'),
    },
  };
}

/**
 * roots：[[URL 前綴, 根目錄]]；libFiles：{ URL 路徑: 檔案路徑 }（只開放這幾個單檔）
 * @param {string} urlString
 * @param {[string, string][]} roots
 * @param {Record<string, string>} libFiles
 * @returns {string | null}
 */
export function resolveAppFile(urlString, roots, libFiles) {
  let pathname;
  try { pathname = decodeURIComponent(new URL(urlString).pathname); } catch { return null; }
  if (Object.hasOwn(libFiles, pathname)) return libFiles[pathname];
  for (const [prefix, root] of roots) {
    if (!pathname.startsWith(prefix)) continue;
    const base = path.resolve(root);
    const target = path.resolve(base, pathname.slice(prefix.length));
    if (target.startsWith(base + path.sep)) return target;
  }
  return null;
}
