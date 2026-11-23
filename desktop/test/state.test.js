// 狀態機測試：node --test test/
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createController, summarize, bubbleLifetime, MOTIONS, GESTURES, EXPRESSION_MS, SLEEPY_MS, IDLE_RETREAT_MS,
  BUBBLE_MAX, BUBBLE_MIN_MS, BUBBLE_MAX_MS, QUICK_MS, EXPAND_NOTICE, APPEAR_MS, RETREAT_MS, CHOICES_MAX,
  GESTURE_MS, THINK_MS, TALK_MIN_MS, TALK_MAX_MS, TALK_PER_CHAR_MS,
} from '../domain/character.js';

const T0 = 1_000_000;
const motions = fx => fx.filter(e => e.type === 'motion').map(e => e.name);
const exprs = fx => fx.filter(e => e.type === 'expression').map(e => e.name);
const poses = fx => fx.filter(e => e.type === 'pose').map(e => e.pose);
const gestures = fx => fx.filter(e => e.type === 'gesture').map(e => e.name);
const ok = (over = {}) => ({ type: 'result', command: 'x', ok: true, steps: [{ kind: 'fast', text: 'x', status: 'ok', logs: ['✅ 已開啟 小算盤'] }], replies: [], total_ms: 5, ...over });

// ---------- 初始與出場 ----------
test('初始：潛伏、待機、沒有泡泡', () => {
  const s = createController().snapshot(T0);
  assert.equal(s.pose, 'peek'); assert.equal(s.mode, 'idle'); assert.equal(s.expression, 'neutral');
  assert.deepEqual(s.bubbles, []); assert.equal(s.session.active, false); assert.equal(s.gesture, GESTURES.none);
});

test('partial 帶喚醒詞 → 出場＋Surprised＋surprised 表情，只在出場那一刻演一次', () => {
  const c = createController();
  const fx = c.handle({ type: 'partial', text: '露西婭', wake: true }, T0);
  assert.deepEqual(poses(fx), ['out']); assert.deepEqual(motions(fx), [MOTIONS.surprised]); assert.deepEqual(exprs(fx), ['surprised']);
  assert.deepEqual(gestures(fx), []);
  assert.equal(fx.find(e => e.type === 'pose').ms, APPEAR_MS);
  const again = c.handle({ type: 'partial', text: '露西婭打開', wake: true }, T0 + 200);
  assert.deepEqual(again, []);
});

test('沒喚醒、不在會話中的 partial／heard(ignored)：不反應、不出泡泡', () => {
  const c = createController();
  assert.deepEqual(c.handle({ type: 'partial', text: '今天天氣很好', wake: false }, T0), []);
  assert.deepEqual(c.handle({ type: 'heard', text: '今天天氣很好', status: 'ignored', command: '' }, T0), []);
  const s = c.snapshot(T0);
  assert.equal(s.pose, 'peek'); assert.deepEqual(s.bubbles, []);
});

