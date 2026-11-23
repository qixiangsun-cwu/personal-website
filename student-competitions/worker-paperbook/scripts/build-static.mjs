// 生成 src/static.gen.js：把前端静态资源（HTML / CSS / 图标 / 小体积 JS / 视频页切片）
// 原样内联成模块导出，由 Worker 直接返回。大文件（wasm、mp4 整本动画、音乐）走 R2。
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, extname } from "node:path";

const ROOT = "D:\\各种工作\\个人网站\\student-competitions\\worker-paperbook";
const SITE = "D:\\中华女子学院\\学生工作\\比赛\\第二届北京市大学生「人工智能+」创新大赛\\纸书智伴-上线部署包\\纸书智伴";
const OUT = join(ROOT, "src", "static.gen.js");

// 前端 11 个 assets/*.js 改成 ES module 引入 → 这些由 fe-*.js 提供，静态目录不再收录
const SKIP_JS = new Set([
  "assets/api-base.js", "assets/audio-bus.js", "assets/book-bridge.js", "assets/chat-history.js",
  "assets/dynamic-book.js", "assets/ai-clips.js", "assets/real-bridge2.js", "assets/qa-guide.js",
  "assets/knowledge.js", "assets/lang.js", "assets/remotion-bridge.js", "assets/hanzi-origin.js",
]);
const MAX_INLINE = 2 * 1024 * 1024;
const MIME = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
  ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
  ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
  ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
  ".gz": "application/gzip", ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm",
};

// 首页把 <script src="assets/xxx.js"> 改成 module 引入（保持原有执行顺序）
const MODULE_RX = /<script\s+src="assets\/([a-z0-9\-.]+\.js)(\?[^"]*)?"\s*><\/script>/g;

const files = {};
const skipped = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name);
    const st = statSync(abs);
    if (st.isDirectory()) { walk(abs); continue; }
    const rel = relative(SITE, abs).replace(/\\/g, "/");
    if (!rel.includes("/")) continue;                       // 只收子目录里的，顶层单独处理
    if (SKIP_JS.has(rel)) continue;
    if (rel.endsWith(".mp4") || rel.endsWith(".wasm")) { skipped.push([rel, st.size, "r2/vendor"]); continue; }
    if (st.size > MAX_INLINE) { skipped.push([rel, st.size, "too-big"]); continue; }
    const ext = extname(rel).toLowerCase();
    let buf = readFileSync(abs);
    if (ext === ".html") buf = Buffer.from(buf.toString("utf8").replace(MODULE_RX, (m, f) => `<script type="module">import "/assets/${f}";</script>`), "utf8");
    files[rel] = { ct: MIME[ext] || "application/octet-stream", b64: buf.toString("base64"), size: buf.length };
  }
}
walk(SITE);

// 顶层 HTML（入口页）
for (const name of readdirSync(SITE)) {
  const abs = join(SITE, name);
  if (!statSync(abs).isFile() || !/\.html$/i.test(name)) continue;
  let buf = readFileSync(abs);
  buf = Buffer.from(buf.toString("utf8").replace(MODULE_RX, (m, f) => `<script type="module">import "/assets/${f}";</script>`), "utf8");
  files[name] = { ct: MIME[".html"], b64: buf.toString("base64"), size: buf.length };
}

const lines = [];
lines.push("// 自动生成：由 scripts/build-static.mjs 从 纸书智伴/ 打包，勿手改");
lines.push("export const FILES = {");
for (const [k, v] of Object.entries(files)) {
  lines.push(`  ${JSON.stringify(k)}: { ct: ${JSON.stringify(v.ct)}, size: ${v.size}, b64: ${JSON.stringify(v.b64)} },`);
}
lines.push("};");
lines.push("export const SKIPPED = " + JSON.stringify(skipped) + ";");
writeFileSync(OUT, lines.join("\n") + "\n", "utf8");

let total = 0;
for (const v of Object.values(files)) total += v.size;
console.log("files=" + Object.keys(files).length + " bytes=" + total + " gen=" + OUT);
console.log("skipped=" + skipped.length);
for (const s of skipped) console.log("  skip", s[0], (s[1] / 1048576).toFixed(2) + "MB", s[2]);
const big = Object.entries(files).sort((a, b) => b[1].size - a[1].size).slice(0, 8);
for (const [k, v] of big) console.log("  big", k, (v.size / 1024).toFixed(1) + "KB");
