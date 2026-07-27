/* ============================================================
   学记 · 灯下书卷  v7 — fx-engine.js
   - PREVIEW_MODE：拦截 fetch 返回 503 + 占位数据
   - 装饰层：自动注入 .fx-bg（灯晕 + 灯尘 + 远光渐变）
   - v7 新增：真实台灯组件（拉绳交互 + 熄灯态 + localStorage 持久化）
   - v7 新增：桌面端光标追光 + 主按钮磁吸
   - v7 新增：标题字符渐显（.title-letters）
   - 动效：reveal / count-up / ripple / tilt / preview-badge
   - 首屏低密度：buildQuickCarousel 注入横向滑动卡片
   - 全部带 prefers-reduced-motion 降级
   ============================================================ */

(function () {
  "use strict";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isMobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
  const isWeChat = /MicroMessenger/i.test(navigator.userAgent || "");

  /* ===========================================================
     0. 工具：占位数据
     =========================================================== */
  const previewFlag = new URLSearchParams(location.search).get("preview");
  const previewOn = previewFlag !== "0" && localStorage.getItem("xueji_preview_mode") !== "0";
  if (previewOn) localStorage.setItem("xueji_preview_mode", "1");

  const placeholder = {
    ok: (data) => ({ ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) }),
    fail: (msg, code = 503) => ({
      ok: false, status: code,
      json: async () => ({ error: msg, detail: msg }),
      text: async () => JSON.stringify({ error: msg })
    })
  };

  function pickApiMock(url, method) {
    if (!previewOn) return null;
    const m = String(url || "");
    const path = m.replace(/^https?:\/\/[^/]+/, "").replace(/^\/xueji/, "");
    // 仅匹配后端 API 入口：/api/* 和 /xueji/api/* 以及显式的 /xueji/health
    const isApi = /\/api\//.test(path) || /\/xueji\/health$/.test(m) || /\/xueji\/api\//.test(m);
    if (!isApi) return null;

    // 健康检查（兼容 /api/health 和 /xueji/health）
    if (/health|ping|status/.test(path)) return placeholder.ok({ status: "ok", mode: "preview" });

    // 学生端
    if (/\/api\/student-access\/[^/]+\/today$/.test(path) && method === "GET") {
      return placeholder.ok(studentTodayMock());
    }
    if (/\/api\/student-access\/[^/]+\/voice-practice\/tts$/.test(path)) {
      return placeholder.ok({ audio_url: "", message: "预览模式：用浏览器朗读" });
    }
    if (/\/api\/student-access\/[^/]+\/voice-practice\/submissions$/.test(path)) {
      const score = 70 + Math.floor(Math.random() * 25);
      return placeholder.ok({ score, feedback: "预览模式评分：完成度不错，多读两遍更稳。" });
    }
    if (/\/api\/students\/login$/.test(path) && method === "POST") {
      return placeholder.ok({ token: "preview-student-token", account: { phone: "13800000000" } });
    }
    if (/\/api\/students\/logout$/.test(path)) {
      return placeholder.ok({ ok: true });
    }

    // 家长端
    if (/\/api\/parents\/login$/.test(path) && method === "POST") {
      return placeholder.ok({ token: "preview-parent-token", parent: { phone: "13800000000", nickname: "预览家长" } });
    }
    if (/\/api\/parents\/register$/.test(path) && method === "POST") {
      return placeholder.ok({ token: "preview-parent-token", parent: { phone: "13800000000", nickname: "预览家长" } });
    }
    if (/\/api\/parents\/me$/.test(path)) {
      return placeholder.ok({ parent: { phone: "13800000000", nickname: "预览家长" }, students: parentStudentsMock() });
    }
    if (/\/api\/parents\/intake$/.test(path) && method === "POST") {
      return placeholder.ok({ student: { id: 1, display_name: "预览同学", student_code: "PREVIEW-001" } });
    }
    if (/\/api\/parents\/logout$/.test(path)) {
      return placeholder.ok({ ok: true });
    }
    if (/plan$/.test(path) && method === "GET") {
      return placeholder.ok(planMock());
    }
    if (/plan$/.test(path) && method === "POST") {
      return placeholder.ok({ status: "queued", job_id: "preview-job-" + Date.now() });
    }
    if (/cards$/.test(path) && method === "GET") {
      return placeholder.ok({ docx_url: "https://example.com/preview.docx", updated_at: "2026-07-28" });
    }
    if (/cards$/.test(path) && method === "POST") {
      return placeholder.ok({ status: "queued", job_id: "preview-cards-" + Date.now() });
    }
    if (/voice-practice$/.test(path) && method === "GET") {
      return placeholder.ok({ items: voiceItemsMock() });
    }
    if (/feedbacks$/.test(path) && method === "POST") {
      return placeholder.ok({ status: "saved", feedback_id: "preview-fb-" + Date.now() });
    }
    if (/feedback-ai$/.test(path) && method === "POST") {
      return placeholder.ok({ status: "queued", job_id: "preview-ai-" + Date.now() });
    }
    if (/referrals$/.test(path)) {
      return placeholder.ok({ code: "PREVIEW-CODE", total_rewards: 0, items: [] });
    }

    // 后台 / tool-api
    if (/parents\/list$/.test(path)) {
      return placeholder.ok({ items: adminParentsMock() });
    }
    if (/students\/list$/.test(path)) {
      return placeholder.ok({ items: adminStudentsMock() });
    }
    if (/actions\/list$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/ai-jobs\/list$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/voice-samples$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/knowledge-points$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/knowledge-resources$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/exam-patterns$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/model-configs$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/entitlements$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/scope-index$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/coverage$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/collection-tasks$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/collection-task-records$/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/student-detail$/.test(path) && method === "GET") {
      return placeholder.ok({ student: adminStudentsMock()[0], feedbacks: [], plans: [] });
    }
    return placeholder.fail("预览模式：该接口无占位数据", 503);
  }

  function studentTodayMock() {
    return {
      student: { display_name: "预览同学", grade_region: "初一 · 北京", goal: "期末基础抢分" },
      date: "2026-07-28",
      guide: { steps: ["英语跟读", "记忆故事", "知识卡", "往日回顾", "今日打卡"] },
      voice: {
        items: [
          { target_text: "Good morning, my friend.", target_meaning: "早上好，我的朋友。", scenario: "日常问候" },
          { target_text: "I finished my homework on time.", target_meaning: "我按时完成了作业。", scenario: "日常表达" },
          { target_text: "The weather is wonderful today.", target_meaning: "今天天气真好。", scenario: "天气描述" }
        ]
      },
      assessment: { quota: { limit: 5, remaining: 5 } },
      knowledge: {
        memory_story: "今天有一条新的记忆故事：一只小猫在灯下读完了整本书。重复一遍，关键词会留在脑海里。",
        cards: [
          { tag: "语文·古诗", q: "「床前明月光」的下一句？", a: "疑是地上霜。" },
          { tag: "数学·单位", q: "1 小时等于多少分钟？", a: "60 分钟。" },
          { tag: "英语·词汇", q: "\"book\" 的复数？", a: "books" }
        ],
        review_cards: [
          { tag: "复习 D1", q: "昨天记过的古诗第二句？", a: "疑是地上霜。" }
        ],
        history_days: [],
        current_day_index: 0,
        adjustment_note: "按 D1/D3/D7 间隔复习，优先处理★卡。"
      },
      account: { phone: "13800000000" }
    };
  }

  function parentStudentsMock() {
    return [
      { id: 1, display_name: "预览同学", student_code: "PREVIEW-001", grade_region: "初一 · 北京", goal: "期末基础抢分", current_level: "中等", execution_mode: "标准", student_account_phone: "13800000001" }
    ];
  }

  function planMock() {
    return {
      plan: {
        student_name: "预览同学",
        summary: "本周按 5 科路径排：语文 1 + 数学 1 + 英语 2 + 历史 1 + 道法 1，每天 6 格小步。",
        subjects: [
          { name: "语文", sessions: 1, focus: "古诗 + 字词" },
          { name: "数学", sessions: 1, focus: "方程基础" },
          { name: "英语", sessions: 2, focus: "跟读 + 词汇" },
          { name: "历史", sessions: 1, focus: "近代史脉络" },
          { name: "道法", sessions: 1, focus: "时政 + 答题方法" }
        ],
        weekly_pace: "5 天 / 周，每天 15-25 分钟"
      },
      versions: [
        { version_code: "V1", published_at: "2026-07-21", status: "published" }
      ]
    };
  }

  function voiceItemsMock() {
    return [
      { id: 1, target_text: "Open your book, please.", target_meaning: "请打开你的书。", scenario: "课堂指令" },
      { id: 2, target_text: "I have a question.", target_meaning: "我有一个问题。", scenario: "主动发言" }
    ];
  }

  function adminParentsMock() {
    return [
      { id: 1, phone: "13800000000", nickname: "预览家长", students_count: 1, package: "试用 7 天" }
    ];
  }

  function adminStudentsMock() {
    return [
      { id: 1, display_name: "预览同学", student_code: "PREVIEW-001", grade_region: "初一 · 北京", goal: "期末基础抢分", parent_phone: "13800000000", status: "在读" }
    ];
  }

  /* ===========================================================
     1. 拦截 fetch
     =========================================================== */
  if (previewOn && !window.__xuejiFetchPatched) {
    window.__xuejiFetchPatched = true;
    const origFetch = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      let url = typeof input === "string" ? input : (input && input.url) || "";
      const method = (init && init.method) || (input && input.method) || "GET";
      const mock = pickApiMock(url, method);
      if (mock) {
        // 模拟 80-180ms 网络延迟
        await new Promise(r => setTimeout(r, 80 + Math.random() * 100));
        return mock;
      }
      return origFetch(input, init);
    };
  }

  /* ===========================================================
     2. 注入装饰层
     =========================================================== */
  function injectFxBg() {
    if (document.querySelector(".fx-bg")) return;
    const bg = document.createElement("div");
    bg.className = "fx-bg";
    bg.setAttribute("aria-hidden", "true");
    // 12 颗灯尘
    for (let i = 0; i < 12; i++) {
      const ember = document.createElement("span");
      ember.className = "ember";
      bg.appendChild(ember);
    }
    // 装饰层永远垫底
    const body = document.body;
    if (body.firstChild) body.insertBefore(bg, body.firstChild);
    else body.appendChild(bg);
  }

  /* ===========================================================
   * 2.5 真实台灯组件（v7 关键）
   * - 在右上角注入 <aside class="xueji-lamp"> 包含内联 SVG
   * - 点击拉绳 → 切换 body.lamp-off + 拉绳摆动 + localStorage 持久化
   * - 渐变通过 SVG <defs> 内联，避免外部资源
   * =========================================================== */
  function lampSvgMarkup() {
    return `
<svg viewBox="0 0 100 110" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="lampShadeGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#ead9b4" />
      <stop offset="55%"  stop-color="#cfa86b" />
      <stop offset="100%" stop-color="#8a6a3a" />
    </linearGradient>
    <linearGradient id="lampBaseGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"   stop-color="#5a4426" />
      <stop offset="100%" stop-color="#2a1f10" />
    </linearGradient>
    <linearGradient id="lampConeGrad" x1="0.5" y1="0" x2="0.5" y2="1">
      <stop offset="0%"   stop-color="rgba(234,217,180,0.55)" />
      <stop offset="60%"  stop-color="rgba(207,168,107,0.18)" />
      <stop offset="100%" stop-color="rgba(207,168,107,0)" />
    </linearGradient>
    <radialGradient id="lampBulbGrad" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0%"   stop-color="#fff7df" />
      <stop offset="60%"  stop-color="#ead9b4" />
      <stop offset="100%" stop-color="#cfa86b" />
    </radialGradient>
  </defs>

  <!-- 底座 -->
  <ellipse class="lamp-base" cx="50" cy="104" rx="20" ry="4" />
  <rect class="lamp-base" x="44" y="92" width="12" height="12" rx="2" />

  <!-- 灯杆：底座 → 关节 → 灯罩 -->
  <line class="lamp-arm" x1="50" y1="92" x2="50" y2="74" />
  <circle class="lamp-joint" cx="50" cy="74" r="3" />
  <line class="lamp-arm" x1="50" y1="74" x2="60" y2="36" />
  <circle class="lamp-joint" cx="60" cy="36" r="3" />

  <!-- 灯罩：梯形 + 描边 -->
  <path class="lamp-shade" d="M44 36 L76 36 L70 16 L50 16 Z" />
  <rect class="lamp-rim" x="46" y="36" width="28" height="2" rx="1" />
  <rect class="lamp-rim" x="46" y="14" width="28" height="2" rx="1" />

  <!-- 灯泡：圆形 + 中心高光 -->
  <circle class="lamp-bulb" cx="60" cy="26" r="5" />
  <circle cx="60" cy="26" r="5" fill="url(#lampBulbGrad)" opacity="0.7" />

  <!-- 光锥：梯形，向下投出 -->
  <polygon class="lamp-cone" points="44,38 76,38 100,108 20,108" />

  <!-- 拉绳：从灯罩底部垂下 -->
  <line class="lamp-cord" x1="60" y1="36" x2="60" y2="70" />
  <circle class="lamp-cord-bead" cx="60" cy="72" r="2" />
</svg>
    `.trim();
  }

  function injectLamp() {
    if (document.querySelector(".xueji-lamp")) return;
    const aside = document.createElement("aside");
    aside.className = "xueji-lamp";
    aside.setAttribute("role", "button");
    aside.setAttribute("aria-label", "台灯开关 · 拉绳切换");
    aside.setAttribute("tabindex", "0");
    aside.innerHTML = lampSvgMarkup();
    document.body.appendChild(aside);
  }

  function setupLamp() {
    // 还原上次状态
    try {
      if (localStorage.getItem("xueji_lamp_off") === "1") {
        document.body.classList.add("lamp-off");
      }
    } catch (e) { /* localStorage 不可用时静默 */ }

    const lamp = document.querySelector(".xueji-lamp");
    if (!lamp) return;

    const toggle = (e) => {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      // 拉绳摆动动画（先 remove 强制重启）
      lamp.classList.remove("lamp-pulling");
      void lamp.offsetWidth;
      lamp.classList.add("lamp-pulling");

      const nextOff = !document.body.classList.contains("lamp-off");
      document.body.classList.toggle("lamp-off", nextOff);
      try { localStorage.setItem("xueji_lamp_off", nextOff ? "1" : "0"); } catch (e) {}

      if (window.xuejiToast) {
        window.xuejiToast(nextOff ? "夜深了，灯熄一盏" : "灯亮了，灯下书卷开始", {
          type: nextOff ? "info" : "good",
          pos: "tr",
          dur: 1800
        });
      }
    };

    lamp.addEventListener("click", toggle);
    lamp.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") toggle(e);
    });
  }

  /* ===========================================================
   * 2.6 桌面端光标追光（仅桌面 + 细指针）
   * =========================================================== */
  function setupCursorGlow() {
    if (reducedMotion || isMobile || isWeChat) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    if (document.querySelector(".xueji-cursor")) return;

    const dot = document.createElement("div");
    dot.className = "xueji-cursor";
    dot.setAttribute("aria-hidden", "true");
    document.body.appendChild(dot);

    let x = -400, y = -400, tx = -400, ty = -400, raf = 0, armed = false;
    const onMove = (e) => {
      tx = e.clientX; ty = e.clientY;
      if (!armed) { x = tx; y = ty; dot.classList.add("is-on"); armed = true; }
    };
    const tick = () => {
      x += (tx - x) * 0.18;
      y += (ty - y) * 0.18;
      dot.style.transform = `translate(${x - 180}px, ${y - 180}px)`;
      raf = requestAnimationFrame(tick);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("mouseleave", () => { dot.classList.remove("is-on"); armed = false; });
    document.addEventListener("mouseenter", () => { dot.classList.add("is-on"); armed = true; });
    raf = requestAnimationFrame(tick);
  }

  /* ===========================================================
   * 2.7 主按钮磁吸（仅桌面 + 细指针）
   * =========================================================== */
  function setupMagneticButtons() {
    if (reducedMotion || isMobile || isWeChat) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    document.querySelectorAll("button.primary, a.primary, .btn-primary").forEach(btn => {
      btn.classList.add("is-magnetic");
      let raf = 0;
      const reset = () => { btn.style.transform = ""; };
      btn.addEventListener("pointermove", (e) => {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          const rect = btn.getBoundingClientRect();
          const dx = ((e.clientX - rect.left) / rect.width - 0.5) * 6;
          const dy = ((e.clientY - rect.top) / rect.height - 0.5) * 4;
          btn.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px)`;
        });
      });
      btn.addEventListener("pointerleave", () => { if (raf) cancelAnimationFrame(raf); reset(); });
    });
  }

  /* ===========================================================
   * 2.8 标题字符渐显（为 [data-title-letters] 的元素自动分字）
   * =========================================================== */
  function setupTitleLetters() {
    if (reducedMotion) return;
    document.querySelectorAll("[data-title-letters]").forEach(el => {
      if (el.dataset.lettered === "1") return;
      el.dataset.lettered = "1";
      const text = el.textContent || "";
      el.textContent = "";
      el.classList.add("title-letters");
      [...text].forEach((ch, i) => {
        const span = document.createElement("span");
        span.textContent = ch === " " ? "\u00A0" : ch;
        span.style.animationDelay = (i * 60) + "ms";
        el.appendChild(span);
      });
    });
  }

  /* ===========================================================
     3. Preview badge
     =========================================================== */
  function injectPreviewBadge() {
    if (!previewOn) return;
    if (document.querySelector(".preview-badge")) return;
    const badge = document.createElement("span");
    badge.className = "preview-badge";
    badge.textContent = "预览模式 · 无后端";
    badge.title = "当前为无后端预览模式，所有 /api/* 请求被模拟返回";
    document.body.appendChild(badge);
  }

  /* ===========================================================
     4. 动效引擎
     =========================================================== */
  function setupReveal() {
    if (reducedMotion) {
      document.querySelectorAll(".fx-reveal").forEach(el => el.classList.add("in"));
      return;
    }
    const targets = document.querySelectorAll("section, .today-overview, .student-card, .guide-card, .ops-card, .today-cell, .trust-item, .step, .knowledge-card, .voice-card, .card, .auth-panel, .hero");
    targets.forEach((el, idx) => {
      el.classList.add("fx-reveal");
      el.style.transitionDelay = Math.min(idx * 30, 200) + "ms";
    });
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: "-40px", threshold: 0.05 });
    targets.forEach(el => io.observe(el));
  }

  function setupCountUp() {
    if (reducedMotion) return;
    const animate = (el) => {
      const text = (el.textContent || "").trim();
      const match = text.match(/^(-?[\d,]+(?:\.\d+)?)(.*)$/);
      if (!match) return;
      const target = parseFloat(match[1].replace(/,/g, ""));
      const suffix = match[2] || "";
      if (!isFinite(target) || target === 0) return;
      const dur = 900;
      const start = performance.now();
      const from = 0;
      const tick = (now) => {
        const t = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - t, 3);
        const val = from + (target - from) * eased;
        el.textContent = (Number.isInteger(target) ? Math.round(val).toLocaleString() : val.toFixed(1)) + suffix;
        if (t < 1) requestAnimationFrame(tick);
        else el.textContent = (Number.isInteger(target) ? target.toLocaleString() : target.toFixed(1)) + suffix;
      };
      requestAnimationFrame(tick);
    };
    const candidates = document.querySelectorAll("#studentCount, #actionCount, .today-cell strong, .meter span[data-num]");
    candidates.forEach((el, idx) => {
      setTimeout(() => animate(el), 300 + idx * 80);
    });
  }

  function setupRipple() {
    if (reducedMotion) return;
    document.addEventListener("pointerdown", (e) => {
      const btn = e.target.closest("button:not(.no-ripple)");
      if (!btn || btn.disabled) return;
      const rect = btn.getBoundingClientRect();
      const r = Math.max(rect.width, rect.height);
      const ink = document.createElement("span");
      ink.className = "ripple";
      ink.style.width = ink.style.height = r + "px";
      ink.style.left = (e.clientX - rect.left - r / 2) + "px";
      ink.style.top = (e.clientY - rect.top - r / 2) + "px";
      btn.appendChild(ink);
      setTimeout(() => ink.remove(), 700);
    }, { passive: true });
  }

  function setupTilt() {
    if (reducedMotion || isMobile) return;
    const targets = document.querySelectorAll(".student-card, .ops-card, .guide-card, .today-cell, .trust-item, .knowledge-card");
    targets.forEach(el => {
      let raf = 0;
      el.addEventListener("pointermove", (e) => {
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          const rect = el.getBoundingClientRect();
          const x = ((e.clientX - rect.left) / rect.width - 0.5) * 4;
          const y = -((e.clientY - rect.top) / rect.height - 0.5) * 4;
          el.style.transform = `perspective(800px) rotateX(${y}deg) rotateY(${x}deg) translateY(-2px)`;
        });
      });
      el.addEventListener("pointerleave", () => {
        el.style.transform = "";
      });
    });
  }

  function setupMeterSheen() {
    if (reducedMotion) return;
    document.querySelectorAll(".meter span").forEach(span => {
      const pct = parseFloat(span.dataset.value || span.textContent || "0");
      if (pct) {
        setTimeout(() => { span.style.width = pct + "%"; }, 200);
      }
    });
  }

  function setupCarousel() {
    document.querySelectorAll(".row-carousel").forEach(c => {
      c.setAttribute("tabindex", "0");
      c.setAttribute("role", "region");
      c.setAttribute("aria-label", "横向滑动浏览");
    });
  }

  /* ===========================================================
   * 4.5 首屏低信息密度：横向滑动卡片轮播
   * 把每个页面 hero 区之后的核心「下一步行动」浓缩成 3-5 张横向滑卡
   * 首屏只露 1.x 张，其余靠滑动揭示 → 降低第一眼信息密度
   * =========================================================== */
  const CAROUSEL_PLANS = {
    parent: {
      eyebrow: "今日 · 一目了然",
      title: "从这一步开始",
      hint: "← 横向滑动 · 5 项待办",
      tiles: [
        { icon: "📒", name: "填写问卷", desc: "约 3 分钟", badge: "1" },
        { icon: "🌱", name: "查看计划", desc: "今日节奏", badge: "2" },
        { icon: "💬", name: "听孩子跟读", desc: "上传录音", badge: "3" },
        { icon: "🪶", name: "本周反馈", desc: "AI 已就绪" },
        { icon: "🎁", name: "邀请好友", desc: "解锁奖励" }
      ]
    },
    student: {
      eyebrow: "今天 · 灯下开始",
      title: "先做一件事",
      hint: "← 横向滑动 · 3 件小事",
      tiles: [
        { icon: "🎙", name: "今日跟读", desc: "开口即成长", badge: "1" },
        { icon: "🃏", name: "知识卡", desc: "回顾 + 1", badge: "2" },
        { icon: "📜", name: "昨日回顾", desc: "看看进步" }
      ]
    },
    admin: {
      eyebrow: "调度台",
      title: "今天要看的三件事",
      hint: "← 横向滑动",
      tiles: [
        { icon: "👥", name: "学生列表", desc: "今日活跃", badge: "12" },
        { icon: "🛠", name: "调整队列", desc: "待处理", badge: "3" },
        { icon: "✨", name: "AI 任务", desc: "运行中", badge: "5" }
      ]
    },
    default: {
      eyebrow: "快捷",
      title: "从这里开始",
      hint: "← 横向滑动",
      tiles: [
        { icon: "✨", name: "开始", desc: "进入下一步" },
        { icon: "📒", name: "记录", desc: "今日点滴" },
        { icon: "💬", name: "对话", desc: "跟 AI 聊聊" }
      ]
    }
  };

  function detectPage() {
    const path = (location.pathname || "").toLowerCase();
    if (path.includes("parent")) return "parent";
    if (path.includes("student")) return "student";
    if (path.includes("loop_tool") || path.includes("tool")) return "admin";
    return "default";
  }

  function pickCarouselHost(page) {
    const sels = {
      parent: [".service-brief", ".tab-content", "main", "body"],
      student: [".today-overview", "main", "body"],
      admin: [".workarea", "main", "body"],
      default: ["main", "body"]
    }[page] || ["main", "body"];
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return document.body;
  }

  function buildQuickCarousel() {
    const page = detectPage();
    const plan = CAROUSEL_PLANS[page] || CAROUSEL_PLANS.default;
    const host = pickCarouselHost(page);
    if (!host) return;
    if (host.querySelector(".row-carousel.xueji-quick")) return; // 防重复

    const row = document.createElement("section");
    row.className = "row-carousel xueji-quick fx-reveal";
    row.setAttribute("aria-label", "快捷操作 · 横向滑动浏览");
    row.innerHTML = `
      <div class="quick-head">
        <span class="quick-eyebrow">${plan.eyebrow}</span>
        <h3 class="quick-title">${plan.title}</h3>
        <span class="quick-hint">${plan.hint}</span>
      </div>
      <div class="quick-track">
        ${plan.tiles
          .map(
            (t, i) => `
          <article class="quick-tile" data-idx="${i}">
            <span class="quick-ico" aria-hidden="true">${t.icon}</span>
            <div class="quick-body">
              <div class="quick-name">${t.name}</div>
              <div class="quick-desc">${t.desc}</div>
            </div>
            ${t.badge ? `<span class="quick-badge">${t.badge}</span>` : ""}
          </article>`
          )
          .join("")}
      </div>
    `;
    host.appendChild(row);
  }

  /* ===========================================================
     5. Toast 简易 API
     =========================================================== */
  window.xuejiToast = function (msg, opts = {}) {
    const { type = "info", pos = "tr", dur = 2400 } = opts;
    let host = document.querySelector(".toast-host." + pos);
    if (!host) {
      host = document.createElement("div");
      host.className = "toast-host " + pos;
      document.body.appendChild(host);
    }
    const t = document.createElement("div");
    t.className = "toast " + type;
    t.textContent = msg;
    host.appendChild(t);
    setTimeout(() => {
      t.classList.add("out");
      setTimeout(() => t.remove(), 320);
    }, dur);
  };

  /* ===========================================================
     6. 初始化
     =========================================================== */
  function init() {
    injectFxBg();
    injectLamp();
    setupLamp();
    setupCursorGlow();
    setupMagneticButtons();
    setupTitleLetters();
    injectPreviewBadge();
    setupReveal();
    setupCountUp();
    setupRipple();
    setupTilt();
    setupMeterSheen();
    setupCarousel();
    buildQuickCarousel();
    if (previewOn) {
      setTimeout(() => {
        window.xuejiToast("预览模式：所有 /api/* 由本引擎模拟返回", { type: "warn", pos: "tr" });
      }, 600);
    }
    if (isWeChat) {
      console.info("[xueji] 微信内置浏览器已识别：safe-area-inset-* 已应用");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
