/**
 * 纸书智伴 · Cloudflare Worker 入口
 * ---------------------------------------------------------------------------
 * 这是交付包里 server/server.mjs（node:http + fs + spawn Python）的 Worker 版：
 *   · 静态页面/CSS/JS/字体/词典 → 内联进 Worker（src/static.gen.js，逐字不变）
 *   · 绘本原图与 MP4 视频       → R2 桶 paperbook-assets（支持 Range 拖动）
 *   · 书库元数据 / index.json / 日志 → D1 cwu-paperbook-db
 *   · 问答、引导、知识、共创、翻译、识字、单词、视觉标注 → DeepSeek（环境密钥）
 *   · 朗读 → 火山引擎 HTTP SSE（首选）/ edge-tts（备选）
 *   · Remotion 渲染、豆包视频、音色克隆、照片 OCR 后台上传 → 本机演示能力，这里明确返回降级说明
 * 前端文件一个字都没改：页面、交互、视觉与本地版完全一致。
 */
import { FILES } from './static.gen.js';
import {
  json, err, readJSON, logEvent, readLogs, readLogsPage, logEventRow, CORS_HEADERS,
  r2Response, r2Head, mimeOf, clamp
} from './runtime.js';
import { enterRequest, currentEnv, getState } from './store.js';
import { PAGE_ALIASES, buildPage, buildIndexHtml, fileBytes, HTML_HEADERS } from './pagebuild.js';
import {
  listBooks, summary, readBook, deleteBook, patchBook,
  getActiveId, setActiveId, getSettings, saveSettings,
  currentIndex, buildBookIndex, analyzeAndCreate,
  buildGuide, buildKnowledge, buildCoCreate,
  callDeepSeek, splitSentences, bookStatic, visionStatus
} from './books.js';
import { checkBookSafety, getSafetyVerdict, clearSafetyCache, checkTextSafety } from './content-safety.js';
import { hasLLM, llmModel } from './llm.js';
import { voicesPayload, synthesize, ttsWorkerStatus, TTS_VOICES, ttsEngine } from './tts.js';

/* ------------------------------------------------------------------ 工具 */

const NOW = () => new Date().toISOString();

function withCors(res) {
  for (const [k, v] of Object.entries(CORS_HEADERS)) res.headers.set(k, v);
  return res;
}

/** 原版把 JSON 响应统一加 CORS；Worker 同源部署，加不加都行，保持一致 */
function ok(data, status = 200) {
  return withCors(json(data, status));
}

function fail(message, status = 400, extra) {
  return withCors(err(message, status, extra));
}

function html(text, status = 200) {
  return new Response(text, {
    status,
    headers: {
      ...HTML_HEADERS,
      'access-control-allow-origin': '*'
    }
  });
}

function staticFile(name, cacheSec) {
  const f = FILES[name];
  if (!f) return null;
  const bytes = fileBytes(name);
  if (!bytes) return null;
  const headers = {
    'content-type': f.ct || mimeOf(name),
    'content-length': String(bytes.length),
    'access-control-allow-origin': '*',
    'x-content-type-options': 'nosniff'
  };
  if (cacheSec) headers['cache-control'] = 'public, max-age=' + cacheSec;
  return new Response(bytes, { status: 200, headers });
}

/** 归一化路径（解码 + 去 query） */
function normPath(url) {
  let p = url.pathname;
  try { p = decodeURI(p); } catch { /* 保留原样 */ }
  return p;
}

/* ------------------------------------------------------- 示例绘本索引兜底 */

/**
 * 内置示例「小兔找家」的索引由 assets/video/index.json + offline-data.js 提供；
 * 前端在 AI 不可用时用离线数据，服务端只需要能把 index.json 当"示例索引"返回。
 */
function sampleIndex() {
  const txt = FILES['assets/video/index.json'] ? Buffer.from(FILES['assets/video/index.json'].b64, 'base64').toString('utf8') : '';
  try { return JSON.parse(txt); } catch { return null; }
}

/* ------------------------------------------------------------ /api/ 路由 */

