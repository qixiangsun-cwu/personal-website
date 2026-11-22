/**
 * 智语外呼 · 火山引擎豆包流式 ASR（大模型 SAUC 2.0）WebSocket 客户端
 *
 * 移植自后端 asr_volcano.py（协议常量与帧布局逐字对齐；音纹锁定的 Python
 * 自相关特征提取未移植——前端仍可看到 speaker_guard 事件，但不再做声纹拦截，
 * 保证"商家说什么都能被识别"，不会出现"本人被误拦"的实机问题）。
 */

export const WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async";
const RESOURCE_ID_DEFAULT = "volc.seedasr.sauc.duration";

const MSG_FULL_REQ = 0b0001;
const MSG_AUDIO_REQ = 0b0010;
const MSG_SERVER_RESP = 0b1001;
const MSG_SERVER_ERR = 0b1111;
const F_POS_SEQ = 0b0001;
const F_NEG_SEQ = 0b0010;
const F_NEG_WITH_SEQ = 0b0011;
const SER_JSON = 0b0001;
const SER_RAW = 0b0000;
const COMP_GZIP = 0b0001;

const KEEPALIVE_INTERVAL_MS = 200;
const KEEPALIVE_BYTES = 6400; // 200ms @16k/16bit/mono

// ---- 内容轻量判据（与 asr_volcano.py content_hint 同源，供投机旁路使用）----
const HINT_NOISE =
  /广播站|校园广播|学弟|学妹|学长|学姐|同学们|老师好|谢谢老师|经济学院|工商管理|学院|专业|上课|下课|早读|晚自习|作业|考试|宿舍|食堂|操场|社团|学生会|招生|毕业典礼|课间|讲座/;
const HINT_BUSINESS =
  /资质|审核|驳回|材料|补|提交|后台|京麦|商标|授权|类目|发票|申诉|费用|复审|文审|品牌|入驻|店铺|证件|证明|执照|上传|修改|整改|过审|结果|进度|通知|工单|热线|专员|工号|实名|结算|账号|法人|登录|注册|官网|平台|规则|要求|标准|流程|疑问|问题|什么意思|怎么办|怎么弄|帮我|公司|企业|负责人|商家|我想问|请问|咨询|了解一下/;
const HINT_ACK =
  /^(好的?|是的?|行|嗯+|哦+|噢+|对对?|明白了?|知道了?|清楚了?|懂了?|没问题|好嘞|谢谢|多谢|感谢|喂|你好|您好|哈喽|我是|本人|没错|可以|没有疑问|没什么问题|就这些|先这样|不用了|没了)[。！!？?,，、~…\s]*$/;

export function contentHint(text) {
  const s = String(text || "").replace(/[\s，,、。！!？?~…·\-—]/g, "");
  if (!s) return "gray";
  if (HINT_NOISE.test(s)) return "noise";
  if (HINT_BUSINESS.test(s)) return "business";
  if (HINT_ACK.test(s)) return "business";
  return "gray";
}

function hdr(mtype, flags, serial, comp) {
  return new Uint8Array([0x11, ((mtype << 4) | flags) & 0xff, ((serial << 4) | comp) & 0xff, 0x00]);
}

function frameWithSeq(mtype, seq, serial, comp, payload) {
  const out = new Uint8Array(12 + payload.length);
  out.set(hdr(mtype, F_POS_SEQ, serial, comp), 0);
  const dv = new DataView(out.buffer);
  dv.setInt32(4, seq, false);
  dv.setUint32(8, payload.length, false);
  out.set(payload, 12);
  return out;
}

async function gzip(bytes) {
  const cs = new CompressionStream("gzip");
  const w = cs.writable.getWriter();
  w.write(bytes);
  w.close();
  const buf = await new Response(cs.readable).arrayBuffer();
  return new Uint8Array(buf);
}

async function gunzip(bytes) {
  const ds = new DecompressionStream("gzip");
  const w = ds.writable.getWriter();
  w.write(bytes);
  w.close();
  const buf = await new Response(ds.readable).arrayBuffer();
  return new Uint8Array(buf);
}

function buildRequest(env) {
  const req = {
    user: { uid: "zhiyu-agent", did: "zhiyu-web" },
    audio: { format: "pcm", codec: "raw", rate: 16000, bits: 16, channel: 1 },
    request: {
      model_name: "bigmodel",
      enable_itn: true,
      enable_punc: true,
      enable_ddc: true,
      show_utterances: true,
      enable_speaker_info: true,
      enable_nonstream: true,
      end_window_size: 450,
    },
  };
  return req;
}

