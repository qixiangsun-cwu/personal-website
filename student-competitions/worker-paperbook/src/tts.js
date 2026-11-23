/**
 * 纸书智伴 · 在线朗读
 * 两条链路，按配置自动选择：
 *   1) 火山引擎大模型语音合成（VOLC_TTS_API_KEY + VOLC_TTS_APPID）——与交付包同源，音质最好
 *   2) Microsoft Edge 朗读（无需 Key）——交付包默认 edge-tts 的等价实现，浏览器/Worker 直连
 * 全链路返回 mp3 字节；失败一律抛错，由调用方降级为浏览器 speechSynthesis，绝不返回假音频。
 */

const EDGE_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4';
const EDGE_URL = 'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1';
const TRUSTED_CLIENT_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4';

/** 交付包 server.mjs 里的 10 个音色（id → edge voice），前端下拉框直接用这些 id */
export const TTS_VOICES = [
  { id: 'xiaoxiao', name: '晓晓姐姐', desc: '温柔亲和 · 中文女声', voice: 'zh-CN-XiaoxiaoNeural' },
  { id: 'xiaoyi', name: '晓伊老师', desc: '清晰讲解 · 中文女声', voice: 'zh-CN-XiaoyiNeural' },
  { id: 'yunxi', name: '云希哥哥', desc: '活泼明快 · 中文男声', voice: 'zh-CN-YunxiNeural' },
  { id: 'yunjian', name: '云健叔叔', desc: '沉稳低音 · 中文男声', voice: 'zh-CN-YunjianNeural' },
  { id: 'yunxia', name: '云夏小朋友', desc: '童声 · 中文男声', voice: 'zh-CN-YunxiaNeural' },
  { id: 'yunyang', name: '云扬主播', desc: '播音腔 · 中文男声', voice: 'zh-CN-YunyangNeural' },
  { id: 'xiaobei', name: '晓北阿姨', desc: '东北话 · 中文女声', voice: 'zh-CN-liaoning-XiaobeiNeural' },
  { id: 'xiaoni', name: '晓妮姐姐', desc: '陕西话 · 中文女声', voice: 'zh-CN-shaanxi-XiaoniNeural' },
  { id: 'en-aria', name: '英语 · Aria', desc: '英文女声 · 绘本原文', voice: 'en-US-AriaNeural' },
  { id: 'en-jenny', name: '英语 · Jenny', desc: '英文女声 · 美式', voice: 'en-US-JennyNeural' }
];

export const VOLC_VOICES = [
  { id: 'volc-kefu', name: '客服女声', desc: '火山 · 温和清晰', voice: 'zh_female_xiaohe_uranus_bigtts' },
  { id: 'volc-story', name: '故事男声', desc: '火山 · 沉稳讲述', voice: 'zh_male_M392_conversation_wvae_bigtts' }
];

export function voiceById(id) {
  const all = [...TTS_VOICES, ...VOLC_VOICES];
  return all.find(v => v.id === id) || TTS_VOICES[0];
}

export function rateString(rate) {
  const r = Number(rate);
  const pct = Number.isFinite(r) ? Math.max(-50, Math.min(100, Math.round((r - 1) * 100))) : 0;
  return (pct >= 0 ? '+' : '') + pct + '%';
}

