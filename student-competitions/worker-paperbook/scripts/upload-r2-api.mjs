// R2 上传：用 wrangler 的 OAuth 刷新令牌换 access token，再走 R2 REST API 直传（PUT /accounts/:id/r2/buckets/:bucket/objects/:key）
//
// ⚠️ 重要：Cloudflare 的 refresh_token 是一次性的——用一次就让 wrangler 自己那份作废，
//    之后任何 wrangler 命令都会报 `Invalid access token [code: 9109]`，需要重新 `wrangler login`。
//    所以本脚本默认拒绝运行，必须显式加 --allow-rotate-token 才执行；
//    跑完请立刻 `wrangler login` 重新授权。
//    日常补传单个文件请用 `node scripts/r2-put.mjs <key> <file>`（用 wrangler r2 object put，不碰 OAuth token）。
//
// 用法：node scripts/upload-r2-api.mjs --allow-rotate-token [--dry] [--only <substr>]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
if (!process.argv.includes("--allow-rotate-token")) {
  console.error("拒绝执行：本脚本会消耗 wrangler 的一次性 refresh_token，导致 wrangler 登录失效。");
  console.error("确实要跑请加 --allow-rotate-token，并在跑完后重新 `wrangler login`。");
  console.error("只补传个别文件请用：node scripts/r2-put.mjs <key> <file>");
  process.exit(2);
}
const CFG = "C:\\Users\\Legion\\AppData\\Roaming\\xdg.config\\.wrangler\\config\\default.toml";
const ACCOUNT = "e46dcdf36d370b18ea9197feaf24ebd2";
const BUCKET = "paperbook-assets";
const CLIENT_ID = "54d11594-84e4-41aa-b438-e81b8fa78ee7";
const dry = process.argv.includes("--dry");
const onlyIdx = process.argv.indexOf("--only");
const only = onlyIdx > 0 ? process.argv[onlyIdx + 1] : "";

const toml = fs.readFileSync(CFG, "utf8");
const read = k => (toml.match(new RegExp(k + '\\s*=\\s*"([^"]+)"')) || [])[1];
const refreshToken = read("refresh_token");
if (!refreshToken) { console.error("no refresh_token in wrangler config"); process.exit(2); }

async function fetchRetry(url, init, tries = 4, label = "") {
  let lastErr = "";
  for (let i = 0; i < tries; i++) {
    try {
      return await fetch(url, init);
    } catch (e) {
      lastErr = String((e && e.cause && e.cause.code) || (e && e.message) || e);
      await new Promise(r => setTimeout(r, 1000 * (i + 1)));
    }
  }
  throw new Error("fetch failed " + label + ": " + lastErr);
}

const body = new URLSearchParams({ grant_type: "refresh_token", refresh_token: refreshToken, client_id: CLIENT_ID });
const tr = await fetchRetry("https://dash.cloudflare.com/oauth2/token", {
  method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body
}, 4, "dash-token");
const tj = await tr.text();
if (tr.status >= 400) { console.error("token refresh failed", tr.status, tj.slice(0, 400)); process.exit(2); }
const token = JSON.parse(tj).access_token;
if (!token) { console.error("no access_token", tj.slice(0, 300)); process.exit(2); }
console.log("token ok (" + token.slice(0, 6) + "...)");

const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "upload-manifest.json"), "utf8"));
const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".mp4": "video/mp4", ".webm": "video/webm", ".json": "application/json",
  ".js": "text/javascript; charset=utf-8", ".wasm": "application/wasm", ".wav": "audio/wav",
  ".mp3": "audio/mpeg", ".gz": "application/gzip"
};

const enc = s => s.split("/").map(encodeURIComponent).join("/");
const api = (key) => `https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}/r2/buckets/${BUCKET}/objects/${enc(key)}`;

// 已存在的键先查一遍：列出桶内全部对象
async function listExisting() {
  const set = new Set();
  let cursor = "";
  for (;;) {
    const u = new URL(`https://api.cloudflare.com/client/v4/accounts/${ACCOUNT}/r2/buckets/${BUCKET}/objects`);
    u.searchParams.set("per_page", "1000");
    if (cursor) u.searchParams.set("cursor", cursor);
    const r = await fetchRetry(u, { headers: { authorization: "Bearer " + token } }, 4, "list");
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.success) { console.log("list failed", r.status, JSON.stringify(j).slice(0, 300)); break; }
    for (const o of j.result || []) set.add(o.key);
    const info = j.result_info || {};
    if (!info.cursor) break;
    cursor = info.cursor;
  }
  return set;
}

const existing = dry ? new Set() : await listExisting();
console.log("existing objects: " + existing.size);

let ok = 0, skip = 0, fail = 0, bytes = 0;
for (const item of manifest) {
  if (only && !item.key.includes(only)) continue;
  const size = fs.statSync(item.file).size;
  const ct = MIME[path.extname(item.file).toLowerCase()] || "application/octet-stream";
  if (dry) { console.log("[dry]", item.key, (size / 1048576).toFixed(2) + "MB"); continue; }
  if (existing.has(item.key)) { skip++; continue; }
  const data = fs.readFileSync(item.file);
  let tries = 0, done = false, lastErr = "";
  while (tries < 3 && !done) {
    tries++;
    try {
      const r = await fetch(api(item.key), {
        method: "PUT",
        headers: { authorization: "Bearer " + token, "content-type": ct, "content-length": String(data.length) },
        body: data
      });
      if (r.ok) { done = true; }
      else { lastErr = r.status + " " + (await r.text()).slice(0, 200); }
    } catch (e) { lastErr = String(e && e.message || e); }
    if (!done) await new Promise(r => setTimeout(r, 800 * tries));
  }
  if (done) { ok++; bytes += size; console.log(`[ok] ${item.key}  ${(size / 1048576).toFixed(2)}MB`); }
  else { fail++; console.log(`[FAIL] ${item.key}  ${lastErr}`); }
}
console.log(`\ndone: ok=${ok} skip=${skip} fail=${fail} bytes=${(bytes / 1048576).toFixed(1)}MB`);
