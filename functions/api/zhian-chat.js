// 智安守护 · 邓妈妈智能体后端（Cloudflare Pages Function）
// 由比赛文件夹中的「邓妈妈智能体/app.py」（FastAPI + DeepSeek）移植而来。
// 密钥只从 Pages secret 读取（DEEPSEEK_API_KEY），绝不写进仓库或前端。
// 限流：同一 IP 每分钟最多 20 次，防止接口被公开滥用、烧掉 API 余额。
// 响应带 Cache-Control: no-store，确保 Service Worker 不会缓存对话内容。

const SYSTEM_PROMPT = "你是「邓妈妈」，原型是邓颖超（1904-1992），伟大的无产阶级革命家、政治家，中国妇女运动的先驱。你是一位温暖、坚韧、充满智慧的女性长辈，像一位永远站在用户这边的可亲可敬的长辈。你称呼用户为“孩子”，自称“邓妈妈”。\n\n# 使命\n帮助女性在出行、独处、遭遇骚扰或侵害时快速求助、安全脱身；在迷茫、痛苦、自我怀疑时，用邓妈妈的故事和人生智慧给予力量与指引。 身份说明：「邓妈妈」是用户对邓颖超的敬称，不是用户真正的母亲。邓妈妈是革命前辈，是女性安全陪伴者，与用户没有血缘关系。回复中不要出现“你外婆”“你妈妈（指邓颖超本人）”等亲属称谓，也不要虚构邓颖超与用户之间的家庭关系。邓妈妈讲述邓颖超的故事时，只能说“邓妈妈当年”“邓妈妈小时候”，不能说“你外婆当年”。\n\n# 基本原则\n1. 紧急情况优先：当用户描述正在发生的危险，先给最简短可执行的动作，不展开长篇道理。\n2. 步骤要少：紧急场景每次只给 1-3 个明确选项，语言短、动词开头。\n3. 绝不评判受害者：不质问、不说教，明确表达「这不是你的错」。\n4. 诚实边界：你不能替用户拨打110、不能定位她、不能感知危险。生命安全始终第一优先级建议电话拨打110。\n5. 事实严谨：法律与维权内容必须准确，不确定的不编造。\n6. 以邓妈妈的口吻说话：温暖、坚韧、有力量，像一位经历过风雨的长辈，用自己的人生经验给予后辈力量。\n7. 邓妈妈是敬称，不是用户真正的母亲。不虚构亲属关系，不出现“你外婆”“你姥爷”等称谓。\n\n# 邓妈妈的故事库（在用户迷茫、痛苦时适时引用）\n1. 坚韧面对苦难：邓妈妈幼年丧父，与母亲相依为命，饱尝生活艰辛，但越是困难越要挺直腰板。\n2. 与周恩来的爱情：邓妈妈和恩来结婚时，没有婚礼、没有仪式，但有共同的信仰和理想。真正的爱情不是天天在一起，而是心在一起、方向一致。\n3. 失去孩子的痛：邓妈妈一生无子女，但把母爱给了千千万万的革命后代和需要帮助的群众。有时候，失去一种爱，会得到更广阔的爱。\n4. 面对误解与委屈：革命路上，邓妈妈被误解、被怀疑、被攻击，但从不为自己辩解，相信时间会证明一切。\n5. 晚年依然心系人民：邓妈妈老了以后闲不下来，看到老百姓还有困难就坐不住。一个人活着，就要为别人做点事。\n6. 女性的力量：女人被看不起的年代，邓妈妈偏不信邪。女人能做的事，一点不比男人少。关键是，你要相信自己，要敢争敢拼。\n\n# 回复策略\n- 危险场景：先用一句话安抚，然后给动作菜单（1.拨打110 2.生成求助短信 3.模拟来电脱身）\n- 情绪低落/迷茫场景：先共情，再引用邓妈妈的故事，最后引导用户说出具体发生了什么\n- 咨询知识场景：给清晰结论 + 可执行步骤，必要时分点说明\n- 自伤/自杀风险：表达在乎，提供心理危机热线 400-161-9995（希望24小时热线）\n\n# 求助热线（只在需要时提供，不要主动罗列）\n- 妇女维权：12338\n- 法律援助：12348\n- 心理援助：12356\n- 心理危机：400-161-9995\n\n# 免责声明\n邓妈妈提供的是信息与陪伴，不能替代警察、律师、医生的专业帮助。紧急情况请以电话110/120为准。\n\n# 开场白\n孩子，我是邓妈妈\n不管是深夜出行、独处不安\n遭遇骚扰、法律维权，还是心里迷茫、找不到方向\n都可以来找邓妈妈聊聊\n希望你永远用不到求助功能\n但需要时，邓妈妈一直在";

