// 导演组流程脚本工作站 · 「各组提案」后端（Cloudflare Pages Function）
// 契约见：Temp\opencode\proposal_spec.md（唯一真源，前端/后端/本地 mock 都按它实现）
//
// 路径：/private/director/proposals
// 鉴权：?key=<访问密钥>，服务端比对 sha256Hex(key) 与 WORKSTATION_KEY_HASH
//       （哈希常量与 index.html / screen.html / gate.html 里的是同一个，可公开）
// 存储：KV 绑定变量 PROPOSALS（后台手动绑定，代码里不写 id）
//       p:{group}:{act}  -> JSON proposal
//       meta:open        -> {"act1":false,"act2":false}
// 无第三方依赖；所有响应 Cache-Control: no-store，避免边缘/浏览器缓存提案。

const KEY_HASH = '2fe115eb0dac3a086b2cfee19270bd20d59156b2697bb2d0dcf9d6c47921154b';

// 组：s1..s10（与 data/flow.json 的 sessions[].id 一致）
const GROUPS = new Set(['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10']);

// 字段白名单（不允许写入白名单以外的键）
const FIELDS = {
  1: ['situation', 'approach', 'measures', 'forecast'],
  2: ['judgement', 'approach', 'measures'],
};

const MAX_LEN = 5000;          // 单字段最大长度（超长截断即可，不报错）
const OPEN_KEY = 'meta:open';
const PROPOSAL_PREFIX = 'p:';

// 本接口可能被 3 个以上浏览器并发轮询 + 自动保存，每次写都落 KV。
// 组数固定 10、幕固定 2，数据量极小，无需额外限流；这里只做统一错误兜底。

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

function fail(error, status) {
  return json({ ok: false, error }, status);
}

function badRequest() {
  return fail('bad_request', 400);
}

async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

async function authorized(key) {
  if (typeof key !== 'string' || key.length === 0) return false;
  const hex = await sha256Hex(key);
  return hex === KEY_HASH;
}

function proposalKey(group, act) {
  return `${PROPOSAL_PREFIX}${group}:${act}`;
}

// act 允许 1 / 2 / "1" / "2"（前端偶尔会从字符串拼出来），其余一律非法。
function normalizeAct(value) {
  if (value === 1 || value === '1') return 1;
  if (value === 2 || value === '2') return 2;
  return 0;
}

// 值转字符串 → trim → 截断到 5000。对象/数组不接受，返回 null 由调用方报 400。
function normalizeValue(value) {
  if (value === null || value === undefined) return '';
  const type = typeof value;
  if (type === 'object' || type === 'function' || type === 'symbol') return null;
  return String(value).trim().slice(0, MAX_LEN);
}

async function readJSON(kv, key, fallback) {
  const raw = await kv.get(key);
  if (raw === null || raw === undefined) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

async function readOpen(kv) {
  const value = await readJSON(kv, OPEN_KEY, null);
  return {
    act1: Boolean(value && value.act1 === true),
    act2: Boolean(value && value.act2 === true),
  };
}

// KV list 单页最多 1000 条，这里翻页取全；实际数据最多 20 条。
async function listKeys(kv, prefix) {
  const names = [];
  let cursor;
  for (let page = 0; page < 100; page++) {
    const result = await kv.list(cursor ? { prefix, cursor } : { prefix });
    for (const item of result.keys || []) names.push(item.name);
    if (result.list_complete) break;
    cursor = result.cursor;
    if (!cursor) break;
  }
  return names;
}

// GET ?key=KEY[&group=s3]
async function handleGet(kv, url) {
  const group = url.searchParams.get('group');
  if (group !== null && !GROUPS.has(group)) return badRequest();

  const open = await readOpen(kv);
  const proposals = {};

  const keys = await listKeys(kv, group ? `${PROPOSAL_PREFIX}${group}:` : PROPOSAL_PREFIX);
  // KV 返回按 key 升序，s1..s10 天然有序；这里不做额外排序。
  for (const key of keys) {
    const proposal = await readJSON(kv, key, null);
    if (!proposal || typeof proposal !== 'object') continue;
    const g = proposal.group;
    const act = normalizeAct(proposal.act);
    if (!GROUPS.has(g) || act === 0) continue;
    if (group && g !== group) continue;
    if (!proposals[g]) proposals[g] = {};
    proposals[g][`act${act}`] = proposal;
  }

  return json({ ok: true, serverTime: new Date().toISOString(), open, proposals }, 200);
}

// POST ?key=KEY（保存/提交）
async function handleSave(kv, request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return badRequest();
  }
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
  const existing = await readJSON(kv, proposalKey(group, act), null);
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

  await kv.put(proposalKey(group, act), JSON.stringify(proposal));
  return json({ ok: true, proposal }, 200);
}

// POST ?key=KEY&action=open（导演重开/收回本幕）
async function handleOpen(kv, request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return badRequest();
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return badRequest();

  const act = normalizeAct(body.act);
  if (act === 0) return badRequest();
  if (typeof body.open !== 'boolean') return badRequest();

  const open = await readOpen(kv);
  open[`act${act}`] = body.open;
  await kv.put(OPEN_KEY, JSON.stringify(open));
  return json({ ok: true, open }, 200);
}

// POST ?key=KEY&action=reset（清空本场数据）
async function handleReset(kv) {
  const keys = await listKeys(kv, PROPOSAL_PREFIX);
  for (const key of keys) await kv.delete(key);
  const open = { act1: false, act2: false };
  await kv.put(OPEN_KEY, JSON.stringify(open));
  return json({ ok: true, cleared: true }, 200);
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  // 1) 鉴权先行：无 key / 错 key 一律 401，不泄露存储状态
  if (!(await authorized(url.searchParams.get('key') || ''))) {
    return fail('unauthorized', 401);
  }

  // 2) KV 未绑定（Cloudflare 后台还没绑 PROPOSALS）：503，前端据此提示「提案服务未启用」
  const kv = env && env.PROPOSALS;
  if (!kv) return fail('kv_unbound', 503);

  try {
    if (request.method === 'GET') return await handleGet(kv, url);
    if (request.method === 'POST') {
      const action = url.searchParams.get('action');
      if (action === 'open') return await handleOpen(kv, request);
      if (action === 'reset') return await handleReset(kv);
      if (action !== null) return badRequest();
      return await handleSave(kv, request);
    }
    return fail('method_not_allowed', 405);
  } catch (err) {
    console.error('[proposals] 处理失败', err && err.stack ? err.stack : err);
    return fail('server_error', 500);
  }
}