test('heard armed／command → 出場；ignored 不反應', () => {
  const c = createController();
  const fx = c.handle({ type: 'heard', text: '露醬 打開小算盤', status: 'command', command: '打開小算盤' }, T0);
  assert.deepEqual(poses(fx), ['out']);
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

// ---------- 快捷泡泡 ----------
test('點角色彈出快捷泡泡（原本的反應照舊）；再點一下收起；QUICK_MS 沒操作就收', () => {
  const c = createController();
  assert.equal(c.snapshot(T0).quick, false);
  const head = c.click('head', T0);
  assert.deepEqual(sends(head), [{ type: 'wake' }]);   // 點頭仍然是開始聆聽
  assert.equal(c.snapshot(T0).quick, true);
  c.click('body', T0 + 100);                           // 再點一下：收起，身體仍然演 happy
  assert.equal(c.snapshot(T0 + 100).quick, false);
  c.click('body', T0 + 200);
  c.tick(T0 + 200 + QUICK_MS - 1); assert.equal(c.snapshot(T0).quick, true);
  c.tick(T0 + 200 + QUICK_MS); assert.equal(c.snapshot(T0).quick, false);
});

test('快捷泡泡：滑鼠停在上面不收，移開後重新計時；closeQuick 立刻收', () => {
  const c = createController();
  c.click('body', T0);
  c.hoverQuick(true, T0 + 1000);
  c.tick(T0 + QUICK_MS * 4); assert.equal(c.snapshot(T0).quick, true);
  c.hoverQuick(false, T0 + QUICK_MS * 4);
  c.tick(T0 + QUICK_MS * 5 - 1); assert.equal(c.snapshot(T0).quick, true);
  c.tick(T0 + QUICK_MS * 5); assert.equal(c.snapshot(T0).quick, false);
  c.click('body', T0 + QUICK_MS * 6);
  c.closeQuick(); assert.equal(c.snapshot(T0).quick, false);
  c.hoverQuick(true, T0); assert.equal(c.snapshot(T0).quick, false);   // 沒開時懸停無作用
});

// ---------- 聊天室主動展開 ----------
test('result 帶 expand：角色泡泡只放提示，種類仍依成敗；沒有 expand 照舊顯示摘要', () => {
  for (const reason of ['screen', 'chat', 'complex']) {
    const c = createController();
    c.handle(ok({ expand: reason, replies: ['很長很長的回覆'.repeat(20)] }), T0);
    assert.deepEqual(c.snapshot(T0).bubbles.map(b => [b.kind, b.text]), [['result', EXPAND_NOTICE]]);
  }
  const f = createController();
  f.handle(ok({ ok: false, expand: 'complex', steps: [{ kind: 'fast', text: 'x', status: 'exec_failed', logs: ['❌ 壞了'] }] }), T0);
  assert.deepEqual(f.snapshot(T0).bubbles.map(b => [b.kind, b.text]), [['error', EXPAND_NOTICE]]);
  const plain = createController();
  plain.handle(ok({ replies: ['嗨'] }), T0);
  assert.deepEqual(plain.snapshot(T0).bubbles.map(b => b.text), ['嗨']);
  const odd = createController();
  odd.handle(ok({ expand: 'nope', replies: ['嗨'] }), T0);
  assert.deepEqual(odd.snapshot(T0).bubbles.map(b => b.text), ['嗨']);
});

test('summarize：回覆優先；失敗取錯誤行；沒有內容用預設字；超長截斷', () => {
  assert.equal(summarize({ ok: true, replies: ['嗨'], steps: [] }), '嗨');
  assert.equal(summarize({ ok: false, replies: [], steps: [{ logs: ['a', '⚠️ b', 'c'] }] }), '⚠️ b');
  assert.equal(summarize({ ok: true, replies: [], steps: [] }), '完成');
  assert.equal(summarize({ ok: false, replies: [], steps: [] }), '失敗');
  assert.equal([...summarize({ ok: true, replies: [], steps: [{ logs: ['字'.repeat(300)] }] })].length, 80);
  assert.equal(summarize({ ok: true, replies: [], steps: [{ logs: ['1', '2', '3'] }] }), '2\n3');
});

// ---------- 隨機事件序列 ----------
test('隨機事件序列：Talk 只在有文字回覆的成功之後、動作／表情／手勢都在白名單、泡泡數量與壽命合法', () => {
  let seed = 12345;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32;
  const allowedMotions = new Set(Object.values(MOTIONS));
  const allowedExpr = new Set(['neutral', 'happy', 'pout', 'sleepy', 'surprised', 'starEyes', 'poutLeft']);
  const allowedGestures = new Set(Object.values(GESTURES));
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
      const ev = rnd() < 0.3 ? null : kinds[Math.floor(rnd() * kinds.length)]();
      const fx = ev ? c.handle(ev, now) : c.tick(now);
      for (const e of fx) {
        if (e.type === 'motion') {
          assert.ok(allowedMotions.has(e.name), e.name);
          if (e.name === MOTIONS.talk) assert.ok(ev && ev.type === 'result' && ev.ok && ev.replies.length, 'Talk 只能由有回覆的成功觸發');
        }
        if (e.type === 'expression') assert.ok(allowedExpr.has(e.name), e.name);
        if (e.type === 'gesture') assert.ok(allowedGestures.has(e.name), e.name);
      }
      const s = c.snapshot(now);
      assert.ok(s.bubbles.length <= BUBBLE_MAX);
      assert.equal(typeof s.quick, 'boolean');
      assert.ok(['peek', 'out'].includes(s.pose));
    }
  }
});

