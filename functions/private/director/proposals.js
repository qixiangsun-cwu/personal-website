// 导演组流程脚本工作站 · 「各组提案」后端（Cloudflare Pages Function）
//
// 路径：/private/director/proposals
// 鉴权：?key=<访问密钥>，服务端比对 sha256Hex(key) 与 WORKSTATION_KEY_HASH
// 存储：优先 D1（绑定变量 DB）；未绑定 D1 时退回 KV（绑定变量 PROPOSALS）
//   D1 表（首次请求自动建）：
//     proposals(grp TEXT, act INTEGER, fields TEXT, submitted INTEGER, updated_at TEXT, PRIMARY KEY(grp,act))
//     meta(k TEXT PRIMARY KEY, v TEXT)
//     usage(day TEXT PRIMARY KEY, req INTEGER, rows_read INTEGER, rows_written INTEGER)
//   KV 键（兜底模式）：
//     p:{group}:{act} -> JSON proposal；meta:open -> {"act1":false,"act2":false}
//
// 响应在契约基础上多两个字段：
//   storage: 'd1' | 'kv'       —— 当前用的存储
//   usage:   {day, req, reads, writes, limits:{reads,writes}} | null  —— 今日用量（KV 模式为 null）
// 无第三方依赖；所有响应 Cache-Control: no-store。

const KEY_HASH = '2fe115eb0dac3a086b2cfee19270bd20d59156b2697bb2d0dcf9d6c47921154b';

// D1 免费额度（每日，UTC 00:00 重置）
const D1_LIMITS = { reads: 5000000, writes: 100000 };

// 组：s1..s10（与 data/flow.json 的 sessions[].id 一致）
const GROUPS = new Set(['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10']);
const GROUP_IDS = [...GROUPS];

// 字段白名单（不允许写入白名单以外的键）
const FIELDS = {
  1: ['situation', 'approach', 'measures', 'forecast'],
  2: ['judgement', 'approach', 'measures'],
};

const MAX_LEN = 5000;          // 单字段最大长度（超长截断即可，不报错）
const OPEN_KEY = 'meta:open';
const RELEASE_KEY = 'meta:release';   // 发放给各组查看的方案（{act1:bool, act2:bool}）
const PROPOSAL_PREFIX = 'p:';

/* ---------------- 通用工具 ---------------- */
function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}
function fail(error, status) { return json({ ok: false, error }, status); }
function badRequest() { return fail('bad_request', 400); }

async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
}
async function authorized(key) {
  if (typeof key !== 'string' || key.length === 0) return false;
  return (await sha256Hex(key)) === KEY_HASH;
}

// act 允许 1 / 2 / "1" / "2"，其余一律非法。
function normalizeAct(value) {
  if (value === 1 || value === '1') return 1;
  if (value === 2 || value === '2') return 2;
  return 0;
}
// 值转字符串 → trim → 截断；对象/数组不接受（返回 null）。
function normalizeValue(value) {
  if (value === null || value === undefined) return '';
  const type = typeof value;
  if (type === 'object' || type === 'function' || type === 'symbol') return null;
  return String(value).trim().slice(0, MAX_LEN);
}
function emptyOpen() { return { act1: false, act2: false }; }
function emptyRelease() { return { act1: false, act2: false }; }
function dayKey() { return new Date().toISOString().slice(0, 10); }

/* ================================================================
   存储适配层：D1 优先，KV 兜底
   适配器接口：kind / getProposal / putProposal / getOpen / setOpen /
              clear / getUsage / bump(reads, writes)
   ================================================================ */

/* ---------------- D1 ---------------- */
const D1_STATEMENTS = [
  'CREATE TABLE IF NOT EXISTS proposals (grp TEXT NOT NULL, act INTEGER NOT NULL, fields TEXT NOT NULL, submitted INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (grp, act))',
  'CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)',
  'CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, req INTEGER NOT NULL DEFAULT 0, rows_read INTEGER NOT NULL DEFAULT 0, rows_written INTEGER NOT NULL DEFAULT 0)',
];
let d1Ready = false;
async function d1Ensure(db) {
  if (d1Ready) return;
  await db.batch(D1_STATEMENTS.map((sql) => db.prepare(sql)));
  d1Ready = true;
}

