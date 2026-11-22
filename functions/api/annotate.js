// 图片性别标注（第二标注者）· Cloudflare Pages Function
//
// POST /api/annotate            公开提交（匿名）
// GET  /api/annotate?key=...    管理端读取（JSON 或 format=csv）
// DELETE /api/annotate?key=&id= 删除一条
//
// 存储：D1 表 ann_responses（首次请求自动建）；未绑定退回 KV（research:annotate:*）

import {
  authorized, clampStr, csvResponse, clientIp, fail, json, makeStore,
  readBody, sha256Hex, toCsv,
} from './_research-lib.js';

const V = '2026-11-22';
const N = 100;
const LABELS = new Set(['M', 'F', 'U']);

function validAnnotator(s) {
  const t = String(s == null ? '' : s).trim();
  return t.length >= 2 && t.length <= 20;
}

function normalize(body) {
  const labels = {};
  for (let i = 1; i <= N; i++) labels[String(i)] = 'U';
  const src = body && body.labels;
  if (!src || typeof src !== 'object') return null;
  for (const k of Object.keys(src)) {
    const idx = Number(k);
    if (!Number.isInteger(idx) || idx < 1 || idx > N) return null;
    const v = String(src[k] || '').toUpperCase();
    if (!LABELS.has(v)) return null;
    labels[String(idx)] = v;
  }
  // 至少 80 张有真实判断，避免空提交
  const judged = Object.values(labels).filter((v) => v === 'M' || v === 'F').length;
  if (judged < 80) return null;
  return labels;
}

function csvHeader() {
  const head = ['id', 'createdAt', 'annotator', 'durationMs', 'judged', 'unknown', 'startedAt'];
  for (let i = 1; i <= N; i++) head.push('a' + String(i).padStart(3, '0'));
  return head;
}

function toRow(item) {
  const d = item.data || {};
  const labels = d.labels || {};
  const row = [
    item.id, item.createdAt, d.annotator || '', d.durationMs == null ? '' : d.durationMs,
    d.judged == null ? '' : d.judged, d.unknown == null ? '' : d.unknown, d.startedAt || '',
  ];
  for (let i = 1; i <= N; i++) row.push(labels[String(i)] || '');
  return row;
}

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();
  const store = makeStore(env, 'annotate');
  if (!store) return fail('storage_unavailable', 503);

  if (method === 'POST') {
    const { body, error } = await readBody(request);
    if (error) return fail(error, 400);
    if (body.hp) return fail('rejected', 400);
    if (!validAnnotator(body.annotator)) return fail('bad_annotator', 400);
    const labels = normalize(body);
    if (!labels) return fail('bad_labels', 400);
    const judged = Object.values(labels).filter((v) => v === 'M' || v === 'F').length;
    const payload = {
      v: V,
      annotator: clampStr(String(body.annotator).trim(), 20),
      labels,
      judged,
      unknown: N - judged,
      durationMs: Number(body.durationMs) || 0,
      startedAt: clampStr(body.startedAt, 40),
      submittedAt: new Date().toISOString(),
    };
    const id = 'A' + new Date().toISOString().slice(0, 10).replace(/-/g, '') + '-' +
      Math.random().toString(16).slice(2, 10);
    const ipHash = (await sha256Hex(clientIp(request) + '|research-annotate')).slice(0, 16);
    await store.put(id, new Date().toISOString(), ipHash, clampStr(request.headers.get('user-agent'), 200), payload);
    const count = await store.count();
    return json({ ok: true, id, count, storage: store.kind });
  }

  if (method === 'GET') {
    const key = url.searchParams.get('key');
    if (!(await authorized(env, key))) return fail('unauthorized', 401);
    const items = await store.list();
    const format = (url.searchParams.get('format') || 'json').toLowerCase();
    if (format === 'csv') {
      const rows = items.map(toRow);
      return csvResponse(toCsv(csvHeader(), rows), 'annotations.csv');
    }
    return json({ ok: true, count: items.length, storage: store.kind, items });
  }

  if (method === 'DELETE') {
    const key = url.searchParams.get('key');
    if (!(await authorized(env, key))) return fail('unauthorized', 401);
    const id = String(url.searchParams.get('id') || '').trim();
    if (!/^[A-Za-z0-9-]{6,40}$/.test(id)) return fail('bad_id', 400);
    await store.delete(id);
    const count = await store.count();
    return json({ ok: true, deleted: id, count, storage: store.kind });
  }

  return fail('method_not_allowed', 405);
}
