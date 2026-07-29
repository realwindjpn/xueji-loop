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

const SHOTS_DIR = path.join(ROOT, "screenshots", "v84-minimal");
fs.mkdirSync(SHOTS_DIR, { recursive: true });

// 每页 × 多个视口
const PAGES = [
  { name: "parent", file: "xueji_parent_h5.html" },
  { name: "admin",  file: "xueji_loop_tool_api.html" },
  { name: "student", file: "xueji_student_h5.html" },
];
const VIEWPORTS = [
  { w: 1280, h: 800, tag: "1280" },
  { w: 1440, h: 900, tag: "1440" },
  { w: 1920, h: 1080, tag: "1920" },
];

(async () => {
  await new Promise(r => srv.listen(PORT, "127.0.0.1", r));
  const b = await puppeteer.launch({ executablePath: exe, headless: true, args: ["--no-sandbox"] });
  for (const pgDef of PAGES) {
    for (const vw of VIEWPORTS) {
      const ctx = await b.createBrowserContext();
      const pg = await ctx.newPage();
      await pg.setViewport({ width: vw.w, height: vw.h, deviceScaleFactor: 1 });
      const url = `http://127.0.0.1:${PORT}/${pgDef.file}?_=${Date.now()}`;
      await pg.goto(url, { waitUntil: "load", timeout: 30000 });
      await new Promise(r => setTimeout(r, 1500));
      const out = path.join(SHOTS_DIR, `${pgDef.name}__${vw.tag}__top.png`);
      await pg.screenshot({ path: out, fullPage: false });
      const outFull = path.join(SHOTS_DIR, `${pgDef.name}__${vw.tag}__full.png`);
      await pg.screenshot({ path: outFull, fullPage: true });
      console.log(`[shot] ${pgDef.name} ${vw.tag} -> ${out}`);
      await ctx.close();
    }
  }
  await b.close();
  srv.close();
  console.log("[done]");
})().catch((e) => { console.error(e); process.exit(1); });
