/* ═══════════════════════════════════════════════════════════════
   个人数字工作室 · 浏览器新标签页 逻辑
   ─────────────────────────────────────────────────────────────────
   模块：
     1. 数据拉取（JSON API + 超时 + 降级）
     2. 天气场景引擎（Canvas 粒子系统，从站点移植）
     3. 音乐播放器
     4. 图片轮播
     5. 时钟与状态

   ★ 离线降级：拉取失败时页面仍完整可用，只显示「离线」状态。
   ★ 零外部依赖：所有代码本地，不引用任何 CDN。
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  const API = 'https://yuban211.duckdns.org:59684/studio/api/newtab.json';
  const SITE = 'https://yuban211.duckdns.org:59684/studio/';

  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const animOK = () => !reduceMQ.matches;

  const $ = (id) => document.getElementById(id);

  /* ═════════════════════════════════════════════════════════════
     1. 天气场景引擎
     ═════════════════════════════════════════════════════════════ */
  const PRESETS = {
    sun:    { sky: ['#f6e6c4', '#f9f0dc', '#f8f4e8'], cloud: 3, cloudA: 0.55, rain: 0,   snow: 0,  leaf: 2,  wind: 0.35, sun: true,  fog: 0,    flash: false },
    cloud:  { sky: ['#dcdcd4', '#ecece6', '#f6f4ec'], cloud: 6, cloudA: 0.7,  rain: 0,   snow: 0,  leaf: 3,  wind: 0.8,  sun: false, fog: 0.1,  flash: false },
    rain:   { sky: ['#ccd6dd', '#e3e9ee', '#f2f4f6'], cloud: 7, cloudA: 0.8,  rain: 90,  snow: 0,  leaf: 2,  wind: 1.1,  sun: false, fog: 0.18, flash: false },
    thunder:{ sky: ['#3a4048', '#4a525c', '#5c6670'], cloud: 8, cloudA: 0.9,  rain: 130, snow: 0,  leaf: 3,  wind: 2.0,  sun: false, fog: 0.25, flash: true  },
    snow:   { sky: ['#d8dee4', '#e8edf1', '#f4f7f9'], cloud: 5, cloudA: 0.6,  rain: 0,   snow: 55, leaf: 0,  wind: 0.5,  sun: false, fog: 0.22, flash: false },
    wind:   { sky: ['#e0e4dc', '#eef1e8', '#f6f7f2'], cloud: 4, cloudA: 0.5,  rain: 0,   snow: 0,  leaf: 26, wind: 2.8,  sun: false, fog: 0.06, flash: false },
    fog:    { sky: ['#cfd4d0', '#dee2de', '#eceeec'], cloud: 4, cloudA: 0.45, rain: 0,   snow: 0,  leaf: 0,  wind: 0.2,  sun: false, fog: 0.55, flash: false },
  };

  class WeatherScene {
    constructor(canvas) {
      this.cv = canvas;
      this.ctx = canvas.getContext('2d', { alpha: true });
      if (!this.ctx) return;
      this.w = 0; this.h = 0; this.dpr = 1;
      this.raf = 0; this.running = false; this.hidden = false;
      this.t = 0; this.last = 0;
      this.type = 'cloud';
      this.p = PRESETS.cloud;
      this.rain = []; this.snow = []; this.leaf = []; this.clouds = []; this.splash = [];
      this.flash = 0;
      this.sun = { x: 0.78, y: 0.22 };
      this.sunTo = { x: 0.78, y: 0.22 };
    }

    resize() {
      const r = this.cv.getBoundingClientRect();
      this.w = Math.max(1, Math.floor(r.width || 300));
      this.h = Math.max(1, Math.floor(r.height || 150));
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.cv.width = Math.floor(this.w * this.dpr);
      this.cv.height = Math.floor(this.h * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.seed();
    }

    count(base, cap) {
      const area = this.w * this.h;
      const s = Math.min(1.6, Math.max(0.35, area / 22000));
      return Math.min(cap, Math.round(base * s));
    }

    seed() {
      const p = this.p;
      this.clouds = [];
      const cn = this.count(p.cloud, 12);
      for (let i = 0; i < cn; i++) {
        this.clouds.push({
          x: Math.random() * (this.w + 200) - 100,
          y: 8 + Math.random() * this.h * 0.42,
          r: 26 + Math.random() * 44,
          speed: 4 + Math.random() * 10,
          layer: i % 3,
          alpha: p.cloudA * (0.5 + (i % 3) * 0.22),
        });
      }
      this.rain = [];
      const rn = this.count(p.rain, 200);
      for (let i = 0; i < rn; i++) this.rain.push(this.drop(true));
      this.snow = [];
      const sn = this.count(p.snow, 110);
      for (let i = 0; i < sn; i++) this.snow.push(this.flake(true));
      this.leaf = [];
      const ln = this.count(p.leaf, 40);
      for (let i = 0; i < ln; i++) this.leaf.push(this.leafP(true));
      this.splash = [];
    }

    drop(init) {
      const layer = Math.floor(Math.random() * 3);
      return {
        x: Math.random() * (this.w + 60) - 30,
        y: init ? Math.random() * this.h : -19,
        len: [7, 12, 19][layer],
        thick: [0.7, 1.1, 1.7][layer],
        speed: [220, 340, 470][layer] * (0.85 + Math.random() * 0.3),
        layer,
        alpha: [0.3, 0.5, 0.72][layer],
      };
    }
    flake(init) {
      const s = 0.4 + Math.random() * 0.6;
      return {
        x: Math.random() * this.w,
        y: init ? Math.random() * this.h : -10,
        r: 1.2 + s * 2.6,
        fall: 16 + (1 - s) * 26,
        amp: 12 + Math.random() * 26,
        freq: 0.6 + Math.random() * 1.1,
        phase: Math.random() * Math.PI * 2,
        alpha: 0.32 + s * 0.5,
      };
    }
    leafP(init) {
      return {
        x: Math.random() * this.w,
        y: init ? Math.random() * this.h : -12,
        size: 3.4 + Math.random() * 4.6,
        rot: Math.random() * Math.PI * 2,
        rotSpeed: (Math.random() - 0.5) * 2.6,
        fall: 28 + Math.random() * 46,
        amp: 16 + Math.random() * 40,
        freq: 0.5 + Math.random() * 1.2,
        phase: Math.random() * Math.PI * 2,
        hue: [28, 34, 42, 88, 96][Math.floor(Math.random() * 5)],
        alpha: 0.42 + Math.random() * 0.4,
      };
    }

    setType(t) {
      if (!PRESETS[t]) t = 'cloud';
      this.type = t;
      this.p = PRESETS[t];
      this.seed();
      this.sunTo = this.p.sun ? { x: 0.78, y: 0.22 } : { x: 0.86, y: 0.16 };
      if (!animOK()) this.drawOnce();
    }

    start() {
      if (this.running) return;
      this.running = true;
      this.last = 0;
      this.raf = requestAnimationFrame(this.frame.bind(this));
    }
    stop() {
      this.running = false;
      cancelAnimationFrame(this.raf);
    }

    frame(ts) {
      if (!this.running) return;
      this.raf = requestAnimationFrame(this.frame.bind(this));
      if (this.hidden) return;
      const dt = this.last ? Math.min(0.05, (ts - this.last) / 1000) : 0.016;
      this.last = ts;
      this.t += dt;
      this.update(dt);
      this.render();
    }

    update(dt) {
      const p = this.p;
      this.sun.x += (this.sunTo.x - this.sun.x) * Math.min(1, dt * 2.2);
      this.sun.y += (this.sunTo.y - this.sun.y) * Math.min(1, dt * 2.2);

      for (const c of this.clouds) {
        c.x += (c.speed * 0.35 + p.wind * 16) * dt * (1 - c.layer * 0.18);
        if (c.x - c.r > this.w + 120) {
          c.x = -c.r - 60;
          c.y = 8 + Math.random() * this.h * 0.42;
        }
      }
      for (let i = 0; i < this.rain.length; i++) {
        const d = this.rain[i];
        d.y += d.speed * dt;
        d.x += p.wind * 30 * dt;
        if (d.y > this.h + d.len) {
          if (d.layer === 2 && this.splash.length < 26) {
            this.splash.push({ x: d.x, y: this.h - 2, r: 1, life: 0.32, max: 0.32 });
          }
          this.rain[i] = this.drop(false);
        }
      }
      for (let i = 0; i < this.snow.length; i++) {
        const s = this.snow[i];
        s.y += s.fall * dt;
        s.phase += s.freq * dt * 2.4;
        s.x += Math.cos(s.phase) * s.amp * dt * 2.2 + p.wind * 22 * dt;
        if (s.y > this.h + 8) this.snow[i] = this.flake(false);
        if (s.x < -12) s.x = this.w + 12;
        if (s.x > this.w + 12) s.x = -12;
      }
      for (let i = 0; i < this.leaf.length; i++) {
        const l = this.leaf[i];
        l.y += l.fall * dt;
        l.phase += l.freq * dt * 3;
        l.x += Math.sin(l.phase) * l.amp * dt * 2 + p.wind * 40 * dt;
        l.rot += l.rotSpeed * dt;
        if (l.y > this.h + 14) this.leaf[i] = this.leafP(false);
        if (l.x < -20) l.x = this.w + 20;
        if (l.x > this.w + 20) l.x = -20;
      }
      for (let i = this.splash.length - 1; i >= 0; i--) {
        const s = this.splash[i];
        s.life -= dt;
        s.r += 26 * dt;
        if (s.life <= 0) this.splash.splice(i, 1);
      }
      if (p.flash) {
        if (this.flash > 0.01) this.flash *= 0.86;
        else if (Math.random() < dt * 0.55) this.flash = 0.5 + Math.random() * 0.35;
      } else {
        this.flash = 0;
      }
    }

    render() {
      const ctx = this.ctx;
      const w = this.w, h = this.h, p = this.p;
      ctx.clearRect(0, 0, w, h);

      const g = ctx.createLinearGradient(0, 0, w * 0.35, h);
      g.addColorStop(0, p.sky[0]);
      g.addColorStop(0.55, p.sky[1]);
      g.addColorStop(1, p.sky[2]);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      if (p.sun) {
        const cx = this.sun.x * w, cy = this.sun.y * h;
        const R = Math.max(w, h) * 0.42;
        const sg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
        sg.addColorStop(0, 'rgba(255,226,150,0.85)');
        sg.addColorStop(0.35, 'rgba(255,232,176,0.32)');
        sg.addColorStop(1, 'rgba(255,240,200,0)');
        ctx.fillStyle = sg;
        ctx.beginPath();
        ctx.arc(cx, cy, R, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = 'rgba(255,244,214,0.95)';
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(10, w * 0.055), 0, Math.PI * 2);
        ctx.fill();
      }

      for (const c of this.clouds) this.cloud(c);

      if (this.rain.length) {
        ctx.lineCap = 'round';
        for (const d of this.rain) {
          ctx.strokeStyle = `rgba(58,96,160,${d.alpha})`;
          ctx.lineWidth = d.thick;
          ctx.beginPath();
          ctx.moveTo(d.x, d.y);
          ctx.lineTo(d.x - p.wind * 2.2, d.y + d.len);
          ctx.stroke();
        }
      }
      for (const s of this.splash) {
        const a = (s.life / s.max) * 0.5;
        ctx.strokeStyle = `rgba(120,160,210,${a})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.ellipse(s.x, s.y, s.r, s.r * 0.38, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
      for (const s of this.snow) {
        ctx.fillStyle = `rgba(255,255,255,${s.alpha})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fill();
      }
      for (const l of this.leaf) {
        ctx.save();
        ctx.translate(l.x, l.y);
        ctx.rotate(l.rot);
        ctx.fillStyle = `hsla(${l.hue},46%,46%,${l.alpha})`;
        ctx.beginPath();
        ctx.ellipse(0, 0, l.size, l.size * 0.48, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
      if (p.fog > 0) {
        const fg = ctx.createLinearGradient(0, h * 0.3, 0, h);
        fg.addColorStop(0, 'rgba(238,240,238,0)');
        fg.addColorStop(1, `rgba(238,240,238,${p.fog})`);
        ctx.fillStyle = fg;
        ctx.fillRect(0, 0, w, h);
      }
      if (this.flash > 0.01) {
        ctx.fillStyle = `rgba(255,255,255,${this.flash})`;
        ctx.fillRect(0, 0, w, h);
      }
    }

    cloud(c) {
      const ctx = this.ctx;
      const r = c.r;
      ctx.save();
      ctx.globalAlpha = c.alpha;
      const parts = [
        { dx: -r * 0.72, dy: r * 0.14, rr: r * 0.62 },
        { dx: 0, dy: 0, rr: r },
        { dx: r * 0.78, dy: r * 0.1, rr: r * 0.7 },
        { dx: r * 0.3, dy: r * 0.34, rr: r * 0.56 },
        { dx: -r * 0.36, dy: r * 0.36, rr: r * 0.52 },
      ];
      ctx.fillStyle = 'rgba(255,255,255,0.92)';
      for (const part of parts) {
        ctx.beginPath();
        ctx.arc(c.x + part.dx, c.y + part.dy, part.rr, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }

    drawOnce() {
      this.render();
    }
  }

  /* ═════════════════════════════════════════════════════════════
     2. 数据拉取
     ═════════════════════════════════════════════════════════════ */
  function fetchData() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 8000);
    return fetch(API, { signal: ctrl.signal, cache: 'no-store' })
      .then((r) => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .finally(() => clearTimeout(timer));
  }

  function setStatus(state, text) {
    const dot = $('statusDot');
    const t = $('statusText');
    if (!dot || !t) return;
    dot.classList.remove('is-online', 'is-offline');
    if (state === 'online') dot.classList.add('is-online');
    else if (state === 'offline') dot.classList.add('is-offline');
    t.textContent = text;
  }

  /* ═════════════════════════════════════════════════════════════
     3. 渲染各模块
     ═════════════════════════════════════════════════════════════ */
  let scene = null;

  function renderWeather(w) {
    const card = $('wxCard');
    if (!card) return;
    if (!w) {
      $('weatherCity').textContent = '离线';
      $('wxTemp').innerHTML = '--<span>°C</span>';
      $('wxDesc').textContent = '无法连接服务';
      $('wxMeta').textContent = '';
      return;
    }
    $('weatherCity').textContent = w.city || '--';
    $('wxPlace').textContent = w.city || '--';
    $('wxTemp').innerHTML = `${Math.round(w.temperature ?? 0)}<span>°C</span>`;
    $('wxDesc').textContent =
      (w.condition || '') + (w.precipProbability != null ? ` · 降水 ${Math.round(w.precipProbability)}%` : '');
    const meta = [];
    if (w.humidity != null) meta.push(`湿度 <b>${Math.round(w.humidity)}%</b>`);
    if (w.observedAt) meta.push(`观测 <b>${w.observedAt.slice(11, 16)}</b>`);
    $('wxMeta').innerHTML = meta.join('');
    $('wxDemo').hidden = !w.isDemo;
    card.dataset.dark = w.theme === 'thunder' ? '1' : '0';

    if (scene) {
      scene.setType(w.theme || 'cloud');
      if (!animOK()) scene.drawOnce();
      else scene.start();
    }
    // 同步按钮状态
    document.querySelectorAll('.wx-swatch').forEach((b) => {
      b.setAttribute('aria-pressed', b.dataset.wx === w.theme ? 'true' : 'false');
    });
  }

  function renderProjects(list) {
    const box = $('projList');
    $('projCount').textContent = String((list || []).length).padStart(2, '0');
    if (!list || !list.length) {
      box.innerHTML = '<p class="empty">还没有项目<br><small>NO PROJECTS YET</small></p>';
      return;
    }
    box.innerHTML = list
      .map(
        (p, i) => `
      <a class="row" href="${SITE}projects/${encodeURIComponent(p.slug)}" target="_blank" rel="noopener">
        <span class="row-idx">${String(i + 1).padStart(2, '0')}</span>
        <span class="row-main">
          <span class="row-title">${esc(p.title)}</span>
          <span class="row-sub">${esc(p.summary)}</span>
        </span>
        <span class="row-tag">${esc(p.status)}</span>
        <span class="row-arrow">↗</span>
      </a>`
      )
      .join('');
  }

  function renderPosts(list) {
    const box = $('noteList');
    $('noteCount').textContent = String((list || []).length).padStart(2, '0');
    if (!list || !list.length) {
      box.innerHTML = '<p class="empty">第一篇记录，留给未来的某一天<br><small>NO POSTS YET</small></p>';
      return;
    }
    box.innerHTML = list
      .map(
        (p, i) => `
      <a class="row" href="${SITE}blog/${encodeURIComponent(p.slug)}" target="_blank" rel="noopener">
        <span class="row-idx">${String(i + 1).padStart(2, '0')}</span>
        <span class="row-main">
          <span class="row-title">${esc(p.title)}</span>
          <span class="row-sub">${esc(p.summary)}</span>
        </span>
        <span class="row-tag">${esc(p.publishedAt)}</span>
        <span class="row-arrow">↗</span>
      </a>`
      )
      .join('');
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ── 图片轮播 ── */
  let sliderIdx = 0;
  let sliderTimer = null;
  const shots = [];

  function renderShots(list) {
    const track = $('sliderTrack');
    const dotsBox = $('sliderDots');
    shots.length = 0;
    (list || []).forEach((p) => shots.push(p));

    $('galleryCount').textContent = shots.length
      ? `${String(shots.length).padStart(2, '0')} IMAGES`
      : 'EMPTY';

    if (!shots.length) {
      track.innerHTML =
        '<div class="slide slide--empty"><span>NO IMAGES YET<br>上传图片到媒体库后自动出现</span></div>';
      dotsBox.innerHTML = '';
      return;
    }
    track.innerHTML = shots
      .map(
        (src, i) => `
      <div class="slide">
        <img src="${esc(src)}" alt="作品截图 ${i + 1}" loading="${i === 0 ? 'eager' : 'lazy'}">
      </div>`
      )
      .join('');
    dotsBox.innerHTML = shots
      .map(
        (_, i) =>
          `<button class="s-dot${i === 0 ? ' is-active' : ''}" type="button" aria-label="第 ${i + 1} 张"></button>`
      )
      .join('');
    dotsBox.querySelectorAll('.s-dot').forEach((d, i) => {
      d.addEventListener('click', () => {
        sliderIdx = i;
        paintSlider();
        restartSlider();
      });
    });
    sliderIdx = 0;
    paintSlider();
    restartSlider();
  }

  function paintSlider() {
    const track = $('sliderTrack');
    if (!track) return;
    track.style.transform = `translateX(-${sliderIdx * 100}%)`;
    document.querySelectorAll('.s-dot').forEach((d, i) => d.classList.toggle('is-active', i === sliderIdx));
  }

  function restartSlider() {
    if (sliderTimer) clearInterval(sliderTimer);
    if (!animOK() || shots.length < 2) return;
    sliderTimer = setInterval(() => {
      sliderIdx = (sliderIdx + 1) % shots.length;
      paintSlider();
    }, 5600);
  }

  /* ═════════════════════════════════════════════════════════════
     4. 音乐播放器
     ═════════════════════════════════════════════════════════════ */
  function setupPlayer() {
    const audio = $('plAudio');
    const disc = $('plDisc');
    const listBox = $('plList');
    const playBtn = document.querySelector('[data-act="play"]');
    if (!audio || !playBtn) return;

    const fmt = (s) => {
      if (!isFinite(s) || s < 0) return '0:00';
      const m = Math.floor(s / 60);
      return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
    };
    const setUI = (playing) => {
      disc.classList.toggle('is-playing', playing);
      playBtn.setAttribute('aria-pressed', String(playing));
      playBtn.setAttribute('aria-label', playing ? '暂停' : '播放');
      playBtn.textContent = playing ? '❚❚' : '▶';
    };

    playBtn.addEventListener('click', () => {
      if (audio.paused) audio.play().catch(() => setUI(false));
      else audio.pause();
    });
    audio.addEventListener('play', () => setUI(true));
    audio.addEventListener('pause', () => setUI(false));
    audio.addEventListener('timeupdate', () => {
      const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
      $('plFill').style.width = pct + '%';
      $('plCur').textContent = fmt(audio.currentTime);
    });
    audio.addEventListener('loadedmetadata', () => {
      $('plDur').textContent = fmt(audio.duration);
    });
    $('plBar').addEventListener('click', (e) => {
      if (!audio.duration) return;
      const r = e.currentTarget.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      audio.currentTime = ratio * audio.duration;
    });
    listBox.addEventListener('click', (e) => {
      const item = e.target.closest('.pl-item');
      if (!item) return;
      const src = item.dataset.src;
      if (!src) return;
      audio.src = src;
      $('plSong').textContent = item.dataset.title || '';
      $('plArtist').textContent = item.dataset.artist || '';
      audio.play().catch(() => setUI(false));
      listBox.querySelectorAll('.pl-item').forEach((x) => x.classList.toggle('is-active', x === item));
    });
  }

  /* ═════════════════════════════════════════════════════════════
     5. 时钟
     ═════════════════════════════════════════════════════════════ */
  function setupClock() {
    const el = $('clock');
    if (!el) return;
    const tick = () => {
      const d = new Date();
      const p = (n) => String(n).padStart(2, '0');
      el.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ═════════════════════════════════════════════════════════════
     6. 启动
     ═════════════════════════════════════════════════════════════ */
  function boot() {
    // 天气场景
    const canvas = $('wxCanvas');
    if (canvas) {
      scene = new WeatherScene(canvas);
      scene.resize();
      if (animOK()) scene.start();
      else scene.drawOnce();

      let rt = 0;
      window.addEventListener('resize', () => {
        clearTimeout(rt);
        rt = setTimeout(() => {
          scene.resize();
          if (!animOK()) scene.drawOnce();
        }, 180);
      });
      document.addEventListener('visibilitychange', () => {
        scene.hidden = document.hidden;
        if (document.hidden) scene.stop();
        else if (animOK()) scene.start();
      });

      document.querySelectorAll('.wx-swatch').forEach((b) => {
        b.addEventListener('click', () => {
          if (!scene) return;
          scene.setType(b.dataset.wx);
          if (!animOK()) scene.drawOnce();
          else scene.start();
          $('wxCard').dataset.dark = b.dataset.wx === 'thunder' ? '1' : '0';
          document.querySelectorAll('.wx-swatch').forEach((x) => {
            x.setAttribute('aria-pressed', x === b ? 'true' : 'false');
          });
        });
      });
    }

    setupClock();
    setupPlayer();

    // 轮播导航
    const prev = document.querySelector('.s-prev');
    const next = document.querySelector('.s-next');
    if (prev) prev.addEventListener('click', () => { sliderIdx = (sliderIdx - 1 + shots.length) % shots.length; paintSlider(); restartSlider(); });
    if (next) next.addEventListener('click', () => { sliderIdx = (sliderIdx + 1) % shots.length; paintSlider(); restartSlider(); });

    // 拉数据
    setStatus('', '连接中');
    fetchData()
      .then((d) => {
        if (!d || !d.ok) throw new Error('bad payload');
        setStatus('online', '已连接');
        renderWeather(d.weather);
        renderProjects(d.projects);
        renderPosts(d.posts);
        renderShots(d.shots);
      })
      .catch((err) => {
        console.warn('[studio-newtab] 数据拉取失败:', err);
        setStatus('offline', '离线');
        renderWeather(null);
        renderProjects([]);
        renderPosts([]);
        renderShots([]);
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
