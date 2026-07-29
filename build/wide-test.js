#!/usr/bin/env node
process.env.NODE_PATH = require("path").join(require("os").homedir(), ".npm-global", "lib", "node_modules");
require("module").Module._initPaths();
const fs = require("fs"), http = require("http"), path = require("path");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "..");
const PORT = 8776;
const exe = [process.env.CHROME_PATH, "/opt/chromium.org/chromium/chrome"].filter(Boolean).find(p => { try { return fs.existsSync(p); } catch (_) { return false; } });
const srv = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split("?")[0]);
  const f = path.resolve(ROOT, p.replace(/^\//, ""));
  if (!f.startsWith(ROOT) || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript" }[path.extname(f)] || "application/octet-stream" });
  fs.createReadStream(f).pipe(res);
});
(async () => {
  await new Promise(r => srv.listen(PORT, "127.0.0.1", r));
  const b = await puppeteer.launch({ executablePath: exe, headless: true, args: ["--no-sandbox"] });
  for (const vw of [1280, 1440, 1920]) {
    const ctx = await b.createBrowserContext();
    const pg = await ctx.newPage();
    await pg.setViewport({ width: vw, height: 900 });
    await pg.goto(`http://127.0.0.1:${PORT}/xueji_student_h5.html?skip-title=1&_=${Date.now()}`, { waitUntil: "load" });
    await new Promise(r => setTimeout(r, 1500));
    const out = path.join(ROOT, "screenshots", "wide-fix", `student__${vw}__login.png`);
    await pg.screenshot({ path: out, fullPage: true });
    console.log("[shot]", out);
    await ctx.close();
  }
  await b.close();
  srv.close();
})();