/* ---------------------------- 火山大模型 TTS ---------------------------- */
async function volcTTS(env, text, voiceId, rate) {
  const key = env.VOLC_TTS_API_KEY;
  const appId = env.VOLC_TTS_APPID || env.VOLC_TTS_APP_ID || '';
  if (!key) throw new Error('未配置 VOLC_TTS_API_KEY');
  const speaker = (VOLC_VOICES.find(v => v.id === voiceId) || VOLC_VOICES[0]).voice;
  const url = env.VOLC_TTS_URL || 'https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse';
  const body = {
    app: { appid: appId, token: key, cluster: 'volcano_tts' },
    reqid: crypto.randomUUID(),
    user: { uid: 'paperbook-worker' },
    audio: {
      voice_type: speaker, encoding: 'mp3', speed_ratio: Number(rate) || 1, volume_ratio: 1, pitch_ratio: 1
    },
    request: { reqid: crypto.randomUUID(), text, text_type: 'plain', operation: 'query' }
  };
  const r = await fetch(url, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'authorization': 'Bearer;' + key,
      'x-api-key': key,
      'x-api-resource-id': env.VOLC_TTS_RESOURCE_ID || 'seed-tts-2.0',
      'x-api-request-id': crypto.randomUUID(),
      'accept': 'text/event-stream'
    },
    body: JSON.stringify(body)
  });
  if (!r.ok) throw new Error('火山 TTS HTTP ' + r.status);
  const txt = await r.text();
  // SSE：每行 data: {...}，音频在 base64 里
  const chunks = [];
  for (const line of txt.split(/\r?\n/)) {
    const m = /^data:\s*(\{.*\})\s*$/.exec(line.trim());
    if (!m) continue;
    let j;
    try { j = JSON.parse(m[1]); } catch { continue; }
    const code = j.code ?? 0;
    if (code !== 0 && code !== 3000 && code !== 20000000) continue;
    const b = j.data || (j.audio && j.audio.data) || '';
    if (b) chunks.push(b64ToBytes(b));
  }
  if (!chunks.length) throw new Error('火山 TTS 未返回音频');
  return concatBytes(chunks);
}

function b64ToBytes(b64) {
  const bin = atob(String(b64).replace(/[^A-Za-z0-9+/=]/g, ''));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function concatBytes(list) {
  let n = 0;
  for (const a of list) n += a.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const a of list) { out.set(a, o); o += a.length; }
  return out;
}

/* ---------------------------- Edge 朗读（免 Key） ---------------------------- */
async function sha256Hex(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function edgeTTS(text, voiceName, rate) {
  const secMs = Math.floor((Date.now() + 11644473600000) / 300000) * 300000;
  const gec = (await sha256Hex(String(secMs) + EDGE_TOKEN)).toUpperCase();
  const connId = [...crypto.randomUUID().replace(/-/g, '')].join('');
  const qs = '?TrustedClientToken=' + TRUSTED_CLIENT_TOKEN
    + '&Sec-MS-GEC=' + gec
    + '&Sec-MS-GEC-Version=1-130.0.2849.68'
    + '&ConnectionId=' + connId;
  const url = EDGE_URL + qs;
  const resp = await fetch(url.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:'), {
    headers: {
      'Upgrade': 'websocket',
      'Origin': 'chrome-extension://jdiccldimpldjdmfncgmihnhojdahdgc',
      'Pragma': 'no-cache',
      'Cache-Control': 'no-cache',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'
    }
  });
  const ws = resp.webSocket;
  if (!ws) throw new Error('edge-tts 连接失败 HTTP ' + resp.status);
  ws.accept();
  const audio = [];
  let done = false;
  const ts = new Date().toISOString();
  const config = JSON.stringify({
    context: { synthesis: { audio: { metadataoptions: { sentenceBoundaryEnabled: false, wordBoundaryEnabled: false }, outputFormat: 'audio-24khz-48kbitrate-mono-mp3' } } }
  });
  ws.send('X-Timestamp:' + ts + '\r\nContent-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n' + config + '\r\n');
  const ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><voice name='" + voiceName + "'>"
    + "<prosody pitch='+0Hz' rate='" + rateString(rate) + "' volume='+0%'>"
    + escapeXml(text) + '</prosody></voice></speak>';
  ws.send('X-RequestId:' + crypto.randomUUID().replace(/-/g, '') + '\r\nContent-Type:application/ssml+xml\r\nX-Timestamp:' + ts + 'Z\r\nPath:ssml\r\n\r\n' + ssml);

  const MAX_MS = 25000;
  const started = Date.now();
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { try { ws.close(); } catch { } reject(new Error('edge-tts 超时')); }, MAX_MS);
    ws.addEventListener('message', ev => {
      const d = ev.data;
      if (typeof d === 'string') {
        if (d.includes('Path:turn.end')) { done = true; clearTimeout(timer); try { ws.close(); } catch { } resolve(); }
        return;
      }
      // 二进制帧：前 2 字节大端表示「头长度」，跳过头部后是音频
      const u8 = new Uint8Array(d);
      if (u8.length < 2) return;
      const hlen = (u8[0] << 8) | u8[1];
      const start = 2 + hlen;
      if (u8.length > start) audio.push(u8.slice(start));
    });
    ws.addEventListener('close', () => { clearTimeout(timer); if (audio.length) resolve(); else reject(new Error('edge-tts 关闭且无音频')); });
    ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('edge-tts 连接错误')); });
  });
  if (!audio.length) throw new Error('edge-tts 未返回音频');
  return concatBytes(audio);
}

