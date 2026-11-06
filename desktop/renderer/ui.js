// 畫面上的 DOM 元件：泡泡、輸入框、紀錄面板。文字一律用 textContent，不解析成 HTML。

export function createBubbles(container, { onHover }) {
  const els = new Map();
  return {
    render(list) {
      const ids = new Set(list.map(b => b.id));
      for (const [id, el] of els) {
        if (ids.has(id) || el.classList.contains('leaving')) continue;
        el.classList.add('leaving');
        setTimeout(() => { el.remove(); if (els.get(id) === el) els.delete(id); }, 250);
      }
      for (const b of list) {
        let el = els.get(b.id);
        if (!el || el.classList.contains('leaving')) {
          el = document.createElement('div');
          el.addEventListener('mouseenter', () => onHover(b.id, true));
          el.addEventListener('mouseleave', () => onHover(b.id, false));
          els.set(b.id, el);
        }
        el.className = `bubble interactive ${b.kind}`;
        if (el.textContent !== b.text) el.textContent = b.text;
        container.appendChild(el);  // 依序放回去，順序＝由舊到新
      }
    },
  };
}

export function createInputBox(form, input, { onSubmit, onClose }) {
  const defaultHint = input.placeholder;
  const box = {
    isOpen: () => !form.hidden,
    open() { form.hidden = false; input.value = ''; input.placeholder = defaultHint; input.focus(); },
    close() { if (form.hidden) return; form.hidden = true; input.blur(); input.value = ''; onClose(); },
    setHint(text) { input.placeholder = text || defaultHint; },
  };
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { box.close(); return; }
    onSubmit(text);
    box.close();
  });
  return box;
}

const ICONS = { heard: '🎙️', user: '⌨️', result: '✅', error: '⚠️' };

export function createHistory(panel, list, closeButton, { onClose }) {
  let shown = -1;
  const h = {
    isOpen: () => !panel.hidden,
    open() { panel.hidden = false; shown = -1; },
    close() { if (panel.hidden) return; panel.hidden = true; onClose(); },
    render(entries) {
      if (panel.hidden || entries.length === shown) return;
      shown = entries.length;
      list.replaceChildren();
      if (!entries.length) {
        const li = document.createElement('li');
        li.className = 'empty'; li.textContent = '還沒有紀錄';
        list.appendChild(li);
        return;
      }
      for (const e of entries) {
        const li = document.createElement('li');
        li.className = e.kind;
        li.textContent = `${ICONS[e.kind] || '•'} ${e.text}`;
        list.appendChild(li);
      }
      list.scrollTop = list.scrollHeight;
    },
  };
  closeButton.addEventListener('click', () => h.close());
  return h;
}
