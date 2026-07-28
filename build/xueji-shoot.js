#!/usr/bin/env node
/* eslint-disable */
process.env.NODE_PATH = require("path").join(require("os").homedir(), ".npm-global", "lib", "node_modules");
require("module").Module._initPaths();
const http = require("http");
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "screenshots");
const PORT = 8766;
const PAGES = [
  { name: "parent", file: "xueji_parent_h5.html" },
  { name: "student", file: "xueji_student_h5.html" },
  { name: "admin", file: "xueji_loop_tool_api.html" }
];

const MIME = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript", ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon" };

const srv = http.createServer((req, res) => {
  const url = req.url === "/" ? "/xueji_parent_h5.html" : req.url.split("?")[0];
  const fp = path.join(ROOT, url);
  if (!fp.startsWith(ROOT) || !fs.existsSync(fp)) {
    res.writeHead(404); res.end("not found"); return;
  }
  const ext = path.extname(fp);
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(fp).pipe(res);
});
srv.listen(PORT, "127.0.0.1", async () => {
  console.log("[srv] up on 127.0.0.1:" + PORT);
  const browser = await puppeteer.launch({
    executablePath: "/opt/chromium.org/chromium/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage"]
  });

  // 1. 标准视口：1440 / 1024 / 390 —— 主屏（跳过标题屏）
  const standard = [
    { name: "1440", viewport: { width: 1440, height: 900 } },
    { name: "1024", viewport: { width: 1024, height: 768 } },
    { name: "390",  viewport: { width: 390,  height: 844 }, isMobile: true, ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1" },
    { name: "wechat", viewport: { width: 390, height: 844 }, isMobile: true, ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003130) NetType/WIFI Language/zh_CN" },
    { name: "reduced", viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" }
  ];

  for (const p of PAGES) {
    for (const v of standard) {
      const ctx = await browser.createBrowserContext();
      const page = await ctx.newPage();
      if (v.isMobile) {
        await page.setViewport({ ...v.viewport, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
        if (v.ua) await page.setUserAgent(v.ua);
      } else {
        await page.setViewport(v.viewport);
      }
      if (v.reducedMotion) {
        await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
      }
      // 用 skip-title=1 跳过标题屏，直接看主屏
      await page.goto(`http://127.0.0.1:${PORT}/${p.file}?skip-title=1`, { waitUntil: "load" });
      await new Promise(r => setTimeout(r, 1500));
      await page.screenshot({ path: path.join(OUT, `${p.name}__${v.name}.png`), fullPage: false });
      console.log(`[shoot] ${p.name} @ ${v.name} -> ${path.join(OUT, p.name + "__" + v.name + ".png")}`);
      await ctx.close();
    }
  }

  // 1.5 标题屏（PRESS START）特写 —— 只截 1440 与 390
  for (const p of PAGES) {
    for (const v of [{ name: "title", viewport: { width: 1440, height: 900 } }]) {
      const ctx = await browser.createBrowserContext();
      const page = await ctx.newPage();
      await page.setViewport(v.viewport);
      await page.goto(`http://127.0.0.1:${PORT}/${p.file}`, { waitUntil: "load" });
      await new Promise(r => setTimeout(r, 800));
      await page.screenshot({ path: path.join(OUT, `${p.name}__${v.name}.png`), fullPage: false });
      console.log(`[shoot] ${p.name} @ ${v.name} -> ${path.join(OUT, p.name + "__" + v.name + ".png")}`);
      await ctx.close();
    }
  }

  // 2. 登录后截：先预置 token 再重载
  const ctx2 = await browser.createBrowserContext();
  const page2 = await ctx2.newPage();
  await page2.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });

  // 家长端
  await page2.goto(`http://127.0.0.1:${PORT}/xueji_parent_h5.html?skip-title=1`, { waitUntil: "load" });
  await page2.evaluate(() => {
    localStorage.setItem("xueji_parent_token", "preview-parent-token");
  });
  await page2.reload({ waitUntil: "load" });
  await new Promise(r => setTimeout(r, 2500));
  await page2.screenshot({ path: path.join(OUT, "parent__1440__loggedin.png") });
  console.log("[shoot] parent__1440__loggedin");

  // 学生端
  await page2.goto(`http://127.0.0.1:${PORT}/xueji_student_h5.html?skip-title=1`, { waitUntil: "load" });
  await page2.evaluate(() => {
    localStorage.setItem("xueji_student_token", "preview-student-token");
    localStorage.setItem("xueji_student_id", "1");
  });
  await page2.reload({ waitUntil: "load" });
  await new Promise(r => setTimeout(r, 2500));
  await page2.evaluate(() => window.scrollTo({ top: 320, behavior: "instant" }));
  await new Promise(r => setTimeout(r, 800));
  await page2.screenshot({ path: path.join(OUT, "student__1440__loggedin.png") });
  console.log("[shoot] student__1440__loggedin");

  // 学生端 mobile loggedin
  const mctx = await browser.createBrowserContext();
  const mpage = await mctx.newPage();
  await mpage.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await mpage.setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1");
  await mpage.goto(`http://127.0.0.1:${PORT}/xueji_student_h5.html?skip-title=1`, { waitUntil: "load" });
  await mpage.evaluate(() => {
    localStorage.setItem("xueji_student_token", "preview-student-token");
    localStorage.setItem("xueji_student_id", "1");
  });
  await mpage.reload({ waitUntil: "load" });
  await new Promise(r => setTimeout(r, 2500));
  await mpage.screenshot({ path: path.join(OUT, "student__390__loggedin.png") });
  console.log("[shoot] student__390__loggedin");
  await mctx.close();

  // 后台
  await page2.goto(`http://127.0.0.1:${PORT}/xueji_loop_tool_api.html?skip-title=1`, { waitUntil: "load" });
  await new Promise(r => setTimeout(r, 2000));
  await page2.screenshot({ path: path.join(OUT, "admin__1440__main.png") });
  console.log("[shoot] admin__1440__main");

  await ctx2.close();
  await browser.close();
  srv.close();
  console.log("[done] all screenshots saved to", OUT);
});
