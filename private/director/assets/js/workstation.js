/* ============================================================
   导演组流程脚本工作站 · 交互逻辑
   ============================================================ */
'use strict';

const DATA_URL = 'data/flow.json';
const STORE_KEY = 'director_workstation_v1';

let D = null;          // 流程脚本数据
let S = null;          // 运行状态
let loopOn = false;
let muteOn = false;
let miniOn = false;
let bound = false;
let lockArmed = false;      // 封控「武装态」：两段确认，防止现场误触
let lockArmTimer = null;
let cmodalOkFn = null;      // 确认弹窗的回调

const $  = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pad = n => String(n).padStart(2,'0');

/* ---------------- 启动 ---------------- */
async function loadData(){
  try{
    const res = await fetch(DATA_URL);
    if(!res.ok) throw new Error('http '+res.status);
    return await res.json();
  }catch(e){
    if(window.__FLOW_DATA__) return window.__FLOW_DATA__;
    throw e;
  }
}

(async function init(){
  try{
    D = await loadData();
  }catch(e){
    document.body.innerHTML =
      '<div style="padding:44px;font-family:Microsoft YaHei,sans-serif;color:var(--ink-2)">'+
      '<h2 style="color:var(--ink-1)">数据加载失败</h2>'+
      '<p>未能读取 <code>data/flow.json</code>。请用本地服务器打开（推荐），或确保该文件与 index.html 同级存在。</p>'+
      '<p style="color:var(--ink-4)">技术提示：<code>py -m http.server 8848</code> 或双击 index.html 均可。</p></div>';
    return;
  }
  loadState();
  renderAll();
  bind();
  setInterval(tick, 1000);
})();

/* ---------------- 状态 ---------------- */
function loadState(){
  const base = JSON.parse(JSON.stringify(D.initialState));
  let saved = null;
  try{ saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); }catch(e){}
  S = Object.assign(base, saved||{});

  /* 十方改造 · 智能迁移
     旧版本默认发言顺序不含 s1（当时 B市妇联部门按「主办方不参演」处理），共 9 条。
     上面的 Object.assign 会让 saved 覆盖 base —— 存档里那 9 条会把新默认的 10 条顶掉，
     表现为「数据已改成十方，页面刷新却仍是九方」。
     只在「存档顺序恰好等于旧默认」时才升级为新默认；用户手动调过的一律尊重、不动。 */
  const defOrder = Array.isArray(D.initialState.speakerOrder) ? D.initialState.speakerOrder : [];
  if(!Array.isArray(S.speakerOrder) || !S.speakerOrder.length){
    S.speakerOrder = defOrder.slice();
  }else{
    const legacy = defOrder.filter(id => id !== 's1');           // 旧的 9 方默认
    const upgraded = defOrder.length !== legacy.length;          // 新默认确实多出 s1
    const isLegacy = upgraded && S.speakerOrder.length === legacy.length
      && legacy.every((id,i) => S.speakerOrder[i] === id);
    if(isLegacy) S.speakerOrder = defOrder.slice();
  }
  S.deadlines   = Object.assign({}, D.initialState.deadlines, (saved&&saved.deadlines)||{});
  S.submissions = Object.assign({}, JSON.parse(JSON.stringify(D.initialState.submissions||{})), (saved&&saved.submissions)||{});
  S.voiceOn     = (saved && typeof saved.voiceOn==='boolean') ? saved.voiceOn : true;
  /* 提示音：默认关闭（现场大多用不到声音），需要时在设置里手动打开 */
  S.cueOn       = (saved && typeof saved.cueOn==='boolean') ? saved.cueOn : false;
  /* P2 新增：主题偏好（'light' | 'dark'） */
  S.theme       = (saved && (saved.theme==='dark'||saved.theme==='light')) ? saved.theme : 'light';
  /* P3 新增：离线缓存开关（默认关闭，避免破坏「改完刷新生效」的开发体验） */
  S.offlineOn   = (saved && typeof saved.offlineOn==='boolean') ? saved.offlineOn : false;
  /* P3 新增：演练时钟锚点 { anchorWall, courseStartMin }
     存锚点而不是绝对偏移量 —— 刷新、换标签页、第二屏都能复算出同一个时刻。
     若课程起止时间在数据里被改过，锚点失效，退回真实时间（宁可显示真实时间，也不显示一个错的倒计时）。 */
  const okDrill = saved && saved.drill
    && typeof saved.drill.anchorWall === 'number'
    && saved.drill.courseStartMin === hm2m(D.course.startTime);
  S.drill = okDrill ? saved.drill : null;
}
function saveState(){
  try{
    localStorage.setItem(STORE_KEY, JSON.stringify({
      activeStep:S.activeStep, activeStage:S.activeStage, activeSession:S.activeSession,
      speakerOrder:S.speakerOrder, deadlines:S.deadlines, warningId:S.warningId,
      lock:S.lock, voiceOn:S.voiceOn, cueOn:S.cueOn, submissions:S.submissions, theme:S.theme,
      drill:S.drill, offlineOn:S.offlineOn
    }));
  }catch(e){}
}

/* ---------------- 时间工具 ----------------
   演练时钟：存「起演锚点」（真实墙钟毫秒）而不是绝对偏移量，
   这样刷新、换标签页、第二屏都能复算出同一个时刻。

   ⚠ 换算必须走「本地一天中的第几分钟」。不能拿 epoch 毫秒和本地时分直接相减 ——
   epoch 是 UTC 基准，那样会把时区偏移整个算进去（UTC+8 下 09:00 会显示成 17:00）。 */
function drillElapsed(){ return (S && S.drill) ? (Date.now() - S.drill.anchorWall)/60000 : 0; }
function drillRawMinutes(){ return (S && S.drill) ? (S.drill.courseStartMin + drillElapsed()) : null; }
function nowMinutes(){
  const raw = drillRawMinutes();
  if(raw !== null) return ((raw % 1440) + 1440) % 1440;
  const d = new Date();
  return d.getHours()*60 + d.getMinutes() + d.getSeconds()/60;
}
/* 演练墙钟：以真实日期为底，把时刻换成演练时刻；跨 0 点时日期跟着进退 */
function wallDate(){
  const real = new Date();
  const raw = drillRawMinutes();
  if(raw === null) return real;
  const m = nowMinutes();
  return new Date(real.getFullYear(), real.getMonth(), real.getDate() + Math.floor(raw/1440),
                  Math.floor(m/60), Math.floor(m%60), Math.floor((m%1)*60));
}
function nowClockText(){
  const d = wallDate();
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function dateText(){
  const d = wallDate();
  const w = ['日','一','二','三','四','五','六'][d.getDay()];
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} 周${w}`;
}
const stepByNo    = n => D.script.find(s=>s.no===Number(n));
const stageById   = id => D.stages.find(s=>s.id===id);
const sessionById = id => D.sessions.find(s=>s.id===id);
const speakerSessions = () => S.speakerOrder.map(id=>sessionById(id)).filter(Boolean);
const hm2m = s => { const [h,m]=String(s).split(':').map(Number); return h*60+m; };

/* ---------------- 阶段进度条 ---------------- */
function renderStageBar(){
  const bar = $('#stageBar');
  const cur = stepByNo(S.activeStep) || D.script[0];
  const curStageId = cur.stage;
  const curIdx = D.stages.findIndex(s=>s.id===curStageId);
  const st = stageById(curStageId);

  /* 阶段宽度 = 该阶段总时长（分钟），让进度条表达真实时间比例而非等宽 */
  const stageMinutes = s => D.script
    .filter(x=>(s.steps||[]).includes(x.no))
    .reduce((t,x)=>t+(Number(x.duration)||0),0);

  bar.innerHTML = D.stages.map((s,i)=>{
    const on = s.id===curStageId, done = i<curIdx;
    const mins = stageMinutes(s);
    return `<div class="sb-item ${on?'on':''} ${done?'done':''}" data-stage="${s.id}" style="flex-grow:${Math.max(mins,1)}"
      title="${esc(s.name)}｜${esc(s.desc)}｜${mins} 分钟（${esc(s.from)}—${esc(s.to)}）">
      <div class="sb-num">${done?'✓':i+1}</div>
      <div style="min-width:0;"><div class="sb-t">${esc(s.name)}${actMark(actOfStage(s.id))}</div><div class="sb-d">${esc(s.desc)}</div></div>
    </div>`;
  }).join('') + `
    <div class="sb-meta">
      <div class="k">当前阶段 · ${esc(st.name)}（${stageMinutes(st)} 分钟）</div>
      <div class="v">${esc(st.id==='prep'?'等待导演组统一开幕':st.id==='act1'?'各组撰写场景一处置方案':st.id==='act2'?'播放场景二 · 各组配合研判':'十方依次发言、质询与总结')}</div>
      <div class="v" style="font-size:11.5px;font-weight:400;color:var(--ink-3);">${esc(st.from)} — ${esc(st.to)}</div>
    </div>`;

  $$('.sb-item', bar).forEach(el=>el.onclick=()=>{
    const s = stageById(el.dataset.stage);
    S.activeStep = s.steps[0]; S.activeStage = s.id; saveState(); renderAll();
  });
}

/* ---------------- 时间轴 ---------------- */
function renderSteps(){
  const host = $('#stepList');
  host.innerHTML = D.script.map(s=>{
    const on = s.no===S.activeStep;
    const dl = S.deadlines[s.no];
    const media = D.media.filter(m=>m.step===s.no);
    const act = actOfStage(s.stage);
    const actRail = act ? `<div class="act-rail" title="${actName(act)}">${ROMAN[act]}</div>` : '';
    const staff = s.staff.map(x=>`<span class="chip">${esc(x)}</span>`).join('');
    const proc = (s.process||[]).length
      ? `<div class="step-proc">${s.process.map(p=>`<div>${esc(p)}</div>`).join('')}</div>` : '';
    const note = s.flagNote ? `<div class="step-note"><b>⚠ ${esc(s.docFlag||'待确认')}</b> — ${esc(s.flagNote)}</div>` : '';
    const docDiff = (s.docFlag && s.docName && s.docName!==s.name)
      ? `<span class="step-flag">文档原文：${esc(s.docName)}</span>` : '';
    const deadChip = dl ? `<span class="chip chip-warn">截止 ${esc(dl)}</span>` : '';
    const mediaChip = media.length ? `<span class="chip chip-b">素材 ${media.length} 段 · ${esc(media[0].durationLabel)}</span>` : '';
    return `<div class="step ${on?'on':''}" data-no="${s.no}">
      <div class="step-time">
        <div class="t">${esc(s.start)}</div>
        <div class="d">${esc(s.end)}</div>
        <div class="d" style="margin-top:4px;">${s.duration} 分钟</div>
        ${actRail}
      </div>
      <div class="step-body">
        <div class="step-top">
          <span class="step-no">${s.no}</span>
          <span class="step-tag">${esc(s.tag)}</span>
          <span class="step-name">${esc(s.name)}</span>
          ${docDiff}
        </div>
        <div class="step-task"><span class="lab">教学任务：</span>${esc(s.task)}</div>
        ${proc}
        <div class="step-foot">${staff}${deadChip}${mediaChip}</div>
        ${note}
      </div>
    </div>`;
  }).join('');

  $$('.step', host).forEach(el=>el.onclick=()=>{
    S.activeStep = Number(el.dataset.no);
    S.activeStage = (stepByNo(S.activeStep)||{}).stage;
    saveState(); renderAll();
  });
}

/* ---------------- 提交状态 ---------------- */
const subKey = (sid,act) => `${sid}__${act}`;

/* ---------------- 各组提案（组端 group.html 提交，存 Cloudflare KV） ----------------
   契约见 Temp\opencode\proposal_spec.md。
   优先读服务端；服务端不可用时自动退回本机 S.submissions（旧演示逻辑，彩排可用）。 */
const PROPOSAL_API = (()=>{ try{ return new URL('proposals', location.href).href; }catch(e){ return 'proposals'; } })();
const PROPOSAL_FIELDS = { 1:['situation','approach','measures','forecast'], 2:['judgement','approach','measures'] };
const PROPOSAL_LABELS = { situation:'形势研判', approach:'对策思路', measures:'处置措施', forecast:'效果预估', judgement:'总体研判' };
const PROPOSAL_CACHE  = 'director_proposals_cache';
let PROPOSALS = null;      // { ok, proposals:{sid:{act1,act2}}, open:{act1,act2}, offsetMs }
let proposalTimer = null;
let proposalBusy = false;

function proposalKey(){
  try{ return sessionStorage.getItem('WORKSTATION_KEY') || ''; }catch(e){ return ''; }
}
function proposalStamp(iso){
  if(!iso) return '';
  const d = new Date(iso);
  if(isNaN(d)) return '';
  return `${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function proposalOf(sid, act){
  const p = (PROPOSALS && PROPOSALS.ok && PROPOSALS.proposals) ? PROPOSALS.proposals[sid] : null;
  return p ? (p['act'+act] || null) : null;
}
function proposalText(act, fields){
  return PROPOSAL_FIELDS[act].map(k=>`【${PROPOSAL_LABELS[k]}】${((fields||{})[k]||'').trim() || '（未填）'}`).join('\n');
}
/* 打印 / Word / 归档用：带标签的 HTML 片段（Word 不认 pre-line，必须用 <br>） */
function proposalHTML(act, rec){
  if(!rec || rec.status==='none') return '<span style="color:#888;">未提交</span>';
  return PROPOSAL_FIELDS[act].map(k=>{
    const v = ((rec.fields||{})[k] || '').trim();
    return `<b>【${PROPOSAL_LABELS[k]}】</b><br>${v ? esc(v).replace(/\n/g,'<br>') : '（未填）'}`;
  }).join('<br>');
}
/* 截止锁定：到点且导演未“重开本幕”即锁定 */
function proposalLocked(act){
  const dl = (D && D.initialState && D.initialState.deadlines) ? D.initialState.deadlines[act===1?'3':'4'] : null;
  if(!dl) return false;
  const target = Date.parse(`${D.course.date}T${dl}:00`);
  if(isNaN(target)) return false;
  if(PROPOSALS && PROPOSALS.ok && PROPOSALS.open && PROPOSALS.open['act'+act]) return false;
  return Date.now() + ((PROPOSALS && PROPOSALS.offsetMs) || 0) > target;
}
async function fetchProposals(){
  if(proposalBusy) return false;
  proposalBusy = true;
  try{
    const u = new URL(PROPOSAL_API);
    const k = proposalKey(); if(k) u.searchParams.set('key', k);
    const res = await fetch(u.href, { cache:'no-store' });
    const j = await res.json().catch(()=>null);
    if(res.ok && j && j.ok){
      PROPOSALS = {
        ok:true, proposals:j.proposals || {}, open:j.open || {},
        storage: j.storage || 'kv',
        usage: j.usage || null,
        offsetMs: j.serverTime ? (Date.parse(j.serverTime) - Date.now()) : 0
      };
      noteSubmitted();
      syncSettingsState();
      /* 中继给第二屏（第二屏零后端，只读 localStorage） */
      try{
        localStorage.setItem(PROPOSAL_CACHE, JSON.stringify({
          at: Date.now(), proposals: PROPOSALS.proposals, open: PROPOSALS.open
        }));
      }catch(e){}
      return true;
    }
    PROPOSALS = { ok:false, error:(j && j.error) || ('http_'+res.status) };
  }catch(e){
    PROPOSALS = { ok:false, error:'network' };
  }finally{
    proposalBusy = false;
  }
  return false;
}
async function proposalPost(body, action){
  const u = new URL(PROPOSAL_API);
  const k = proposalKey(); if(k) u.searchParams.set('key', k);
  if(action) u.searchParams.set('action', action);
  try{
    const res = await fetch(u.href, {
      method:'POST', cache:'no-store', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body || {})
    });
    const j = await res.json().catch(()=>null);
    if(res.ok && j && j.ok) return j;
    toast(`提案服务返回 ${res.status}${j && j.error ? ' · '+j.error : ''}`);
  }catch(e){
    toast('提案服务不可达：请检查网络，或在 Cloudflare 绑定 KV 后重试');
  }
  return null;
}
async function refreshProposals(){
  await fetchProposals();
  renderProgress(); renderSeatTable(); renderSeatTabs(); renderCompare();
  renderSeatMini(); buildPrintArea(); renderProposalStatus();
}
function startProposalPolling(){
  refreshProposals();
  clearInterval(proposalTimer);
  /* KV 免费版每天只有 10 万次读；20 个 key × 每 60 秒一次 ≈ 0.42 万读/场。
     ⚠ 控制台整天开着（哪怕 30~60 秒一次）也会把 10 万读慢慢烧完 —— 迁 D1 后无此问题。
     需要立刻看最新状态时，点「刷新提案」或切回本页即可。 */
  proposalTimer = setInterval(()=>{ if(document.visibilityState === 'visible') refreshProposals(); }, 60000);
  document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) refreshProposals(); });
  window.addEventListener('focus', ()=>refreshProposals());
  window.addEventListener('storage', e=>{
    if(e.key === PROPOSAL_CACHE) refreshProposals();     /* 第二屏 / 多标签页互相拉齐 */
  });
}
/* 有组从「草稿」变成「已提交」时响一声（提示音开关打开才响）。
   首次拉取只记基线，不补响历史上已提交的组。 */
