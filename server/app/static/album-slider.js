/* ═══════════════════════════════════════════════════════════════
   轮播 + 信息卡交互 —— 复刻参考站 image.vue / information.vue 的脚本逻辑
   ─────────────────────────────────────────────────────────────────
   照搬的行为：
     1. 图片加载前显示骨架屏，load 后淡入（含 error 也淡入，避免永久骨架）
     2. 左右箭头切换，800ms 滑动
     3. 悬停容器才显示箭头
   我补的（他的源码没有）：
     4. 键盘左右键
     5. 触控滑动
     6. 自动播放（可关）
     7. 页面隐藏时暂停
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  if (window.__albumBooted) return;
  window.__albumBooted = true;

  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const html = document.documentElement;
  const motionMode = html.dataset.motion || 'immersive';
  const animOK = () => !reduceMQ.matches && motionMode !== 'still';

  /* ═══ 轮播 ═══ */
  function setupSlider() {
    const wrap = document.querySelector('[data-album]');
    if (!wrap) return;

    const inner = wrap.querySelector('.slider-inner');
    const slides = Array.from(wrap.querySelectorAll('.slide'));
    const prevBtn = wrap.querySelector('.prev');
    const nextBtn = wrap.querySelector('.next');
    if (!inner || slides.length < 1) return;

    let index = 0;
    let timer = null;
    const AUTOPLAY_MS = 6000;

    /* ★ 骨架屏 → 图片淡入（照搬他的 imageLoaded 逻辑） */
    slides.forEach((slide) => {
      const bg = slide.querySelector('.bg-img');
      const skel = slide.querySelector('.skeleton-img');
      if (!bg) return;

      const url = (bg.dataset.src || '').trim();
      if (!url) {
        // 没有图：直接隐藏骨架，避免永久闪烁
        if (skel) skel.classList.add('skeleton-hidden');
        return;
      }

      const img = new Image();
      const done = () => {
        bg.style.backgroundImage = `url("${url}")`;
        bg.classList.add('bg-visible');
        if (skel) skel.classList.add('skeleton-hidden');
      };
      img.onload = done;
      img.onerror = () => {
        // 加载失败也隐藏骨架，不留永久抖动
        if (skel) skel.classList.add('skeleton-hidden');
      };
      img.src = url;
    });

    function render() {
      inner.style.transform = `translateX(-${index * 100}%)`;
      slides.forEach((s, i) => {
        s.classList.toggle('active', i === index);
        s.setAttribute('aria-hidden', i === index ? 'false' : 'true');
      });
    }

    function go(i) {
      index = (i + slides.length) % slides.length;
      render();
      restart();
    }

    function restart() {
      if (timer) clearInterval(timer);
      if (!animOK() || slides.length < 2) return;
      timer = setInterval(() => {
        index = (index + 1) % slides.length;
        render();
      }, AUTOPLAY_MS);
    }

    if (prevBtn) prevBtn.addEventListener('click', () => go(index - 1));
    if (nextBtn) nextBtn.addEventListener('click', () => go(index + 1));

    /* 键盘 */
    wrap.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(index - 1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(index + 1); }
    });

    /* 触控 */
    let sx = 0, sy = 0, tracking = false;
    wrap.addEventListener('touchstart', (e) => {
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });
    wrap.addEventListener('touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const dx = e.changedTouches[0].clientX - sx;
      const dy = e.changedTouches[0].clientY - sy;
      if (Math.abs(dx) > 44 && Math.abs(dx) > Math.abs(dy)) {
        go(index + (dx < 0 ? 1 : -1));
      }
    }, { passive: true });

    /* 悬停暂停自动播放（照搬他的 onMouseEnter/Leave 意图） */
    wrap.addEventListener('mouseenter', () => { if (timer) clearInterval(timer); });
    wrap.addEventListener('mouseleave', restart);

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (timer) clearInterval(timer);
      } else {
        restart();
      }
    });

    render();
    restart();
  }

  /* ═══ 头像加载 ═══ */
  function setupAvatar() {
    const img = document.querySelector('.info-card .avatar');
    const skel = document.querySelector('.info-card .skeleton-avatar');
    if (!img) return;

    const done = () => {
      img.classList.add('avatar-visible');
      if (skel) skel.classList.add('skeleton-hidden');
    };

    if (img.complete && img.naturalWidth > 0) {
      done();
    } else {
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', done, { once: true });
    }
  }

  /* ═══ 入场动画（照搬 animate.css 的 fadeInLeft/Right/Up 时机）═══ */
  function setupEntrance() {
    const items = document.querySelectorAll('[data-anim]');
    if (!items.length) return;

    if (!animOK()) {
      items.forEach((el) => el.classList.add('is-in'));
      return;
    }

    items.forEach((el, i) => {
      el.style.opacity = '0';
      setTimeout(() => {
        el.classList.add('is-in');
        el.style.opacity = '';
      }, 120 + i * 110);
    });
  }

  function boot() {
    setupSlider();
    setupAvatar();
    setupEntrance();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
