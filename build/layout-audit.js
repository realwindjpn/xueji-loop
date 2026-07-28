#!/usr/bin/env node
/* eslint-disable */
/**
 * xueji layout-audit · 四视口溢出 + 控制台 + 页签交互自动检查
 *
 * 用法：node build/layout-audit.js
 * 依赖：puppeteer-core + Chrome/Chromium
 * 输出：screenshots/layout-audit/<page>__<viewport>.png + report.json
 */
process.env.NODE_PATH = require("path").join(require("os").homedir(), ".npm-global", "lib", "node_modules");
require("module").Module._initPaths();
const fs = require("fs");
const http = require("http");
const path = require("path");
const puppeteer = require("puppeteer-core");

const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "screenshots", "layout-audit");
const PORT = 8768;
const PAGES = [
  { name: "parent", file: "xueji_parent_h5.html", tokenKey: "xueji_parent_token", token: "preview-parent-token" },
  { name: "student", file: "xueji_student_h5.html", tokenKey: "xueji_student_token", token: "preview-student-token", extra: { xueji_student_id: "1" } },
  { name: "admin", file: "xueji_loop_tool_api.html" }
];
const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1024", width: 1024, height: 768 },
  { name: "720",  width: 720,  height: 900 },
  { name: "390",  width: 390,  height: 844, isMobile: true }
];

const chromeCandidates = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "/opt/chromium.org/chromium/chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/snap/bin/chromium"
].filter(Boolean);
const executablePath = chromeCandidates.find(p => { try { return fs.existsSync(p); } catch (_) { return false; } });
if (!executablePath) {
  throw new Error("未找到 Chrome/Chromium；请设置 CHROME_PATH。候选路径：" + chromeCandidates.join(", "));
}

fs.mkdirSync(OUT, { recursive: true });
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon"
};

const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(req.url.split("?")[0]);
  const relative = pathname === "/" ? PAGES[0].file : pathname.replace(/^\//, "");
  const file = path.resolve(ROOT, relative);
  if (!file.startsWith(ROOT) || !fs.existsSync(file)) {
    res.writeHead(404); res.end("not found"); return;
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});

async function auditPage(browser, file, tokenKey, token, extra, viewport) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  const messages = [];
  page.on("console", m => {
    if (["error", "warning"].includes(m.type())) messages.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", err => messages.push(`pageerror: ${err.message}`));

  await page.setViewport({
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.isMobile ? 2 : 1,
    isMobile: Boolean(viewport.isMobile),
    hasTouch: Boolean(viewport.isMobile)
  });
  await page.goto(`http://127.0.0.1:${PORT}/${file}?skip-title=1`, { waitUntil: "load" });

  if (tokenKey) {
    await page.evaluate(({ k, v, e }) => {
      localStorage.setItem(k, v);
      for (const [ek, ev] of Object.entries(e || {})) localStorage.setItem(ek, ev);
    }, { k: tokenKey, v: token, e: extra });
    await page.reload({ waitUntil: "load" });
  }

  await new Promise(r => setTimeout(r, 700));

  const navTargets = await page.$$eval(
    "[data-view]",
    nodes => [...new Set(nodes.map(n => n.dataset.view).filter(Boolean))]
  );
  for (const target of navTargets) {
    const btn = await page.$(`[data-view="${target}"]`);
    if (!btn) continue;
    try {
      await btn.click();
      await new Promise(r => setTimeout(r, 80));
      const state = await page.evaluate(id => {
        const s = document.getElementById(id);
        if (!s) return { exists: false, active: false };
        return { exists: true, active: s.classList.contains("active") || s.style.display !== "none" };
      }, target);
      if (!state.exists) messages.push(`interaction: data-view=${target} section not found`);
    } catch (e) {
      messages.push(`interaction: data-view=${target} click failed: ${e.message}`);
    }
  }

  const layout = await page.evaluate(() => {
    const root = document.documentElement;
    const viewportWidth = root.clientWidth;
    const offenders = [];
    for (const el of document.querySelectorAll("body *")) {
      if (el.closest(".table-scroll") || el.tagName === "PRE" || el.tagName === "CODE") continue;
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const rect = el.getBoundingClientRect();
      const exceedsViewport = rect.left < -1 || rect.right > viewportWidth + 1;
      const visibleOverflow = el.scrollWidth > el.clientWidth + 1
        && !["auto", "scroll", "hidden", "clip"].includes(style.overflowX)
        && el.clientWidth > 0;
      if (exceedsViewport || visibleOverflow) {
        offenders.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || "",
          className: String(el.className || "").slice(0, 100),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          clientWidth: el.clientWidth,
          scrollWidth: el.scrollWidth
        });
      }
      if (offenders.length >= 30) break;
    }
    return {
      rootClientWidth: root.clientWidth,
      rootScrollWidth: root.scrollWidth,
      rootOverflow: root.scrollWidth > root.clientWidth + 1,
      offenders
    };
  });

  await page.screenshot({
    path: path.join(OUT, `${path.basename(file, ".html")}__${viewport.name}.png`),
    fullPage: false
  });
  await ctx.close();
  return { file: path.basename(file), viewport: viewport.name, messages, layout };
}

server.listen(PORT, "127.0.0.1", async () => {
  console.log("[audit] server up on 127.0.0.1:" + PORT);
  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"]
  });
  const results = [];
  try {
    for (const p of PAGES) {
      for (const v of VIEWPORTS) {
        results.push(await auditPage(browser, p.file, p.tokenKey, p.token, p.extra, v));
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(results, null, 2));
  console.log("\n[audit] results:");
  for (const r of results) {
    const ok = !r.layout.rootOverflow && r.layout.offenders.length === 0
      && !r.messages.some(m => m.startsWith("pageerror"));
    console.log(`  ${ok ? "✓" : "✗"} ${r.file} @ ${r.viewport}: rootOverflow=${r.layout.rootOverflow} offenders=${r.layout.offenders.length} msgs=${r.messages.length}`);
  }
  const failed = results.filter(r => r.layout.rootOverflow || r.layout.offenders.length > 0
    || r.messages.some(m => m.startsWith("pageerror")));
  if (failed.length) {
    console.error(`\n[audit] FAILED: ${failed.length}/${results.length} viewport(s)`);
    process.exitCode = 1;
  } else {
    console.log("\n[audit] PASSED");
  }
});
