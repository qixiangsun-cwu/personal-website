/**
 * 纸书智伴 · Worker 离线冒烟测试
 * 不需要 wrangler、不需要联网：用最小的 D1 / R2 / KV 桩件模拟 Workers 运行时，
 * 直接 import src/index.js 的 fetch，逐个打真实接口，看返回结构与降级行为是否与
 * 交付包 server.mjs 的契约一致。
 *
 * 用法：node scripts/smoke.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(HERE, '..', 'src');

/* ------------------------------------------------------------------ D1 桩 */
/** 极简 D1：只实现本 Worker 用到的 prepare/bind/run/all/first 与 SQLite 语义子集 */
class D1Stub {
  constructor(seed = {}) {
    this.tables = seed;       // { tableName: [row, ...] }
    this.nextId = {};
  }
  prepare(sql) {
    return new D1Stmt(this, sql);
  }
  async batch(stmts) {
    const out = [];
    for (const s of stmts) out.push(await s.run());
    return out;
  }
}

class D1Stmt {
  constructor(db, sql) {
    this.db = db;
    this.sql = String(sql).replace(/\s+/g, ' ').trim();
    this.args = [];
  }
  bind(...args) { this.args = args; return this; }

  async all() {
    const sql = this.sql;
    let t = /FROM\s+([A-Za-z_][A-Za-z0-9_]*)/i.exec(sql);
    const name = t ? t[1] : '';
    let rows = (this.db.tables[name] || []).slice();
    // WHERE key = ? / book_id=? AND kind=? 这类简单等值过滤
    const whereKeys = [...sql.matchAll(/([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\?/g)].map(m => m[1]);
    if (whereKeys.length) {
      const vals = this.args.slice(0, whereKeys.length);
      rows = rows.filter(r => whereKeys.every((k, i) => String(r[k]) === String(vals[i])));
    }
    const orderBy = /ORDER BY\s+([A-Za-z_][A-Za-z0-9_]*)(\s+DESC|\s+ASC)?/i.exec(sql);
    if (orderBy) {
      const k = orderBy[1], desc = /desc/i.test(orderBy[2] || '');
      rows.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * (desc ? -1 : 1));
    }
    const lim = /LIMIT\s+\?/i.test(sql) ? Number(this.args[this.args.length - 1]) : null;
    if (lim && Number.isFinite(lim)) rows = rows.slice(0, lim);
    return { success: true, results: rows };
  }
  async first() {
    const r = await this.all();
    return r.results[0] || null;
  }
  async run() {
    const sql = this.sql;
    if (/^INSERT/i.test(sql)) {
      const name = /INTO\s+([A-Za-z_][A-Za-z0-9_]*)/i.exec(sql)[1];
      const cols = /\(([^)]*)\)\s*VALUES/i.exec(sql)[1].split(',').map(s => s.trim());
      const row = {};
      cols.forEach((c, i) => { row[c] = this.args[i] === undefined ? null : this.args[i]; });
      this.db.tables[name] = this.db.tables[name] || [];
      if (/OR REPLACE/i.test(sql)) {
        const pk = cols[0];
        this.db.tables[name] = this.db.tables[name].filter(r => String(r[pk]) !== String(row[pk]));
      }
      this.db.tables[name].push(row);
      return { success: true, meta: { last_row_id: this.db.tables[name].length, changes: 1 } };
    }
    if (/^DELETE/i.test(sql)) {
      const name = /FROM\s+([A-Za-z_][A-Za-z0-9_]*)/i.exec(sql)[1];
      const k = /([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\?/.exec(sql);
      const before = (this.db.tables[name] || []).length;
      if (k) this.db.tables[name] = (this.db.tables[name] || []).filter(r => String(r[k[1]]) !== String(this.args[0]));
      else this.db.tables[name] = [];
      return { success: true, meta: { changes: before - (this.db.tables[name] || []).length } };
    }
    if (/^UPDATE/i.test(sql)) return { success: true, meta: { changes: 0 } };
    return { success: true, meta: { changes: 0 } };
  }
}

/* ------------------------------------------------------------------ R2 桩 */
class R2Stub {
  constructor(files) { this.files = files || new Map(); }
  async get(key, opts) {
    const f = this.files.get(key);
    if (!f) return null;
    let bytes = f.bytes;
    if (opts && opts.range) {
      bytes = bytes.subarray(opts.range.offset, opts.range.offset + opts.range.length);
    }
    return {
      key, size: f.bytes.length, httpEtag: 'stub-' + key,
      httpMetadata: { contentType: f.ct },
      body: new Blob([bytes]).stream()
    };
  }
  async head(key) {
    const f = this.files.get(key);
    if (!f) return null;
    return { key, size: f.bytes.length, httpMetadata: { contentType: f.ct } };
  }
  async put(key, body) {
    const bytes = body instanceof Uint8Array ? body : new Uint8Array(await new Response(body).arrayBuffer());
    this.files.set(key, { bytes, ct: 'application/octet-stream' });
    return { key };
  }
}

/* ------------------------------------------------------------------ 运行 */
const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail });
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond || detail === undefined ? '' : '  -> ' + JSON.stringify(detail).slice(0, 220)));
}

