// 狀態機測試：node --test test/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createController, summarize, bubbleLifetime, MOTIONS, EXPRESSION_MS, SLEEPY_MS, IDLE_RETREAT_MS,
  BUBBLE_MAX, BUBBLE_MIN_MS, BUBBLE_MAX_MS, HISTORY_MAX, APPEAR_MS, RETREAT_MS,
} from '../state.js';

const T0 = 1_000_000;
const motions = fx => fx.filter(e => e.type === 'motion').map(e => e.group);
const exprs = fx => fx.filter(e => e.type === 'expression').map(e => e.name);
const poses = fx => fx.filter(e => e.type === 'pose').map(e => e.pose);
const ok = (over = {}) => ({ type: 'result', command: 'x', ok: true, steps: [{ kind: 'fast', text: 'x', status: 'ok', logs: ['✅ 已開啟 小算盤'] }], replies: [], total_ms: 5, ...over });

// ---------- 初始與出場 ----------
test('初始：潛伏、待機、沒有泡泡', () => {
  const s = createController().snapshot(T0);
  assert.equal(s.pose, 'peek'); assert.equal(s.mode, 'idle'); assert.equal(s.expression, 'neutral');
  assert.deepEqual(s.bubbles, []); assert.equal(s.session.active, false); assert.equal(s.fps, 15);
});

test('partial 帶喚醒詞 → 出場＋驚訝＋happy，只在出場那一刻演一次', () => {
  const c = createController();
  const fx = c.handle({ type: 'partial', text: '露西婭', wake: true }, T0);
  assert.deepEqual(poses(fx), ['out']); assert.deepEqual(motions(fx), [MOTIONS.surprised]); assert.deepEqual(exprs(fx), ['happy']);
  assert.equal(fx.find(e => e.type === 'pose').ms, APPEAR_MS);
  const again = c.handle({ type: 'partial', text: '露西婭打開', wake: true }, T0 + 200);
  assert.deepEqual(again, []);
  assert.equal(c.snapshot(T0).fps, 30);
});

test('沒喚醒、不在會話中的 partial／heard(ignored)：不反應、不出泡泡', () => {
  const c = createController();
  assert.deepEqual(c.handle({ type: 'partial', text: '今天天氣很好', wake: false }, T0), []);
  assert.deepEqual(c.handle({ type: 'heard', text: '今天天氣很好', status: 'ignored', command: '' }, T0), []);
  const s = c.snapshot(T0);
  assert.equal(s.pose, 'peek'); assert.deepEqual(s.bubbles, []); assert.deepEqual(s.history, []);
});

test('heard armed／command → 出場並記入紀錄；ignored 不記', () => {
  const c = createController();
  const fx = c.handle({ type: 'heard', text: '露醬 打開小算盤', status: 'command', command: '打開小算盤' }, T0);
  assert.deepEqual(poses(fx), ['out']);
  assert.deepEqual(c.snapshot(T0).history.map(h => h.kind), ['heard']);
});

// ---------- 會話 ----------
test('會話中的 partial → 聆聽、即時泡泡（原地更新，不重演出場）', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  const fx = c.handle({ type: 'partial', text: '打開小', wake: false }, T0 + 1000);
  assert.deepEqual(fx, []);
  c.handle({ type: 'partial', text: '打開小算盤', wake: false }, T0 + 1500);
  const s = c.snapshot(T0 + 1500);
  assert.equal(s.mode, 'listening');
  assert.deepEqual(s.bubbles.map(b => [b.id, b.text]), [['partial', '打開小算盤']]);
  c.handle({ type: 'heard', text: '打開小算盤', status: 'command', command: '打開小算盤' }, T0 + 2000);
  assert.deepEqual(c.snapshot(T0 + 2000).bubbles, []);  // heard 之後 partial 泡泡收掉
});

test('session 事件開啟會話並計算倒數比例', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  assert.equal(c.snapshot(T0).session.ratio, 1);
  assert.equal(c.snapshot(T0 + 30_000).session.ratio, 0.5);
  assert.equal(c.snapshot(T0 + 30_000).session.remainingMs, 30_000);
  c.handle({ type: 'session', remaining_s: 60 }, T0 + 30_000);  // 每句重新計時
  assert.equal(c.snapshot(T0 + 30_000).session.ratio, 1);
});

