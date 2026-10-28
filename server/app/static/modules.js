/* ═══════════════════════════════════════════════════════════════
   首页组件模块行为
   ─────────────────────────────────────────────────────────────────
   三个模块，全部无第三方依赖：
     1. 音乐播放器 —— 用 <audio> 元素，真实的播放/暂停/进度/切歌
     2. 图片轮播 —— 自动播放 + 手势 + 键盘 + 指示点
     3. 天气卡 —— 数据由服务端渲染，这里只做主题切换与刷新

   ★ 共同原则：
     - 单个初始化保护（防重复注册）
     - pagehide 时清理
     - 减少动效时降低或禁用自动行为
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const html = document.documentElement;
  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const motionMode = html.dataset.motion || 'immersive';
  const animOK = () => !reduceMQ.matches && motionMode !== 'still';

  if (window.__studioModulesBooted) return;
  window.__studioModulesBooted = true;

  const cleanups = [];
  const on = (el, type, fn, opts) => {
    el.addEventListener(type, fn, opts);
    cleanups.push(() => el.removeEventListener(type, fn, opts));
  };

  /* ═════════════════════════════════════════════════════════════
     1) 音乐播放器
     ═════════════════════════════════════════════════════════════ */
  function setupPlayer() {
    const root = document.querySelector('.player');
    if (!root) return;

    const audio = root.querySelector('audio');
    const disc = root.querySelector('.player-disc');
    const btnPlay = root.querySelector('[data-act="play"]');
    const btnPrev = root.querySelector('[data-act="prev"]');
    const btnNext = root.querySelector('[data-act="next"]');
    const bar = root.querySelector('.player-bar');
    const barFill = bar ? bar.querySelector('i') : null;
    const tCur = root.querySelector('.player-time .t-cur');
    const tDur = root.querySelector('.player-time .t-dur');
    const songEl = root.querySelector('.player-song');
    const artistEl = root.querySelector('.player-artist');
    const items = Array.from(root.querySelectorAll('.player-item'));

    if (!audio || !btnPlay) return;

    let index = 0;
    let tracks = [];

    try {
      tracks = JSON.parse(root.dataset.tracks || '[]');
    } catch (e) {
      tracks = [];
    }
    if (!tracks.length) return;

    const fmt = (s) => {
      if (!isFinite(s) || s < 0) return '0:00';
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return `${m}:${String(sec).padStart(2, '0')}`;
    };

    function load(i, autoplay) {
      index = (i + tracks.length) % tracks.length;
      const t = tracks[index];
      audio.src = t.src;
      if (songEl) songEl.textContent = t.title || '未命名';
      if (artistEl) artistEl.textContent = t.artist || '';
      items.forEach((el, k) => el.classList.toggle('is-active', k === index));
      if (autoplay) {
        audio.play().catch(() => {
          // 自动播放被浏览器拦截：保持暂停态，不报错打断用户
          setUI(false);
        });
      }
    }

    function setUI(playing) {
      root.classList.toggle('is-playing', playing);
      if (btnPlay) {
        btnPlay.setAttribute('aria-pressed', String(playing));
        btnPlay.setAttribute('aria-label', playing ? '暂停' : '播放');
      }
    }

    on(btnPlay, 'click', () => {
      if (audio.paused) {
        audio.play().catch(() => setUI(false));
      } else {
        audio.pause();
      }
    });
    on(audio, 'play', () => setUI(true));
    on(audio, 'pause', () => setUI(false));
    on(audio, 'ended', () => load(index + 1, true));

    if (btnPrev) on(btnPrev, 'click', () => load(index - 1, !audio.paused));
    if (btnNext) on(btnNext, 'click', () => load(index + 1, !audio.paused));

    on(audio, 'timeupdate', () => {
      if (!barFill) return;
      const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      barFill.style.width = `${pct}%`;
      if (tCur) tCur.textContent = fmt(audio.currentTime);
    });
    on(audio, 'loadedmetadata', () => {
      if (tDur) tDur.textContent = fmt(audio.duration);
    });

    if (bar) {
      on(bar, 'click', (e) => {
        if (!audio.duration) return;
        const r = bar.getBoundingClientRect();
        const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        audio.currentTime = ratio * audio.duration;
      });
    }

    items.forEach((el, k) => {
      on(el, 'click', () => load(k, true));
    });

    // 初始化：停在第一首，不自动播放（浏览器策略 + 尊重用户）
    load(0, false);
    if (disc && !animOK()) {
      // 减少动效：唱片不转（CSS 已处理，这里只是语义确认）
      disc.style.animation = 'none';
    }
  }

  /* ═════════════════════════════════════════════════════════════
     2) 图片轮播
     ═════════════════════════════════════════════════════════════ */
  function setupSlider() {
    const root = document.querySelector('.slider');
    if (!root) return;

    const track = root.querySelector('.slider-track');
    const slides = Array.from(root.querySelectorAll('.slider-slide'));
    const dotsWrap = root.querySelector('.slider-dots');
    const btnPrev = root.querySelector('.slider-nav--prev');
    const btnNext = root.querySelector('.slider-nav--next');
    if (!track || slides.length < 2) return;

    let index = 0;
    let timer = null;
    const AUTOPLAY_MS = 5200;

    // 生成指示点
    let dots = [];
    if (dotsWrap) {
      dotsWrap.innerHTML = '';
      slides.forEach((_, k) => {
        const b = document.createElement('button');
        b.className = 'slider-dot';
        b.type = 'button';
        b.setAttribute('aria-label', `第 ${k + 1} 张`);
        b.addEventListener('click', () => go(k, true));
        dotsWrap.appendChild(b);
        dots.push(b);
      });
    }
    dots = Array.from(root.querySelectorAll('.slider-dot'));

    function render() {
      track.style.transform = `translateX(-${index * 100}%)`;
      dots.forEach((d, k) => {
        d.classList.toggle('is-active', k === index);
        d.setAttribute('aria-current', k === index ? 'true' : 'false');
      });
      slides.forEach((s, k) => {
        s.setAttribute('aria-hidden', k === index ? 'false' : 'true');
      });
    }

    function go(i, userAction) {
      index = (i + slides.length) % slides.length;
      render();
      if (userAction) restart();
    }

    function restart() {
      if (timer) clearInterval(timer);
      // 减少动效时不做自动播放（避免不可预期的位移）
      if (!animOK()) return;
      timer = setInterval(() => go(index + 1, false), AUTOPLAY_MS);
      cleanups.push(() => clearInterval(timer));
    }

    if (btnPrev) on(btnPrev, 'click', () => go(index - 1, true));
    if (btnNext) on(btnNext, 'click', () => go(index + 1, true));

    // 键盘：左右方向键
    on(root, 'keydown', (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(index - 1, true); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(index + 1, true); }
    });

    // 触控滑动
    let sx = 0, sy = 0, tracking = false;
    on(root, 'touchstart', (e) => {
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });
    on(root, 'touchend', (e) => {
      if (!tracking) return;
      tracking = false;
      const dx = e.changedTouches[0].clientX - sx;
      const dy = e.changedTouches[0].clientY - sy;
      // 横向位移大于纵向且超过阈值才算翻页，避免干扰页面滚动
      if (Math.abs(dx) > 44 && Math.abs(dx) > Math.abs(dy)) {
        go(index + (dx < 0 ? 1 : -1), true);
      }
    }, { passive: true });

    // 鼠标移入暂停
    on(root, 'mouseenter', () => { if (timer) clearInterval(timer); });
    on(root, 'mouseleave', restart);

    render();
    restart();
  }

  /* ═════════════════════════════════════════════════════════════
     3) 天气卡主题
     ═════════════════════════════════════════════════════════════ */
  function setupWeather() {
    const card = document.querySelector('.wcard');
    if (!card) return;
    // 主题由服务端 data-type 决定，这里只需要在数据刷新时更新
    // （当前为服务端渲染，静态即可；保留钩子便于以后接实时刷新）
  }

  /* ═════════════════════════════════════════════════════════════
     启动与清理
     ═════════════════════════════════════════════════════════════ */
  function boot() {
    setupWeather();
    setupPlayer();
    setupSlider();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  function cleanup() {
    cleanups.forEach((fn) => {
      try { fn(); } catch (e) { /* 忽略 */ }
    });
    cleanups.length = 0;
  }
  window.addEventListener('pagehide', cleanup);
  window.addEventListener('beforeunload', cleanup);
})();
