import { FILES } from "./frontend.gen.js";
import * as RP from "./rules.js";
import * as prompts from "./prompts.js";
import { searchKnowledge, priorCalls, riskProfile, riskProfiles } from "./server-helpers.js";
import { SUBMIT_HTML } from "./submit-page.js";

export { CallRoom } from "./call-room.js";
export { MonitorBus } from "./monitor-bus.js";

const FILE_MAP = new Map(FILES.map((f) => [f.path, f]));
const TEXT = { "content-type": "text/plain; charset=utf-8" };

const json = (obj, init = {}) =>
  new Response(JSON.stringify(obj), {
    ...init,
    headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*", ...(init.headers || {}) },
  });

const err = (msg, code = 400) => json({ ok: false, error: msg }, { status: code });

function html(name) {
  const f = FILE_MAP.get(name);
  if (!f) return new Response("not found", { status: 404 });
  return new Response(f.body, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function median(arr) {
  const a = arr.slice().sort((x, y) => x - y);
  if (!a.length) return 0;
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}
function p95(arr) {
  const a = arr.slice().sort((x, y) => x - y);
  if (!a.length) return 0;
  return a[Math.ceil(a.length * 0.95) - 1];
}

async function all(db, sql, ...binds) {
  const r = await db.prepare(sql).bind(...binds).all();
  return r.results || [];
}
async function one(db, sql, ...binds) {
  return await db.prepare(sql).bind(...binds).first();
}

function parseJson(v, fallback) {
  if (v === null || v === undefined || v === "") return fallback;
  try {
    return JSON.parse(v);
  } catch (_e) {
    return fallback;
  }
}

// ---------------------------------------------------------------- metrics helpers
function redlineStats(events) {
  const cats = new Set();
  let intercepted = 0;
  let blocked = 0;
  let acked = 0;
  const byName = {};
  for (const ev of events) {
    const p = parseJson(ev.payload, {}) || {};
    const branch = p.branch || "";
    const key = p.key || p.name || "OTHER";
    cats.add(key);
    byName[key] = (byName[key] || 0) + 1;
    const severe = p.severe || branch === "C" || (p.level === "B" && !branch);
    if (severe) blocked += 1;
    if (branch === "A" || p.ack) acked += 1;
    if (p.say || p.guide || p.reply || p.outcome || p.action || branch || p.close || p.ack) intercepted += 1;
  }
  const triggered = events.length;
  return {
    total: triggered,
    triggered,
    intercepted,
    missed: Math.max(0, triggered - intercepted),
    rate: triggered ? Math.round((intercepted / triggered) * 1000) / 10 : 0,
    definition: "红线触发后被拦截并纠正的比例（含引导话术）",
    offtopic_excluded: 0,
    blocked,
    accused_acked: acked,
    warn: triggered - acked,
    by_name: byName,
    strict: !!RP.STRICT,
    categories: RP.redlineCategories().map((c) => ({ key: c.key, name: c.name, level: c.level })),
    risk: cats.size,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method.toUpperCase();

    // ---------------- 诊断路由：默认关闭，只有 env.DIAG = "1" 时才暴露
    if (path.startsWith("/__diag") || path === "/ws/echo" || path === "/ws/doecho") {
      if (String(env.DIAG || "") !== "1") return new Response("not found", { status: 404 });
    }
    if (method === "GET" && path === "/__diagcall") {
      const cid = url.searchParams.get("id") || "";
      const rows = await all(
        env.DB,
        `SELECT turn_no, role, text, intent, state FROM call_turns WHERE call_id = ? ORDER BY turn_no ASC, id ASC`,
        cid
      );
      const call = await one(env.DB, `SELECT call_id, status, closed_loop, stop_stage, result_json FROM calls WHERE call_id = ?`, cid);
      return json({ call, turns: rows });
    }
    if (method === "GET" && path === "/__diagroute") {
      const t = url.searchParams.get("t") || "是我";
      const state = url.searchParams.get("state") || "S1-VERIFY_PERSON";
      const out = { input: t, state };
      try {
        const { CallAgent } = await import("./call-agent.js");
        const a = new CallAgent({
          callId: "diagroute",
          merchant: {
            id: 1,
            company_name: "杭州云裳服饰有限公司",
            shop_name: "云裳服饰2026新旗舰店",
            contact_name: "陈主管",
          },
          scenario: {
            id: 6,
            level1_category: "商标注册证",
            level2_category: "商标状态不符合要求",
            close_method: "电话",
            script_text: "尊敬的商家伙伴您好",
          },
          versionNote: "",
          env: {},
          db: null,
          offline: true,
        });
        a.state = state;
        a.client = null;
        a.offline = true;
        const aux = {};
        const r = a._route(t, false, false, aux, "other");
        out.route = r;
        out.fixed = a._fixed_reply(r.ps, r.ns, r.intent, aux._vio || null, aux);
        out.reconfirm = a._reconfirm_advance();
      } catch (e) {
        out.err = String(e && e.message);
        out.stack = String(e && e.stack).slice(0, 800);
      }
      return json(out);
    }
    if (method === "GET" && path === "/__diagrules") {
      const t = url.searchParams.get("t") || "是我";
      const out = { input: t };
      try {
        out.asrNorm = RP.asrNorm(t);
      } catch (e) { out.asrNormErr = String(e && e.message); }
      try {
        out.identityKind = RP.identityKind(out.asrNorm ?? t);
      } catch (e) { out.identityKindErr = String(e && e.message); }
      try {
        out.affirm = RP.affirmStrongHit(out.asrNorm ?? t);
      } catch (e) { out.affirmErr = String(e && e.message); }
      return json(out);
    }
    if (method === "GET" && path === "/__diagsql") {
      const cid = url.searchParams.get("id") || "";
      const out = {};
      try {
        out.plain = await one(env.DB, `SELECT call_id, status, merchant_id, scenario_id FROM calls WHERE call_id = ?`, cid);
      } catch (e) { out.plainErr = String(e && e.message); }
      try {
        out.joined = await one(
          env.DB,
          `SELECT c.*, m.company_name, m.shop_name, m.contact_name, s.level1_category, s.level2_category, s.close_method, s.script_text
             FROM calls c LEFT JOIN merchants m ON m.id = c.merchant_id LEFT JOIN scenarios s ON s.id = c.scenario_id
            WHERE c.call_id = ?`,
          cid
        );
      } catch (e) { out.joinedErr = String(e && e.message); }
      try {
        out.anyCall = await one(env.DB, `SELECT call_id FROM calls ORDER BY id DESC LIMIT 1`);
      } catch (e) { out.anyErr = String(e && e.message); }
      out.reqId = cid;
      return json(out);
    }
    if (method === "GET" && path === "/__diag") {
      return json({ ok: true, build: "diag-1", path, query: url.search, hasProbe: url.searchParams.has("probe") });
    }
    if (method === "GET" && (path === "/sc" || path === "/sc/" || path === "/submit")) {
      return new Response(SUBMIT_HTML, {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    // 作品入口：/lingting-2026/ 直接进商家侧体验页（学生/评委对外只需要这一个地址）
    if (method === "GET" && /^\/lingting-2026\/?$/.test(path)) {
      return Response.redirect(url.origin + "/merchant", 302);
    }
    if (method === "GET" && /^\/lingting-2026\/(admin|console)\/?$/.test(path)) {
      return Response.redirect(url.origin + "/", 302);
    }
    if (method === "GET" && /^\/lingting-2026\/(kb|rag|knowledge)\/?$/.test(path)) {
      return Response.redirect(url.origin + "/rag", 302);
    }
    if (method === "GET" && (path === "/" || path === "/admin" || path === "/admin.html")) return html("admin.html");
    if (method === "GET" && (path === "/merchant" || path === "/merchant.html")) return html("merchant.html");
    if (method === "GET" && (path === "/rag" || path === "/rag.html" || path === "/rag_knowledge_view.html"))
      return html("rag_knowledge_view.html");
    if (method === "GET" && path.startsWith("/static/")) {
      const name = path.slice("/static/".length);
      const f = FILE_MAP.get(name);
      if (!f) return new Response("not found", { status: 404 });
      const ct = name.endsWith(".svg") ? "image/svg+xml" : name.endsWith(".css") ? "text/css" : "application/octet-stream";
      return new Response(f.body, { headers: { "content-type": ct } });
    }

    try {
      if (path === "/ws/echo") {
        const pair = new WebSocketPair();
        const [client, server] = Object.values(pair);
        server.accept();
        server.addEventListener("message", (ev) => {
          try {
            server.send(typeof ev.data === "string" ? "echo:" + ev.data : "bin:" + ev.data.byteLength);
          } catch (_e) {}
        });
        return new Response(null, { status: 101, webSocket: client });
      }
      if (path === "/ws/doecho") {
        const stub = env.CALL_ROOM.get(env.CALL_ROOM.idFromName("echo-" + Math.random().toString(36).slice(2)));
        return stub.fetch("https://call/echo", { headers: { upgrade: "websocket" } });
      }
      if (path === "/__diaglog") {        const rows = await all(env.DB, "SELECT key, value FROM settings WHERE key LIKE '__diag_%'");
        return json({ ok: true, rows });
      }
      if (path.startsWith("/__wscall/")) {
        const callId = decodeURIComponent(path.slice("/__wscall/".length));
        try {
          const stub = env.CALL_ROOM.get(env.CALL_ROOM.idFromName(callId));
          const r = await stub.fetch(`https://call/${encodeURIComponent(callId)}/state`);
          const body = await r.text();
          return json({ ok: true, status: r.status, body: body.slice(0, 2000) });
        } catch (e) {
          return json({ ok: false, error: String((e && e.message) || e), detail: String((e && e.stack) || "").slice(0, 1500) }, { status: 500 });
        }
      }
      if (path === "/ws/monitor") {
        const stub = env.MONITOR_BUS.get(env.MONITOR_BUS.idFromName("global"));
        return stub.fetch("https://monitor/ws", request);
      }
      if (path.startsWith("/ws/call/")) {
        const callId = decodeURIComponent(path.slice("/ws/call/".length));
        if (method === "GET" && url.searchParams.has("probe")) {
          try {
            const stub = env.CALL_ROOM.get(env.CALL_ROOM.idFromName(callId));
            const r = await stub.fetch(`https://call/${encodeURIComponent(callId)}/state`);
            const body = await r.text();
            return json({ ok: true, status: r.status, body: body.slice(0, 2000) });
          } catch (e) {
            return json({ ok: false, error: String((e && e.message) || e), detail: String((e && e.stack) || "").slice(0, 1500) }, { status: 500 });
          }
        }
        return await openCallSocket(request, env, callId, url);
      }
      if (path.startsWith("/api/")) {
        return await handleApi(request, env, url, method, path);
      }
    } catch (e) {
      console.error("worker error", e && e.stack ? e.stack : e);
      return err(String((e && e.message) || e), 500);
    }

    return new Response("not found", { status: 404 });
  },
};

// ---------------------------------------------------------------- 作品提交
const SUBMIT_LIMITS = { team: 40, school: 40, competition: 80, track: 40, title: 60, url: 300, desc: 600, members: 120, contact: 60 };
const REQUIRED = ["team", "school", "competition", "title", "url", "desc"];

function cut(s, n) {
  return String(s == null ? "" : s).trim().slice(0, n);
}

async function sha256hex(str) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function submitWork(request, env) {
  const db = env.DB;
  let body;
  try {
    body = await request.json();
  } catch (_e) {
    return json({ ok: false, error: "请求格式不正确" }, { status: 400 });
  }
  const data = {};
  for (const k of Object.keys(SUBMIT_LIMITS)) data[k] = cut(body[k], SUBMIT_LIMITS[k]);

  for (const k of REQUIRED) {
    if (!data[k]) return json({ ok: false, error: "缺少必填项：" + k }, { status: 400 });
  }
  if (!/^https?:\/\/[^\s]+$/i.test(data.url)) {
    return json({ ok: false, error: "作品链接需要以 http:// 或 https:// 开头" }, { status: 400 });
  }
  if (data.desc.length < 10) {
    return json({ ok: false, error: "作品简介请至少填写 10 个字" }, { status: 400 });
  }

  // 简单限流：同一 IP 每小时最多 5 条
  const ip = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Real-IP") || "";
  const ipHash = ip ? (await sha256hex(ip + "|qxs-submit")).slice(0, 32) : "";
  if (ipHash) {
    const recent = await one(
      db,
      `SELECT COUNT(*) AS n FROM submissions WHERE ip_hash = ? AND created_at > datetime('now', '-1 hour')`,
      ipHash
    );
    if (recent && recent.n >= 5) {
      return json({ ok: false, error: "提交过于频繁，请稍后再试" }, { status: 429 });
    }
  }

  const uid = crypto.randomUUID().replace(/-/g, "").slice(0, 10);
  try {
    await db
      .prepare(
        `INSERT INTO submissions (uid, team, school, competition, track, title, url, description, members, contact, ip_hash)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(uid, data.team, data.school, data.competition, data.track, data.title, data.url, data.desc, data.members, data.contact, ipHash)
      .run();
  } catch (e) {
    return json({ ok: false, error: "保存失败，请稍后重试" }, { status: 500 });
  }
  return json({ ok: true, id: uid });
}

async function openCallSocket(request, env, callId, url) {  const up = (request.headers.get("Upgrade") || "").toLowerCase();
  if (up !== "websocket") return err("expected websocket", 400);
  const call = await one(
    env.DB,
    `SELECT c.*, m.company_name, m.shop_name, m.contact_name, s.level1_category, s.level2_category, s.close_method, s.script_text
       FROM calls c LEFT JOIN merchants m ON m.id = c.merchant_id LEFT JOIN scenarios s ON s.id = c.scenario_id
      WHERE c.call_id = ?`,
    callId
  );
  if (!call) {
    // 与原版一致：先握手再下发错误事件
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    try {
      await env.DB.prepare("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)")
        .bind("__diag_wslookup_" + new Date().toISOString(), "callId=" + callId + " len=" + callId.length + " up=" + up)
        .run();
    } catch (_e) {}
    server.send(JSON.stringify({ type: "error", msg: "通话不存在" }));
    setTimeout(() => {
      try {
        server.close(1000, "call not found");
      } catch (_e) {}
    }, 50);
    return new Response(null, { status: 101, webSocket: client });
  }
  const id = env.CALL_ROOM.idFromName(callId);
  const stub = env.CALL_ROOM.get(id);
  // 只传可序列化的纯数据：agent / asr / socket 等运行时对象由 DO 自己建立
  const plainCall = {};
  for (const [k, v] of Object.entries(call)) {
    if (v === null || typeof v === "string" || typeof v === "number" || typeof v === "boolean") plainCall[k] = v;
  }
  const session = {
    call_id: callId,
    call: plainCall,
    offline_echo: url.searchParams.get("echo") === "1",
    skip_asr_only: url.searchParams.get("noasr") === "1",
    skip_asr_hard: url.searchParams.get("noasrhard") === "1",
  };
  // 会话参数走 header（DO 的 body 在 101 升级时不可靠）
  let encoded;
  try {
    encoded = btoa(unescape(encodeURIComponent(JSON.stringify({ session }))));
  } catch (e) {
    encoded = "";
  }
  try {
    const resp = await stub.fetch(`https://call/${encodeURIComponent(callId)}/attach`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        upgrade: "websocket",
        "x-call-session": encoded,
      },
      body: "{}",
    });
    // 关键：101 响应带 webSocket，必须原样返回（不能重建 Response）
    if (resp.status === 101) return resp;
    const body = await resp.text();
    console.error("attach non-101", resp.status, body);
    return err("attach failed: " + body.slice(0, 300), 500);
  } catch (e) {
    console.error("attach failed", e && e.stack ? e.stack : e);
    return json({ ok: false, stage: "attach", error: String((e && e.message) || e), detail: String((e && e.stack) || "").slice(0, 800) }, { status: 500 });
  }
}

async function handleApi(request, env, url, method, path) {
  const db = env.DB;
  const q = url.searchParams;

  // ---------------- merchants
  if (path === "/api/merchants" && method === "GET") {
    const rows = await all(db, `SELECT * FROM merchants WHERE status = 'active' ORDER BY id`);
    const rk = await riskProfiles(db, rows.map((r) => r.id));
    return json(rows.map((r) => ({ ...r, risk: rk[r.id] || null })));
  }

  const mMerchant = path.match(/^\/api\/merchants\/(\d+)$/);
  if (mMerchant && method === "GET") {
    const id = Number(mMerchant[1]);
    const merchant = await one(db, `SELECT * FROM merchants WHERE id = ?`, id);
    if (!merchant) return err("商家不存在", 404);
    const calls = await all(
      db,
      `SELECT c.*, s.level2_category FROM calls c LEFT JOIN scenarios s ON s.id = c.scenario_id
        WHERE c.merchant_id = ? ORDER BY c.id DESC LIMIT 50`,
      id
    );
    const tickets = await all(
      db,
      `SELECT t.*, c.reject_reason FROM tickets t LEFT JOIN calls c ON c.call_id = t.call_id
        WHERE t.merchant_id = ? ORDER BY t.id DESC LIMIT 50`,
      id
    );
    const risk = await riskProfile(db, id);
    const summary = {
      total_calls: calls.length,
      completed_calls: calls.filter((c) => c.status === "completed").length,
      closed_loop: calls.filter((c) => c.closed_loop === 1).length,
      pending_items: tickets.filter((t) => t.status === "pending_resubmit" || t.status === "pending_followup").length,
    };
    return json({ ...merchant, calls, tickets, risk, summary });
  }

  if (path === "/api/scenarios" && method === "GET") {
    return json(await all(db, `SELECT * FROM scenarios ORDER BY id`));
  }

  // ---------------- calls
  if (path === "/api/calls" && method === "GET") {
    const rows = await all(
      db,
      `SELECT c.*, m.company_name, m.shop_name, s.level1_category, s.level2_category
         FROM calls c LEFT JOIN merchants m ON m.id = c.merchant_id LEFT JOIN scenarios s ON s.id = c.scenario_id
        ORDER BY c.id DESC LIMIT 100`
    );
    return json(rows);
  }

  if (path === "/api/calls" && method === "POST") {
    let body = {};
    try {
      body = await request.json();
    } catch (_e) {
      body = {};
    }
    const merchantId = Number(body.merchant_id || body.merchant || 0);
    const merchant = await one(db, `SELECT * FROM merchants WHERE id = ?`, merchantId);
    if (!merchant) return err("商家不存在", 404);
    let scenario = null;
    if (body.scenario_id) scenario = await one(db, `SELECT * FROM scenarios WHERE id = ?`, Number(body.scenario_id));
    if (!scenario) scenario = await one(db, `SELECT * FROM scenarios WHERE script_text <> '' ORDER BY id LIMIT 1`);
    const callId = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const version = await one(db, `SELECT id FROM rsi_versions WHERE status = 'active' ORDER BY id DESC LIMIT 1`);
    await db
      .prepare(
        `INSERT INTO calls (call_id, merchant_id, scenario_id, direction, status, version_id, reject_reason, started_at, created_at)
         VALUES (?, ?, ?, ?, 'ringing', ?, ?, datetime('now'), datetime('now'))`
      )
      .bind(callId, merchantId, scenario ? scenario.id : null, body.direction || "outbound", version ? version.id : null, scenario ? scenario.level2_category : null)
      .run();
    await publish(env, { type: "call_ringing", call_id: callId, merchant: merchant.company_name });
    return json({ call_id: callId, merchant: merchant.company_name, scenario: scenario ? scenario.level2_category : null });
  }

  const mCall = path.match(/^\/api\/calls\/([A-Za-z0-9_-]+)$/);
  if (mCall && method === "GET") {
    const cid = mCall[1];
    const call = await one(
      db,
      `SELECT c.*, m.company_name, m.shop_name, s.level1_category, s.level2_category, s.close_method, s.script_text
         FROM calls c LEFT JOIN merchants m ON m.id = c.merchant_id LEFT JOIN scenarios s ON s.id = c.scenario_id
        WHERE c.call_id = ?`,
      cid
    );
    if (!call) return err("通话不存在", 404);
    const turns = await all(db, `SELECT * FROM call_turns WHERE call_id = ? ORDER BY turn_no ASC, id ASC`, cid);
    const ticket = await one(db, `SELECT * FROM tickets WHERE call_id = ? ORDER BY id DESC LIMIT 1`, cid);
    return json({ ...call, result: parseJson(call.result_json, null), turns, ticket, flags: [] });
  }

  const mAct = path.match(/^\/api\/calls\/([A-Za-z0-9_-]+)\/(answer|hangup|reject)$/);
  if (mAct && method === "POST") {
    const cid = mAct[1];
    const act = mAct[2];
    const call = await one(db, `SELECT * FROM calls WHERE call_id = ?`, cid);
    if (!call) return err("通话不存在", 404);
    if (act === "answer") {
      await db.prepare(`UPDATE calls SET status = 'in_progress', answered_at = datetime('now') WHERE call_id = ?`).bind(cid).run();
      await publish(env, { type: "call_answered", call_id: cid });
      return json({ ok: true });
    }
    if (act === "reject") {
      await db
        .prepare(`UPDATE calls SET status = 'rejected', ended_at = datetime('now') WHERE call_id = ?`)
        .bind(cid)
        .run();
      await publish(env, { type: "call_rejected", call_id: cid });
      return json({ ok: true });
    }
    // hangup
    if (["completed", "failed", "rejected", "pending_followup", "superseded"].includes(call.status)) {
      return json({ ok: true, already_final: true });
    }
    await db
      .prepare(`UPDATE calls SET status = 'pending_followup', ended_at = datetime('now'), stop_stage = ? WHERE call_id = ?`)
      .bind("商家中途挂断（REST）", cid)
      .run();
    await publish(env, { type: "call_finalized", call_id: cid, closed_loop: false });
    await publish(env, { type: "call_ended", call_id: cid });
    return json({ ok: true });
  }

  // ---------------- transcript
  if (path === "/api/transcript" && method === "GET") {
    const cid = q.get("call_id") || "";
    const rows = await all(
      db,
      `SELECT role, text, asr_confidence, created_at FROM call_turns WHERE call_id = ? ORDER BY turn_no ASC, id ASC`,
      cid
    );
    return json({
      call_id: cid,
      lines: rows.map((r, i) => ({
        key: "db" + i,
        role: r.role,
        text: r.text || "",
        tags: r.role === "merchant" && r.asr_confidence !== null ? ["识别置信 " + Math.round(Number(r.asr_confidence) * 100) + "%"] : [],
        ts: r.created_at || "",
      })),
      rev: rows.length,
      epoch: 1,
    });
  }

  if (path === "/api/transcript/line" && method === "POST") {
    let body = {};
    try {
      body = await request.json();
    } catch (_e) {
      body = {};
    }
    return json({ ok: true, echo: body });
  }

  // ---------------- metrics
  if (path === "/api/metrics" && method === "GET") {
    const calls = await all(db, `SELECT * FROM calls`);
    const turns = await all(db, `SELECT call_id, role, latency_ms, e2e_ms FROM call_turns`);
    const tickets = await all(db, `SELECT call_id, compliance_rate FROM tickets`);
    const total = calls.length;
    const completed = calls.filter((c) => c.status === "completed");
    const pendingFollowup = calls.filter((c) => c.status === "pending_followup");
    const superseded = calls.filter((c) => c.status === "superseded");
    const failed = calls.filter((c) => c.status === "failed");
    const closedLoop = calls.filter((c) => c.closed_loop === 1);
    const byCall = {};
    for (const t of tickets) byCall[t.call_id] = t;
    const comp = tickets.map((t) => Number(t.compliance_rate || 0));
    const lat = turns.filter((t) => t.e2e_ms).map((t) => Number(t.e2e_ms));
    const emotions = { angry: 0, anxious: 0, satisfied: 0, neutral: 0 };
    for (const c of calls) {
      const r = parseJson(c.result_json, null);
      const e = (r && r.emotion) || "neutral";
      if (e in emotions) emotions[e] += 1;
    }
    const turnsPerCall = {};
    for (const t of turns) if (t.role === "merchant") turnsPerCall[t.call_id] = (turnsPerCall[t.call_id] || 0) + 1;
    const turnCounts = Object.values(turnsPerCall);
    const events = await all(db, `SELECT payload FROM work_order_events WHERE kind = 'violation'`);
    const p50 = median(lat);
    return json({
      total_calls: total,
      valid_calls: completed.length,
      excluded_calls: superseded.length,
      completed: completed.length,
      pending_followup_calls: pendingFollowup.length,
      superseded_calls: superseded.length,
      failed_system_calls: failed.length,
      closed_loop_rate: completed.length ? Math.round((closedLoop.length / completed.length) * 1000) / 10 : 0,
      compliance_rate: comp.length ? Math.round((comp.reduce((a, b) => a + b, 0) / comp.length) * 1000) / 10 : 0,
      avg_turns: turnCounts.length ? Math.round((turnCounts.reduce((a, b) => a + b, 0) / turnCounts.length) * 10) / 10 : 0,
      low_turn_calls: turnCounts.filter((n) => n < 3).length,
      p50_ms: p50,
      p95_ms: p95(lat),
      est_cost_yuan: Math.round(total * 0.012 * 1000) / 1000,
      emotion_dist: emotions,
      latency_samples: lat.length,
      tool_stats: { count: 0, ok: 0, tools: [] },
      redline_stats: redlineStats(events),
      legacy_samples: 0,
      threshold_ms: 800,
      p50_pass: p50 > 0 ? p50 <= 800 : false,
      pass_rate: lat.length ? Math.round((lat.filter((x) => x <= 800).length / lat.length) * 1000) / 10 : 0,
      calls_3plus: turnCounts.filter((n) => n >= 3).length,
      calls_with_turns: turnCounts.length,
      turns_3plus_pass: turnCounts.filter((n) => n >= 3).length,
      stage_budget: {
        asr_tail_ms: 300,
        server_ms: 150,
        llm_first_ms: 800,
        tts_first_ms: 250,
        ws_play_ms: 80,
        spec_hit_rate: 0,
      },
      latency_definition: "端到端时延 = 商家说完 -> 首帧音频下发（毫秒）",
      targets: { p95_ms: 800.0, compliance_rate: 95.0, min_turns: 3 },
      metric_def: {
        closed_loop_rate: "闭环通话 / 有效通话",
        compliance_rate: "必说要素覆盖率均值",
        p95_ms: "端到端时延 95 分位",
      },
    });
  }

  if (path === "/api/metrics/v2" && method === "GET") {
    const base = await (async () => {
      const calls = await all(db, `SELECT * FROM calls`);
      const closed = calls.filter((c) => c.closed_loop === 1).length;
      const completed = calls.filter((c) => c.status === "completed").length;
      return {
        total_calls: calls.length,
        completed,
        closed_loop: closed,
        closed_loop_rate: completed ? Math.round((closed / completed) * 1000) / 10 : 0,
      };
    })();
    return json({ ok: true, strict: q.get("strict") === "1", ...base });
  }

  if (path === "/api/failures" && method === "GET") {
    return json(
      await all(
        db,
        `SELECT c.*, m.company_name, m.shop_name, s.level2_category FROM calls c
           LEFT JOIN merchants m ON m.id = c.merchant_id LEFT JOIN scenarios s ON s.id = c.scenario_id
          WHERE c.status IN ('completed','failed') AND c.closed_loop = 0 ORDER BY c.id DESC LIMIT 50`
      )
    );
  }

  if (path === "/api/tools" && method === "GET") {
    return json({
      enabled: true,
      timeout_ms: 4000,
      model: env.ZHIPU_MODEL || "glm-4-flashx",
      tools: [
        { name: "query_order_status", label: "查工单状态", description: prompts.TOOL_DESC.query_order_status, schema: {} },
        { name: "query_merchant_profile", label: "查商家画像", description: prompts.TOOL_DESC.query_merchant_profile, schema: {} },
        { name: "search_knowledge", label: "查知识库", description: prompts.TOOL_DESC.search_knowledge, schema: {} },
      ],
      memory: true,
      persisted: true,
    });
  }

  if (path === "/api/tools/events" && method === "GET") {
    const limit = Number(q.get("limit") || 30);
    const rows = await all(db, `SELECT * FROM work_order_events WHERE kind = 'tool' ORDER BY id DESC LIMIT ?`, limit);
    return json(rows.map((r) => ({ ...r, payload: parseJson(r.payload, {}) })));
  }

  if (path === "/api/webrtc/stats" && method === "GET") {
    return json({
      ok: true,
      samples: 0,
      weaknet_loss_pct: 0,
      rtt_ms: { p50: 0, p95: 0, max: 0 },
      jitter_ms: 0,
      loss_pct: 0,
      recent: [],
    });
  }

  // ---------------- 学生作品提交（/sc 页面）
  if (path === "/api/submit" && method === "POST") {
    return submitWork(request, env);
  }
  if (path === "/api/submit/list" && method === "GET") {
    // 作品集页面用：只暴露已审核通过的条目，不含联系方式
    const rows = await all(
      db,
      `SELECT uid, team, school, competition, track, title, url, description, members, created_at
         FROM submissions WHERE status = 'approved' ORDER BY created_at DESC LIMIT 200`
    );
    return json(rows);
  }

  if (path === "/api/kb/stats" && method === "GET") {
    const rows = await all(db, `SELECT source, COUNT(*) AS n FROM knowledge_chunks GROUP BY source`);
    const by = {};
    let total = 0;
    for (const r of rows) {
      by[r.source || "unknown"] = r.n;
      total += r.n;
    }
    return json({ total, sources: rows.length, by_source: by });
  }

  if (path === "/api/debug/client-error" && method === "POST") {
    let body = {};
    try {
      body = await request.json();
    } catch (_e) {
      body = {};
    }
    console.error("client-error", JSON.stringify(body).slice(0, 800));
    return json({ ok: true });
  }

  // ---------------- RSI versions
  if (path === "/api/rsi/versions" && method === "GET") {
    return json(await all(db, `SELECT * FROM rsi_versions ORDER BY id DESC`));
  }
  const mRsi = path.match(/^\/api\/rsi\/(versions\/)?([A-Za-z0-9_]+)(?:\/([a-z]+))?$/);
  if (mRsi && method === "POST") {
    const id = Number(mRsi[2]);
    const sub = mRsi[3] || "";
    const row = await one(db, `SELECT * FROM rsi_versions WHERE id = ?`, id);
    if (!row) return err("版本不存在", 404);
    if (sub === "activate") {
      await db.prepare(`UPDATE rsi_versions SET status = 'inactive'`).run();
      await db.prepare(`UPDATE rsi_versions SET status = 'active' WHERE id = ?`).bind(id).run();
      return json({ ok: true });
    }
    if (sub === "rollback") {
      const parent = row.parent_version;
      if (!parent) return err("无父版本可回滚", 400);
      const p = await one(db, `SELECT * FROM rsi_versions WHERE version = ?`, parent);
      if (!p) return err("父版本不存在", 404);
      await db.prepare(`UPDATE rsi_versions SET status = 'rolled_back' WHERE id = ?`).bind(id).run();
      await db.prepare(`UPDATE rsi_versions SET status = 'active' WHERE id = ?`).bind(p.id).run();
      return json({ ok: true });
    }
    if (sub === "delete") {
      if (row.status === "active") return json({ ok: false, msg: "当前生效版本不能删除，请先激活其他版本或回滚后再删" });
      await db.prepare(`DELETE FROM rsi_versions WHERE id = ?`).bind(id).run();
      return json({ ok: true });
    }
    if (sub === "engineer") {
      const changes = parseJson(row.changes_json, []) || [];
      const selected = changes.filter((c) => c && c.selected);
      if (!selected.length) return json({ ok: false, msg: "该版本没有勾选任何诊断，无法生成补丁" });
      return json({ ok: true });
    }
    if (sub === "apply") return json({ ok: true });
    return json({ ok: true });
  }

  const mRsiChange = path.match(/^\/api\/rsi\/versions\/(\d+)\/change\/(\d+)\/delete$/);
  if (mRsiChange && method === "POST") {
    const id = Number(mRsiChange[1]);
    const idx = Number(mRsiChange[2]);
    const row = await one(db, `SELECT * FROM rsi_versions WHERE id = ?`, id);
    if (!row) return err("版本不存在", 404);
    const changes = parseJson(row.changes_json, []) || [];
    if (idx < 0 || idx >= changes.length) return err("变更序号越界", 400);
    changes.splice(idx, 1);
    await db.prepare(`UPDATE rsi_versions SET changes_json = ? WHERE id = ?`).bind(JSON.stringify(changes), id).run();
    return json({ ok: true });
  }

  if (path.startsWith("/api/rsi/") && method === "POST") return json({ ok: true });

  // ---------------- import & companies
  if (path.startsWith("/api/import") || path.startsWith("/api/companies")) {
    if (method === "GET") return json({ ok: true, items: [] });
    return json({ ok: false, error: "该接口在当前部署下为只读（数据已从交付包导入 D1）" });
  }

  return err("接口不存在: " + path, 404);
}

async function publish(env, event) {
  try {
    const stub = env.MONITOR_BUS.get(env.MONITOR_BUS.idFromName("global"));
    await stub.fetch("https://monitor/publish", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(event),
    });
  } catch (_e) {}
}