function rowToProposal(row) {
  if (!row) return null;
  let fields = {};
  try { fields = JSON.parse(row.fields || '{}') || {}; } catch { fields = {}; }
  return {
    group: row.grp,
    act: Number(row.act),
    fields,
    submitted: Number(row.submitted) === 1,
    updatedAt: row.updated_at,
  };
}

function d1Store(db) {
  return {
    kind: 'd1',
    async getProposal(group, act) {
      const row = await db.prepare('SELECT grp, act, fields, submitted, updated_at FROM proposals WHERE grp=? AND act=?')
        .bind(group, act).first();
      return rowToProposal(row);
    },
    async putProposal(p) {
      await db.prepare(
        'INSERT INTO proposals (grp, act, fields, submitted, updated_at) VALUES (?,?,?,?,?) ' +
        'ON CONFLICT(grp, act) DO UPDATE SET fields=excluded.fields, submitted=excluded.submitted, updated_at=excluded.updated_at'
      ).bind(p.group, p.act, JSON.stringify(p.fields), p.submitted ? 1 : 0, p.updatedAt).run();
    },
    async getOpen() {
      const row = await db.prepare('SELECT v FROM meta WHERE k=?').bind(OPEN_KEY).first();
      if (!row) return emptyOpen();
      try {
        const v = JSON.parse(row.v || '{}');
        return { act1: v.act1 === true, act2: v.act2 === true };
      } catch { return emptyOpen(); }
    },
    async setOpen(open) {
      await db.prepare('INSERT INTO meta (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v')
        .bind(OPEN_KEY, JSON.stringify(open)).run();
    },
    async getRelease() {
      const row = await db.prepare('SELECT v FROM meta WHERE k=?').bind(RELEASE_KEY).first();
      if (!row) return emptyRelease();
      try {
        const v = JSON.parse(row.v || '{}');
        return { act1: v.act1 === true, act2: v.act2 === true };
      } catch { return emptyRelease(); }
    },
    async setRelease(rel) {
      await db.prepare('INSERT INTO meta (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v')
        .bind(RELEASE_KEY, JSON.stringify(rel)).run();
    },
    async clear() {
      await db.prepare('DELETE FROM proposals').run();
      await this.setOpen(emptyOpen());
      await this.setRelease(emptyRelease());
    },
    async getUsage() {
      const day = dayKey();
      const row = await db.prepare('SELECT req, rows_read, rows_written FROM usage WHERE day=?').bind(day).first();
      return {
        day,
        req: row ? Number(row.req) : 0,
        reads: row ? Number(row.rows_read) : 0,
        writes: row ? Number(row.rows_written) : 0,
        limits: D1_LIMITS,
      };
    },
    async bump(reads, writes) {
      const day = dayKey();
      await db.prepare(
        'INSERT INTO usage (day, req, rows_read, rows_written) VALUES (?,1,?,?) ' +
        'ON CONFLICT(day) DO UPDATE SET req=req+1, rows_read=rows_read+?, rows_written=rows_written+?'
      ).bind(day, reads, writes, reads, writes).run();
      return this.getUsage();
    },
  };
}

