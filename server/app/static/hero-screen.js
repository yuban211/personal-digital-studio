/* ═══════════════════════════════════════════════════════════════
   Hero 雪点画布 + 页面交互
   ─────────────────────────────────────────────────────────────────
   移植自参考站 text.vue 的 canvas 逻辑，做了三点改进：
     1. 粒子数量按面积算，有硬上限（参考站是固定值）
     2. 页面隐藏时暂停 rAF
     3. 减少动效时只画静态一帧
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const html = document.documentElement;
  const motionMode = html.dataset.motion || 'immersive';
  const animOK = () => !reduceMQ.matches && motionMode !== 'still';

  /* ═══ Hero 雪点 ═══ */
  function setupHeroSnow() {
    const canvas = document.querySelector('.hero-snow');
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let W = 0;
    let H = 0;
    let dpr = 1;
    let flakes = [];
    let raf = 0;
    let running = true;
    let hidden = false;

    function resize() {
      const rect = canvas.getBoundingClientRect();
      W = Math.max(1, Math.floor(rect.width || window.innerWidth));
      H = Math.max(1, Math.floor(rect.height || window.innerHeight));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(W * dpr);
      canvas.height = Math.floor(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // 数量按面积，上限 110
      const n = Math.min(110, Math.max(32, Math.round((W * H) / 18000)));
      flakes = [];
      for (let i = 0; i < n; i++) {
        flakes.push(newFlake(true));
      }
    }

    function newFlake(init) {
      return {
        x: Math.random() * W,
        y: init ? Math.random() * H : -6,
        r: 0.6 + Math.random() * 2.1,
        fall: 6 + Math.random() * 22,
        drift: (Math.random() - 0.5) * 12,
        phase: Math.random() * Math.PI * 2,
        wobble: 0.5 + Math.random() * 1.2,
        alpha: 0.18 + Math.random() * 0.5,
      };
    }

    let last = 0;
    function frame(ts) {
      if (!running) return;
      raf = requestAnimationFrame(frame);
      if (hidden) return;

      const dt = last ? Math.min(0.05, (ts - last) / 1000) : 0.016;
      last = ts;

      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < flakes.length; i++) {
        const f = flakes[i];
        f.y += f.fall * dt;
        f.phase += f.wobble * dt * 2;
        f.x += Math.sin(f.phase) * f.drift * dt + f.drift * dt * 0.5;
        if (f.y > H + 6) {
          flakes[i] = newFlake(false);
          continue;
        }
        if (f.x < -6) f.x = W + 6;
        if (f.x > W + 6) f.x = -6;

        ctx.fillStyle = `rgba(255, 255, 255, ${f.alpha})`;
        ctx.beginPath();
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    resize();

    if (animOK()) {
      raf = requestAnimationFrame(frame);
    } else {
      // 减少动效：画一帧静态，之后不再更新
      for (const f of flakes) {
        ctx.fillStyle = `rgba(255, 255, 255, ${f.alpha})`;
        ctx.beginPath();
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    let rt = 0;
    window.addEventListener('resize', () => {
      clearTimeout(rt);
      rt = setTimeout(() => {
        resize();
        if (!animOK()) {
          for (const f of flakes) {
            ctx.fillStyle = `rgba(255, 255, 255, ${f.alpha})`;
            ctx.beginPath();
            ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }, 180);
    });

    document.addEventListener('visibilitychange', () => {
      hidden = document.hidden;
      if (!document.hidden && animOK() && running) {
        last = 0;
      }
    });

    window.addEventListener('pagehide', () => {
      running = false;
      cancelAnimationFrame(raf);
    });
  }

  /* ═══ 平滑滚动到下一屏 ═══ */
  function setupScrollHint() {
    const link = document.querySelector('.hero-scroll');
    if (!link) return;
    link.addEventListener('click', (e) => {
      const target = document.querySelector(link.getAttribute('href') || '#main-modules');
      if (target) {
        e.preventDefault();
        target.scrollIntoView({
          behavior: animOK() ? 'smooth' : 'auto',
          block: 'start',
        });
      }
    });
  }

  /* ═══ 滚动时为顶栏加背景 ═══ */
  function setupScrollState() {
    const bar = document.querySelector('.site-header');
    if (!bar) return;
    let ticking = false;
    const update = () => {
      bar.classList.toggle('is-scrolled', window.scrollY > 40);
      ticking = false;
    };
    window.addEventListener(
      'scroll',
      () => {
        if (!ticking) {
          ticking = true;
          requestAnimationFrame(update);
        }
      },
      { passive: true }
    );
    update();
  }

  function boot() {
    setupHeroSnow();
    setupScrollHint();
    setupScrollState();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
