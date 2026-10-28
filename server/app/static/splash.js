/* ═══════════════════════════════════════════════════════════════
   开屏动画控制
   ─────────────────────────────────────────────────────────────────
   设计要点（借鉴参考站并做了更稳的处理）：
     1. sessionStorage 记录播放状态：首次 2200ms，之后 300ms
     2. 等待「关键资源就绪」但不等待全部 —— 最多等 2.2s 就放行
     3. 防 FOUC：开屏元素由 HTML 直接渲染，不靠 JS 创建，
        所以即使 JS 失败，开屏也只是「不消失」而不会是空白页
        —— 所以必须给一个兜底：页面 load 后无论如何都移除
     4. 减少动效 / still 模式下直接不显示（CSS 已处理 display:none，
        这里再确保 DOM 被移除，避免挡住交互）
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const splash = document.getElementById('splash');
  if (!splash) return;

  const html = document.documentElement;
  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const motionMode = html.dataset.motion || 'immersive';

  const KEY = 'studio:splashShown';

  // ── 不显示开屏的情况：直接移除，绝不残留遮罩 ──
  if (reduceMQ.matches || motionMode === 'still') {
    splash.remove();
    return;
  }

  let alreadyShown = false;
  try {
    alreadyShown = sessionStorage.getItem(KEY) === '1';
  } catch (e) {
    alreadyShown = false;
  }

  // 首次 2200ms 完整播放；会话内二次进入 300ms 快速过
  const MIN_DISPLAY = alreadyShown ? 300 : 2200;
  const EXIT_MS = alreadyShown ? 450 : 1100;

  try {
    sessionStorage.setItem(KEY, '1');
  } catch (e) { /* 忽略 */ }

  if (alreadyShown) splash.classList.add('splash--quick');

  const started = Date.now();
  let exited = false;

  function exit() {
    if (exited) return;
    exited = true;
    splash.classList.add('splash--exit');
    // 等退场动画结束再真正移除，避免留下不可见的遮挡层
    setTimeout(() => {
      splash.remove();
    }, EXIT_MS + 80);
  }

  function scheduleExit() {
    const elapsed = Date.now() - started;
    const wait = Math.max(0, MIN_DISPLAY - elapsed);
    setTimeout(exit, wait);
  }

  // 等关键资源：DOM 就绪 + 首屏主图（如果有）
  const domReady = new Promise((resolve) => {
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      resolve();
    } else {
      document.addEventListener('DOMContentLoaded', resolve, { once: true });
    }
  });

  Promise.race([
    domReady,
    // 兜底：最多等 1.8s，绝不因资源慢而卡住开屏
    new Promise((r) => setTimeout(r, 1800)),
  ]).then(scheduleExit);

  // 双保险：页面 load 后无论如何都开始退场
  window.addEventListener('load', () => {
    setTimeout(scheduleExit, 60);
  }, { once: true });

  // 极端兜底：4 秒后强制移除，防止任何情况下开屏卡住
  setTimeout(() => {
    if (document.body.contains(splash)) exit();
  }, 4000);

  // 键盘/点击可跳过（无障碍：不让用户被强制等待）
  const skip = () => exit();
  splash.addEventListener('click', skip, { once: true });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') skip();
  }, { once: true });
})();