/* ---------------- KV 兜底 ---------------- */
async function readJSON(kv, key, fallback) {
  const raw = await kv.get(key);
  if (raw === null || raw === undefined) return fallback;
  try { return JSON.parse(raw); } catch { return fallback; }
}
function kvStore(kv) {
  const key = (g, a) => `${PROPOSAL_PREFIX}${g}:${a}`;
  return {
    kind: 'kv',
    async getProposal(group, act) {
      const p = await readJSON(kv, key(group, act), null);
      if (!p || typeof p !== 'object') return null;
      if (p.group !== group || normalizeAct(p.act) !== act) return null;
      return p;
    },
    async putProposal(p) { await kv.put(key(p.group, p.act), JSON.stringify(p)); },
    async getOpen() {
      const v = await readJSON(kv, OPEN_KEY, null);
      return { act1: Boolean(v && v.act1 === true), act2: Boolean(v && v.act2 === true) };
    },
    async setOpen(open) { await kv.put(OPEN_KEY, JSON.stringify(open)); },
    async getRelease() {
      const v = await readJSON(kv, RELEASE_KEY, null);
      return { act1: Boolean(v && v.act1 === true), act2: Boolean(v && v.act2 === true) };
    },
    async setRelease(rel) { await kv.put(RELEASE_KEY, JSON.stringify(rel)); },
    async clear() {
      const keys = [];
      for (const g of GROUP_IDS) for (const act of [1, 2]) keys.push(key(g, act));
      await Promise.all(keys.map((k) => kv.delete(k)));
      await this.setOpen(emptyOpen());
      await this.setRelease(emptyRelease());
    },
    async getUsage() { return null; },   // KV 读不到自己的每日用量（没有公开 API）
    async bump() { return null; },
  };
}

/* ================================================================
   业务处理（存储无关）
   ================================================================ */

// GET ?key=KEY[&group=s3]
async function handleGet(store, url) {
  const group = url.searchParams.get('group');
  if (group !== null && !GROUPS.has(group)) return badRequest();

  const open = await store.getOpen();
  const release = await store.getRelease();
  const proposals = {};
  const targets = group ? [group] : GROUP_IDS;
  for (const g of targets) {
    for (const act of [1, 2]) {
      const p = await store.getProposal(g, act);
      if (!p) continue;
      if (!proposals[g]) proposals[g] = {};
      proposals[g][`act${act}`] = p;
    }
  }

  /* 发放：仅组端单组读时返回「其他组已提交的第一幕方案」。
     未提交的组只保留占位（前端显示未提交），不泄露草稿。 */
  let shared = null;
  let extraReads = 0;
  if (group && release.act1) {
    shared = {};
    for (const g of GROUP_IDS) {
      const p = await store.getProposal(g, 1);
      extraReads += 1;
      if (!p || p.submitted !== true) continue;
      shared[g] = { group: g, act: 1, submitted: true, updatedAt: p.updatedAt, fields: p.fields };
    }
  }

  const usage = await store.bump(targets.length * 2 + 1 + extraReads, 0);
  return json({
    ok: true,
    serverTime: new Date().toISOString(),
    open, release, shared, proposals,
    storage: store.kind, usage,
  }, 200);
}

