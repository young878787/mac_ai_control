// ui.jsonl：桌面角色每則後端訊息一行。bus_ms＝後端送出→本程序收到；apply_ms＝收到→畫面套用（null＝沒等到套用）。
// 後端的 id 每次連線重編，所以帶 conn（第幾次連上匯流排）才唯一。沒設 logPath 就什麼都不記。
import fs from 'node:fs';
import path from 'node:path';

export function isoLocal(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, '0'), off = -d.getTimezoneOffset(), a = Math.abs(off);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${off >= 0 ? '+' : '-'}${p(Math.floor(a / 60))}:${p(a % 60)}`;
}

/** @param {{ logPath: string, runId: string }} opts */
export function createTelemetry({ logPath, runId }) {
  if (logPath) { try { fs.mkdirSync(path.dirname(logPath), { recursive: true }); } catch (e) { console.error('log dir create failed', e); } }
  let conn = 0;
  const pending = new Map();
  const logRow = (row) => {
    if (!logPath) return;
    fs.appendFile(logPath, JSON.stringify(row) + '\n', () => {});
  };
  return {
    /** 匯流排連上了 */
    connected() { conn += 1; },
    /** 收到一則後端訊息；hello 畫面還沒載入，不會有套用時間，直接記；其餘等畫面回報 applied */
    record(msg, recv) {
      if (!logPath) return;
      const row = { ts_iso: isoLocal(recv), run_id: runId, conn, id: msg.id, type: msg.type, bus_ms: Math.round((recv - msg.ts * 1000) * 10) / 10, recv };
      if (msg.type === 'hello') logRow({ ...row, recv: undefined, apply_ms: null });
      else pending.set(msg.id, row);
    },
    /** 畫面套用完這則訊息 */
    applied(id, at) {
      const row = pending.get(id);
      if (!row) return;
      pending.delete(id);
      const { recv, ...rest } = row;
      logRow({ ...rest, apply_ms: Math.round((at - recv) * 10) / 10 });
    },
    /** 連線中斷：還沒等到套用的全部記成 null */
    flush() {
      for (const { recv, ...rest } of pending.values()) logRow({ ...rest, apply_ms: null });
      pending.clear();
    },
  };
}
