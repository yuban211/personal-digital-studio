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
  let weatherAbort = null;
  let weatherRequest = 0;
  let weatherTimer = 0;
  let drawerTimer = 0;
  let focusBeforeDrawer = null;

  /* ─────────────── 时间与问候 ─────────────── */

  function tickClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    $('clock').textContent = hh + ':' + mm;
    const date = new Intl.DateTimeFormat('zh-CN', {
      month: 'long', day: 'numeric', weekday: 'long',
    }).format(now);
    $('dateLabel').textContent = date;

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
      if (!s) return DEFAULT_BM.slice();
      const parsed = JSON.parse(s);
      if (!Array.isArray(parsed)) return DEFAULT_BM.slice();
      return parsed.filter((b) => b && typeof b.title === 'string'
        && typeof b.url === 'string' && b.title.trim() && safeBookmarkURL(b.url)).slice(0, 48);
    } catch (e) {
      return DEFAULT_BM.slice();
    }
  }

  function safeBookmarkURL(raw) {
    try {
      const parsed = new URL(String(raw), window.location.href);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch (e) { return false; }
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
      a.style.setProperty('--item-index', Math.min(i, 8));
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
      const normalizedUrl = /^https?:\/\//i.test(url) ? url : 'https://' + url;
      if (!safeBookmarkURL(normalizedUrl)) { alert('请输入有效的网址'); return; }
      const l = loadBM();
      l.push({ title: title.trim().slice(0, 48), url: normalizedUrl });
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
      if (!s) return [];
      const parsed = JSON.parse(s);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((t) => t && typeof t.text === 'string').slice(-300);
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
      li.style.setProperty('--item-index', Math.min(i, 12));
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
    const requestId = ++weatherRequest;
    if (weatherAbort) weatherAbort.abort();
    if (!cfg().showWeather) { el.hidden = true; return; }

    const controller = new AbortController();
    weatherAbort = controller;
    const timeout = setTimeout(() => controller.abort(), 7000);

    try {
      // Open-Meteo：免密钥、无需注册
      const city = cfg().city || 'Shanghai';
      const geoResponse = await fetch(
        'https://geocoding-api.open-meteo.com/v1/search?name='
        + encodeURIComponent(city) + '&count=1',
        { signal: controller.signal }
      );
      if (!geoResponse.ok) throw new Error('geocoding failed');
      const geo = await geoResponse.json();

      if (requestId !== weatherRequest) return;
      if (!geo.results || !geo.results.length) { el.hidden = true; return; }
      const { latitude, longitude, name } = geo.results[0];

      const weatherResponse = await fetch(
        'https://api.open-meteo.com/v1/forecast?latitude=' + latitude
        + '&longitude=' + longitude + '&current=temperature_2m,weather_code',
        { signal: controller.signal }
      );
      if (!weatherResponse.ok) throw new Error('weather failed');
      const w = await weatherResponse.json();

      const t = w.current && w.current.temperature_2m;
      if (requestId !== weatherRequest) return;
      if (!Number.isFinite(t)) { el.hidden = true; return; }
      el.textContent = name + ' ' + Math.round(t) + '°';
      el.hidden = false;
    } catch (e) {
      // 断网或失败：静默隐藏，绝不弹错
      if (requestId === weatherRequest) el.hidden = true;
    } finally {
      clearTimeout(timeout);
      if (weatherAbort === controller) weatherAbort = null;
    }
  }

  function scheduleWeather() {
    clearTimeout(weatherTimer);
    weatherTimer = setTimeout(loadWeather, 220);
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
      $('setQuotes').checked = !!c.showQuotes;
      $('setQuotesApi').value = c.quotesApi || '';
      $('setWatchlist').value = (c.watchlist || []).join(', ');
    }

    /** "600519.sh, 000858" → ['600519.SH', '000858']。中英文逗号、分号、空格都认。 */
    function parseWatchlist(raw) {
      return String(raw || '')
        .split(/[,，;；\s]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);
    }

    function bind(event) {
      const custom = $('setCustomUrl').value.trim();
      const prevWatch = cfg().watchlist || [];
      const nextWatch = parseWatchlist($('setWatchlist').value);

      window.StartPageConfig.set({
        engine: sel.value,
        customEngineUrl: custom,
        useCustomEngine: !!custom,
        openInNewTab: $('setNewTab').checked,
        showWeather: $('setWeather').checked,
        city: $('setCity').value.trim(),
        apiBase: $('setApi').value.trim(),
        showQuotes: $('setQuotes').checked,
        quotesApi: $('setQuotesApi').value.trim(),
        watchlist: nextWatch,
      });

      // 自选池变了要通知服务端改抓取范围，否则新加的票永远没数据
      if (window.StartPageQuotes
          && JSON.stringify(prevWatch) !== JSON.stringify(nextWatch)) {
        window.StartPageQuotes.refresh(prevWatch);
      }
      if (event && (event.target === $('setWeather') || event.target === $('setCity'))) {
        scheduleWeather();
      }
    }

    [sel, $('setCustomUrl'), $('setNewTab'), $('setWeather'),
     $('setCity'), $('setApi'), $('setQuotes'), $('setQuotesApi'),
     $('setWatchlist')].forEach((el) => {
      el.addEventListener('change', bind);
    });

    $('btnSettings').addEventListener('click', () => {
      fill();
      focusBeforeDrawer = document.activeElement;
      clearTimeout(drawerTimer);
      $('drawer').hidden = false;
      $('btnSettings').setAttribute('aria-expanded', 'true');
      requestAnimationFrame(() => {
        $('drawer').classList.add('is-open');
        sel.focus();
      });
    });
    function closeDrawer() {
      if ($('drawer').hidden) return;
      clearTimeout(drawerTimer);
      $('drawer').classList.remove('is-open');
      $('btnSettings').setAttribute('aria-expanded', 'false');
      const delay = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 190;
      drawerTimer = setTimeout(() => {
        $('drawer').hidden = true;
        if (focusBeforeDrawer && focusBeforeDrawer.focus) focusBeforeDrawer.focus();
      }, delay);
    }
    $('btnClose').addEventListener('click', closeDrawer);
    $('drawer').addEventListener('click', (event) => {
      if (event.target === $('drawer')) closeDrawer();
    });
    document.addEventListener('keydown', (event) => {
      if ($('drawer').hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeDrawer();
      } else if (event.key === 'Tab') {
        const focusable = Array.from($('drawer').querySelectorAll(
          'button:not([disabled]), input:not([disabled]):not([type="file"]), select:not([disabled]), textarea:not([disabled])'
        )).filter((el) => el.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    });

    $('btnReset').addEventListener('click', () => {
      if (!confirm('恢复默认设置？快捷入口和待办不受影响。')) return;
      window.StartPageConfig.reset();
      fill();
      scheduleWeather();
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
          scheduleWeather();
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
    requestAnimationFrame(() => document.body.classList.add('is-ready'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
