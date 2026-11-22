/**
 * 智语外呼 · 火山引擎豆包流式 TTS（unidirectional SSE）
 *
 * 移植自后端 tts_volcano.py：同端点、同请求头、同 SSE 事件语义
 * （352 音频分片 / 351 句结束 / 152 会话结束 / 153 失败），PCM 24k16bit 单声道
 * 加 44 字节 WAV 头后下发，每片都能被前端 decodeAudioData 独立解码。
 */

const SAMPLE_RATE = 24000;
const OK_CODES = new Set([0, 20000000]);
const ADDITIONS = '{"aigc_watermark":false,"cache_config":{"text_type":1,"use_cache":true}}';
const FIRST_FLUSH_BYTES = 1920; // 首帧 ~40ms 尽快出声
const FLUSH_BYTES = 9600; // 之后 ~200ms 一片

export function pcmToWav(pcm, rate = SAMPLE_RATE) {
  const n = (pcm && pcm.length) || 0;
  const buf = new Uint8Array(44 + n);
  const dv = new DataView(buf.buffer);
  const w = (o, s) => {
    for (let i = 0; i < s.length; i++) buf[o + i] = s.charCodeAt(i);
  };
  w(0, "RIFF");
  dv.setUint32(4, 36 + n, true);
  w(8, "WAVE");
  w(12, "fmt ");
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, rate, true);
  dv.setUint32(28, rate * 2, true);
  dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  w(36, "data");
  dv.setUint32(40, n, true);
  if (n) buf.set(pcm, 44);
  return buf;
}

export function wavSeconds(wav, rate = SAMPLE_RATE) {
  const n = Math.max(0, (wav.length || 0) - 44);
  return n / (rate * 2);
}

function b64ToBytes(b64) {
  const clean = String(b64).replace(/\s+/g, "");
  const bin = atob(clean);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function concat(a, b) {
  if (!a.length) return b;
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/**
 * 流式合成。onChunk(Uint8Array wav) 每积攒一片回调一次。
 * @returns {Promise<{ok:boolean, chunks:number, error?:string}>}
 */
export async function synthesizeStream(env, text, onChunk) {
  const url = env.VOLCANO_TTS_URL || "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse";
  const rid = env.VOLCANO_TTS_RESOURCE_ID || "seed-tts-2.0";
  const speaker = env.VOLCANO_TTS_SPEAKER || "zh_female_xiaohe_uranus_bigtts";
  const body = {
    user: { uid: "zhiyu-callcenter" },
    req_params: {
      text,
      speaker,
      audio_params: { format: "pcm", sample_rate: SAMPLE_RATE },
      additions: ADDITIONS,
    },
  };
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "X-Api-Key": env.VOLCANO_TTS_API_KEY || "",
        "X-Api-Resource-Id": rid,
        "X-Api-Request-Id": crypto.randomUUID().replace(/-/g, ""),
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return { ok: false, chunks: 0, error: "tts fetch: " + (e.message || e) };
  }
  if (!res.ok || !res.body) {
    const t = await res.text().catch(() => "");
    return { ok: false, chunks: 0, error: `tts ${res.status}: ${String(t).slice(0, 120)}` };
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let acc = new Uint8Array(0);
  let sent = 0;
  let err = null;
  let done = false;
  const flush = (force) => {
    const need = sent ? FLUSH_BYTES : FIRST_FLUSH_BYTES;
    if (acc.length && (force || acc.length >= need)) {
      const wav = pcmToWav(acc);
      acc = new Uint8Array(0);
      sent += 1;
      onChunk(wav);
    }
  };
  while (!done) {
    const { value, done: rd } = await reader.read();
    if (rd) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) {
        const evt = line.slice(6).trim();
        // 153 是失败事件：即使 code=0 / data 为空也必须标记失败
        if (evt === "153" && !err) err = "SessionFailed(153)";
        if (evt === "152") done = true;
        continue;
      }
      if (!line.startsWith("data:")) continue;
      let j;
      try {
        j = JSON.parse(line.slice(5).trim());
      } catch (_) {
        continue;
      }
      const code = j.code;
      if (code !== undefined && code !== null && !OK_CODES.has(code) && !err) {
        err = `${code}: ${String(j.message || "").slice(0, 120)}`;
      }
      if (j.data) {
        try {
          acc = concat(acc, b64ToBytes(j.data));
          flush(false);
        } catch (_) {
          /* 忽略脏分片 */
        }
      }
    }
  }
  flush(true);
  // 已有分片下发时不抛异常（避免上层降级重复播报，与 Python 版一致）
  if (err && sent === 0) return { ok: false, chunks: 0, error: err };
  return { ok: true, chunks: sent, error: err || undefined };
}

/** 非流式合成（降级路径）：返回整段 WAV 或 null。 */
export async function synthesize(env, text) {
  const parts = [];
  const r = await synthesizeStream(env, text, (wav) => parts.push(wav));
  if (!parts.length) return null;
  // 把多片 WAV 拼成一个（去掉后片 44 字节头）
  let total = 44;
  for (let i = 0; i < parts.length; i++) total += parts[i].length - 44;
  const out = new Uint8Array(total);
  out.set(parts[0], 0);
  let off = parts[0].length;
  for (let i = 1; i < parts.length; i++) {
    out.set(parts[i].subarray(44), off);
    off += parts[i].length - 44;
  }
  const dv = new DataView(out.buffer);
  dv.setUint32(4, total - 8, true);
  dv.setUint32(40, total - 44, true);
  if (!r.ok && !parts.length) return null;
  return out;
}