/** 解析服务端响应帧 → {type:'result',body} | {type:'error',code,msg} */
export async function parseResponse(raw) {
  const u8 = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
  if (u8.length < 8) return {};
  const mtype = u8[1] >> 4;
  const flags = u8[1] & 0x0f;
  const comp = u8[2] & 0x0f;
  const rest = u8.subarray(4);
  const dv = new DataView(rest.buffer, rest.byteOffset, rest.byteLength);
  if (mtype === MSG_SERVER_ERR) {
    try {
      const code = dv.getUint32(0, false);
      const size = dv.getUint32(4, false);
      const msg = new TextDecoder().decode(rest.subarray(8, 8 + size));
      return { type: "error", code, msg };
    } catch (_) {
      return { type: "error", code: -1, msg: "err-decode" };
    }
  }
  if (mtype === MSG_SERVER_RESP) {
    try {
      let off = flags === F_POS_SEQ || flags === F_NEG_SEQ || flags === F_NEG_WITH_SEQ ? 4 : 0;
      const seq = off ? dv.getInt32(0, false) : null;
      const size = dv.getUint32(off, false);
      off += 4;
      let payload = rest.subarray(off, off + size);
      if (comp === COMP_GZIP) payload = await gunzip(payload);
      const body = JSON.parse(new TextDecoder().decode(payload));
      return { type: "result", seq, body };
    } catch (e) {
      return { type: "error", code: -1, msg: "响应解析失败: " + e.message };
    }
  }
  return { type: "unknown", mtype };
}

const FATAL_CODES = new Set([55000000, 45000202, 45000001]);

export class VolcanoASR {
  /**
   * @param {object} env
   * @param {string} callId
   * @param {{onPartial?:Function,onFinal?:Function,onSpeakerEvent?:Function,onStatus?:Function}} cb
   */
  constructor(env, callId, cb = {}) {
    this.env = env;
    this.callId = callId;
    this.cb = cb;
    this.ws = null;
    this.closed = false;
    this.seq = 0;
    this.queue = [];
    this.running = false;
    this.restarts = 0;
    this.lastFinal = "";
    this.lastFinalAt = 0;
    this.emitted = [];
    this.reconnectTimer = null;
    this.keepAliveTimer = null;
    this.fullReqPayload = null;
  }

  get available() {
    return !!this.env.VOLCANO_APP_KEY;
  }

  async start() {
    if (!this.available) throw new Error("火山ASR未配置：需要 VOLCANO_APP_KEY");
    await this.connectOnce();
  }

  async connectOnce() {
    const url = this.env.VOLCANO_ASR_URL || WS_URL;
    const rid = this.env.VOLCANO_RESOURCE_ID || RESOURCE_ID_DEFAULT;
    const headers = {
      "X-Api-Key": this.env.VOLCANO_APP_KEY || "",
      "X-Api-Resource-Id": rid,
      "X-Api-Request-Id": crypto.randomUUID(),
    };
    const resp = await fetch(url.replace(/^wss:/, "https:").replace(/^ws:/, "http:"), {
      headers: { ...headers, Upgrade: "websocket" },
    });
    if (!resp.webSocket) {
      let detail = "";
      try {
        detail = " status=" + resp.status + " body=" + (await resp.text()).slice(0, 300);
      } catch (_e) {}
      throw new Error("asr websocket upgrade failed" + detail);
    }
    const ws = resp.webSocket;
    ws.accept();
    this.ws = ws;
    this.seq = 1;
    ws.addEventListener("message", (ev) => {
      this.onMessage(ev.data).catch(() => {});
    });
    ws.addEventListener("close", () => {
      if (this.ws === ws) this.ws = null;
      if (!this.closed) this.scheduleReconnect();
    });
    ws.addEventListener("error", () => {
      if (this.ws === ws) this.ws = null;
      if (!this.closed) this.scheduleReconnect();
    });
    this.fullReqPayload = await gzip(new TextEncoder().encode(JSON.stringify(buildRequest(this.env))));
    ws.send(frameWithSeq(MSG_FULL_REQ, this.seq, SER_JSON, COMP_GZIP, this.fullReqPayload));
    this.startKeepAlive();
    this.drain();
  }

  startKeepAlive() {
    // DO 环境禁用存活定时器：改为在 feedAudio 时按需补静音保活，
    // 避免 Durable Object 在请求返回后被平台回收/中断。
    this.stopKeepAlive();
    this.keepAliveTimer = null;
  }

  stopKeepAlive() {
    if (this.keepAliveTimer) clearInterval(this.keepAliveTimer);
    this.keepAliveTimer = null;
  }