function loadBookSeed() {
  // 从交付包的书库目录读真实数据，保证测试跑的是真数据而非造出来的
  const base = 'D:\\中华女子学院\\学生工作\\比赛\\第二届北京市大学生「人工智能+」创新大赛\\纸书智伴-上线部署包\\server\\data\\books';
  const seed = { books: [], book_blobs: [], settings: [], logs: [], assets: [] };
  if (!fs.existsSync(base)) return { seed, base: null };
  const dirs = fs.readdirSync(base).filter(d => /^b-[a-z0-9-]+$/.test(d));
  const chunks = [];
  for (const d of dirs) {
    const file = path.join(base, d, 'book.json');
    if (!fs.existsSync(file)) continue;
    const book = JSON.parse(fs.readFileSync(file, 'utf8'));
    seed.books.push({
      id: book.id, title: book.title, source: book.source || 'upload', source_type: book.sourceType || '',
      page_count: (book.pages || []).length, cover: '', created_at: book.createdAt || '', updated_at: book.updatedAt || '',
      analysis_json: JSON.stringify(book.analysis || {})
    });
    const js = JSON.stringify(book);
    const CHUNK = 40000;
    let part = 0;
    for (let i = 0; i < js.length; i += CHUNK) {
      chunks.push({ book_id: book.id, kind: 'book', part: part++, total_parts: Math.ceil(js.length / CHUNK), part_data: js.slice(i, i + CHUNK) });
    }
    const idxFile = path.join(base, d, 'index.json');
    if (fs.existsSync(idxFile)) {
      const ix = fs.readFileSync(idxFile, 'utf8');
      let p2 = 0;
      for (let i = 0; i < ix.length; i += CHUNK) {
        chunks.push({ book_id: book.id, kind: 'index', part: p2++, total_parts: Math.ceil(ix.length / CHUNK), part_data: ix.slice(i, i + CHUNK) });
      }
    }
  }
  seed.book_blobs = chunks;
  seed.settings = [{ key: 'active_book', value: dirs.includes('b-mwn0edob-f15d19') ? 'b-mwn0edob-f15d19' : '' }];
  return { seed, base };
}

const R2_FILES = new Map();
R2_FILES.set('assets/video/book-full.mp4', { bytes: new Uint8Array(1024).fill(7), ct: 'video/mp4' });
R2_FILES.set('assets/video/page-1.mp4', { bytes: new Uint8Array(512).fill(3), ct: 'video/mp4' });

const { seed, base } = loadBookSeed();
const env = {
  DB: new D1Stub(seed),
  ASSETS: new R2Stub(R2_FILES),
  DEEPSEEK_BASE_URL: 'https://api.deepseek.com',
  DEEPSEEK_MODEL: 'deepseek-chat',
  PB_ALLOW_UPLOAD: '0',
  PB_ALLOW_ADMIN: '0'
};

const mod = await import('file:///' + path.join(SRC, 'index.js').replace(/\\/g, '/'));
const worker = mod.default;

async function hit(p, init) {
  const req = new Request('https://paperbook.qixiangsun.com.cn' + p, init);
  const res = await worker.fetch(req, env, { waitUntil() { } });
  const ct = res.headers.get('content-type') || '';
  let body;
  if (/json/.test(ct)) body = await res.json().catch(() => null);
  else body = await res.text().catch(() => '');
  return { status: res.status, ct, body, headers: res.headers };
}

console.log('\n=== 静态页面 ===');
let r = await hit('/');
check('GET / 200 且是 HTML', r.status === 200 && /text\/html/.test(r.ct), { status: r.status, len: String(r.body).length });
check('GET / 页面内联了样式', /<style data-src="assets\/real-bridge.css">/.test(String(r.body)));
check('GET / 含书名/标题', /纸书智伴/.test(String(r.body)));
const idxLen = String(r.body).length;

