/* ============================================================
   学记 · 灯下书卷  v8 — fx-engine.js
   - PREVIEW_MODE：拦截 fetch 返回 503 + 占位数据（沿用 v7）
   - v8 全新：像素 RPG 视觉层
     · 标题屏 PRESS START（点击/Enter 进入主屏）
     · Canvas 像素星点背景
     · 主屏包裹 <main> 为 .frame（角色卡 / 任务日志 / 成就墙）
     · 任务日志 tab：主线 / 支线 / 图鉴 / 传说
     · 详情对话框：点击任务条目弹出，含 ◀ / ▶ / 关闭
   - 沿用：reveal / count-up / ripple / title-letters / preview-badge / toast
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
    const isApi = /\/api\//.test(path) || /\/xueji\/health$/.test(m) || /\/xueji\/api\//.test(m);
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
        await new Promise(r => setTimeout(r, 80 + Math.random() * 100));
        return mock;
      }
      return origFetch(input, init);
    };
  }

  /* ===========================================================
     2. v8 像素 RPG 视觉层
     =========================================================== */

  /* --- 2.1 像素星点背景 canvas --- */
  function injectStars() {
    if (document.querySelector(".xueji-stars")) return;
    const canvas = document.createElement("canvas");
    canvas.className = "xueji-stars";
    canvas.setAttribute("aria-hidden", "true");
    document.body.appendChild(canvas);
    if (reducedMotion) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let w = 0, h = 0, dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let stars = [];

    const COLORS = ["#FFD23F", "#5BC0EB", "#7BC950", "#FF6EC7", "#FFF8E7"];

    function resize() {
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.min(120, Math.max(60, Math.floor((w * h) / 18000)));
      stars = [];
      for (let i = 0; i < count; i++) {
        stars.push({
          x: Math.floor(Math.random() * w),
          y: Math.floor(Math.random() * h),
          size: Math.random() < 0.85 ? 1 : 2,
          c: COLORS[Math.floor(Math.random() * COLORS.length)],
          tw: Math.random() * Math.PI * 2,
          sp: 0.02 + Math.random() * 0.04
        });
      }
    }

    function tick() {
      ctx.clearRect(0, 0, w, h);
      const t = performance.now() / 1000;
      for (const s of stars) {
        const a = 0.4 + 0.6 * (0.5 + 0.5 * Math.sin(t * s.sp * 6 + s.tw));
        ctx.globalAlpha = a;
        ctx.fillStyle = s.c;
        ctx.fillRect(s.x, s.y, s.size, s.size);
      }
      ctx.globalAlpha = 1;
      requestAnimationFrame(tick);
    }

    resize();
    if (!reducedMotion) requestAnimationFrame(tick);
    else {
      ctx.clearRect(0, 0, w, h);
      ctx.globalAlpha = 0.6;
      for (const s of stars) {
        ctx.fillStyle = s.c;
        ctx.fillRect(s.x, s.y, s.size, s.size);
      }
      ctx.globalAlpha = 1;
    }
    let resizeT = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizeT);
      resizeT = setTimeout(resize, 200);
    }, { passive: true });
  }

  /* --- 2.2 标题屏（PRESS START）--- */
  function detectPageMeta() {
    const path = (location.pathname || "").toLowerCase();
    if (path.includes("parent")) return { code: "PARENT", name: "家长终端", subtitle: "PARENT · 灯下书卷" };
    if (path.includes("student")) return { code: "STUDENT", name: "学生终端", subtitle: "STUDENT · 灯下书卷" };
    if (path.includes("loop_tool") || path.includes("tool")) return { code: "ADMIN", name: "调度终端", subtitle: "ADMIN · 灯下书卷" };
    return { code: "XUEJI", name: "灯下书卷", subtitle: "XUEJI · 灯下书卷" };
  }

  function injectTitleScreen() {
    if (new URLSearchParams(location.search).get("skip-title") === "1") return;
    if (document.querySelector(".title-screen")) return;
    const meta = detectPageMeta();
    const screen = document.createElement("div");
    screen.className = "title-screen";
    screen.setAttribute("role", "button");
    screen.setAttribute("tabindex", "0");
    screen.setAttribute("aria-label", "点击开始进入 " + meta.name);
    screen.innerHTML = `
      <div class="console">
        <div class="logo">学记 · 灯下书卷</div>
        <div class="subtitle">${meta.subtitle} · v8</div>
        <div class="press-start">▶ PRESS START · 按 ENTER 开始</div>
        <div class="hint">点击屏幕 / 敲击空格 / 按 ENTER 键 · 继续冒险</div>
        <div class="copy">© 2026 灯下书卷 · RPG QUEST LOG</div>
      </div>
    `;
    document.body.appendChild(screen);
    return screen;
  }

  function setupTitleScreen() {
    const screen = document.querySelector(".title-screen");
    if (!screen) return;
    let started = false;
    const start = () => {
      if (started) return;
      started = true;
      screen.classList.add("fade-out");
      setTimeout(() => {
        screen.setAttribute("hidden", "");
        if (window.xuejiToast) {
          window.xuejiToast("冒险开始 · 灯下书卷已就绪", { type: "good", pos: "tr", dur: 1800 });
        }
      }, 380);
    };
    screen.addEventListener("click", start);
    screen.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        start();
      }
    });
    const onceKey = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        start();
        document.removeEventListener("keydown", onceKey);
      }
    };
    setTimeout(() => document.addEventListener("keydown", onceKey), 50);
  }

  /* --- 2.3 主屏包裹为 .frame --- */
  function wrapAsFrame() {
    const main = document.querySelector("main, .workarea");
    if (!main || main.classList.contains("frame")) return;
    const frame = document.createElement("div");
    frame.className = "frame";
    const top = document.createElement("div");
    top.className = "frame-top";
    const meta = detectPageMeta();
    top.textContent = "★ QUEST LOG · " + meta.name + " ★";
    const foot = document.createElement("div");
    foot.className = "frame-foot";
    foot.textContent = "v8 · 像素 RPG · 灯下书卷";
    while (main.firstChild) frame.appendChild(main.firstChild);
    main.appendChild(top);
    main.appendChild(frame);
    main.appendChild(foot);
  }

  /* --- 2.4 任务日志 tab + 详情对话框 --- */
  const QUEST_CATEGORIES = [
    { key: "all",    label: "全部",   cn: "全部" },
    { key: "main",   label: "主线",   cn: "主线" },
    { key: "side",   label: "支线",   cn: "支线" },
    { key: "codex",  label: "图鉴",   cn: "图鉴" },
    { key: "legend", label: "传说",   cn: "传说" }
  ];

  // 把现有元素按子节点位置 → 任务分类
  function classifyRow(el, indexInParent, totalInParent) {
    // 顺序：前 50% 视为主线；之后 30% 支线；最后 20% 传说
    const ratio = totalInParent <= 1 ? 0 : (indexInParent / (totalInParent - 1));
    if (ratio < 0.5) return "main";
    if (ratio < 0.8) return "side";
    return "legend";
  }

  // 过滤掉不合格的"任务条目"——例如隐藏的 tab content、纯数字标签、空标题
  function isValidQuest(el) {
    // 1) 不能在隐藏元素里
    if (el.closest('[hidden], .hidden, [style*="display: none"], [style*="display:none"]')) return false;
    // 2) 不能在 .tab-content 容器里（学生端每个标签页的内容）—— 避免重复任务
    if (el.closest('.tab-content, .tab-pane, [role="tabpanel"][aria-hidden="true"]')) return false;
    // 3) 元素可见
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    // 4) 不能是 input/button/form 本身
    if (el.matches('input, button, form, select, textarea, label, a[href]')) return false;
    return true;
  }

  // 提取更好的标题
  function pickTitle(el) {
    // 优先取 strong
    const strong = el.querySelector("strong, .quick-name, .title, .name, h1, h2, h3, h4");
    if (strong) {
      const t = strong.textContent.trim();
      if (t && t.length >= 2) return t;
    }
    // 退而取元素的纯文本（去掉数字干扰）
    const text = (el.textContent || "").replace(/\s+/g, " ").trim();
    return text.slice(0, 20);
  }

  // 描述
  function pickDesc(el) {
    const descEl = el.querySelector("p, .desc, .quick-desc, .sub, .label, span");
    return (descEl ? descEl.textContent.replace(/\s+/g, " ").trim() : "").slice(0, 80);
  }

  function buildQuestList() {
    const sels = [
      ".trust-strip > *",
      ".today-grid > *",
      ".ops-grid > *",
      ".steps > *",
      ".student-trust > *",
      ".quick-tile",
      ".guide-card",
      ".student-card",
      ".knowledge-card",
      ".voice-card"
    ];
    const seen = new Set();
    const rows = [];
    sels.forEach(sel => {
      document.querySelectorAll(sel).forEach((el) => {
        if (el.dataset.questBound === "1") return;
        if (!isValidQuest(el)) return;
        // 去重：相同 strong 文本的只取一次
        const title = pickTitle(el);
        if (!title || title.length < 2) return;
        // 过滤纯数字/单字标签（如 "0问"、"3"）
        if (/^[\d\s\.,:·、，：。]+$/.test(title)) return;
        if (seen.has(title)) return;
        seen.add(title);
        el.dataset.questBound = "1";
        el.classList.add("q-row-clickable");

        const desc = pickDesc(el);
        const parent = el.parentElement;
        const sibs = parent ? parent.children : [el];
        const idxInParent = Array.prototype.indexOf.call(sibs, el);
        const cat = classifyRow(el, idxInParent, sibs.length);
        const exp = 20 + (rows.length % 5) * 30;
        rows.push({
          el, title, desc, cat, exp,
          isDone: el.classList.contains("done") || el.dataset.done === "1",
          isPinned: el.classList.contains("pinned") || el.dataset.pinned === "1"
        });
      });
    });
    return rows;
  }

  function buildQuestSection(quests) {
    if (document.querySelector(".quest.xueji-quest-log")) return;
    const host = document.querySelector("main, .workarea") || document.body;

    const section = document.createElement("section");
    section.className = "quest xueji-quest-log";
    section.style.cssText = "padding: 16px 24px;";
    section.innerHTML = `
      <div class="tabs" data-xueji-tabs>
        ${QUEST_CATEGORIES.map((c, i) => `
          <button type="button" class="tab ${i === 0 ? "active" : ""}" data-cat="${c.key}">
            ${c.label}<span class="c">0</span>
          </button>
        `).join("")}
      </div>
      <div class="q-list" data-xueji-qlist></div>
    `;
    host.appendChild(section);

    const tabsHost = section.querySelector("[data-xueji-tabs]");
    const listHost = section.querySelector("[data-xueji-qlist]");
    function recount() {
      const counts = { all: quests.length, main: 0, side: 0, codex: 0, legend: 0 };
      quests.forEach(q => { counts[q.cat] = (counts[q.cat] || 0) + 1; });
      tabsHost.querySelectorAll(".tab").forEach(t => {
        const k = t.dataset.cat;
        const c = counts[k] || 0;
        t.querySelector(".c").textContent = c;
      });
    }
    recount();

    function render(filter) {
      const items = quests
        .map((q, i) => ({ q, i }))
        .filter(({ q }) => filter === "all" ? true : q.cat === filter);
      listHost.innerHTML = items.map(({ q, i }) => {
        const tagClass = "tag-" + q.cat;
        const tagLabel = (QUEST_CATEGORIES.find(c => c.key === q.cat) || {}).label || "主线";
        return `
          <div class="q-row" data-idx="${i}" data-qidx="${quests.indexOf(q)}">
            <span class="q-check ${q.isDone ? "" : "un"}">${q.isDone ? "✔" : "○"}</span>
            <div>
              <div class="q-title">${escapeHtml(q.title)}</div>
              <div class="q-cat">${tagLabel} · 任务 #${(quests.indexOf(q) + 1).toString().padStart(2, "0")}${q.isPinned ? '<span class="q-pin"> ★ 置顶</span>' : ""}</div>
            </div>
            <span class="d-tag ${tagClass}">${tagLabel}</span>
            <span class="q-exp">+${q.exp} EXP</span>
          </div>
        `;
      }).join("") || `<div class="q-row" style="cursor:default"><span class="q-check un">○</span><div class="q-title">当前分类暂无任务</div><span></span><span></span></div>`;
      listHost.querySelectorAll(".q-row").forEach(row => {
        if (row.dataset.bound === "1") return;
        row.dataset.bound = "1";
        row.addEventListener("click", () => {
          const idx = parseInt(row.dataset.qidx, 10);
          openDialogue(quests[idx]);
        });
      });
    }

    tabsHost.querySelectorAll(".tab").forEach(t => {
      t.addEventListener("click", () => {
        tabsHost.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
        t.classList.add("active");
        render(t.dataset.cat);
      });
    });

    render("all");
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  /* --- 2.5 详情对话框 --- */
  let dialogueOpen = false;
  let dialogueList = [];
  let dialogueIndex = 0;

  function ensureDialogueHost() {
    if (document.querySelector(".dialogue.xueji-dialogue")) return;
    const d = document.createElement("div");
    d.className = "dialogue xueji-dialogue";
    d.setAttribute("hidden", "");
    d.innerHTML = `
      <div class="d-head">
        <span class="d-name">NPC · 灯下书卷</span>
        <span class="d-tag tag-main" data-xueji-dtag>主线</span>
      </div>
      <div class="d-body" data-xueji-dbody></div>
      <div class="d-foot">
        <span class="d-next" data-xueji-dnext>▼ 按 ESC 关闭 · 方向键切换</span>
        <div class="d-actions">
          <button type="button" data-xueji-dprev>◀ 上页</button>
          <button type="button" data-xueji-dnext-btn>下页 ▶</button>
          <button type="button" data-xueji-dclose>关闭 ✕</button>
        </div>
      </div>
    `;
    document.body.appendChild(d);
    d.querySelector("[data-xueji-dprev]").addEventListener("click", () => moveDialogue(-1));
    d.querySelector("[data-xueji-dnext-btn]").addEventListener("click", () => moveDialogue(+1));
    d.querySelector("[data-xueji-dclose]").addEventListener("click", closeDialogue);
  }

  function openDialogue(item) {
    if (!item) return;
    dialogueList = Array.from(document.querySelectorAll(".xueji-quest-log .q-row"))
      .map(r => parseInt(r.dataset.qidx, 10))
      .map(i => window.__xuejiQuests ? window.__xuejiQuests[i] : null)
      .filter(Boolean);
    dialogueIndex = dialogueList.indexOf(item);
    if (dialogueIndex < 0) dialogueIndex = 0;
    renderDialogue();
  }

  function renderDialogue() {
    const d = document.querySelector(".dialogue.xueji-dialogue");
    if (!d) return;
    const item = dialogueList[dialogueIndex];
    if (!item) {
      closeDialogue();
      return;
    }
    d.removeAttribute("hidden");
    dialogueOpen = true;
    const tag = d.querySelector("[data-xueji-dtag]");
    const labelMap = { main: "主线", side: "支线", codex: "图鉴", legend: "传说" };
    tag.className = "d-tag tag-" + item.cat;
    tag.textContent = labelMap[item.cat] || "主线";
    const body = d.querySelector("[data-xueji-dbody]");
    const expLine = `<p style="color: var(--gold); font-family: var(--font-mono);">+${item.exp} EXP · 任务奖励</p>`;
    body.innerHTML = `
      <h2>${escapeHtml(item.title)}</h2>
      <p>${item.desc ? escapeHtml(item.desc) : "本任务包含一段剧情或操作说明，点击「下页」可继续浏览；按 ESC 可关闭对话框。"}${item.isDone ? '<span style="color: var(--green); margin-left: 8px;">✔ 已完成</span>' : ""}</p>
      ${expLine}
      <h3>任务指引</h3>
      <ul>
        <li>查看顶部状态条（HP / MP / EXP）确认角色状态</li>
        <li>点击主菜单「继续冒险」或「成就墙」可切换主屏</li>
        <li>不确定时回「任务日志」切换分类（主线 / 支线 / 图鉴 / 传说）</li>
      </ul>
    `;
    d.querySelector("[data-xueji-dprev]").disabled = dialogueIndex <= 0;
    d.querySelector("[data-xueji-dnext-btn]").disabled = dialogueIndex >= dialogueList.length - 1;
    d.querySelector("[data-xueji-dnext]").textContent =
      `▼ 第 ${dialogueIndex + 1} / ${dialogueList.length} 条 · ESC 关闭 · 方向键切换`;
  }

  function moveDialogue(delta) {
    if (!dialogueOpen) return;
    const next = dialogueIndex + delta;
    if (next < 0 || next >= dialogueList.length) return;
    dialogueIndex = next;
    renderDialogue();
  }

  function closeDialogue() {
    const d = document.querySelector(".dialogue.xueji-dialogue");
    if (!d) return;
    d.setAttribute("hidden", "");
    dialogueOpen = false;
  }

  document.addEventListener("keydown", (e) => {
    if (!dialogueOpen) return;
    if (e.key === "Escape") { closeDialogue(); e.preventDefault(); return; }
    if (e.key === "ArrowLeft") { moveDialogue(-1); e.preventDefault(); }
    if (e.key === "ArrowRight") { moveDialogue(+1); e.preventDefault(); }
  });

  /* ===========================================================
     3. Preview 徽章
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
     4. 动效引擎（沿用 v7）
     =========================================================== */
  function setupReveal() {
    if (reducedMotion) {
      document.querySelectorAll(".fx-reveal").forEach(el => el.classList.add("in"));
      return;
    }
    const targets = document.querySelectorAll("section, .today-overview, .student-card, .guide-card, .ops-card, .today-cell, .trust-item, .step, .knowledge-card, .voice-card, .card, .auth-panel, .hero, .quest");
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

  function setupMeterSheen() {
    if (reducedMotion) return;
    document.querySelectorAll(".meter span").forEach(span => {
      const pct = parseFloat(span.dataset.value || span.textContent || "0");
      if (pct) {
        setTimeout(() => { span.style.width = pct + "%"; }, 200);
      }
    });
  }

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

  function setupCarousel() {
    document.querySelectorAll(".row-carousel").forEach(c => {
      c.setAttribute("tabindex", "0");
      c.setAttribute("role", "region");
      c.setAttribute("aria-label", "横向滑动浏览");
    });
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
    injectStars();
    injectTitleScreen();
    setupTitleScreen();
    injectPreviewBadge();
    ensureDialogueHost();
    setupReveal();
    setupCountUp();
    setupRipple();
    setupMeterSheen();
    setupTitleLetters();
    setupCarousel();

    setTimeout(() => {
      try {
        wrapAsFrame();
        const quests = buildQuestList();
        window.__xuejiQuests = quests;
        if (quests.length > 0) {
          buildQuestSection(quests);
        } else {
          // 没有任何候选任务时，注入一段 RPG 教程任务
          const seed = [
            { title: "认识灯下书卷", desc: "学记是游戏化学习任务系统，把学习包装成主线 / 支线 / 图鉴 / 传说四类任务。", cat: "main", exp: 50 },
            { title: "完成今日 5 步学习流", desc: "跟读 / 记忆故事 / 知识卡 / 往日回顾 / 今日打卡，每天 5 步。", cat: "main", exp: 80 },
            { title: "查看家长端计划", desc: "家长端会按 5 科路径排计划，确认后再生成知识卡。", cat: "side", exp: 30 },
            { title: "知识图鉴", desc: "复习 D1 / D3 / D7 间隔循环，错卡优先回炉。", cat: "codex", exp: 60 },
            { title: "每日反馈", desc: "记录完成数、会了几张、情绪和★卡，决定下一版调整。", cat: "side", exp: 40 },
            { title: "邀请好友", desc: "邀请其他家长加入，双方各得 7 天权益。", cat: "legend", exp: 200 }
          ];
          window.__xuejiQuests = seed;
          buildQuestSection(seed);
        }
      } catch (err) {
        console.warn("[xueji] v8 frame/quest 初始化失败：", err);
      }
    }, 60);

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
