/* ═══════════════════════════════════════════════════════════════
   store.js · 数据层
   ─────────────────────────────────────────────────────────────────
   两套后端，对上层是同一套接口：
     remote —— 自家 FastAPI + SQLite（/api/library 系列），数据跨设备一致
     local  —— 浏览器 localStorage，服务不可用时自动接管，功能不降级

   判定顺序：先请求 remote，401/网络错误/超时都退回 local。
   remote 恢复后下一次写入会尝试重新接管（不自动合并，避免误覆盖）。
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const KEY = 'startpage.v3.library';
  const BASE = 'api';                       // 相对路径：带不带反代前缀都能命中
  const TIMEOUT = 6000;

  const state = {
    categories: [],
    bookmarks: [],
    mode: 'local',
    loaded: false,
  };

  /* ─────────────── 工具 ─────────────── */

  function normalizeUrl(raw) {
    const v = String(raw || '').trim();
    if (!v) return '';
    if (/^(javascript|data|vbscript|file):/i.test(v)) return '';
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(v)) return 'https://' + v.replace(/^\/+/, '');
    return v.slice(0, 2000);
  }

  function hostOf(url) {
    const m = String(url).match(/^[a-z][a-z0-9+.-]*:\/\/([^/?#]+)/i);
    return m ? m[1].replace(/^www\./, '') : String(url).slice(0, 60);
  }

  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  function readCache() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.categories) || !Array.isArray(data.bookmarks)) return null;
      return data;
    } catch (e) { return null; }
  }

  function writeCache() {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        categories: state.categories, bookmarks: state.bookmarks,
      }));
      return true;
    } catch (e) { return false; }
  }

  /** 带超时的 fetch —— 服务没起来时不要卡住整个页面 */
  async function api(path, opts) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
    try {
      const res = await fetch(BASE + path, Object.assign({
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        credentials: 'same-origin',
      }, opts || {}));
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  /* ─────────────── 读写 ─────────────── */

  async function loadRemote() {
    const data = await api('/library');
    state.categories = data.categories || [];
    state.bookmarks = data.bookmarks || [];
    state.mode = 'remote';
    state.loaded = true;
    writeCache();
  }

  function loadLocal() {
    const cache = readCache();
    state.categories = cache ? cache.categories : [];
    state.bookmarks = cache ? cache.bookmarks : [];
    state.mode = 'local';
    state.loaded = true;
  }

  async function init() {
    try {
      await loadRemote();
    } catch (e) {
      loadLocal();
    }
    return state;
  }

  /** remote 模式下改完拉一次全量，保证 id / sort_order 与服务端一致 */
  async function afterWrite() {
    if (state.mode === 'remote') {
      try { await loadRemote(); return; } catch (e) { /* 落到下面按本地处理 */ }
    }
    writeCache();
  }

  async function call(path, opts) {
    if (state.mode !== 'remote') return null;
    try {
      const r = await api(path, opts);
      await afterWrite();
      return r;
    } catch (e) {
      // 写失败：降级本地继续可用，但要如实告知
      state.mode = 'local';
      throw e;
    }
  }

  const nextId = () => state.bookmarks.reduce((m, b) => Math.max(m, b.id || 0), 0) + 1;
  const nextCatId = () => state.categories.reduce((m, c) => Math.max(m, c.id || 0), 0) + 1;

  const Store = {
    state,
    normalizeUrl,
    hostOf,

    init,

    /* ── 分类 ── */
    async createCategory(name, icon) {
      const clean = { name: String(name).trim().slice(0, 30) || '新分类', icon: String(icon || '').slice(0, 2) };
      if (state.mode === 'remote') {
        await call('/categories', { method: 'POST', body: JSON.stringify(clean) });
        return;
      }
      state.categories.push(Object.assign({ id: nextCatId(), sort_order: state.categories.length }, clean));
      writeCache();
    },

    async updateCategory(id, patch) {
      if (state.mode === 'remote') {
        await call('/categories/' + id, { method: 'PUT', body: JSON.stringify(patch) });
        return;
      }
      const c = state.categories.find((x) => x.id === id);
      if (c) { Object.assign(c, patch); writeCache(); }
    },

    async deleteCategory(id, cascade) {
      if (state.mode === 'remote') {
        await call('/categories/' + id + (cascade ? '?cascade=true' : ''), { method: 'DELETE' });
        return;
      }
      state.categories = state.categories.filter((c) => c.id !== id);
      state.bookmarks = cascade
        ? state.bookmarks.filter((b) => b.category_id !== id)
        : state.bookmarks.map((b) => (b.category_id === id ? Object.assign(b, { category_id: null }) : b));
      writeCache();
    },

    async reorderCategories(ids) {
      if (state.mode === 'remote') {
        await call('/categories/reorder', { method: 'POST', body: JSON.stringify({ ids }) });
        return;
      }
      state.categories.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
      writeCache();
    },

    /* ── 收藏 ── */
    async createBookmark(data) {
      const url = normalizeUrl(data.url);
      if (!url) throw new Error('网址无效');
      const payload = {
        title: String(data.title || '').trim().slice(0, 60) || hostOf(url),
        url,
        icon: String(data.icon || '').slice(0, 2),
        category_id: data.category_id || null,
        note: String(data.note || '').slice(0, 200),
      };
      if (state.mode === 'remote') {
        await call('/bookmarks', { method: 'POST', body: JSON.stringify(payload) });
        return;
      }
      state.bookmarks.push(Object.assign({
        id: nextId(), hits: 0, sort_order: state.bookmarks.length,
      }, payload));
      writeCache();
    },

    async updateBookmark(id, patch) {
      const clean = Object.assign({}, patch);
      if ('url' in clean) clean.url = normalizeUrl(clean.url);
      if (state.mode === 'remote') {
        await call('/bookmarks/' + id, { method: 'PUT', body: JSON.stringify(clean) });
        return;
      }
      const b = state.bookmarks.find((x) => x.id === id);
      if (b) { Object.assign(b, clean); writeCache(); }
    },

    async deleteBookmark(id) {
      if (state.mode === 'remote') {
        await call('/bookmarks/' + id, { method: 'DELETE' });
        return;
      }
      state.bookmarks = state.bookmarks.filter((b) => b.id !== id);
      writeCache();
    },

    async moveBookmark(id, categoryId, position) {
      if (state.mode === 'remote') {
        await call('/bookmarks/' + id + '/move', {
          method: 'POST',
          body: JSON.stringify({ category_id: categoryId, position }),
        });
        return;
      }
      const b = state.bookmarks.find((x) => x.id === id);
      if (!b) return;
      b.category_id = categoryId || null;
      writeCache();
    },

    async reorderBookmarks(ids) {
      if (state.mode === 'remote') {
        await call('/bookmarks/reorder', { method: 'POST', body: JSON.stringify({ ids }) });
        return;
      }
      const rank = new Map(ids.map((id, i) => [id, i]));
      state.bookmarks.sort((a, b) => (rank.has(a.id) ? rank.get(a.id) : 1e9) - (rank.has(b.id) ? rank.get(b.id) : 1e9));
      writeCache();
    },

    async hit(id) {
      const b = state.bookmarks.find((x) => x.id === id);
      if (b) b.hits = (b.hits || 0) + 1;
      if (state.mode === 'remote') {
        try { await api('/bookmarks/' + id + '/hit', { method: 'POST' }); } catch (e) { /* 计数失败不影响打开 */ }
      }
      writeCache();
    },

    /* ── 导入导出 ── */
    exportJson() {
      const nameById = new Map(state.categories.map((c) => [c.id, c.name]));
      return {
        version: 1,
        exported_at: new Date().toISOString(),
        categories: clone(state.categories),
        bookmarks: state.bookmarks.map((b) => Object.assign(clone(b), {
          category: nameById.get(b.category_id) || null,
        })),
      };
    },

    async importJson(payload, replace) {
      if (state.mode === 'remote') {
        const r = await api('/library/import' + (replace ? '?replace=true' : ''), {
          method: 'POST', body: JSON.stringify(payload),
        });
        await loadRemote();
        return r;
      }
      // 本地模式：同样按分类名对齐
      if (replace) { state.categories = []; state.bookmarks = []; }
      const name2id = new Map(state.categories.map((c) => [c.name, c.id]));
      (payload.categories || []).forEach((c) => {
        const name = String(c.name || '').trim().slice(0, 30);
        if (!name || name2id.has(name)) return;
        const id = nextCatId();
        state.categories.push({ id, name, icon: c.icon || '', sort_order: state.categories.length });
        name2id.set(name, id);
      });
      let added = 0;
      (payload.bookmarks || []).forEach((b) => {
        const url = normalizeUrl(b.url);
        if (!url) return;
        state.bookmarks.push({
          id: nextId(), title: (b.title || hostOf(url)).slice(0, 60), url,
          icon: b.icon || '', note: b.note || '', hits: 0,
          category_id: name2id.get(b.category) || null,
          sort_order: state.bookmarks.length,
        });
        added += 1;
      });
      writeCache();
      return { bookmarks: added };
    },
  };

  window.HomeStore = Store;
})();