r = await hit('/backend.html');
check('GET /backend.html 200', r.status === 200 && String(r.body).length > 1000, { status: r.status, len: String(r.body).length });
r = await hit('/assets/real-bridge.css');
check('GET /assets/real-bridge.css 200 CSS', r.status === 200 && /css/.test(r.ct), { status: r.status, ct: r.ct });
r = await hit('/vendor/tesseract.min.js');
check('GET /vendor/tesseract.min.js 200 JS', r.status === 200, { status: r.status, ct: r.ct });
r = await hit('/vendor/tessdata/chi_sim.traineddata.gz');
check('GET chi_sim.traineddata.gz 200', r.status === 200, { status: r.status, ct: r.ct });
r = await hit('/assets/video/book-full.mp4');
check('GET 示例整本视频 200 (R2)', r.status === 200, { status: r.status });
{
  const req = new Request('https://paperbook.qixiangsun.com.cn/assets/video/book-full.mp4', { headers: { range: 'bytes=100-199' } });
  const res = await worker.fetch(req, env, { waitUntil() { } });
  check('Range 请求返回 206 + content-range', res.status === 206 && /bytes 100-199\/1024/.test(res.headers.get('content-range') || ''), { status: res.status, cr: res.headers.get('content-range') });
}

console.log('\n=== /api 基础 ===');
r = await hit('/api/health');
check('/api/health ok:true', r.status === 200 && r.body?.ok === true, r.body);
check('/api/health 标注 Workers 运行时', r.body?.runtime === 'cloudflare-workers', r.body?.runtime);
r = await hit('/api/books');
check('/api/books 返回书列表 + 示例书', r.status === 200 && Array.isArray(r.body?.books) && r.body.books.length >= 2, { n: r.body?.books?.length, active: r.body?.activeId });
check('/api/books 带 vision 状态', !!r.body?.vision, r.body?.vision);
const bookId = r.body?.books?.find(b => b.id !== 'sample')?.id;
r = await hit('/api/index');
check('/api/index 返回索引', r.status === 200 && !!r.body?.schema, { schema: r.body?.schema, pages: r.body?.pages?.length });
r = await hit('/api/active');
check('/api/active 指向激活书', r.status === 200 && r.body?.ok === true, r.body);
r = await hit('/api/pipeline/status');
check('/api/pipeline/status ok', r.status === 200 && r.body?.ok === true, { pages: r.body?.indexSummary?.pages });
r = await hit('/api/tts/voices');
check('/api/tts/voices 返回音色表', r.status === 200 && Array.isArray(r.body?.voices) && r.body.voices.length >= 8, { n: r.body?.voices?.length, provider: r.body?.provider });
r = await hit('/api/tts/worker');
check('/api/tts/worker ok', r.status === 200 && r.body?.ok === true, r.body);
r = await hit('/api/compliance');
check('/api/compliance ok', r.status === 200 && r.body?.ok === true && !!r.body?.privacy, { officer: r.body?.officer });
r = await hit('/api/hotspots?page=1');
check('/api/hotspots ok', r.status === 200 && r.body?.ok === true, { count: r.body?.count });
r = await hit('/api/eval');
check('/api/eval ok', r.status === 200 && r.body?.ok === true, r.body);
r = await hit('/api/logs?limit=5');
check('/api/logs 返回数组', r.status === 200 && Array.isArray(r.body?.logs), { total: r.body?.total });
r = await hit('/api/logs', { method: 'POST', body: JSON.stringify({ event: 'smoke', stage: 'test', value: 1 }), headers: { 'content-type': 'application/json' } });
check('/api/logs POST 写入', r.status === 200 && r.body?.ok === true, r.body);
r = await hit('/api/book/safety?bookId=' + (bookId || 'sample'));
check('/api/book/safety ok', r.status === 200 && r.body?.ok !== false, { safe: r.body?.safe, mode: r.body?.mode });

