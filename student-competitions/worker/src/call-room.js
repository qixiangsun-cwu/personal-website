import { DurableObject } from "cloudflare:workers";
import { CallAgent } from "./call-agent.js";
import { createRunner } from "./call-room-session.js";

const PLAYBACK_BUFFER_AUDIO = 2.5;
const PLAYBACK_BUFFER_TEXT = 0.3;
const CLOSING_SILENCE_SEC = 5.0;
const CLOSING_DRAIN_MAX_SEC = 30.0;
const PLAYBACK_TAIL_SEC = 1.0;
const AUTO_CONTINUE_GAP = 1.0;

const now = () => Date.now() / 1000;

// 每通电话一个 Durable Object：字幕快照、回复纪元、打断门、播放时钟都在这里，
// 保证 /ws/monitor 与 /ws/call 拿到的是同一份内存态（与原版 server.py 的 SESSIONS 等价）。
export class CallRoom extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.env = env;
    this.attached = null;
    this.agent = null;
    this.session = null;
    this.running = false;
    this.sockets = new Set();
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/echo")) return this._echo();
    if (url.pathname.endsWith("/attach")) return this._attach(request);
    if (url.pathname.endsWith("/state")) return this._state();
    return new Response("not found", { status: 404 });
  }

  async _echo() {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    this.echoMode = true;
    return new Response(null, { status: 101, webSocket: client });
  }

  async _state() {
    const sess = this.session || {};
    return Response.json({
      call_id: sess.call_id || null,
      attached: !!this.attached,
      transcript: sess.transcript || { call_id: null, lines: [], rev: 0, epoch: 1 },
      status: sess.status || null,
      turn_no: this.agent ? this.agent.turn_no : 0,
      state: this.agent ? this.agent.state : null,
    });
  }

  async _attach(request) {
    try {
      return await this._attachInner(request);
    } catch (err) {
      const msg = String((err && err.message) || err);
      const detail = String((err && err.stack) || "").slice(0, 1200);
      console.error("attach inner failed", err && err.stack ? err.stack : err);
      try {
        await this._log("attach-error", msg + " | " + detail);
      } catch (_e3) {}
      return new Response(JSON.stringify({ ok: false, stage: "do-attach", error: msg, detail }), {
        status: 500,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }
  }

  async _log(tag, text) {
    try {
      const key = "__diag_" + tag + "_" + new Date().toISOString();
      await this.env.DB.prepare("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)").bind(key, String(text).slice(0, 3000)).run();
    } catch (_e) {}
  }

  async _attachInner(request) {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    // 用 Hibernation API 接管：DO 休眠后 WebSocket 依然存活，消息走 webSocketMessage
    this.ctx.acceptWebSocket(server);
    this.sockets.add(server);
    this.attached = server;

    let payload = {};
    const raw = request.headers.get("x-call-session");
    if (raw) {
      try {
        payload = JSON.parse(decodeURIComponent(escape(atob(raw))));
      } catch (_e) {
        payload = {};
      }
    } else {
      try {
        payload = await request.json();
      } catch (_e) {
        payload = {};
      }
    }
    const sess = payload.session || {};
    sess.socket = server;
    sess.transcript = { call_id: sess.call_id, lines: [], rev: 0, epoch: 1 };
    sess.reply_epoch = 1;
    sess.tts_count = 0;
    sess.anti_doubletalk = true;
    sess.play_end_at = 0;
    sess.audio_pending = 0;
    sess.started = false;
    sess.ended = false;
    this.session = sess;
    this.ctx.storage.put("session", sess).catch(() => {});
    this._send(sess, { type: "system", text: "（已连接）" });
    this.ctx.waitUntil(this._log("session-keys", Object.keys(sess).join(",")));
    this.ctx.waitUntil(this._log("attach-ok", new Date().toISOString() + " call=" + String(sess.call_id)));
    return new Response(null, { status: 101, webSocket: client });
  }

  async _restore() {
    if (this.session) return this.session;
    try {
      const stored = await this.ctx.storage.get("session");
      if (stored) {
        stored.socket = null;
        this.session = stored;
      }
    } catch (_e) {}
    return this.session;
  }

  // ---- Hibernation API 回调：连接在 DO 休眠后仍由平台持有
  async webSocketMessage(ws, message) {
    if (this.echoMode) {
      try {
        const t0 = Date.now();
        let sample = "";
        if (typeof message === "string") {
          const m = JSON.parse(message);
          if (m && m.load) {
            // 模拟 text 分支的 CPU 负载：跑一遍规则层的正则匹配
            const { CallAgent } = await import("./call-agent.js");
            const a = new CallAgent({
              callId: "bench",
              merchant: { company_name: "测试公司", shop_name: "测试店铺", contact: "张三" },
              scenario: { level2_category: "商标状态不符合要求", script_text: "", action_items: "" },
              env: {},
              db: null,
              offline: true,
            });
            sample = String(!!a);
          }
        }
        ws.send("do-echo:" + sample + ":" + (Date.now() - t0) + "ms");
      } catch (e) {
        try { ws.send("do-echo:ERR:" + String(e && e.message)); } catch (_e) {}
      }
      return;
    }
    let sess = (await this._restore()) || this.session;
    if (!sess) {
      this.ctx.waitUntil(this._log("msg-no-session", String(typeof message)));
      return;
    }
    sess.socket = ws;
    try {
      await this._onMessage(sess, message);
    } catch (err) {
      await this._log("msg-error", String((err && err.message) || err) + " | " + String((err && err.stack) || "").slice(0, 1200)).catch(() => {});
      try {
        ws.send(JSON.stringify({ type: "system", text: "（服务端处理异常：" + String((err && err.message) || err) + "）" }));
      } catch (_e2) {}
    }
  }

  async webSocketClose(ws, code, reason) {
    await this._log("ws-close", "code=" + String(code) + " reason=" + String(reason)).catch(() => {});
    this.sockets.delete(ws);
    if (this.attached === ws) this.attached = null;
    if (this.session) await this._onClose(this.session).catch(() => {});
  }

  async webSocketError(ws, err) {
    await this._log("ws-error", String((err && err.message) || err)).catch(() => {});
    this.sockets.delete(ws);
    if (this.attached === ws) this.attached = null;
  }

  _send(sess, obj) {
    const ws = sess && sess.socket;
    if (!ws) return false;
    try {
      ws.send(JSON.stringify(obj));
      return true;
    } catch (_e) {
      return false;
    }
  }

  async _onMessage(sess, data) {
    if (typeof data === "string") {
      // 文本帧：JSON 动作
      let msg = {};
      try {
        msg = JSON.parse(data);
      } catch (_e) {
        return;
      }
      await this._log("action", String(msg && msg.action));
      try {
        if (!this.runner) this.runner = createRunner(this);
      } catch (e) {
        await this._log("runner-error", String((e && e.message) || e) + " | " + String((e && e.stack) || "").slice(0, 1000));
        throw e;
      }
      const t0 = Date.now();
      try {
        await this.runner.handleAction(sess, msg);
        await this._log("done", String(msg && msg.action) + " " + (Date.now() - t0) + "ms");
      } catch (e) {
        await this._log("action-error", String(msg && msg.action) + " | " + String((e && e.message) || e) + " | " + String((e && e.stack) || "").slice(0, 1200));
        throw e;
      }
      return;
    }
    // 二进制帧：16k/16bit/mono PCM -> ASR
    if (!sess.asr) return;
    try {
      let buf = data;
      if (buf instanceof ArrayBuffer) buf = new Uint8Array(buf);
      else if (buf && typeof buf.arrayBuffer === "function") buf = new Uint8Array(await buf.arrayBuffer());
      // 转成独立的 ArrayBuffer 副本，避免分片丢失
      const copy = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
      await sess.asr.feedAudio(new Uint8Array(copy));
    } catch (err) {
      console.error("feedAudio failed", err && err.message);
    }
  }

  async _onClose(sess) {
    if (sess.closed) return;
    sess.closed = true;
    if (sess.asr) {
      try {
        await sess.asr.close();
      } catch (_e) {}
    }
  }

  async _onAction(sess, msg) {
    // 运行器在构造时就建好，避免每轮动态 import（DO 里动态导入失败会静默断连）
    if (!this.runner) this.runner = createRunner(this);
    await this.runner.handleAction(sess, msg);
  }

  // 供 runner 使用的最小工具集
  send(sess, obj) {
    return this._send(sess, obj);
  }

  async broadcastMonitor(event) {
    try {
      const stub = this.env.MONITOR_BUS.get(this.env.MONITOR_BUS.idFromName("global"));
      await stub.fetch("https://monitor/publish", {
        method: "POST",
        body: JSON.stringify(event),
        headers: { "content-type": "application/json" },
      });
    } catch (err) {
      console.error("broadcastMonitor failed", err && err.message);
    }
  }

  now() {
    return now();
  }
}

export const CALL_ROOM_CONST = {
  PLAYBACK_BUFFER_AUDIO,
  PLAYBACK_BUFFER_TEXT,
  CLOSING_SILENCE_SEC,
  CLOSING_DRAIN_MAX_SEC,
  PLAYBACK_TAIL_SEC,
  AUTO_CONTINUE_GAP,
};
