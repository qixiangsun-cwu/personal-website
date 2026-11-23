// 单文件补传到 R2 —— 走 `wrangler r2 object put`，不碰 OAuth refresh_token，不会让登录失效。
// 用法：node scripts/r2-put.mjs <对象key> <本地文件> [content-type]
// 例：  node scripts/r2-put.mjs books/b-mwon9s99-a57fb3/video/book-full.mp4 "D:\...\book-full.mp4"
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const NODE = "C:\\Users\\Legion\\AppData\\Local\\Microsoft\\WinGet\\Packages\\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\\node-v24.19.0-win-x64\\node.exe";
const NPX = "C:\\Users\\Legion\\AppData\\Local\\Microsoft\\WinGet\\Packages\\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\\node-v24.19.0-win-x64\\node_modules\\npm\\bin\\npx-cli.js";
const ACCOUNT = "e46dcdf36d370b18ea9197feaf24ebd2";
const BUCKET = "paperbook-assets";

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".mp4": "video/mp4", ".webm": "video/webm", ".json": "application/json",
  ".js": "text/javascript; charset=utf-8", ".gz": "application/gzip",
  ".mp3": "audio/mpeg", ".wav": "audio/wav"
};

const [key, file, ctArg] = process.argv.slice(2);
if (!key || !file) {
  console.error("用法：node scripts/r2-put.mjs <对象key> <本地文件> [content-type]");
  process.exit(2);
}
if (!fs.existsSync(file)) { console.error("文件不存在：" + file); process.exit(2); }
const ct = ctArg || MIME[path.extname(file).toLowerCase()] || "application/octet-stream";
const size = fs.statSync(file).size;
console.log(`上传 ${file} (${(size / 1048576).toFixed(2)}MB) -> r2://${BUCKET}/${key}  [${ct}]`);

const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
console.log(`cwd=${PROJECT_DIR}`);
const r = spawnSync(NODE, [NPX, "--yes", "wrangler@3", "r2", "object", "put", `${BUCKET}/${key}`, "--file", file, "--content-type", ct], {
  encoding: "utf8",
  env: { ...process.env, CLOUDFLARE_ACCOUNT_ID: ACCOUNT, WRANGLER_SEND_METRICS: "false" },
  cwd: PROJECT_DIR
});
const out = (r.stdout || "") + (r.stderr || "");
if (/Upload complete/i.test(out)) { console.log("OK " + key); process.exit(0); }
console.error(out.split(/\r?\n/).filter(l => l && !/out-of-date|update available|Please update|npm install/.test(l)).join("\n"));
process.exit(1);