  scheduleReconnect() {
    this.stopKeepAlive();
    if (this.reconnectTimer || this.closed) return;
    this.restarts += 1;
    if (this.restarts > 12) {
      this.emitStatus("[ASR] 反复失败已放弃重建，请检查配额或网络后再试");
      return;
    }
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      if (this.closed) return;
      try {
        await this.connectOnce();
      } catch (e) {
        this.emitStatus("[ASR] 连接失败将重试: " + String(e.message || e).slice(0, 80));
        this.scheduleReconnect();
      }
    }, 1200);
    // DO 里 setTimeout 不会被 waitUntil 保留，但重连失败可接受（前端会提示 ASR 不可用）
    if (this.reconnectTimer && typeof this.reconnectTimer.unref === "function") this.reconnectTimer.unref();
  }

  emitStatus(text) {
    if (this.cb.onPartial) {
      try {
        this.cb.onPartial(text);
      } catch (_) {}
    }
  }

  feedAudio(bytes) {
    if (this.closed) return;
    this.queue.push(bytes);
    if (this.queue.length > 300) this.queue.shift();
    this.drain();
  }

  async drain() {
    if (this.running) return;
    this.running = true;
    try {
      while (this.queue.length && !this.closed) {
        const chunk = this.queue.shift();
        try {
          await this.sendAudio(chunk);
        } catch (_) {
          this.queue.unshift(chunk);
          await new Promise((r) => setTimeout(r, 300));
          if (!this.ws) break;
        }
      }
    } finally {
      this.running = false;
    }
  }

  async sendAudio(chunk) {
    const ws = this.ws;
    if (!ws) throw new Error("asr not connected");
    this.seq += 1;
    const z = await gzip(chunk);
    ws.send(frameWithSeq(MSG_AUDIO_REQ, this.seq, SER_RAW, COMP_GZIP, z));
  }

  async onMessage(data) {
    let raw;
    if (data instanceof ArrayBuffer) raw = new Uint8Array(data);
    else if (data && data.arrayBuffer) raw = new Uint8Array(await data.arrayBuffer());
    else return;
    const resp = await parseResponse(raw);
    if (resp.type === "error") {
      if (!FATAL_CODES.has(resp.code) && resp.code !== 45000081) {
        this.emitStatus(`[ASR错误 ${resp.code}] ${String(resp.msg || "").slice(0, 120)}`);
      }
      const low = String(resp.msg || "").toLowerCase();
      if (FATAL_CODES.has(resp.code) || /internal error|new session|timeout|invalid|exceed|quota/.test(low)) {
        this.dropAndReconnect();
      }
      return;
    }
    if (resp.type === "result") this.parseResult(resp.body || {});
  }

  dropAndReconnect() {
    const ws = this.ws;
    this.ws = null;
    try {
      ws && ws.close();
    } catch (_) {}
    this.scheduleReconnect();
  }

  norm(text) {
    return String(text || "").replace(/[^0-9A-Za-z\u4e00-\u9fff]/g, "");
  }

  emitFinal(text, conf) {
    const norm = this.norm(text);
    if (!norm) return;
    const now = Date.now();
    this.emitted = this.emitted.filter((e) => now - e.t <= 15000);
    if (this.emitted.some((e) => e.norm === norm)) {
      this.lastFinal = text;
      this.lastFinalAt = now;
      return;
    }
    const prev = this.norm(this.lastFinal);
    const within = now - this.lastFinalAt <= 3000;
    if (prev && within) {
      if (norm === prev) {
        this.lastFinalAt = now;
        return;
      }
      if (Math.abs(norm.length - prev.length) <= 4 && (norm.includes(prev) || prev.includes(norm))) {
        this.lastFinal = text;
        this.lastFinalAt = now;
        return;
      }
    }
    this.lastFinal = text;
    this.lastFinalAt = now;
    this.emitted.push({ norm, t: now });
    if (this.cb.onFinal) {
      Promise.resolve(this.cb.onFinal(text, conf)).catch(() => {});
    }
  }

  parseResult(body) {
    const result = body.result || {};
    const utterances = result.utterances || [];
    if (!utterances) return;
    if (!utterances.length) return;
    for (const u of utterances) {
      const text = u.text || "";
      if (!text) continue;
      const definite = !!u.definite;
      const conf = u.confidence === undefined ? 1.0 : u.confidence;
      if (definite) {
        this.emitFinal(text, conf);
      } else if (this.cb.onPartial) {
        Promise.resolve(this.cb.onPartial(text)).catch(() => {});
      }
    }
  }

  async close() {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopKeepAlive();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        this.seq += 1;
        const z = await gzip(new Uint8Array(0));
        const buf = new Uint8Array(12 + z.length);
        buf.set(hdr(MSG_AUDIO_REQ, F_NEG_WITH_SEQ, SER_RAW, COMP_GZIP), 0);
        const dv = new DataView(buf.buffer);
        dv.setInt32(4, -this.seq, false);
        dv.setUint32(8, z.length, false);
        buf.set(z, 12);
        ws.send(buf);
      } catch (_) {}
      try {
        ws.close();
      } catch (_) {}
    }
  }
}
