/* ═══════════════════════════════════════════════════════════════
   配置与存储
   ─────────────────────────────────────────────────────────────────
   本地优先：所有配置存在浏览器本地，不登录、不联网也能用。

   存储选择：
     - chrome.storage.local 优先（扩展环境，跨会话稳定）
     - 退化到 localStorage（普通网页打开时调试用）
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /** 预设搜索引擎。默认 Bing —— 用户要求。 */
  const ENGINES = {
    bing:       { name: 'Bing',       url: 'https://www.bing.com/search?q=' },
    google:     { name: 'Google',     url: 'https://www.google.com/search?q=' },
    duckduckgo: { name: 'DuckDuckGo', url: 'https://duckduckgo.com/?q=' },
    baidu:      { name: '百度',        url: 'https://www.baidu.com/s?wd=' },
    sogou:      { name: '搜狗',        url: 'https://www.sogou.com/web?query=' },
  };

  /** 默认配置 */
  const DEFAULTS = {
    engine: 'bing',            // 搜索引擎 key
    customEngineUrl: '',       // 自定义引擎，用 {} 占位
    useCustomEngine: false,
    openInNewTab: false,       // 搜索结果是否新标签打开
    showWeather: true,
    showClock: true,
    showBookmarks: true,
    showTodos: true,
    showQuotes: true,          // 自选行情卡片
    city: '',                  // 天气城市，空则自动
    apiBase: '',               // 可选的服务器 API 地址
    quotesApi: '',             // 行情接口，空则走同源 market（Caddy 反代）
    // 自选股。默认给三个常见标的，用户可在设置里随意改
    watchlist: ['600519.SH', '000858.SZ', '300750.SZ'],
    greeting: true,
  };

  const KEY = 'startpage.v2.config';

  /** 读取存储（统一封装，屏蔽环境差异） */
  function readRaw() {
    try {
      const s = localStorage.getItem(KEY);
      return s ? JSON.parse(s) : null;
    } catch (e) {
      return null;
    }
  }

  function writeRaw(obj) {
    try {
      localStorage.setItem(KEY, JSON.stringify(obj));
      return true;
    } catch (e) {
      return false;
    }
  }

  /** 获取配置（与默认值合并，缺字段自动补） */
  function get() {
    const stored = readRaw() || {};
    return Object.assign({}, DEFAULTS, stored);
  }

  /** 保存配置（部分更新） */
  function set(patch) {
    const cur = get();
    const next = Object.assign({}, cur, patch);
    return writeRaw(next);
  }

  /** 重置为默认 */
  function reset() {
    return writeRaw(Object.assign({}, DEFAULTS));
  }

  // 暴露给其他模块
  window.StartPageConfig = {
    ENGINES,
    DEFAULTS,
    get,
    set,
    reset,
    _readRaw: readRaw,
    _writeRaw: writeRaw,
  };
})();
