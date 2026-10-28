/* ═══════════════════════════════════════════════════════════════
   天气场景引擎（Weather Scene Engine）
   ─────────────────────────────────────────────────────────────────
   参考站的做法（用手法，不搬代码）：
     - 雨按线宽分 3 层，做出景深
     - 雪 = 主下落 + 横向摇摆（双动画叠加）
     - 风力按层递减 windSpeed / (i + 1)
     - 粒子池按需补充，不一次性创建
     - 7 种天气类型：sun / cloud / rain / thunder / snow / wind / fog

   本实现的差异：
     ★ 用原生 Canvas 2D 单画布，不引入 Snap.svg（它多年未更新，
       且每个粒子一个 DOM/SVG 节点在 60fps 下开销很高）
     ★ 单画布 = 一次清屏 + 一次性绘制，帧率更稳
     ★ 粒子对象复用（不在帧循环里 new），减少 GC 抖动
     ★ 不用 GSAP：粒子用简单运动学积分，太阳/月亮用缓动插值

   性能纪律：
     - 粒子数按画布面积缩放，硬上限
     - 页面隐藏暂停 rAF
     - 减少动效：只画静态一帧（云和不动的粒子）
     - DPR 上限 2，避免 4K 屏爆内存
   ═══════════════════════════════════════════════════════════════ */

/* ── 天气类型定义 ── */
const WEATHER_PRESETS = {
  sun: {
    sky: ['#f6e6c4', '#f9f0dc', '#f8f4e8'],
    cloud: 3,
    cloudAlpha: 0.55,
    rain: 0,
    snow: 0,
    leaf: 2,
    wind: 0.35,
    sun: true,
    fog: 0,
    flash: false,
  },
  cloud: {
    sky: ['#dcdcd4', '#ecece6', '#f6f4ec'],
    cloud: 6,
    cloudAlpha: 0.7,
    rain: 0,
    snow: 0,
    leaf: 3,
    wind: 0.8,
    sun: false,
    fog: 0.1,
    flash: false,
  },
  rain: {
    sky: ['#ccd6dd', '#e3e9ee', '#f2f4f6'],
    cloud: 7,
    cloudAlpha: 0.8,
    rain: 90,
    snow: 0,
    leaf: 2,
    wind: 1.1,
    sun: false,
    fog: 0.18,
    flash: false,
  },
  thunder: {
    sky: ['#3a4048', '#4a525c', '#5c6670'],
    cloud: 8,
    cloudAlpha: 0.9,
    rain: 130,
    snow: 0,
    leaf: 3,
    wind: 2.0,
    sun: false,
    fog: 0.25,
    flash: true,
  },
  snow: {
    sky: ['#d8dee4', '#e8edf1', '#f4f7f9'],
    cloud: 5,
    cloudAlpha: 0.6,
    rain: 0,
    snow: 55,
    leaf: 0,
    wind: 0.5,
    sun: false,
    fog: 0.22,
    flash: false,
  },
  wind: {
    sky: ['#e0e4dc', '#eef1e8', '#f6f7f2'],
    cloud: 4,
    cloudAlpha: 0.5,
    rain: 0,
    snow: 0,
    leaf: 26,
    wind: 2.8,
    sun: false,
    fog: 0.06,
    flash: false,
  },
  fog: {
    sky: ['#cfd4d0', '#dee2de', '#eceeec'],
    cloud: 4,
    cloudAlpha: 0.45,
    rain: 0,
    snow: 0,
    leaf: 0,
    wind: 0.2,
    sun: false,
    fog: 0.55,
    flash: false,
  },
};

class WeatherScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: true });
    if (!this.ctx) return;

    this.w = 0;
    this.h = 0;
    this.dpr = 1;
    this.raf = 0;
    this.running = false;
    this.hidden = false;
    this.t = 0;          // 累计时间（秒）
    this.lastTs = 0;

    this.type = 'sun';
    this.preset = WEATHER_PRESETS.sun;

    // 粒子数组（复用对象，不每帧 new）
    this.rain = [];
    this.snow = [];
    this.leaf = [];
    this.clouds = [];
    this.splashes = [];

    // 闪电状态
    this.flashAlpha = 0;

    // 太阳位置插值
    this.sunPos = { x: 0.78, y: 0.22 };
    this.sunTarget = { x: 0.78, y: 0.22 };

    this.reduce = false;
    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
  }

  /* ── 尺寸 ── */
  resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.w = Math.max(1, Math.floor(rect.width || 300));
    this.h = Math.max(1, Math.floor(rect.height || 200));
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.floor(this.w * this.dpr);
    this.canvas.height = Math.floor(this.h * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.seed();
  }

  /* 按面积决定粒子数量，硬上限 */
  count(base, cap) {
    const area = this.w * this.h;
    const scale = Math.min(1.6, Math.max(0.35, area / 220000));
    return Math.min(cap, Math.round(base * scale));
  }

  /* ── 初始化/重建粒子 ── */
  seed() {
    const p = this.preset;

    /* 云：位置、速度、大小都随机，做出自然错落 */
    this.clouds = [];
    const cloudN = this.count(p.cloud, 12);
    for (let i = 0; i < cloudN; i++) {
      this.clouds.push({
        x: Math.random() * (this.w + 200) - 100,
        y: 8 + Math.random() * this.h * 0.42,
        r: 26 + Math.random() * 44,
        speed: 4 + Math.random() * 10,
        layer: i % 3,          // 0 远 1 中 2 近
        alpha: p.cloudAlpha * (0.5 + (i % 3) * 0.22),
      });
    }

    /* 雨：三层，按线宽区分层次（近粗远细） */
    this.rain = [];
    const rainN = this.count(p.rain, 200);
    for (let i = 0; i < rainN; i++) {
      this.rain.push(this.newDrop(true));
    }

    /* 雪 */
    this.snow = [];
    const snowN = this.count(p.snow, 110);
    for (let i = 0; i < snowN; i++) {
      this.snow.push(this.newFlake(true));
    }

    /* 落叶 */
    this.leaf = [];
    const leafN = this.count(p.leaf, 40);
    for (let i = 0; i < leafN; i++) {
      this.leaf.push(this.newLeafP(true));
    }

    this.splashes = [];
  }

  newDrop(randomY) {
    // layer: 0 远（细淡慢） 1 中 2 近（粗亮快）
    const layer = Math.floor(Math.random() * 3);
    const thick = [0.7, 1.1, 1.7][layer];
    const len = [7, 12, 19][layer];
    const speed = [220, 340, 470][layer];
    return {
      x: Math.random() * (this.w + 60) - 30,
      y: randomY ? Math.random() * this.h : -len,
      len,
      thick,
      speed: speed * (0.85 + Math.random() * 0.3),
      layer,
      alpha: [0.3, 0.5, 0.72][layer],
    };
  }

  newFlake(randomY) {
    const scale = 0.4 + Math.random() * 0.6;
    return {
      x: Math.random() * this.w,
      y: randomY ? Math.random() * this.h : -10,
      r: 1.2 + scale * 2.6,
      fall: 16 + (1 - scale) * 26,      // 小的落得慢
      drift: 0,                          // 横向偏移累加
      wobbleAmp: 12 + Math.random() * 26,
      wobbleFreq: 0.6 + Math.random() * 1.1,
      phase: Math.random() * Math.PI * 2,
      alpha: 0.32 + scale * 0.5,
    };
  }

  newLeafP(randomY) {
    const size = 3.4 + Math.random() * 4.6;
    return {
      x: Math.random() * this.w,
      y: randomY ? Math.random() * this.h : -12,
      size,
      rot: Math.random() * Math.PI * 2,
      rotSpeed: (Math.random() - 0.5) * 2.6,
      fall: 28 + Math.random() * 46,
      swayAmp: 16 + Math.random() * 40,
      swayFreq: 0.5 + Math.random() * 1.2,
      phase: Math.random() * Math.PI * 2,
      hue: [28, 34, 42, 88, 96][Math.floor(Math.random() * 5)],
      alpha: 0.42 + Math.random() * 0.4,
    };
  }

  /* ── 切换天气 ── */
  setWeather(type) {
    const next = WEATHER_PRESETS[type] ? type : 'cloud';
    if (next === this.type && this.clouds.length) return;
    this.type = next;
    this.preset = WEATHER_PRESETS[next];
    this.seed();
    this.sunTarget = this.preset.sun
      ? { x: 0.78, y: 0.22 }
      : { x: 0.86, y: 0.16 };
    if (this.reduce) this.drawStatic();
  }

  /* ── 主循环 ── */
  start() {
    if (this.running) return;
    this.running = true;
    this.lastTs = 0;
    this.raf = requestAnimationFrame(this.frame);
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.raf);
  }

  frame(ts) {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.frame);
    if (this.hidden) return;

    // dt 上限 50ms，避免切回标签页时粒子瞬移
    const dt = this.lastTs ? Math.min(0.05, (ts - this.lastTs) / 1000) : 0.016;
    this.lastTs = ts;
    this.t += dt;

    this.update(dt);
    this.render();
  }

  /* ── 更新 ── */
  update(dt) {
    const p = this.preset;
    const wind = p.wind;

    /* 太阳位置缓动 */
    this.sunPos.x += (this.sunTarget.x - this.sunPos.x) * Math.min(1, dt * 2.2);
    this.sunPos.y += (this.sunTarget.y - this.sunPos.y) * Math.min(1, dt * 2.2);

    /* 云：按层不同速度，风越大越快 */
    for (const c of this.clouds) {
      c.x += (c.speed * 0.35 + wind * 16) * dt * (1 - c.layer * 0.18);
      if (c.x - c.r > this.w + 120) {
        c.x = -c.r - 60;
        c.y = 8 + Math.random() * this.h * 0.42;
      }
    }

    /* 雨 */
    if (this.rain.length) {
      for (let i = 0; i < this.rain.length; i++) {
        const d = this.rain[i];
        d.y += d.speed * dt;
        d.x += wind * 30 * dt;
        if (d.y > this.h + d.len) {
          // 近层落地产生溅射
          if (d.layer === 2 && this.splashes.length < 26) {
            this.splashes.push({ x: d.x, y: this.h - 2, r: 1, life: 0.32, max: 0.32 });
          }
          this.rain[i] = this.newDrop(false);
        }
      }
    }

    /* 雪：下落 + 横向摇摆（双运动叠加，和参考站同思路） */
    if (this.snow.length) {
      for (let i = 0; i < this.snow.length; i++) {
        const s = this.snow[i];
        s.y += s.fall * dt;
        s.phase += s.wobbleFreq * dt * 2.4;
        s.x += Math.cos(s.phase) * s.wobbleAmp * dt * 2.2 + wind * 22 * dt;
        if (s.y > this.h + 8) {
          this.snow[i] = this.newFlake(false);
        }
        if (s.x < -12) s.x = this.w + 12;
        if (s.x > this.w + 12) s.x = -12;
      }
    }

    /* 落叶：下落 + 摇摆 + 自转 */
    if (this.leaf.length) {
      for (let i = 0; i < this.leaf.length; i++) {
        const l = this.leaf[i];
        l.y += l.fall * dt;
        l.phase += l.swayFreq * dt * 3;
        l.x += Math.sin(l.phase) * l.swayAmp * dt * 2 + wind * 40 * dt;
        l.rot += l.rotSpeed * dt;
        if (l.y > this.h + 14) {
          this.leaf[i] = this.newLeafP(false);
        }
        if (l.x < -20) l.x = this.w + 20;
        if (l.x > this.w + 20) l.x = -20;
      }
    }

    /* 溅射扩散 */
    if (this.splashes.length) {
      for (let i = this.splashes.length - 1; i >= 0; i--) {
        const s = this.splashes[i];
        s.life -= dt;
        s.r += 26 * dt;
        if (s.life <= 0) this.splashes.splice(i, 1);
      }
    }

    /* 闪电：随机触发 */
    if (p.flash) {
      if (this.flashAlpha > 0.01) {
        this.flashAlpha *= 0.86;
      } else if (Math.random() < dt * 0.55) {
        this.flashAlpha = 0.5 + Math.random() * 0.35;
      }
    } else {
      this.flashAlpha = 0;
    }
  }

  /* ── 渲染 ── */
  render() {
    const ctx = this.ctx;
    const w = this.w;
    const h = this.h;
    const p = this.preset;

    ctx.clearRect(0, 0, w, h);

    /* 天空渐变 */
    const g = ctx.createLinearGradient(0, 0, w * 0.35, h);
    g.addColorStop(0, p.sky[0]);
    g.addColorStop(0.55, p.sky[1]);
    g.addColorStop(1, p.sky[2]);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    /* 太阳/月亮光晕 */
    if (p.sun) {
      const cx = this.sunPos.x * w;
      const cy = this.sunPos.y * h;
      const R = Math.max(w, h) * 0.42;
      const sg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
      sg.addColorStop(0, 'rgba(255, 226, 150, 0.85)');
      sg.addColorStop(0.35, 'rgba(255, 232, 176, 0.32)');
      sg.addColorStop(1, 'rgba(255, 240, 200, 0)');
      ctx.fillStyle = sg;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // 光核
      ctx.fillStyle = 'rgba(255, 244, 214, 0.95)';
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(10, w * 0.055), 0, Math.PI * 2);
      ctx.fill();
    }

    /* 云 */
    for (const c of this.clouds) {
      this.drawCloud(c);
    }

    /* 雨 */
    if (this.rain.length) {
      ctx.lineCap = 'round';
      for (const d of this.rain) {
        ctx.strokeStyle = `rgba(58, 96, 160, ${d.alpha})`;
        ctx.lineWidth = d.thick;
        ctx.beginPath();
        ctx.moveTo(d.x, d.y);
        ctx.lineTo(d.x - p.wind * 2.2, d.y + d.len);
        ctx.stroke();
      }
    }

    /* 溅射 */
    if (this.splashes.length) {
      for (const s of this.splashes) {
        const a = (s.life / s.max) * 0.5;
        ctx.strokeStyle = `rgba(120, 160, 210, ${a})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.ellipse(s.x, s.y, s.r, s.r * 0.38, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    /* 雪 */
    if (this.snow.length) {
      for (const s of this.snow) {
        ctx.fillStyle = `rgba(255, 255, 255, ${s.alpha})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    /* 落叶 */
    if (this.leaf.length) {
      for (const l of this.leaf) {
        ctx.save();
        ctx.translate(l.x, l.y);
        ctx.rotate(l.rot);
        ctx.fillStyle = `hsla(${l.hue}, 46%, 46%, ${l.alpha})`;
        // 用椭圆 + 叶脉做叶片，比矩形自然
        ctx.beginPath();
        ctx.ellipse(0, 0, l.size, l.size * 0.48, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }

    /* 雾 */
    if (p.fog > 0) {
      const fg = ctx.createLinearGradient(0, h * 0.3, 0, h);
      fg.addColorStop(0, `rgba(238, 240, 238, 0)`);
      fg.addColorStop(1, `rgba(238, 240, 238, ${p.fog})`);
      ctx.fillStyle = fg;
      ctx.fillRect(0, 0, w, h);
    }

    /* 闪电 */
    if (this.flashAlpha > 0.01) {
      ctx.fillStyle = `rgba(255, 255, 255, ${this.flashAlpha})`;
      ctx.fillRect(0, 0, w, h);
    }
  }

  drawCloud(c) {
    const ctx = this.ctx;
    const r = c.r;
    ctx.save();
    ctx.globalAlpha = c.alpha;
    // 用多个圆叠出云朵轮廓，比单圆自然
    const parts = [
      { dx: -r * 0.72, dy: r * 0.14, rr: r * 0.62 },
      { dx: 0, dy: 0, rr: r },
      { dx: r * 0.78, dy: r * 0.1, rr: r * 0.7 },
      { dx: r * 0.3, dy: r * 0.34, rr: r * 0.56 },
      { dx: -r * 0.36, dy: r * 0.36, rr: r * 0.52 },
    ];
    ctx.fillStyle = 'rgba(255, 255, 255, 0.92)';
    for (const part of parts) {
      ctx.beginPath();
      ctx.arc(c.x + part.dx, c.y + part.dy, part.rr, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  /* 减少动效：只画一帧静态 */
  drawStatic() {
    const prev = {
      rain: this.rain,
      snow: this.snow,
      leaf: this.leaf,
      splashes: this.splashes,
      flashAlpha: this.flashAlpha,
    };
    // 静态帧保留少量粒子做构图，但不运动
    this.splashes = [];
    this.flashAlpha = 0;
    this.render();
    this.rain = prev.rain;
    this.snow = prev.snow;
    this.leaf = prev.leaf;
    this.splashes = prev.splashes;
    this.flashAlpha = prev.flashAlpha;
  }

  destroy() {
    this.stop();
    this.rain = [];
    this.snow = [];
    this.leaf = [];
    this.clouds = [];
    this.splashes = [];
  }
}

/* ═══════════════════════════════════════════════════════════════
   对外接口：挂载到页面上的所有 [data-weather-scene] 元素
   ═══════════════════════════════════════════════════════════════ */
(function initWeatherScenes() {
  if (window.__wxBooted) return;
  window.__wxBooted = true;

  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const html = document.documentElement;
  const motionMode = html.dataset.motion || 'immersive';
  const animOK = () => !reduceMQ.matches && motionMode !== 'still';

  const scenes = [];

  function mount() {
    const nodes = document.querySelectorAll('[data-weather-scene]');
    if (!nodes.length) return;

    nodes.forEach((node) => {
      const canvas = node.querySelector('canvas');
      if (!canvas) return;

      const scene = new WeatherScene(canvas);
      if (!scene.ctx) return;

      const type = node.dataset.weatherScene || 'cloud';
      scene.type = WEATHER_PRESETS[type] ? type : 'cloud';
      scene.preset = WEATHER_PRESETS[scene.type];
      scene.reduce = !animOK();

      scene.resize();
      scenes.push(scene);
      node.dataset.dark = scene.type === 'thunder' ? '1' : '0';

      if (scene.reduce) {
        scene.drawStatic();
      } else {
        scene.start();
      }

      // 绑定天气切换按钮（在最近的 section 或父容器里找 .wx-swatch）
      const host = node.closest('section') || node.parentElement;
      if (host) {
        host.querySelectorAll('.wx-swatch').forEach((btn) => {
          btn.addEventListener('click', () => {
            const next = btn.dataset.wx;
            scene.setWeather(next);
            if (scene.reduce) scene.drawStatic();
            else { scene.lastTs = 0; scene.start(); }
            // 更新按下态
            host.querySelectorAll('.wx-swatch').forEach((b) => {
              b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
            });
            // 深色天空时文字反白
            node.dataset.dark = next === 'thunder' ? '1' : '0';
          });
        });
      }

      // 尺寸变化重建
      let rTimer = 0;
      const ro = new ResizeObserver(() => {
        clearTimeout(rTimer);
        rTimer = setTimeout(() => {
          scene.resize();
          if (scene.reduce) scene.drawStatic();
        }, 160);
      });
      ro.observe(node);
      scene._ro = ro;
    });
  }

  // 页面隐藏暂停，回来恢复
  document.addEventListener('visibilitychange', () => {
    scenes.forEach((s) => {
      s.hidden = document.hidden;
      if (document.hidden) {
        s.stop();
      } else if (!s.reduce) {
        s.lastTs = 0;
        s.start();
      }
    });
  });

  // 动效偏好变化
  reduceMQ.addEventListener('change', () => {
    scenes.forEach((s) => {
      s.reduce = !animOK();
      if (s.reduce) {
        s.stop();
        s.drawStatic();
      } else {
        s.start();
      }
    });
  });

  function cleanup() {
    scenes.forEach((s) => {
      if (s._ro) s._ro.disconnect();
      s.destroy();
    });
    scenes.length = 0;
  }
  window.addEventListener('pagehide', cleanup);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }

  // 暴露给外部（天气切换按钮用）
  window.__weatherScenes = scenes;
})();
