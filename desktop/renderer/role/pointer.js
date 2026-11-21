// 點擊穿透、點擊與拖曳、右鍵選單：透明處讓滑鼠穿透到底下的視窗，點到角色才攔截。
const ALPHA_HIT = 25;                             // 游標下像素 alpha ≥ 這個值（約 10%）算點到角色
const DRAG_PX = 4;

/**
 * @param {{ alphaAt: (x: number, y: number) => number,
 *           animator: { topY(): number, headCenterY(): number, time: number },
 *           controller: { click(part: string, now: number): any[] }, attention: any,
 *           applyEffects: (effects: any[]) => void,
 *           width: number, modelH: number, headFraction: number }} deps
 */
export function createPointer({ alphaAt, animator, controller, attention, applyEffects, width, modelH, headFraction }) {
  const inUi = (target) => !!/** @type {Element} */ (target).closest('.interactive');
  const interactiveAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    return !!(el && el.closest('.interactive')) || alphaAt(x, y) >= ALPHA_HIT;
  };
  let lastHit = 0, dragging = null;
  document.addEventListener('mousemove', (e) => {
    if (dragging) return;
    const now = performance.now();
    if (now - lastHit < 33) return;
    lastHit = now;
    desktop.setIgnore(!interactiveAt(e.clientX, e.clientY));
  });
  document.addEventListener('mouseleave', () => { if (!dragging) desktop.setIgnore(true); });
  document.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || inUi(e.target) || alphaAt(e.clientX, e.clientY) < ALPHA_HIT) return;
    dragging = { x: e.screenX, y: e.clientY, moved: false };
    document.body.setPointerCapture?.(e.pointerId);
    desktop.dragStart();
  });
  document.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.screenX - dragging.x;
    if (!dragging.moved && Math.abs(dx) < DRAG_PX) return;
    dragging.moved = true;
    desktop.dragMove(dx);
  });
  document.addEventListener('pointerup', (e) => {
    if (!dragging) return;
    const d = dragging; dragging = null;
    if (d.moved) { desktop.dragEnd(); return; }
    const head = (d.y - animator.topY()) / modelH < headFraction;
    if (attention) attention.click(e.clientX - width / 2, animator.headCenterY() - e.clientY, animator.time * 1000);
    applyEffects(controller.click(head ? 'head' : 'body', Date.now()));
  });
  document.addEventListener('contextmenu', (e) => { e.preventDefault(); if (inUi(e.target) || alphaAt(e.clientX, e.clientY) >= ALPHA_HIT) desktop.menu(); });
}
