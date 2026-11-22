// 研究项目 · 匿名问卷后端（Cloudflare Pages Function）· v2（2026-11-22）
//
// 路由：/api/survey
//   POST            公开提交一份问卷（无需鉴权）
//   GET  ?key=KEY   导出全部答卷，format=json（默认）或 format=csv
//   DELETE ?key=&id= 删除单条（自检数据清理）
//
// v2 结构：5 张卡（A 刻板×组合一、B 反刻板×组合一、C 刻板×组合二、D 反刻板×组合二、E 中性）
// 每卡 4 个 1–7 量表（fair/follow/bias/realism）+ 2 个操纵检验（care/repair）+ 1 个选填理由
// 另有注意力检查 attn（必须为 3）

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
const N_CARDS = 5;

function validate(body) {
  if (body.consent !== true) return 'consent_required';
  if (body.adult !== true) return 'adult_required';
  if (!GENDERS.has(body.gender)) return 'bad_gender';
  if (!isScale(body.age, 1, 5)) return 'bad_age';
  if (!isScale(body.marital, 1, 4)) return 'bad_marital';
  if (!isScale(body.live, 1, 5)) return 'bad_live';
  if (!isScale(body.main, 1, 5)) return 'bad_main';
  if (!isScale(body.timeUse, 1, 5)) return 'bad_timeUse';
  if (!isScale(body.edu, 1, 4)) return 'bad_edu';
  if (!isScale(body.city, 1, 4)) return 'bad_city';
  if (typeof body.aiUsed !== 'boolean') return 'bad_aiUsed';

  const order = body.order;
  if (!Array.isArray(order) || order.length !== N_CARDS) return 'bad_order';
  const seenOrder = new Set(order.map(Number));
  if (seenOrder.size !== N_CARDS || [...seenOrder].some((n) => n < 0 || n > N_CARDS - 1)) return 'bad_order';

  const cards = body.cards;
  if (!Array.isArray(cards) || cards.length !== N_CARDS) return 'bad_cards';
  const seenCards = new Set();
  for (const c of cards) {
    if (!c || typeof c !== 'object') return 'bad_card';
    const i = Number(c.i);
    if (!Number.isInteger(i) || i < 0 || i > N_CARDS - 1 || seenCards.has(i)) return 'bad_card_index';
    seenCards.add(i);
    for (const k of ['fair', 'follow', 'bias', 'realism']) {
      if (!isScale(c[k], 1, 7)) return 'bad_' + k;
    }
    if (!MANIP.has(c.care)) return 'bad_care';
    if (!MANIP.has(c.repair)) return 'bad_repair';
    if (typeof c.why !== 'string' || c.why.length > 300) return 'bad_why';
  }

  const gen = body.gen;
  if (!gen || typeof gen !== 'object') return 'bad_gen';
  for (const k of ['c1', 'c2', 'c3', 'c4']) if (!isScale(gen[k], 1, 5)) return 'bad_' + k;

  if (Number(body.attn) !== 3) return 'bad_attn';

  const post = body.post;
  if (!post || typeof post !== 'object') return 'bad_post';
  if (!isScale(post.use, 1, 7) || !isScale(post.neutral, 1, 7) || !isScale(post.understand, 1, 7)) {
    return 'bad_post_scale';
  }
  if (!isScale(post.heard, 1, 3)) return 'bad_heard';

  const open = body.open;
  if (!open || typeof open !== 'object') return 'bad_open';
  if (typeof open.e1 !== 'string' || open.e1.length > 800) return 'bad_e1';
  if (typeof open.e2 !== 'string' || open.e2.length > 800) return 'bad_e2';
  if (typeof body.contact !== 'string' || body.contact.length > 80) return 'bad_contact';

  return null;
}

function normalize(body, request) {
  return {
    v: clampStr(body.v, 20) || '2026-11-22',
    consent: true,
    adult: true,
    gender: body.gender,
    age: String(body.age),
    marital: String(body.marital),
    live: String(body.live),
    main: String(body.main),
    timeUse: String(body.timeUse),
    edu: String(body.edu),
    city: String(body.city),
    aiUsed: body.aiUsed,
    order: body.order.map(Number),
    cards: body.cards.map((c) => ({
      i: Number(c.i),
      fair: Number(c.fair),
      follow: Number(c.follow),
      bias: Number(c.bias),
      realism: Number(c.realism),
      care: c.care,
      repair: c.repair,
      why: clampStr(c.why, 300),
    })),
    gen: {
      c1: Number(body.gen.c1),
      c2: Number(body.gen.c2),
      c3: Number(body.gen.c3),
      c4: Number(body.gen.c4),
    },
    attn: Number(body.attn),
    post: {
      use: Number(body.post.use),
      neutral: Number(body.post.neutral),
      understand: Number(body.post.understand),
      heard: String(body.post.heard),
    },
    open: { e1: clampStr(body.open.e1, 800), e2: clampStr(body.open.e2, 800) },
    contact: clampStr(body.contact, 80),
    durationMs: Math.max(0, Math.min(Number(body.durationMs) || 0, 86400000)),
    startedAt: clampStr(body.startedAt, 40),
    submittedAt: new Date().toISOString(),
    ua: clampStr(request.headers.get('user-agent') || '', 300),
  };
}

const CARD_FIELDS = ['fair', 'follow', 'bias', 'realism', 'care', 'repair', 'why'];

const CSV_HEADER = (() => {
  const h = [
    'id', 'createdAt', 'durationMs', 'gender', 'age', 'marital', 'live', 'main',
    'timeUse', 'edu', 'city', 'aiUsed', 'order',
  ];
  for (let i = 0; i < N_CARDS; i++) {
    for (const f of CARD_FIELDS) h.push('c' + i + '_' + f);
  }
  h.push(
    'gen_c1', 'gen_c2', 'gen_c3', 'gen_c4', 'attn',
    'post_use', 'post_neutral', 'post_understand', 'heard',
    'e1', 'e2', 'contact', 'startedAt'
  );
  return h;
})();

function toRow(item) {
  const d = item.data || {};
  const byCard = {};
  for (const c of d.cards || []) byCard[c.i] = c;
  const row = [
    item.id, item.createdAt, d.durationMs, d.gender, d.age, d.marital, d.live, d.main,
    d.timeUse, d.edu, d.city, d.aiUsed, (d.order || []).join('|'),
  ];
  for (let i = 0; i < N_CARDS; i++) {
    const c = byCard[i] || {};
    for (const f of CARD_FIELDS) row.push(c[f]);
  }
  const g = d.gen || {};
  const p = d.post || {};
  row.push(
    g.c1, g.c2, g.c3, g.c4, d.attn,
    p.use, p.neutral, p.understand, p.heard,
    d.open && d.open.e1, d.open && d.open.e2, d.contact, d.startedAt
  );
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
