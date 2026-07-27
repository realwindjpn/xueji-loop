#!/usr/bin/env node
process.env.NODE_PATH = require("path").join(process.env.HOME, ".npm-global", "lib", "node_modules");
require("module").Module._initPaths();
const http = require("http");
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const ROOT = path.resolve(__dirname, "..");
const PORT = 8767;
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
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on("console", m => console.log("[browser]", m.type(), m.text().slice(0, 200)));
  page.on("pageerror", e => console.log("[pageerror]", e.message));
  // Prime localStorage BEFORE the page scripts read it
  await page.goto("http://127.0.0.1:" + PORT + "/xueji_parent_h5.html", { waitUntil: "load" });
  await page.evaluate(() => {
    localStorage.setItem("xueji_parent_token", "preview-parent-token");
    localStorage.setItem("xueji_student_token", "");
  });
  await page.reload({ waitUntil: "load" });
  await new Promise(r => setTimeout(r, 2500));

  const stats = await page.evaluate(() => ({
    quickFound: !!document.querySelector(".row-carousel.xueji-quick"),
    quickTileCount: document.querySelectorAll(".row-carousel.xueji-quick .quick-tile").length,
    bodyClass: document.body.className,
    activeTab: document.querySelector(".tab.active")?.textContent?.trim() || "none",
    fxBg: !!document.querySelector(".fx-bg"),
    badge: !!document.querySelector(".preview-badge"),
  }));
  console.log("[stats]", JSON.stringify(stats, null, 2));

  await page.screenshot({ path: "/tmp/dbg-parent-loggedin.png", fullPage: false });
  console.log("[done] /tmp/dbg-parent-loggedin.png");
  await browser.close();
  srv.close();
});