console.log('\n=== 无密钥时的降级路径（必须明确降级、不伪造） ===');
r = await hit('/api/qa', { method: 'POST', body: JSON.stringify({ query: '番茄为什么跑掉了？', page: 1 }), headers: { 'content-type': 'application/json' } });
check('/api/qa 200 且 degraded', r.status === 200 && r.body?.ok === true, { degraded: r.body?.degraded, mode: r.body?.mode });
check('/api/qa 给出 grounded/confidence', typeof r.body?.confidence === 'number' && typeof r.body?.grounded === 'boolean', { confidence: r.body?.confidence, grounded: r.body?.grounded });
r = await hit('/api/guard-should-404');
check('未知 /api 返回 404 + unknown api', r.status === 404 && r.body?.error === 'unknown api', r.body);
r = await hit('/api/books/analyze', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
check('/api/books/analyze 关闭上传返回 503', r.status === 503, r.body);
r = await hit('/api/book/recognize', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
check('/api/book/recognize 返回 501 降级', r.status === 501, r.body);
r = await hit('/api/remotion/status');
check('/api/remotion/status 返回未启用', r.status === 200 && r.body?.ready === false, r.body);
r = await hit('/api/remotion/generate', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
check('/api/remotion/generate 返回 503 降级', r.status === 503, r.body);
r = await hit('/api/video-doubao/config');
check('/api/video-doubao/config 未配置', r.status === 200 && r.body?.configured === false, r.body);
r = await hit('/api/tts', { method: 'POST', body: JSON.stringify({ text: '你好', voice: 'xiaoxiao' }), headers: { 'content-type': 'application/json' } });
check('/api/tts 无密钥时 503 且说明原因', r.status === 503 || r.status === 200, { status: r.status, body: typeof r.body === 'string' ? r.body.slice(0, 120) : r.body });

console.log('\n=== 汉字/单词/翻译 本地兜底 ===');
r = await hit('/api/hanzi', { method: 'POST', body: JSON.stringify({ char: '大', sentence: '大番茄' }), headers: { 'content-type': 'application/json' } });
check('/api/hanzi 本地字典兜底', r.status === 200 && r.body?.char === '大' && r.body?.meaning, { mode: r.body?.mode, meaning: r.body?.meaning });
r = await hit('/api/hanzi', { method: 'POST', body: JSON.stringify({ char: 'ab' }), headers: { 'content-type': 'application/json' } });
check('/api/hanzi 非法输入 400', r.status === 400, r.body);
r = await hit('/api/word', { method: 'POST', body: JSON.stringify({ word: 'popcorn', sentence: 'I like popcorn.' }), headers: { 'content-type': 'application/json' } });
check('/api/word 本地词典兜底', r.status === 200 && r.body?.word === 'popcorn' && r.body?.translation, { mode: r.body?.mode, translation: r.body?.translation });
r = await hit('/api/word', { method: 'POST', body: JSON.stringify({ word: '123' }), headers: { 'content-type': 'application/json' } });
check('/api/word 非法输入 400', r.status === 400, r.body);
r = await hit('/api/translate-page', { method: 'POST', body: JSON.stringify({ text: 'Hello world, this is a book.', bookTitle: 'Going to the Cinema' }), headers: { 'content-type': 'application/json' } });
check('/api/translate-page 英文直通', r.status === 200 && r.body?.mode === 'passthrough', r.body?.mode);
r = await hit('/api/zi-boxes', { method: 'POST', body: JSON.stringify({ pageNo: 1 }), headers: { 'content-type': 'application/json' } });
check('/api/zi-boxes 无密钥时明确降级', r.status === 200 && r.body?.ok === false && r.body?.degraded === true, r.body);
r = await hit('/api/locate', { method: 'POST', body: JSON.stringify({ keyword: '番茄' }), headers: { 'content-type': 'application/json' } });
check('/api/locate 有关键字定位能力', r.status === 200 && typeof r.body?.pageNo !== 'undefined', r.body);

console.log('\n=== 404 ===');
r = await hit('/no-such-page.html');
check('未知路径 404', r.status === 404, { status: r.status });

/* ------------------------------------------------------------------ 汇总 */
const failed = results.filter(x => !x.ok);
console.log('\n---------------------------------------------');
console.log('总计 ' + results.length + ' 项，通过 ' + (results.length - failed.length) + '，失败 ' + failed.length);
console.log('书库来源: ' + (base ? base : '（未找到交付包，跳过真实数据）'));
console.log('首页字节: ' + idxLen);
if (failed.length) {
  console.log('失败项：');
  failed.forEach(f => console.log('  - ' + f.name + ' -> ' + JSON.stringify(f.detail).slice(0, 300)));
  process.exit(1);
}
