// 把 book.json / index.json 拆成短块写进 D1（D1 单条语句上限约 100KB，标题元数据放这里）
// 用法：node scripts/build-data.mjs  然后  wrangler d1 execute ... --file data-books.sql
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const PKG = "D:\\中华女子学院\\学生工作\\比赛\\第二届北京市大学生「人工智能+」创新大赛\\纸书智伴-上线部署包";
const BOOKS = path.join(PKG, "server", "data", "books");
const OUT_SQL = path.join(ROOT, "data-books.sql");
const OUT_MANIFEST = path.join(ROOT, "upload-manifest.json");
const OUT_ASSET_SQL = path.join(ROOT, "data-assets.sql");
const CHUNK = 40000; // 单块字符数，留足 SQL 转义余量

function q(v) {
  if (v === null || v === undefined) return "NULL";
  return "'" + String(v).replace(/'/g, "''") + "'";
}
function readJSON(p, dflt = null) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch { return dflt; }
}
function chunk(s) {
  const out = [];
  for (let i = 0; i < s.length; i += CHUNK) out.push(s.slice(i, i + CHUNK));
  return out.length ? out : [""];
}

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".gif": "image/gif", ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
  ".json": "application/json", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".html": "text/html; charset=utf-8", ".woff2": "font/woff2",
  ".woff": "font/woff", ".ttf": "font/ttf", ".mp3": "audio/mpeg", ".wav": "audio/wav",
  ".txt": "text/plain; charset=utf-8"
};

const bookRows = [];
const blobRows = [];
const assetRows = [];
const manifest = [];

for (const id of fs.readdirSync(BOOKS)) {
  const dir = path.join(BOOKS, id);
  if (id.startsWith(".") || !fs.statSync(dir).isDirectory()) continue;
  const book = readJSON(path.join(dir, "book.json"), null);
  const index = readJSON(path.join(dir, "index.json"), null);
  if (!index) { console.warn("[skip] 无 index.json:", id); continue; }

  const pagesDir = path.join(dir, "pages");
  if (fs.existsSync(pagesDir)) {
    for (const f of fs.readdirSync(pagesDir)) {
      const fp = path.join(pagesDir, f);
      if (!fs.statSync(fp).isFile()) continue;
      const key = `books/${id}/pages/${f}`;
      manifest.push({ key, file: fp });
      assetRows.push(`  (${q(key)}, 'page', ${q(id)}, ${fs.statSync(fp).size}, ${q(MIME[path.extname(f).toLowerCase()] || "application/octet-stream")})`);
    }
  }
  const videoDir = path.join(dir, "video");
  let videoFull = null;
  if (fs.existsSync(videoDir)) {
    for (const f of fs.readdirSync(videoDir)) {
      const fp = path.join(videoDir, f);
      if (!fs.statSync(fp).isFile()) continue;
      const key = `books/${id}/video/${f}`;
      manifest.push({ key, file: fp });
      assetRows.push(`  (${q(key)}, 'video', ${q(id)}, ${fs.statSync(fp).size}, ${q(MIME[path.extname(f).toLowerCase()] || "application/octet-stream")})`);
      if (f === "book-full.mp4") videoFull = `/books/${id}/video/${f}`;
    }
  }

  const b = book || {};
  const pages = Array.isArray(b.pages) ? b.pages : [];
  const firstImg = pages[0] && pages[0].image ? pages[0].image : null;
  const cover = firstImg ? `/${String(firstImg).replace(/^\/+/, "")}` : null;

  // index 里部分字段名重复（St/st），JS 侧解析会丢字段，先按原始文本修补成本表结构后再拆分
  const bookJson = book ? JSON.stringify(book) : "";
  const indexJson = JSON.stringify(index);

  bookRows.push(
    `  (${q(id)}, ${q(index?.book?.title || b.title || id)}, ${q(index.source || "uploaded")}, ` +
    `${q(b.sourceType || "")}, ${Array.isArray(index.pages) ? index.pages.length : pages.length}, ` +
    `${q(cover)}, ${q(b.createdAt || index?.generator?.createdAt || "")}, ${q(b.updatedAt || "")}, ` +
    `${q(JSON.stringify(index.analysis || b.analysis || {}))}, ${q(videoFull)}, ` +
    `${q(videoFull ? "" : "缺整本视频：仅支持单页切片")})`
  );

  blobRows.push(`  (${q(id)}, 'book', 0, '', 0, ${q(bookJson)})`);
  chunk(indexJson).forEach((part, i) => {
    blobRows.push(`  (${q(id)}, 'index', ${i}, ${q(part)}, ${chunk(indexJson).length}, '')`);
  });
}

const sql = [
  "-- 自动生成，勿手改：node scripts/build-data.mjs",
  "-- D1 不接受 BEGIN TRANSACTION；单条 SQL 有 ~100KB 上限，故一行一条 INSERT、json 拆块存 book_blobs",
  "DELETE FROM books;",
  "DELETE FROM book_blobs;",
  ...bookRows.map(r => "INSERT INTO books (id,title,source,source_type,page_count,cover,created_at,updated_at,analysis_json,video_full,video_note) VALUES " + r + ";"),
  ...blobRows.map(r => "INSERT OR REPLACE INTO book_blobs (book_id,kind,part,part_data,total_parts,data) VALUES " + r + ";"),
  ""
].join("\n");

const assetSql = [
  "-- 自动生成，勿手改：node scripts/build-data.mjs",
  "DELETE FROM assets;",
  ...assetRows.map(r => "INSERT OR REPLACE INTO assets (key,kind,book_id,size,content_type) VALUES " + r + ";"),
  ""
].join("\n");

fs.writeFileSync(OUT_SQL, sql, "utf8");
fs.writeFileSync(OUT_ASSET_SQL, assetSql, "utf8");
fs.writeFileSync(OUT_MANIFEST, JSON.stringify(manifest, null, 2), "utf8");

let total = 0;
for (const m of manifest) total += fs.statSync(m.file).size;
console.log("books:", bookRows.length, "blobs:", blobRows.length, "assets:", assetRows.length);
console.log("sql:", (fs.statSync(OUT_SQL).size / 1024).toFixed(1) + "KB", "assetSql:", (fs.statSync(OUT_ASSET_SQL).size / 1024).toFixed(1) + "KB");
console.log("upload total:", (total / 1024 / 1024).toFixed(1) + "MB");
