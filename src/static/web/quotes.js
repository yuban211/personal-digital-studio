/* ═══════════════════════════════════════════════════════════════
   自选行情
   ─────────────────────────────────────────────────────────────────
   数据来源：自建行情服务（services/market，FastAPI + easy_tdx）
   访问路径：同源反代 /studio/market/*（Caddy → 127.0.0.1:9111）

   设计原则（与整站一致）：
     1. 失败静默 —— 行情拉不到只显示一行灰字，绝不弹窗、不阻塞其他功能
     2. 服务端不信任前端 —— 前端只管"我要看哪些"，采集与容错都在服务端
     3. 本地优先 —— 自选列表存 localStorage，断网也能看到上次的列表结构

   为什么用轮询而不是 WebSocket：
     服务端有 /ws/quotes，但 WS 需要反向代理正确透传升级头，多一个失败点。
     采集端本身 3 秒一轮，前端也 3 秒轮询，拿到的就是服务端最新快照，
     延迟等价，链路少一跳，出问题好排查。想切 WS 把 mode 改成 'ws' 即可。
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const cfg = () => window.StartPageConfig.get();

  const POLL_INTERVAL = 3000;      // 与采集端 poll_interval 对齐
  const FETCH_TIMEOUT = 4000;      // 单次请求超时，避免卡住的请求堆积

  let timer = null;
  let inflight = false;
  let latest = [];                 // 最近一次成功的行情
  let lastError = '';              // 最近一次失败原因（用于灰字提示）
  let started = false;

  /* ─────────────── 基础 ─────────────── */

  function apiBase() {
    const raw = (cfg().quotesApi || '').trim();
    // 默认走同源反代；CSP 的 connect-src 只允许同源，写外站会被浏览器拦
    return (raw || 'market').replace(/\/+$/, '');
  }

  function url(path) {
    const base = apiBase();
    // 相对路径：以当前页面 URL 为基准解析，挂在 / 或 /studio/ 下都成立
    return base + path;
  }

  function watchlist() {
    const list = cfg().watchlist;
    return Array.isArray(list) ? list.filter(Boolean) : [];
  }

  /* ─────────────── 与服务端同步自选池 ─────────────── */

  async function postWatch(symbols, mode) {
    try {
      const r = await fetchWithTimeout(url('/api/watch'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: symbols, mode: mode || 'merge' }),
      });
      if (!r.ok) return false;
      const data = await r.json();
      return Array.isArray(data.symbols) ? data.symbols : true;
    } catch (e) {
      return false;
    }
  }

  /** 把本地列表与服务端对齐：新增的并集上去，删掉的显式移除。 */
  async function syncWatchlist(prev, next) {
    const p = prev || [];
    const n = next || [];
    const added = n.filter((s) => p.indexOf(s) === -1);
    const removed = p.filter((s) => n.indexOf(s) === -1);
    if (added.length) await postWatch(added, 'merge');
    if (removed.length) await postWatch(removed, 'remove');
    if (!p.length && n.length) await postWatch(n, 'merge');
  }

  /* ─────────────── 取数 ─────────────── */

  function fetchWithTimeout(target, options) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), FETCH_TIMEOUT);
    const opts = Object.assign({}, options || {}, { signal: ctl.signal });
    return fetch(target, opts).finally(() => clearTimeout(t));
  }

  async function poll() {
    if (inflight) return;
    // 关掉开关时也要走一次 render —— 否则区块不会立即隐藏，要等下次刷新
    if (!cfg().showQuotes) { render(); return; }
    if (!watchlist().length) { render(); return; }

    inflight = true;
    try {
      const r = await fetchWithTimeout(url('/api/quotes'), { cache: 'no-store' });
      if (r.status === 503) {
        // 服务在线但行情还没就绪/已陈旧 —— 这是预期内的降级，不当错误刷屏
        const body = await r.json().catch(() => ({}));
        lastError = body.code === 'stale' ? '行情已陈旧，采集端可能在重连' : '正在连接行情节点';
        latest = latest.length ? latest : [];
        render();
        return;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      latest = Array.isArray(data.quotes) ? data.quotes : [];
      lastError = '';
      render(data.age);
    } catch (e) {
      lastError = navigator.onLine ? '行情服务不可达' : '网络已断开';
      render();
    } finally {
      inflight = false;
    }
  }

  /* ─────────────── 渲染 ─────────────── */

  function num(v) {
    return typeof v === 'number' && isFinite(v) ? v : null;
  }

  function fmtPrice(v) {
    const n = num(v);
    if (n === null) return '—';
    return n >= 1000 ? n.toFixed(2) : n.toFixed(2);
  }

  function fmtPct(v) {
    const n = num(v);
    if (n === null) return '—';
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  /** 涨红跌绿 —— 中国市场惯例。 */
  function trendClass(delta) {
    if (!delta) return 'flat';
    return delta > 0 ? 'up' : 'down';
  }

  function render(age) {
    const box = $('quotes');
    const section = $('quotesSection');
    if (!box || !section) return;

    if (!cfg().showQuotes) { section.hidden = true; return; }
    section.hidden = false;

    const list = watchlist();
    const byKey = {};
    latest.forEach((q) => {
      if (q && q.code) byKey[String(q.code)] = q;
    });

    box.innerHTML = '';

    if (!list.length) {
      const empty = document.createElement('div');
      empty.className = 'quotes-hint';
      empty.textContent = '还没有自选股。在设置里填入代码，例如 600519.SH、000858。';
      box.appendChild(empty);
      return;
    }

    list.forEach((sym) => {
      const code = String(sym).split('.')[0];
      const q = byKey[code];

      const row = document.createElement('div');
      row.className = 'quote';

      const name = q && q.name ? String(q.name).replace(/\s+/g, '') : code;
      const close = q ? num(q.close) : null;
      const pre = q ? num(q.pre_close) : null;
      const delta = (close !== null && pre !== null && pre !== 0)
        ? (close - pre) : null;
      const pct = (delta !== null && pre) ? (delta / pre * 100) : null;
      const cls = trendClass(delta);

      row.innerHTML =
        '<div class="q-main">'
        + '<span class="q-name">' + esc(name) + '</span>'
        + '<span class="q-code">' + esc(code) + '</span>'
        + '</div>'
        + '<div class="q-nums ' + cls + '">'
        + '<span class="q-price">' + (close === null ? '—' : fmtPrice(close)) + '</span>'
        + '<span class="q-pct">' + (pct === null ? '' : fmtPct(pct)) + '</span>'
        + '</div>';

      box.appendChild(row);
    });

    // 状态行：只在异常或有时效信息时出现，正常时不占视觉
    const status = $('quotesStatus');
    if (status) {
      if (lastError) {
        status.textContent = lastError;
        status.hidden = false;
      } else if (typeof age === 'number' && age > 10) {
        status.textContent = '数据 ' + Math.round(age) + ' 秒前';
        status.hidden = false;
      } else {
        status.hidden = true;
      }
    }
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ─────────────── 生命周期 ─────────────── */

  function start() {
    if (started) return;
    started = true;
    syncWatchlist([], watchlist());
    poll();
    // 页面切到后台就停轮询，回来立刻刷一次 —— 省电、省服务端压力
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopTimers();
      } else {
        poll();
        startTimers();
      }
    });
    startTimers();
  }

  function startTimers() {
    stopTimers();
    timer = setInterval(poll, POLL_INTERVAL);
  }

  function stopTimers() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  /** 设置面板改了自选或开关后调用。 */
  function refresh(prevList) {
    const next = watchlist();
    syncWatchlist(prevList || [], next).then(() => {
      latest = [];
      poll();
    });
  }

  window.StartPageQuotes = { start, refresh, poll };

  // 与其他模块一样，等 DOM 就绪再挂
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