test('不使用 Blink（它會把 Idle 的姿勢歸零）', () => {
  assert.ok(!Object.values(MOTIONS).includes('Blink'));
});

// ---------- 新表情、手勢、說話（fullrig03，計畫書 §5.2） ----------
test('點頭部 → Surprised＋star_eyes＋揮手（arm_r_up），1.5 秒後收回手勢、2 秒後表情復原', () => {
  const c = createController();
  const fx = c.click('head', T0);
  assert.deepEqual(motions(fx), [MOTIONS.surprised]); assert.deepEqual(exprs(fx), ['starEyes']);
  assert.deepEqual(gestures(fx), [GESTURES.armRightUp]);
  assert.equal(c.snapshot(T0).gesture, GESTURES.armRightUp);
  assert.deepEqual(gestures(c.tick(T0 + GESTURE_MS - 1)), []);
  assert.deepEqual(gestures(c.tick(T0 + GESTURE_MS)), [GESTURES.none]);
  assert.deepEqual(exprs(c.tick(T0 + EXPRESSION_MS)), ['neutral']);
});

test('點身體 → Happy＋happy＋舉左手（arm_l_up）', () => {
  const c = createController();
  const fx = c.click('body', T0);
  assert.deepEqual(motions(fx), [MOTIONS.happy]); assert.deepEqual(exprs(fx), ['happy']);
  assert.deepEqual(gestures(fx), [GESTURES.armLeftUp]);
  assert.deepEqual(gestures(c.tick(T0 + GESTURE_MS)), [GESTURES.none]);
});

test('手勢互斥：舉手中換成另一個手勢時，同一時間只會有一個', () => {
  const c = createController();
  c.click('body', T0);                                          // arm_l_up
  c.handle({ type: 'session', remaining_s: 1 }, T0 + 100);
  const end = c.tick(T0 + 1100);                                // 會話結束 → 托腮，取代舉手
  assert.deepEqual(gestures(end), [GESTURES.chinRest]);
  assert.equal(c.snapshot(T0 + 1100).gesture, GESTURES.chinRest);
});

test('執行超過 THINK_MS 才托腮；結果出來就收回', () => {
  const c = createController();
  c.handle({ type: 'executing', command: 'x' }, T0);
  assert.deepEqual(gestures(c.tick(T0 + THINK_MS - 1)), []);
  assert.deepEqual(gestures(c.tick(T0 + THINK_MS)), [GESTURES.chinRest]);
  assert.deepEqual(gestures(c.tick(T0 + THINK_MS + 10_000)), []);   // 直到結果，不會自己收
  const fx = c.handle(ok(), T0 + THINK_MS + 10_100);
  assert.deepEqual(gestures(fx), [GESTURES.none]);
});

test('結果在 THINK_MS 之前出來：之後不會再托腮', () => {
  const c = createController();
  c.handle({ type: 'executing', command: 'x' }, T0);
  const fx = c.handle(ok(), T0 + 500);
  assert.deepEqual(gestures(fx), []);                          // 沒有手勢可收
  assert.deepEqual(gestures(c.tick(T0 + THINK_MS + 1)), []);
});

