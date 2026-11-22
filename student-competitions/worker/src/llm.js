/**
 * 智语外呼 · LLM 适配（智谱 glm-4-flashx，OpenAI 兼容 /chat/completions）
 * 对应 Python 版 agent.py 里 `ZhipuAI(api_key=...).chat.completions.create(...)`
 */

function baseUrl(env) {
  return (env.ZHIPU_BASE_URL || "https://open.bigmodel.cn/api/paas/v4").replace(/\/+$/, "");
}

function headers(env) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${env.ZHIPU_API_KEY || ""}`,
  };
}

/** 非流式补全（对应 temperature=0, max_tokens 很小的判定调用）。 */
export async function complete(env, messages, opts = {}) {
  const body = {
    model: env.ZHIPU_MODEL || "glm-4-flashx",
    messages,
    temperature: opts.temperature === undefined ? 0.0 : opts.temperature,
    stream: false,
  };
  if (opts.max_tokens) body.max_tokens = opts.max_tokens;
  const res = await fetch(`${baseUrl(env)}/chat/completions`, {
    method: "POST",
    headers: headers(env),
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`llm ${res.status}: ${text.slice(0, 200)}`);
  const data = JSON.parse(text);
  return (data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content) || "";
}

/**
 * 流式补全：以增量回调返回文本片段。
 * 返回完整文本（即便流被中断，也返回已收到的部分）。
 */
export async function stream(env, messages, onDelta, opts = {}) {
  const body = {
    model: env.ZHIPU_MODEL || "glm-4-flashx",
    messages,
    temperature: opts.temperature === undefined ? 0.3 : opts.temperature,
    stream: true,
  };
  if (opts.max_tokens) body.max_tokens = opts.max_tokens;
  const res = await fetch(`${baseUrl(env)}/chat/completions`, {
    method: "POST",
    headers: headers(env),
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`llm ${res.status}: ${String(text).slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let full = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        const j = JSON.parse(payload);
        const d = j.choices && j.choices[0];
        const piece =
          (d && d.delta && (d.delta.content || d.delta.reasoning_content)) || "";
        if (piece) {
          full += piece;
          if (onDelta) onDelta(piece);
        }
      } catch (_) {
        /* 忽略半包/心跳 */
      }
    }
  }
  return full;
}
