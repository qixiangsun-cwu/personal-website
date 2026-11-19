// 研究项目 · 匿名问卷后端（Cloudflare Pages Function）
//
// 路由：/api/survey
//   POST            公开提交一份问卷（无需鉴权）
//   GET  ?key=KEY   导出全部答卷，format=json（默认）或 format=csv
//
// 存储：D1（env.DB）优先，KV（env.PROPOSALS）兜底；表 survey_responses
// 鉴权：?key= 与服务端 sha256 常量比对（RESEARCH_KEY_HASH 可在 wrangler.toml [vars] 覆盖）

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
const MANIP = new Set(['male', 'female', 'balanced']);

function validate(body) {
  if (body.consent !== true) return 'consent_required';
  if (body.adult !== true) return 'adult_required';
  if (typeof body.aiUsed !== 'boolean') return 'bad_aiUsed';
  if (!GENDERS.has(body.gender)) return 'bad_gender';
  if (!isScale(body.age, 1, 5)) return 'bad_age';

  const order = body.order;
  if (!Array.isArray(order) || order.length !== 4) return 'bad_order';
  const seenOrder = new Set(order.map(Number));
  if (seenOrder.size !== 4 || [...seenOrder].some((n) => n < 0 || n > 3)) return 'bad_order';

  const cards = body.cards;
  if (!Array.isArray(cards) || cards.length !== 4) return 'bad_cards';
  const seenCards = new Set();
  for (const c of cards) {
    if (!c || typeof c !== 'object') return 'bad_card';
    const i = Number(c.i);
    if (!Number.isInteger(i) || i < 0 || i > 3 || seenCards.has(i)) return 'bad_card_index';
    seenCards.add(i);
    for (const k of ['fair', 'follow', 'bias', 'trust']) {
      if (!isScale(c[k], 1, 7)) return 'bad_' + k;
    }
    if (!MANIP.has(c.manip)) return 'bad_manip';
    if (typeof c.why !== 'string' || c.why.length > 600) return 'bad_why';
  }

  const gen = body.gen;
  if (!gen || typeof gen !== 'object') return 'bad_gen';
  if (!isScale(gen.c1, 1, 7) || !isScale(gen.c2, 1, 7)) return 'bad_c1_c2';
  for (const k of ['c3', 'c4', 'c5', 'c6']) if (!isScale(gen[k], 1, 5)) return 'bad_' + k;

  const d = body.demo;
  if (!d || typeof d !== 'object') return 'bad_demo';
  const ranges = { marital: [1, 4], live: [1, 5], main: [1, 5], edu: [1, 4], city: [1, 4], heard: [1, 3] };
  for (const k of Object.keys(ranges)) if (!isScale(d[k], ranges[k][0], ranges[k][1])) return 'bad_' + k;

  const open = body.open;
  if (!open || typeof open !== 'object') return 'bad_open';
  if (typeof open.e1 !== 'string' || open.e1.length > 1000) return 'bad_e1';
  if (typeof open.e2 !== 'string' || open.e2.length > 1000) return 'bad_e2';

  return null;
}

function normalize(body, request) {
  return {
    v: clampStr(body.v, 20) || '2026-11-20',
    consent: true,
    adult: true,
    aiUsed: body.aiUsed,
    gender: body.gender,
    age: String(body.age),
    order: body.order.map(Number),
    cards: body.cards.map((c) => ({
      i: Number(c.i),
      fair: Number(c.fair),
      follow: Number(c.follow),
      bias: Number(c.bias),
      trust: Number(c.trust),
      manip: c.manip,
      why: clampStr(c.why, 600),
    })),
    gen: {
      c1: Number(body.gen.c1),
      c2: Number(body.gen.c2),
      c3: Number(body.gen.c3),
      c4: Number(body.gen.c4),
      c5: Number(body.gen.c5),
      c6: Number(body.gen.c6),
    },
    demo: {
      marital: String(body.demo.marital),
      live: String(body.demo.live),
      main: String(body.demo.main),
      edu: String(body.demo.edu),
      city: String(body.demo.city),
      heard: String(body.demo.heard),
    },
    open: { e1: clampStr(body.open.e1, 1000), e2: clampStr(body.open.e2, 1000) },
    contact: clampStr(body.contact, 120),
    durationMs: Math.max(0, Math.min(Number(body.durationMs) || 0, 86400000)),
    startedAt: clampStr(body.startedAt, 40),
    submittedAt: new Date().toISOString(),
    ua: clampStr(request.headers.get('user-agent') || '', 300),
  };
}