test('成功且有文字回覆 → Talk（依字數，限制在最短／最長之間）＋happy，播完回到 Idle', () => {
  const c = createController();
  const fx = c.handle(ok({ replies: ['今天晴天'] }), T0);      // 4 字 → 480 ms，低於最短
  assert.deepEqual(motions(fx), [MOTIONS.talk]); assert.deepEqual(exprs(fx), ['happy']);
  assert.deepEqual(motions(c.tick(T0 + TALK_MIN_MS - 1)), []);
  assert.deepEqual(motions(c.tick(T0 + TALK_MIN_MS)), [MOTIONS.idle]);
  const long = createController();
  long.handle(ok({ replies: ['字'.repeat(200)] }), T0);
  assert.deepEqual(motions(long.tick(T0 + TALK_MAX_MS - 1)), []);
  assert.deepEqual(motions(long.tick(T0 + TALK_MAX_MS)), [MOTIONS.idle]);
  const mid = createController();
  mid.handle(ok({ replies: ['字'.repeat(20)] }), T0);          // 20 字 → 2400 ms
  assert.deepEqual(motions(mid.tick(T0 + 20 * TALK_PER_CHAR_MS)), [MOTIONS.idle]);
});

test('成功沒有回覆 → Nod；只有空白的回覆也算沒有', () => {
  const c = createController();
  assert.deepEqual(motions(c.handle(ok({ replies: ['  '] }), T0)), [MOTIONS.nod]);
});

test('說話中被新動作打斷：說話時間到也不會把新動作切回 Idle', () => {
  const c = createController();
  c.handle(ok({ replies: ['字'.repeat(30)] }), T0);
  c.handle({ type: 'executing', command: 'y' }, T0 + 500);     // Curious 取代 Talk
  assert.deepEqual(motions(c.tick(T0 + TALK_MAX_MS)), []);
});

test('連續失敗：第 1 次 pout，第 2 次起 pout_left；成功後歸零', () => {
  const c = createController();
  const fail = ok({ ok: false, steps: [{ kind: 'fast', text: 'x', status: 'exec_failed', logs: ['❌ 失敗'] }] });
  assert.deepEqual(exprs(c.handle(fail, T0)), ['pout']);
  assert.deepEqual(exprs(c.handle(fail, T0 + 1000)), ['poutLeft']);
  assert.deepEqual(exprs(c.handle(fail, T0 + 2000)), ['poutLeft']);
  c.handle(ok(), T0 + 3000);
  assert.deepEqual(exprs(c.handle(fail, T0 + 4000)), ['pout']);
});

test('連續失敗計數在會話結束與退場時歸零', () => {
  const fail = ok({ ok: false, steps: [{ kind: 'fast', text: 'x', status: 'exec_failed', logs: ['❌ 失敗'] }] });
  const c = createController();
  c.handle({ type: 'session', remaining_s: 10 }, T0);
  c.handle(fail, T0 + 1000);
  c.tick(T0 + 10_000);                                          // 會話結束
  c.handle({ type: 'session', remaining_s: 10 }, T0 + 10_100);
  assert.deepEqual(exprs(c.handle(fail, T0 + 10_200)), ['pout']);
  const d = createController();
  d.userCommand('x', T0);
  d.handle(fail, T0 + 100);
  d.tick(T0 + 100 + IDLE_RETREAT_MS);                           // 退場
  d.userCommand('x', T0 + 30_000);
  assert.deepEqual(exprs(d.handle(fail, T0 + 30_100)), ['pout']);
});

test('會話結束 → sleepy＋托腮 1.5 秒，退場後手勢是 none', () => {
  const c = createController();
  c.handle({ type: 'session', remaining_s: 1 }, T0);
  const end = c.tick(T0 + 1000);
  assert.deepEqual(exprs(end), ['sleepy']); assert.deepEqual(gestures(end), [GESTURES.chinRest]);
  const leave = c.tick(T0 + 1000 + SLEEPY_MS);
  assert.deepEqual(poses(leave), ['peek']); assert.deepEqual(gestures(leave), [GESTURES.none]);
  assert.equal(c.snapshot(T0 + 1000 + SLEEPY_MS).gesture, GESTURES.none);
});

