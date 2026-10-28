/* ═══════════════════════════════════════════════════════════════
   纵向分屏翻页引擎
   ─────────────────────────────────────────────────────────────────
   复刻 Swiper vertical fullpage 的核心行为：

     1. 滚轮/键盘/触摸 → 翻页（一次一屏）
     2. ★ releaseOnEdges：内层可滚区滚到边界才翻页
        —— 这是让「整屏翻页」不别扭的关键。没有它，
           第二屏内容一长就永远看不到后面的内容。
     3. 内层仍保持原生滚动（overflow-y:auto），
        所以长列表、文章列表都能正常看

   ★ 安全设计：
     - 默认不启用；JS 成功初始化才加 .fp-enabled
     - 减少动效时不启用（退回普通滚动）
     - 任何异常都 catch，出错就退回普通滚动
     - 顶栏遮挡用 scroll-padding-top 规避
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  if (window.__fpBooted) return;
  window.__fpBooted = true;

  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const html = document.documentElement;
  const motionMode = html.dataset.motion || 'immersive';
  const animOK = () => !reduceMQ.matches && motionMode !== 'still';

  // 触控设备：保留原生滚动，不接管（手感更好）
  const isTouch = window.matchMedia('(hover: none) and (pointer: coarse)').matches;

  function init() {
    const root = document.querySelector('[data-fullpage]');
    if (!root) return;

    const viewport = root.querySelector('.fp-viewport');
    const track = root.querySelector('.fp-track');
    const slides = Array.from(root.querySelectorAll('.fp-slide'));
    const dotsWrap = root.querySelector('.fp-dots');

    if (!viewport || !track || slides.length < 2) return;

    // 减少动效 / 触控 / still 模式：不启用满屏锁定
    if (!animOK() || isTouch) {
      buildDots(false);
      return;
    }

    let index = 0;
    let moving = false;
    let locked = false;

    /* ── 指示点 ── */
    function buildDots(active) {
      if (!dotsWrap) return;
      dotsWrap.innerHTML = '';
      slides.forEach((_, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'fp-dot';
        b.setAttribute('aria-label', `第 ${i + 1} 屏`);
        if (slides[i].classList.contains('fp-slide-hero')) {
          b.dataset.theme = 'dark';
        }
        b.addEventListener('click', () => goto(i));
        dotsWrap.appendChild(b);
      });
      if (active) syncDots();
    }

    function syncDots() {
      if (!dotsWrap) return;
      Array.from(dotsWrap.children).forEach((d, i) => {
        d.classList.toggle('is-active', i === index);
        d.setAttribute('aria-current', i === index ? 'true' : 'false');
      });
    }

    /* ── 翻到第 i 屏 ── */
    function goto(i) {
      const target = Math.max(0, Math.min(slides.length - 1, i));
      if (target === index || moving) {
        // 同屏时也要确保滚动条归位
        if (target === index) return;
      }
      index = target;
      moving = true;
      track.classList.add('is-moving');
      track.style.transform = `translate3d(0, -${index * 100}%, 0)`;
      syncDots();

      // 翻页完成后：新屏的内层滚动区回到顶部
      const scroller = slides[index].querySelector('.fp-scroll');
      if (scroller) scroller.scrollTop = 0;

      setTimeout(() => {
        moving = false;
        track.classList.remove('is-moving');
      }, 720);
    }

    function next() { if (index < slides.length - 1) goto(index + 1); }
    function prev() { if (index > 0) goto(index - 1); }

    /* ── 判断内层是否还有滚动空间 ── */
    function atTop(el) { return el.scrollTop <= 1; }
    function atBottom(el) { return el.scrollTop + el.clientHeight >= el.scrollHeight - 1; }

    /* ══ 滚轮 ══ */
    let wheelLock = 0;
    window.addEventListener(
      'wheel',
      (e) => {
        if (!locked) return;
        // 只处理纵向
        if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;

        const scroller = slides[index].querySelector('.fp-scroll');

        // ★ releaseOnEdges：内层还有空间就让它自己滚
        if (scroller && scroller.scrollHeight > scroller.clientHeight + 2) {
          const down = e.deltaY > 0;
          if (down && !atBottom(scroller)) return;
          if (!down && !atTop(scroller)) return;
        }

        e.preventDefault();
        const now = Date.now();
        if (now < wheelLock) return;
        wheelLock = now + 760;

        if (e.deltaY > 0) next();
        else prev();
      },
      { passive: false }
    );

    /* ══ 键盘 ══ */
    window.addEventListener('keydown', (e) => {
      if (!locked) return;
      // 焦点在输入框时不拦截
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.target.isContentEditable) return;

      const scroller = slides[index].querySelector('.fp-scroll');

      switch (e.key) {
        case 'ArrowDown':
        case 'PageDown':
          if (scroller && !atBottom(scroller)) return;
          e.preventDefault();
          next();
          break;
        case 'ArrowUp':
        case 'PageUp':
          if (scroller && !atTop(scroller)) return;
          e.preventDefault();
          prev();
          break;
        case 'Home':
          e.preventDefault();
          goto(0);
          break;
        case 'End':
          e.preventDefault();
          goto(slides.length - 1);
          break;
        default:
          break;
      }
    });

    /* ══ 启动 ══ */
    try {
      root.classList.add('fp-enabled');
      locked = true;
      buildDots(true);
      index = 0;
      track.style.transform = 'translate3d(0,0,0)';
      syncDots();

      // 尺寸变化时修正位置
      let rt = 0;
      window.addEventListener('resize', () => {
        clearTimeout(rt);
        rt = setTimeout(() => {
          track.style.transition = 'none';
          track.style.transform = `translate3d(0, -${index * 100}%, 0)`;
          requestAnimationFrame(() => {
            track.style.transition = '';
          });
        }, 160);
      });

      // 减少动效开关变化：直接重载最稳妥
      reduceMQ.addEventListener('change', () => window.location.reload());
    } catch (err) {
      // 任何异常都退回普通滚动，绝不让页面卡死
      root.classList.remove('fp-enabled');
      locked = false;
      // eslint-disable-next-line no-console
      console.warn('[fullpage] 初始化失败，已退回普通滚动:', err);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