let submittedSeen = null;
function noteSubmitted(){
  const map = {};
  const ps = (PROPOSALS && PROPOSALS.proposals) || {};
  for(const g of Object.keys(ps)){
    const acts = ps[g] || {};
    map[g] = {};
    for(const a of [1,2]){
      const p = acts['act' + a];
      map[g][a] = !!(p && p.submitted);
    }
  }
  if(submittedSeen){
    const newly = [];
    for(const g of Object.keys(map)){
      for(const a of [1,2]){
        if(map[g][a] && !(submittedSeen[g] && submittedSeen[g][a])) newly.push(`${g} · 第${a}幕`);
      }
    }
    if(newly.length){
      playCue('submit');
      toast('新提交：' + newly.join('、'));
    }
  }
  submittedSeen = map;
}

function propStateText(){
  return (PROPOSALS && PROPOSALS.ok)
    ? '已接入 · 各组在组端页面提交，控制台每 60 秒同步'
    : '未接入：需在 Cloudflare 绑定 D1（DB）或 KV（PROPOSALS）';
}
function quotaStateText(){
  const base = (PROPOSALS && PROPOSALS.ok)
    ? (PROPOSALS.storage === 'd1'
        ? 'D1 模式（免费：读 500 万行/日、写 10 万行/日；北京 08:00 重置）'
        : 'KV 模式（免费：读 10 万/日、写 1,000/日）—— 绑定 D1 后自动切换')
    : '未接入提案服务';
  return base + '。本页每 60 秒刷新一次、组端输入停 30 秒保存一次';
}
/* 设置面板打开后，提案状态可能在后续轮询里才就绪：这里保持文案同步 */
function syncSettingsState(){
  const a = $('#setPropState'), b = $('#setQuotaState');
  if(a) a.textContent = propStateText();
  if(b) b.textContent = quotaStateText();
}

function renderProposalStatus(){
  const el = $('#seatTag');
  if(!el) return;
  const ok = !!(PROPOSALS && PROPOSALS.ok);
  el.textContent = `${S.speakerOrder.length} 方参演 · ${ok ? '提案实时同步' : '提案未接入'}`;
  el.title = ok
    ? '各组在 group.html 填写，控制台每 60 秒自动刷新'
    : '未检测到提案服务（需在 Cloudflare 绑定 D1/KV）；当前仅本机演示数据可用';
  renderQuotaTag();
}

/* 今日额度提示：D1 模式显示实际读/写行数与剩余额度；KV 模式只提示模式 */
function renderQuotaTag(){
  const el = $('#quotaTag');
  if(!el) return;
  const ok = !!(PROPOSALS && PROPOSALS.ok);
  const storage = PROPOSALS && PROPOSALS.storage;
  const u = PROPOSALS && PROPOSALS.usage;

  if(!ok){
    const err = (PROPOSALS && PROPOSALS.error) || '';
    el.textContent = err ? '额度：读取失败' : '额度 —';
    el.title = err
      ? `读取提案数据失败：${err}\n如果是 KV 免费额度（读 10 万/日）用完：等次日北京时间 08:00 重置，或绑定 D1（额度独立且大得多）`
      : '提案服务未接入，暂无额度数据';
    el.classList.toggle('warn', !!err);
    return;
  }
  if(storage === 'd1' && u && u.limits){
    const fmt = n => n >= 10000 ? (n / 10000).toFixed(n >= 100000 ? 0 : 1) + ' 万' : String(n);
    const readPct = u.reads / u.limits.reads;
    const writePct = u.writes / u.limits.writes;
    el.textContent = `今日 读 ${fmt(u.reads)} · 写 ${fmt(u.writes)}`;
    el.title =
      `存储：D1（免费额度按日，北京时间 08:00 重置）\n` +
      `今日（UTC ${u.day}）：请求 ${u.req} 次、读 ${u.reads} 行、写 ${u.writes} 行\n` +
      `剩余：读 ${(u.limits.reads - u.reads).toLocaleString('zh-CN')} 行、写 ${(u.limits.writes - u.writes).toLocaleString('zh-CN')} 行\n` +
      `说明：这是本站自己的计数，不含账号内其它项目`;
    el.classList.toggle('warn', Math.max(readPct, writePct) > 0.8);
    return;
  }
  el.textContent = storage === 'kv' ? '额度：KV 模式' : '额度 —';
  el.title = storage === 'kv'
    ? '当前走 KV（免费 读 10 万/日、写 1,000/日）。绑定 D1 后这里会显示今日读/写行数与剩余额度'
    : '暂无额度数据';
  el.classList.remove('warn');
}

function subOf(sid, act){
  const p = proposalOf(sid, act);
  if(p){
    const fields = p.fields || {};
    return {
      status: p.submitted ? 'done' : 'draft',
      savedAt: proposalStamp(p.updatedAt),
      submittedAt: p.submitted ? proposalStamp(p.updatedAt) : '',
      text: proposalText(act, fields),
      fields, fromServer: true
    };
  }
  return S.submissions[subKey(sid,act)] || {status:'none', savedAt:'', submittedAt:'', text:'', fields:{}};
}
function setSub(sid, act, patch){
  const k = subKey(sid,act);
  S.submissions[k] = Object.assign({status:'none',savedAt:'',submittedAt:'',text:''}, S.submissions[k], patch);
  saveState();
}

/* ---------------- P2 幕次罗马数字系统 ---------------- */
const ROMAN = {1:'Ⅰ', 2:'Ⅱ'};
function actOfStage(id){ return id==='act1' ? 1 : id==='act2' ? 2 : 0; }
function actOfStep(no){ const s = stepByNo(no); return s ? actOfStage(s.stage) : 0; }
function actRoman(a){ return a ? ROMAN[a] : ''; }
function actName(a){ return a ? `第${a===1?'一':'二'}幕` : ''; }
/* 普通型：直接内联；boxed：带框，用于表头/标题 */
function actMark(a, boxed){
  if(!a) return '';
  return `<b class="act-roman${boxed?' boxed':''}">${ROMAN[a]}</b>`;
}

/* ---------------- 提交完成度矩阵（P1 新增） ---------------- */
function renderProgress(){
  const host = $('#progressStrip');
  if(!host) return;
  const seats = speakerSessions();                 // 10 方参演
  if(!seats.length){ host.innerHTML=''; return; }

  let done = 0;
  const total = seats.length * 2;                  // 10 × 2 幕 = 20

  const cellsOf = act => seats.map(s=>{
    const r = subOf(s.id, act);
    if(r.status==='done') done++;
    const cls = r.status==='done' ? 'done' : r.status==='draft' ? 'draft' : 'none';
    const st  = {done:'已正式提交',draft:'草稿已保存',none:'未提交'}[r.status];
    const gl  = {done:'✓',draft:'◐',none:'·'}[r.status];
    const when = r.submittedAt || r.savedAt;
    return `<button class="ps-cell ${cls}" data-ps="${s.id}|${act}"
      title="${esc(s.code)} ${esc(s.name)} · 第${act===1?'一':'二'}幕 · ${st}${when?'（'+esc(when)+'）':''}"
      aria-label="${esc(s.name)} 第${act===1?'一':'二'}幕，${st}">${gl}</button>`;
  }).join('');

  /* ⚠ 必须先生成两行格子再算汇总：cellsOf() 会顺带累加 done，
     而下面的环形图百分比、stroke-dashoffset 和「x / total」计数都要读 done。
     顺序颠倒会让格子标对了、汇总却永远停在 0（旧 bug，已修）。 */
  const row1 = cellsOf(1), row2 = cellsOf(2);

  const pct = total ? Math.round(done/total*100) : 0;
  const C = 163.36;                                 // 2π × r(26)

  host.innerHTML = `
    <div class="ps-ring">
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <circle class="t" cx="32" cy="32" r="26"></circle>
        <circle class="f" cx="32" cy="32" r="26" style="stroke-dashoffset:${(C*(1-done/total)).toFixed(2)}"></circle>
      </svg>
      <div class="v">${pct}%</div>
    </div>
    <div class="ps-meta">
      <div class="k">提交完成度</div>
      <div class="v">${done}<small> / ${total}</small></div>
      <div class="d">${seats.length} 方参演 × 2 幕</div>
    </div>
    <div class="ps-matrix">
      <div class="ps-rl"></div>
      <div class="ps-cells ps-hd">${seats.map(s=>`<span title="${esc(s.name)}">${esc(s.code)}</span>`).join('')}</div>
      <div class="ps-rl">${actMark(1)} 第一幕</div>
      <div class="ps-cells">${row1}</div>
      <div class="ps-rl">${actMark(2)} 第二幕</div>
      <div class="ps-cells">${row2}</div>
    </div>
    <div class="ps-legend">
      <span><i class="l-done"></i>已提交 ${seats.filter(s=>subOf(s.id,1).status==='done'||subOf(s.id,2).status==='done').length} 方</span>
      <span><i class="l-draft"></i>有草稿</span>
      <span><i class="l-none"></i>未提交</span>
    </div>`;

  $$('[data-ps]', host).forEach(b=>b.onclick = ()=>{
    const [sid, act] = b.dataset.ps.split('|');
    S.activeSession = sid; saveState();
    renderSeatTable(); renderSeatTabs(); renderCompare(); renderSeatMini();
    const s = sessionById(sid);
    const r = subOf(sid, Number(act));
    toast(`${s.name} · 第${act==='1'?'一':'二'}幕 — ${({done:'已正式提交',draft:'草稿已保存',none:'未提交'})[r.status]}`);
  });
}

