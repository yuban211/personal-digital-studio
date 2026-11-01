/* ═══════════════════════════════════════════════════════════════
   quant.js · 全A行情 + 模拟实盘页面
   ─────────────────────────────────────────────────────────────────
   原则：
     1. 数据只从自己的服务端拿（/api/*），页面不直连任何第三方
     2. 轮询有节流，表格滚动时不重排（避免"抖"）
     3. 图表用 canvas 手写，不引图表库
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const API = 'api/';          // 相对路径：反代带不带前缀都能命中

  const state = {
    page: 1, size: 50, sort: 'chg', order: 'desc', filter: 'all', q: '',
    pages: 1, total: 0,
    rows: [],
    filterList: [
      ['all', '全部'], ['up', '上涨'], ['down', '下跌'],
      ['limit_up', '涨停'], ['limit_down', '跌停'],
      ['main', '主板'], ['gem', '创业板'], ['star', '科创板'], ['bj', '北交所'],
    ],
    detail: null,
    engineRunning: true,
  };

  /* ─────────────── 工具 ─────────────── */

  function cls(v) { return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'; }
  function sign(v) { return v > 0 ? '+' : ''; }

  function fmtAmount(v) {
    if (!v) return '—';
    if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return String(Math.round(v));
  }

  function fmtMoney(v) {
    if (v == null) return '—';
    return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(v) { return (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%'; }
  function fmtPrice(v) { return Number(v).toFixed(v >= 100 ? 2 : v >= 10 ? 2 : 3); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function get(path) {
    const res = await fetch(API + path, { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function post(path, body) {
    const res = await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  let toastTimer = 0;
  function toast(msg) {
    const el = $('toast');
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2400);
  }

  /* ─────────────── 指数条 ─────────────── */

  function renderIndices(list) {
    $('indices').innerHTML = (list || []).map((i) => `
      <div class="idx">
        <span class="n">${esc(i.name)}</span>
        <span class="v ${cls(i.chg)}">${fmtPrice(i.price)}</span>
        <span class="c ${cls(i.chg)}">${sign(i.chg_amt)}${i.chg_amt.toFixed(2)} ${fmtPct(i.chg)}</span>
      </div>`).join('');
  }

  /* ─────────────── 涨跌分布 ─────────────── */

  function renderBreadth(b) {
    if (!b) return;
    const total = Math.max(1, b.total);
    const upPct = (b.up / total) * 100;
    const flatPct = (b.flat / total) * 100;
    const downPct = (b.down / total) * 100;
    $('bfill').children[0].style.width = upPct + '%';
    $('bfill').children[1].style.width = flatPct + '%';
    $('bfill').children[2].style.width = downPct + '%';
    $('blegend').innerHTML = `
      <span>上涨 <b class="up">${b.up}</b></span>
      <span>平盘 <b class="flat">${b.flat}</b></span>
      <span>下跌 <b class="down">${b.down}</b></span>
      <span>涨停 <b class="up">${b.limit_up}</b></span>
      <span>跌停 <b class="down">${b.limit_down}</b></span>`;
    $('breadthMeta').textContent = `共 ${b.total} 只 · 成交额 ${fmtAmount(b.amount)}`;
  }

  /* ─────────────── 板块 ─────────────── */

  function renderSectors(list) {
    $('sectors').innerHTML = (list || []).slice(0, 18).map((s) => `
      <div class="sec ${s.chg > 2 ? 'hot' : s.chg < -2 ? 'cold' : ''}" title="${esc(s.name)} · 领涨 ${esc(s.leader)}">
        <div class="sn"><span>${esc(s.name)}</span><span class="sc ${cls(s.chg)}">${fmtPct(s.chg)}</span></div>
        <div class="sl">${esc(s.leader || '—')} ${s.leader_chg ? fmtPct(s.leader_chg) : ''}</div>
      </div>`).join('');
  }

  /* ─────────────── 全A表格 ─────────────── */

  function renderRows(rows) {
    if (!rows.length) {
      $('rows').innerHTML = '<tr><td colspan="14" class="meta" style="text-align:center;padding:22px">没有匹配的标的</td></tr>';
      return;
    }
    $('rows').innerHTML = rows.map((r) => `
      <tr data-code="${r.code}">
        <td class="c-code">${r.code}</td>
        <td class="c-name">${esc(r.name)}</td>
        <td class="num ${cls(r.chg)}">${r.price ? fmtPrice(r.price) : '—'}</td>
        <td class="num ${cls(r.chg)}">${r.price ? fmtPct(r.chg) : '—'}</td>
        <td class="num ${cls(r.chg)}">${r.price ? sign(r.chg_amt) + r.chg_amt.toFixed(2) : '—'}</td>
        <td class="num">${fmtAmount(r.amount)}</td>
        <td class="num">${r.turnover ? r.turnover.toFixed(2) + '%' : '—'}</td>
        <td class="num">${r.vol_ratio ? r.vol_ratio.toFixed(2) : '—'}</td>
        <td class="num">${r.amplitude ? r.amplitude.toFixed(2) + '%' : '—'}</td>
        <td class="num">${r.high ? fmtPrice(r.high) : '—'}</td>
        <td class="num">${r.low ? fmtPrice(r.low) : '—'}</td>
        <td class="num">${r.pe ? r.pe.toFixed(1) : '—'}</td>
        <td class="num">${fmtAmount(r.mktcap)}</td>
        <td class="num ${cls(r.main_flow)}">${fmtAmount(r.main_flow)}</td>
      </tr>`).join('');
  }

  function renderPager() {
    $('pageInfo').textContent = `第 ${state.page} / ${state.pages} 页 · 共 ${state.total} 只`;
    $('prevPage').disabled = state.page <= 1;
    $('nextPage').disabled = state.page >= state.pages;
    const th = document.querySelectorAll('#grid thead th[data-sort]');
    th.forEach((el) => {
      el.classList.toggle('sorted', el.dataset.sort === state.sort);
      el.classList.toggle('asc', el.dataset.sort === state.sort && state.order === 'asc');
    });
  }

  async function loadList() {
    const qs = new URLSearchParams({
      page: state.page, size: state.size, sort: state.sort,
      order: state.order, filter: state.filter, q: state.q,
    });
    const d = await get('list?' + qs.toString());
    state.rows = d.rows || [];
    state.total = d.total || 0;
    state.pages = d.pages || 1;
    state.page = Math.min(state.page, state.pages);
    renderRows(state.rows);
    renderPager();
    $('listMeta').textContent = `快照 ${d.snapshot_age >= 0 ? d.snapshot_age.toFixed(0) + 's 前' : '—'}`;
  }

  /* ─────────────── 账户 ─────────────── */

  function renderAccount(a) {
    $('equity').innerHTML = `${fmtMoney(a.equity)}<small>总资产</small>`;
    $('kPnl').textContent = `${sign(a.pnl)}${fmtMoney(a.pnl)} (${fmtPct(a.pnl_pct)})`;
    $('kPnl').className = cls(a.pnl);
    $('kCash').textContent = fmtAmount(a.cash);
    $('kMv').textContent = fmtAmount(a.market_value);
    $('posMeta').textContent = `${a.position_count} 只 · 浮盈 ${sign(a.float_pnl)}${fmtAmount(a.float_pnl)}`;

    // 今日盈亏：优先用净值曲线最后两点
    const curve = a.curve || [];
    if (curve.length >= 2) {
      const prev = curve[curve.length - 2].total;
      const day = a.equity - prev;
      $('kDay').textContent = `${sign(day)}${fmtMoney(day)}`;
      $('kDay').className = cls(day);
    } else {
      $('kDay').textContent = '—';
    }

    $('posRows').innerHTML = a.positions.length ? a.positions.map((p) => `
      <tr data-code="${p.code}">
        <td class="c-name">${esc(p.name)}<br><span class="meta">${p.code}</span></td>
        <td class="num">${p.qty}</td>
        <td class="num">${p.cost.toFixed(2)}</td>
        <td class="num ${cls(p.pnl)}">${p.price.toFixed(2)}</td>
        <td class="num ${cls(p.pnl)}">${sign(p.pnl)}${fmtAmount(p.pnl)}<br>${fmtPct(p.pnl_pct)}</td>
        <td class="num">${p.weight.toFixed(1)}%</td>
        <td class="num"><button class="btn sm ghost" data-sell="${p.code}" data-avail="${p.available}">卖</button></td>
      </tr>`).join('')
      : '<tr><td colspan="7" class="meta" style="text-align:center;padding:14px">空仓</td></tr>';

    drawLine($('navChart'), curve.map((x) => x.total), a.initial, {
      height: 120, fill: true,
    });
  }

  function renderTrades(d) {
    const list = (d.trades || []).slice(0, 40);
    $('tradeMeta').textContent = `最近 ${list.length} 笔`;
    $('trades').innerHTML = list.map((t) => `
      <li>
        <span class="t">${t.ts.slice(11, 16)}</span>
        <span class="nm ${t.side === 'buy' ? 'up' : 'down'}">${t.side === 'buy' ? '买' : '卖'} ${esc(t.name)} ${t.qty}股 @${Number(t.price).toFixed(2)}</span>
        <span class="am">${fmtAmount(t.amount)}</span>
      </li>`).join('') || '<li class="meta">暂无成交</li>';
  }

  function renderSignals(engine) {
    const sigs = engine.signals || [];
    const exec = engine.executed || [];
    if (!sigs.length) {
      $('signals').innerHTML = `<span class="meta">最近一轮 ${engine.last_tick || '—'} 无信号</span>`;
      return;
    }
    $('signals').innerHTML = sigs.slice(0, 12).map((s) => `
      <div class="sig">
        <span>${s.side === 'buy' ? '买' : '卖'} ${esc(s.name || s.code)}<span class="meta"> ${esc(s.code)}</span></span>
        <span class="r">${esc(s.rejected ? '未成交：' + s.rejected : s.reason || '')}</span>
      </div>`).join('')
      + (exec.length ? `<div class="sig" style="margin-top:6px"><span class="meta">本轮成交 ${exec.length} 笔</span></div>` : '');
  }

  /* ─────────────── 画图（canvas 手写） ─────────────── */

  function prepare(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 320;
    const h = Number(canvas.getAttribute('height')) || 120;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function drawLine(canvas, values, baseline, opt) {
    if (!canvas) return;
    const { ctx, w, h } = prepare(canvas);
    const pad = { l: 4, r: 4, t: 8, b: 12 };
    if (!values || values.length < 2) {
      ctx.fillStyle = '#98a0a8';
      ctx.font = '11px -apple-system, "Microsoft YaHei", sans-serif';
      ctx.fillText('暂无净值数据', 8, h / 2);
      return;
    }
    const min = Math.min(...values, baseline || Infinity);
    const max = Math.max(...values, baseline || -Infinity);
    const span = max - min || 1;
    const x = (i) => pad.l + (i / (values.length - 1)) * (w - pad.l - pad.r);
    const y = (v) => pad.t + (1 - (v - min) / span) * (h - pad.t - pad.b);

    // 基准线
    if (baseline) {
      ctx.strokeStyle = '#e3e6ea';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(pad.l, y(baseline));
      ctx.lineTo(w - pad.r, y(baseline));
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const last = values[values.length - 1];
    const color = last >= (baseline || last) ? '#d9272e' : '#12864a';
    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.stroke();

    if (opt && opt.fill) {
      ctx.lineTo(x(values.length - 1), h - pad.b);
      ctx.lineTo(x(0), h - pad.b);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
      grad.addColorStop(0, color + '22');
      grad.addColorStop(1, color + '00');
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // 末端点
    ctx.beginPath();
    ctx.arc(x(values.length - 1), y(last), 2.6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  function drawTrend(canvas, points, preclose) {
    if (!canvas) return;
    const { ctx, w, h } = prepare(canvas);
    const pad = { l: 4, r: 44, t: 8, b: 14 };
    if (!points || !points.length) {
      ctx.fillStyle = '#98a0a8';
      ctx.font = '11px sans-serif';
      ctx.fillText('无分时数据（非交易日或停牌）', 8, h / 2);
      return;
    }
    const prices = points.map((p) => p.price);
    const maxDev = Math.max(...prices.map((p) => Math.abs(p - preclose)), preclose * 0.005);
    const hi = preclose + maxDev;
    const lo = preclose - maxDev;
    const x = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);

    ctx.strokeStyle = '#eceef1';
    ctx.beginPath();
    ctx.moveTo(pad.l, y(preclose));
    ctx.lineTo(w - pad.r, y(preclose));
    ctx.stroke();

    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p.price)) : ctx.moveTo(x(i), y(p.price))));
    const up = prices[prices.length - 1] >= preclose;
    ctx.strokeStyle = up ? '#d9272e' : '#12864a';
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // 均价线
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p.avg)) : ctx.moveTo(x(i), y(p.avg))));
    ctx.strokeStyle = 'rgba(216, 160, 30, .8)';
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = '#8b9199';
    ctx.font = '10px sans-serif';
    ctx.fillText(hi.toFixed(2), w - pad.r + 4, pad.t + 8);
    ctx.fillText(lo.toFixed(2), w - pad.r + 4, h - pad.b);
  }

  /* ─────────────── 详情抽屉 ─────────────── */

  async function openDetail(code) {
    $('drawer').hidden = false;
    $('dName').textContent = '载入中…';
    $('dCode').textContent = code;
    try {
      const d = await get('quote/' + code);
      const q = d.quote;
      state.detail = q;
      $('dName').textContent = q.name;
      $('dCode').textContent = `${q.code} · ${q.market === 1 ? '沪' : '深'}`;
      $('dQuote').innerHTML = `
        <div>最新价<b class="${cls(q.chg)}">${fmtPrice(q.price)}</b></div>
        <div>涨跌幅<b class="${cls(q.chg)}">${fmtPct(q.chg)}</b></div>
        <div>成交额<b>${fmtAmount(q.amount)}</b></div>
        <div>今开<b>${fmtPrice(q.open)}</b></div>
        <div>最高<b class="up">${fmtPrice(q.high)}</b></div>
        <div>最低<b class="down">${fmtPrice(q.low)}</b></div>
        <div>换手率<b>${q.turnover.toFixed(2)}%</b></div>
        <div>量比<b>${q.vol_ratio.toFixed(2)}</b></div>
        <div>市盈率<b>${q.pe ? q.pe.toFixed(1) : '—'}</b></div>`;
      drawTrend($('dChart'), d.trend.points, d.trend.preclose || q.preclose);
      state.detailTrend = d.trend.points;
      state.detailPreclose = d.trend.preclose || q.preclose;
      $('dHint').textContent = `买入按 100 股整数倍 · 现价约 ${fmtPrice(q.price)} 元/股`;
    } catch (e) {
      $('dName').textContent = '加载失败';
    }
  }

  function closeDetail() {
    $('drawer').hidden = true;
    state.detail = null;
  }

  async function trade(side, code, qty) {
    if (!code) return;
    try {
      const d = await post('trade', { code, side, qty });
      toast(d.ok ? `${side === 'buy' ? '买入' : '卖出'}成功` : ('未成交：' + (d.result && d.result.reason)));
      await refreshAccount();
      await loadList();
    } catch (e) {
      toast('下单失败');
    }
  }

  /* ─────────────── 轮询 ─────────────── */

  async function refreshOverview() {
    try {
      const d = await get('overview');
      renderIndices(d.indices);
      renderBreadth(d.breadth);
      renderSectors(d.sectors);
      const live = d.mode === 'session';
      const tag = $('modeTag');
      tag.textContent = live ? '盘中' : (d.mode === 'offhours' ? '非交易时段 · 模拟撮合' : '休市');
      tag.className = 'tag ' + (live ? 'live' : 'off');
      $('metaLine').textContent = `更新 ${d.ts.slice(11)}`;
    } catch (e) {
      $('modeTag').textContent = '连接失败';
    }
  }

  async function refreshAccount() {
    try {
      const d = await get('account');
      renderAccount(d);
    } catch (e) { /* 静默 */ }
  }

  async function refreshStatus() {
    try {
      const d = await get('status');
      const eng = d.engine || {};
      state.engineRunning = !!eng.running;
      $('btnEngine').textContent = eng.running ? '暂停引擎' : '启动引擎';
      $('btnEngine').className = 'btn sm ' + (eng.running ? 'primary' : '');
      $('stratTag').textContent = eng.strategy || '—';
      renderSignals(eng);
      if (eng.last_error) $('listMeta').textContent = '引擎提示：' + eng.last_error;
    } catch (e) { /* 静默 */ }
  }

  async function refreshOrders() {
    try { renderTrades(await get('orders?limit=40')); } catch (e) { /* 静默 */ }
  }

  async function refreshAll() {
    await Promise.allSettled([refreshOverview(), loadList(), refreshAccount(), refreshStatus()]);
  }

  /* ─────────────── 策略面板 ─────────────── */

  async function loadStrategies() {
    const d = await get('strategies');
    const sel = $('stratSel');
    sel.innerHTML = (d.strategies || []).map((s) => (
      `<option value="${s.key}" ${s.key === d.current ? 'selected' : ''}>${esc(s.label)}${s.builtin ? '' : '（自定义）'}</option>`
    )).join('');
    $('stratDesc').textContent = (d.strategies.find((s) => s.key === d.current) || {}).desc || '';
    const p = d.params || {};
    $('rStop').value = p.stop_loss != null ? p.stop_loss : -8;
    $('rTake').value = p.take_profit != null ? p.take_profit : 25;
    $('rPos').value = Math.round((p.position_pct != null ? p.position_pct : 0.12) * 100);
    $('rMax').value = p.max_positions != null ? p.max_positions : 8;
  }

  async function saveStrategy() {
    const payload = {
      strategy: $('stratSel').value,
      stop_loss: Number($('rStop').value),
      take_profit: Number($('rTake').value),
      position_pct: Number($('rPos').value) / 100,
      max_positions: Number($('rMax').value),
    };
    try {
      await post('strategy', payload);
      toast('设置已保存');
      await loadStrategies();
    } catch (e) {
      toast('保存失败');
    }
  }

  /* ─────────────── 事件绑定 ─────────────── */

  function bind() {
    // 筛选 chips
    $('filters').innerHTML = state.filterList.map(([k, label]) => (
      `<button class="chip" type="button" data-filter="${k}" aria-pressed="${k === state.filter}">${label}</button>`
    )).join('');
    $('filters').addEventListener('click', (e) => {
      const btn = e.target.closest('.chip');
      if (!btn) return;
      state.filter = btn.dataset.filter;
      state.page = 1;
      $('filters').querySelectorAll('.chip').forEach((c) => {
        c.setAttribute('aria-pressed', String(c.dataset.filter === state.filter));
      });
      loadList();
    });

    // 排序
    document.querySelectorAll('#grid thead th[data-sort]').forEach((th) => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (state.sort === key) state.order = state.order === 'desc' ? 'asc' : 'desc';
        else { state.sort = key; state.order = 'desc'; }
        state.page = 1;
        loadList();
      });
    });

    // 搜索（防抖）
    let searchTimer = 0;
    $('search').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      const v = e.target.value.trim();
      searchTimer = setTimeout(() => { state.q = v; state.page = 1; loadList(); }, 260);
    });

    $('prevPage').addEventListener('click', () => {
      if (state.page > 1) { state.page -= 1; loadList(); }
    });
    $('nextPage').addEventListener('click', () => {
      if (state.page < state.pages) { state.page += 1; loadList(); }
    });

    // 行点击 → 详情
    $('rows').addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-code]');
      if (tr) openDetail(tr.dataset.code);
    });
    $('posRows').addEventListener('click', (e) => {
      const btn = e.target.closest('[data-sell]');
      if (btn) {
        if (window.confirm('卖出全部可卖数量？')) trade('sell', btn.dataset.sell, Number(btn.dataset.avail));
        return;
      }
      const tr = e.target.closest('tr[data-code]');
      if (tr) openDetail(tr.dataset.code);
    });

    // 抽屉
    $('drawerMask').addEventListener('click', closeDetail);
    $('dClose').addEventListener('click', closeDetail);
    $('dBuy').addEventListener('click', () => {
      if (state.detail) trade('buy', state.detail.code, Number($('dQty').value));
    });
    $('dSell').addEventListener('click', () => {
      if (state.detail) trade('sell', state.detail.code, Number($('dQty').value));
    });

    // 引擎
    $('btnEngine').addEventListener('click', async () => {
      try {
        await post('engine', { action: state.engineRunning ? 'stop' : 'start' });
        await refreshStatus();
        toast(state.engineRunning ? '引擎已暂停' : '引擎已启动');
      } catch (e) { toast('操作失败'); }
    });
    $('btnTick').addEventListener('click', async () => {
      $('btnTick').disabled = true;
      try {
        const d = await post('engine', { action: 'tick', run: true });
        toast(`本轮信号 ${d.result.signals} 个，成交 ${d.result.executed} 笔`);
        await Promise.allSettled([refreshAccount(), refreshStatus(), refreshOrders()]);
      } catch (e) { toast('执行失败'); }
      $('btnTick').disabled = false;
    });

    // 策略
    $('stratSel').addEventListener('change', () => {
      const opt = $('stratSel').selectedOptions[0];
      $('stratDesc').textContent = opt ? opt.textContent : '';
    });
    $('btnSave').addEventListener('click', saveStrategy);
    $('btnReset').addEventListener('click', async () => {
      if (!window.confirm('重置账户会清空持仓、委托、成交与净值曲线，确定？')) return;
      try { await post('reset', { confirm: true }); await refreshAll(); await refreshOrders(); toast('账户已重置'); }
      catch (e) { toast('重置失败'); }
    });

    window.addEventListener('resize', () => {
      if (state.detail) drawTrend($('dChart'), state.detailTrend || [], state.detailPreclose || 0);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeDetail();
    });
  }

  /* ─────────────── 启动 ─────────────── */

  async function boot() {
    bind();
    try {
      await Promise.all([loadStrategies(), refreshAll(), refreshOrders()]);
    } catch (e) {
      toast('服务连接失败');
    }
    setInterval(refreshOverview, 5000);
    setInterval(() => { if (!document.hidden) loadList(); }, 6000);
    setInterval(() => { if (!document.hidden) refreshAccount(); }, 6000);
    setInterval(() => { if (!document.hidden) refreshStatus(); }, 8000);
    setInterval(() => { if (!document.hidden) refreshOrders(); }, 15000);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
