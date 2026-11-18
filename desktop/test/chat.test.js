// 聊天室紀錄與開關狀態機測試：node --test test/
import test from 'node:test';
import assert from 'node:assert/strict';
import { createChat, bodyOf, CHAT_MAX, AUTO_CLOSE_MS, PARTIAL_MS, PENDING_MS } from '../chat.js';

const T0 = 1_000_000;
const types = fx => fx.map(e => e.type);
const res = (over = {}) => ({ type: 'result', command: 'x', ok: true, steps: [{ kind: 'fast', text: 'x', status: 'ok', logs: ['✅ 已開啟 小算盤'] }], replies: [], total_ms: 5, ...over });

// ---------- 紀錄 ----------
test('初始：沒開、沒有紀錄', () => {
  const s = createChat().snapshot();
  assert.deepEqual(s.entries, []); assert.equal(s.open, false); assert.equal(s.pending, false); assert.equal(s.partial, null);
});

test('語音指令：heard(command) 記使用者訊息，executing 對得上就不重複記，result 取代處理中', () => {
  const c = createChat();
  c.handle({ type: 'heard', status: 'command', text: '露醬 打開小算盤', command: '打開小算盤' }, T0);
  c.handle({ type: 'executing', command: '打開小算盤' }, T0 + 5);
  assert.equal(c.snapshot().pending, true);
  c.handle(res({ command: '打開小算盤' }), T0 + 50);
  const s = c.snapshot();
  assert.equal(s.pending, false);
  assert.deepEqual(s.entries.map(e => [e.role, e.text]), [['user', '打開小算盤'], ['assistant', '✅ 已開啟 小算盤']]);
  assert.equal(s.entries[0].source, 'voice');
});

test('heard ignored／armed 不記；暫停中不記', () => {
  const c = createChat();
  c.handle({ type: 'heard', status: 'ignored', text: '旁人說話' }, T0);
  c.handle({ type: 'heard', status: 'armed', text: '露醬' }, T0);
  c.handle({ type: 'paused', value: true }, T0);
  c.handle({ type: 'heard', status: 'command', command: '開小算盤' }, T0);
  assert.deepEqual(c.snapshot().entries, []);
});

test('打字指令：記一次；沒有人先記過的 executing（清單點選）也會記', () => {
  const c = createChat();
  c.userCommand('  看一下畫面 ', T0);
  c.handle({ type: 'executing', command: '看一下畫面' }, T0 + 1);
  c.handle({ type: 'executing', command: '選擇第 2 筆' }, T0 + 2);
  const e = c.snapshot().entries;
  assert.deepEqual(e.map(x => [x.source, x.text]), [['typed', '看一下畫面'], ['click', '選擇第 2 筆']]);
  c.userCommand('   ', T0 + 3);
  assert.equal(c.snapshot().entries.length, 2);
});

test('助手回覆：有 replies 用 replies（完整、不截斷）；沒有就接各步驟記錄；都沒有看 ok', () => {
  const long = '長'.repeat(300);
  assert.equal(bodyOf({ ok: true, replies: [long, ' 第二句 '] }, []), `${long}\n第二句`);
  const steps = [{ logs: ['✅ a', ' '] }, { logs: ['⚠️ b'] }];
  assert.equal(bodyOf({ ok: false, replies: [] }, steps), '✅ a\n⚠️ b');
  assert.equal(bodyOf({ ok: true, replies: [] }, []), '完成');
  assert.equal(bodyOf({ ok: false, replies: [] }, []), '失敗');
});