/* ---------------- 席位表 ---------------- */
function renderSeatTable(){
  const tb = $('#seatTbody');
  const orderMap = {};
  speakerSessions().forEach((s,i)=>orderMap[s.id]=i+1);

  tb.innerHTML = D.sessions.map(s=>{
    const a1 = subOf(s.id,1), a2 = subOf(s.id,2);
    const st = (a1.status==='done' && a2.status==='done') ? 'done'
             : (a1.status==='draft' || a2.status==='draft') ? 'draft' : 'none';
    const stTxt = {done:'已完成',draft:'草稿已保存',none:'未提交'}[st];
    const stCls = {done:'chip-done',draft:'chip-d',none:'chip-n'}[st];
    const on = s.id===S.activeSession;
    const ord = orderMap[s.id] ? `<span class="order-badge" title="集中研讨发言顺序">${orderMap[s.id]}</span>`
                               : `<span class="chip chip-n" title="不在本轮集中研讨发言顺序内">轮空</span>`;
    const cell = r => {
      if(r.status==='done') return `<span class="chip chip-ok"><span class="st">正式提交</span></span><span class="ts">提交：${esc(r.submittedAt||'--')}</span>`;
      if(r.status==='draft') return `<span class="chip chip-d"><span class="st">草稿已保存</span></span><span class="ts">保存：${esc(r.savedAt||'--')}</span>`;
      return `<span class="chip chip-n"><span class="st">未提交</span></span>`;
    };
    return `<tr class="${on?'on':''}" data-sid="${s.id}">
      <td><div class="seat-cell" data-archive="${s.id}">
        ${ord}<span class="seat-code">${esc(s.code)}</span>
        <span class="seat-name">${esc(s.name)}${s.role==='主办方'?'<span class="host">主办方</span>':''}</span>
      </div></td>
      <td class="col-act"><div class="sub-st">${cell(a1)}</div></td>
      <td class="col-act"><div class="sub-st">${cell(a2)}</div></td>
      <td><span class="chip ${stCls}">${stTxt}</span>${S.lock?'<br><span class="lock-badge">已封控</span>':''}</td>
      <td><div style="display:flex;gap:6px;">
        <button class="btn btn-o btn-sm" data-archive2="${s.id}">档案</button>
        <button class="btn btn-g btn-sm" data-pick="${s.id}" title="在集中研讨区展示该组">选中</button>
      </div></td>
    </tr>`;
  }).join('');

  $$('[data-archive]', tb).forEach(el=>el.onclick = e=>{ e.stopPropagation(); openArchive(el.dataset.archive); });
  $$('[data-archive2]', tb).forEach(el=>el.onclick = e=>{ e.stopPropagation(); openArchive(el.dataset.archive2); });
  $$('[data-pick]', tb).forEach(el=>el.onclick = e=>{
    e.stopPropagation();
    S.activeSession = el.dataset.pick; saveState();
    renderSeatTable(); renderSeatTabs(); renderCompare(); renderSeatMini();
    toast(`${sessionById(S.activeSession).name} 已推送至集中研讨区`);
  });
  $$('tr', tb).forEach(tr=>tr.onclick = ()=>{
    S.activeSession = tr.dataset.sid; saveState();
    renderSeatTable(); renderSeatTabs(); renderCompare(); renderSeatMini();
  });
}

/* ---------------- 集中研讨对比 ---------------- */
function renderSeatTabs(){
  const host = $('#seatTabs');
  host.innerHTML = D.sessions.map(s=>{
    const n = (subOf(s.id,1).status==='done'?1:0) + (subOf(s.id,2).status==='done'?1:0);
    return `<button class="stab ${s.id===S.activeSession?'on':''}" data-sid="${s.id}">${esc(s.name)}${n?` <span style="opacity:.75;font-size:11px;">${n}/2</span>`:''}</button>`;
  }).join('');
  $$('.stab', host).forEach(el=>el.onclick=()=>{
    S.activeSession=el.dataset.sid; saveState();
    renderSeatTabs(); renderCompare(); renderSeatTable(); renderSeatMini();
  });
}

function renderCompare(){
  const s = sessionById(S.activeSession);
  if(!s) return;
  $$('#seatTabs .stab').forEach(el=>el.classList.toggle('on', el.dataset.sid===s.id));
  $('#cmp1Title').innerHTML = `${esc(s.name)} · ${actMark(1, true)} 第一幕`;
  $('#cmp2Title').innerHTML = `${esc(s.name)} · ${actMark(2, true)} 第二幕`;
  const a1 = subOf(s.id,1), a2 = subOf(s.id,2);
  const meta = r => {
    if(r.fromServer) return r.status==='done'
      ? `已提交 · ${r.submittedAt}`
      : `草稿 · ${r.savedAt}（组端尚未点“提交本幕”）`;
    return r.submittedAt ? `正式提交 · ${r.submittedAt}` : '尚未正式提交';
  };
  $('#cmp1Meta').textContent = meta(a1);
  $('#cmp2Meta').textContent = meta(a2);

  const body = (rec, act, actName) => {
    if(rec.fromServer && rec.status !== 'none')
      return (rec.status === 'done' ? '' : '<div class="cmp-meta" style="margin-bottom:6px;">草稿（组端尚未点“提交本幕”）</div>')
        + `<div class="cmp-text">${proposalHTML(act, rec)}</div>`;
    if(rec.status==='done') return `<div class="cmp-text">${esc(rec.text||'（正式提交内容待技术平台回填）')}</div>`;
    if(rec.status==='draft') return `<div class="cmp-empty"><b>本组已保存草稿</b>尚未正式提交，按规则不展示草稿内容。<br>导演组可在技术平台查看，或代为提交。</div>`;
    return `<div class="cmp-empty"><b>尚未正式提交</b>该组${actName}方案未提交，<br>正式提交后将在此双栏对比展示。</div>`;
  };
  $('#cmp1Body').innerHTML = body(a1, 1, '场景一');
  $('#cmp2Body').innerHTML = body(a2, 2, '场景二');
}

/* ---------------- 席位档案侧栏 ---------------- */
function renderSeatMini(){
  const host = $('#seatMini');
  host.innerHTML = D.sessions.map(s=>{
    const n = (subOf(s.id,1).status==='done'?1:0) + (subOf(s.id,2).status==='done'?1:0);
    return `<div class="sm-item ${s.id===S.activeSession?'on':''}" data-sid="${s.id}">
      <span class="sc">${esc(s.code)}</span>
      <span class="sn">${esc(s.name)}</span>
      <span class="sp2">${n}/2</span>
    </div>`;
  }).join('');
  $$('.sm-item', host).forEach(el=>el.onclick=()=>{
    S.activeSession=el.dataset.sid; saveState();
    renderSeatMini(); renderSeatTabs(); renderCompare(); renderSeatTable();
  });
}

/* ---------------- 席位档案弹窗 ---------------- */
let modalSid = null;
function openArchive(sid){
  const s = sessionById(sid);
  if(!s) return;
  modalSid = sid;
  const a1=subOf(sid,1), a2=subOf(sid,2);
  const idx = speakerSessions().findIndex(x=>x.id===sid);
  const rows = [
    ['席位编号', s.code],
    ['事件处置方', s.name],
    ['席位性质', s.role + (s.role==='参演席位' ? ` · 第 ${idx+1} 位` : ' · 不参演')],
    ['协调员', s.coordinator || '（未指定）'],
    ['对接学院 / 单位', s.unit || '（未指定）'],
    ['驻组专家', s.expert || '（未指定）'],
  ];
  const stChip = r => {
    if(r.status==='done') return `<span class="chip chip-ok">正式提交</span> <span class="cmp-meta">${esc(r.submittedAt||'')}</span>`;
    if(r.status==='draft') return `<span class="chip chip-d">草稿已保存</span> <span class="cmp-meta">${esc(r.savedAt||'')}</span>`;
    return `<span class="chip chip-n">未提交</span>`;
  };
  $('#modalTitle').textContent = `${s.code} · ${s.name}`;
  $('#modalBody').innerHTML = `
    ${rows.map(r=>`<div class="info-row"><div class="k">${esc(r[0])}</div><div class="v">${esc(r[1])}</div></div>`).join('')}
    <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--line);">
      <div style="font-size:12.5px;font-weight:700;margin-bottom:8px;">提交状态</div>
      <div style="display:flex;gap:20px;font-size:12.5px;flex-wrap:wrap;">
        <div>第一幕：${stChip(a1)}</div>
        <div>第二幕：${stChip(a2)}</div>
      </div>
      <div style="margin-top:12px;">
        ${PROPOSALS && PROPOSALS.ok
          ? `<div style="font-size:var(--fs-2xs);color:var(--ink-4);margin-bottom:10px;">数据来自各组在组端页面的实时提交（每 5 秒同步）</div>
             <div style="font-size:12.5px;font-weight:700;margin-bottom:6px;">${actMark(1,true)} 第一幕方案</div>
             <div class="cmp-text" style="min-height:0;">${a1.status==='none' ? '未提交' : proposalHTML(1,a1)}</div>
             <div style="font-size:12.5px;font-weight:700;margin:12px 0 6px;">${actMark(2,true)} 第二幕方案</div>
             <div class="cmp-text" style="min-height:0;">${a2.status==='none' ? '未提交' : proposalHTML(2,a2)}</div>`
          : `<div style="display:flex;gap:8px;flex-wrap:wrap;">
               <button class="btn btn-o btn-sm" data-sim="${sid}|1|done">模拟第一幕已提交</button>
               <button class="btn btn-g btn-sm" data-sim="${sid}|1|draft">存为草稿</button>
               <button class="btn btn-o btn-sm" data-sim="${sid}|2|done">模拟第二幕已提交</button>
               <button class="btn btn-g btn-sm" data-sim="${sid}|2|draft">存为草稿</button>
               <button class="btn btn-g btn-sm" data-sim="${sid}|0|reset">重置该席位</button>
             </div>
             <div style="margin-top:8px;font-size:var(--fs-2xs);color:var(--ink-4);">「模拟」用于现场彩排与流程验证；接入提案服务后，此处显示各组真实提交。</div>`}
      </div>
    </div>
    <div style="margin-top:14px;padding:10px 12px;background:var(--blue-50);border:1px solid var(--blue-100);border-radius:var(--r-m);font-size:12px;color:var(--ink-2);line-height:1.85;">
      <b style="color:var(--blue-900);">席位档案说明</b><br>
      协调员负责本方与导演组的日常联络；对接学院 / 单位为本方挂靠支援单位；驻组专家现场陪伴并参与点评。
      ${s.role==='主办方'?'<br><b>主办方不参与集中研讨发言</b>，仅负责组织与统筹。':''}
    </div>`;

  $$('[data-sim]', $('#modalBody')).forEach(b=>b.onclick=()=>{
    const [sid2, actS, kind] = b.dataset.sim.split('|');
    const act = Number(actS);
    if(kind==='reset'){ setSub(sid2,1,{status:'none'}); setSub(sid2,2,{status:'none'}); }
    else{
      const stamp = `${D.course.deadlinePrefix} ${nowClockText().slice(0,5)}`;
      setSub(sid2, act, kind==='done'
        ? {status:'done', submittedAt:stamp, savedAt:stamp}
        : {status:'draft', savedAt:stamp});
    }
    renderAll(); openArchive(sid2);
    toast('席位状态已更新');
  });

  $('#mask').classList.add('show');
}
function closeModal(){ $('#mask').classList.remove('show'); modalSid=null; }

/* ---------------- 视频 ---------------- */
function renderMediaTabs(){
  const host = $('#mediaTabs');
  if(!D.media.length){ host.innerHTML=''; return; }
  const cur = stepByNo(S.activeStep);
  host.innerHTML = D.media.map(m=>{
    const on = cur && m.step===cur.no;
    return `<button class="mtab ${on?'on':''}" data-mid="${m.id}">
      <span class="mn">环节 ${m.step}</span>
      <span><span class="mt">${esc(m.title)}</span><br><span class="md">${esc(m.durationLabel)} · ${esc(m.resolution)}</span></span>
    </button>`;
  }).join('');
  $$('.mtab', host).forEach(el=>el.onclick=()=>{
    const m = D.media.find(x=>x.id===el.dataset.mid);
    S.activeStep = m.step; S.activeStage = (stepByNo(m.step)||{}).stage;
    saveState(); renderAll(); loadMedia(m.id, true);
  });
}

function loadMedia(mid, autoplay){
  const m = D.media.find(x=>x.id===mid);
  if(!m) return;
  currentMediaId = mid;
  const v = $('#video');
  v.src = m.src; v.poster = m.poster; v.loop = loopOn; v.muted = muteOn;
  $('#vpBadge').textContent = `环节 ${m.step} · ${m.title}`;
  $('#vpNote').textContent = m.note;
  if(autoplay) v.play().catch(()=>{});
  const mv = $('#miniVideo');
  mv.src = m.src; mv.poster = m.poster; mv.muted = true; mv.loop = loopOn;
  if(miniOn) mv.play().catch(()=>{});
  updateVpTime();
}
function mediaOfCurrentStep(){
  const cur = stepByNo(S.activeStep);
  return D.media.find(m=>m.step===cur.no) || null;
}
function updateVpTime(){
  const v = $('#video');
  const f = t => `${pad(Math.floor((t||0)/60))}:${pad(Math.floor((t||0)%60))}`;
  $('#vpTime').textContent = `${f(v.currentTime)} / ${f(v.duration)}`;
  /* P1 新增：同步进度滑块 */
  const sk = $('#vpSeek');
  if(sk){
    const ok = v.duration && isFinite(v.duration);
    sk.disabled = !ok;
    const pct = ok ? (v.currentTime / v.duration) * 100 : 0;
    sk.value = Math.round(pct * 10);
    sk.style.setProperty('--p', pct.toFixed(2) + '%');
  }
}

/* ---------------- 导播栏 ---------------- */
function renderSide(){
  const s = stepByNo(S.activeStep) || D.script[0];
  $('#nowNo').textContent = `环节 ${s.no} · ${s.tag}`;
  $('#nowName').textContent = s.name;
  $('#nowMeta').textContent = `${s.start} - ${s.end}　共 ${s.duration} 分钟`;
  $('#sideStage').textContent = (stageById(s.stage)||{}).name || '';

  const sel = $('#selStage');
  if(sel.options.length !== D.stages.length)
    sel.innerHTML = D.stages.map(st=>`<option value="${st.id}">${esc(st.name)} · ${esc(st.desc)}</option>`).join('');
  sel.value = s.stage;

  const dsel = $('#selDeadlineStep');
  if(dsel.options.length !== D.script.length)
    dsel.innerHTML = D.script.map(x=>`<option value="${x.no}">环节 ${x.no} · ${esc(x.name)}</option>`).join('');
  dsel.value = S.activeStep;
  const dl = S.deadlines[S.activeStep];
  $('#inpDeadline').value = dl ? `${D.course.date}T${dl}` : '';

  const wsel = $('#selWarn');
  if(wsel.options.length !== D.warnings.length)
    wsel.innerHTML = D.warnings.map(x=>`<option value="${x.id}">${esc(x.label)}</option>`).join('');
  /* 历史 localStorage 里可能存着已被删除的警告 id（如旧的「国外疫情输入」），回退到不设置 */
  if(!D.warnings.some(x=>x.id===S.warningId)){ S.warningId = D.warnings[0].id; saveState(); }
  wsel.value = S.warningId;
  const w = D.warnings.find(x=>x.id===S.warningId) || D.warnings[0];
  const wb = $('#vpWarn');
  if(w && w.id!=='w0'){ wb.style.display='block'; wb.textContent = w.text; }
  else wb.style.display='none';

  $('#btnLock').disabled = S.lock;
  $('#btnUnlock').disabled = !S.lock;
  if(S.lock) disarmLock();
  else if(!lockArmed) $('#btnLock').textContent = '开启全体封控';
  syncVoiceBtn();
}