async function api(request, env, url) {
  const p = url.pathname.replace(/\/+$/, '') || '/api';
  const method = request.method.toUpperCase();

  if (method === 'OPTIONS') {
    return withCors(new Response(null, { status: 204 }));
  }

  /* ---- 健康检查 ---- */
  if (p === '/api/health') {
    return ok({
      ok: true,
      service: '纸书智伴 · 云伴读服务',
      time: NOW(),
      runtime: 'cloudflare-workers',
      indexLoaded: !!currentIndex(sampleIndex()),
      adapters: {
        deepseek: hasLLM(env),
        tts: ttsEngine(env),
        remotion: false,
        doubao: false,
        voiceClone: false
      },
      model: llmModel(env)
    });
  }

  /* ---- 示例/激活书索引 ---- */
  if (p === '/api/index' && method === 'GET') {
    const wantId = url.searchParams.get('bookId') || '';
    let idx;
    if (wantId && wantId !== 'sample') {
      const b = readBook(wantId);
      if (!b) return fail('book not found', 404);
      const st = getState(env);
      idx = st.indexes[b.id] || buildBookIndex(b);
    } else {
      idx = currentIndex(sampleIndex());
    }
    if (!idx) return fail('index.json 未就绪：请先运行一次生成流程，或激活一本书。', 503);
    return ok(idx);
  }

  /* ---- 定位页码 ---- */
  if (p === '/api/locate' && method === 'POST') {
    const body = await readJSON(request);
    const keyword = String(body?.keyword || '').trim();
    if (!keyword) return fail('请给一个关键词');
    const idx = currentIndex(sampleIndex());
    if (!idx) return fail('index.json 未就绪', 503);
    const t0 = Date.now();
    const pages = (idx.pages || []).map(x => ({ pageNo: x.pageNo, text: x.text || x.narration || '' }));
    if (!hasLLM(env)) {
      // 无模型时退回关键字直接匹配，绝不编造页码
      const hit = pages.find(x => x.text.includes(keyword));
      return ok({ ok: !!hit, pageNo: hit ? hit.pageNo : null, reason: hit ? '按关键字直接命中' : '没有在原文里找到该关键词', latencyMs: Date.now() - t0, degraded: true });
    }
    try {
      const raw = await callDeepSeek(
        '你是绘本页码定位助手。只依据给定的每页原文，判断用户关键词最可能出现在第几页。只输出 JSON：{"pageNo":数字,"reason":"简短理由"}；如果原文里没有相关内容，pageNo 填 null。',
        '每页原文：\n' + pages.map(x => '第' + x.pageNo + '页：' + String(x.text).slice(0, 200)).join('\n') + '\n\n用户关键词：' + keyword
      );
      return ok({ ok: raw?.pageNo != null, pageNo: raw?.pageNo ?? null, reason: raw?.reason || '', latencyMs: Date.now() - t0 });
    } catch (e) {
      return ok({ ok: false, pageNo: null, reason: '模型调用失败：' + String(e.message).slice(0, 120), latencyMs: Date.now() - t0, degraded: true });
    }
  }

  /* ---- 书库 ---- */
  if (p === '/api/books' && method === 'GET') {
    return ok({ ok: true, ...listBooks(), vision: visionStatus() });
  }

  if (p === '/api/books/analyze' && method === 'POST') {
    if (String(env.PB_ALLOW_UPLOAD || '0') !== '1') {
      return fail('线上版已关闭「上传绘本」：版面识别依赖本机 tesseract.js 与 Remotion 渲染，请在本地演示环境中使用。', 503, { ok: false, degraded: true });
    }
    const body = await readJSON(request);
    const r = await analyzeAndCreate(body, (type, detail) => logEvent(env, type, detail));
    if (r.error) return fail(r.error, r.status || 400, { ok: false, degraded: !!r.degraded });
    return ok(r.result, r.status || 201);
  }

  let m;
  if ((m = /^\/api\/books\/(b-[a-z0-9-]+)$/.exec(p))) {
    const id = m[1];
    if (method === 'GET') {
      const b = readBook(id);
      if (!b) return fail('book not found', 404, { ok: false });
      return ok({ ok: true, book: b });
    }
    if (method === 'PATCH') {
      const body = await readJSON(request);
      const b = patchBook(id, body || {});
      if (!b) return fail('book not found', 404, { ok: false });
      return ok({ ok: true, book: b });
    }
    if (method === 'DELETE') {
      if (String(env.PB_ALLOW_ADMIN || '0') !== '1') return fail('线上版已关闭删除书库操作', 503, { ok: false });
      const done = deleteBook(id);
      if (!done) return fail('book not found', 404, { ok: false });
      return ok({ ok: true });
    }
  }

  /* ---- 内容安全 ---- */
  if (p === '/api/book/safety' && (method === 'GET' || method === 'POST')) {
    const bookId = url.searchParams.get('bookId') || getActiveId() || 'sample';
    const force = url.searchParams.get('force') === '1';
    const v = await getSafetyVerdict(bookId, { env, force });
    return ok({ ...v, ok: v.ok !== false });
  }
  if ((m = /^\/api\/book\/safety\/(b-[a-z0-9-]+)\/recheck$/.exec(p)) && method === 'POST') {
    clearSafetyCache(m[1]);
    const v = await checkBookSafety(m[1], { env, force: true });
    return ok({ ...v, ok: v.ok !== false });
  }

  /* ---- 激活书 ---- */
  if (p === '/api/active') {
    if (method === 'GET') {
      const b = listBooks();
      return ok({
        ok: true,
        activeId: b.activeId,
        activeBook: b.activeId ? (b.books.find(x => x.id === b.activeId) || null) : null,
        sample: b.activeId ? null : { id: 'sample', title: '小兔找家', pageCount: 3 }
      });
    }
    if (method === 'POST') {
      const body = await readJSON(request);
      const raw = body?.bookId;
      if (raw === null || raw === undefined || raw === '' || raw === 'sample') {
        setActiveId(null);
        return ok({ ok: true, activeId: null });
      }
      const id = String(raw);
      if (!readBook(id)) return fail('book not found', 404, { ok: false });
      setActiveId(id);
      return ok({ ok: true, activeId: id });
    }
  }

  /* ---- 朗读 ---- */
  if (p === '/api/tts/voices' && method === 'GET') return ok(await voicesPayload(env));
  if (p === '/api/tts/worker' && method === 'GET') return ok({ ok: true, ...ttsWorkerStatus(env) });
  if (p === '/api/voice-clones' && (method === 'GET' || method === 'POST' || method === 'DELETE')) {
    return ok({ ok: true, ready: false, voices: [], pending: false, message: '音色克隆服务（Chatterbox 3.1GB 模型）需在本机演示环境运行，线上版未启用。' });
  }
  if (p === '/api/tts' && method === 'POST') {
    const body = await readJSON(request);
    const text = String(body?.text || '').slice(0, 800);
    if (!text.trim()) return fail('text 不能为空', 400, { ok: false });
    const r = await synthesize(env, { text, voice: body?.voice, rate: body?.rate });
    if (!r.ok) return fail(r.message || 'TTS 不可用', 503, { ok: false, degraded: true, engine: r.engine, reason: r.reason });
    return new Response(r.bytes, {
      status: 200,
      headers: {
        'content-type': r.contentType || 'audio/mpeg',
        'cache-control': 'no-store',
        'access-control-allow-origin': '*',
        'x-tts-engine': r.engine || ''
      }
    });
  }
  if (p === '/api/tts/stream' && method === 'POST') {
    const body = await readJSON(request);
    const text = String(body?.text || '').slice(0, 800);
    if (!text.trim()) return fail('text 不能为空', 400, { ok: false });
    const engine = ttsEngine(env);
    if (engine === 'volc') {
      // 火山引擎是整段 SSE 返回，这里直接一次性给出完整音频（前端仍按 ArrayBuffer 播放）
      const r = await synthesize(env, { text, voice: body?.voice, rate: body?.rate });
      if (!r.ok) return fail(r.message || 'TTS 不可用', 503, { ok: false, degraded: true, engine: r.engine });
      return new Response(r.bytes, {
        status: 200,
        headers: { 'content-type': r.contentType || 'audio/mpeg', 'cache-control': 'no-store', 'x-tts-stream': '0', 'access-control-allow-origin': '*' }
      });
    }
    const r = await synthesize(env, { text, voice: body?.voice, rate: body?.rate });
    if (!r.ok) return fail(r.message || 'TTS 不可用', 503, { ok: false, degraded: true, engine: r.engine });
    return new Response(r.bytes, {
      status: 200,
      headers: { 'content-type': r.contentType || 'audio/mpeg', 'cache-control': 'no-store', 'x-tts-stream': '1', 'access-control-allow-origin': '*' }
    });
  }

  /* ---- 视觉识别（照片 OCR / 版面） ---- */
  if (p === '/api/vision/status' && method === 'GET') {
    return ok({ ok: true, ...visionStatus(), activeId: getActiveId() });
  }
  if (p === '/api/vision/settings' && method === 'POST') {
    const body = await readJSON(request);
    const s = getSettings();
    if (typeof body?.allowCloudImages === 'boolean') s.allowCloudImages = body.allowCloudImages;
    if (Number.isFinite(body?.maxImageEdge)) s.maxImageEdge = clamp(Math.round(body.maxImageEdge), 320, 2048);
    saveSettings(s);
    return ok({ ok: true, ...visionStatus() });
  }
  if (p === '/api/book/recognize' && method === 'POST') {
    return fail('照片已收到，但线上版不保存上传文件；OCR 由浏览器端 tesseract.js 完成，本接口仅本地演示环境可用。', 501, {
      ok: false, degraded: true, message: '照片已收到并归档，但 OCR/VLM 适配器尚未配置；本次不伪造识别结果。'
    });
  }

  /* ---- 流水线状态 ---- */
  if (p === '/api/pipeline/status' && method === 'GET') {
    const b = listBooks();
    const idx = currentIndex(sampleIndex()) || {};
    const utter = Array.isArray(idx.utterances) ? idx.utterances.length : 0;
    const words = Array.isArray(idx.utterances) ? idx.utterances.reduce((n, u) => n + String(u.text || '').length, 0) : 0;
    const hotspots = Array.isArray(idx.hotspots) ? idx.hotspots.length : 0;
    const last = Array.isArray(idx.timeline) && idx.timeline.length ? idx.timeline[idx.timeline.length - 1] : null;
    const totalMs = last ? (last.end ?? last.endMs ?? 0) : 0;
    return ok({
      ok: true, online: hasLLM(env),
      adapters: { deepseek: hasLLM(env), tts: ttsEngine(env), remotion: false, doubao: false, voiceClone: false },
      products: { book: true, pages: (idx.pages || []).length, index: !!idx.schema, manifest: true, story: false },
      active: { activeId: b.activeId, source: b.activeId ? 'upload' : 'sample', title: b.activeId ? (b.books.find(x => x.id === b.activeId) || {}).title : '小兔找家', bookCount: b.books.length },
      vision: visionStatus(),
      books: b.books.length,
      indexSummary: {
        pages: (idx.pages || []).length, utterances: utter, words, keywords: (idx.keywords || []).length, hotspots,
        durationText: formatMs(totalMs), totalMs
      }
    });
  }

  /* ---- 伴读问答 ---- */
  if (p === '/api/qa' && method === 'POST') {
    const body = await readJSON(request);
    const query = String(body?.query || '').trim();
    if (!query) return fail('请先说出或输入你的问题', 400, { ok: false });
    return ok(await answerQa(env, body, query));
  }

  /* ---- 想要了解更多 / AI 启发提问 ---- */
  if (p === '/api/guide' && method === 'POST') {
    const body = await readJSON(request);
    const r = await buildGuide(currentIndex(sampleIndex()), body?.page ?? body?.pageNo, {
      cloud: !body?.forceLocal && hasLLM(env), env, variant: body?.variant, lang: body?.lang
    });
    return ok(r);
  }
  if (p === '/api/knowledge' && method === 'POST') {
    const body = await readJSON(request);
    const r = await buildKnowledge(currentIndex(sampleIndex()), body?.page ?? body?.pageNo, {
      cloud: !body?.forceLocal && hasLLM(env), env, variant: body?.variant
    });
    return ok(r);
  }
  if (p === '/api/co-create' && method === 'POST') {
    const body = await readJSON(request);
    const r = await buildCoCreate(currentIndex(sampleIndex()), body?.page ?? body?.pageNo, {
      task: body?.task, keyword: body?.keyword, draft: body?.draft,
      cloud: !!(body?.cloud ?? true) && hasLLM(env), env
    });
    const text = String(r?.text || '');
    if (text) {
      const safe = await checkTextSafety(text, { env, label: 'co-create' });
      if (!safe.safe) {
        return ok({ ok: true, safeBlocked: true, text: '', categories: safe.categories, reasons: safe.reasons });
      }
    }
    return ok(r);
  }

  /* ---- 整页翻译 ---- */
  if (p === '/api/translate-page' && method === 'POST') {
    const body = await readJSON(request);
    const text = String(body?.text || '').slice(0, 1200);
    if (!text.trim()) return fail('text 不能为空', 400, { ok: false });
    return ok(await translatePage(env, text, String(body?.bookTitle || ''), body?.pageNo));
  }

  /* ---- 汉字小课堂 ---- */
  if (p === '/api/hanzi' && method === 'POST') {
    const body = await readJSON(request);
    const ch = String(body?.char || '').trim();
    if ([...ch].length !== 1 || !/[\u4e00-\u9fff]/.test(ch)) return fail('需要一个汉字', 400, { ok: false });
    return ok(await hanziInfo(env, ch, String(body?.sentence || ''), String(body?.bookTitle || '')));
  }

  /* ---- 英文单词小课堂 ---- */
  if (p === '/api/word' && method === 'POST') {
    const body = await readJSON(request);
    const w = String(body?.word || '').trim();
    if (!/^[A-Za-z][A-Za-z'’-]{0,30}$/.test(w)) return fail('需要一个英文单词', 400, { ok: false });
    return ok(await wordInfo(env, w, String(body?.sentence || ''), String(body?.bookTitle || '')));
  }

  /* ---- 页面逐字框（视觉标注） ---- */
  if (p === '/api/zi-boxes' && method === 'POST') {
    const body = await readJSON(request);
    return ok(await ziBoxes(env, body));
  }

  /* ---- 本机专属能力：明确降级，不伪造 ---- */
  if (p === '/api/book/video/concat') {
    const body = method === 'POST' ? await readJSON(request) : {};
    const bookId = body?.bookId || url.searchParams.get('bookId') || getActiveId();
    const b = bookId && bookId !== 'sample' ? readBook(bookId) : null;
    if (b && b.pages && b.pages.length) {
      const idx = getState(env).indexes[bookId];
      const full = idx && idx.video && idx.video.full;
      return ok({ ok: !!full, url: full || ('/books/' + bookId + '/video/book-full.mp4'), message: full ? '' : '这本书还没有整本成片，可在本地演示环境用 Remotion 渲染。' });
    }
    return ok({ ok: true, url: '/assets/video/book-full.mp4', message: '' });
  }
  if (p.startsWith('/api/remotion/')) {
    if (p === '/api/remotion/status' && method === 'GET') {
      return ok({ ok: true, ready: false, rendering: false, queue: 0, outputs: [], message: 'Remotion 程序化渲染需 Chromium 与本地磁盘，线上版未启用。' });
    }
    if (p === '/api/remotion/outputs' && method === 'GET') {
      return ok({ ok: true, outputs: [] });
    }
    return fail('Remotion 程序化渲染需要 Chromium 与本地磁盘，线上版不可用；请在本地演示环境生成动画。', 503, { ok: false, degraded: true });
  }
  if (p.startsWith('/api/video-doubao/')) {
    if (p === '/api/video-doubao/config' && method === 'GET') {
      return ok({ ok: true, configured: false, enabled: false, message: '豆包 Seedance 图生视频需在本机演示环境运行。' });
    }
    return fail('豆包图生视频需在本机演示环境运行（线上版不落盘 25MB+ 视频）。', 503, { ok: false, degraded: true });
  }
  if (p === '/api/generate' || p === '/api/check' || p === '/api/job') {
    return fail('Remotion 渲染任务队列需在本机演示环境运行。', 503, { ok: false, degraded: true });
  }

  /* ---- 热区 ---- */
  if (p === '/api/hotspots' && method === 'GET') {
    const pageNo = Number(url.searchParams.get('page') || 0);
    const idx = currentIndex(sampleIndex()) || {};
    const hs = (idx.hotspots || []).filter(h => !pageNo || Number(h.pageNo || h.page) === pageNo);
    const pg = (idx.pages || []).find(x => Number(x.pageNo) === pageNo);
    const out = hs.length ? hs : ((pg && pg.hotspots) || []);
    return ok({ ok: true, page: pageNo || null, count: out.length, hotspots: out, source: hs.length ? 'index' : (out.length ? 'page' : 'none') });
  }

  /* ---- 读书记录 ---- */
  if (p === '/api/logs') {
    if (method === 'GET') {
      const limit = clamp(Number(url.searchParams.get('limit') || 60), 1, 200);
      const bookId = url.searchParams.get('bookId') || '';
      const page = await readLogsPage(env, limit, bookId);
      return ok({ ok: true, total: page.total, bookId, logs: page.logs, note: '线上版记录保存在 D1，可按日清理。' });
    }
    if (method === 'POST') {
      const body = await readJSON(request);
      const type = String(body?.event || body?.type || 'client').slice(0, 40);
      const payload = body && typeof body === 'object' ? body : {};
      const id = await logEventRow(env, type, payload);
      return ok({ ok: true, logged: id > 0, id });
    }
  }

  /* ---- 评测 ---- */
  if (p === '/api/eval' && method === 'GET') {
    const rows = await readLogs(env, 500, '');
    const qa = rows.filter(r => r.type === 'qa');
    const grounded = qa.filter(r => r.grounded).length;
    const judged = qa.filter(r => typeof r.correct === 'boolean');
    const correct = judged.filter(r => r.correct).length;
    return ok({
      ok: true,
      qaTotal: qa.length,
      grounded,
      retrievalHitRate: qa.length ? Math.round((grounded / qa.length) * 1000) / 1000 : 0,
      judged: judged.length,
      correct,
      accuracy: judged.length ? Math.round((correct / judged.length) * 1000) / 1000 : 0,
      targetAccuracy: 0.6,
      avgLatencyMs: qa.length ? Math.round(qa.reduce((n, r) => n + Number(r.latencyMs || 0), 0) / qa.length) : 0,
      continuity: 1,
      note: '线上版按 D1 日志统计；人工标注评测请在本机演示环境使用。'
    });
  }
  if (p === '/api/eval/judge' && method === 'POST') {
    if (String(env.PB_ALLOW_ADMIN || '0') !== '1') return fail('线上版已关闭评测标注', 503, { ok: false });
    return ok({ ok: true });
  }

  /* ---- 合规说明 ---- */
  if (p === '/api/compliance') {
    const s = getSettings();
    if (method === 'GET') {
      return ok({
        ok: true,
        officer: s.officer || '',
        officerSet: !!s.officer,
        reviewMechanism: '每本上传绘本先过本地规则+模型语义复核两道内容安全检测；生成文本（共创/引导）同样过安全过滤，命中即不输出。',
        ageLabel: '面向 3-8 岁儿童与家长共读',
        materials: ['内容安全检测记录', '问答日志', 'AI 辅助完成说明', '数据与隐私说明'],
        commitments: [
          '不使用孩子的真实姓名、照片、声音作为训练或上传云端',
          '不采集人脸、声纹等生物识别信息',
          'AI 生成内容均标注「AI 辅助」，重要信息请家长核对',
          '页面上传与音色克隆仅在本地演示环境开放'
        ],
        privacy: '线上版不保存孩子照片与人声音频；问题与朗读文本仅用于本次会话，日志只记录技术指标。',
        riskNote: '绘本内容与 AI 生成内容可能存在误差，请家长与老师陪伴核对；本服务不替代成人监护与专业意见。'
      });
    }
    if (method === 'POST') {
      const body = await readJSON(request);
      if (body?.officer !== undefined) {
        s.officer = String(body.officer || '').slice(0, 40);
        saveSettings(s);
      }
      return ok({ ok: true, officer: s.officer || '' });
    }
  }

  if (p === '/api/debug/client-error' && method === 'POST') {
    const body = await readJSON(request);
    const id = await logEvent(env, 'client-error', body || {});
    return ok({ ok: true, logged: !!id });
  }

  return fail('unknown api', 404, { path: p });
}

/* ------------------------------------------------------- 各 API 的业务实现 */

function formatMs(ms) {
  const t = Math.max(0, Math.round(Number(ms) || 0) / 1000);
  const mnt = Math.floor(t / 60), sec = Math.floor(t % 60);
  return mnt + '分' + String(sec).padStart(2, '0') + '秒';
}

function pageOf(idx, pageNo) {
  if (!idx) return null;
  const pages = Array.isArray(idx.pages) ? idx.pages : [];
  const no = Number(pageNo);
  if (Number.isFinite(no) && no > 0) return pages.find(p => Number(p.pageNo) === no) || null;
  return pages[0] || null;
}

function buildEvidence(idx, page) {
  const out = [];
  const pageNo = page ? Number(page.pageNo) : 0;
  (idx.utterances || []).forEach(u => {
    if (pageNo && Number(u.pageNo) && Number(u.pageNo) !== pageNo) return;
    const text = String(u.text || '').trim();
    if (text) out.push({ kind: 'utterance', text, pageNo: u.pageNo || pageNo, t0: u.start, t1: u.end });
  });
  const hs = page && Array.isArray(page.hotspots) && page.hotspots.length
    ? page.hotspots
    : (idx.hotspots || []).filter(h => !pageNo || Number(h.pageNo || h.page) === pageNo);
  hs.forEach(h => {
    const text = [h.label, h.qa].filter(Boolean).join('：');
    if (text) out.push({ kind: 'hotspot', text, pageNo: h.pageNo || pageNo });
  });
  if (page) {
    const base = [page.text, page.narration, page.summary].filter(Boolean).join(' ');
    if (base) out.push({ kind: 'page', text: base, pageNo });
  }
  (idx.timeline || []).forEach(t => {
    if (pageNo && Number(t.pageNo) && Number(t.pageNo) !== pageNo) return;
    const text = String(t.caption || t.text || '').trim();
    if (text) out.push({ kind: 'timeline', text, pageNo: t.pageNo || pageNo });
  });
  return out.slice(0, 80);
}

function ngrams(s, n) {
  const src = String(s || '').replace(/\s+/g, '');
  const g = new Set();
  for (let i = 0; i + n <= src.length; i++) g.add(src.slice(i, i + n));
  return g;
}

function similarity(q, text) {
  const weights = { 1: 0.6, 2: 1.4, 3: 2.2 };
  let score = 0, total = 0;
  for (const n of [1, 2, 3]) {
    const qg = ngrams(q, n), tg = ngrams(text, n);
    total += weights[n];
    if (!qg.size || !tg.size) continue;
    let hit = 0;
    for (const g of qg) if (tg.has(g)) hit++;
    score += weights[n] * (hit / qg.size);
  }
  return total ? score / total : 0;
}

const KEYWORD_BOOST = ['为什么', '怎么', '哪里', '谁', '什么', '多少', '能不能', '是不是', 'what', 'why', 'how', 'where', 'who'];
const TH = 0.32;

/**
 * 取出问题里的「实词集合」（中文 2 字以上片段 + 英文单词），去掉疑问词与虚词。
 * 用它做双向覆盖判定：问题问的那些实词，原文里到底有没有交代。
 */
function queryContentWords(q) {
  const raw = String(q || '').toLowerCase();
  const words = new Set();
  /* 英文单词 */
  raw.split(/[^a-z]+/).forEach(w => {
    if (w.length >= 3 && !STOPWORDS.includes(w)) words.add(w);
  });
  /* 中文：去掉疑问/虚词后按标点切段，段内取 2–4 字滑窗 */
  let s = raw.replace(/[a-z0-9]+/g, ' ');
  STOPWORDS.forEach(w => { if (/[\u4e00-\u9fff]/.test(w)) s = s.split(w).join(' '); });
  s.split(/[^\u4e00-\u9fff]+/).forEach(seg => {
    if (seg.length < 2) return;
    for (let n = Math.min(4, seg.length); n >= 2; n--) {
      for (let i = 0; i + n <= seg.length; i++) words.add(seg.slice(i, i + n));
    }
  });
  return [...words];
}

/**
 * 判定「原文能否回应这个问题」：问题里的实词至少要有 60% 出现在这段原文中。
 * 「番茄的学名是什么」去掉『的/是/什么』后剩『番茄』『学名』，
 * 原文只有『番茄』没有『学名』→ 覆盖率 50% < 60%，应判为无法作答。
 */
function coversQuestion(contentWords, text) {
  if (!contentWords.length) return true;
  const t = String(text || '').toLowerCase();
  let hit = 0;
  for (const w of contentWords) if (t.includes(w)) hit++;
  return hit / contentWords.length >= 0.6;
}

/** 疑问词与虚词：不参与「主体词」提取 */
const STOPWORDS = ['为什么', '怎么样', '怎么', '哪里', '哪个', '什么', '多少', '能不能', '是不是', '有没有',
  '请问', '这个', '那个', '这些', '那些', '他们', '我们', '你们', '自己', '然后', '因为', '所以',
  '的是', '了吗', '呢', '的', '了', '吗', '呀', '啊', '是', '在', '和', '有', '要', '会', '都', '就',
  'what', 'why', 'how', 'where', 'who', 'the', 'and', 'is', 'are', 'to', 'of', 'in', 'on', 'a'];

/**
 * 从问题里提取「主体词」（名词性片段）。
 * 中文按 2–4 字滑窗取，去掉含疑问词/虚词的片段，再按在问题中的位置保留前 4 个。
 * 目的：问「番茄为什么逃跑」时，应优先命中含「番茄」的原文，而不是随便一句。
 */
function querySubjects(q) {
  const s = String(q || '').toLowerCase().replace(/[\s，。？！、；：,.\?!"'（）()]/g, '');
  if (!s) return [];
  const out = [];
  const isBad = frag => STOPWORDS.some(w => frag.includes(w)) || !/[\u4e00-\u9fff]/.test(frag) && frag.length < 3;
  for (let n = 4; n >= 2; n--) {
    for (let i = 0; i + n <= s.length; i++) {
      const frag = s.slice(i, i + n);
      if (isBad(frag)) continue;
      if (out.some(k => k.includes(frag) || frag.includes(k))) continue;
      out.push(frag);
    }
  }
  /* 英文单词也作为主体词 */
  String(q || '').toLowerCase().split(/[^a-z]+/).forEach(w => {
    if (w.length >= 3 && !STOPWORDS.includes(w) && !out.includes(w)) out.push(w);
  });
  return out.slice(0, 6);
}

function retrieve(idx, query, page, lang) {
  const evidence = buildEvidence(idx, page);
  const q = String(query || '').toLowerCase();
  const subjects = querySubjects(q);
  const hasKw = KEYWORD_BOOST.some(k => q.includes(k));
  const scored = evidence.map(e => {
    const t = String(e.text).toLowerCase();
    const sim = similarity(q, t);
    let s = sim;
    if (t.includes(q) && q.length >= 2) s += 0.18;
    if (hasKw) s += 0.04;
    /* 主体词命中：只奖励「同时具备字面相似度」的候选，
       避免「番茄的学名是什么」这类问法仅因命中主体词就误判为已作答 */
    if (subjects.length && sim > 0) {
      const hit = subjects.filter(k => t.includes(k)).length;
      if (hit) s += Math.min(0.28, 0.12 * hit + 0.04 * (hit / subjects.length));
    }
    if (page && Number(e.pageNo) === Number(page.pageNo)) s += 0.08;
    return { ...e, score: Math.round(Math.min(1.8, s) * 1000) / 1000, _sim: sim };
  }).sort((a, b) => b.score - a.score);

  /* 去掉与首条完全重复的候选，避免答案里同一句出现两次 */
  const seen = new Set();
  const uniq = [];
  for (const e of scored) {
    const k = String(e.text).trim();
    if (seen.has(k)) continue;
    seen.add(k);
    uniq.push(e);
  }
  const top5 = uniq.slice(0, 5);
  const top = top5[0] || null;
  /* 判定「有据可依」需同时满足：
     ① 总分过阈值；② 与问题字面有实质重合；③ 问题里的实词在原文中有足够覆盖。
     第 ③ 条用于拦住「番茄的学名是什么」这类——原文有『番茄』但没有『学名』。 */
  const contentWords = queryContentWords(q);
  const covered = top ? coversQuestion(contentWords, top.text) : false;
  const grounded = !!top && top.score >= TH && (top._sim || 0) >= 0.12 && covered;
  return {
    evidence: top5.map(({ _sim, ...e }) => e),
    top: top ? top.score : 0,
    grounded
  };
}

function snippet(text, q) {
  const s = String(text || '');
  const key = String(q || '').slice(0, 8);
  const at = key ? s.indexOf(key) : -1;
  if (at < 0) return s.slice(0, 60);
  const from = Math.max(0, at - 18);
  return (from > 0 ? '…' : '') + s.slice(from, from + 70);
}

function composeAnswer(query, ev, lang, page, grounded) {
  if (!ev.length) return '这个问题在这本绘本里还没有对应内容，要不要换个问法？';
  const head = ev[0];
  const subjects = querySubjects(query);
  const headText = String(head.text || '');

  /* 相关度不足：不硬凑答案，如实说明并给出「本页实际有的内容」当线索 */
  if (!grounded) {
    const hitSub = subjects.find(k => headText.toLowerCase().includes(k));
    const hint = subjects.length
      ? '我只在第 ' + (head.pageNo || 0) + ' 页找到相关内容：「' + snippet(headText, query) + '」。'
      : '我找到了这一页的原文：「' + snippet(headText, query) + '」。';
    const tail = hitSub
      ? '绘本原文里没有直接解释「' + hitSub + '」的句子，要不要换个问法，或者先听听这一页的朗读？'
      : '绘本原文里没有直接对应的句子，要不要换个问法，或者先听听这一页的朗读？';
    return hint + tail;
  }

  const more = ev.slice(1, 3).map(e => snippet(e.text, query));
  let a = '在第 ' + (head.pageNo || 0) + ' 页，' + snippet(headText, query) + '。';
  if (more.length) a += '另外，' + more.join('；') + '。';
  return a;
}

async function answerQa(env, body, query) {
  const idx = currentIndex(sampleIndex());
  const t0 = Date.now();
  if (!idx) {
    return { ok: false, error: 'index.json 未就绪', degraded: true };
  }
  const pageNo = Number(body?.page || 0) || null;
  const page = pageOf(idx, pageNo);
  const lang = String(body?.lang || '').toLowerCase() || 'zh';
  const r = retrieve(idx, query, page, lang);
  const retrieval = Date.now() - t0;

  const qaId = 'qa-' + Math.random().toString(36).slice(2, 10);
  /* 书名取真实来源：页面级 > 索引 > 当前激活书的标题；仅示例书才回落到内置名 */
  const activeId = getActiveId();
  const activeBook = activeId ? readBook(activeId) : null;
  const realTitle = (pageOf(idx, 1)?.bookTitle) || idx.title
    || (activeBook ? activeBook.title : '') || (activeId ? '' : '小兔找家');
  const base = {
    ok: true, qaId,
    bookId: activeId || 'sample',
    bookSource: activeId ? 'upload' : 'sample',
    bookTitle: realTitle || '这本绘本',
    query, lang,
    evidence: r.evidence.map(e => ({ kind: e.kind, pageNo: e.pageNo, score: e.score, snippet: snippet(e.text, query) })),
    retrievalMs: retrieval
  };

  if (!hasLLM(env)) {
    const answer = composeAnswer(query, r.evidence, lang, page, r.grounded);
    await logEvent(env, 'qa', { query, grounded: r.grounded, degraded: true, latencyMs: Date.now() - t0, pageNo });
    return { ...base, answer, followUp: '你还想问问这本绘本里的哪一页呢？', confidence: r.top, grounded: r.grounded, degraded: true, mode: 'local', reason: '未配置 DeepSeek 密钥，使用原文检索回答', sources: r.evidence.map(e => snippet(e.text, query)), officer: getSettings().officer || '', latencyMs: Date.now() - t0 };
  }

  const pageText = page ? String(page.text || page.narration || '') : '';
  const bookLang = /[A-Za-z]{4,}/.test(pageText) && !/[\u4e00-\u9fff]/.test(pageText) ? 'en' : 'zh';
  const langRule = lang === 'en'
    ? '用简单英文回答（3-6 岁孩子能听懂，每句不超过 12 个单词）。'
    : '用中文回答（3-6 岁孩子能听懂，不超过 3 句）。';
  const system = '你是绘本伴读老师，只依据给定的本页证据回答小朋友的问题。'
    + '如果证据里没有答案，直接说"这本绘本里没有提到，我们一起再看看下一页好吗？"，绝不编造。'
    + '回答里不要出现"根据证据""原文说"这类词。' + (bookLang === 'en' ? ' This book is in English.' : '')
    + langRule
    + ' 只输出 JSON：{"answer":"回答","followUp":"一句引导孩子继续观察的追问"}';

  try {
    const raw = await callDeepSeek(system,
      '当前第 ' + (page?.pageNo || '?') + ' 页原文：' + pageText.slice(0, 600) + '\n\n'
      + '证据片段：\n' + r.evidence.map((e, i) => (i + 1) + '. [第' + e.pageNo + '页] ' + String(e.text).slice(0, 160)).join('\n') + '\n\n'
      + '小朋友的问题：' + query);
    const answer = String(raw?.answer || '').trim();
    if (!answer) throw new Error('模型未返回回答');
    const out = {
      ...base,
      answer,
      followUp: String(raw?.followUp || '').slice(0, 120),
      confidence: r.top,
      grounded: r.grounded,
      degraded: false,
      mode: 'deepseek',
      reason: '',
      sources: r.evidence.map(e => snippet(e.text, query)),
      officer: getSettings().officer || '',
      latencyMs: Date.now() - t0
    };
    await logEvent(env, 'qa', { query, grounded: r.grounded, degraded: false, latencyMs: out.latencyMs, pageNo });
    return out;
  } catch (e) {
    const answer = composeAnswer(query, r.evidence, lang, page, r.grounded);
    await logEvent(env, 'qa', { query, grounded: r.grounded, degraded: true, latencyMs: Date.now() - t0, pageNo, error: String(e.message).slice(0, 120) });
    return { ...base, answer, followUp: '你还想问问这本绘本里的哪一页呢？', confidence: r.top, grounded: r.grounded, degraded: true, mode: 'local', reason: '模型调用失败：' + String(e.message).slice(0, 120), sources: r.evidence.map(e => snippet(e.text, query)), officer: getSettings().officer || '', latencyMs: Date.now() - t0 };
  }
}

async function translatePage(env, text, bookTitle, pageNo) {
  const t0 = Date.now();
  if (/^[\x00-\x7F\s]*$/.test(text) && /[A-Za-z]{3,}/.test(text)) {
    return { ok: true, cached: false, text, en: text, mode: 'passthrough', degraded: false, reason: '原文已是英文', latencyMs: Date.now() - t0 };
  }
  if (!hasLLM(env)) {
    return { ok: true, cached: false, text, en: '', mode: 'local', degraded: true, reason: '未配置 DeepSeek 密钥，暂不翻译', latencyMs: Date.now() - t0 };
  }
  try {
    const raw = await callDeepSeek(
      '你是儿童绘本翻译。把中文绘本页面文字翻译成自然、适合 3-6 岁孩子的英文，保留原有的句数。只输出 JSON：{"en":"英文译文"}',
      (bookTitle ? '书名：' + bookTitle + '\n' : '') + (pageNo ? '页码：第' + pageNo + '页\n' : '') + '原文：' + text
    );
    const en = String(raw?.en || '').trim();
    if (!en) throw new Error('模型未返回译文');
    return { ok: true, cached: false, text, en, mode: 'deepseek', degraded: false, reason: '', latencyMs: Date.now() - t0 };
  } catch (e) {
    return { ok: true, cached: false, text, en: '', mode: 'local', degraded: true, reason: '翻译失败：' + String(e.message).slice(0, 120), latencyMs: Date.now() - t0 };
  }
}

const HANZI_FALLBACK = {
  人: { pinyin: 'rén', radical: '人', strokes: 2, meaning: '人就是你我他，会走路会说话的好朋友。', words: ['大人', '人们', '中国人'] },
  大: { pinyin: 'dà', radical: '大', strokes: 3, meaning: '大是很大、很大的意思。', words: ['很大', '大树', '长大'] },
  小: { pinyin: 'xiǎo', radical: '小', strokes: 3, meaning: '小是很小很小的意思。', words: ['小小', '小狗', '小鸟'] }
};

async function hanziInfo(env, ch, sentence, bookTitle) {
  const t0 = Date.now();
  const fb = HANZI_FALLBACK[ch];
  if (!hasLLM(env)) {
    if (fb) return { ok: true, cached: false, char: ch, ...fb, sentence, mode: 'local', degraded: true, reason: '未配置 DeepSeek 密钥，使用内置小字典', latencyMs: Date.now() - t0 };
    return { ok: true, cached: false, char: ch, pinyin: '', radical: '', strokes: 0, meaning: '', words: [], sentence, mode: 'local', degraded: true, reason: '未配置 DeepSeek 密钥，且该字不在内置小字典中', latencyMs: Date.now() - t0 };
  }
  try {
    const raw = await callDeepSeek(
      '你是给 3-6 岁孩子讲汉字的老师。只依据给出的汉字输出准确信息，宁缺毋滥。只输出 JSON：'
      + '{"pinyin":"带声调拼音","radical":"部首","strokes":笔画数,"meaning":"给小朋友的一句话解释（不超过25字）","words":["含该词的3个词"]}',
      (bookTitle ? '绘本：' + bookTitle + '\n' : '') + '句子：' + sentence.slice(0, 120) + '\n汉字：' + ch
    );
    return {
      ok: true, cached: false, char: ch,
      pinyin: String(raw?.pinyin || '').slice(0, 12),
      radical: String(raw?.radical || '').slice(0, 4),
      strokes: Number(raw?.strokes) || 0,
      meaning: String(raw?.meaning || '').slice(0, 60),
      words: Array.isArray(raw?.words) ? raw.words.slice(0, 4).map(x => String(x).slice(0, 8)) : [],
      sentence, mode: 'deepseek', degraded: false, reason: '', latencyMs: Date.now() - t0
    };
  } catch (e) {
    if (fb) return { ok: true, cached: false, char: ch, ...fb, sentence, mode: 'local', degraded: true, reason: '模型失败：' + String(e.message).slice(0, 80), latencyMs: Date.now() - t0 };
    return { ok: true, cached: false, char: ch, pinyin: '', radical: '', strokes: 0, meaning: '', words: [], sentence, mode: 'local', degraded: true, reason: '模型失败：' + String(e.message).slice(0, 80), latencyMs: Date.now() - t0 };
  }
}

const WORD_FALLBACK = {
  cinema: { ipa: '/ˈsɪnəmə/', pos: 'n.', translation: '电影院', meaning: '看电影的地方', meaningEn: 'a place where you watch films.', example: 'We are going to the cinema.', exampleCn: '我们要去电影院。' },
  going: { ipa: '/ˈɡəʊɪŋ/', pos: 'v.', translation: '去', meaning: '正在去的路上', meaningEn: 'moving to a place.', example: 'We are going home.', exampleCn: '我们正在回家。' },
  to: { ipa: '/tuː/', pos: 'prep.', translation: '到、向', meaning: '表示方向', meaningEn: 'showing a direction.', example: 'Go to school.', exampleCn: '去学校。' },
  the: { ipa: '/ðə/', pos: 'art.', translation: '这个、那个', meaning: '特指某样东西', meaningEn: 'used before a known thing.', example: 'The cat is here.', exampleCn: '那只猫在这里。' },
  film: { ipa: '/fɪlm/', pos: 'n.', translation: '电影', meaning: '会动的画面故事', meaningEn: 'a moving picture story.', example: 'We watch a film.', exampleCn: '我们看一部电影。' },
  movie: { ipa: '/ˈmuːvi/', pos: 'n.', translation: '电影', meaning: '会动的画面故事', meaningEn: 'a moving picture story.', example: 'The movie is fun.', exampleCn: '这部电影很有趣。' },
  story: { ipa: '/ˈstɔːri/', pos: 'n.', translation: '故事', meaning: '讲一个事情', meaningEn: 'a tale about something.', example: 'Tell me a story.', exampleCn: '给我讲个故事。' },
  pictures: { ipa: '/ˈpɪktʃəz/', pos: 'n.', translation: '图片', meaning: '画出来的画面', meaningEn: 'images you can see.', example: 'I like pictures.', exampleCn: '我喜欢图片。' },
  picture: { ipa: '/ˈpɪktʃə/', pos: 'n.', translation: '图片', meaning: '一张画', meaningEn: 'one image.', example: 'Draw a picture.', exampleCn: '画一张画。' },
  by: { ipa: '/baɪ/', pos: 'prep.', translation: '在…旁边', meaning: '靠近什么', meaningEn: 'next to something.', example: 'Sit by me.', exampleCn: '坐我旁边。' },
  and: { ipa: '/ænd/', pos: 'conj.', translation: '和', meaning: '把两样东西连起来', meaningEn: 'joins two things.', example: 'You and me.', exampleCn: '你和我。' },
  ticket: { ipa: '/ˈtɪkɪt/', pos: 'n.', translation: '票', meaning: '进电影院要用的纸', meaningEn: 'a paper to get in.', example: 'Here is my ticket.', exampleCn: '这是我的票。' },
  popcorn: { ipa: '/ˈpɒpkɔːn/', pos: 'n.', translation: '爆米花', meaning: '看电影时吃的零食', meaningEn: 'a snack for movies.', example: 'I like popcorn.', exampleCn: '我喜欢爆米花。' },
  screen: { ipa: '/skriːn/', pos: 'n.', translation: '屏幕', meaning: '放画面的大布', meaningEn: 'a big surface showing pictures.', example: 'Look at the screen.', exampleCn: '看屏幕。' },
  friends: { ipa: '/frendz/', pos: 'n.', translation: '朋友们', meaning: '一起玩的人', meaningEn: 'people you play with.', example: 'We are friends.', exampleCn: '我们是朋友。' },
  friend: { ipa: '/frend/', pos: 'n.', translation: '朋友', meaning: '一起玩的人', meaningEn: 'a person you play with.', example: 'You are my friend.', exampleCn: '你是我的朋友。' },
  happy: { ipa: '/ˈhæpi/', pos: 'adj.', translation: '开心的', meaning: '心里很快乐', meaningEn: 'feeling glad.', example: 'I am happy.', exampleCn: '我很开心。' },
  big: { ipa: '/bɪɡ/', pos: 'adj.', translation: '大的', meaning: '很大很大', meaningEn: 'large in size.', example: 'A big bear.', exampleCn: '一只大熊。' },
  little: { ipa: '/ˈlɪtl/', pos: 'adj.', translation: '小的', meaning: '小小一个', meaningEn: 'small in size.', example: 'A little rabbit.', exampleCn: '一只小兔子。' },
  green: { ipa: '/ɡriːn/', pos: 'adj.', translation: '绿色的', meaning: '像小草的颜色', meaningEn: 'the colour of grass.', example: 'A green tree.', exampleCn: '一棵绿树。' }
};

async function wordInfo(env, word, sentence, bookTitle) {
  const t0 = Date.now();
  const key = word.toLowerCase();
  const fb = WORD_FALLBACK[key];
  if (!hasLLM(env)) {
    if (fb) return { ok: true, cached: false, word, ...fb, mode: 'local', degraded: true, reason: '未配置 DeepSeek 密钥，使用内置小词典', latencyMs: Date.now() - t0 };
    return { ok: true, cached: false, word, ipa: '', pos: '', translation: '', meaning: '', meaningEn: '', example: '', exampleCn: '', mode: 'local', degraded: true, reason: '未配置 DeepSeek 密钥，且该词不在内置小词典中', latencyMs: Date.now() - t0 };
  }
  try {
    const raw = await callDeepSeek(
      '你是给 3-6 岁中国孩子讲英文单词的老师。只依据给出的单词与句子输出准确信息。只输出 JSON：'
      + '{"ipa":"音标","pos":"词性缩写","translation":"中文释义","meaning":"给小朋友的一句话中文解释（≤20字）","meaningEn":"one short simple English definition","example":"一个简单英文例句","exampleCn":"例句中文"}',
      (bookTitle ? '绘本：' + bookTitle + '\n' : '') + '句子：' + sentence.slice(0, 160) + '\n单词：' + word
    );
    return {
      ok: true, cached: false, word,
      ipa: String(raw?.ipa || '').slice(0, 30),
      pos: String(raw?.pos || '').slice(0, 10),
      translation: String(raw?.translation || '').slice(0, 20),
      meaning: String(raw?.meaning || '').slice(0, 60),
      meaningEn: String(raw?.meaningEn || '').slice(0, 80),
      example: String(raw?.example || '').slice(0, 100),
      exampleCn: String(raw?.exampleCn || '').slice(0, 60),
      mode: 'deepseek', degraded: false, reason: '', latencyMs: Date.now() - t0
    };
  } catch (e) {
    if (fb) return { ok: true, cached: false, word, ...fb, mode: 'local', degraded: true, reason: '模型失败：' + String(e.message).slice(0, 80), latencyMs: Date.now() - t0 };
    return { ok: true, cached: false, word, ipa: '', pos: '', translation: '', meaning: '', meaningEn: '', example: '', exampleCn: '', mode: 'local', degraded: true, reason: '模型失败：' + String(e.message).slice(0, 80), latencyMs: Date.now() - t0 };
  }
}

/** 页面逐字框：模型给坐标，但必须用本页原文做校验，过滤幻觉 */
async function ziBoxes(env, body) {
  const t0 = Date.now();
  const idx = currentIndex(sampleIndex());
  const pageNo = Number(body?.pageNo || body?.page || 0);
  const page = pageOf(idx, pageNo);
  const bookId = getActiveId() || 'sample';
  if (!page) return { ok: false, degraded: true, reason: '当前页不存在', lines: [] };
  if (!hasLLM(env)) {
    return { ok: false, degraded: true, reason: '未配置 DEEPSEEK_API_KEY，无法做视觉逐字框标注', bookId, pageNo, lines: [], latencyMs: Date.now() - t0 };
  }
  const pageText = String(page.text || page.narration || '');
  const pageNorm = pageText.replace(/\s+/g, '');
  if (!pageNorm) return { ok: false, degraded: true, reason: '本页没有可标注的文字', bookId, pageNo, lines: [], latencyMs: Date.now() - t0 };
  try {
    const raw = await callDeepSeek(
      '你是绘本页面文字框标注助手。依据给出的本页文字，输出每一行文字的归一化 bbox（左、上、宽、高，取值 0-1）与逐字框。'
      + '只输出 JSON：{"lines":[{"text":"原文中的一行","bbox":[l,t,w,h],"chars":[{"ch":"字","bbox":[l,t,w,h]}]}]}。'
      + 'text 必须逐字来自给定原文，不允许改动或新增；做不到就返回空的 lines。',
      '本页原文（逐行）：\n' + pageText.slice(0, 800)
    );
    const lines = (Array.isArray(raw?.lines) ? raw.lines : []).map(l => {
      const text = String(l?.text || '').replace(/\s+/g, '');
      if (!text || !pageNorm.includes(text)) return null;
      const bbox = Array.isArray(l?.bbox) && l.bbox.length === 4 ? l.bbox.map(Number) : [0, 0, 0, 0];
      const chars = (Array.isArray(l?.chars) ? l.chars : []).map(c => {
        const ch = String(c?.ch || '');
        if (!ch || !text.includes(ch)) return null;
        const bb = Array.isArray(c?.bbox) && c.bbox.length === 4 ? c.bbox.map(Number) : [0, 0, 0, 0];
        return { ch, bbox: bb };
      }).filter(Boolean);
      return { text, bbox, chars };
    }).filter(Boolean);
    return { ok: true, cached: false, bookId, pageNo, lines, engine: llmModel(env), latencyMs: Date.now() - t0 };
  } catch (e) {
    return { ok: false, degraded: true, reason: '视觉标注失败：' + String(e.message).slice(0, 120), bookId, pageNo, lines: [], latencyMs: Date.now() - t0 };
  }
}

/* ------------------------------------------------------------- fetch 入口 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    try {
      await enterRequest(env);

      const p = normPath(url);

      if (p.startsWith('/api/')) {
        return await api(request, env, url);
      }

      /* --- 页面 --- */
      if (!p.includes('.') || PAGE_ALIASES[p]) {
        const name = PAGE_ALIASES[p];
        if (name) {
          const body = buildPage(name);
          if (body) return html(body);
          return html('<h1>页面缺失</h1>', 500);
        }
      }

      /* --- 内联静态资源（CSS/JS/字体/词典/音效/图片） --- */
      const rel = p.replace(/^\/+/, '');
      if (FILES[rel]) {
        const isHtml = /\.html?$/i.test(rel);
        if (isHtml) {
          const body = buildPage(rel);
          if (body) return html(body);
        }
        const res = staticFile(rel, 86400);
        if (res) return res;
      }

      /* --- R2：绘本原图与视频（支持 Range） --- */
      const bookKey = bookStatic(p);
      if (bookKey) {
        const res = await r2Response(request, env, bookKey, 3600);
        if (res) return res;
        return new Response('not found', { status: 404 });
      }

      /* --- R2：assets/video/*.mp4（示例视频、整本成片） --- */
      if (/^\/assets\/video\/[A-Za-z0-9._/-]+\.(mp4|webm)$/.test(p)) {
        const res = await r2Response(request, env, rel, 3600);
        if (res) return res;
        return new Response('not found', { status: 404 });
      }
      if (/^\/vendor\/core\/[A-Za-z0-9._-]+\.wasm\.js$/.test(p)) {
        const res = await r2Response(request, env, rel, 86400);
        if (res) return res;
        return new Response('not found', { status: 404 });
      }

      /* --- HEAD 探测（前端部分媒体会先 HEAD） --- */
      if (request.method === 'HEAD') {
        if (bookKey) {
          const h = await r2Head(env, bookKey);
          if (h) return new Response(null, { status: 200, headers: { 'content-length': String(h.size || 0), 'content-type': mimeOf(bookKey) } });
        }
        return new Response(null, { status: 404 });
      }

      return new Response('not found', { status: 404, headers: { 'content-type': 'text/plain; charset=utf-8' } });
    } catch (e) {
      return json({ ok: false, error: '服务器内部错误：' + String(e && e.message ? e.message : e) }, 500);
    }
  }
};
