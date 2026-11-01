/* ═══════════════════════════════════════════════════════════════
   bookmarks.js · 收藏夹（分类 / 网格 / 增删改 / 拖拽排序）
   ─────────────────────────────────────────────────────────────────
   交互约定：
     左键   —— 打开并计一次点击
     右键   —— 上下文菜单（打开 / 编辑 / 置顶 / 换分类 / 删除）
     拖拽   —— 同分类内排序；拖到分类标签上 = 换分类
     「+」  —— 新增，默认落在当前分类
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = () => window.HomeStore.state;

  let activeCat = 'all';          // 'all' | 'none' | number
  let ctxTarget = null;           // 右键命中的收藏
  let editing = null;             // 正在编辑的收藏（null = 新增）
  let dragId = null;

  const ui = () => window.HomeUI || { toast: function () {} };

  /* ─────────────── 渲染 ─────────────── */

  function countOf(catId) {
    if (catId === 'all') return state().bookmarks.length;
    if (catId === 'none') return state().bookmarks.filter((b) => !b.category_id).length;
    return state().bookmarks.filter((b) => b.category_id === catId).length;
  }

  function renderCats() {
    const box = $('cats');
    const cats = state().categories;
    box.innerHTML = '';

    const mk = (label, key, badge, extra) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'cat' + (extra ? ' ' + extra : '');
      btn.setAttribute('role', 'tab');
      btn.setAttribute('aria-selected', String(activeCat === key));
      btn.dataset.cat = String(key);
      btn.innerHTML = '<span></span>';
      btn.firstChild.textContent = label;
      if (badge !== undefined) {
        const b = document.createElement('span');
        b.className = 'badge';
        b.textContent = badge;
        btn.appendChild(b);
      }
      return btn;
    };

    box.appendChild(mk('全部', 'all', countOf('all')));
    cats.forEach((c) => {
      box.appendChild(mk((c.icon ? c.icon + ' ' : '') + c.name, c.id, countOf(c.id)));
    });
    if (countOf('none')) box.appendChild(mk('未分类', 'none', countOf('none')));

    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'cat add';
    add.textContent = '+ 分类';
    add.title = '新增分类';
    add.addEventListener('click', () => openCatModal(null));
    box.appendChild(add);
  }

  function visible() {
    const list = state().bookmarks.slice();
    let out;
    if (activeCat === 'all') out = list;
    else if (activeCat === 'none') out = list.filter((b) => !b.category_id);
    else out = list.filter((b) => b.category_id === activeCat);
    return out.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id);
  }

  function hueOf(text) {
    let h = 0;
    for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) % 997;
    return h % 6;
  }

  function iconText(b) {
    if (b.icon) return b.icon;
    const t = (b.title || window.HomeStore.hostOf(b.url) || '?').trim();
    return t.slice(0, 1).toUpperCase();
  }

  function renderGrid() {
    const grid = $('grid');
    const items = visible();
    grid.innerHTML = '';

    items.forEach((b) => {
      const tile = document.createElement('a');
      tile.className = 'tile';
      tile.href = b.url;
      tile.dataset.id = String(b.id);
      tile.draggable = true;
      tile.title = b.note ? b.title + '\n' + b.note : b.title;

      const icon = document.createElement('span');
      icon.className = 'tile-icon';
      icon.dataset.hue = String(hueOf(b.title || b.url));
      icon.textContent = iconText(b);

      const name = document.createElement('span');
      name.className = 'tile-name';
      name.textContent = b.title;

      tile.append(icon, name);
      grid.appendChild(tile);
    });

    // 末尾「+」：始终可见，避免新用户找不到入口
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'tile add';
    add.innerHTML = '<span class="tile-icon">+</span><span class="tile-name">添加</span>';
    add.addEventListener('click', () => openBookmarkModal(null));
    grid.appendChild(add);

    $('empty').hidden = items.length > 0;
    $('footStat').textContent = state().bookmarks.length
      ? state().bookmarks.length + ' 个收藏 · ' + state().categories.length + ' 个分类'
      : '还没有收藏';
  }

  function renderAll() {
    renderCats();
    renderGrid();
    if (window.HomeUI && window.HomeUI.renderCatManager) window.HomeUI.renderCatManager();
  }

  /* ─────────────── 打开 ─────────────── */

  function openBookmark(b) {
    window.HomeStore.hit(b.id);
    window.open(b.url, '_blank', 'noopener');
  }

  /* ─────────────── 上下文菜单 ─────────────── */

  function showCtx(x, y, b) {
    ctxTarget = b;
    const menu = $('ctx');
    menu.hidden = false;
    // 先显示再量尺寸，避免贴边溢出
    const rect = menu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - rect.width - 8);
    const top = Math.min(y, window.innerHeight - rect.height - 8);
    menu.style.left = Math.max(8, left) + 'px';
    menu.style.top = Math.max(8, top) + 'px';
  }

  function hideCtx() {
    $('ctx').hidden = true;
    ctxTarget = null;
  }

  function handleCtx(act) {
    const b = ctxTarget;
    if (!b) return;
    hideCtx();
    if (act === 'open') openBookmark(b);
    else if (act === 'edit') openBookmarkModal(b);
    else if (act === 'pin') {
      window.HomeStore.reorderBookmarks([b.id].concat(
        visible().filter((x) => x.id !== b.id).map((x) => x.id)
      )).then(renderAll).catch(err);
    } else if (act === 'move') {
      const name = window.prompt('移动到哪个分类？（填写分类名，留空 = 未分类）\n可选：'
        + state().categories.map((c) => c.name).join(' / '));
      if (name === null) return;
      const target = state().categories.find((c) => c.name === name.trim());
      window.HomeStore.moveBookmark(b.id, target ? target.id : null, null)
        .then(renderAll).catch(err);
    } else if (act === 'delete') {
      if (!window.confirm('删除「' + b.title + '」？')) return;
      window.HomeStore.deleteBookmark(b.id).then(renderAll).catch(err);
    }
  }

  /* ─────────────── 收藏弹窗 ─────────────── */

  function fillCatSelect(selected) {
    const sel = $('bmCat');
    sel.innerHTML = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = '未分类';
    sel.appendChild(none);
    state().categories.forEach((c) => {
      const o = document.createElement('option');
      o.value = String(c.id);
      o.textContent = c.name;
      sel.appendChild(o);
    });
    sel.value = selected == null ? '' : String(selected);
  }

  function openBookmarkModal(b, presetCat) {
    editing = b || null;
    $('bmTitle').textContent = b ? '编辑收藏' : '新增收藏';
    $('bmName').value = b ? b.title : '';
    $('bmUrl').value = b ? b.url : '';
    $('bmIcon').value = b ? b.icon || '' : '';
    $('bmNote').value = b ? b.note || '' : '';
    const cat = b ? b.category_id : (presetCat && presetCat !== 'all' && presetCat !== 'none' ? presetCat : null);
    fillCatSelect(cat);
    $('bmModal').showModal();
    setTimeout(() => $('bmName').focus(), 30);
  }

  async function submitBookmark(ev) {
    ev.preventDefault();
    const data = {
      title: $('bmName').value.trim(),
      url: $('bmUrl').value.trim(),
      icon: $('bmIcon').value.trim(),
      note: $('bmNote').value.trim(),
      category_id: $('bmCat').value || null,
    };
    if (!data.title || !window.HomeStore.normalizeUrl(data.url)) {
      ui().toast('名称和网址都不能为空');
      return;
    }
    try {
      if (editing) await window.HomeStore.updateBookmark(editing.id, data);
      else await window.HomeStore.createBookmark(data);
      $('bmModal').close();
      renderAll();
      ui().toast(editing ? '已更新' : '已添加');
    } catch (e) {
      renderAll();
      ui().toast('已保存到本地（服务端暂不可用）');
    }
  }

  /* ─────────────── 分类弹窗 ─────────────── */

  let editingCat = null;

  function openCatModal(c) {
    editingCat = c || null;
    $('catTitle').textContent = c ? '编辑分类' : '新增分类';
    $('catName').value = c ? c.name : '';
    $('catIcon').value = c ? c.icon || '' : '';
    $('catModal').showModal();
    setTimeout(() => $('catName').focus(), 30);
  }

  async function submitCat(ev) {
    ev.preventDefault();
    const name = $('catName').value.trim();
    const icon = $('catIcon').value.trim();
    if (!name) return;
    try {
      if (editingCat) await window.HomeStore.updateCategory(editingCat.id, { name, icon });
      else await window.HomeStore.createCategory(name, icon);
      $('catModal').close();
      renderAll();
    } catch (e) {
      renderAll();
      ui().toast('已保存到本地');
    }
  }

  /* ─────────────── 拖拽 ─────────────── */

  function clearDropMarks() {
    document.querySelectorAll('.tile.drop-before,.tile.drop-after').forEach((el) => {
      el.classList.remove('drop-before', 'drop-after');
    });
    document.querySelectorAll('.cat.drag-over').forEach((el) => el.classList.remove('drag-over'));
  }

  function bindDnd(grid) {
    grid.addEventListener('dragstart', (e) => {
      const tile = e.target.closest('.tile');
      if (!tile || !tile.dataset.id) return;
      dragId = Number(tile.dataset.id);
      tile.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(dragId));
    });

    grid.addEventListener('dragend', () => {
      dragId = null;
      document.querySelectorAll('.tile.dragging').forEach((el) => el.classList.remove('dragging'));
      clearDropMarks();
    });

    grid.addEventListener('dragover', (e) => {
      if (!dragId) return;
      const tile = e.target.closest('.tile');
      if (!tile || !tile.dataset.id || Number(tile.dataset.id) === dragId) return;
      e.preventDefault();
      clearDropMarks();
      const r = tile.getBoundingClientRect();
      tile.classList.add(e.clientX < r.left + r.width / 2 ? 'drop-before' : 'drop-after');
    });

    grid.addEventListener('drop', async (e) => {
      if (!dragId) return;
      const tile = e.target.closest('.tile');
      if (!tile || !tile.dataset.id || Number(tile.dataset.id) === dragId) return;
      e.preventDefault();
      const before = tile.classList.contains('drop-before');
      const targetId = Number(tile.dataset.id);
      const order = visible().map((b) => b.id).filter((id) => id !== dragId);
      const at = order.indexOf(targetId) + (before ? 0 : 1);
      order.splice(at, 0, dragId);
      clearDropMarks();
      dragId = null;
      try {
        await window.HomeStore.reorderBookmarks(order);
      } catch (err) { /* 已降级本地，渲染仍生效 */ }
      renderAll();
    });
  }

  /* ─────────────── 事件 ─────────────── */

  function bind() {
    // 分类点击 / 拖入
    $('cats').addEventListener('click', (e) => {
      const btn = e.target.closest('.cat');
      if (!btn || btn.classList.contains('add') || !btn.dataset.cat) return;
      const raw = btn.dataset.cat;
      activeCat = raw === 'all' || raw === 'none' ? raw : Number(raw);
      renderAll();
    });
    $('cats').addEventListener('dragover', (e) => {
      const btn = e.target.closest('.cat');
      if (!btn || !dragId || !btn.dataset.cat) return;
      e.preventDefault();
      btn.classList.add('drag-over');
    });
    $('cats').addEventListener('dragleave', (e) => {
      const btn = e.target.closest('.cat');
      if (btn) btn.classList.remove('drag-over');
    });
    $('cats').addEventListener('drop', async (e) => {
      const btn = e.target.closest('.cat');
      if (!btn || !dragId || !btn.dataset.cat) return;
      e.preventDefault();
      const raw = btn.dataset.cat;
      const catId = raw === 'all' || raw === 'none' ? null : Number(raw);
      const id = dragId;
      dragId = null;
      clearDropMarks();
      try { await window.HomeStore.moveBookmark(id, catId, null); } catch (err) { /* 忽略 */ }
      renderAll();
    });

    // 网格：左键打开、右键菜单、拖拽
    const grid = $('grid');
    grid.addEventListener('click', (e) => {
      const tile = e.target.closest('.tile');
      if (!tile || !tile.dataset.id) return;
      e.preventDefault();
      const b = state().bookmarks.find((x) => x.id === Number(tile.dataset.id));
      if (b) openBookmark(b);
    });
    grid.addEventListener('contextmenu', (e) => {
      const tile = e.target.closest('.tile');
      if (!tile || !tile.dataset.id) return;
      e.preventDefault();
      const b = state().bookmarks.find((x) => x.id === Number(tile.dataset.id));
      if (b) showCtx(e.clientX, e.clientY, b);
    });
    bindDnd(grid);

    // 上下文菜单
    $('ctx').addEventListener('click', (e) => {
      const btn = e.target.closest('button');
      if (btn) handleCtx(btn.dataset.act);
    });
    document.addEventListener('click', (e) => {
      if (!$('ctx').hidden && !e.target.closest('#ctx')) hideCtx();
    });
    document.addEventListener('scroll', hideCtx, true);
    window.addEventListener('resize', hideCtx);

    // 弹窗
    $('bmForm').addEventListener('submit', submitBookmark);
    $('bmCancel').addEventListener('click', () => $('bmModal').close());
    $('catForm').addEventListener('submit', submitCat);
    $('catCancel').addEventListener('click', () => $('catModal').close());

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideCtx();
    });
  }

  function init() {
    bind();
    renderAll();
  }

  window.HomeBookmarks = {
    init,
    renderAll,
    openBookmarkModal,
    openCatModal,
    setActiveCat(cat) { activeCat = cat; renderAll(); },
    getActiveCat: () => activeCat,
  };
})();
