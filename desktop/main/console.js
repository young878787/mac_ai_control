// 把畫面行程的 console 與當機訊息轉到主程序的終端機（只轉錯誤、警告與 [ui] 開頭的記錄）。

/** @param {import('electron').WebContents} wc @param {string} name */
export function forwardConsole(wc, name) {
  wc.on('console-message', (event) => {
    const { level, message } = event;
    if (message.startsWith('Electron Security Warning')) return;  // 只有開發模式才有；CSP 是刻意設的
    if (level === 'error' || level === 'warning' || /^\[ui\]/.test(message)) console.log(`[${name} ${level}] ${message} (${event.sourceId}:${event.lineNumber})`);
  });
  wc.on('render-process-gone', (_e, details) => console.error(`${name} gone`, details));
}