test('會話結束：倒數到 0 → sleepy 1.5 秒 → 退場，順序正確', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  c.tick(T0 + 3000);                       // 出場時的 happy 表情已復原
  assert.deepEqual(c.tick(T0 + 59_999), []);
  const end = c.tick(T0 + 60_000);
  assert.deepEqual(exprs(end), ['sleepy']); assert.deepEqual(poses(end), []);
  assert.equal(c.snapshot(T0 + 60_000).session.active, false);
  assert.deepEqual(poses(c.tick(T0 + 60_000 + SLEEPY_MS - 1)), []);
  const leave = c.tick(T0 + 60_000 + SLEEPY_MS);
  assert.deepEqual(poses(leave), ['peek']); assert.equal(leave.find(e => e.type === 'pose').ms, RETREAT_MS);
  assert.deepEqual(motions(leave), [MOTIONS.idle]);
  assert.equal(c.snapshot(T0).pose, 'peek');
});

test('sleepy 表情之後復原為 neutral', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 1 }, T0);
  c.tick(T0 + 1000);                       // sleepy 開始
  c.tick(T0 + 1000 + SLEEPY_MS);           // 退場
  assert.equal(c.snapshot(T0).expression, 'neutral');
});

test('session(0)（例如暫停）結束會話並退場', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  c.handle({ type: 'session', remaining_s: 0 }, T0 + 5000);
  assert.deepEqual(poses(c.tick(T0 + 5000 + SLEEPY_MS)), ['peek']);
});

test('會話中不會因閒置而退場', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  assert.deepEqual(poses(c.tick(T0 + 50_000)), []);
});

// ---------- 指令執行與結果 ----------
test('executing → Curious＋「…」泡泡', () => {
  const c = createController();
  const fx = c.handle({ type: 'executing', command: '打開小算盤' }, T0);
  assert.deepEqual(poses(fx), ['out']); assert.deepEqual(motions(fx), [MOTIONS.curious]);
  assert.equal(c.snapshot(T0).mode, 'executing');
  assert.deepEqual(c.snapshot(T0).bubbles.map(b => [b.id, b.text]), [['pending', '…']]);
});

test('result 成功 → Nod＋happy 2 秒，泡泡顯示結果，pending 消失', () => {
  const c = createController();
  c.handle({ type: 'executing', command: 'x' }, T0);
  const fx = c.handle(ok(), T0 + 300);
  assert.deepEqual(motions(fx), [MOTIONS.nod]); assert.deepEqual(exprs(fx), ['happy']);
  const s = c.snapshot(T0 + 300);
  assert.equal(s.mode, 'success');
  assert.deepEqual(s.bubbles.map(b => b.text), ['✅ 已開啟 小算盤']);
  assert.deepEqual(exprs(c.tick(T0 + 300 + EXPRESSION_MS - 1)), []);
  assert.deepEqual(exprs(c.tick(T0 + 300 + EXPRESSION_MS)), ['neutral']);
  assert.equal(c.snapshot(T0 + 5000).mode, 'idle');
});

test('result 失敗 → Shake＋pout，泡泡優先顯示錯誤行', () => {
  const c = createController();
  const fx = c.handle(ok({ ok: false, steps: [{ kind: 'fast', text: 'x', status: 'exec_failed', logs: ['已嘗試', '❌ 找不到 App'] }] }), T0);
  assert.deepEqual(motions(fx), [MOTIONS.shake]); assert.deepEqual(exprs(fx), ['pout']);
  assert.deepEqual(c.snapshot(T0).bubbles.map(b => [b.kind, b.text]), [['error', '❌ 找不到 App']]);
});

test('result 有回覆 → 泡泡顯示回覆', () => {
  const c = createController();
  c.handle(ok({ replies: ['今天晴天', '二十度'] }), T0);
  assert.equal(c.snapshot(T0).bubbles[0].text, '今天晴天\n二十度');
});

test('typed command（沒有喚醒）也會出場；結果出現後不重演驚訝', () => {
  const c = createController();
  assert.deepEqual(poses(c.userCommand('打開小算盤', T0)), ['out']);
  c.handle({ type: 'executing', command: '打開小算盤' }, T0 + 10);
  const fx = c.handle(ok(), T0 + 500);
  assert.ok(!motions(fx).includes(MOTIONS.surprised));
  assert.equal(c.snapshot(T0).history[0].kind, 'user');
});

// ---------- 非會話觸發的退場 ----------
test('打字／點擊觸發：最後一個事件後 15 秒退場', () => {
  const c = createController();
  c.userCommand('x', T0);
  assert.deepEqual(poses(c.tick(T0 + IDLE_RETREAT_MS - 1)), []);
  assert.deepEqual(poses(c.tick(T0 + IDLE_RETREAT_MS)), ['peek']);
});