test('托腮思考中閒置退場：退場時一併收回手勢', () => {
  const c = createController();
  c.userCommand('x', T0);
  c.handle({ type: 'executing', command: 'x' }, T0);
  c.tick(T0 + THINK_MS);                                        // 托腮
  const leave = c.tick(T0 + IDLE_RETREAT_MS);
  assert.deepEqual(poses(leave), ['peek']); assert.deepEqual(gestures(leave), [GESTURES.none]);
  assert.deepEqual(motions(leave), [MOTIONS.idle]);
});

// ---------- 搜尋結果清單 ----------

const ITEMS = [
  { n: 1, name: '報告A.pdf', dir: '~/docs', kind: 'pdf' },
  { n: 2, name: '報告B.pdf', dir: '~/docs', kind: 'pdf' },
  { n: 3, name: '報告C.txt', dir: '~/docs', kind: 'txt' },
];
const withChoices = (over = {}) => ok({
  replies: ['找到 3 筆「報告」'],
  choices: { list_id: 4, query: '報告', total: 3, default_action: 'reveal', expires_in_s: 60, items: ITEMS, ...over },
});
const sends = fx => fx.filter(e => e.type === 'send').map(e => e.message);

test('result 帶 choices → 清單開啟，倒數照後端給的秒數', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  const s = c.snapshot(T0 + 1000);
  assert.equal(s.choices.listId, 4); assert.equal(s.choices.query, '報告'); assert.equal(s.choices.total, 3);
  assert.equal(s.choices.defaultAction, 'reveal'); assert.deepEqual(s.choices.items, ITEMS);
  assert.equal(s.choices.remainingMs, 59_000);
  assert.deepEqual(s.bubbles, []);  // 清單本身就是輸出：不另外跳泡泡（會被清單蓋住）
  assert.equal(createController().snapshot(T0).choices, null);
});

test('聯合搜尋的網路項目（kind＝web）照樣進清單：只有標題與網域', () => {
  const web = { n: 4, name: '周杰倫 - 維基百科', dir: 'zh.wikipedia.org', kind: 'web' };
  const c = createController();
  c.handle(withChoices({ items: [...ITEMS, web], total: 4 }), T0);
  assert.deepEqual(c.snapshot(T0).choices.items.at(-1), web);
});

test('清單到期就關閉', () => {
  const c = createController();
  c.handle(withChoices({ expires_in_s: 30 }), T0);
  c.tick(T0 + 29_999);
  assert.ok(c.snapshot(T0).choices);
  c.tick(T0 + 30_000);
  assert.equal(c.snapshot(T0).choices, null);
});

test('choose：送出 list_id、序號與動作；沒指定動作就用清單的預設', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  assert.deepEqual(sends(c.choose(2, null, T0 + 100)), [{ type: 'choose', list_id: 4, n: 2, action: 'reveal' }]);
  assert.deepEqual(sends(c.choose(3, 'open', T0 + 100)), [{ type: 'choose', list_id: 4, n: 3, action: 'open' }]);
  c.handle(withChoices({ default_action: 'open' }), T0 + 200);
  assert.deepEqual(sends(c.choose(1, undefined, T0 + 300)), [{ type: 'choose', list_id: 4, n: 1, action: 'open' }]);
});

test('choose：沒有清單、序號不在清單裡、清單已到期 → 什麼都不送', () => {
  const c = createController();
  assert.deepEqual(c.choose(1, null, T0), []);
  c.handle(withChoices(), T0);
  assert.deepEqual(c.choose(4, null, T0 + 1), []);
  assert.deepEqual(c.choose(0, null, T0 + 1), []);
  assert.deepEqual(c.choose(1, null, T0 + 60_000), []);
});

