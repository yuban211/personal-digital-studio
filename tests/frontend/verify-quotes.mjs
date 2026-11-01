/* 前端渲染验证：用最小 DOM 桩跑 web/quotes.js 的真实代码。
 *
 * 为什么不用浏览器：本机 Edge 已卸载、无 puppeteer/playwright，
 * 而截图只能看"好不好看"，看不出"逻辑对不对"。这里直接断言
 * 生成的 DOM 结构、涨跌 class、降级提示是否按预期出现。
 *
 * 运行：node tests/frontend/verify-quotes.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, '../../src/static/web');

/* ── 最小 DOM ── */
class El {
  constructor(tag) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = [];
    this._html = '';
    this.className = '';
    this.hidden = false;
    this.textContent = '';
    this.style = {};
  }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get innerHTML() {
    // textContent 也要算进来：空态提示是 textContent 赋值，不是 innerHTML
    return this._html + this.textContent
      + this.children.map((c) => c.innerHTML).join('');
  }
  get outerHTML() {
    return `<${this.tagName.toLowerCase()} class="${this.className}">${this.innerHTML}</${this.tagName.toLowerCase()}>`;
  }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener() {}
  querySelector() { return null; }
}

function makeDocument(ids) {
  const nodes = {};
  ids.forEach((id) => { nodes[id] = new El('div'); });
  return {
    readyState: 'loading',              // 阻止 quotes.js 自动 start，由测试显式调用
    hidden: false,
    getElementById: (id) => nodes[id] || null,
    createElement: (tag) => new El(tag),
    addEventListener() {},
    _nodes: nodes,
  };
}

function makeContext(fetchImpl, opts = {}) {
  const ids = ['quotes', 'quotesSection', 'quotesStatus'];
  const document = makeDocument(ids);
  const store = new Map();
  if (opts.config) store.set('startpage.v2.config', JSON.stringify(opts.config));

  const sandbox = {
    console,
    document,
    navigator: { onLine: true },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    fetch: fetchImpl,
    AbortController,
    setTimeout, clearTimeout, setInterval, clearInterval,
    JSON, Math, String, Number, Array, Object, Date, isFinite,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);

  for (const f of ['config.js', 'quotes.js']) {
    vm.runInContext(readFileSync(resolve(WEB, f), 'utf8'), ctx, { filename: f });
  }
  return { ctx, document };
}

/* ── 断言 ── */
let failed = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log(`  OK   ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${name}${detail ? ' → ' + detail : ''}`);
  }
}

const QUOTES = [
  { market: 1, code: '600519', name: '贵州茅台', close: 1392.05, pre_close: 1354.0 },
  { market: 0, code: '000858', name: '五 粮 液', close: 74.70, pre_close: 76.00 },
  { market: 0, code: '300750', name: '宁德时代', close: 232.92, pre_close: 232.92 },
];