function renderOrder(){
  const host = $('#orderList');
  host.innerHTML = S.speakerOrder.map((id,i)=>{
    const s = sessionById(id);
    return `<div class="ol-item">
      <span class="ob">${i+1}</span>
      <span class="nm">${esc(s.name)}<span style="color:var(--ink-4);font-size:var(--fs-2xs);"> · ${esc(s.code)}</span></span>
      <span class="mv"><button data-up="${i}" title="上移">▲</button><button data-dn="${i}" title="下移">▼</button></span>
    </div>`;
  }).join('');
  $$('[data-up]', host).forEach(b=>b.onclick=()=>moveOrder(Number(b.dataset.up),-1));
  $$('[data-dn]', host).forEach(b=>b.onclick=()=>moveOrder(Number(b.dataset.dn),1));
  $('#orderBadge').textContent = `${S.speakerOrder.length} 方`;
}
function moveOrder(i, dir){
  const j = i+dir;
  if(j<0 || j>=S.speakerOrder.length) return;
  const arr = S.speakerOrder;
  [arr[i],arr[j]] = [arr[j],arr[i]];
  saveState(); renderOrder(); renderSeatTable();
}

/* ---------------- 每秒 tick ---------------- */
const RING_C = 326.726;   /* 2π × r(52)，倒计时仪表盘周长 */

function tick(){
  $('#clockNow').textContent = nowClockText();
  $('#clockDate').textContent = dateText();
  announceDeadlineCues();
  updateRuler();          /* P2：刻度尺播放头每秒同步 */

  const s = stepByNo(S.activeStep) || D.script[0];
  const end = hm2m(s.end);
  const now = nowMinutes();
  let left = end - now;
  const over = now > end;
  if(over){
    const next = stepByNo(s.no+1);
    if(next) left = hm2m(next.end) - now;
  }
  const leftMin = Math.max(left, 0);
  /* P1 修复（显示层）：原代码 mm=floor(左)/60、ss=floor(左%60) 把「分钟」当「秒」算，
     导致 77 分钟显示成 01:16、7 分钟显示成 00:07 且每分钟才跳一次（看似卡死）。
     此处统一先换算成总秒数，再按 真分:真秒 显示。 */
  const totalSec = Math.round(leftMin * 60);
  /* P1 修复（分级）：原代码把「分钟」直接和 60 / 300 比，任何环节都判为 danger（全程红）。 */
  const leftSec = totalSec;
  const mm = Math.floor(totalSec / 60), ss = totalSec % 60;
  const el = $('#cdNum');
  const cdBox = $('#countdown');
  const inCourse = now >= hm2m(D.course.startTime) && now <= hm2m(D.course.endTime);

  /* 仪表盘：环形进度 = 本环节剩余比例 */
  const stepTotal = Math.max(hm2m(s.end) - hm2m(s.start), 0.0001);
  const frac = inCourse ? Math.min(leftMin/stepTotal, 1) : 1;
  $('#cdFill').style.strokeDashoffset = (RING_C * (1 - frac)).toFixed(1);

  if(!inCourse){
    // 非课程时段（彩排/待开课）：显示本环节全长，不以真实钟点判定超时
    el.textContent = `${pad(s.duration)}:${pad(0)}`;
    el.className = 'num';
    cdBox.className = 'countdown';
    $('#cdSub').textContent = `本环节全长 ${s.duration} 分钟　${s.start}—${s.end}　· 点击「从 09:00 起演」进入演练`;
    updateVpTime();
    return;
  }

  const lvl = leftSec<=60 ? ' danger' : leftSec<=300 ? ' warn' : '';
  el.textContent = `${pad(mm)}:${pad(ss)}`;
  el.className = 'num' + lvl;
  cdBox.className = 'countdown' + lvl;
  $('#cdSub').textContent = over && stepByNo(s.no+1)
    ? `本环节已超时 ${pad(Math.floor((now-end)/60))} 分钟`
    : `${s.start} — ${s.end}　总时长 ${s.duration} 分钟`;

  announceCountdown(s, leftSec);
  updateVpTime();
}

/* P1 新增：最后 60 秒语音播报（浏览器原生 speechSynthesis，零依赖） */
let lastSpokenKey = null;
function speak(text){
  if(!('speechSynthesis' in window)) return;
  try{
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'zh-CN'; u.rate = 1.05; u.pitch = 1;
    speechSynthesis.speak(u);
  }catch(e){}
}
function announceCountdown(s, leftSec){
  if(!S.voiceOn || leftSec > 60) return;
  const key = s.no + ':' + leftSec;
  if(key === lastSpokenKey) return;
  lastSpokenKey = key;
  if(leftSec <= 0)      speak(`环节 ${s.no} 结束，进入下一环节`);
  else if(leftSec % 10 === 0) speak(`还剩 ${leftSec} 秒`);
}

/* ---------------- 提示音（默认关闭，设置里手动打开） ----------------
   用途：① 有组提交提案时「叮」一声；② 本幕截止前 5 分钟、1 分钟各提醒一次。
   用 WebAudio 合成，不加载任何音频文件，断网也能响。
   浏览器要求先有用户手势才能出声 —— 打开开关/点「试听」就是手势，之后即可正常响。 */
let cueCtx = null;
function cueContext(){
  const AC = window.AudioContext || window.webkitAudioContext;
  if(!AC) return null;
  if(!cueCtx) cueCtx = new AC();
  if(cueCtx.state === 'suspended') cueCtx.resume().catch(()=>{});
  return cueCtx;
}
function playCue(type){
  if(type !== 'test' && !S.cueOn) return;
  const ctx = cueContext();
  if(!ctx) return;
  const seqs = {
    submit: [[880, 0, 0.09], [1320, 0.12, 0.10]],
    five:   [[660, 0, 0.14], [880, 0.18, 0.14], [1046, 0.36, 0.22]],
    one:    [[1046, 0, 0.11], [1046, 0.17, 0.11], [1046, 0.34, 0.20]],
    test:   [[880, 0, 0.09], [1320, 0.12, 0.10]],
  };
  const seq = seqs[type] || seqs.test;
  const t0 = ctx.currentTime + 0.02;
  for(const [freq, off, dur] of seq){
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t0 + off);
    g.gain.exponentialRampToValueAtTime(0.22, t0 + off + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + off + dur);
    osc.connect(g);
    g.connect(ctx.destination);
    osc.start(t0 + off);
    osc.stop(t0 + off + dur + 0.03);
  }
}

/* 提案截止提醒：第一幕/第二幕截止前 5 分钟、1 分钟各响一次
   （用 S.deadlines 里导演可改的「关键截止」，不是环节结束时间） */
let cueDlBucket = {};   // act -> 0(>5min) | 5(<=5min) | 1(<=1min)
function announceDeadlineCues(){
  if(!S.cueOn){ cueDlBucket = {}; return; }
  for(const act of [1, 2]){
    const hm = S.deadlines[act === 1 ? '3' : '4'];
    if(!hm) continue;
    const t = /^(\d{1,2}):(\d{2})$/.exec(String(hm).trim());
    if(!t) continue;
    const targetMin = Number(t[1]) * 60 + Number(t[2]);
    const leftSec = Math.round((targetMin - nowMinutes()) * 60);
    const bucket = leftSec <= 60 ? 1 : leftSec <= 300 ? 5 : 0;
    const prev = cueDlBucket[act];
    if(prev === undefined){ cueDlBucket[act] = bucket; continue; }   // 刚打开/刚改时间：只记基线，不补响
    if(bucket === prev) continue;
    cueDlBucket[act] = bucket;
    if(bucket === 5) playCue('five');
    else if(bucket === 1) playCue('one');
  }
}

function syncCueBtn(){
  const b = $('#setCue');
  if(!b) return;
  b.textContent = S.cueOn ? '已开启' : '已关闭';
  b.className = 'btn btn-sm ' + (S.cueOn ? 'btn-o' : 'btn-g');
  b.setAttribute('aria-pressed', String(!!S.cueOn));
}
function toggleCue(){
  S.cueOn = !S.cueOn;
  cueDlBucket = {};
  saveState(); syncCueBtn();
  if(S.cueOn){ playCue('test'); toast('提示音已开启：组提交 / 截止前 5、1 分钟提醒'); }
  else toast('提示音已关闭');
}
function testCue(){ playCue('test'); }

/* ---------------- 打印导播单 ----------------
   P3 重构：把正文拆成 printHTML()，打印（#printArea）与导出 Word 共用同一份 HTML，
   避免「打印一份、Word 一份」两处排版漂移。 */
function printHTML(){
  const s = stepByNo(S.activeStep) || D.script[0];
  const order = speakerSessions();
  const rows = D.script.map(x=>{
    const dl = S.deadlines[x.no];
    return `<tr>
      <td style="width:8%;">${x.no}</td>
      <td style="width:11%;">${esc(x.start)}<br>${esc(x.end)}</td>
      <td style="width:8%;">${x.duration}′</td>
      <td style="width:16%;"><b>${esc(x.name)}</b>${x.docFlag?`<br><span style="color:#8F5406;">〔${esc(x.docFlag)}〕原文：${esc(x.docName)}</span>`:''}</td>
      <td>${esc(x.task)}${(x.process&&x.process.length)?`<br><span style="color:#555;">${x.process.map(esc).join('<br>')}</span>`:''}</td>
      <td style="width:12%;">${x.staff.map(esc).join('、')}</td>
      <td style="width:7%;">${dl?esc(dl):'—'}</td>
    </tr>`;
  }).join('');

  const stTxt = r => r.status==='done'?'正式提交':r.status==='draft'?'草稿':'未提交';
  const seatRows = order.map((x,i)=>{
    const a1=subOf(x.id,1), a2=subOf(x.id,2);
    return `<tr>
      <td style="width:5%;">${i+1}</td>
      <td style="width:8%;">${esc(x.code)}</td>
      <td style="width:15%;">${esc(x.name)}</td>
      <td style="width:11%;">${esc(x.coordinator||'—')}</td>
      <td style="width:14%;">${esc(x.unit||'—')}</td>
      <td style="width:14%;">${esc(x.expert||'—')}</td>
      <td style="width:11%;">${stTxt(a1)}</td>
      <td style="width:11%;">${stTxt(a2)}</td>
    </tr>`;
  }).join('');

  const host = D.sessions.find(x=>x.organizer);
  const warn = D.warnings.find(x=>x.id===S.warningId);
  const flags = D.script.filter(x=>x.flagNote);

  /* 各组方案原文（打印/Word 用全文；草稿也打印出来，避免组端漏点“提交”时现场没内容） */
  const propCell = (r, act) => {
    if(r.status==='none') return '未提交';
    const draft = r.status==='draft' ? '<br><span style="color:#8F5406;">（草稿）</span>' : '';
    if(r.fromServer) return proposalHTML(act, r) + draft;
    return esc(r.text||'（无内容）').replace(/\n/g,'<br>') + draft;
  };
  const propRows = order.map((x,i)=>{
    const a1 = subOf(x.id,1), a2 = subOf(x.id,2);
    return `<tr>
      <td style="width:4%;">${i+1}</td>
      <td style="width:13%;">${esc(x.code)}<br>${esc(x.name)}</td>
      <td>${propCell(a1,1)}</td>
      <td>${propCell(a2,2)}</td>
    </tr>`;
  }).join('');

  return `
    <div class="pr-h">
      <h1>导演组流程脚本 · 导播单</h1>
      <div class="s">${esc(D.meta.system)}　|　${esc(D.meta.title)}　|　全程 ${esc(D.course.startTime)}—${esc(D.course.endTime)}　共 ${D.course.totalMinutes} 分钟</div>
    </div>

    <div class="pr-sec">
      <h3>一、当前执行状态</h3>
      <div class="pr-two">
        <table><tbody>
          <tr><th style="width:30%;">当前环节</th><td>环节 ${s.no} · ${esc(s.name)}</td></tr>
          <tr><th>时段</th><td>${esc(s.start)} — ${esc(s.end)}（${s.duration} 分钟）</td></tr>
          <tr><th>所属阶段</th><td>${esc((stageById(s.stage)||{}).name||'')}</td></tr>
          <tr><th>教学任务</th><td>${esc(s.task)}</td></tr>
          <tr><th>人员配备</th><td>${s.staff.map(esc).join('、')}</td></tr>
        </tbody></table>
        <table><tbody>
          <tr><th style="width:30%;">主办方</th><td>${host?esc(host.name)+'（协调员 '+esc(host.coordinator)+'）':'—'}</td></tr>
          <tr><th>参演方数</th><td>${S.speakerOrder.length} 方</td></tr>
          <tr><th>提交封控</th><td>${S.lock?'已封控':'未封控'}</td></tr>
          <tr><th>警告提示</th><td>${warn&&warn.id!=='w0'?esc(warn.text):'无'}</td></tr>
          <tr><th>提案同步</th><td>${PROPOSALS&&PROPOSALS.ok?'已接入（各组组端提交）':'未接入（本机演示数据）'}</td></tr>
          <tr><th>关键截止</th><td>${Object.entries(S.deadlines).map(([k,v])=>`环节 ${k} 前 ${esc(v)}`).join('；')||'—'}</td></tr>
        </tbody></table>
      </div>
    </div>

    <div class="pr-sec">
      <h3>二、流程脚本全表</h3>
      <table>
        <thead><tr><th>序号</th><th>时间</th><th>时长</th><th>教学过程</th><th>教学任务</th><th>人员配备</th><th>截止</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>

    <div class="pr-sec">
      <h3>三、席位名单与集中研讨发言顺序（十方）</h3>
      <table>
        <thead><tr><th>序</th><th>席位号</th><th>事件处置方</th><th>协调员</th><th>对接学院/单位</th><th>驻组专家</th><th>${actMark(1,true)} 第一幕 · 场景一方案</th><th>${actMark(2,true)} 第二幕 · 场景二方案</th></tr></thead>
        <tbody>${seatRows}</tbody>
      </table>
    </div>

    <div class="pr-sec">
      <h3>四、各组方案（组端提交原文）</h3>
      <table>
        <thead><tr>
          <th>序</th><th>席位</th>
          <th>${actMark(1,true)} 第一幕（形势研判 / 对策思路 / 处置措施 / 效果预估）</th>
          <th>${actMark(2,true)} 第二幕（总体研判 / 对策思路 / 处置措施）</th>
        </tr></thead>
        <tbody>${propRows}</tbody>
      </table>
    </div>

    <div class="pr-sec">
      <h3>五、口径与素材说明</h3>
      <table><tbody>
        ${flags.map(x=>`<tr><td style="width:16%;">环节 ${x.no}</td><td>${esc(x.flagNote)}</td></tr>`).join('')}
        <tr><td style="width:16%;">席位口径</td><td>《事件处置方分组表》列 10 个处置方，本单按 <strong>10 方全部参演</strong>（十方推演）处理，发言顺序为分组表序号 1—10。其中 B市妇联部门为主办方，同时作为第 1 号参演席位参与集中研讨三轮发言。源文档《导演组流程脚本.doc》原称「九方」，现以十方为准。</td></tr>
        <tr><td style="width:16%;">素材清单</td><td>本场 5 段：先导片·战略推演背景 07:00（960×540）；科普短片一 02:59、科普短片二 03:00（640×368）；情景一 03:05、情景二 02:52（1280×720）。单文件均 ≤25 MB，H.264 / AAC，浏览器直接播放。</td></tr>
        <tr><td style="width:16%;">素材版本</td><td>情景一/二 源为 HEVC（1922×1080），已转 H.264；科普短片为源文件重封装；均带 faststart，可边下边播。情景二由学生助理在各分组教室播放。</td></tr>
      </tbody></table>
    </div>

    <div class="pr-foot">
      <span>打印时间：${dateText()} ${nowClockText()}　打印人：导演组</span>
      <span>${esc(D.meta.docSource)} · ${esc(D.meta.revision)}</span>
    </div>`;
}
function buildPrintArea(){
  $('#printArea').innerHTML = printHTML();
}