test('15 秒內有新事件就重新計算', () => {
  const c = createController();
  c.userCommand('x', T0);
  c.handle(ok(), T0 + 10_000);
  assert.deepEqual(poses(c.tick(T0 + 20_000)), []);
  assert.deepEqual(poses(c.tick(T0 + 25_000)), ['peek']);
});

test('輸入框開著不退場；關閉後才重新計時', () => {
  const c = createController();
  c.setInputOpen(true, T0);
  assert.deepEqual(poses(c.tick(T0 + 60_000)), []);
  c.setInputOpen(false, T0 + 60_000);
  assert.deepEqual(poses(c.tick(T0 + 60_000 + IDLE_RETREAT_MS - 1)), []);
  assert.deepEqual(poses(c.tick(T0 + 60_000 + IDLE_RETREAT_MS)), ['peek']);
});

test('紀錄面板開著不退場', () => {
  const c = createController();
  c.setHistoryOpen(true, T0);
  assert.deepEqual(poses(c.tick(T0 + 100_000)), []);
});

test('會話結束時輸入框開著：不退場，關閉後 15 秒才退', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 10 }, T0);
  c.setInputOpen(true, T0 + 1000);
  c.tick(T0 + 10_000);
  assert.deepEqual(poses(c.tick(T0 + 10_000 + SLEEPY_MS + 1)), []);
  c.setInputOpen(false, T0 + 20_000);
  assert.deepEqual(poses(c.tick(T0 + 20_000 + IDLE_RETREAT_MS)), ['peek']);
});

// ---------- 點擊 ----------
test('點頭部 → 出場＋送 wake；點身體 → Happy', () => {
  const c = createController();
  const head = c.click('head', T0);
  assert.deepEqual(poses(head), ['out']);
  assert.deepEqual(head.filter(e => e.type === 'send').map(e => e.message), [{ type: 'wake' }]);
  const body = c.click('body', T0 + 100);
  assert.deepEqual(motions(body), [MOTIONS.happy]); assert.deepEqual(exprs(body), ['happy']);
  assert.equal(body.filter(e => e.type === 'send').length, 0);
});

// ---------- 暫停 ----------
test('暫停：會話結束、收起，之後的 partial／heard／session 都不反應', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  c.handle({ type: 'paused', value: true }, T0 + 1000);
  assert.equal(c.snapshot(T0).paused, true); assert.equal(c.snapshot(T0).session.active, false);
  assert.deepEqual(poses(c.tick(T0 + 1000 + SLEEPY_MS)), ['peek']);
  assert.deepEqual(c.handle({ type: 'partial', text: '露西婭', wake: true }, T0 + 5000), []);
  assert.deepEqual(c.handle({ type: 'session', remaining_s: 60 }, T0 + 5000), []);
  c.handle({ type: 'paused', value: false }, T0 + 6000);
  assert.equal(c.handle({ type: 'partial', text: '露西婭', wake: true }, T0 + 7000).length > 0, true);
});

test('hello 帶著剩餘會話時間 → 還原會話但不打招呼', () => {
  const c = createController();
  const fx = c.handle({ type: 'hello', version: 1, paused: false, session_s: 60, remaining_s: 20 }, T0);
  assert.deepEqual(poses(fx), ['out']); assert.deepEqual(motions(fx), []);
  assert.equal(c.snapshot(T0).session.remainingMs, 20_000);
  assert.equal(createController().handle({ type: 'hello', version: 1, paused: true, session_s: 60, remaining_s: 0 }, T0).length, 0);
});

// ---------- 泡泡 ----------
test('泡泡壽命＝3 秒＋每字 0.15 秒，夾在 3～12 秒', () => {
  assert.equal(bubbleLifetime('好'), BUBBLE_MIN_MS + 150);
  assert.equal(bubbleLifetime(''), BUBBLE_MIN_MS);
  assert.equal(bubbleLifetime('字'.repeat(20)), 6000);
  assert.equal(bubbleLifetime('字'.repeat(500)), BUBBLE_MAX_MS);
  for (let n = 0; n < 200; n++) { const t = bubbleLifetime('字'.repeat(n)); assert.ok(t >= 3000 && t <= 12000); }
});

test('泡泡到期消失（結果泡泡依字數）', () => {
  const c = createController();
  c.handle(ok({ steps: [{ logs: ['字'.repeat(20)] }] }), T0);
  assert.equal(c.snapshot(T0).bubbles.length, 1);
  c.tick(T0 + 5999); assert.equal(c.snapshot(T0).bubbles.length, 1);
  c.tick(T0 + 6000); assert.equal(c.snapshot(T0).bubbles.length, 0);
});

