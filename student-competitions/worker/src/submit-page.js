const SUBMIT_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>提交参赛作品 · 孙琪翔学生比赛作品集</title>
<meta name="robots" content="noindex">
<link rel="icon" type="image/svg+xml" href="/static/lingting-mark.svg">
<style>
:root{
  color-scheme: light dark;
  --color-paper: oklch(97% 0.005 85);
  --color-paper-2: oklch(94% 0.006 85);
  --color-surface: oklch(98% 0.003 85);
  --color-rule: oklch(88% 0.005 85);
  --color-muted: oklch(52% 0.006 85);
  --color-ink: oklch(22% 0.008 80);
  --color-ink-2: oklch(38% 0.008 80);
  --color-accent: oklch(50% 0.18 270);
  --color-accent-soft: oklch(50% 0.18 270 / 0.12);
  --color-accent-border: oklch(50% 0.18 270 / 0.35);
  --color-focus: oklch(52% 0.19 270);
  --glass-bg: oklch(100% 0.001 85 / 0.52);
  --glass-border: oklch(100% 0.001 85 / 0.55);
  --glass-blur: 24px;
  --glass-shadow: 0 8px 32px oklch(20% 0.01 270 / 0.09), inset 0 1px 0 oklch(100% 0 0 / 0.60), inset 0 -1px 0 oklch(100% 0 0 / 0.12);
  --font-body: 'Noto Sans SC','Source Han Sans SC','PingFang SC','Microsoft YaHei',system-ui,sans-serif;
  --font-outlier: 'JetBrains Mono','Consolas',monospace;
  --tone-bad-fg: oklch(50% 0.16 25);
  --tone-bad-bg: oklch(55% 0.15 25 / 0.14);
  --tone-ok-fg: oklch(48% 0.14 160);
  --tone-ok-bg: oklch(55% 0.12 160 / 0.14);
}
@media (prefers-color-scheme: dark){
  :root{
    --color-paper: oklch(20% 0.008 80);
    --color-paper-2: oklch(24% 0.008 80);
    --color-surface: oklch(26% 0.008 80);
    --color-rule: oklch(34% 0.008 80);
    --color-muted: oklch(68% 0.006 85);
    --color-ink: oklch(94% 0.004 85);
    --color-ink-2: oklch(80% 0.005 85);
    --color-accent: oklch(72% 0.15 270);
    --color-accent-soft: oklch(72% 0.15 270 / 0.16);
    --color-accent-border: oklch(72% 0.15 270 / 0.4);
    --glass-bg: oklch(30% 0.008 80 / 0.55);
    --glass-border: oklch(100% 0 0 / 0.14);
    --glass-shadow: 0 8px 32px oklch(0% 0 0 / 0.35), inset 0 1px 0 oklch(100% 0 0 / 0.10);
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; padding:clamp(1.5rem,4vw,3.5rem) clamp(1rem,4vw,2rem) 4rem;
  background:var(--color-paper); color:var(--color-ink);
  font-family:var(--font-body); font-size:1rem; line-height:1.62;
  min-height:100vh;
  background-image:radial-gradient(60rem 40rem at 15% -10%, oklch(50% 0.18 270 / 0.10), transparent 60%),
                   radial-gradient(50rem 34rem at 90% 0%, oklch(60% 0.14 340 / 0.08), transparent 60%);
  background-repeat:no-repeat;
}
.wrap{max-width:46rem;margin:0 auto}
.crumb{font-size:.8125rem;color:var(--color-muted);margin-bottom:1.25rem}
.crumb a{color:var(--color-accent);text-decoration:none}
.crumb a:hover{text-decoration:underline}
.head{display:flex;gap:1rem;align-items:center;margin-bottom:.75rem}
.head img{width:3.25rem;height:3.25rem;flex:none;border-radius:.9rem}
h1{font-size:clamp(1.5rem,4.5vw,2.1rem);line-height:1.25;margin:0;letter-spacing:-.01em}
.sub{color:var(--color-ink-2);margin:.35rem 0 0;font-size:.9375rem}
.card{
  background:var(--glass-bg); border:1px solid var(--glass-border);
  border-radius:1.25rem; padding:clamp(1.1rem,3vw,1.75rem);
  box-shadow:var(--glass-shadow);
  -webkit-backdrop-filter:blur(var(--glass-blur)) saturate(1.6);
  backdrop-filter:blur(var(--glass-blur)) saturate(1.6);
  margin-top:1.5rem;
}
.field{margin-bottom:1.15rem}
.field:last-of-type{margin-bottom:0}
label{display:block;font-weight:600;font-size:.9375rem;margin-bottom:.4rem}
label .req{color:var(--tone-bad-fg);margin-left:.15rem}
.hint{font-weight:400;color:var(--color-muted);font-size:.8125rem;margin-left:.35rem}
input[type=text],input[type=url],input[type=email],textarea,select{
  width:100%; padding:.7rem .85rem; font:inherit; font-size:.9375rem;
  color:var(--color-ink); background:var(--color-surface);
  border:1px solid var(--color-rule); border-radius:.7rem;
  outline:none; transition:border-color .15s, box-shadow .15s;
}
textarea{min-height:6.5rem;resize:vertical;line-height:1.6}
input:focus,textarea:focus,select:focus{
  border-color:var(--color-accent);
  box-shadow:0 0 0 3px var(--color-accent-soft);
}
.row{display:grid;gap:1.15rem;grid-template-columns:1fr}
@media (min-width:40rem){ .row.two{grid-template-columns:1fr 1fr} }
.actions{display:flex;gap:.75rem;align-items:center;margin-top:1.5rem;flex-wrap:wrap}
button{
  font:inherit; font-weight:600; font-size:.9375rem; cursor:pointer;
  padding:.75rem 1.6rem; border-radius:.75rem; border:1px solid transparent;
  background:var(--color-accent); color:oklch(98% 0.002 270);
  transition:filter .15s, transform .12s;
}
button:hover:not(:disabled){filter:brightness(1.08)}
button:active:not(:disabled){transform:translateY(1px)}
button:disabled{opacity:.55;cursor:progress}
button.ghost{
  background:transparent; color:var(--color-ink-2); border-color:var(--color-rule);
}
.msg{margin-top:1rem;padding:.8rem 1rem;border-radius:.7rem;font-size:.9rem;display:none}
.msg.show{display:block}
.msg.err{background:var(--tone-bad-bg);color:var(--tone-bad-fg)}
.msg.ok{background:var(--tone-ok-bg);color:var(--tone-ok-fg)}
.foot{margin-top:2rem;font-size:.8125rem;color:var(--color-muted);line-height:1.7}
.foot a{color:var(--color-accent);text-decoration:none}
.foot a:hover{text-decoration:underline}
.done{text-align:center;padding:1rem 0}
.done .big{font-size:2.4rem;line-height:1;margin-bottom:.75rem}
.done h2{margin:.2rem 0 .5rem;font-size:1.3rem}
.done p{color:var(--color-ink-2);margin:.4rem 0}
code{font-family:var(--font-outlier);font-size:.85em;background:var(--color-accent-soft);padding:.1em .4em;border-radius:.3em}
</style>
</head>
<body>
<div class="wrap">
  <p class="crumb"><a href="https://www.qixiangsun.com.cn/student-competitions">← 学生比赛作品集</a></p>
  <div class="head">
    <img src="/static/lingting-mark.svg" alt="">
    <div>
      <h1>提交参赛作品</h1>
      <p class="sub">填写作品信息后提交，指导教师审核通过后会在作品集页面展示。</p>
    </div>
  </div>

  <form id="f" class="card" novalidate>
    <div class="row two">
      <div class="field">
        <label for="team">团队名称<span class="req">*</span></label>
        <input id="team" name="team" type="text" maxlength="40" placeholder="例如：智安小队" required>
      </div>
      <div class="field">
        <label for="school">所在学校<span class="req">*</span></label>
        <input id="school" name="school" type="text" maxlength="40" placeholder="例如：中华女子学院" required>
      </div>
    </div>

    <div class="row two">
      <div class="field">
        <label for="competition">赛事名称<span class="req">*</span></label>
        <input id="competition" name="competition" type="text" maxlength="80" placeholder="例如：2026 华北五省大学生计算机应用大赛" required>
      </div>
      <div class="field">
        <label for="track">赛道 / 组别<span class="hint">选填</span></label>
        <input id="track" name="track" type="text" maxlength="40" placeholder="例如：计算机应用赛道">
      </div>
    </div>

    <div class="field">
      <label for="title">作品名称<span class="req">*</span></label>
      <input id="title" name="title" type="text" maxlength="60" placeholder="例如：智安守护" required>
    </div>

    <div class="field">
      <label for="url">作品链接<span class="req">*</span></label>
      <input id="url" name="url" type="url" maxlength="300" placeholder="https://" required>
      <p class="hint" style="margin:.35rem 0 0">可填写在线演示地址、仓库地址或网盘分享链接（需保持可访问）。</p>
    </div>

    <div class="field">
      <label for="desc">作品简介<span class="req">*</span></label>
      <textarea id="desc" name="desc" maxlength="600" placeholder="简要说明作品解决的问题、核心功能与亮点（建议 100–300 字）" required></textarea>
      <p class="hint" id="cnt" style="margin:.35rem 0 0">0 / 600</p>
    </div>

    <div class="row two">
      <div class="field">
        <label for="members">团队成员<span class="hint">选填</span></label>
        <input id="members" name="members" type="text" maxlength="120" placeholder="多人用顿号分隔">
      </div>
      <div class="field">
        <label for="contact">联系方式<span class="hint">选填</span></label>
        <input id="contact" name="contact" type="text" maxlength="60" placeholder="手机 / 微信 / 邮箱">
      </div>
    </div>

    <div class="actions">
      <button type="submit" id="btn">提交作品</button>
      <button type="button" class="ghost" id="reset">清空</button>
    </div>
    <div class="msg" id="msg" role="status" aria-live="polite"></div>
  </form>

  <p class="foot">
    提交即表示同意本站展示该作品信息。如需修改或撤下，请联系指导教师。<br>
    遇到问题可返回 <a href="https://www.qixiangsun.com.cn/student-competitions">作品集首页</a>。
  </p>
</div>

<script>
(function(){
  var f = document.getElementById('f');
  var btn = document.getElementById('btn');
  var msg = document.getElementById('msg');
  var desc = document.getElementById('desc');
  var cnt = document.getElementById('cnt');

  desc.addEventListener('input', function(){
    cnt.textContent = desc.value.length + ' / 600';
  });

  function show(kind, text){
    msg.className = 'msg show ' + kind;
    msg.textContent = text;
  }
  function hide(){ msg.className = 'msg'; msg.textContent = ''; }

  document.getElementById('reset').addEventListener('click', function(){
    f.reset(); hide(); cnt.textContent = '0 / 600';
  });

  function val(id){ return (document.getElementById(id).value || '').trim(); }

  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    hide();

    var data = {
      team: val('team'),
      school: val('school'),
      competition: val('competition'),
      track: val('track'),
      title: val('title'),
      url: val('url'),
      desc: val('desc'),
      members: val('members'),
      contact: val('contact')
    };

    var need = [
      ['team','团队名称'], ['school','所在学校'], ['competition','赛事名称'],
      ['title','作品名称'], ['url','作品链接'], ['desc','作品简介']
    ];
    for (var i = 0; i < need.length; i++) {
      if (!data[need[i][0]]) { show('err', '请填写「' + need[i][1] + '」'); document.getElementById(need[i][0]).focus(); return; }
    }
    if (!/^https?:\\/\\//i.test(data.url)) {
      show('err', '作品链接需要以 http:// 或 https:// 开头');
      document.getElementById('url').focus();
      return;
    }
    if (data.desc.length < 10) {
      show('err', '作品简介请至少填写 10 个字');
      document.getElementById('desc').focus();
      return;
    }

    btn.disabled = true;
    btn.textContent = '提交中…';

    fetch('/api/submit', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(data)
    })
    .then(function(r){ return r.json().then(function(j){ return { ok: r.ok, body: j }; }); })
    .then(function(res){
      if (!res.ok || !res.body || !res.body.ok) {
        throw new Error((res.body && res.body.error) || '提交失败');
      }
      f.innerHTML = '<div class="done">' +
        '<div class="big">✅</div>' +
        '<h2>提交成功</h2>' +
        '<p>作品「' + data.title + '」已收到，编号 <code>' + res.body.id + '</code>。</p>' +
        '<p>指导教师审核后会展示在作品集页面。</p>' +
        '</div>';
    })
    .catch(function(err){
      show('err', '提交失败：' + (err && err.message ? err.message : '网络异常，请稍后重试'));
      btn.disabled = false;
      btn.textContent = '提交作品';
    });
  });
})();
</script>
</body>
</html>
`;

export { SUBMIT_HTML };