/* ---------------- 导出 Word（P3 新增）----------------
   Word 是另一套渲染引擎：不支持 CSS 变量、flex/grid、gap、border-radius，
   所以 WORD_CSS 不是打印样式的副本，而是面向 Word 的「移植版」——只复用正文 HTML。 */
const WORD_CSS = `
@page{ size:21cm 29.7cm; margin:1.6cm 1.5cm 1.8cm; }
body{ font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",sans-serif;
      color:#16202C; font-size:10pt; line-height:1.5; }
table{ width:100%; border-collapse:collapse; font-size:9pt; }
th{ background:#EEF4FA; border:1px solid #C8D8E8; padding:5px 6px;
    text-align:left; font-weight:700; }
td{ border:1px solid #D6E0EB; padding:5px 6px; vertical-align:top; line-height:1.6; }
tr{ page-break-inside:avoid; }
/* 幕次罗马数：与屏上 .act-roman.boxed 同一套识别系统（Word 不认 var()，取字面量） */
.act-roman{ font-family:Consolas,"Courier New",monospace; font-weight:700; color:#175FA8; }
.act-roman.boxed{ display:inline-block; padding:1px 5px; margin-right:4px;
                  border:1px solid #2C87D8; background:#E7F1FB; font-size:9pt; }
.pr-h{ text-align:center; border-bottom:2px solid #175FA8; padding-bottom:8px; margin-bottom:14px; }
.pr-h h1{ font-size:19pt; margin:0 0 4px; letter-spacing:1px; }
.pr-h .s{ font-size:10pt; color:#555; }
.pr-sec{ margin-bottom:16px; page-break-inside:avoid; }
.pr-sec h3{ font-size:12pt; margin:0 0 7px; padding:5px 9px;
            background:#E7F1FB; color:#123F72; border-left:4px solid #175FA8; }
.pr-foot{ margin-top:16px; padding-top:8px; border-top:1px solid #C8D8E8;
          font-size:9pt; color:#555; }
`;

/* Word 不认 grid：把 .pr-two 里的两张表搬进外层表格的两个单元格，保持并排 */
function wordLayout(html){
  return html.replace(/<div class="pr-two">([\s\S]*?)<\/div>/g, (m, inner)=>{
    const t = inner.match(/<table>[\s\S]*?<\/table>/g) || [];
    if(t.length < 2) return m;
    return '<table style="width:100%;border-collapse:collapse;border:none;">'
      + '<tr><td style="width:50%;border:none;padding:0 6px 0 0;vertical-align:top;">' + t[0]
      + '</td><td style="width:50%;border:none;padding:0 0 0 6px;vertical-align:top;">' + t[1]
      + '</td></tr></table>';
  });
}

