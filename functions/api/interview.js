// 研究项目 · 在线自填访谈后端（Cloudflare Pages Function）
//
// 路由：/api/interview
//   POST            公开提交一份自填访谈（无需鉴权）
//   GET  ?key=KEY   导出全部，format=json（默认）或 format=csv
//
// 存储：D1（env.DB）优先，KV（env.PROPOSALS）兜底；表 interview_responses

import {
  json,
  fail,
  authorized,
  makeStore,
  readBody,
  clientIp,
  clampStr,
  isScale,
  toCsv,
  csvResponse,
  dayKey,
  sha256Hex,
} from './_research-lib.js';

const KEY_HASH_DEFAULT = '9cdfc5c67b91446fb3b36d55309b2005142ecb0d4e5ff0dc86e141a3f91651c4';
const GENDERS = new Set(['F', 'M', 'O']);
const QN = 18;
const ANSWER_MAX = 1200;
const MIN_ANSWERED = 12;

function validate(body) {
  if (body.consent !== true) return 'consent_required';
  const d = body.demo;
  if (!d || typeof d !== 'object') return 'bad_demo';
  if (!GENDERS.has(d.gender)) return 'bad_gender';
  if (!isScale(d.age, 1, 5)) return 'bad_age';
  const ranges = { marital: [1, 4], live: [1, 5], city: [1, 4], edu: [1, 4] };
  for (const k of Object.keys(ranges)) if (!isScale(d[k], ranges[k][0], ranges[k][1])) return 'bad_' + k;
  if (typeof d.aiUsed !== 'boolean') return 'bad_aiUsed';

  const a = body.a;
  if (!a || typeof a !== 'object') return 'bad_answers';
  let answered = 0;
  for (let i = 1; i <= QN; i++) {
    const v = a['q' + i];
    if (v === undefined || v === null) continue;
    if (typeof v !== 'string') return 'bad_q' + i;
    if (v.length > ANSWER_MAX) return 'too_long_q' + i;
    if (v.trim().length > 0) answered++;
  }
  if (answered < MIN_ANSWERED) return 'too_few_answers';
  return null;
}

function normalize(body, request) {
  const a = {};
  for (let i = 1; i <= QN; i++) a['q' + i] = clampStr(body.a['q' + i], ANSWER_MAX);
  return {
    v: clampStr(body.v, 20) || '2026-11-20',
    consent: true,
    demo: {
      gender: body.demo.gender,
      age: String(body.demo.age),
      marital: String(body.demo.marital),
      live: String(body.demo.live),
      city: String(body.demo.city),
      edu: String(body.demo.edu),
      aiUsed: body.demo.aiUsed,
    },
    a,
    contact: clampStr(body.contact, 120),
    durationMs: Math.max(0, Math.min(Number(body.durationMs) || 0, 86400000)),
    startedAt: clampStr(body.startedAt, 40),
    submittedAt: new Date().toISOString(),
    ua: clampStr(request.headers.get('user-agent') || '', 300),
  };
}

const CSV_HEADER = [
  'id', 'createdAt', 'durationMs', 'gender', 'age', 'marital', 'live', 'city', 'edu', 'aiUsed',
  ...Array.from({ length: QN }, (_, i) => 'q' + (i + 1)),
  'contact', 'startedAt',
];

function toRow(item) {
  const d = item.data || {};
  const m = d.demo || {};
  const a = d.a || {};
  const row = [item.id, item.createdAt, d.durationMs, m.gender, m.age, m.marital, m.live, m.city, m.edu, m.aiUsed];
  for (let i = 1; i <= QN; i++) row.push(a['q' + i] || '');
  row.push(d.contact, d.startedAt);
  return row;
}

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);
  const keyHash = (env && env.RESEARCH_KEY_HASH) || KEY_HASH_DEFAULT;
  const withHash = env && env.RESEARCH_KEY_HASH ? env : Object.assign({}, env, { RESEARCH_KEY_HASH: keyHash });

  try {
    if (request.method === 'POST') {
      const { body, error } = await readBody(request);
      if (error) return fail(error, 400);
      if (body.hp) return fail('rejected', 400);
      const bad = validate(body);
      if (bad) return fail(bad, 400);

      const store = makeStore(env, 'interview');
      if (!store) return fail('storage_unbound', 503);

      const data = normalize(body, request);
      const id = 'I' + dayKey().replace(/-/g, '') + '-' + crypto.randomUUID().slice(0, 8);
      const ipHash = (await sha256Hex(clientIp(request) + '|research-interview')).slice(0, 16);
      await store.put(id, data.submittedAt, ipHash, data.ua, data);
      const count = await store.count();
      return json({ ok: true, id, count, storage: store.kind }, 200);
    }

    if (request.method === 'GET') {
      if (!(await authorized(withHash, url.searchParams.get('key') || ''))) {
        return fail('unauthorized', 401);
      }
      const store = makeStore(env, 'interview');
      if (!store) return fail('storage_unbound', 503);
      const format = (url.searchParams.get('format') || 'json').toLowerCase();
      const items = await store.list();
      if (format === 'csv') {
        return csvResponse(
          toCsv(CSV_HEADER, items.map(toRow)),
          'interview-responses-' + dayKey() + '.csv'
        );
      }
      return json({ ok: true, count: items.length, storage: store.kind, items }, 200);
    }

    if (request.method === 'DELETE') {
      if (!(await authorized(withHash, url.searchParams.get('key') || ''))) {
        return fail('unauthorized', 401);
      }
      const id = url.searchParams.get('id') || '';
      if (!/^[A-Za-z0-9-]{6,40}$/.test(id)) return fail('bad_id', 400);
      const store = makeStore(env, 'interview');
      if (!store) return fail('storage_unbound', 503);
      await store.delete(id);
      return json({ ok: true, deleted: id, count: await store.count(), storage: store.kind }, 200);
    }

    return fail('method_not_allowed', 405);
  } catch (err) {
    console.error('[interview] 处理失败', err && err.stack ? err.stack : err);
    return fail('server_error', 500);
  }
}