function escapeXml(s) {
  return String(s).replace(/[<>&"']/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c]));
}

/* ---------------------------- 统一入口 ---------------------------- */
export function ttsEngine(env) {
  const pref = String(env.PB_TTS_PROVIDER || '').toLowerCase();
  if (pref === 'volc' && env.VOLC_TTS_API_KEY) return 'volc';
  if (pref === 'edge') return 'edge';
  if (env.VOLC_TTS_API_KEY) return 'volc';
  return 'edge';
}

/** edge-tts 在 Worker 里依赖「WebSocket 升级 + 二进制帧」，先做能力探测，避免每次朗读都白等一次超时 */
let EDGE_WS_OK = null;
export async function edgeCapable() {
  if (EDGE_WS_OK !== null) return EDGE_WS_OK;
  EDGE_WS_OK = false;
  try {
    const r = await fetch(EDGE_URL + '?TrustedClientToken=' + TRUSTED_CLIENT_TOKEN, {
      headers: { 'Upgrade': 'websocket', 'Origin': 'chrome-extension://jdiccldimpldjdmfncgmihnhojdahdgc' }
    });
    if (r && r.webSocket) { try { r.webSocket.accept(); r.webSocket.close(); } catch { } EDGE_WS_OK = true; }
  } catch { EDGE_WS_OK = false; }
  return EDGE_WS_OK;
}

export async function voicesPayload(env) {
  const engine = ttsEngine(env);
  let configured = engine === 'volc' ? !!env.VOLC_TTS_API_KEY : true;
  let note;
  if (engine === 'volc') {
    note = '音色由火山引擎大模型语音合成提供，语速 0.5–2.0 可调。';
  } else {
    /* 免密钥的 edge 朗读在数据中心出口会被端点拒绝，这里如实探测后告知前端 */
    configured = await edgeCapable();
    note = configured
      ? '音色由 Microsoft Edge 朗读提供（免密钥），语速 0.5–2.0 可调。'
      : '云端朗读当前不可用（运行环境未开通朗读通道），已自动使用浏览器内置语音朗读；音质以本地浏览器为准。';
  }
  return {
    ok: true,
    engine,
    provider: engine,
    configured,
    fineVoiceReady: engine === 'volc',
    browserTTS: true,
    pending: false,
    cloneReady: false,
    cloneDevice: '',
    cloneVoices: [],
    defaultVoice: engine === 'volc' ? VOLC_VOICES[0].id : TTS_VOICES[0].id,
    voices: engine === 'volc' ? VOLC_VOICES : TTS_VOICES,
    note
  };
}

/** 返回 { ok, bytes, contentType, engine } 或 { ok:false, degraded:true, message } */
export async function synthesize(env, { text, voice, rate } = {}) {
  const t = String(text || '').trim().slice(0, 1200);
  if (!t) return { ok: false, degraded: true, message: '没有需要朗读的文字' };
  const engine = ttsEngine(env);
  const v = voiceById(voice);
  try {
    if (engine === 'volc') {
      const bytes = await volcTTS(env, t, String(voice || ''), rate);
      return { ok: true, bytes, contentType: 'audio/mpeg', engine: 'volc' };
    }
    if (!(await edgeCapable())) {
      return { ok: false, degraded: true, engine, reason: 'runtime-no-ws', message: '当前运行环境不支持 WebSocket 直连朗读服务，请使用浏览器语音合成。' };
    }
    const bytes = await edgeTTS(t, v.voice, rate);
    return { ok: true, bytes, contentType: 'audio/mpeg', engine: 'edge' };
  } catch (e) {
    return {
      ok: false, degraded: true, engine,
      message: '在线 TTS 暂不可用，请使用浏览器语音合成：' + String((e && e.message) || e)
    };
  }
}

export function ttsWorkerStatus(env) {
  const engine = ttsEngine(env);
  return {
    ok: true, engine, online: engine === 'volc' ? !!env.VOLC_TTS_API_KEY : true,
    workers: 0, queue: 0, cached: 0, note: '朗读由在线服务直接合成，不占用本机进程。'
  };
}
