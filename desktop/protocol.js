// app:// 自訂協定的路徑解析：只回傳白名單內的檔案路徑，其餘一律 null（主程序據此回 403）。純函式，方便測試路徑穿越。
import path from 'node:path';

/** roots：[[URL 前綴, 根目錄]]；libFiles：{ URL 路徑: 檔案路徑 }（只開放這幾個單檔） */
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
