import { CallAgent } from "./call-agent.js";
import { VolcanoASR } from "./asr.js";
import * as tts from "./tts.js";
import * as RP from "./rules.js";

const PLAYBACK_BUFFER_AUDIO = 2.5;
const PLAYBACK_BUFFER_TEXT = 0.3;
const CLOSING_SILENCE_SEC = 5.0;
const CLOSING_DRAIN_MAX_SEC = 30.0;
const PLAYBACK_TAIL_SEC = 1.0;
const AUTO_CONTINUE_GAP = 1.0;

const TERMINAL = new Set(["completed", "failed", "rejected", "pending_followup", "superseded"]);

// 把 8 片 128 字节的 DOM 数据合并成 Uint8Array
function toBytes(buf) {
  if (buf instanceof ArrayBuffer) return new Uint8Array(buf);
  if (ArrayBuffer.isView(buf)) return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
  return new Uint8Array(0);
}

class Queue {
  constructor() {
    this.items = [];
    this.waiters = [];
  }
  push(item) {
    const w = this.waiters.shift();
    if (w) w(item);
    else this.items.push(item);
  }
  async shift() {
    if (this.items.length) return this.items.shift();
    return await new Promise((res) => this.waiters.push(res));
  }
  clear() {
    this.items.length = 0;
  }
  get size() {
    return this.items.length;
  }
}