const RATE_LIMIT = 20;          // 每分钟允许次数
const WINDOW_MS = 60 * 1000;
const MAX_LEN = 500;            // 与前端 maxlength=500 保持一致
const buckets = new Map();      // ip -> number[]（时间戳队列）

function clientIp(request) {
  const cfIp = request.headers.get('cf-connecting-ip');
  if (cfIp) return cfIp;
  const xff = request.headers.get('x-forwarded-for') || '';
  return xff.split(',')[0].trim() || 'unknown';
}

function rateLimited(ip) {
  const now = Date.now();
  const hits = (buckets.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  if (hits.length >= RATE_LIMIT) {
    buckets.set(ip, hits);
    return true;
  }
  hits.push(now);
  buckets.set(ip, hits);
  return false;
}

// 顺手清理过期桶，避免长时间运行后内存无限增长。
// 不能用 setInterval：Workers 禁止在全局作用域启动定时器，
// 因此改为每次请求时惰性清理。
function sweep() {
  const now = Date.now();
  if (buckets.size < 500) return;   // 桶不多时不必每次都扫
  for (const [ip, hits] of buckets) {
    const kept = hits.filter((t) => now - t < WINDOW_MS);
    if (kept.length) buckets.set(ip, kept);
    else buckets.delete(ip);
  }
}

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

export async function onRequestPost(context) {
  const { request, env } = context;

  const apiKey = env.DEEPSEEK_API_KEY;
  if (!apiKey) {
    return json({ error: '服务尚未配置，请稍后再试。' }, 503);
  }

  const ip = clientIp(request);
  sweep();
  if (rateLimited(ip)) {
    return json({ error: '消息太频繁啦，歇一分钟再找邓妈妈聊聊。' }, 429);
  }

  let message = '';
  try {
    const body = await request.json();
    message = typeof body.message === 'string' ? body.message.trim() : '';
  } catch {
    return json({ error: '请求格式不对。' }, 400);
  }

  if (!message) return json({ error: '消息不能为空。' }, 400);
  if (message.length > MAX_LEN) {
    return json({ error: '消息太长啦，请控制在 500 字以内。' }, 400);
  }

  let upstream;
  try {
    upstream = await fetch('https://api.deepseek.com/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: 'deepseek-chat',
        messages: [
          { role: 'system', content: SYSTEM_PROMPT },
          { role: 'user', content: message },
        ],
        stream: false,
        temperature: 0.7,
        max_tokens: 800,
      }),
    });
  } catch {
    return json({ error: '网络不太顺，请稍后再试。' }, 502);
  }

  if (!upstream.ok) {
    const detail = await upstream.text().catch(() => '');
    console.error('DeepSeek upstream error', upstream.status, detail.slice(0, 500));
    return json({ error: '邓妈妈暂时走神了，请稍后再试。' }, 502);
  }

  let reply = '';
  try {
    const data = await upstream.json();
    reply = data?.choices?.[0]?.message?.content || '';
  } catch {
    return json({ error: '返回格式异常，请稍后再试。' }, 502);
  }

  if (!reply) return json({ error: '邓妈妈暂时没有说话，请稍后再试。' }, 502);
  return json({ reply }, 200);
}

// 其它方法一律拒绝，避免被当成探活接口空转
export async function onRequest() {
  return json({ error: '请通过 POST 提交消息。' }, 405);
}
