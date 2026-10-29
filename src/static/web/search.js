/* ═══════════════════════════════════════════════════════════════
   搜索引擎
   ─────────────────────────────────────────────────────────────────
   借鉴 mtab 的做法：显式引擎表 + 自定义引擎（{} 占位）。

   为什么不用"交给浏览器 omnibox"（V1 的做法）：
     - 不可预测：用户装了别的搜索扩展就会跳走
     - 无法在设置里切换引擎
     - 实测 mtab 的显式表方案更可控

   搜索栏增强（零依赖）：
     - =1+2         → 计算器
     - example.com  → 直接打开
     - https://...  → 直接打开
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /**
   * 构造搜索 URL。
   * @param {string} query 关键词
   * @param {object} cfg   配置（来自 StartPageConfig.get()）
   * @returns {string} 完整 URL
   */
  function buildSearchUrl(query, cfg) {
    const q = String(query || '').trim();
    if (!q) return '';

    // 自定义引擎优先（用 {} 占位）
    if (cfg.useCustomEngine && cfg.customEngineUrl) {
      return cfg.customEngineUrl.replace('{}', encodeURIComponent(q));
    }
    const engines = window.StartPageConfig.ENGINES;
    const e = engines[cfg.engine] || engines.bing;
    return e.url + encodeURIComponent(q);
  }

  /**
   * 判断一个字符串是不是 URL/域名（而非搜索关键词）。
   */
  function looksLikeUrl(text) {
    const t = String(text || '').trim();
    if (!t) return false;
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(t)) return true;   // 有协议
    if (/^localhost(:\d+)?(\/.*)?$/i.test(t)) return true;
    // 裸域名：至少两段，末段纯字母
    const m = t.match(/^[\w-]+(\.[\w-]+)+(:\d+)?([\/?#].*)?$/);
    if (!m) return false;
    return /\.([a-z]{2,})(?=$|[:/?#])/i.test(t);
  }

  /**
   * 计算器：=1+2*3
   * 只做安全的算术，不用 eval 执行任意代码。
   * @returns {string|null} 结果文本，非算式返回 null
   */
  function tryCalculate(text) {
    const t = String(text || '').trim();
    if (!t.startsWith('=')) return null;
    const expr = t.slice(1).trim();
    if (!expr) return null;
    // 只允许数字、运算符、括号、空格、小数点
    if (!/^[0-9+\-*/(). %\s]+$/.test(expr)) return null;

    try {
      // 用 Function 而非 eval：仍在同一作用域外，但限制在表达式内
      const fn = new Function('return (' + expr + ')');
      const r = fn();
      if (typeof r === 'number' && isFinite(r)) {
        return String(r);
      }
      return null;
    } catch (e) {
      return null;
    }
  }

  /**
   * 主入口：解析输入，决定"搜什么"或"去哪"。
   * @returns {{type:'url'|'search'|'calc'|'empty', value:string}}
   */
  function resolve(input, cfg) {
    const raw = String(input || '').trim();
    if (!raw) return { type: 'empty', value: '' };

    const calc = tryCalculate(raw);
    if (calc !== null) {
      return { type: 'calc', value: calc };
    }
    if (looksLikeUrl(raw)) {
      const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : 'https://' + raw;
      return { type: 'url', value: url };
    }
    return { type: 'search', value: buildSearchUrl(raw, cfg) };
  }

  window.StartPageSearch = {
    buildSearchUrl,
    looksLikeUrl,
    tryCalculate,
    resolve,
  };
})();
