/* ═══════════════════════════════════════════════════════════════
   动效运行时（Motion Runtime）
   ─────────────────────────────────────────────────────────────────
   设计依据（GSAP 官方 Accessibility 与 ScrollTrigger 指南）：
     1. 所有动效包在 prefers-reduced-motion 判断内
     2. 不做 DOM 文字拆分（会破坏屏幕阅读器）
     3. 同一个元素只被一个机制控制（避免属性冲突跳回）
     4. 依赖视口尺寸的 ScrollTrigger 用函数式 start/end
     5. 页面卸载/重新进入时清理，禁止重复注册
     6. GSAP 加载失败时，CSS 回退仍能完整工作

   分层策略（任务书要求的 motion/* 职责）：
     tokens        → motion.css 的 CSS 变量
     entrance      → reveal / title-line / hero-art
     scroll        → GSAP ScrollTrigger 增强（可选）
     interaction   → 指针跟随、卡片反馈
     lifecycle     → 本文件的 cleanup / 单次初始化保护
     accessibility → reduced-motion 与 still 模式
   ═══════════════════════════════════════════════════════════════ */
(() => {
  'use strict';

  // —— 环境探测 ——
  const html = document.documentElement;
  const motionMode = html.dataset.motion || 'immersive';
  const reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointerMQ = window.matchMedia('(hover: hover) and (pointer: fine)');

  // 是否应启用动效：用户没要求减少 且 站点未设为 still
  const motionEnabled = () => !reduceMQ.matches && motionMode !== 'still';

  // —— 单次初始化保护 ——
  // 任务书要求「禁止重复初始化相同的滚动触发器」。
  // 用一个标志位确保脚本被重复注入时也只有一个实例在跑。
  if (window.__studioMotionBooted) return;
  window.__studioMotionBooted = true;

  // 运行期需要清理的资源集合
  const cleanups = [];
  const registerCleanup = (fn) => cleanups.push(fn);

  /* ─────────────────────────────────────────────────────────────
     1) 揭幕：IntersectionObserver
     ★ 为什么用 IO 而不是 GSAP ScrollTrigger：
       IO 是原生 API，无依赖、开销低，且 GSAP 加载失败时仍可用。
       ScrollTrigger 只留给需要「滚动进度联动」的场景。
     ───────────────────────────────────────────────────────────── */
  function setupReveals() {
    // 首屏元素由 revealAboveFold 同步处理，这里只负责折叠线以下的内容
    const all = document.querySelectorAll(
      '.reveal, .stagger, .title-line, .hero-art, .brand-mark'
    );
    const targets = Array.from(all).filter((el) => {
      const hero = el.closest('.hero');
      return !hero;
    });
    if (!targets.length) return;

    if (!motionEnabled() || !('IntersectionObserver' in window)) {
      // 不动效时直接显示，绝不让内容留在隐藏态
      targets.forEach((el) => {
        el.classList.add('is-visible');
        el.classList.add('is-settled');
      });
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-visible');
          // 动画播完后撤掉 will-change，释放合成层
          const settleDelay = 1400;
          const t = setTimeout(() => {
            entry.target.classList.add('is-settled');
          }, settleDelay);
          registerCleanup(() => clearTimeout(t));
          io.unobserve(entry.target);
        });
      },
      {
        threshold: 0.08,
        // 提前一点触发，避免元素「已经看到了才开始动」
        rootMargin: '0px 0px -8% 0px',
      }
    );

    targets.forEach((el) => io.observe(el));
    registerCleanup(() => io.disconnect());
  }

  /* ─────────────────────────────────────────────────────────────
     2) 首屏品牌开场
     ★ 任务书要求：默认只在「合适的首次访问场景」完整播放，
       站内导航、浏览器返回时不重复。
       判断依据：sessionStorage 标记 + 是否首屏可见。
     ───────────────────────────────────────────────────────────── */
  function setupBrandSequence() {
    const hero = document.querySelector('.hero');
    if (!hero) return false;

    const KEY = 'studio:brandPlayed';
    let alreadyPlayed = false;
    try {
      alreadyPlayed = sessionStorage.getItem(KEY) === '1';
    } catch (e) {
      alreadyPlayed = false;
    }

    if (!motionEnabled()) {
      hero.classList.add('is-ready', 'no-brand');
      return false;
    }

    if (alreadyPlayed) {
      // 跳过冗长开场，但**首屏元素仍必须可见**
      hero.classList.add('is-ready', 'no-brand');
      return false;
    }

    try {
      sessionStorage.setItem(KEY, '1');
    } catch (e) { /* 忽略 */ }

    hero.classList.add('is-ready');
    return true; // 表示本次播放完整开场
  }

  /* 首屏元素立即显示
     ★ 关键：不依赖 IntersectionObserver 的异步回调。
       首屏元素在加载时必然可见，必须同步标记，
       否则会出现「主视觉被 clip-path 遮住、要等 IO 才展开」的空窗。 */
  function revealAboveFold() {
    const hero = document.querySelector('.hero');
    if (!hero) return;
    hero.querySelectorAll('.reveal, .title-line, .hero-art, .brand-mark')
      .forEach((el) => {
        el.classList.add('is-visible');
        el.classList.add('is-settled');
      });
  }

  /* ─────────────────────────────────────────────────────────────
     3) 滚动叙事（可选增强层）
     ★ 严格遵循 GSAP 指南：
       - ScrollTrigger 挂在**时间线**上，不挂在嵌套 tween 上
       - 同一元素不被多个 ST 控制同一属性
       - start/end 用函数值以适配视口变化
       - 只在 GSAP 可用且允许动效时启用
     ───────────────────────────────────────────────────────────── */
  function setupScrollNarrative() {
    if (!motionEnabled()) return;
    if (!window.gsap || !window.ScrollTrigger) return;

    const gsap = window.gsap;
    const ScrollTrigger = window.ScrollTrigger;
    gsap.registerPlugin(ScrollTrigger);

    // 章节标题的轻微视差：只改 y，幅度很小（不抢阅读）
    const headings = gsap.utils.toArray('.section-heading h2');
    headings.forEach((el) => {
      gsap.fromTo(
        el,
        { y: 18, opacity: 0.55 },
        {
          y: 0,
          opacity: 1,
          duration: 0.85,
          ease: 'power2.out',
          scrollTrigger: {
            trigger: el,
            // 函数值：视口尺寸变化后能被 ScrollTrigger 重新计算
            start: () => 'top 88%',
            once: true,
          },
        }
      );
    });

    // 宣言段落的进度联动（scrub）
    // ★ 用 scrub 会与滚动「绑定」，所以幅度必须极小，
    //   否则会让人感觉页面自己在动。
    const manifesto = document.querySelector('.manifesto-text');
    if (manifesto) {
      gsap.fromTo(
        manifesto,
        { y: 14, opacity: 0.7 },
        {
          y: -14,
          opacity: 1,
          ease: 'none',
          scrollTrigger: {
            trigger: manifesto,
            start: () => 'top bottom',
            end: () => 'bottom top',
            scrub: 0.6,
          },
        }
      );
    }

    // 保存实例以便清理
    registerCleanup(() => {
      ScrollTrigger.getAll().forEach((st) => st.kill());
    });
  }

  /* ─────────────────────────────────────────────────────────────
     4) 指针交互（仅桌面精确指针）
     ★ 任务书要求：光标跟随不得遮挡正文、链接或输入框。
       这里只移动装饰性图形，不创建跟随光标，不改光标本身。
     ───────────────────────────────────────────────────────────── */
  function setupPointer() {
    if (!motionEnabled() || !finePointerMQ.matches) return;

    const cards = document.querySelectorAll('.project-visual, .archive-art');
    cards.forEach((card) => {
      const shape = card.querySelector('.visual-shape');
      if (!shape) return;

      let raf = 0;
      const onMove = (e) => {
        if (raf) return;
        raf = requestAnimationFrame(() => {
          const r = card.getBoundingClientRect();
          const x = (e.clientX - r.left) / r.width - 0.5;
          const y = (e.clientY - r.top) / r.height - 0.5;
          // 幅度压到 9px 内，纯装饰，不干扰阅读
          shape.style.transform =
            `translate(calc(-50% + ${x * 9}px), calc(-50% + ${y * 9}px)) rotate(${x * 3}deg)`;
          raf = 0;
        });
      };
      const onLeave = () => {
        shape.style.transform = '';
      };

      card.addEventListener('pointermove', onMove, { passive: true });
      card.addEventListener('pointerleave', onLeave);
      registerCleanup(() => {
        card.removeEventListener('pointermove', onMove);
        card.removeEventListener('pointerleave', onLeave);
      });
    });
  }

  /* ─────────────────────────────────────────────────────────────
     5) 导航与进度条
     ───────────────────────────────────────────────────────────── */
  function setupNavAndProgress() {
    const toggle = document.querySelector('.menu-toggle');
    const nav = document.querySelector('.main-nav');
    if (toggle && nav) {
      const onClick = () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', String(open));
        toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
        nav.classList.toggle('is-open', open);
      };
      toggle.addEventListener('click', onClick);
      registerCleanup(() => toggle.removeEventListener('click', onClick));
    }

    const progress = document.querySelector('.scroll-progress');
    if (!progress) return;

    let ticking = false;
    const update = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      progress.style.width = `${max > 0 ? (window.scrollY / max) * 100 : 0}%`;
      ticking = false;
    };
    const onScroll = () => {
      if (!ticking) {
        requestAnimationFrame(update);
        ticking = true;
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    update();
    registerCleanup(() => window.removeEventListener('scroll', onScroll));
  }

  /* ─────────────────────────────────────────────────────────────
     6) 表单确认（后台用）
     ───────────────────────────────────────────────────────────── */
  function setupConfirm() {
    document.querySelectorAll('form[data-confirm]').forEach((form) => {
      const onSubmit = (e) => {
        if (!window.confirm(form.dataset.confirm || '确定继续吗？')) e.preventDefault();
      };
      form.addEventListener('submit', onSubmit);
      registerCleanup(() => form.removeEventListener('submit', onSubmit));
    });
  }

  /* ─────────────────────────────────────────────────────────────
     7) 生命周期
     ★ 任务书要求：页面卸载时清理时间轴、监听、观察器、定时器。
       SPA 场景靠路由切换钩子；多页场景靠 beforeunload/pagehide。
       这里两者都覆盖，且清理函数可重入。
     ───────────────────────────────────────────────────────────── */
  let cleaned = false;
  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    cleanups.forEach((fn) => {
      try {
        fn();
      } catch (err) {
        // 清理失败不应影响页面；静默并记录一次
        console.warn('[motion] cleanup error:', err);
      }
    });
    cleanups.length = 0;
  }
  window.addEventListener('pagehide', cleanup);
  window.addEventListener('beforeunload', cleanup);

  /* ─────────────────────────────────────────────────────────────
     8) 启动
     ★ 关键：先给 <html> 打 js-motion 标记，CSS 才知道要应用初始隐藏态。
       如果 JS 失败，这个标记不会出现，内容始终可见。
     ───────────────────────────────────────────────────────────── */
  function boot() {
    if (motionEnabled()) {
      html.classList.add('js-motion');
    } else {
      html.classList.remove('js-motion');
      // 不动效时立刻让所有元素可见
      document
        .querySelectorAll('.reveal, .stagger, .title-line, .hero-art, .brand-mark')
        .forEach((el) => el.classList.add('is-visible', 'is-settled'));
    }

    setupBrandSequence();
    // 首屏必须同步可见，先于 IO 初始化
    revealAboveFold();
    setupReveals();
    setupScrollNarrative();
    setupPointer();
    setupNavAndProgress();
    setupConfirm();
  }

  // 系统偏好变化时动态响应（任务书要求）
  const onReduceChange = () => {
    if (reduceMQ.matches) {
      // 切到「减少动效」：立即显示全部内容
      document
        .querySelectorAll('.reveal, .stagger, .title-line, .hero-art, .brand-mark')
        .forEach((el) => el.classList.add('is-visible', 'is-settled'));
      html.classList.remove('js-motion');
    }
  };
  if (reduceMQ.addEventListener) {
    reduceMQ.addEventListener('change', onReduceChange);
    registerCleanup(() => reduceMQ.removeEventListener('change', onReduceChange));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