// POST ?key=KEY（保存/提交）
async function handleSave(store, request) {
  let body;
  try { body = await request.json(); } catch { return badRequest(); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return badRequest();

  const group = body.group;
  if (typeof group !== 'string' || !GROUPS.has(group)) return badRequest();

  const act = normalizeAct(body.act);
  if (act === 0) return badRequest();

  const fields = body.fields;
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return badRequest();

  const allowed = FIELDS[act];
  for (const key of Object.keys(fields)) {
    if (!allowed.includes(key)) return badRequest();
  }

  let submitted;
  if (Object.prototype.hasOwnProperty.call(body, 'submitted')) {
    if (typeof body.submitted !== 'boolean') return badRequest();
    submitted = body.submitted;
  }

  // 合并式保存：未提交的字段沿用旧值（前端一般每次 POST 全量 fields，此规则无害）。
  const existing = await store.getProposal(group, act);
  const oldFields = (existing && existing.fields) || {};
  const nextFields = {};
  for (const key of allowed) {
    if (Object.prototype.hasOwnProperty.call(fields, key)) {
      const value = normalizeValue(fields[key]);
      if (value === null) return badRequest();
      nextFields[key] = value;
    } else {
      nextFields[key] = typeof oldFields[key] === 'string' ? oldFields[key] : '';
    }
  }

  const proposal = {
    group,
    act,
    fields: nextFields,
    submitted: typeof submitted === 'boolean' ? submitted : existing?.submitted === true,
    updatedAt: new Date().toISOString(),
  };

  await store.putProposal(proposal);
  const usage = await store.bump(1, 1);
  return json({ ok: true, proposal, storage: store.kind, usage }, 200);
}

// POST ?key=KEY&action=open（导演重开/收回本幕）
async function handleOpen(store, request) {
  let body;
  try { body = await request.json(); } catch { return badRequest(); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return badRequest();

  const act = normalizeAct(body.act);
  if (act === 0) return badRequest();
  if (typeof body.open !== 'boolean') return badRequest();

  const open = await store.getOpen();
  open[`act${act}`] = body.open;
  await store.setOpen(open);
  const usage = await store.bump(1, 1);
  return json({ ok: true, open, storage: store.kind, usage }, 200);
}

// POST ?key=KEY&action=release（导演把各组第一幕方案发放给所有组查看 / 收回）
async function handleRelease(store, request) {
  let body;
  try { body = await request.json(); } catch { return badRequest(); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return badRequest();

  const act = normalizeAct(body.act);
  if (act === 0) return badRequest();
  if (typeof body.released !== 'boolean') return badRequest();

  const release = await store.getRelease();
  release[`act${act}`] = body.released;
  await store.setRelease(release);
  const usage = await store.bump(1, 1);
  return json({ ok: true, release, storage: store.kind, usage }, 200);
}

// POST ?key=KEY&action=reset（清空本场数据）
async function handleReset(store) {
  await store.clear();
  const usage = await store.bump(1, 20);
  return json({ ok: true, cleared: true, storage: store.kind, usage }, 200);
}

/* ================================================================
   入口
   ================================================================ */
export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  /* 鉴权分两档（2026-10-26 改）：
     · 组端页 group.html 是公开页（不需要输入密钥），所以「读本组」与「保存/提交」不校验 key；
     · 导演台相关的「读全量」「重开本幕」「清空本场」仍然必须带 key。
     公开的只到「单组」这一级：GET 不带 group / POST 带 action 一律 401。 */
  const action = url.searchParams.get('action');
  const group = url.searchParams.get('group');
  const groupScoped = (request.method === 'GET' && group !== null)
    || (request.method === 'POST' && action === null);

  if (!groupScoped && !(await authorized(url.searchParams.get('key') || ''))) {
    return fail('unauthorized', 401);
  }

  /* 存储选择：D1 优先；D1 未绑定时退回 KV；两者都没有 → 503（前端据此提示未启用） */
  const db = env && env.DB;
  const kv = env && env.PROPOSALS;
  if (!db && !kv) return fail('kv_unbound', 503);

  try {
    if (!db) {
      // KV 兜底模式
      const store = kvStore(kv);
      if (request.method === 'GET') return await handleGet(store, url);
      if (request.method === 'POST') {
        if (action === 'open') return await handleOpen(store, request);
        if (action === 'release') return await handleRelease(store, request);
        if (action === 'reset') return await handleReset(store);
        if (action !== null) return badRequest();
        return await handleSave(store, request);
      }
      return fail('method_not_allowed', 405);
    }

    await d1Ensure(db);
    const store = d1Store(db);
    if (request.method === 'GET') return await handleGet(store, url);
    if (request.method === 'POST') {
      if (action === 'open') return await handleOpen(store, request);
        if (action === 'release') return await handleRelease(store, request);
      if (action === 'reset') return await handleReset(store);
      if (action !== null) return badRequest();
      return await handleSave(store, request);
    }
    return fail('method_not_allowed', 405);
  } catch (err) {
    console.error('[proposals] 处理失败', err && err.stack ? err.stack : err);
    return fail('server_error', 500);
  }
}
