/* ═══════════════════════════════════════════════════════════════
   主逻辑：搜索 / 快捷入口 / 待办 / 天气 / 设置
   ─────────────────────────────────────────────────────────────────
   原则：
     1. 本地优先 —— 断网也能用
     2. 失败静默 —— 天气拉不到就隐藏，不弹错、不白屏
     3. 零外部依赖 —— 天气用 Open-Meteo（免密钥），不引任何 CDN
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const cfg = () => window.StartPageConfig.get();

  /* ─────────────── 时间与问候 ─────────────── */

  function tickClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    $('clock').textContent = hh + ':' + mm;

    const h = now.getHours();
    const greet = h < 6 ? '夜深了' : h < 11 ? '早上好'
      : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
    $('greeting').textContent = greet;
  }

  /* ─────────────── 搜索 ─────────────── */

  function initSearch() {
    const form = $('searchForm');
    const input = $('searchInput');

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const raw = input.value;
      const r = window.StartPageSearch.resolve(raw, cfg());

      if (r.type === 'empty') return;

      // 计算器：把结果显示在提示行，不跳转
      if (r.type === 'calc') {
        $('hint').textContent = '= ' + r.value;
        input.select();
        return;
      }

      // URL 或搜索结果
      const c = cfg();
      if (c.openInNewTab) {
        window.open(r.value, '_blank');
        input.value = '';
      } else {
        window.location.href = r.value;
      }
    });

    // 快捷键：/ 聚焦搜索
    document.addEventListener('keydown', (e) => {
      const tag = (e.target.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
      if (e.key === '/' && !typing) {
        e.preventDefault();
        input.focus();
      }
      if (e.key === 'Escape' && document.activeElement === input) {
        input.value = '';
        input.blur();
      }
    });

    input.focus();
  }

  /* ─────────────── 快捷入口 ─────────────── */

  const BM_KEY = 'startpage.v2.bookmarks';
  const DEFAULT_BM = [
    { title: 'Bing', url: 'https://www.bing.com' },
    { title: 'GitHub', url: 'https://github.com' },
  ];

  function loadBM() {
    try {
      const s = localStorage.getItem(BM_KEY);
      return s ? JSON.parse(s) : DEFAULT_BM.slice();
    } catch (e) {
      return DEFAULT_BM.slice();
    }
  }

  function saveBM(list) {
    try { localStorage.setItem(BM_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function renderBM() {
    const box = $('bookmarks');
    if (!cfg().showBookmarks) { box.innerHTML = ''; return; }
    const list = loadBM();
    box.innerHTML = '';

    list.forEach((b, i) => {
      const a = document.createElement('a');
      a.className = 'bm';
      a.href = b.url;
      a.innerHTML = '<span class="bm-icon">' + esc(b.title.slice(0, 1)) + '</span>'
        + '<span class="bm-title">' + esc(b.title) + '</span>';
      box.appendChild(a);

      // 右键删除（简单可靠，不用拖拽）
      a.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        if (!confirm('删除「' + b.title + '」？')) return;
        const l = loadBM();
        l.splice(i, 1);
        saveBM(l);
        renderBM();
      });
    });

    // 添加按钮
    const add = document.createElement('button');
    add.className = 'bm bm-add';
    add.innerHTML = '<span class="bm-icon">+</span><span class="bm-title">添加</span>';
    add.addEventListener('click', () => {
      const url = prompt('网址：');
      if (!url) return;
      const title = prompt('名称：', url.replace(/^https?:\/\//, '').slice(0, 12)) || url;
      const l = loadBM();
      l.push({ title, url: /^https?:\/\//.test(url) ? url : 'https://' + url });
      saveBM(l);
      renderBM();
    });
    box.appendChild(add);
  }

  /* ─────────────── 待办 ─────────────── */

  const TODO_KEY = 'startpage.v2.todos';

  function loadTodos() {
    try {
      const s = localStorage.getItem(TODO_KEY);
      return s ? JSON.parse(s) : [];
    } catch (e) { return []; }
  }

  function saveTodos(list) {
    try { localStorage.setItem(TODO_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function renderTodos() {
    const box = $('todoList');
    box.innerHTML = '';
    const list = loadTodos();
    list.forEach((t, i) => {
      const li = document.createElement('li');
      li.className = 'todo' + (t.done ? ' done' : '');
      li.innerHTML =
        '<input type="checkbox"' + (t.done ? ' checked' : '') + '>'
        + '<span class="todo-text">' + esc(t.text) + '</span>'
        + '<button class="todo-del" aria-label="删除">✕</button>';
      li.querySelector('input').addEventListener('change', () => {
        const l = loadTodos();
        l[i].done = !l[i].done;
        saveTodos(l);
        renderTodos();
      });
      li.querySelector('.todo-del').addEventListener('click', () => {
        const l = loadTodos();
        l.splice(i, 1);
        saveTodos(l);
        renderTodos();
      });
      box.appendChild(li);
    });
  }

  function initTodo() {
    $('todoForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = $('todoInput').value.trim();
      if (!v) return;
      const l = loadTodos();
      l.push({ text: v, done: false });
      saveTodos(l);
      $('todoInput').value = '';
      renderTodos();
    });
    renderTodos();
  }

  /* ─────────────── 天气（可选，失败静默）─────────────── */

  async function loadWeather() {
    const el = $('weather');
    if (!cfg().showWeather) { el.hidden = true; return; }

    try {
      // Open-Meteo：免密钥、无需注册
      const city = cfg().city || 'Shanghai';
      const geo = await fetch(
        'https://geocoding-api.open-meteo.com/v1/search?name='
        + encodeURIComponent(city) + '&count=1'
      ).then((r) => r.json());

      if (!geo.results || !geo.results.length) { el.hidden = true; return; }
      const { latitude, longitude, name } = geo.results[0];

      const w = await fetch(
        'https://api.open-meteo.com/v1/forecast?latitude=' + latitude
        + '&longitude=' + longitude + '&current=temperature_2m,weather_code'
      ).then((r) => r.json());

      const t = w.current && w.current.temperature_2m;
      el.textContent = name + ' ' + Math.round(t) + '°';
      el.hidden = false;
    } catch (e) {
      // 断网或失败：静默隐藏，绝不弹错
      el.hidden = true;
    }
  }

  /* ─────────────── 设置 ─────────────── */

  function initSettings() {
    const sel = $('setEngine');
    const engines = window.StartPageConfig.ENGINES;
    sel.innerHTML = '';
    Object.keys(engines).forEach((k) => {
      const o = document.createElement('option');
      o.value = k;
      o.textContent = engines[k].name;
      sel.appendChild(o);
    });

    function fill() {
      const c = cfg();
      sel.value = c.engine;
      $('setCustomUrl').value = c.customEngineUrl || '';
      $('setNewTab').checked = !!c.openInNewTab;
      $('setWeather').checked = !!c.showWeather;
      $('setCity').value = c.city || '';
      $('setApi').value = c.apiBase || '';
    }

    function bind() {
      const custom = $('setCustomUrl').value.trim();
      window.StartPageConfig.set({
        engine: sel.value,
        customEngineUrl: custom,
        useCustomEngine: !!custom,
        openInNewTab: $('setNewTab').checked,
        showWeather: $('setWeather').checked,
        city: $('setCity').value.trim(),
        apiBase: $('setApi').value.trim(),
      });
    }

    [sel, $('setCustomUrl'), $('setNewTab'), $('setWeather'),
     $('setCity'), $('setApi')].forEach((el) => {
      el.addEventListener('change', bind);
    });

    $('btnSettings').addEventListener('click', () => {
      fill();
      $('drawer').hidden = false;
    });
    $('btnClose').addEventListener('click', () => { $('drawer').hidden = true; });

    $('btnReset').addEventListener('click', () => {
      if (!confirm('恢复默认设置？快捷入口和待办不受影响。')) return;
      window.StartPageConfig.reset();
      fill();
    });

    $('btnExport').addEventListener('click', () => {
      const blob = new Blob([JSON.stringify(cfg(), null, 2)],
        { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'startpage-config.json';
      a.click();
    });

    $('btnImport').addEventListener('click', () => $('importFile').click());
    $('importFile').addEventListener('change', (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fr = new FileReader();
      fr.onload = () => {
        try {
          const obj = JSON.parse(fr.result);
          window.StartPageConfig.set(obj);
          fill();
          alert('已导入');
        } catch (err) {
          alert('不是有效的配置文件');
        }
      };
      fr.readAsText(f);
    });

    fill();
  }

  /* ─────────────── 工具 ─────────────── */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ─────────────── 启动 ─────────────── */

  function boot() {
    tickClock();
    setInterval(tickClock, 30000);
    initSearch();
    renderBM();
    initTodo();
    initSettings();
    loadWeather();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
