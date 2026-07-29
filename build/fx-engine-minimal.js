/* ============================================================
   学记 · 灯下书卷  v8.4 — fx-engine-minimal.js
   仅家长端 / 管理端启用（极简现代·卡片式主题）
   - 保留：PREVIEW_MODE fetch 拦截（沿用 v7 占位数据）
   - 保留：reveal / count-up / ripple / toast
   - 移除：标题屏 PRESS START（无像素入场）
   - 移除：Canvas 像素星点背景（改为极简无背景）
   - 移除：wrapAsFrame 包裹（极简主题不需要 frame 容器）
   - 移除：tab 切换的 RPG 任务日志术语（主线/支线/图鉴/传说）
   ============================================================ */

(function () {
  "use strict";
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isMobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);

  /* ===========================================================
     0. 占位数据 + fetch 拦截（v8.6：统一 mock 层，拦截相对 /api/* 路径）
     =========================================================== */
  const previewFlag = new URLSearchParams(location.search).get("preview");
  const previewOn = previewFlag !== "0" && localStorage.getItem("xueji_preview_mode") !== "0";
  if (previewOn) localStorage.setItem("xueji_preview_mode", "1");
  // 预览模式自动填充 admin key，跳过口令弹窗
  if (previewOn && !localStorage.getItem("xueji_admin_key")) {
    localStorage.setItem("xueji_admin_key", "preview-admin-key");
  }

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
    const isApi = /\/api\//.test(path) || /\/health$/.test(path) || /\/xueji\/health$/.test(m) || /\/xueji\/api\//.test(m);
    if (!isApi) return null;

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

    // 后台 v8.6 补充：admin 实际路径（/api/students, /api/admin/...）
    if (/\/api\/students$/.test(path) && method === "GET") {
      return placeholder.ok({ items: adminStudentsMock() });
    }
    if (/\/api\/admin\/parents/.test(path)) {
      return placeholder.ok({ items: adminParentsMock() });
    }
    if (/\/api\/admin\/adjustments/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/ai-jobs/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/entitlement-presets/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/model-configs/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/voice-practice\/samples/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/learning-resources/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/knowledge-points/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/exam-patterns/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/learning-coverage/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/learning-collection-tasks/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/learning-collection-task-records/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/learning-scope-index/.test(path)) {
      return placeholder.ok({ items: [] });
    }
    if (/\/api\/admin\/voice-assessment\/calibrate/.test(path)) {
      return placeholder.ok({ status: "ok", message: "预览模式：校准完成" });
    }
    if (/\/api\/students\/[^/]+\/weekly-report/.test(path)) {
      return placeholder.ok({ status: "ok", message: "预览模式：周报已生成" });
    }
    if (/\/api\/plans\/[^/]+\/publish/.test(path)) {
      return placeholder.ok({ status: "ok", message: "预览模式：方案已发布" });
    }
    if (/\/api\/students\/intake/.test(path)) {
      return placeholder.ok({ student: { id: 1, display_name: "预览同学", student_code: "PREVIEW-001" } });
    }
    if (/\/api\/students\/[^/]+\/daily-feedback/.test(path)) {
      return placeholder.ok({ status: "saved", feedback_id: "preview-fb-" + Date.now() });
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
     0.1 拦截 fetch（拦截相对 /api/* 路径，返回 Response-like mock）
     =========================================================== */
  if (previewOn && !window.__xuejiFetchPatched) {
    window.__xuejiFetchPatched = true;
    const origFetch = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      let url = typeof input === "string" ? input : (input && input.url) || "";
      const method = (init && init.method) || (input && input.method) || "GET";
      const mock = pickApiMock(url, method);
      if (mock) {
        await new Promise(r => setTimeout(r, 80 + Math.random() * 100));
        return mock;
      }
      return origFetch(input, init);
    };
  }

  /* ===========================================================
     1. Reveal-on-scroll（极简：淡入 0→1）
     =========================================================== */
  function setupReveal() {
    const items = document.querySelectorAll("[data-reveal], .reveal, .card, .section, .panel");
    if (!items.length) return;
    if (reducedMotion || !("IntersectionObserver" in window)) {
      items.forEach((el) => el.classList.add("is-revealed"));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add("is-revealed");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
    items.forEach((el) => io.observe(el));
  }

  /* ===========================================================
     2. Count-up 数字滚动
     =========================================================== */
  function setupCountUp() {
    const els = document.querySelectorAll("[data-count], .count-up");
    if (!els.length) return;
    if (reducedMotion) {
      els.forEach((el) => el.textContent = el.dataset.count || el.textContent);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const el = e.target;
        const target = parseFloat(el.dataset.count || el.textContent);
        if (isNaN(target)) return;
        const decimals = (el.dataset.count || "").includes(".") ? 1 : 0;
        const duration = 900;
        const start = performance.now();
        const tick = (t) => {
          const p = Math.min((t - start) / duration, 1);
          const ease = 1 - Math.pow(1 - p, 3);
          el.textContent = (target * ease).toFixed(decimals);
          if (p < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
        io.unobserve(el);
      });
    }, { threshold: 0.4 });
    els.forEach((el) => io.observe(el));
  }

  /* ===========================================================
     3. Toast
     =========================================================== */
  function ensureToastContainer() {
    let c = document.querySelector(".toast-container");
    if (c) return c;
    c = document.createElement("div");
    c.className = "toast-container";
    document.body.appendChild(c);
    return c;
  }
  window.toast = function (msg, type) {
    const c = ensureToastContainer();
    const el = document.createElement("div");
    el.className = "toast" + (type ? " toast-" + type : "");
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateX(20px)";
      el.style.transition = "all .3s";
      setTimeout(() => el.remove(), 320);
    }, 2400);
  };

  /* ===========================================================
     4. Ripple 按钮波纹（极简：细圆点扩散）
     =========================================================== */
  function setupRipple() {
    if (reducedMotion) return;
    document.addEventListener("click", (e) => {
      const t = e.target.closest(".btn, button.btn, [data-ripple]");
      if (!t) return;
      const rect = t.getBoundingClientRect();
      const r = document.createElement("span");
      const size = Math.max(rect.width, rect.height);
      r.style.cssText = `
        position:absolute; left:${e.clientX - rect.left - size/2}px; top:${e.clientY - rect.top - size/2}px;
        width:${size}px;height:${size}px;border-radius:50%;background:currentColor;opacity:.18;
        transform:scale(0);transition:transform .5s,opacity .6s;pointer-events:none;
      `;
      const oldPos = getComputedStyle(t).position;
      if (oldPos === "static") t.style.position = "relative";
      t.style.overflow = "hidden";
      t.appendChild(r);
      requestAnimationFrame(() => {
        r.style.transform = "scale(2.2)";
        r.style.opacity = "0";
      });
      setTimeout(() => r.remove(), 600);
    });
  }

  /* ===========================================================
     5. Reveal 入场 CSS（极简版 · 通过 data-attr 或 .reveal 触发）
     =========================================================== */
  function injectRevealCSS() {
    if (document.getElementById("xueji-minimal-reveal")) return;
    const s = document.createElement("style");
    s.id = "xueji-minimal-reveal";
    s.textContent = `
      .reveal:not(.is-revealed), [data-reveal]:not(.is-revealed) {
        opacity: 0; transform: translateY(8px);
        transition: opacity .5s ease-out, transform .5s ease-out;
      }
      .reveal.is-revealed, [data-reveal].is-revealed {
        opacity: 1; transform: none;
      }
      .card, .section, .panel { will-change: opacity, transform; }
    `;
    document.head.appendChild(s);
  }

  /* ===========================================================
     6. 启动
     =========================================================== */
  function boot() {
    injectRevealCSS();
    setupReveal();
    setupCountUp();
    setupRipple();

    // 渲染完成事件（供页面脚本接入）
    window.dispatchEvent(new CustomEvent("xueji:ready", { detail: { theme: "minimal" } }));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