function respond(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ── 场景 1：正常行情，验证涨跌配色与渲染 ── */
console.log('\n[1] 正常返回三只票（含涨/跌/平）');
{
  const { ctx, document } = makeContext((url) => {
    if (String(url).includes('/api/watch')) return respond({ ok: true, symbols: [] });
    return respond({ ok: true, age: 0.2, quotes: QUOTES });
  }, { config: { showQuotes: true, watchlist: ['600519.SH', '000858.SZ', '300750.SZ'] } });

  ctx.window.StartPageQuotes.poll();
  await sleep(60);

  const box = document._nodes.quotes;
  const section = document._nodes.quotesSection;
  check('区块未被隐藏', section.hidden === false);
  check('渲染出 3 行情卡片', box.children.length === 3, `实际 ${box.children.length}`);

  const rows = box.children.map((c) => c.innerHTML);
  check('茅台显示为上涨（up）', box.children[0].innerHTML.includes('q-nums up'), rows[0]);
  check('茅台涨幅 +2.81%', rows[0].includes('+2.81%'), rows[0]);
  check('茅台名称已去除空格', rows[0].includes('贵州茅台'));
  check('五粮液显示为下跌（down）', box.children[1].innerHTML.includes('q-nums down'), rows[1]);
  check('五粮液涨幅 -1.71%', rows[1].includes('-1.71%'), rows[1]);
  check('名称中的空格被清掉', rows[1].includes('五粮液'), rows[1]);
  check('平盘显示为 flat', box.children[2].innerHTML.includes('q-nums flat'), rows[2]);
  check('状态行隐藏（无异常）', document._nodes.quotesStatus.hidden === true);
}

/* ── 场景 2：服务 503（行情端未就绪），必须优雅降级 ── */
console.log('\n[2] 服务返回 503（行情未就绪）');
{
  const { ctx, document } = makeContext((url) => {
    if (String(url).includes('/api/watch')) return respond({ ok: true, symbols: [] });
    return respond({ code: 'stale' }, 503);
  }, { config: { showQuotes: true, watchlist: ['600519.SH'] } });

  ctx.window.StartPageQuotes.poll();
  await sleep(60);
  const status = document._nodes.quotesStatus;
  check('不抛异常且给出降级提示', status.hidden === false && status.textContent.length > 0,
    status.textContent);
  check('提示内容指向陈旧/重连', /陈旧|重连|连接/.test(status.textContent), status.textContent);
}

/* ── 场景 3：网络彻底失败，不能崩 ── */
console.log('\n[3] 请求抛错（服务不可达）');
{
  const { ctx, document } = makeContext(() => Promise.reject(new Error('boom')),
    { config: { showQuotes: true, watchlist: ['600519.SH'] } });

  ctx.window.StartPageQuotes.poll();
  await sleep(60);
  const status = document._nodes.quotesStatus;
  check('捕获异常并提示不可达', status.hidden === false && /不可达/.test(status.textContent),
    status.textContent);
}

/* ── 场景 4：自选为空，给出引导而不是空白 ── */
console.log('\n[4] 自选池为空');
{
  const { ctx, document } = makeContext(() => respond({ ok: true, quotes: [] }),
    { config: { showQuotes: true, watchlist: [] } });

  ctx.window.StartPageQuotes.poll();
  await sleep(60);
  const box = document._nodes.quotes;
  const hint = box.children[0];
  check('显示空态提示元素', box.children.length === 1, `实际 ${box.children.length}`);
  check('空态元素带 quotes-hint 类', hint && hint.className === 'quotes-hint');
  check('提示包含示例代码', hint && hint.textContent.includes('600519'), hint && hint.textContent);
}

/* ── 场景 5：关闭开关，整个区块隐藏 ── */
console.log('\n[5] 关闭「显示自选行情」');
{
  const { ctx, document } = makeContext(() => respond({ ok: true, quotes: QUOTES }),
    { config: { showQuotes: false, watchlist: ['600519.SH'] } });

  ctx.window.StartPageQuotes.poll();
  await sleep(60);
  check('区块被隐藏', document._nodes.quotesSection.hidden === true);
}

/* ── 场景 6：自选变更要同步到服务端 ── */
console.log('\n[6] 自选池变更同步服务端');
{
  const calls = [];
  const { ctx } = makeContext((url, opt) => {
    if (String(url).includes('/api/watch')) {
      calls.push({ url: String(url), body: opt && opt.body });
      return respond({ ok: true, symbols: ['600519.SH', '000001.SZ'] });
    }
    return respond({ ok: true, quotes: [] });
  }, { config: { showQuotes: true, watchlist: ['600519.SH', '000001.SZ'] } });

  ctx.window.StartPageQuotes.refresh(['600519.SH', '300750.SZ']);
  await sleep(80);

  const posts = calls.filter((c) => c.body);
  const merged = posts.some((c) => c.body.includes('000001') && c.body.includes('merge'));
  const removed = posts.some((c) => c.body.includes('300750') && c.body.includes('remove'));
  check('新增标的以 merge 上报', merged, JSON.stringify(posts.map((p) => p.body)));
  check('删除标的以 remove 上报', removed, JSON.stringify(posts.map((p) => p.body)));
}

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
