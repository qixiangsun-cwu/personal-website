// 研究问卷/访谈 · 公共库（Cloudflare Pages Function 内部模块，不对外路由）
//
// 存储：优先 D1（env.DB）；未绑定或不可用时退回 KV（env.PROPOSALS）
//   D1 表（首次请求自动建）：
//     survey_responses(id TEXT PRIMARY KEY, created_at TEXT, ip_hash TEXT, ua TEXT, data TEXT)
//     interview_responses(id TEXT PRIMARY KEY, created_at TEXT, ip_hash TEXT, ua TEXT, data TEXT)
//   KV 键（兜底）：research:{kind}:{isoTime}:{id} -> JSON
//
// 响应一律 Cache-Control: no-store，不缓存任何答卷。

export const MAX_BODY = 64 * 1024; // 64KB，正常答卷 8-20KB

export function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}
export function fail(error, status = 400, extra) {
  return json(Object.assign({ ok: false, error }, extra || {}), status);
}

export async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
}
export async function authorized(env, key) {
  const hash = env && env.RESEARCH_KEY_HASH;
  if (!hash) return false;
  if (typeof key !== 'string' || key.length === 0) return false;
  return (await sha256Hex(key)) === String(hash).toLowerCase();
}

export function dayKey() {
  return new Date().toISOString().slice(0, 10);
}

/* ---------------- D1 ---------------- */
const D1_STATEMENTS = {
  survey: [
    'CREATE TABLE IF NOT EXISTS survey_responses (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ip_hash TEXT, ua TEXT, data TEXT NOT NULL)',
    'CREATE INDEX IF NOT EXISTS idx_survey_created ON survey_responses(created_at)',
  ],
  interview: [
    'CREATE TABLE IF NOT EXISTS interview_responses (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ip_hash TEXT, ua TEXT, data TEXT NOT NULL)',
    'CREATE INDEX IF NOT EXISTS idx_interview_created ON interview_responses(created_at)',
  ],
};
const ready = { survey: false, interview: false };

async function ensureD1(db, kind) {
  if (ready[kind]) return;
  await db.batch(D1_STATEMENTS[kind].map((sql) => db.prepare(sql)));
  ready[kind] = true;
}

function tableFor(kind) {
  return kind === 'survey' ? 'survey_responses' : 'interview_responses';
}

/* ---------------- 存储适配层 ---------------- */
export function makeStore(env, kind) {
  const db = env && env.DB;
  const kv = env && env.PROPOSALS;
  if (db) {
    return {
      kind: 'd1',
      async put(id, createdAt, ipHash, ua, data) {
        await ensureD1(db, kind);
        await db
          .prepare(
            `INSERT INTO ${tableFor(kind)} (id, created_at, ip_hash, ua, data) VALUES (?,?,?,?,?)`
          )
          .bind(id, createdAt, ipHash || '', ua || '', JSON.stringify(data))
          .run();
      },
      async list() {
        await ensureD1(db, kind);
        const res = await db
          .prepare(`SELECT id, created_at, ip_hash, ua, data FROM ${tableFor(kind)} ORDER BY created_at DESC`)
          .all();
        const rows = (res && res.results) || [];
        return rows.map((r) => ({
          id: r.id,
          createdAt: r.created_at,
          ipHash: r.ip_hash,
          ua: r.ua,
          data: safeParse(r.data),
        }));
      },
      async count() {
        await ensureD1(db, kind);
        const row = await db.prepare(`SELECT COUNT(*) AS n FROM ${tableFor(kind)}`).first();
        return row ? Number(row.n) : 0;
      },
    };
  }
  if (kv) {
    const prefix = `research:${kind}:`;
    return {
      kind: 'kv',
      async put(id, createdAt, ipHash, ua, data) {
        await kv.put(prefix + createdAt + ':' + id, JSON.stringify({ id, createdAt, ipHash, ua, data }));
      },
      async list() {
        const out = [];
        let cursor;
        do {
          const page = await kv.list({ prefix, cursor });
          for (const k of page.keys) {
            const raw = await kv.get(k.name);
            if (!raw) continue;
            const item = safeParse(raw);
            if (item && item.id) out.push(item);
          }
          cursor = page.list_complete ? undefined : page.cursor;
        } while (cursor);
        out.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
        return out;
      },
      async count() {
        let n = 0;
        let cursor;
        do {
          const page = await kv.list({ prefix, cursor });
          n += page.keys.length;
          cursor = page.list_complete ? undefined : page.cursor;
        } while (cursor);
        return n;
      },
    };
  }
  return null;
}

export function safeParse(raw) {
  if (typeof raw !== 'string') return raw && typeof raw === 'object' ? raw : null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/* ---------------- 请求体与字段校验 ---------------- */
export async function readBody(request) {
  const text = await request.text();
  if (!text || text.length > MAX_BODY) return { error: 'body_too_large_or_empty' };
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return { error: 'bad_json' };
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { error: 'bad_body' };
  return { body };
}

export function clientIp(request) {
  return (
    request.headers.get('cf-connecting-ip') ||
    request.headers.get('x-forwarded-for') ||
    ''
  );
}

export function clampStr(v, max) {
  return String(v == null ? '' : v).slice(0, max);
}

export function isScale(v, min, max) {
  const n = Number(v);
  return Number.isInteger(n) && n >= min && n <= max;
}

/* ---------------- CSV ---------------- */
export function csvEscape(v) {
  const s = v == null ? '' : String(v);
  if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}
export function toCsv(header, rows) {
  const lines = [header.map(csvEscape).join(',')];
  for (const row of rows) lines.push(row.map(csvEscape).join(','));
  return '\ufeff' + lines.join('\r\n') + '\r\n';
}
export function csvResponse(text, name) {
  return new Response(text, {
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="${name}"`,
      'Cache-Control': 'no-store',
    },
  });
}