test('result 內容不可信：欄位限制長度、步驟與記錄限制數量、壞資料不炸', () => {
  const c = createChat();
  c.handle(res({ replies: 'bad', steps: 'bad' }), T0);
  c.handle(res({ replies: ['x'.repeat(9999)], steps: [null, 7, { kind: 'slow', text: 't', status: 's', route: 'cloud', ms: 12.5, logs: Array(99).fill('l'.repeat(9999)) }, ...Array(40).fill({ logs: [] })] }), T0 + 1);
  const [a, b] = c.snapshot().entries;
  assert.equal(a.role, 'assistant'); assert.deepEqual(a.steps, []);
  assert.equal(b.text.length, 4000);
  assert.equal(b.steps.length, 18);   // 先取前 20 個，再丟掉 null 與數字（共 2 個）
  const st = b.steps.find(x => x.route === 'cloud');
  assert.equal(st.logs.length, 30); assert.equal(st.logs[0].length, 2000); assert.equal(st.ms, 12.5);
});

test('紀錄上限：超過 CHAT_MAX 丟最舊的；rev 隨紀錄與處理中變動', () => {
  const c = createChat();
  const r0 = c.snapshot().rev;
  for (let i = 0; i < CHAT_MAX + 5; i++) c.userCommand(`指令${i}`, T0 + i);
  const s = c.snapshot();
  assert.equal(s.entries.length, CHAT_MAX); assert.equal(s.entries[0].text, '指令5');
  assert.ok(s.rev > r0);
  const r1 = s.rev;
  c.handle({ type: 'partial', text: '聽到', wake: true }, T0);   // 只有即時文字改變：rev 不動
  assert.equal(c.snapshot().rev, r1);
  c.handle({ type: 'executing', command: '指令0' }, T0);
  assert.ok(c.snapshot().rev > r1);
});

// ---------- 即時文字（partial） ----------
test('partial：沒喚醒又不在會話中不顯示；會話中或帶喚醒才顯示；heard 後清掉', () => {
  const c = createChat();
  c.handle({ type: 'partial', text: '旁人', wake: false }, T0);
  assert.equal(c.snapshot().partial, null);
  c.handle({ type: 'partial', text: '露醬打開', wake: true }, T0);
  assert.equal(c.snapshot().partial, '露醬打開');
  c.handle({ type: 'heard', status: 'ignored', text: 'x' }, T0 + 10);
  assert.equal(c.snapshot().partial, null);
  c.handle({ type: 'session', remaining_s: 50 }, T0 + 20);
  c.handle({ type: 'partial', text: '會話中', wake: false }, T0 + 21);
  assert.equal(c.snapshot().partial, '會話中');
  c.handle({ type: 'session', remaining_s: 0 }, T0 + 30);
  assert.equal(c.snapshot().partial, null);
});

test('partial 沒有後續也會在期限後消失；處理中標記超時也會清掉', () => {
  const c = createChat();
  c.handle({ type: 'partial', text: '聽', wake: true }, T0);
  c.tick(T0 + PARTIAL_MS - 1); assert.equal(c.snapshot().partial, '聽');
  c.tick(T0 + PARTIAL_MS); assert.equal(c.snapshot().partial, null);
  c.handle({ type: 'executing', command: 'x' }, T0);
  c.tick(T0 + PENDING_MS - 1); assert.equal(c.snapshot().pending, true);
  c.tick(T0 + PENDING_MS); assert.equal(c.snapshot().pending, false);
});

test('hello（重新連線）清掉處理中與即時文字，紀錄保留', () => {
  const c = createChat();
  c.userCommand('a', T0);
  c.handle({ type: 'executing', command: 'a' }, T0);
  c.handle({ type: 'partial', text: '聽', wake: true }, T0);
  c.handle({ type: 'hello', paused: false, remaining_s: 0 }, T0 + 1);
  const s = c.snapshot();
  assert.equal(s.pending, false); assert.equal(s.partial, null); assert.equal(s.entries.length, 1);
});

// ---------- 主動展開 ----------
test('result 帶 expand（screen／chat／complex）→ 主動開；沒有或不認得就不開', () => {
  for (const reason of ['screen', 'chat', 'complex']) {
    const c = createChat();
    assert.deepEqual(c.handle(res({ expand: reason }), T0), [{ type: 'chat-open', auto: true }]);
    assert.equal(c.isOpen(), true);
  }
  const c = createChat();
  assert.deepEqual(c.handle(res(), T0), []);
  assert.deepEqual(c.handle(res({ expand: 'nope' }), T0), []);
  assert.deepEqual(c.handle(res({ expand: null }), T0), []);
  assert.equal(c.isOpen(), false);
});

