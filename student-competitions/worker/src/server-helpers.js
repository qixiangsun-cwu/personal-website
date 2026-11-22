// Server-side helpers: knowledge search (D1), risk profile, work orders (port of db.py/risk.py/orders.py)
import { asrNorm } from "./rules.js";

const KN_STOP_WORDS = new Set([
  "的", "了", "是", "在", "我", "你", "他", "吗", "呢", "吧", "啊", "和", "与", "就",
  "都", "也", "还", "要", "有", "没", "不", "这", "那", "个", "们", "会", "能", "对",
  "请问", "什么", "怎么", "为什么", "如何", "可以", "是不是", "我们", "你们",
]);
const KN_STOP_CHARS = new Set("的了是在吗呢吧啊和与就都也还要有没不这那个们会能对呀哦嗯".split(""));

function bigrams(text) {
  const s = String(text || "")
    .replace(/[\s，。！？；：、（）()《》【】"'\u3000]/g, "")
    .toLowerCase();
  const grams = [];
  for (let i = 0; i < s.length - 1; i++) {
    const g = s.slice(i, i + 2);
    if (KN_STOP_WORDS.has(g)) continue;
    if ([...g].every((c) => KN_STOP_CHARS.has(c))) continue;
    grams.push(g);
  }
  return grams;
}

export async function searchKnowledge(db, text, topK = 3, excludeSources = ["merchant"], minScore = 0.2) {
  const q = String(text || "").trim();
  if (!q) return [];
  const qgrams = bigrams(q);
  if (!qgrams.length) return [];
  const { results } = await db.prepare("SELECT id, source, title, content FROM knowledge_chunks").all();
  const rows = results || [];
  if (!rows.length) return [];
  const docs = rows.map((r) => {
    const title = String(r.title || "");
    const content = String(r.content || "");
    return { row: r, title, content, tgrams: bigrams(title), cgrams: bigrams(content), clen: content.length || 1 };
  });
  const N = docs.length;
  const df = new Map();
  for (const d of docs) {
    const seen = new Set([...d.tgrams, ...d.cgrams]);
    for (const g of seen) df.set(g, (df.get(g) || 0) + 1);
  }
  const idf = (g) => Math.log(1 + N / (1 + (df.get(g) || 0)));
  const scored = [];
  for (const d of docs) {
    if (excludeSources.includes(d.row.source)) continue;
    const tcount = new Map();
    for (const g of d.tgrams) tcount.set(g, (tcount.get(g) || 0) + 1);
    const ccount = new Map();
    for (const g of d.cgrams) ccount.set(g, (ccount.get(g) || 0) + 1);
    let s = 0;
    for (const g of qgrams) {
      if (tcount.has(g)) s += 2.5 * (tcount.get(g) > 0 ? 1 : 0) * idf(g);
      if (ccount.has(g)) s += 1.0 * (ccount.get(g) > 0 ? 1 : 0) * idf(g);
    }
    s = s / (1 + 0.015 * Math.sqrt(d.clen));
    if (s > 0) scored.push({ id: d.row.id, source: d.row.source, title: d.row.title, content: d.row.content, score: s });
  }
  if (!scored.length) return [];
  scored.sort((a, b) => b.score - a.score);
  const best = scored[0].score;
  const cutoff = Math.max(minScore, best * 0.5);
  const out = [];
  const seenTitles = new Set();
  for (const h of scored) {
    if (h.score < cutoff) break;
    const k = String(h.title || "");
    if (seenTitles.has(k)) continue;
    seenTitles.add(k);
    out.push({ ...h, score: 1 / (0.1 + h.score) });
    if (out.length >= topK) break;
  }
  return out;
}

export async function priorCalls(db, merchantId, limit = 3, excludeCallId = "") {
  const { results } = await db
    .prepare(
      `SELECT c.call_id, c.ended_at, c.direction, c.closed_loop, c.stop_stage, c.result_json
         FROM calls c
        WHERE c.merchant_id = ? AND c.call_id <> ?
          AND c.status IN ('completed','failed','rejected','missed')
        ORDER BY COALESCE(c.ended_at, c.started_at) DESC
        LIMIT ?`,
    )
    .bind(merchantId, excludeCallId || "", limit)
    .all();
  return (results || []).map((r) => {
    let res = {};
    try {
      res = r.result_json ? JSON.parse(r.result_json) : {};
    } catch {
      res = {};
    }
    return {
      call_id: r.call_id,
      ended_at: r.ended_at || "",
      direction: r.direction || "outbound",
      closed_loop: r.closed_loop === 1,
      stop_stage: r.stop_stage || "",
      resolved: res.resolved || [],
      pending: res.pending || [],
    };
  });
}

const LEVEL_NAMES = { A: "轻微关注", B: "重点管控", C: "高风险" };

export async function riskProfile(db, merchantId) {
  const r = await db
    .prepare("SELECT * FROM merchant_risk WHERE merchant_id = ?")
    .bind(merchantId)
    .first();
  if (!r) return {};
  let tags = [];
  try {
    tags = r.tags ? JSON.parse(r.tags) : [];
  } catch {
    tags = [];
  }
  return {
    merchant_id: r.merchant_id,
    level: r.level || "",
    level_name: LEVEL_NAMES[r.level] || r.level || "",
    tags,
    score: r.score || 0,
    b_count: r.b_count || 0,
    a_count: r.a_count || 0,
    last_kind: r.last_kind || "",
    last_at: r.last_at || "",
    note: r.note || "",
  };
}

export async function riskProfiles(db, ids) {
  const out = {};
  for (const id of ids || []) {
    out[id] = await riskProfile(db, id);
  }
  return out;
}

export function asrNormExport(text) {
  return asrNorm(text);
}