test('最多同時 3 則，超過丟最舊的結果泡泡', () => {
  const c = createController();
  for (let i = 1; i <= 5; i++) c.handle(ok({ steps: [{ logs: [`第${i}則`] }] }), T0 + i);
  assert.deepEqual(c.snapshot(T0).bubbles.map(b => b.text), ['第3則', '第4則', '第5則']);
  assert.ok(c.snapshot(T0).bubbles.length <= BUBBLE_MAX);
});

test('滑鼠停在泡泡上時暫停淡出，移開後接著算', () => {
  const c = createController();
  c.handle(ok({ steps: [{ logs: ['好'] }] }), T0);       // 壽命 3150
  c.hoverBubble(c.snapshot(T0).bubbles[0].id, true, T0 + 1000);
  c.tick(T0 + 60_000); assert.equal(c.snapshot(T0).bubbles.length, 1);
  c.hoverBubble(c.snapshot(T0).bubbles[0].id, false, T0 + 60_000);
  c.tick(T0 + 60_000 + 2149); assert.equal(c.snapshot(T0).bubbles.length, 1);
  c.tick(T0 + 60_000 + 2150); assert.equal(c.snapshot(T0).bubbles.length, 0);
});

test('partial 泡泡沒有後續事件時 4 秒後自己消失', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 60 }, T0);
  c.handle({ type: 'partial', text: '嗯', wake: false }, T0);
  c.tick(T0 + 4000);
  assert.equal(c.snapshot(T0).bubbles.length, 0);
});

// ---------- 紀錄 ----------
test('紀錄最多保留 50 筆', () => {
  const c = createController();
  for (let i = 0; i < HISTORY_MAX + 20; i++) c.userCommand(`指令${i}`, T0 + i);
  const h = c.snapshot(T0).history;
  assert.equal(h.length, HISTORY_MAX); assert.equal(h[0].text, '指令20');
});

test('summarize：回覆優先；失敗取錯誤行；沒有內容用預設字；超長截斷', () => {
  assert.equal(summarize({ ok: true, replies: ['嗨'], steps: [] }), '嗨');
  assert.equal(summarize({ ok: false, replies: [], steps: [{ logs: ['a', '⚠️ b', 'c'] }] }), '⚠️ b');
  assert.equal(summarize({ ok: true, replies: [], steps: [] }), '完成');
  assert.equal(summarize({ ok: false, replies: [], steps: [] }), '失敗');
  assert.equal([...summarize({ ok: true, replies: [], steps: [{ logs: ['字'.repeat(300)] }] })].length, 80);
  assert.equal(summarize({ ok: true, replies: [], steps: [{ logs: ['1', '2', '3'] }] }), '2\n3');
});

// ---------- 不做嘴型：任何情況都不會選到 Talk ----------
test('隨機事件序列：不會選到 Talk、動作與表情都在白名單、泡泡數量與壽命合法', () => {
  let seed = 12345;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32;
  const allowedMotions = new Set(Object.values(MOTIONS));
  const allowedExpr = new Set(['neutral', 'happy', 'pout', 'sleepy']);
  const kinds = [
    () => ({ type: 'partial', text: '字'.repeat(1 + Math.floor(rnd() * 30)), wake: rnd() < 0.4 }),
    () => ({ type: 'heard', text: 'x', status: ['command', 'armed', 'ignored'][Math.floor(rnd() * 3)], command: 'x' }),
    () => ({ type: 'session', remaining_s: rnd() < 0.2 ? 0 : 60 }),
    () => ({ type: 'executing', command: 'x' }),
    () => ok({ ok: rnd() < 0.5, replies: rnd() < 0.3 ? ['回覆'] : [] }),
    () => ({ type: 'paused', value: rnd() < 0.3 }),
  ];
  for (let run = 0; run < 50; run++) {
    const c = createController();
    let now = T0;
    for (let i = 0; i < 200; i++) {
      now += Math.floor(rnd() * 8000);
      const fx = rnd() < 0.3 ? c.tick(now) : c.handle(kinds[Math.floor(rnd() * kinds.length)](), now);
      for (const e of fx) {
        if (e.type === 'motion') { assert.notEqual(e.group, 'Talk'); assert.ok(allowedMotions.has(e.group), e.group); }
        if (e.type === 'expression') assert.ok(allowedExpr.has(e.name), e.name);
      }
      const s = c.snapshot(now);
      assert.ok(s.bubbles.length <= BUBBLE_MAX);
      assert.ok(s.history.length <= HISTORY_MAX);
      assert.ok(['peek', 'out'].includes(s.pose));
    }
  }
});

test('Talk 不在 MOTIONS 白名單', () => {
  assert.ok(!Object.values(MOTIONS).includes('Talk'));
});
