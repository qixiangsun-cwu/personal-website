// 修复：D1 的 books.analysis_json 里缺少 pages（原版 book.json 的页数据），
// 导致 Worker 无状态读库后 pageCount=0、无热区、无伴读文本。
// 做法：从原始 server/data/books/<id>/book.json 读 pages，塞进 analysis_json.__pages，逐本 UPDATE。
// 输出：scripts/fix-book-pages.sql（一条 UPDATE 一行，避免 D1 单语句 100KB 限制）。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = 'D:/中华女子学院/学生工作/比赛/第二届北京市大学生「人工智能+」创新大赛/纸书智伴-上线部署包/server/data/books';
const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fix-book-pages.sql');

function q(s) { return "'" + String(s == null ? '' : s).replace(/'/g, "''") + "'"; }

const ids = fs.readdirSync(SRC).filter(d => d.startsWith('b-'));
const lines = [];
for (const id of ids) {
  const p = path.join(SRC, id, 'book.json');
  if (!fs.existsSync(p)) { console.log('skip (no book.json)', id); continue; }
  const b = JSON.parse(fs.readFileSync(p, 'utf8'));
  const pages = Array.isArray(b.pages) ? b.pages : [];
  // 保留 D1 里已有的 analysis 字段，只追加 __pages
  const analysis = { ...(b.analysis || {}), __pages: pages };
  const json = JSON.stringify(analysis);
  lines.push(`UPDATE books SET analysis_json = ${q(json)}, page_count = ${pages.length}, video_full = ${q(b.videoFull || '')}, video_note = ${q(b.videoNote || '')} WHERE id = ${q(id)};`);
  console.log(`${id}  页数=${pages.length}  热区合计=${pages.reduce((n, x) => n + ((x.hotspots || []).length), 0)}  analysis_json=${json.length} 字符`);
}
fs.writeFileSync(OUT, lines.join('\n') + '\n', 'utf8');
console.log('写出', OUT, fs.statSync(OUT).size, 'bytes,', lines.length, '条 UPDATE');
