// 與後端事件匯流排（ui_bus，本機 WebSocket）的連線：自動重連、只送白名單內的訊息型別。token 在網址裡，只有主程序看得到。

/**
 * @param {{ url: string, allowedTypes: Set<string>, onMessage: (msg: any, recv: number) => void, onOpen: () => void, onClose: () => void }} opts
 */
export function createBus({ url, allowedTypes, onMessage, onOpen, onClose }) {
  let ws = null;
  let retryMs = 1000;

  function connect() {
    if (!url) return;
    let sock;
    try { sock = new WebSocket(url); } catch (e) { console.error('bus connect failed', e); scheduleReconnect(); return; }
    ws = sock;
    sock.addEventListener('open', () => { retryMs = 1000; onOpen(); console.log('bus connected'); });
    sock.addEventListener('message', (ev) => {
      const recv = Date.now();
      let msg;
      try { msg = JSON.parse(String(ev.data)); } catch { return; }
      if (!msg || typeof msg.type !== 'string') return;
      onMessage(msg, recv);
    });
    sock.addEventListener('close', () => { if (ws === sock) ws = null; onClose(); scheduleReconnect(); });
    sock.addEventListener('error', () => {});
  }
  function scheduleReconnect() {
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 5000);
  }
  /** 送給後端；型別不在白名單或沒連上回傳 false */
  function send(msg) {
    if (!msg || !allowedTypes.has(msg.type) || !ws || ws.readyState !== 1) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }
  return { connect, send };
}
