/* =============================================================================
 * vr-probe.js —— 给学生自己的 VR 页面用的"随身自检探针"
 *
 * 用法（一行）：
 *     <script src="vr-probe.js"></script>
 * 放在你自己页面的 <head> 或 </body> 之前都行。它会：
 *   ① 捕获 JS 报错与未处理的 Promise 异常，显示在角落里；
 *   ② 显示四项 WebXR 前提（安全上下文 / navigator.xr / immersive-vr / 是否在 VR 会话中）；
 *   ③ 显示 FPS；
 *   ④ 如果页面里能找到 three.js 的 renderer，顺带显示 DrawCall 与三角面；
 *   ⑤ 提供 window.VRProbe.report() 返回一段可复制的文本报告。
 *
 * 两个必须知道的限制：
 *   - HUD 是 DOM 元素，**沉浸式 VR 模式下看不见**（WebXR 只渲染 3D 场景）。
 *     所以在头显里请用投屏/镜像看这个面板，或者在 2D 模式下看。
 *   - 它不会改你的场景，也不会接管渲染循环；它只观察。
 * ========================================================================== */
(function () {
  'use strict';

  var errors = [];
  var session = null;
  var sessionCount = 0, sessionSeconds = 0, sessionStartAt = 0;
  var fps = 0, frames = 0, fpsClock = (performance && performance.now) ? performance.now() : Date.now();

  /* ---------------------------------------------------------------- 错误捕获 */
  window.addEventListener('error', function (e) {
    errors.push((e.message || 'error') + ' @' + (e.lineno || '?') + ':' + (e.colno || '?'));
    if (errors.length > 6) errors.shift();
    render(true);
  });
  window.addEventListener('unhandledrejection', function (e) {
    errors.push('未处理的 Promise 异常：' + ((e.reason && e.reason.message) || e.reason));
    if (errors.length > 6) errors.shift();
    render(true);
  });

  /* --------------------------------------------------- 捕获 WebXR 会话（不改行为） */
  if (navigator.xr && navigator.xr.requestSession) {
    var orig = navigator.xr.requestSession.bind(navigator.xr);
    navigator.xr.requestSession = function (mode, init) {
      return orig(mode, init).then(function (s) {
        session = s; sessionCount++;
        sessionStartAt = (performance && performance.now) ? performance.now() : Date.now();
        try {
          s.addEventListener('sessionend', function () {
            var now = (performance && performance.now) ? performance.now() : Date.now();
            sessionSeconds = (now - sessionStartAt) / 1000;
            session = null;
            render(true);
          });
        } catch (e) {}
        render(true);
        return s;
      });
    };
  }

  /* ------------------------------------------------------------------- FPS 统计 */
  function tick() {
    frames++;
    var now = (performance && performance.now) ? performance.now() : Date.now();
    if (now - fpsClock >= 500) {
      fps = frames * 1000 / (now - fpsClock);
      frames = 0; fpsClock = now;
      render();
    }
    requestAnimationFrame(tick);
  }

  /* -------------------------------------------------------------- 找 three 的 renderer */
  function findRenderer() {
    var cands = [window.renderer, window.__renderer, window._renderer];
    for (var i = 0; i < cands.length; i++) {
      var r = cands[i];
      if (r && r.info && r.info.render) return r;
    }
    // 再退一步：从已有 canvas 反查（three 的 renderer 会把自己挂在 domElement 上）
    var cs = document.querySelectorAll('canvas');
    for (var j = 0; j < cs.length; j++) {
      var p = cs[j].__threeRenderer;
      if (p && p.info && p.info.render) return p;
    }
    return null;
  }

  /* ---------------------------------------------------------------------- HUD */
  var box = null, body = null, collapsed = false;

  function build() {
    box = document.createElement('div');
    box.id = 'vr-probe-hud';
    box.setAttribute('style', [
      'position:fixed', 'right:10px', 'top:10px', 'z-index:2147483000',
      'max-width:320px', 'font:12px/1.65 Consolas,Menlo,"Microsoft YaHei",monospace',
      'color:#e9f2ff', 'background:rgba(8,14,22,.86)',
      'border:1px solid rgba(120,190,255,.35)', 'border-radius:10px',
      'padding:8px 10px', 'pointer-events:auto', 'white-space:pre-wrap',
      'box-shadow:0 6px 22px rgba(0,0,0,.45)', 'backdrop-filter:blur(4px)'
    ].join(';'));

    var head = document.createElement('div');
    head.setAttribute('style', 'display:flex;justify-content:space-between;gap:8px;align-items:center;cursor:pointer');
    head.innerHTML = '<b style="color:#9fd0ff">VR 自检探针</b>';
    var btn = document.createElement('span');
    btn.textContent = '收起';
    btn.setAttribute('style', 'color:#8ab4d8;font-size:11px');
    head.appendChild(btn);

    body = document.createElement('div');
    body.setAttribute('style', 'margin-top:6px');

    head.onclick = function () {
      collapsed = !collapsed;
      body.style.display = collapsed ? 'none' : 'block';
      btn.textContent = collapsed ? '展开' : '收起';
    };
    box.appendChild(head);
    box.appendChild(body);

    function attach() {
      if (document.body) { document.body.appendChild(box); render(); }
      else setTimeout(attach, 60);
    }
    attach();
  }

  function yn(ok) { return ok ? '✅' : '❌'; }

  function render(withErrors) {
    if (!body) return;
    var r = findRenderer();
    var lines = [];
    lines.push(yn(window.isSecureContext === true) + ' 安全上下文　' + location.protocol);
    lines.push(yn(typeof navigator.xr !== 'undefined') + ' navigator.xr');
    lines.push('　 immersive-vr：' + STATUS.immersive);
    lines.push((session ? '★' : yn(false)) + ' VR 会话：' + (session ? '进行中' : '未进入')
      + (sessionCount ? '（历史 ' + sessionCount + ' 次，上次 ' + sessionSeconds.toFixed(1) + ' 秒）' : ''));
    lines.push('　 FPS：' + (fps > 0 ? fps.toFixed(0) : '—')
      + (r ? '　DrawCall：' + r.info.render.calls + '　三角面：' + r.info.render.triangles : '　（未找到 three 的 renderer，仅显示 FPS）'));
    if (window.VRProbe && window.VRProbe.__note) lines.push('　备注：' + window.VRProbe.__note);
    if (withErrors || errors.length) {
      lines.push('');
      lines.push(errors.length ? ('⚠ 报错 ' + errors.length + ' 条：') : '✅ 暂无 JS 报错');
      for (var i = 0; i < errors.length; i++) lines.push('　· ' + errors[i]);
    }
    body.textContent = lines.join('\n');
  }

  var STATUS = { immersive: '检测中…' };
  if (navigator.xr && navigator.xr.isSessionSupported) {
    navigator.xr.isSessionSupported('immersive-vr').then(function (ok) {
      STATUS.immersive = ok ? '支持' : '不支持';
      render();
    }).catch(function (e) { STATUS.immersive = '检测异常'; render(); });
  } else {
    STATUS.immersive = '无法检测（不是安全上下文时属正常）';
  }

  /* --------------------------------------------------------------- 对外接口 */
  window.VRProbe = {
    report: function () {
      var r = findRenderer();
      var t = [];
      t.push('【VR 探针报告】' + new Date().toLocaleString('zh-CN'));
      t.push('页面：' + location.href);
      t.push('协议：' + location.protocol + '　安全上下文：' + (window.isSecureContext === true ? '是' : '否'));
      t.push('navigator.xr：' + (typeof navigator.xr !== 'undefined' ? '存在' : '不存在'));
      t.push('immersive-vr：' + STATUS.immersive);
      t.push('VR 会话次数：' + sessionCount + '　上次时长：' + sessionSeconds.toFixed(1) + ' 秒');
      t.push('FPS：' + (fps > 0 ? fps.toFixed(0) : '—')
        + (r ? '　DrawCall：' + r.info.render.calls + '　三角面：' + r.info.render.triangles
             : '　（未找到 three 的 renderer）'));
      t.push('JS 报错：' + (errors.length ? errors.join(' | ') : '无'));
      t.push('UA：' + navigator.userAgent);
      return t.join('\n');
    },
    errors: function () { return errors.slice(); },
    toggle: function () { collapsed = !collapsed; if (body) body.style.display = collapsed ? 'none' : 'block'; },
    note: function (s) { window.VRProbe.__note = s; render(); },
    hide: function () { if (box) box.style.display = 'none'; },
    show: function () { if (box) box.style.display = 'block'; },
    __note: ''
  };

  build();
  requestAnimationFrame(tick);
  setInterval(render, 2000);
})();