const CSV_HEADER = [
  'id', 'createdAt', 'durationMs', 'gender', 'age', 'aiUsed', 'order',
  'c0_fair', 'c0_follow', 'c0_bias', 'c0_trust', 'c0_manip', 'c0_why',
  'c1_fair', 'c1_follow', 'c1_bias', 'c1_trust', 'c1_manip', 'c1_why',
  'c2_fair', 'c2_follow', 'c2_bias', 'c2_trust', 'c2_manip', 'c2_why',
  'c3_fair', 'c3_follow', 'c3_bias', 'c3_trust', 'c3_manip', 'c3_why',
  'gen_c1', 'gen_c2', 'gen_c3', 'gen_c4', 'gen_c5', 'gen_c6',
  'marital', 'live', 'main', 'edu', 'city', 'heard',
  'e1', 'e2', 'contact', 'startedAt',
];

function toRow(item) {
  const d = item.data || {};
  const byCard = {};
  for (const c of d.cards || []) byCard[c.i] = c;
  const row = [item.id, item.createdAt, d.durationMs, d.gender, d.age, d.aiUsed, (d.order || []).join('|')];
  for (let i = 0; i < 4; i++) {
    const c = byCard[i] || {};
    row.push(c.fair, c.follow, c.bias, c.trust, c.manip, c.why);
  }
  const g = d.gen || {};
  row.push(g.c1, g.c2, g.c3, g.c4, g.c5, g.c6);
  const m = d.demo || {};
  row.push(m.marital, m.live, m.main, m.edu, m.city, m.heard);
  row.push(d.open && d.open.e1, d.open && d.open.e2, d.contact, d.startedAt);
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
      if (body.hp) return fail('rejected', 400); // honeypot
      const bad = validate(body);
      if (bad) return fail(bad, 400);

      const store = makeStore(env, 'survey');
      if (!store) return fail('storage_unbound', 503);

      const data = normalize(body, request);
      const id = 'S' + dayKey().replace(/-/g, '') + '-' + crypto.randomUUID().slice(0, 8);
      const ipHash = (await sha256Hex(clientIp(request) + '|research-survey')).slice(0, 16);
      await store.put(id, data.submittedAt, ipHash, data.ua, data);
      const count = await store.count();
      return json({ ok: true, id, count, storage: store.kind }, 200);
    }

    if (request.method === 'GET') {
      if (!(await authorized(withHash, url.searchParams.get('key') || ''))) {
        return fail('unauthorized', 401);
      }
      const store = makeStore(env, 'survey');
      if (!store) return fail('storage_unbound', 503);
      const format = (url.searchParams.get('format') || 'json').toLowerCase();
      const items = await store.list();
      if (format === 'csv') {
        return csvResponse(
          toCsv(CSV_HEADER, items.map(toRow)),
          'survey-responses-' + dayKey() + '.csv'
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
      const store = makeStore(env, 'survey');
      if (!store) return fail('storage_unbound', 503);
      await store.delete(id);
      return json({ ok: true, deleted: id, count: await store.count(), storage: store.kind }, 200);
    }

    return fail('method_not_allowed', 405);
  } catch (err) {
    console.error('[survey] 处理失败', err && err.stack ? err.stack : err);
    return fail('server_error', 500);
  }
}
