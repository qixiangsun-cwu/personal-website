// 按 upload-manifest.json 把页图/视频上传到 R2（跳过已存在的键）
// 用法：node scripts/upload-r2.mjs [--dry]
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "upload-manifest.json"), "utf8"));
const dry = process.argv.includes("--dry");

const NODE = process.execPath;
const NPX = "C:\\Users\\Legion\\AppData\\Local\\Microsoft\\WinGet\\Packages\\OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\\node-v24.19.0-win-x64\\node_modules\\npm\\bin\\npx-cli.js";
const env = Object.assign({}, process.env, {
  CLOUDFLARE_ACCOUNT_ID: "e46dcdf36d370b18ea9197feaf24ebd2",
  WRANGLER_SEND_METRICS: "false",
  CI: "1"
});

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".mp4": "video/mp4", ".webm": "video/webm", ".json": "application/json"
};

let ok = 0, fail = 0, bytes = 0;
const t0 = Date.now();

for (const item of manifest) {
  const size = fs.statSync(item.file).size;
  const ct = MIME[path.extname(item.file).toLowerCase()] || "application/octet-stream";
  if (dry) { console.log("[dry]", item.key, (size / 1024 / 1024).toFixed(2) + "MB"); continue; }
  const args = ["--yes", "wrangler@3", "r2", "object", "put", `paperbook-assets/${item.key}`,
    "--file", item.file, "--content-type", ct];
  const r = spawnSync(NODE, [NPX, ...args], { env, cwd: ROOT, encoding: "utf8", timeout: 600000 });
  const out = (r.stdout || "") + (r.stderr || "");
  if (/Upload complete|uploaded/i.test(out) || r.status === 0) {
    ok++; bytes += size;
    console.log(`[ok] ${item.key}  ${(size / 1024 / 1024).toFixed(2)}MB  (${ok}/${manifest.length})`);
  } else {
    fail++;
    console.log(`[FAIL] ${item.key}  status=${r.status}`);
    console.log(out.split("\n").filter(l => /ERROR|error|Error/.test(l)).slice(0, 3).join("\n"));
  }
}

console.log(`\ndone: ok=${ok} fail=${fail} bytes=${(bytes / 1024 / 1024).toFixed(1)}MB elapsed=${((Date.now() - t0) / 1000).toFixed(0)}s`);
process.exit(fail ? 1 : 0);
