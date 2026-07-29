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
     0. 占位数据 + fetch 拦截
     =========================================================== */
  const previewFlag = new URLSearchParams(location.search).get("preview");
  const previewOn = previewFlag !== "0" && localStorage.getItem("xueji_preview_mode") !== "0";
  if (previewOn) localStorage.setItem("xueji_preview_mode", "1");

  function pickApiMock(url, method) {
    const u = String(url || "").toLowerCase();
    if (u.includes("/health")) return { code: 0, data: { status: "ok", mode: "preview" } };
    if (u.includes("/login") || u.includes("/auth")) return { code: 0, data: { token: "preview-token", user: { id: "u-001", name: "预览用户" } } };
    if (u.includes("/user")) return { code: 0, data: { id: "u-001", name: "预览用户", role: "parent" } };
    if (u.includes("/children") || u.includes("/students")) return { code: 0, data: { items: [
      { id: "s-001", name: "小明", grade: "三年级", avatar: "明" },
      { id: "s-002", name: "小红", grade: "五年级", avatar: "红" }
    ]}};
    if (u.includes("/progress") || u.includes("/learning")) return { code: 0, data: { items: [
      { id: "p-001", subject: "语文", progress: 72, updated: "今天" },
      { id: "p-002", subject: "数学", progress: 85, updated: "昨天" },
      { id: "p-003", subject: "英语", progress: 64, updated: "2 天前" }
    ]}};
    if (u.includes("/notice") || u.includes("/announce")) return { code: 0, data: { items: [
      { id: "n-001", title: "家长会通知", time: "今天 10:00" },
      { id: "n-002", title: "期中复习安排", time: "昨天" }
    ]}};
    if (u.includes("/task") || u.includes("/todo")) return { code: 0, data: { items: [
      { id: "t-001", title: "检查作业", status: "pending" },
      { id: "t-002", title: "签到", status: "done" }
    ]}};
    if (u.includes("/metric") || u.includes("/stats") || u.includes("/kpi")) return { code: 0, data: {
      items: [
        { label: "本周学习", value: "18.5h", delta: "+12%" },
        { label: "完成率", value: "92%", delta: "+4%" },
        { label: "待办", value: "3", delta: "-1" }
      ]
    }};
    return { code: 0, data: { items: [] } };
  }

  function isApiCall(url) {
    const u = String(url || "");
    return /^https?:\/\//i.test(u) && !u.includes(location.host);
  }

  if (previewOn && !isApiCall("")) {
    const _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      if (isApiCall(url)) {
        return new Promise((resolve) => {
          setTimeout(() => {
            const mock = pickApiMock(url, (init && init.method) || "GET");
            resolve(new Response(JSON.stringify(mock), {
              status: 200,
              headers: { "Content-Type": "application/json" }
            }));
          }, 80);
        });
      }
      return _fetch(input, init);
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
