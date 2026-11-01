/* ═══════════════════════════════════════════════════════════════
   home.js · 壳层逻辑
   ─────────────────────────────────────────────────────────────────
   负责：时钟 / 搜索路由 / 偏好设置 / 壁纸 / 便签 / 抽屉 / 快捷键
   收藏夹的增删改在 bookmarks.js，数据在 store.js。
   不依赖任何第三方库；搜索引擎只是"跳转到对方页面"，不加载对方脚本。
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const PREF_KEY = 'startpage.v3.prefs';
  const NOTE_KEY = 'startpage.v3.note';
  const HIST_KEY = 'startpage.v3.history';

  const ENGINES = {
    bing: { name: '必应', url: 'https://www.bing.com/search?q=' },
    baidu: { name: '百度', url: 'https://www.baidu.com/s?wd=' },
    google: { name: 'Google', url: 'https://www.google.com/search?q=' },
    sogou: { name: '搜狗', url: 'https://www.sogou.com/web?query=' },
    ddg: { name: 'DuckDuckGo', url: 'https://duckduckgo.com/?q=' },
  };

  const WALLS = [
    { key: 'dawn', name: '晨光' },
    { key: 'dusk', name: '暮色' },
    { key: 'forest', name: '林间' },
    { key: 'ocean', name: '深海' },
    { key: 'ink', name: '墨黑' },
    { key: 'linen', name: '亚麻' },
  ];

  const DEFAULTS = {
    wallpaper: 'dawn',
    customWall: '',
    blur: 0,
    dim: 20,
    engine: 'bing',
    customEngine: '',
    newTab: false,
    showClock: true,
    showCats: true,
    showNote: false,
  };

  let prefs = Object.assign({}, DEFAULTS);
  let toastTimer = 0;
  let noteTimer = 0;

  /* ─────────────── 偏好 ─────────────── */

  function loadPrefs() {
    try {
      const raw = localStorage.getItem(PREF_KEY);
      if (raw) prefs = Object.assign({}, DEFAULTS, JSON.parse(raw));
    } catch (e) { /* 坏数据按默认走，不阻塞页面 */ }
  }

  function savePrefs(patch) {
    prefs = Object.assign({}, prefs, patch);
    try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (e) { /* 隐私模式 */ }
    applyPrefs();
  }

  function applyPrefs() {
    const body = document.body;
    body.dataset.wallpaper = prefs.customWall ? 'custom' : prefs.wallpaper;
    document.documentElement.style.setProperty('--blur', prefs.blur + 'px');
    document.documentElement.style.setProperty('--dim', (prefs.dim / 100).toFixed(2));
    const wall = $('wallpaper');
    wall.style.backgroundImage = prefs.customWall ? 'url("' + prefs.customWall + '")' : '';

    $('clock').style.display = prefs.showClock ? '' : 'none';
    $('dateText').style.display = prefs.showClock ? '' : 'none';
    $('lunarText').style.display = prefs.showClock ? '' : 'none';
    $('cats').hidden = !prefs.showCats;
    if (!prefs.showNote) $('notePanel').hidden = true;

    $('engineName').textContent = engineLabel();
    $('setEngine').value = prefs.engine;
    $('setCustomEngine').value = prefs.customEngine;
    $('setNewTab').checked = !!prefs.newTab;
    $('setShowClock').checked = !!prefs.showClock;
    $('setShowCats').checked = !!prefs.showCats;
    $('setShowNote').checked = !!prefs.showNote;
    $('setBlur').value = prefs.blur;
    $('setBlurOut').textContent = prefs.blur + 'px';
    $('setDim').value = prefs.dim;
    $('setDimOut').textContent = prefs.dim + '%';

    document.querySelectorAll('.wall-chip').forEach((el) => {
      const on = !prefs.customWall && el.dataset.wall === prefs.wallpaper;
      el.setAttribute('aria-pressed', String(on));
    });
  }

  function engineLabel() {
    if (prefs.customEngine && prefs.engine === 'custom') return '自定义';
    return (ENGINES[prefs.engine] || ENGINES.bing).name;
  }

  /* ─────────────── 提示 ─────────────── */

  function toast(msg) {
    const el = $('toast');
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
  }

  /* ─────────────── 时钟 ─────────────── */

  function tick() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    $('clock').textContent = pad(now.getHours()) + ':' + pad(now.getMinutes());
    const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()];
    $('dateText').textContent = (now.getMonth() + 1) + ' 月 ' + now.getDate() + ' 日 · 星期' + week;

    // 第几周：以本年第 1 个周四所在周为第 1 周（ISO 8601）
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const day = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - day + 3);
    const firstThu = new Date(d.getFullYear(), 0, 4);
    const firstDay = (firstThu.getDay() + 6) % 7;
    firstThu.setDate(firstThu.getDate() - firstDay + 3);
    const weekNo = 1 + Math.round((d - firstThu) / 604800000);
    $('lunarText').textContent = now.getFullYear() + ' 年 · 第 ' + weekNo + ' 周';
  }

  /* ─────────────── 搜索 ─────────────── */

  function isUrlLike(text) {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) return true;
    if (/^localhost(:\d+)?(\/|$)/i.test(text)) return true;
    return /^[\w-]+(\.[\w-]+)+(\/[\w\-./?%&=#]*)?$/.test(text) && !/\s/.test(text);
  }

  function searchUrl(query) {
    const q = encodeURIComponent(query);
    if (prefs.engine === 'custom' && /\{\}/.test(prefs.customEngine)) {
      return prefs.customEngine.replace('{}', q);
    }
    const eng = ENGINES[prefs.engine] || ENGINES.bing;
    return eng.url + q;
  }

  function pushHistory(term) {
    if (!term) return;
    try {
      const raw = JSON.parse(localStorage.getItem(HIST_KEY) || '[]');
      const list = [term].concat(raw.filter((x) => x !== term)).slice(0, 24);
      localStorage.setItem(HIST_KEY, JSON.stringify(list));
    } catch (e) { /* 忽略 */ }
  }

  function readHistory() {
    try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch (e) { return []; }
  }

  function submitSearch(e) {
    if (e) e.preventDefault();
    const input = $('searchInput');
    const raw = input.value.trim();
    if (!raw) return;
    const url = isUrlLike(raw) ? window.HomeStore.normalizeUrl(raw) : searchUrl(raw);
    hideSuggest();
    if (!isUrlLike(raw)) pushHistory(raw);
    if (e && e.shiftKey) window.open(url, '_blank', 'noopener');
    else if (prefs.newTab) window.open(url, '_blank', 'noopener');
    else window.location.href = url;
  }

  let suggestIndex = -1;

  function showSuggest() {
    const q = $('searchInput').value.trim();
    const box = $('suggest');
    const items = [];
    if (q) {
      if (isUrlLike(q)) items.push({ label: '打开 ' + q, url: window.HomeStore.normalizeUrl(q), tag: '直达' });
      items.push({ label: '用' + engineLabel() + '搜索「' + q + '」', url: searchUrl(q), tag: '搜索' });
      window.HomeStore.state.bookmarks
        .filter((b) => (b.title + ' ' + b.url).toLowerCase().includes(q.toLowerCase()))
        .slice(0, 5)
        .forEach((b) => items.push({ label: b.title, url: b.url, tag: '收藏' }));
    } else {
      readHistory().slice(0, 6).forEach((h) => items.push({ label: h, url: searchUrl(h), tag: '历史' }));
    }
    if (!items.length) { hideSuggest(); return; }

    box.innerHTML = '';
    items.forEach((it, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.dataset.url = it.url;
      const span = document.createElement('span');
      span.textContent = it.label;
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = it.tag;
      btn.append(span, tag);
      if (i === suggestIndex) btn.classList.add('active');
      btn.addEventListener('mousedown', (ev) => {
        ev.preventDefault();
        hideSuggest();
        window.location.href = it.url;
      });
      box.appendChild(btn);
    });
    box.hidden = false;
  }

  function hideSuggest() {
    $('suggest').hidden = true;
    suggestIndex = -1;
  }

  function moveSuggest(delta) {
    const btns = Array.from($('suggest').querySelectorAll('button'));
    if (!btns.length) return;
    suggestIndex = (suggestIndex + delta + btns.length) % btns.length;
    btns.forEach((b, i) => b.classList.toggle('active', i === suggestIndex));
    btns[suggestIndex].scrollIntoView({ block: 'nearest' });
  }

  function renderEngineMenu() {
    const menu = $('engineMenu');
    menu.innerHTML = '';
    Object.keys(ENGINES).forEach((key) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = ENGINES[key].name;
      b.setAttribute('aria-selected', String(prefs.engine === key));
      b.addEventListener('click', () => {
        savePrefs({ engine: key });
        menu.hidden = true;
        $('engineBtn').setAttribute('aria-expanded', 'false');
        $('searchInput').focus();
      });
      menu.appendChild(b);
    });
    if (prefs.customEngine) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = '自定义';
      b.setAttribute('aria-selected', String(prefs.engine === 'custom'));
      b.addEventListener('click', () => {
        savePrefs({ engine: 'custom' });
        menu.hidden = true;
        $('engineBtn').setAttribute('aria-expanded', 'false');
      });
      menu.appendChild(b);
    }
  }

  /* ─────────────── 壁纸与偏好 UI ─────────────── */

  function renderWalls() {
    const box = $('wallpapers');
    box.innerHTML = '';
    WALLS.forEach((w) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'wall-chip';
      chip.dataset.wall = w.key;
      chip.title = w.name;
      // 用同一套渐变做缩略图，避免额外请求图片
      chip.style.background = gradientFor(w.key);
      const label = document.createElement('span');
      label.textContent = w.name;
      chip.appendChild(label);
      chip.addEventListener('click', () => savePrefs({ wallpaper: w.key, customWall: '' }));
      box.appendChild(chip);
    });
    if (prefs.customWall) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'wall-chip';
      chip.dataset.wall = 'custom';
      chip.style.backgroundImage = 'url("' + prefs.customWall + '")';
      chip.style.backgroundSize = 'cover';
      const label = document.createElement('span');
      label.textContent = '自定义';
      chip.appendChild(label);
      chip.addEventListener('click', () => savePrefs({ customWall: prefs.customWall }));
      box.appendChild(chip);
    }
  }

  function gradientFor(key) {
    const map = {
      dawn: 'linear-gradient(155deg,#24323c,#3b4a52 38%,#a08a6d 74%,#d8b98a)',
      dusk: 'linear-gradient(160deg,#2a1f36,#4b2c45 45%,#8a4a51)',
      forest: 'linear-gradient(150deg,#0f241d,#24473a 46%,#5d8468)',
      ocean: 'linear-gradient(160deg,#0b1f2c,#164055 50%,#2f6f86)',
      ink: 'linear-gradient(150deg,#16181b,#26292e 55%,#3a3f45)',
      linen: 'linear-gradient(150deg,#d8cec0,#e8e1d6 45%,#c9bfae)',
    };
    return map[key] || map.dawn;
  }

  function readImageFile(file, cb) {
    if (!file) return;
    if (file.size > 3 * 1024 * 1024) { toast('图片超过 3MB，换一张小点的'); return; }
    const reader = new FileReader();
    reader.onload = () => cb(String(reader.result));
    reader.onerror = () => toast('读取失败');
    reader.readAsDataURL(file);
  }

  /* ─────────────── 便签 ─────────────── */

  function openNote(open) {
    $('notePanel').hidden = !open;
    savePrefs({ showNote: open });
    if (open) {
      $('noteBody').value = localStorage.getItem(NOTE_KEY) || '';
      setTimeout(() => $('noteBody').focus(), 60);
    }
  }

  function bindNote() {
    $('noteBody').addEventListener('input', () => {
      const v = $('noteBody').value;
      $('noteSaved').textContent = '保存中…';
      clearTimeout(noteTimer);
      noteTimer = setTimeout(() => {
        try {
          localStorage.setItem(NOTE_KEY, v);
          $('noteSaved').textContent = '已保存';
        } catch (e) { $('noteSaved').textContent = '保存失败'; }
      }, 350);
    });
    $('btnNoteClose').addEventListener('click', () => openNote(false));
  }

  /* ─────────────── 设置抽屉 ─────────────── */

  function openDrawer(open) {
    $('drawer').hidden = !open;
    $('btnSettings').setAttribute('aria-expanded', String(open));
    if (open) renderCatManager();
  }

  function renderCatManager() {
    const list = $('catList');
    if (!list) return;
    const st = window.HomeStore.state;
    list.innerHTML = '';
    if (!st.categories.length) {
      const li = document.createElement('li');
      li.textContent = '还没有分类';
      list.appendChild(li);
      return;
    }
    st.categories.forEach((c) => {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'grow';
      name.textContent = (c.icon ? c.icon + ' ' : '') + c.name;
      const count = document.createElement('span');
      count.className = 'count';
      count.textContent = st.bookmarks.filter((b) => b.category_id === c.id).length + ' 项';
      const edit = document.createElement('button');
      edit.className = 'mini';
      edit.type = 'button';
      edit.textContent = '改名';
      edit.addEventListener('click', () => window.HomeBookmarks.openCatModal(c));
      const del = document.createElement('button');
      del.className = 'mini danger';
      del.type = 'button';
      del.textContent = '删除';
      del.addEventListener('click', async () => {
        const n = st.bookmarks.filter((b) => b.category_id === c.id).length;
        const msg = n
          ? '「' + c.name + '」里有 ' + n + ' 个收藏。\n确定 = 连同收藏一起删除；取消 = 只删分类（收藏变未分类）。'
          : '删除分类「' + c.name + '」？';
        const cascade = n ? window.confirm(msg) : true;
        try { await window.HomeStore.deleteCategory(c.id, cascade); } catch (e) { /* 本地已生效 */ }
        if (window.HomeBookmarks.getActiveCat() === c.id) window.HomeBookmarks.setActiveCat('all');
        window.HomeBookmarks.renderAll();
      });
      li.append(name, count, edit, del);
      list.appendChild(li);
    });
  }

  async function doExport() {
    let payload;
    try {
      payload = window.HomeStore.exportJson();
    } catch (e) {
      toast('导出失败');
      return;
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'startpage-bookmarks-' + new Date().toISOString().slice(0, 10) + '.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    toast('已导出 ' + payload.bookmarks.length + ' 条');
  }

  function doImport(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      let data;
      try { data = JSON.parse(String(reader.result)); } catch (e) { toast('不是有效的 JSON'); return; }
      if (!data || !Array.isArray(data.bookmarks)) { toast('文件里没有 bookmarks 数组'); return; }
      const replace = window.confirm('导入 ' + data.bookmarks.length + ' 条收藏。\n确定 = 先清空现有数据再导入；取消 = 追加合并。');
      try {
        const r = await window.HomeStore.importJson(data, replace);
        window.HomeBookmarks.renderAll();
        toast('已导入 ' + ((r && r.bookmarks) || data.bookmarks.length) + ' 条');
      } catch (e) {
        toast('导入失败');
      }
    };
    reader.readAsText(file);
  }

  /* ─────────────── 启动 ─────────────── */

  async function boot() {
    loadPrefs();
    renderWalls();
    renderEngineMenu();
    applyPrefs();
    tick();
    setInterval(tick, 1000);

    const st = await window.HomeStore.init();
    window.HomeBookmarks.init();

    const modeText = st.mode === 'remote' ? '存储：服务端 SQLite（已连接）' : '存储：浏览器本地（服务端未连接）';
    $('storeMode').textContent = modeText;
    if (st.mode === 'local') setTimeout(() => toast('服务端未连接，已切换到本地存储'), 600);

    // 搜索
    $('searchForm').addEventListener('submit', submitSearch);
    const input = $('searchInput');
    input.addEventListener('input', () => { suggestIndex = -1; showSuggest(); });
    input.addEventListener('focus', showSuggest);
    input.addEventListener('blur', () => setTimeout(hideSuggest, 140));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveSuggest(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveSuggest(-1); }
      else if (e.key === 'Escape') { hideSuggest(); }
    });
    $('engineBtn').addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = $('engineMenu');
      menu.hidden = !menu.hidden;
      $('engineBtn').setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('#engineMenu') && !e.target.closest('#engineBtn')) {
        $('engineMenu').hidden = true;
        $('engineBtn').setAttribute('aria-expanded', 'false');
      }
    });

    // 抽屉与设置项
    $('btnSettings').addEventListener('click', () => openDrawer($('drawer').hidden));
    $('btnDrawerClose').addEventListener('click', () => openDrawer(false));
    $('drawerMask').addEventListener('click', () => openDrawer(false));

    $('setEngine').addEventListener('change', (e) => { savePrefs({ engine: e.target.value }); renderEngineMenu(); });
    $('setCustomEngine').addEventListener('input', (e) => {
      savePrefs({ customEngine: e.target.value.trim(), engine: e.target.value.trim() ? 'custom' : prefs.engine });
      renderEngineMenu();
    });
    $('setNewTab').addEventListener('change', (e) => savePrefs({ newTab: e.target.checked }));
    $('setShowClock').addEventListener('change', (e) => savePrefs({ showClock: e.target.checked }));
    $('setShowCats').addEventListener('change', (e) => savePrefs({ showCats: e.target.checked }));
    $('setShowNote').addEventListener('change', (e) => savePrefs({ showNote: e.target.checked }));
    $('setBlur').addEventListener('input', (e) => savePrefs({ blur: Number(e.target.value) }));
    $('setDim').addEventListener('input', (e) => savePrefs({ dim: Number(e.target.value) }));

    $('wallUpload').addEventListener('change', (e) => {
      readImageFile(e.target.files[0], (dataUrl) => {
        try {
          savePrefs({ customWall: dataUrl });
          renderWalls();
          toast('壁纸已更换');
        } catch (err) {
          toast('图片太大，浏览器存不下');
        }
      });
      e.target.value = '';
    });

    $('btnAddCat').addEventListener('click', () => window.HomeBookmarks.openCatModal(null));
    $('btnExport').addEventListener('click', doExport);
    $('importFile').addEventListener('change', (e) => { doImport(e.target.files[0]); e.target.value = ''; });

    // 便签
    bindNote();
    $('btnNote').addEventListener('click', () => openNote($('notePanel').hidden));
    if (prefs.showNote) openNote(true);

    // 行情页
    const toQuant = () => { window.location.href = 'quant/'; };
    $('btnQuotes').addEventListener('click', toQuant);
    $('quantLink').addEventListener('click', (e) => { e.preventDefault(); toQuant(); });

    // 快捷键
    document.addEventListener('keydown', (e) => {
      const meta = e.ctrlKey || e.metaKey;
      if (meta && e.key.toLowerCase() === 'k') { e.preventDefault(); input.focus(); input.select(); }
      if (meta && e.key === ',') { e.preventDefault(); openDrawer(true); }
      if (e.key === '/' && document.activeElement !== input && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
        e.preventDefault(); input.focus();
      }
      if (e.key === 'n' && !meta && e.altKey) window.HomeBookmarks.openBookmarkModal(null, window.HomeBookmarks.getActiveCat());
    });
  }

  window.HomeUI = { toast, renderCatManager, openDrawer, engineLabel };
  document.addEventListener('DOMContentLoaded', boot);
})();
