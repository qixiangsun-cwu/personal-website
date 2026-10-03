// 智安守护 · 在线语音合成（Edge TTS）
// 用于无障碍「朗读此页」与「朗读回复」。
// 选型说明：DeepSeek 官方 API 不提供语音合成能力，故采用微软 Edge 朗读服务。
// 该服务无需密钥、中文音色自然、无需额外计费，适合本次比赛的公益场景。
// 注意：这是非官方公开接口，如需长期商用稳定性应改接正式 TTS 服务商。

const EDGE_TTS_HOST = 'speech.platform.bing.com';
const TRUSTED_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4';

// 与 DeepSeek 接口共用的限流逻辑，避免另起一套阈值
const RATE_LIMIT = 20;
const WINDOW_MS = 60 * 1000;
const MAX_LEN = 2000;   // 单次合成文本上限，防止被滥用成免费 TTS 服务
const buckets = new Map();

function clientIp(request) {
  return request.headers.get('cf-connecting-ip') || 'unknown';
}

function rateLimited(ip) {
  const now = Date.now();
  const hits = (buckets.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  if (hits.length >= RATE_LIMIT) { buckets.set(ip, hits); return true; }
  hits.push(now);
  buckets.set(ip, hits);
  return false;
}

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204 });
}

export async function onRequest(context) {
  const { request } = context;
  if (request.method !== 'POST') {
    return json({ error: '请通过 POST 提交文本。' }, 405);
  }
  if (rateLimited(clientIp(request))) {
    return json({ error: '请求太频繁，请稍后再试。' }, 429);
  }

  let text = '';
  try {
    const body = await request.json();
    text = typeof body.text === 'string' ? body.text.trim() : '';
  } catch {
    return json({ error: '请求格式不对。' }, 400);
  }
  if (!text) return json({ error: '文本不能为空。' }, 400);
  if (text.length > MAX_LEN) {
    // 超长正文（朗读整页）截断，避免请求体过大与费用浪费
    text = text.slice(0, MAX_LEN);
  }

  // 组装 SSML：中文女声，语速稍慢，适合长辈陪伴感
  const ssml = `<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>
    <voice name='zh-CN-XiaoxiaoNeural'>
      <prosody rate='-10%' pitch='+5Hz'>${escapeXml(text)}</prosody>
    </voice>
  </speak>`;

  const url = `https://${EDGE_TTS_HOST}/consumer/speech/synthesize/readaloud/voices` +
    `/list?trustedclienttoken=${TRUSTED_TOKEN}`;

  // 先取可用音色，确认端点可达并拿到 WS URL
  let voicesRes;
  try {
    voicesRes = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      cf: { cacheTtl: 86400 },
    });
  } catch (err) {
    return json({ error: '语音服务暂时不可达，请稍后再试。' }, 502);
  }
  if (!voicesRes.ok) {
    return json({ error: '语音服务返回异常，请稍后再试。' }, 502);
  }

  const voices = await voicesRes.json().catch(() => []);
  const zh = Array.isArray(voices) ? voices.filter((v) => /^zh[-_]?CN/i.test(v.Locale || '')) : [];
  const voice = zh[0];
  if (!voice) return json({ error: '未找到可用的中文语音。' }, 502);

  const ttsUrl = new URL(`https://${EDGE_TTS_HOST}/tts/consumer/speech/synthesize/` +
    `readaloud/edge/v1?TrustedClientToken=${TRUSTED_TOKEN}`);
  ttsUrl.searchParams.set('ConnectionId', voice.VoiceId);

  const secMsGec = await makeSecMsGec();

  let audioRes;
  try {
    audioRes = await fetch(ttsUrl.toString(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/ssml+xml',
        'X-Microsoft-OutputFormat': 'riff-24khz-16bit-mono-pcm',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Sec-MS-GEC': secMsGec,
        'Sec-MS-GEC-Version': '1-143.0.3650.75',
      },
      body: ssml,
    });
  } catch (err) {
    return json({ error: '语音合成失败，请稍后再试。' }, 502);
  }

  if (!audioRes.ok) {
    const detail = await audioRes.text().catch(() => '');
    console.error('EdgeTTS error', audioRes.status, detail.slice(0, 300));
    console.error('EdgeTTS', audioRes.status, detail.slice(0, 200));
    return json({ error: '语音服务暂时不可用，请稍后再试。' }, 502);
  }

  const audio = await audioRes.arrayBuffer();
  if (!audio.byteLength) return json({ error: '未生成音频。' }, 502);

  return new Response(audio, {
    status: 200,
    headers: {
      'Content-Type': 'audio/wav',
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
    },
  });
}


/**
 * 生成 Edge TTS 要求的 Sec-MS-GEC 校验令牌。
 * 算法（对齐 rany2/edge-tts 的 DRM.generate_sec_ms_gec）：
 *   1) 取当前 Unix 秒
 *   2) 加 WIN_EPOCH 换成 Windows file time
 *   3) 向下取整到 5 分钟边界，保证同一窗口内令牌稳定
 *   4) 乘 1e7 转成 100 纳秒刻度
 *   5) 拼接「刻度 + TrustedClientToken」做 SHA-256，取大写十六进制
 * 注意顺序是 ticks 在前、token 在后，颠倒会导致 400。
 */
async function makeSecMsGec() {
  const ticks = Math.floor(Date.now() / 1000) + 11644473600;
  const windowed = ticks - (ticks % 300);
  const intervals = windowed * 10000000;
  const payload = intervals + TRUSTED_TOKEN;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
}

function escapeXml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}