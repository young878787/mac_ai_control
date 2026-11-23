// 畫面上的 DOM 元件：泡泡、輸入框、快捷泡泡、搜尋結果清單。文字一律用 textContent，不解析成 HTML。

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

/** 快捷泡泡（點角色彈出）：目前只有「聊天室」一顆。show(top) 時放在頭部旁邊；滑鼠停在上面不收 */
export function createQuick(box, button, { onOpen, onHover }) {
  button.addEventListener('click', () => onOpen());
  box.addEventListener('mouseenter', () => onHover(true));
  box.addEventListener('mouseleave', () => onHover(false));
  return {
    render(show, top) {
      if (!show) { if (!box.hidden) box.hidden = true; return; }
      box.hidden = false;
      box.style.top = `${Math.round(top)}px`;
    },
  };
}

/** 搜尋結果清單：每列一筆（序號、檔名、所在資料夾）。點整列＝預設動作，右邊按鈕＝另一個動作。文字一律 textContent。
 *  聯合搜尋的網路項目（kind＝web）：名稱是標題、dir 是網域，點了用瀏覽器開，沒有另一個動作 */
export function createChoices(panel, title, list, closeButton, { onChoose, onClose }) {
  let shownKey = '';
  const verb = (action) => (action === 'open' ? '打開' : '顯示');
  closeButton.addEventListener('click', () => onClose());
  return {
    isOpen: () => !panel.hidden,
    render(choices) {
      if (!choices) {
        if (!panel.hidden) { panel.hidden = true; shownKey = ''; list.replaceChildren(); }
        return;
      }
      const key = `${choices.listId}|${choices.items.length}`;
      if (key === shownKey) return;
      shownKey = key;
      panel.hidden = false;
      const more = choices.total > choices.items.length ? `，共 ${choices.total} 筆` : '';
      title.textContent = `「${choices.query}」${more}・點選${verb(choices.defaultAction)}，或說「第二個」`;
      const other = choices.defaultAction === 'open' ? 'reveal' : 'open';
      list.replaceChildren();
      for (const item of choices.items) {
        const li = document.createElement('li');
        li.className = 'choice';
        const web = item.kind === 'web';
        li.title = web ? `${item.name}（${item.dir}）` : `${item.dir}/${item.name}`;
        const n = document.createElement('span'); n.className = 'n'; n.textContent = String(item.n);
        const text = document.createElement('span'); text.className = 'text';
        const name = document.createElement('span'); name.className = 'name'; name.textContent = item.name;
        const dir = document.createElement('span'); dir.className = 'dir'; dir.textContent = item.dir;
        text.append(name, dir);
        li.addEventListener('click', () => onChoose(item.n, null));
        li.append(n, text);
        if (!web) {
          const alt = document.createElement('button'); alt.type = 'button'; alt.textContent = verb(other);
          alt.addEventListener('click', (e) => { e.stopPropagation(); onChoose(item.n, other); });
          li.append(alt);
        }
        list.appendChild(li);
      }
    },
  };
}