export function createRunner(room) {
  const env = room.env;
  const db = env.DB;
  const state = {
    room,
    sessions: new Map(),
    handleAction,
  };

  async function loadCall(callId) {
    return await db
      .prepare(
        `SELECT c.*, m.company_name, m.shop_name, m.contact_name, m.status AS merchant_status,
                s.level1_category, s.level2_category, s.close_method, s.script_text
           FROM calls c LEFT JOIN merchants m ON m.id = c.merchant_id
                LEFT JOIN scenarios s ON s.id = c.scenario_id
          WHERE c.call_id = ?`
      )
      .bind(callId)
      .first();
  }

  function broadcast(callId, event) {
    room.broadcastMonitor({ call_id: callId, ...event }).catch(() => {});
  }

  function broadcastTranscript(sess) {
    const snap = sess.transcript;
    const obj = { type: "transcript", ...snap };
    room.send(sess, obj);
    broadcast(sess.call_id, obj);
  }

  function transcriptInit(callId) {
    return { call_id: callId, lines: [], rev: 0, epoch: 1 };
  }

  function transcriptAdd(sess, role, key, text, tags = []) {
    const tr = sess.transcript;
    let line = tr.lines.find((l) => l.key === key);
    if (!line) {
      line = { key, role, text, tags, ts: "" };
      tr.lines.push(line);
    } else {
      line.text = text;
      line.role = role;
      line.tags = tags;
    }
    tr.rev += 1;
    broadcastTranscript(sess);
  }

  async function ensureAgent(sess, call) {
    if (sess.agent) return sess.agent;
    const merchant = {
      id: call.merchant_id,
      merchant_id: call.merchant_id,
      company_name: call.company_name,
      shop_name: call.shop_name,
      contact_name: call.contact_name,
      direction: call.direction || "outbound",
    };
    const scenario = call.level2_category || call.script_text ? {
      level1_category: call.level1_category,
      level2_category: call.level2_category,
      close_method: call.close_method,
      script_text: call.script_text,
    } : null;
    const agent = new CallAgent({
      callId: call.call_id,
      merchant,
      scenario,
      env,
      db,
      offline: false,
    });
    await agent.init();
    sess.agent = agent;
    return agent;
  }

  async function handleAction(sess, msg) {
    const action = String(msg.action || "");
    if (action === "ping") return room.send(sess, { type: "pong", t: Date.now() });
    if (action === "start") return startCall(sess, msg);
    if (action === "anti_doubletalk") return room.send(sess, { type: "anti_doubletalk_ack", enabled: !!msg.enabled });
    if (action === "relock_speaker") {
      room.send(sess, { type: "speaker_guard_ack", relocked: true });
      return room.send(sess, { type: "system", text: "（音纹已重锁：请本人说一句，重新建立目标）" });
    }
    if (action === "diag_dump") return room.send(sess, { type: "diag_data", snapshot: diagSnapshot(sess) });
    if (action === "latency_report") {
      const e2e = Number(msg.e2e_ms || 0);
      const stage = msg.stage || {};
      return room.send(sess, { type: "latency", e2e_ms: e2e, stage });
    }
    if (action === "rtc_offer" || action === "rtc_candidate") {
      return room.send(sess, {
        type: "rtc_state",
        state: "unavailable",
        msg: "后端 WebRTC 未启用（缺 aiortc 或 ZHIYU_WEBRTC=0），保持 WS PCM 链路",
      });
    }
    if (action === "rtc_weaknet") {
      return room.send(sess, { type: "rtc_weaknet_state", pct: 0, msg: "后端 WebRTC 未启用，无法模拟弱网" });
    }
    if (action === "rtc_stats") {
      return broadcast(sess.call_id, { type: "rtc_stats", stats: msg.stats || {} });
    }
    if (action === "rtc_close") return room.send(sess, { type: "rtc_state", state: "closed" });
    if (action === "silence_reset") return room.send(sess, { type: "silence_reset_ack" });
    if (action === "hangup") return hangup(sess);
    if (action === "text") {
      const text = String(msg.text || "").trim();
      if (!text) return;
      // final 事件由 onFinal 统一发送（含 turn 号），这里不再重复发
      if (sess.offline_echo) {
        room.send(sess, { type: "final", text, confidence: 1.0, turn: (sess.agent ? sess.agent.turn_no : 0) + 1 });
        room.send(sess, { type: "agent_reply", text: "（回显）" + text, tts: false, epoch: sess.reply_epoch });
        return null;
      }
      if (sess.skip_asr_only) {
        room.send(sess, { type: "final", text, confidence: 1.0, turn: (sess.agent ? sess.agent.turn_no : 0) + 1 });
        return onFinalNoAsr(sess, text, 1.0);
      }
      return onFinal(sess, text, 1.0);
    }
    return null;
  }

  function diagSnapshot(sess) {
    const a = sess.agent;
    return {
      call_id: sess.call_id,
      state: a ? a.state : null,
      turn_no: a ? a.turn_no : 0,
      merchant_turns: a ? a.merchant_turns : 0,
      echo_epoch: sess.reply_epoch,
      tts_count: sess.tts_count || 0,
      anti_doubletalk: sess.anti_doubletalk !== false,
    };
  }

  async function startCall(sess, msg) {
    if (sess.started) return;
    sess.started = true;
    const call = sess.call;
    const agent = await ensureAgent(sess, call);

    room.send(sess, { type: "ready", asr: "connecting" });
    if (!sess.skip_asr_only && !sess.skip_asr_hard) {
    try {
      sess.asr = new VolcanoASR(env, call.call_id, {
        onPartial: (text) => onPartial(sess, text),
        onFinal: (text, conf) => {
          onFinal(sess, text, conf).catch((e) => console.error("onFinal", e && e.stack ? e.stack : e));
        },
        onSpeakerEvent: (ev) => room.send(sess, { type: "speaker_guard", ...ev }),
        onStatus: (st) => room.send(sess, { type: "asr_ready", ...st }),
      });
      await sess.asr.start();
      room.send(sess, { type: "asr_ready", asr: "volcano" });
    } catch (err) {
      console.error("asr init failed", err && err.message);
      room.send(sess, { type: "asr_ready", asr: "unavailable", err: String(err && err.message) });
    }
    }

    // 有历史轮次则恢复，否则播开场白
    const hist = await db.prepare(`SELECT COUNT(*) AS n FROM call_turns WHERE call_id = ?`).bind(call.call_id).first();
    if (hist && hist.n > 0) {
      const rows = await db
        .prepare(`SELECT role, text FROM call_turns WHERE call_id = ? ORDER BY turn_no ASC, id ASC LIMIT 40`)
        .bind(call.call_id)
        .all();
      for (const r of rows.results || []) {
        agent.history.push({ role: r.role === "merchant" ? "user" : "assistant", content: r.text });
      }
      room.send(sess, { type: "resumed" });
      return;
    }

    if (msg && msg.text) {
      // 手动首句
      return onFinal(sess, String(msg.text), 1.0);
    }
    await speakFixed(sess, await agent.openingReply());
  }

  // 播固定话术：直接合成并下发
  async function speakFixed(sess, text) {
    if (!text) return;
    room.send(sess, {
      type: "agent_reply",
      text,
      tts: true,
      epoch: sess.reply_epoch,
      state: sess.agent ? sess.agent.state : "",
      done_elements: [],
    });
    await speak(sess, text);
    broadcast(sess.call_id, { type: "turn", role: "agent", text, state: sess.agent ? sess.agent.state : "" });
  }

  async function speak(sess, text) {
    const epoch = sess.reply_epoch;
    if (epoch !== sess.reply_epoch) return;
    let first = true;
    const chunks = [];
    try {
      await tts.synthesizeStream(env, text, (pcmChunk) => {
        if (epoch !== sess.reply_epoch) return;
        chunks.push(pcmChunk);
      });
    } catch (err) {
      console.error("tts failed", err && err.message);
    }
    if (epoch !== sess.reply_epoch) return;
    if (!chunks.length) return;
    const pcm = concat(chunks);
    const wav = tts.pcmToWav(pcm, 24000);
    if (first) {
      first = false;
      room.send(sess, { type: "tts_first_audio", turn: sess.agent ? sess.agent.turn_no : 0, final_to_audio_ms: 0 });
    }
    const ws = sess.socket;
    if (ws) {
      try {
        ws.send(wav);
        sess.play_end_at = Math.max(sess.play_end_at || 0, room.now() + tts.wavSeconds(wav));
      } catch (_e) {}
    }
  }

  function concat(list) {
    let n = 0;
    for (const c of list) n += c.length;
    const out = new Uint8Array(n);
    let off = 0;
    for (const c of list) {
      out.set(c, off);
      off += c.length;
    }
    return out;
  }

  function onPartial(sess, text) {
    const t = String(text || "").trim();
    if (!t || t.startsWith("[ASR")) return;
    room.send(sess, { type: "partial", text: t });
    broadcast(sess.call_id, { type: "partial", text: t });
  }

  // 差分诊断：只跑 LLM，不做 TTS 外呼、不碰 ASR
  async function onFinalNoAsr(sess, text, conf) {
    const agent = sess.agent;
    if (!agent || sess.ended) return;
    const turn = agent.turn_no + 1;
    sess.reply_epoch += 1;
    let cm;
    try {
      cm = await agent.chat_stream(text, false, conf, () => {}, false);
    } catch (err) {
      room.send(sess, { type: "system", text: "（LLM 失败：" + String(err && err.message) + "）" });
      return;
    }
    room.send(sess, {
      type: "agent_reply",
      text: cm.ai_reply || "",
      tts: false,
      epoch: sess.reply_epoch,
      state: cm.state,
    });
  }

  async function onFinal(sess, text, conf) {
    const agent = sess.agent;
    if (!agent || sess.ended) return;
    const turn = agent.turn_no + 1;
    room.send(sess, { type: "final", text, confidence: conf, turn });
    // 打断：新一轮开始，作废上一次回复的音频
    sess.reply_epoch += 1;
    const wasPlaying = (sess.play_end_at || 0) > room.now();
    if (wasPlaying) room.send(sess, { type: "interrupt_tts" });

    transcriptAdd(sess, "merchant", "u" + turn, text);

    let emitted = [];
    const collected = [];
    const onSentence = (s) => {
      if (!s) return;
      collected.push(s);
    };
    let cm;
    try {
      cm = await agent.chat_stream(text, wasPlaying, conf, onSentence, false);
    } catch (err) {
      console.error("chat_stream failed", err && err.stack ? err.stack : err);
      room.send(sess, { type: "system", text: "（系统繁忙，请稍后再试）" });
      return;
    }
    const reply = cm.ai_reply || "";
    if (reply) {
      room.send(sess, {
        type: "agent_reply",
        text: reply,
        tts: true,
        epoch: sess.reply_epoch,
        state: cm.state,
        done_elements: Object.keys((cm.compliance && cm.compliance.elements) || {}).filter(
          (k) => cm.compliance.elements[k]
        ),
      });
      room.send(sess, {
        type: "agent_reply_meta",
        state: cm.state,
        finished: !!cm.finished,
        emotion: cm.emotion,
        latency_ms: 0,
        compliance: cm.compliance,
        flow5: cm.flow5,
      });
      transcriptAdd(sess, "agent", "a" + turn, reply);
      await speak(sess, reply);
    }
    broadcast(sess.call_id, {
      type: "turn",
      role: "merchant",
      text,
      state: cm.state,
      intent: cm.intent,
      compliance: cm.compliance,
      flow5: cm.flow5,
    });
    await persistTurn(sess, agent, turn, text, conf, cm);

    if (cm.finished) {
      await finalize(sess, agent, "completed");
      room.send(sess, { type: "call_finished" });
    }
    emitted.length = 0;
  }

  async function persistTurn(sess, agent, turn, userText, conf, cm) {
    try {
      await db
        .prepare(
          `INSERT OR IGNORE INTO call_turns (call_id, turn_no, role, text, asr_confidence, intent, state, emotion, compliance, interrupted, latency_ms, e2e_ms, created_at)
           VALUES (?, ?, 'merchant', ?, ?, ?, ?, ?, NULL, 0, NULL, NULL, datetime('now'))`
        )
        .bind(sess.call_id, turn, userText, conf, cm.intent, cm.state, cm.emotion)
        .run();
      await db
        .prepare(
          `INSERT OR IGNORE INTO call_turns (call_id, turn_no, role, text, asr_confidence, intent, state, emotion, compliance, interrupted, latency_ms, e2e_ms, created_at)
           VALUES (?, ?, 'agent', ?, NULL, ?, ?, ?, ?, 0, NULL, NULL, datetime('now'))`
        )
        .bind(
          sess.call_id,
          turn,
          cm.ai_reply || "",
          cm.intent,
          cm.state,
          cm.emotion,
          cm.compliance ? cm.compliance.rate : null
        )
        .run();
    } catch (err) {
      console.error("persistTurn failed", err && err.message);
    }
  }

  async function hangup(sess) {
    const agent = sess.agent;
    if (!agent) return;
    sess.ended = true;
    await finalize(sess, agent, "pending_followup");
    room.send(sess, { type: "call_finished" });
  }

  async function finalize(sess, agent, status) {
    const summary = agent.summary();
    const closed = !!summary.closed_loop;
    const st = status === "completed" ? (closed ? "completed" : "pending_followup") : status;
    try {
      await db
        .prepare(
          `UPDATE calls SET status = ?, closed_loop = ?, ended_at = datetime('now'), stop_stage = ?, result_json = ? WHERE call_id = ?`
        )
        .bind(st, closed ? 1 : 0, summary.stop_stage || "", JSON.stringify(summary), sess.call_id)
        .run();
      const ticketStatus = closed ? "resolved" : "pending_resubmit";
      const exists = await db.prepare(`SELECT id FROM tickets WHERE call_id = ?`).bind(sess.call_id).first();
      if (exists) {
        await db
          .prepare(`UPDATE tickets SET status = ?, compliance_rate = ?, emotion_summary = ? WHERE call_id = ?`)
          .bind(ticketStatus, summary.compliance ? summary.compliance.rate : 0, summary.emotion || "neutral", sess.call_id)
          .run();
      } else {
        await db
          .prepare(
            `INSERT INTO tickets (call_id, merchant_id, resolved_json, pending_json, emotion_summary, compliance_rate, status, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
          )
          .bind(
            sess.call_id,
            sess.call.merchant_id,
            JSON.stringify(summary.resolved || []),
            JSON.stringify(summary.pending || []),
            summary.emotion || "neutral",
            summary.compliance ? summary.compliance.rate : 0,
            ticketStatus
          )
          .run();
      }
    } catch (err) {
      console.error("finalize failed", err && err.message);
    }
    room.send(sess, { type: "call_finalized", closed_loop: closed, summary });
    broadcast(sess.call_id, { type: "call_ended", closed_loop: closed, stop_stage: summary.stop_stage });
    if (sess.asr) {
      try {
        await sess.asr.close();
      } catch (_e) {}
    }
  }

  state.handleAction = handleAction;
  state.startCall = startCall;
  state.onFinal = onFinal;
  state.generate = null;
  state.sessions = state.sessions;
  return state;
}
