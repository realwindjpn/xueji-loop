#!/usr/bin/env node
process.env.NODE_PATH = require("path").join(require("os").homedir(), ".npm-global", "lib", "node_modules");
require("module").Module._initPaths();
const fs = require("fs"), http = require("http"), path = require("path");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "..");
const PORT = 8778;
const exe = [process.env.CHROME_PATH, "/opt/chromium.org/chromium/chrome"].filter(Boolean).find(p => { try { return fs.existsSync(p); } catch (_) { return false; } });
const srv = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split("?")[0]);
  const f = path.resolve(ROOT, p.replace(/^\//, ""));
  if (!f.startsWith(ROOT) || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript" }[path.extname(f)] || "application/octet-stream" });
  fs.createReadStream(f).pipe(res);
});
const SHOTS = path.join(ROOT, "screenshots", "v84-interactive");
fs.mkdirSync(SHOTS, { recursive: true });

const SHOTS_PLAN = [
  // 1366 常见笔记本视口
  { page: "parent", file: "xueji_parent_h5.html", w: 1366, h: 800, name: "parent-1366", actions: [] },
  { page: "admin",  file: "xueji_loop_tool_api.html", w: 1366, h: 800, name: "admin-1366", actions: [] },
  // 平板 768
  { page: "parent", file: "xueji_parent_h5.html", w: 768, h: 1024, name: "parent-768", actions: [] },
  { page: "admin",  file: "xueji_loop_tool_api.html", w: 768, h: 1024, name: "admin-768", actions: [] },
  // 移动 390
  { page: "parent", file: "xueji_parent_h5.html", w: 390, h: 844, name: "parent-390", isMobile: true, actions: [] },
  { page: "admin",  file: "xueji_loop_tool_api.html", w: 390, h: 844, name: "admin-390", isMobile: true, actions: [] },
  // 学生端 1366
  { page: "student", file: "xueji_student_h5.html", w: 1366, h: 800, name: "student-1366", actions: [] },
  // 交互：家长端点击"知识卡片"tab
  { page: "parent", file: "xueji_parent_h5.html", w: 1366, h: 800, name: "parent-cards-tab", actions: [{ click: '[data-view="cards"]' }] },
  // 交互：家长端"语音练习"tab
  { page: "parent", file: "xueji_parent_h5.html", w: 1366, h: 800, name: "parent-voice-tab", actions: [{ click: '[data-view="voice"]' }] },
  // 交互：管理端"学生列表"tab
  { page: "admin", file: "xueji_loop_tool_api.html", w: 1366, h: 800, name: "admin-students-tab", actions: [{ click: '[data-view="students"]' }] },
  // 交互：管理端"AI任务队列"tab
  { page: "admin", file: "xueji_loop_tool_api.html", w: 1366, h: 800, name: "admin-ai-tab", actions: [{ click: '[data-view="aiJobs"]' }] },
  // 交互：管理端"模型配置"tab
  { page: "admin", file: "xueji_loop_tool_api.html", w: 1366, h: 800, name: "admin-models-tab", actions: [{ click: '[data-view="models"]' }] },
  // 学生端跳过标题屏看仪表盘
  { page: "student", file: "xueji_student_h5.html", w: 1366, h: 800, name: "student-1366-dashboard", actions: [], skipTitle: true },
  // 学生端 1920 大屏
  { page: "student", file: "xueji_student_h5.html", w: 1920, h: 1080, name: "student-1920-dashboard", actions: [], skipTitle: true },
];

(async () => {
  await new Promise(r => srv.listen(PORT, "127.0.0.1", r));
  const b = await puppeteer.launch({ executablePath: exe, headless: true, args: ["--no-sandbox"] });
  for (const plan of SHOTS_PLAN) {
    const ctx = await b.createBrowserContext();
    const pg = await ctx.newPage();
    await pg.setViewport({
      width: plan.w, height: plan.h,
      deviceScaleFactor: plan.isMobile ? 2 : 1,
      isMobile: Boolean(plan.isMobile),
      hasTouch: Boolean(plan.isMobile)
    });
    const skip = plan.skipTitle ? "&skip-title=1" : "";
    await pg.goto(`http://127.0.0.1:${PORT}/${plan.file}?_=${Date.now()}${skip}`, { waitUntil: "load", timeout: 30000 });
    await new Promise(r => setTimeout(r, 1500));
    for (const a of plan.actions || []) {
      if (a.click) {
        try {
          await pg.click(a.click);
          await new Promise(r => setTimeout(r, 400));
        } catch (e) {
          console.warn("click failed:", a.click, e.message);
        }
      }
    }
    await new Promise(r => setTimeout(r, 600));
    const out = path.join(SHOTS, `${plan.name}.png`);
    await pg.screenshot({ path: out, fullPage: true });
    console.log("[shot]", plan.name, "->", out);
    await ctx.close();
  }
  await b.close();
  srv.close();
  console.log("[done]");
})().catch((e) => { console.error(e); process.exit(1); });