test('失敗的 result 帶 expand 也照樣展開（內容要讓使用者看得到）', () => {
  const c = createChat();
  assert.deepEqual(types(c.handle(res({ ok: false, expand: 'complex' }), T0)), ['chat-open']);
  assert.equal(c.snapshot().entries[0].ok, false);
});

test('主動展開沒有互動：恰好 20 秒收回', () => {
  const c = createChat();
  c.handle(res({ expand: 'chat' }), T0);
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS - 1), []);
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS), [{ type: 'chat-close' }]);
  assert.equal(c.isOpen(), false);
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS + 1), []);   // 不會重複收
});

test('主動展開後有互動（滑鼠移上去或點進去）就不會自動收回，直到手動關', () => {
  const c = createChat();
  c.handle(res({ expand: 'chat' }), T0);
  c.interact();
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS * 5), []);
  assert.equal(c.isOpen(), true);
  assert.deepEqual(c.close(), [{ type: 'chat-close' }]);
  assert.deepEqual(c.close(), []);
});

test('展開中又有新的 expand：重新計時；互動過就維持開著', () => {
  const c = createChat();
  c.handle(res({ expand: 'chat' }), T0);
  assert.deepEqual(c.handle(res({ expand: 'complex' }), T0 + 15000), []);   // 已經開著，不再重複開
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS), []);
  assert.deepEqual(c.tick(T0 + 15000 + AUTO_CLOSE_MS), [{ type: 'chat-close' }]);
  c.handle(res({ expand: 'chat' }), T0 + 100000);
  c.interact();
  c.handle(res({ expand: 'chat' }), T0 + 110000);
  assert.deepEqual(c.tick(T0 + 200000), []);
});

test('關掉後再主動展開：互動狀態重置，重新自動收回', () => {
  const c = createChat();
  c.handle(res({ expand: 'chat' }), T0);
  c.interact(); c.close();
  c.handle(res({ expand: 'chat' }), T0 + 50000);
  assert.deepEqual(c.tick(T0 + 50000 + AUTO_CLOSE_MS), [{ type: 'chat-close' }]);
});

// ---------- 手動開關 ----------
test('手動開（快捷泡泡、選單）不會自動收；再點一次關', () => {
  const c = createChat();
  assert.deepEqual(c.toggle(T0), [{ type: 'chat-open', auto: false }]);
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS * 10), []);
  assert.equal(c.isOpen(), true);
  assert.deepEqual(c.toggle(T0 + 1), [{ type: 'chat-close' }]);
  assert.equal(c.isOpen(), false);
});

test('手動開著時收到 expand：維持手動（不會被自動收回）', () => {
  const c = createChat();
  c.toggle(T0);
  assert.deepEqual(c.handle(res({ expand: 'chat' }), T0 + 1000), []);
  assert.deepEqual(c.tick(T0 + 1000 + AUTO_CLOSE_MS * 3), []);
});

test('主動展開中點快捷泡泡＝關閉；interact 在沒開時沒作用', () => {
  const c = createChat();
  c.interact();
  c.handle(res({ expand: 'chat' }), T0);
  assert.deepEqual(c.toggle(T0 + 1), [{ type: 'chat-close' }]);
  assert.deepEqual(c.tick(T0 + AUTO_CLOSE_MS), []);
});

test('關掉聊天室不會清掉紀錄；不認得的事件忽略', () => {
  const c = createChat();
  c.handle(res({ expand: 'chat', replies: ['哈囉'] }), T0);
  c.close();
  assert.equal(c.snapshot().entries.length, 1);
  assert.deepEqual(c.handle({ type: 'stt', state: 'connected' }, T0), []);
  assert.deepEqual(c.handle(null, T0), []);
  assert.deepEqual(c.handle({ type: 'whatever' }, T0), []);
});