function exportWord(){
  buildPrintArea();                       /* 与打印同源，顺带刷新导出时间 */
  const doc =
    '<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" '
    + 'xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">'
    + '<head><meta charset="utf-8"><title>导演组流程脚本 · 导播单</title>'
    + '<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>100</w:Zoom>'
    + '</w:WordDocument></xml><![endif]-->'
    + '<style>' + WORD_CSS + '</style></head><body>'
    + wordLayout(printHTML())
    + '</body></html>';

  /* BOM 必须有：Windows 中文版 Word 没有它会把 UTF-8 当 GBK 读，中文全成乱码 */
  const blob = new Blob(['\ufeff', doc], {type:'application/msword;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `导演组流程脚本_导播单_${new Date().toISOString().slice(0,10)}.doc`;
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  toast('已导出 Word 版导播单，可在 Word 中批注后存档');
}

/* ---------------- 第二屏大屏输出（P3 新增）----------------
   导播自己看控制台，副屏给技术组 / 主持人 / 全场看「现在到哪、还剩多久、下一步是什么」。
   screen.html 自己读 flow.json 与 localStorage，两端之间零依赖、零后端。 */
let screenWin = null;
function openSecondScreen(){
  const url = 'screen.html';
  screenWin = window.open(url, 'director-second-screen', 'width=1440,height=810');
  if(!screenWin){
    toast('浏览器拦截了弹窗，请允许本站弹出窗口后再试');
    return;
  }
  try{ screenWin.focus(); }catch(e){}
  toast('第二屏已打开：可投到副屏 / 投影仪，本页继续操作');
}

/* ---------------- 离线缓存（P3 新增）----------------
   默认关闭，不破坏「改完刷新生效」；彩排前手动打开，现场断网不黑屏。 */
const SW_URL = 'sw.js';
function swSupported(){
  return 'serviceWorker' in navigator
      && (location.protocol === 'https:' || location.hostname === 'localhost'
          || location.hostname === '127.0.0.1');
}
async function applyOffline(){
  if(!swSupported()) return;
  try{
    if(S.offlineOn){
      const reg = await navigator.serviceWorker.register(SW_URL);
      if(reg && reg.waiting) reg.waiting.postMessage({type:'ACTIVATE'});
    }else{
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map(r=>r.unregister()));
      if(window.caches){
        const keys = await caches.keys();
        await Promise.all(keys.filter(k=>k.indexOf('ws-')===0).map(k=>caches.delete(k)));
      }
    }
  }catch(e){}
}
function toggleOffline(){
  S.offlineOn = !S.offlineOn;
  saveState();
  applyOffline();
  renderConn();
  toast(S.offlineOn
    ? '离线缓存已启用：彩排期间断网不会黑屏；改完代码后请关一次再开'
    : '离线缓存已关闭，回到「改完刷新生效」');
}

/* P3：顶栏连接状态 —— 现场必须一眼看出是「真在线」还是「在用本地缓存」 */
function renderConn(){
  const el = $('#connText');
  if(!el) return;
  if(!navigator.onLine){
    el.innerHTML = '<span class="conn-warn">⚠ 网络已断开</span>'
      + (S && S.offlineOn ? ' · 正在使用本地缓存' : '');
    el.parentElement.classList.add('is-offline');
  }else{
    el.textContent = '已连接课堂 · 导播在线';
    el.parentElement.classList.remove('is-offline');
  }
}

/* ---------------- 导出 ---------------- */
function exportData(){
  const blob = new Blob([JSON.stringify({meta:D.meta, state:S}, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `导演工作台演示数据_${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  toast('演示数据已导出');
}

/* ---------------- Toast ---------------- */
let toastTimer=null;
function toast(msg){
  const t=$('#toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>t.classList.remove('show'), 2200);
}

/* ---------------- 确认弹窗（P0 新增：危险操作二次确认）
   P1 扩展：single 模式复用为「设置面板 / 快捷键帮助」 ---------------- */
function confirmAction(opt){
  const modal = $('#cmask .cmodal');
  $('#cmodalTitle').textContent = opt.title || '确认操作';
  $('#cmodalBody').innerHTML    = opt.body  || '';
  $('#cmodalOk').textContent    = opt.okText || '确认执行';
  $('#cmodalCancel').style.display = opt.single ? 'none' : '';
  $('#cmodalOk').className      = opt.single ? 'btn btn-p' : 'btn btn-d';
  modal.classList.toggle('neutral', !!opt.single);
  $('.cmodal-ic').textContent   = opt.icon || (opt.single ? '≡' : '!');
  cmodalOkFn = opt.onOk || null;
  $('#cmask').classList.add('show');
  setTimeout(()=>{ (opt.single ? $('#cmodalOk') : $('#cmodalCancel')).focus(); }, 0);
}
function closeConfirm(){
  $('#cmask').classList.remove('show');
  cmodalOkFn = null;
}

/* ---------------- 封控武装态（P0 新增：两段确认）---------------- */
function disarmLock(){
  lockArmed = false;
  if(lockArmTimer){ clearTimeout(lockArmTimer); lockArmTimer = null; }
  const b = $('#btnLock');
  if(b){ b.classList.remove('armed'); b.textContent = S.lock ? '封控中' : '开启全体封控'; }
}

/* ---------------- 危险操作确认（P1 抽出以便设置面板复用）---------------- */
function askReset(){
  confirmAction({
    title:'恢复初始状态',
    body:'将<em>清空本机保存的全部运行状态</em>，包括：<br>当前环节、选中席位、发言顺序、截止时间调整、封控与警告设置、语音开关，以及各席位的全部提交记录。<br><br>此操作<em>不可撤销</em>。不影响 <em>data/flow.json</em> 源数据。',
    okText:'清空并恢复初始状态',
    onOk(){ localStorage.removeItem(STORE_KEY); loadState(); renderAll(); toast('已恢复初始状态'); }
  });
}
function askResetOrder(){
  confirmAction({
    title:'恢复默认发言顺序',
    body:'将「指定小组顺序」还原为《事件处置方分组表》序号 <em>1—10</em>。<br>你此前手动调整的顺序将<em>被覆盖</em>。',
    okText:'恢复默认顺序',
    onOk(){
      S.speakerOrder = D.initialState.speakerOrder.slice();
      saveState(); renderOrder(); renderSeatTable();
      toast('发言顺序已恢复默认');
    }
  });
}

/* ---------------- 提案服务：重开本幕 / 清空本场 ---------------- */
async function reopenAct(act){
  if(!(PROPOSALS && PROPOSALS.ok)){ toast('提案服务未接入，无法重开'); return; }
  const j = await proposalPost({ act, open:true }, 'open');
  if(j){
    await refreshProposals();
    toast(`已重新开放第${act===1?'一':'二'}幕，各组可继续修改`);
    closeConfirm(); setTimeout(openSettings, 80);
  }
}
function askClearProposals(){
  if(!(PROPOSALS && PROPOSALS.ok)){ toast('提案服务未接入，无可清空数据'); return; }
  confirmAction({
    title:'清空本场提案',
    icon:'!',
    okText:'确认清空',
    body:'将删除 Cloudflare KV 中<em>全部 10 组 × 2 幕</em>的方案与「重开本幕」状态，<br><em>不可撤销</em>。建议只在正式场开始前、彩排数据需要作废时使用。',
    onOk: async ()=>{
      const j = await proposalPost({}, 'reset');
      if(j){ await refreshProposals(); toast('本场提案已清空'); }
    }
  });
}

/* ---------------- 语音播报开关（P1 新增）---------------- */
function syncVoiceBtn(){
  const b = $('#btnVoice');
  if(!b) return;
  b.textContent = S.voiceOn ? '🔊 语音：开' : '🔇 语音：关';
  b.className = 'btn btn-sm ' + (S.voiceOn ? 'btn-o' : 'btn-g');
  b.setAttribute('aria-pressed', String(!!S.voiceOn));
}
function toggleVoice(){
  S.voiceOn = !S.voiceOn;
  saveState(); syncVoiceBtn();
  if(S.voiceOn){ speak('语音播报已开启'); toast('语音播报已开启'); }
  else{ if('speechSynthesis' in window) speechSynthesis.cancel(); toast('语音播报已关闭'); }
}

/* ============================================================
   P2 · 时间刻度尺
   ① 标注行 ② 播放头行 ③ 刻度行；仅在课程时段内做实时播放头
   ============================================================ */
function rulerPct(minute, t0, span){ return ((minute - t0) / span) * 100; }

function renderRuler(){
  const track = $('#rulerTrack');
  if(!track || !D || !D.course) return;
  const t0 = hm2m(D.course.startTime), t1 = hm2m(D.course.endTime);
  const span = Math.max(t1 - t0, 1);

  const durH = Math.floor(D.course.totalMinutes / 60), durM = D.course.totalMinutes % 60;
  $('#rlScale').textContent = '刻度 · 每 5 分钟';
  $('#rlEnd').textContent = `${D.course.startTime} — ${D.course.endTime} · ${durH ? durH+' 小时 ' : ''}${durM} 分钟`;
  const rEl = $('#ruler');
  if(rEl) rEl.setAttribute('aria-label',
    `全天时间刻度尺：${D.course.startTime} 至 ${D.course.endTime}，共 ${D.course.totalMinutes} 分钟；` +
    `整点与半点有加长刻度，虚线为阶段分界，Ⅰ / Ⅱ 标记第一幕与第二幕起点。`);

  let html = '<div class="rl-base"></div>';
  /* 底层：全天已演区间 + 当前环节高亮（刻度线画在其上，保证可读） */
  html += '<div class="rl-done" id="rlDone" style="width:0"></div>';
  html += '<div class="rl-fill" id="rlFill" style="left:0;width:0"></div>';
  /* 刻度：5 分钟短刻 / 半点中刻 / 整点长刻并标注 */
  for(let m = Math.ceil(t0/5)*5; m <= t1; m += 5){
    const p = rulerPct(m, t0, span).toFixed(3);
    const isHour = m % 60 === 0, isHalf = m % 30 === 0;
    html += `<div class="rl-tick ${isHour?'q':isHalf?'hf':''}" style="left:${p}%"></div>`;
    if(isHour) html += `<div class="rl-lab" style="left:${p}%">${pad(Math.floor(m/60))}:${pad(m%60)}</div>`;
  }
  /* 阶段分界；幕次起点标罗马数（与阶段条、时间轴、表头同一套系统） */
  D.stages.forEach((s,i)=>{
    if(i===0) return;
    const a = actOfStage(s.id);
    html += `<div class="rl-act" style="left:${rulerPct(hm2m(s.from),t0,span).toFixed(3)}%">${a?`<span>${ROMAN[a]}</span>`:''}</div>`;
  });
  html += '<div class="rl-now idle" id="rlNow" style="left:0">待开演</div>';
  html += '<div class="rl-head rl-head-idle" id="rlHead" style="left:0"></div>';
  track.innerHTML = html;
  updateRuler();
}

function updateRuler(){
  const track = $('#rulerTrack');
  if(!track) return;
  const head = $('#rlHead');
  if(!head) return;
  const nowEl = $('#rlNow');
  const t0 = hm2m(D.course.startTime), t1 = hm2m(D.course.endTime);
  const span = Math.max(t1 - t0, 1);
  const now = nowMinutes();
  const inCourse = now >= t0 && now <= t1;
  const clamped = Math.min(Math.max(now, t0), t1);
  const pNow = Math.min(Math.max(rulerPct(clamped, t0, span), 0), 100);

  head.style.left = pNow.toFixed(3) + '%';
  nowEl.style.left = Math.min(Math.max(pNow, 3), 97).toFixed(3) + '%';

  if(inCourse){
    head.classList.remove('rl-head-idle');
    nowEl.classList.remove('idle');
    let secs = Math.round((clamped - Math.floor(clamped)) * 60);
    let mm = Math.floor(clamped / 60);
    if(secs >= 60){ secs = 0; mm += 1; }          // 防止出现 :60
    nowEl.textContent = `${pad(mm)}:${pad(secs)}`;
    $('#rlDone').style.width = pNow.toFixed(3) + '%';
  }else{
    head.classList.add('rl-head-idle');
    nowEl.classList.add('idle');
    nowEl.textContent = now < t0 ? '待开演' : '已收工';
    $('#rlDone').style.width = now < t0 ? '0%' : '100%';
  }

  /* 当前环节在全天刻度上的区间高亮 */
  const s = stepByNo(S.activeStep) || D.script[0];
  if(s){
    const a = Math.min(Math.max(hm2m(s.start), t0), t1);
    const b = Math.min(Math.max(hm2m(s.end),   t0), t1);
    const l = Math.min(Math.max(rulerPct(a,t0,span),0),100);
    const r = Math.min(Math.max(rulerPct(b,t0,span),0),100);
    $('#rlFill').style.left  = l.toFixed(3) + '%';
    $('#rlFill').style.width = Math.max(r - l, 0).toFixed(3) + '%';
  }
}

/* ============================================================
   P2 · 深色指挥室模式（一键切换 + 快捷键 D；打印恒为浅色）
   ============================================================ */
function applyTheme(){
  const dark = S.theme === 'dark';
  /* 主题类同时挂 html 与 body：
     html 让滚动条伪元素 / color-scheme 读到深色令牌，body 承载全部组件规则 */
  document.documentElement.classList.toggle('theme-dark', dark);
  document.body.classList.toggle('theme-dark', dark);
  const b = $('#btnTheme');
  if(b){
    b.textContent = dark ? '◑' : '◐';
    b.setAttribute('aria-pressed', String(dark));
    b.title = dark ? '切换到浅色模式（快捷键 D）' : '切换到深色指挥室模式（快捷键 D）';
  }
}
function toggleTheme(){
  S.theme = (S.theme === 'dark') ? 'light' : 'dark';
  saveState(); applyTheme();
  toast(S.theme === 'dark' ? '深色指挥室模式 · 打印时仍为浅色' : '已切回浅色模式');
}

/* ============================================================
   P2 · 导播全屏模式（快捷键 G；收起页头与提交进度表）
   ============================================================ */
let directorExitTimer = null;
function setDirector(on){
  on = !!on;
  if(on === document.body.classList.contains('director')) return;
  document.body.classList.toggle('director', on);

  const b = $('#btnDirector');
  if(b){
    b.setAttribute('aria-pressed', String(on));
    b.textContent = on ? '✕' : '⛶';
    b.title = on ? '退出导播模式（快捷键 G）' : '进入导播全屏模式（快捷键 G）';
  }
  if(on){
    showModeExit();
    /* 进入导播模式后回到顶部，确保阶段条 / 刻度尺 / 视频台首屏可见 */
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    try{ window.scrollTo({top:0, behavior: reduce ? 'auto' : 'smooth'}); }
    catch(e){ window.scrollTo(0, 0); }
    if(document.documentElement.requestFullscreen){
      document.documentElement.requestFullscreen().catch(()=>{});
    }
  }else{
    hideModeExit();
    if(document.fullscreenElement && document.exitFullscreen){
      document.exitFullscreen().catch(()=>{});
    }
  }
}
function toggleDirector(){ setDirector(!document.body.classList.contains('director')); }

function showModeExit(){
  const el = $('#modeExit');
  if(!el) return;
  el.classList.add('show');
  clearTimeout(directorExitTimer);
  directorExitTimer = setTimeout(()=>el.classList.remove('show'), 5000);
}
function hideModeExit(){
  clearTimeout(directorExitTimer);
  const el = $('#modeExit');
  if(el) el.classList.remove('show');
}

/* ---------------- 快捷键帮助（P1 新增）---------------- */
function openHelp(){
  confirmAction({
    title:'键盘快捷键', single:true, icon:'⌨', okText:'知道了',
    body:`<div class="kbd-grid">
      <span class="kbd">←</span><span>切换到上一环节</span>
      <span class="kbd">→</span><span>切换到下一环节</span>
      <span class="kbd">空格</span><span>播放 / 暂停当前视频</span>
      <span class="kbd">/</span><span>打开快速搜索</span>
      <span class="kbd">P</span><span>打印 A4 导播单</span>
      <span class="kbd">F</span><span>视频大屏模式</span>
      <span class="kbd">G</span><span>导播全屏模式（收起次要区域）</span>
      <span class="kbd">D</span><span>深色指挥室模式（打印仍为浅色）</span>
      <span class="kbd">S</span><span>打开第二屏大屏输出（副屏 / 投影）</span>
      <span class="kbd">W</span><span>导出 Word 版导播单（可批注存档）</span>
      <span class="kbd">?</span><span>打开本帮助面板</span>
      <span class="kbd">Esc</span><span>关闭弹窗、搜索或退出导播模式</span>
    </div>
    <p style="margin:14px 0 0;font-size:var(--fs-xs);color:var(--ink-3);line-height:1.7;">
      在输入框、下拉框中输入或选择时，快捷键自动失效，不会误触。</p>`
  });
}

/* ---------------- 设置面板（P1 新增，顶栏 ⚙）---------------- */
function openSettings(){
  confirmAction({
    title:'设置', single:true, icon:'⚙', okText:'完成',
    body:`
      <div class="set-row">
        <div class="sl"><b>倒计时语音播报</b><span>最后 60 秒每 10 秒播报，归零播报「环节结束」</span></div>
        <button class="btn btn-sm ${S.voiceOn?'btn-o':'btn-g'}" id="setVoice">${S.voiceOn?'已开启':'已关闭'}</button>
      </div>
      <div class="set-row">
        <div class="sl"><b>提示音</b><span>默认关闭。开启后：有组提交提案时响一声；本幕截止前 5 分钟、1 分钟各提醒一次</span></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn btn-g btn-sm" id="setCueTest">试听</button>
          <button class="btn btn-sm ${S.cueOn?'btn-o':'btn-g'}" id="setCue">${S.cueOn?'已开启':'已关闭'}</button>
        </div>
      </div>
      <div class="set-row">
        <div class="sl"><b>深色指挥室模式</b><span>暗光现场/投影环境使用；打印导播单恒为浅色</span></div>
        <button class="btn btn-sm ${S.theme==='dark'?'btn-o':'btn-g'}" id="setTheme">${S.theme==='dark'?'已开启':'已关闭'}</button>
      </div>
      <div class="set-row">
        <div class="sl"><b>离线缓存</b><span>${
          swSupported()
            ? '默认关闭。彩排前打开，现场断网不黑屏；改完代码后需关一次再开'
            : '当前环境不支持 Service Worker（需通过 http://localhost 或 https 访问）'
        }</span></div>
        ${
          swSupported()
            ? `<button class="btn btn-sm ${S.offlineOn?'btn-o':'btn-g'}" id="setOffline">${S.offlineOn?'已开启':'已关闭'}</button>`
            : '<button class="btn btn-sm btn-g" id="setOffline" disabled>不可用</button>'
        }
      </div>
      <div class="set-row">
        <div class="sl"><b>各组提案</b><span id="setPropState">${propStateText()}</span></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <a class="btn btn-g btn-sm" id="setGroupLink" target="_blank" rel="noopener">组端页</a>
          <button class="btn btn-g btn-sm" id="setOpen1">重开第一幕</button>
          <button class="btn btn-g btn-sm" id="setOpen2">重开第二幕</button>
        </div>
      </div>
      <div class="set-row">
        <div class="sl"><b>清空本场提案</b><span>删除云端全部方案与「重开本幕」状态，不可撤销</span></div>
        <button class="btn btn-g btn-sm" id="setClearProp">清空</button>
      </div>
      <div class="set-row">
        <div class="sl"><b>恢复初始状态</b><span>清空本机保存的全部运行状态，不可撤销</span></div>
        <button class="btn btn-g btn-sm" id="setReset">执行</button>
      </div>
      <div class="set-row">
        <div class="sl"><b>导出演示数据</b><span>导出当前状态 JSON，用于彩排留档</span></div>
        <button class="btn btn-g btn-sm" id="setExport">导出</button>
      </div>
      <div class="set-row">
        <div class="sl"><b>存储与今日额度</b><span id="setQuotaState">${quotaStateText()}</span></div>
      </div>
      <div class="set-row">
        <div class="sl"><b>数据源</b><span>${esc(D.meta.docSource)} · ${esc(D.meta.revision)} · 纯静态 + 提案服务（Cloudflare Functions/KV）</span></div>
      </div>`
  });
  $('#setVoice').onclick = ()=>{
    toggleVoice();
    const b = $('#setVoice');
    b.textContent = S.voiceOn ? '已开启' : '已关闭';
    b.className = 'btn btn-sm ' + (S.voiceOn ? 'btn-o' : 'btn-g');
  };
  if($('#setCue')) $('#setCue').onclick = ()=>{ toggleCue(); syncCueBtn(); };
  if($('#setCueTest')) $('#setCueTest').onclick = testCue;
  $('#setReset').onclick = ()=>{ closeConfirm(); setTimeout(askReset, 80); };
  $('#setExport').onclick = exportData;
  const gl = $('#setGroupLink');
  if(gl){ const k = proposalKey(); gl.href = 'group.html' + (k ? '?key='+encodeURIComponent(k) : ''); }
  if($('#setOpen1')) $('#setOpen1').onclick = ()=>reopenAct(1);
  if($('#setOpen2')) $('#setOpen2').onclick = ()=>reopenAct(2);
  if($('#setClearProp')) $('#setClearProp').onclick = askClearProposals;
  $('#setTheme').onclick = ()=>{
    toggleTheme();
    const b = $('#setTheme');
    b.textContent = S.theme==='dark' ? '已开启' : '已关闭';
    b.className = 'btn btn-sm ' + (S.theme==='dark' ? 'btn-o' : 'btn-g');
  };
  const so = $('#setOffline');
  if(so && !so.disabled){
    so.onclick = ()=>{
      toggleOffline();
      so.textContent = S.offlineOn ? '已开启' : '已关闭';
      so.className = 'btn btn-sm ' + (S.offlineOn ? 'btn-o' : 'btn-g');
    };
  }
}

/* ---------------- 快速搜索（P1 新增）---------------- */
function openSearch(){
  $('#searchBar').hidden = false;
  const i = $('#inpSearch'); i.focus(); i.select();
}
function closeSearch(){
  $('#searchBar').hidden = true;
  $('#inpSearch').value = '';
  /* 必须显式清除过滤类：applySearch() 在隐藏态会提前返回 */
  $$('.step').forEach(el=>el.classList.remove('no-match'));
  $$('#seatTbody tr').forEach(el=>el.classList.remove('is-dimmed'));
  $('#searchCount').textContent = '';
}
function applySearch(){
  if($('#searchBar').hidden) return;
  const q = $('#inpSearch').value.trim();
  let hits = 0;
  $$('.step').forEach(el=>{
    const hit = !q || el.textContent.includes(q);
    el.classList.toggle('no-match', !hit);
    if(q && hit) hits++;
  });
  $$('#seatTbody tr').forEach(el=>{
    const hit = !q || el.textContent.includes(q);
    el.classList.toggle('is-dimmed', !!q && !hit);
  });
  $('#searchCount').textContent = q ? `${hits} / ${D.script.length} 个环节` : '';
}

/* ---------------- 播放器辅助 ---------------- */
function togglePlay(){
  const v = $('#video');
  if(!v.getAttribute('src')) return;
  if(v.paused) v.play().catch(()=>{}); else v.pause();
}
function toggleFullscreen(){
  const b = $('#video').closest('.stage-box');
  if(document.fullscreenElement) document.exitFullscreen();
  else if(b && b.requestFullscreen) b.requestFullscreen().catch(()=>{});
}

/* ---------------- 统一渲染 ---------------- */
function renderAll(){
  $('#tbCrumb').textContent = D.meta.system;
  $('#phTitle').textContent = D.meta.title;
  $('#phSub').textContent = D.meta.subtitle;
  $('#scriptTag').textContent = `${D.script.length} 个环节 · ${D.course.totalMinutes} 分钟`;
  renderProposalStatus();
  $('#seatCountBadge').textContent = D.sessions.length;

  renderStageBar();
  renderRuler();
  renderSteps();
  renderProgress();
  renderSeatTable();
  renderSeatTabs();
  renderCompare();
  renderSeatMini();
  renderMediaTabs();
  renderSide();
  renderOrder();
  buildPrintArea();
  syncMediaToStep();
  syncScrollToStep();
  applySearch();
}

/* P1 新增：环节变化后自动把当前环节滚动到视野中央 */
let lastScrolledStep = null;
function syncScrollToStep(){
  if(lastScrolledStep === S.activeStep) return;
  const first = (lastScrolledStep === null);
  lastScrolledStep = S.activeStep;
  if(first) return;                              // 首屏不滚动，避免进场跳动
  requestAnimationFrame(()=>{
    const el = $('.step.on');
    if(!el) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({block:'center', behavior: reduce ? 'auto' : 'smooth'});
  });
}

/* 当前环节的素材与播放区保持一致（切环节自动换片） */
let currentMediaId = null;
function syncMediaToStep(){
  const m = mediaOfCurrentStep();
  const cur = stepByNo(S.activeStep) || D.script[0];
  if(!m){
    // 本环节无视频素材 → 清空播放区并给出提示，避免误放上一环节画面
    if(currentMediaId !== null){
      currentMediaId = null;
      const v = $('#video');
      v.removeAttribute('src'); v.load();
      $('#miniVideo').removeAttribute('src'); $('#miniVideo').load();
      $('#vpBadge').textContent = `环节 ${cur.no} · 本环节无视频素材`;
      $('#vpNote').textContent = `「${cur.name}」为 ${cur.start}—${cur.end} 的 ${cur.duration} 分钟环节，无配套视频。请按流程脚本推进。`;
      $('#vpTime').textContent = '00:00 / 00:00';
    }
    return;
  }
  if(m.id === currentMediaId) return;
  currentMediaId = m.id;
  loadMedia(m.id, false);
}
/* ---------------- 事件绑定 ---------------- */
function bind(){
  if(bound) return; bound = true;

  const v = $('#video');
  v.addEventListener('timeupdate', updateVpTime);
  v.addEventListener('loadedmetadata', updateVpTime);
  v.addEventListener('play',  ()=>$('#btnPlay').textContent='❚❚ 暂停');
  v.addEventListener('pause', ()=>$('#btnPlay').textContent='▶ 播放');
  $('#btnPlay').onclick = ()=>{
    // 无素材环节调用 play() 会让 promise 永久挂起，必须先守卫
    if(!v.getAttribute('src')){ toast('本环节无视频素材'); return; }
    if(v.paused) v.play().catch(()=>{}); else v.pause();
  };
  $('#btnLoop').onclick = ()=>{ loopOn=!loopOn; v.loop=loopOn; $('#miniVideo').loop=loopOn; $('#btnLoop').textContent=`循环：${loopOn?'开':'关'}`; };
  $('#btnMute').onclick = ()=>{ muteOn=!muteOn; v.muted=muteOn; $('#btnMute').textContent=`静音：${muteOn?'开':'关'}`; };
  /* P1 新增：点击画面播放/暂停 + 进度滑块拖动 */
  v.addEventListener('click', ()=>{ if(v.paused) v.play().catch(()=>{}); else v.pause(); });
  v.addEventListener('timeupdate', updateVpTime);
  $('#vpSeek').addEventListener('input', e=>{
    if(v.duration && isFinite(v.duration)) v.currentTime = (e.target.value/1000) * v.duration;
  });
  $('#btnMini').onclick = ()=>{
    miniOn = !miniOn;
    $('#mini').classList.toggle('show', miniOn);
    $('#btnMini').textContent = miniOn ? '关闭小窗' : '小窗播放';
    if(miniOn){ const m=mediaOfCurrentStep(); if(m){ const mv=$('#miniVideo'); mv.src=m.src; mv.poster=m.poster; mv.play().catch(()=>{}); } }
  };
  $('#miniClose').onclick = ()=>{ miniOn=false; $('#mini').classList.remove('show'); $('#btnMini').textContent='小窗播放'; $('#miniVideo').pause(); };

  $('#btnPrevStep').onclick = ()=>goStep(-1);
  $('#btnNextStep').onclick = ()=>goStep(1);
  $('#btnPrev').onclick = ()=>cycleSession(-1);
  $('#btnNext').onclick = ()=>cycleSession(1);

  $('#selStage').onchange = e=>{ const st=stageById(e.target.value); S.activeStep=st.steps[0]; S.activeStage=st.id; saveState(); renderAll(); };
  $('#selWarn').onchange = e=>{ S.warningId=e.target.value; saveState(); renderSide(); toast('警告提示已更新'); };
  $('#btnLock').onclick   = ()=>{
    if(S.lock) return;                       // 已封控，禁用态下不应触发
    if(!lockArmed){                          // 第一下：进入武装态
      lockArmed = true;
      const b = $('#btnLock');
      b.classList.add('armed');
      b.textContent = '⚠ 再次点击确认封控';
      lockArmTimer = setTimeout(disarmLock, 3000);
      return;
    }
    disarmLock();                            // 第二下：生效
    S.lock=true; saveState(); renderSide(); renderSeatTable(); toast('已开启全体封控');
  };
  $('#btnUnlock').onclick = ()=>{ disarmLock(); S.lock=false; saveState(); renderSide(); renderSeatTable(); toast('已解除封控'); };
  $('#selDeadlineStep').onchange = e=>{ S.activeStep=Number(e.target.value); saveState(); renderSide(); renderSteps(); };
  $('#inpDeadline').onchange = e=>{
    const t = e.target.value ? e.target.value.split('T')[1] : '';
    if(t) S.deadlines[S.activeStep]=t; else delete S.deadlines[S.activeStep];
    saveState(); renderSteps(); renderSide();
    toast(t?`环节 ${S.activeStep} 截止时间设为 ${t}`:'已清除该环节截止时间');
  };
  /* P3：演练时钟改为持久化锚点 —— 刷新 / 换标签页 / 第二屏都能复算出同一个时刻 */
  $('#btnClockNow').onclick = ()=>{ S.drill=null; saveState(); toast('演练时钟已同步真实时间'); };
  $('#btnClockStart').onclick = ()=>{
    const d = new Date();
    S.drill = { anchorWall: d.getTime(), courseStartMin: hm2m(D.course.startTime) };
    saveState();
    toast(`演练时钟已置为 ${D.course.startTime}（刷新不会丢失）`);
  };
  $('#btnPrint').onclick = ()=>{ buildPrintArea(); window.print(); };
  $('#btnWord').onclick = exportWord;
  $('#btnScreen').onclick = openSecondScreen;
  $('#btnGroupPage').onclick = ()=>{
    const k = proposalKey();
    window.open('group.html' + (k ? '?key=' + encodeURIComponent(k) : ''), '_blank');
  };
  $('#btnExport').onclick = exportData;
  if($('#btnPropHit')) $('#btnPropHit').onclick = ()=>{ refreshProposals(); toast('已刷新各组提案'); };
  $('#btnReset').onclick = askReset;
  $('#btnResetOrder').onclick = askResetOrder;

  /* 十方改造新增：法规与政策参考库 */
  lawBind();

  $('#modalClose').onclick = closeModal;
  $('#modalCancel').onclick = closeModal;
  $('#modalConfirm').onclick = ()=>{ if(modalSid){ S.activeSession=modalSid; saveState(); renderAll(); } closeModal(); };
  $('#mask').onclick = e=>{ if(e.target===$('#mask')) closeModal(); };

  $('#cmodalCancel').onclick = closeConfirm;
  $('#cmodalOk').onclick = ()=>{ const fn=cmodalOkFn; closeConfirm(); if(fn) fn(); };
  $('#cmask').onclick = e=>{ if(e.target===$('#cmask')) closeConfirm(); };

  /* ---- P1 新增：语音 / 帮助 / 设置 / 搜索 ---- */
  $('#btnVoice').onclick    = toggleVoice;
  $('#btnHelp').onclick     = openHelp;
  $('#btnSettings').onclick = openSettings;
  $('#btnSearch').onclick   = openSearch;
  $('#btnSearchClose').onclick = closeSearch;
  $('#inpSearch').addEventListener('input', applySearch);
  $('#btnFull').onclick     = toggleFullscreen;

  /* ---- P2 新增：主题切换 / 导播全屏模式 ---- */
  $('#btnTheme').onclick    = toggleTheme;
  $('#btnDirector').onclick = toggleDirector;
  $('#modeExitBtn').onclick = ()=>setDirector(false);
  $('#modeExitHint').textContent = '已收起提交进度表与页头';
  applyTheme();
  document.addEventListener('fullscreenchange', ()=>{
    if(!document.fullscreenElement && document.body.classList.contains('director')) setDirector(false);
  });

  /* ---- P3 新增：第二屏 / 离线缓存 / 网络状态 ---- */
  applyOffline();
  renderConn();
  /* 提案服务：首拉 + 5 秒轮询（页面隐藏时暂停） */
  startProposalPolling();
  window.addEventListener('online',  ()=>{ renderConn(); toast('网络已恢复'); });
  window.addEventListener('offline', ()=>{
    renderConn();
    toast(S.offlineOn ? '网络已断开，正在使用本地缓存' : '网络已断开，请检查会场网络');
  });

  /* ---- P1 新增：键盘快捷键系统 ---- */
  document.addEventListener('keydown', e=>{
    const t = e.target;
    const typing = t && (t.tagName==='INPUT' || t.tagName==='TEXTAREA' || t.tagName==='SELECT' || t.isContentEditable);
    const cOpen = $('#cmask').classList.contains('show');
    const mOpen = $('#mask').classList.contains('show');
    const lOpen = lawsIsOpen();

    if(e.key === 'Escape'){
      if(lOpen) closeLaws();
      else if(cOpen) closeConfirm();
      else if(!$('#searchBar').hidden) closeSearch();
      else if(mOpen) closeModal();
      else if(document.body.classList.contains('director')) setDirector(false);
      return;
    }
    if(typing || cOpen || mOpen || lOpen || e.ctrlKey || e.metaKey || e.altKey) return;

    switch(e.key){
      case 'ArrowLeft':   e.preventDefault(); goStep(-1); break;
      case 'ArrowRight':  e.preventDefault(); goStep(1);  break;
      case ' ':           e.preventDefault(); togglePlay(); break;
      case '/':           e.preventDefault(); openSearch(); break;
      case '?':           e.preventDefault(); openHelp();   break;
      case 'p': case 'P': e.preventDefault(); buildPrintArea(); window.print(); break;
      case 'f': case 'F': e.preventDefault(); toggleFullscreen(); break;
      case 'g': case 'G': e.preventDefault(); toggleDirector(); break;
      case 'd': case 'D': e.preventDefault(); toggleTheme();    break;
      case 's': case 'S': e.preventDefault(); openSecondScreen(); break;
      case 'w': case 'W': e.preventDefault(); exportWord();       break;
      case 'l': case 'L': e.preventDefault(); openLaws();         break;
    }
  });

  const m = mediaOfCurrentStep();
  if(m) loadMedia(m.id, false);
}

function goStep(dir){
  const n = S.activeStep + dir;
  if(n < 1 || n > D.script.length) return false;
  S.activeStep = n;
  S.activeStage = (stepByNo(n)||{}).stage;
  saveState(); renderAll();
  return true;
}

function cycleSession(dir){
  const ids = D.sessions.map(s=>s.id);
  let i = ids.indexOf(S.activeSession);
  i = (i + dir + ids.length) % ids.length;
  S.activeSession = ids[i]; saveState();
  renderSeatTabs(); renderCompare(); renderSeatTable(); renderSeatMini();
}

/* ============================================================
   法规与政策参考库（十方改造新增）

   约束：无后端、无构建、无框架。只是 fetch 两个静态文件——
     data/laws/index.json      目录（随手加载）
     data/laws/NN.md           正文（点开哪一份才加载哪一份）
   正文用下面这个小渲染器出 HTML，不引任何第三方库。

   file:// 直接双击打开时 fetch 必然失败（与 flow.json 同样的取舍），
   此处不提供 JS 副本降级，直接给「请用本地服务器打开」的提示。
   ============================================================ */

const LAWS_URL = 'data/laws/index.json';
const LAWS_DIR = 'data/laws/';

let LAWS = null;      // index.json 内容
let LAW_MD = {};      // file -> 已加载的 markdown 原文
let LAW_CAT = 'all';  // 当前类别筛选
let LAW_Q = '';       // 当前搜索词
let LAW_CUR = null;   // 当前打开的条目对象
let LAW_PENDING = null; // 扫码深链：索引就绪后自动打开的 n
let LAW_DEEP = false;   // 本次打开是否由二维码触发（不自动聚焦搜索框，避免手机弹键盘）

/* ---------- 极简 markdown 渲染器 ---------- */

function lawEsc(s){
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function lawHrefOk(u){
  return /^(https?:)?\/\//i.test(u) || /^\.{0,2}\//.test(u);
}

function lawInline(s){
  let h = lawEsc(s);
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, txt, url) => {
    if(!lawHrefOk(url)) return txt;
    return '<a href="' + lawEsc(url) + '" target="_blank" rel="noopener noreferrer">' + txt + '</a>';
  });
  return h;
}

function lawMd2html(md){
  const blocks = String(md).replace(/\r\n?/g, '\n').split(/\n{2,}/);
  const out = [];
  for(const raw of blocks){
    const t = raw.replace(/^\s+|\s+$/g, '');
    if(!t) continue;

    const h = /^(#{1,6})\s+(.*)$/.exec(t);
    if(h){
      const lv = Math.min(6, h[1].length);
      const body = t.replace(/^#{1,6}\s+/, '');
      out.push('<h' + lv + '>' + body.split('\n').map(lawInline).join('<br>') + '</h' + lv + '>');
      continue;
    }
    if(/^>\s?/.test(t)){
      const inner = t.split('\n').map(l => lawInline(l.replace(/^>\s?/, ''))).join('<br>');
      out.push('<blockquote>' + inner + '</blockquote>');
      continue;
    }
    const ls = t.split('\n').filter(l => /^\s*[-*]\s+/.test(l));
    if(ls.length && ls.length === t.split('\n').length){
      out.push('<ul>' + ls.map(l => '<li>' + lawInline(l.replace(/^\s*[-*]\s+/, '')) + '</li>').join('') + '</ul>');
      continue;
    }
    out.push('<p>' + t.split('\n').map(lawInline).join('<br>') + '</p>');
  }
  return out.join('\n');
}

/* ---------- 打开 / 关闭 ---------- */

function openLaws(opt){
  const m = $('#lawMask');
  if(!m) return;
  const focus = !opt || opt.focus !== false;
  m.hidden = false;
  m.classList.add('show');
  document.body.style.overflow = 'hidden';
  lawEnsureIndex();
  if(focus) setTimeout(()=>{ const i = $('#lawSearch'); if(i) i.focus(); }, 40);
}

function closeLaws(){
  const m = $('#lawMask');
  if(!m) return;
  m.classList.remove('show');
  m.hidden = true;
  document.body.style.overflow = '';
}

function lawsIsOpen(){
  const m = $('#lawMask');
  return !!(m && !m.hidden);
}

/* ---------- 目录加载 ---------- */

function lawFlushPending(){
  if(LAW_PENDING == null) return;
  const n = LAW_PENDING;
  LAW_PENDING = null;
  lawOpenItem(n);
}

function lawEnsureIndex(){
  if(LAWS){ lawRenderSide(); lawFlushPending(); return; }

  if(location.protocol === 'file:'){
    $('#lawList').innerHTML =
      '<div class="law-err" style="margin:12px">' +
      '法规库需要通过本地服务器打开（直接双击 index.html 时浏览器禁止读取本地 JSON）。<br>' +
      '请运行同目录下的「启动本机服务器.ps1」，再访问提示的地址。</div>';
    $('#lawCats').innerHTML = '';
    $('#lawCount').textContent = '';
    return;
  }

  $('#lawList').innerHTML = '<div class="law-loading" style="padding:16px 12px">正在加载目录…</div>';

  fetch(LAWS_URL, {cache:'no-cache'})
    .then(r => { if(!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(j => { LAWS = j; lawRenderSide(); lawFlushPending(); })
    .catch(err => {
      $('#lawList').innerHTML =
        '<div class="law-err" style="margin:12px">目录加载失败：<code>' + lawEsc(err.message) + '</code><br>' +
        '请确认 <code>' + LAWS_URL + '</code> 存在，并通过本地服务器访问。</div>';
    });
}

/* ---------- 左侧渲染 ---------- */

function lawCatName(id){
  const c = (LAWS.cats || []).find(c => c.id === id);
  return c ? c.name : id;
}

function lawFiltered(){
  const q = LAW_Q.trim().toLowerCase();
  return (LAWS.items || []).filter(it => {
    if(LAW_CAT !== 'all' && it.cat !== LAW_CAT) return false;
    if(!q) return true;
    const hay = (it.title + ' ' + (it.docNo||'') + ' ' + (it.org||'') + ' ' + (it.date||'')).toLowerCase();
    return hay.indexOf(q) >= 0;
  });
}

function lawRenderSide(){
  const sub = $('#lawSub');
  if(sub){
    sub.textContent = LAWS.count + ' 份 · 更新于 ' + (LAWS.updated || '—');
  }

  /* 类别筛选条 */
  const cats = [{id:'all', name:'全部'}].concat(LAWS.cats || []);
  $('#lawCats').innerHTML = cats.map(c => {
    const on = c.id === LAW_CAT ? ' on' : '';
    const n = c.id === 'all'
      ? (LAWS.items||[]).length
      : (LAWS.items||[]).filter(i => i.cat === c.id).length;
    return '<button class="law-cat' + on + '" data-cat="' + c.id + '" role="tab" ' +
           'aria-selected="' + (c.id === LAW_CAT) + '">' + lawEsc(c.name) + ' ' + n + '</button>';
  }).join('');

  /* 目录列表：「全部」视图按类别定义顺序 + 序号排序，否则分类标题会交错重复 */
  const list = lawFiltered();
  if(LAW_CAT === 'all'){
    const ord = {};
    (LAWS.cats || []).forEach((c, i) => { ord[c.id] = i; });
    list.sort((a, b) => ((ord[a.cat] ?? 99) - (ord[b.cat] ?? 99)) || (a.n - b.n));
  }
  $('#lawCount').textContent = list.length + ' / ' + (LAWS.items||[]).length;

  if(!list.length){
    $('#lawList').innerHTML = '<div class="law-loading" style="padding:22px 12px;text-align:center">没有匹配的文件</div>';
    return;
  }

  let html = '', lastCat = null;
  for(const it of list){
    if(LAW_CAT === 'all' && it.cat !== lastCat){
      lastCat = it.cat;
      html += '<div class="law-grp">' + lawEsc(lawCatName(it.cat)) + '</div>';
    }
    const on = (LAW_CUR && LAW_CUR.n === it.n) ? ' on' : '';
    const flag = it.status === 'partial'
      ? '<span class="law-flag" title="' + lawEsc(it.note || '部分公开') + '">部分</span>'
      : '';
    html += '<button class="law-item' + on + '" data-n="' + it.n + '">' +
              '<span class="n">' + it.n + '</span>' +
              '<span class="t">' + lawEsc(it.title) + '<small>' +
                lawEsc(it.docNo || it.org || '') +
              '</small></span>' + flag +
            '</button>';
  }
  $('#lawList').innerHTML = html;
}

/* ---------- 打开一份文件 ---------- */

function lawOpenItem(n){
  const it = (LAWS.items || []).find(i => i.n === n);
  if(!it) return;

  LAW_CUR = it;
  lawRenderSide();

  $('#lawEmpty').hidden = true;
  $('#lawDoc').hidden = false;
  $('#lawDocTitle').textContent = it.title;
  $('#lawPos').textContent = '';
  $('#lawJump').value = '';

  /* 元信息胶囊 */
  const meta = [];
  if(it.org)    meta.push({t: it.org});
  if(it.docNo)  meta.push({t: it.docNo});
  if(it.date)   meta.push({t: it.date});
  if(it.effective) meta.push({t: it.effective});
  meta.push({t: (it.chars||0).toLocaleString('zh-CN') + ' 字'});
  if(it.status === 'partial') meta.push({t: '部分公开', w: true});
  $('#lawDocMeta').innerHTML = meta.map(m =>
    '<span' + (m.w ? ' class="warn"' : '') + '>' + lawEsc(m.t) + '</span>').join('');

  /* 原文出处 / PDF */
  const a = $('#lawSrc');
  a.href = it.url || '#';
  a.textContent = '原文出处（' + (it.urlName || '来源') + '）↗';
  const p = $('#lawPdf');
  if(it.pdf){ p.hidden = false; p.href = LAWS_DIR + it.pdf; }
  else { p.hidden = true; p.removeAttribute('href'); }

  /* 提示条 */
  const old = $('#lawDoc .law-note');
  if(old) old.remove();
  if(it.note){
    const d = document.createElement('div');
    d.className = 'law-note';
    d.textContent = it.note;
    $('#lawDocTitle').after(d);
  }

  const body = $('#lawBody');
  if(LAW_MD[it.file] != null){ body.innerHTML = lawMd2html(LAW_MD[it.file]); return; }

  body.innerHTML = '<div class="law-loading">正在加载正文…</div>';

  fetch(LAWS_DIR + it.file, {cache:'force-cache'})
    .then(r => { if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(txt => {
      LAW_MD[it.file] = txt;
      /* 用户可能在加载期间已经切到别处 */
      if(!LAW_CUR || LAW_CUR.n !== it.n) return;
      body.innerHTML = lawMd2html(txt);
    })
    .catch(err => {
      body.innerHTML =
        '<div class="law-err">正文加载失败：<code>' + lawEsc(err.message) + '</code><br>' +
        '请确认 <code>' + LAWS_DIR + it.file + '</code> 存在。' +
        (err.message.indexOf('HTTP 404') >= 0
          ? '<br>若已开启 Service Worker 且当前离线，该文件尚未被缓存过，需联网打开一次。'
          : '') + '</div>';
    });
}

/* ---------- 文内查找 / 条文定位 ---------- */

function lawFindInDoc(q){
  const body = $('#lawBody');
  const pos  = $('#lawPos');
  body.querySelectorAll('mark').forEach(m => {
    const p = m.parentNode;
    while(m.firstChild) p.insertBefore(m.firstChild, m);
    m.remove();
    p.normalize();
  });
  body.querySelectorAll('.hit').forEach(e => e.classList.remove('hit'));

  q = (q || '').trim();
  if(q.length < 2){ pos.textContent = ''; return; }

  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let nd;
  while((nd = walker.nextNode())) if(nd.nodeValue && nd.nodeValue.indexOf(q) >= 0) nodes.push(nd);

  if(!nodes.length){ pos.textContent = '未找到「' + q + '」'; return; }

  let count = 0, first = null;
  for(const node of nodes){
    const frag = document.createDocumentFragment();
    let rest = node.nodeValue;
    for(;;){
      const i = rest.indexOf(q);
      if(i < 0){ frag.appendChild(document.createTextNode(rest)); break; }
      if(i > 0) frag.appendChild(document.createTextNode(rest.slice(0, i)));
      const mk = document.createElement('mark');
      mk.textContent = q;
      frag.appendChild(mk);
      count++;
      if(!first) first = mk;
      rest = rest.slice(i + q.length);
    }
    node.parentNode.replaceChild(frag, node);
  }

  pos.textContent = '找到 ' + count + ' 处';
  if(first){
    let blk = first;
    while(blk && blk !== body && blk.parentNode !== body) blk = blk.parentNode;
    if(blk && blk !== body) blk.classList.add('hit');
    first.scrollIntoView({block:'center', behavior:'smooth'});
  }
}

/* ---------- 事件绑定 ---------- */

function lawBind(){
  const m = $('#lawMask');
  if(!m) return;

  $('#btnLaws').onclick = () => lawsIsOpen() ? closeLaws() : openLaws();
  $('#lawClose').onclick = closeLaws;
  /* 点遮罩关闭，点面板内不关（与席位档案弹窗一致） */
  m.onclick = e => { if(e.target === m) closeLaws(); };

  $('#lawCats').onclick = e => {
    const b = e.target.closest('.law-cat');
    if(!b) return;
    LAW_CAT = b.dataset.cat;
    lawRenderSide();
  };

  $('#lawList').onclick = e => {
    const b = e.target.closest('.law-item');
    if(!b) return;
    lawOpenItem(parseInt(b.dataset.n, 10));
  };

  /* ---- 扫码深链 ----
     二维码形如 .../director/?key=XXX&law=9 或 ...&key=XXX&cat=doc。
     key 由线上门禁读取并校验，此处只认 law / cat 两个参数。 */
  let deepOpen = false;
  try{
    const q = new URLSearchParams(location.search);
    const cat = q.get('cat');
    const law = q.get('law');
    if(cat){ LAW_CAT = cat; deepOpen = true; }
    if(law != null){
      // 只要是 law 参数（哪怕格式错/越界）都展开目录，避免扫码后页面毫无反应；
      // 合法 1..20 才进一步自动打开该篇。
      deepOpen = true;
      const n = parseInt(law, 10);
      if(n >= 1) LAW_PENDING = n;
    }
  }catch(e){}
  if(deepOpen){ LAW_DEEP = true; openLaws({focus:false}); }

  let t = null;
  $('#lawSearch').oninput = e => {
    LAW_Q = e.target.value;
    clearTimeout(t);
    t = setTimeout(lawRenderSide, 120);
  };
  /* 回车直接打开第一条命中 */
  $('#lawSearch').onkeydown = e => {
    if(e.key !== 'Enter') return;
    const list = lawFiltered();
    if(list.length) lawOpenItem(list[0].n);
  };

  let t2 = null;
  const jump = $('#lawJump');
  jump.oninput = e => { clearTimeout(t2); t2 = setTimeout(() => lawFindInDoc(e.target.value), 200); };
  jump.onkeydown = e => { if(e.key === 'Enter'){ clearTimeout(t2); lawFindInDoc(e.target.value); } };
}

