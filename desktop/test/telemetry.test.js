// ui.jsonl 記錄：一則訊息一行，bus_ms／apply_ms 的算法、連線編號、中斷時補 null。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createTelemetry, isoLocal } from '../main/telemetry.js';

const tmpLog = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'telemetry-')), 'sub', 'ui.jsonl');
/** 記錄是非同步寫入（appendFile）：等到檔案有 n 行 */
async function lines(file, n) {
  for (let i = 0; i < 100; i++) {
    const rows = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : [];
    if (rows.length >= n) return rows;
    await new Promise(r => setTimeout(r, 10));
  }
  throw new Error(`等不到 ${n} 行：${fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '(沒有檔案)'}`);
}
const msg = (id, type, ts) => ({ id, type, ts });

test('isoLocal：本地時間加上時區偏移', () => {
  assert.match(isoLocal(Date.UTC(2026, 10, 20, 12, 0, 0)), /^2026-11-20T\d\d:00:00[+-]\d\d:\d\d$/);
});

test('沒設 logPath：什麼都不記、也不會丟錯', () => {
  const t = createTelemetry({ logPath: '', runId: 'r' });
  t.connected(); t.record(msg(1, 'result', 1), 2000); t.applied(1, 2010); t.flush();
});

test('一般訊息：等畫面回報 applied 才寫一行，帶 run_id、conn、bus_ms、apply_ms，不帶 recv', async () => {
  const file = tmpLog();
  const t = createTelemetry({ logPath: file, runId: 'run-7' });
  t.connected();
  t.record(msg(5, 'result', 1000.0), 1000500);          // 後端 ts 是秒：1000 s → 1_000_000 ms；收到時間差 500 ms
  assert.equal(fs.existsSync(file), false, '還沒套用，不該寫');
  t.applied(5, 1000500 + 12.34);
  const [row] = await lines(file, 1);
  assert.equal(row.run_id, 'run-7'); assert.equal(row.conn, 1); assert.equal(row.id, 5); assert.equal(row.type, 'result');
  assert.equal(row.bus_ms, 500); assert.equal(row.apply_ms, 12.3);
  assert.ok(!('recv' in row));
  assert.match(row.ts_iso, /^\d{4}-\d\d-\d\dT/);
});

test('hello：畫面還沒載入，直接寫、apply_ms 是 null', async () => {
  const file = tmpLog();
  const t = createTelemetry({ logPath: file, runId: 'r' });
  t.connected();
  t.record(msg(1, 'hello', 1000), 1_000_100);
  const [row] = await lines(file, 1);
  assert.equal(row.type, 'hello'); assert.equal(row.apply_ms, null); assert.ok(!('recv' in row));
});

test('沒有對應的 applied（id 不明或重複）不寫東西', async () => {
  const file = tmpLog();
  const t = createTelemetry({ logPath: file, runId: 'r' });
  t.connected();
  t.applied(99, 5);
  t.record(msg(2, 'partial', 1), 1100);
  t.applied(2, 1110); t.applied(2, 1120);
  const rows = await lines(file, 1);
  await new Promise(r => setTimeout(r, 50));
  assert.equal(fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).length, 1);
  assert.equal(rows[0].id, 2);
});

test('連線中斷：還沒套用的補成 apply_ms = null，之後不會重複寫；重連後 conn 加一', async () => {
  const file = tmpLog();
  const t = createTelemetry({ logPath: file, runId: 'r' });
  t.connected();
  t.record(msg(1, 'executing', 1), 1010);
  t.record(msg(2, 'result', 1), 1020);
  t.flush(); t.flush();
  let rows = await lines(file, 2);
  assert.deepEqual(rows.map(r => [r.id, r.conn, r.apply_ms]).sort(), [[1, 1, null], [2, 1, null]]);   // appendFile 並行，寫入順序不保證
  t.connected();
  t.record(msg(1, 'executing', 2), 2010);   // 後端的 id 每次連線重編：同樣的 id，conn 不同
  t.applied(1, 2015);
  rows = await lines(file, 3);
  const last = rows.find(r => r.conn === 2);
  assert.deepEqual([last.id, last.conn, last.apply_ms], [1, 2, 5]);
});