test('點選後清單要等後端回報成功才關（失敗時留著可以重選）', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  c.choose(2, null, T0 + 100);
  assert.ok(c.snapshot(T0 + 100).choices);
  c.handle(ok({ ok: false, steps: [{ kind: 'fast', text: 'x', status: 'exec_failed', logs: ['⚠️ 清單已更新，請重選'] }] }), T0 + 200);
  assert.ok(c.snapshot(T0 + 200).choices);
  c.handle(ok({ choices: { list_id: 4, closed: true } }), T0 + 300);
  assert.equal(c.snapshot(T0 + 300).choices, null);
  assert.equal(c.snapshot(T0 + 300).bubbles.at(-1).text, '✅ 已開啟 小算盤');  // 清單之外的結果照常顯示泡泡
});

test('closed 通知只關同一份清單；新清單取代舊清單', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  c.handle(ok({ choices: { list_id: 3, closed: true } }), T0 + 10);
  assert.equal(c.snapshot(T0 + 10).choices.listId, 4);
  c.handle(withChoices({ list_id: 5, query: '新', items: ITEMS.slice(0, 2), total: 2 }), T0 + 20);
  const s = c.snapshot(T0 + 20).choices;
  assert.equal(s.listId, 5); assert.equal(s.items.length, 2);
  assert.deepEqual(sends(c.choose(1, null, T0 + 30)), [{ type: 'choose', list_id: 5, n: 1, action: 'reveal' }]);
});

test('不相關的 result（例如切換分頁）不會動到清單', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  c.handle(ok(), T0 + 500);
  assert.equal(c.snapshot(T0 + 500).choices.listId, 4);
});

test('closeChoices 關掉清單', () => {
  const c = createController();
  c.handle(withChoices(), T0);
  c.closeChoices(T0 + 1);
  assert.equal(c.snapshot(T0 + 1).choices, null);
});

test('清單開著不會退場；關掉之後再閒置才退場', () => {
  const c = createController();
  c.handle(withChoices({ expires_in_s: 600 }), T0);
  assert.deepEqual(poses(c.tick(T0 + IDLE_RETREAT_MS + 1000)), []);
  c.closeChoices(T0 + IDLE_RETREAT_MS + 1000);
  assert.deepEqual(poses(c.tick(T0 + 2 * IDLE_RETREAT_MS + 2000)), ['peek']);
});

test('清單內容不可信：只留顯示欄位、最多 5 筆、壞資料整份丟掉', () => {
  const c = createController();
  const many = Array.from({ length: 8 }, (_, i) => ({ n: i + 1, name: `f${i + 1}`, dir: '~', kind: 'txt' }));
  c.handle(withChoices({ items: many, total: 8 }), T0);
  const s = c.snapshot(T0).choices;
  assert.equal(CHOICES_MAX, 5); assert.deepEqual(s.items.map(i => i.n), [1, 2, 3, 4, 5]); assert.equal(s.total, 8);

  const bad = [
    { list_id: '4', items: ITEMS }, { list_id: 4 }, { list_id: 4, items: [] }, { list_id: 4, items: 'x' },
    { list_id: 4, items: [{ n: 9, name: 'x', dir: '', kind: '' }] }, { list_id: 4, items: [null, { n: 'a' }] },
  ];
  for (const choices of bad) {
    const d = createController();
    d.handle(ok({ choices: { query: 'q', total: 1, expires_in_s: 60, ...choices } }), T0);
    assert.equal(d.snapshot(T0).choices, null, JSON.stringify(choices));
  }

  const e = createController();  // 欄位型別不對：轉成字串、預設動作回到 reveal
  e.handle(withChoices({ default_action: 'rm -rf', items: [{ n: 1, name: 5, dir: null, kind: undefined, path: '/etc/passwd' }] }), T0);
  const item = e.snapshot(T0).choices;
  assert.equal(item.defaultAction, 'reveal'); assert.deepEqual(item.items, [{ n: 1, name: '5', dir: '', kind: '' }]);
});
