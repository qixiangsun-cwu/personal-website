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
 * 两个必须知道的事：
 *   - HUD 默认是 DOM 元素，**沉浸式 VR 模式下看不见**（WebXR 只渲染 3D 场景）。
 *     所以本探针会**自动再挂一块"场景内面板"**：只要你的页面把 three 的 scene / camera
 *     挂在 window 上（window.scene / window.camera，或 window.__scene / window.__camera），
 *     它就把同一份读数画成一块平面贴在视线下方，进 VR 低头就能看到。
 *     （做法来自课程里的《太和殿 · 故宫沉浸式场景》A-Frame 工程 buildPanel()。）
 *   - 它不会改你的场景，也不会接管渲染循环；它只观察（外加一块自己加的提示平面）。
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

  function statusLines(withErrors) {
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
    return lines;
  }

  function render(withErrors) {
    if (body) body.textContent = statusLines(withErrors).join('\n');
    update3DPanel();
  }

  /* =======================================================================
   * 3D 场景内面板 —— 因为在沉浸式 VR 里浏览器不渲染 DOM，DOM 面板看不见。
   * 做法参考课程里的《太和殿 · 故宫沉浸式场景》那份 A-Frame 工程
   * （它的 index.html 里写着："真正的控制台是做进 3D 场景里的，见 app.js 的 buildPanel()"）。
   * 这里做成通用版：自动找 window.scene / window.camera / window.THREE，
   * 找到就把一块 CanvasTexture 平面挂在相机上，位置在视线下方。
   * 找不到就什么都不做（DOM 面板照旧）。
   * ======================================================================= */
  var P3D = { canvas: null, ctx: null, tex: null, mesh: null, scene: null, camera: null, T: null, ok: false };

  function findSceneAndCamera() {
    var T = window.THREE;
    if (!T) return false;
    var cands = [
      [window.scene, window.camera],
      [window.__scene, window.__camera],
      [window.scene3d, window.camera3d]
    ];
    for (var i = 0; i < cands.length; i++) {
      if (cands[i][0] && cands[i][1]) { P3D.scene = cands[i][0]; P3D.camera = cands[i][1]; P3D.T = T; return true; }
    }
    // 退一步：找 renderer 然后把相机挂上去 + 让调用方自己给 scene
    var r = findRenderer();
    if (r && window.__VRPROBE_SCENE && r.xr) { P3D.scene = window.__VRPROBE_SCENE; P3D.camera = window.__VRPROBE_CAMERA || null; P3D.T = T; return !!P3D.camera; }
    return false;
  }

  function setup3D() {
    if (!findSceneAndCamera()) return;
    var T = P3D.T, W = 640, H = 320;
    var cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    P3D.canvas = cv; P3D.ctx = cv.getContext('2d');
    P3D.tex = new T.CanvasTexture(cv);
    var mat = new T.MeshBasicMaterial({ map: P3D.tex, transparent: true, depthTest: false, depthWrite: false, fog: false });
    P3D.mesh = new T.Mesh(new T.PlaneGeometry(0.76, 0.38), mat);
    P3D.mesh.position.set(0, -0.34, -1.1);   // 视线下方，低头就能看到
    P3D.mesh.renderOrder = 999;
    P3D.mesh.visible = false;                // 只在 VR 会话里显示
    try { P3D.camera.add(P3D.mesh); } catch (e) { return; }
    P3D.ok = true;
    update3DPanel();
  }

  function update3DPanel() {
    if (!P3D.ok) return;
    var inXR = !!(session);
    P3D.mesh.visible = inXR;
    if (!inXR) return;
    var g = P3D.ctx, W = P3D.canvas.width, H = P3D.canvas.height;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(8,14,22,0.86)';
    g.fillRect(0, 0, W, H);
    g.strokeStyle = 'rgba(120,190,255,0.55)';
    g.lineWidth = 4; g.strokeRect(2, 2, W - 4, H - 4);
    g.fillStyle = '#9fd0ff';
    g.font = 'bold 30px "Microsoft YaHei",sans-serif';
    g.fillText('VR 自检探针（场景内）', 20, 46);
    g.font = '24px Consolas,"Microsoft YaHei",monospace';
    var lines = statusLines(true);
    var y = 90;
    for (var i = 0; i < lines.length && y < H - 16; i++) {
      g.fillStyle = (lines[i].indexOf('⚠') === 0 || lines[i].indexOf('❌') >= 0) ? '#ff9d80' : '#e9f2ff';
      g.fillText(lines[i].slice(0, 44), 20, y);
      y += 34;
    }
    P3D.tex.needsUpdate = true;
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
    /* 调试用：场景内面板有没有挂上（学生在头显里看不到面板时，先用它排查） */
    p3d: function () {
      return { ok: P3D.ok, visible: !!(P3D.ok && P3D.mesh && P3D.mesh.visible),
               hasScene: !!P3D.scene, hasCamera: !!P3D.camera,
               hint: P3D.ok ? '场景内面板已就绪（进入 VR 后低头可见）'
                            : '未找到 window.scene / window.camera（或 window.__scene / window.__camera），只能在 2D 模式看 DOM 面板' };
    },
    toggle: function () { collapsed = !collapsed; if (body) body.style.display = collapsed ? 'none' : 'block'; },
    note: function (s) { window.VRProbe.__note = s; render(); },
    hide: function () { if (box) box.style.display = 'none'; },
    show: function () { if (box) box.style.display = 'block'; },
    __note: ''
  };

  build();
  // three 的场景对象可能晚于本脚本创建，稍后重试几次
  (function trySetup3D(n) {
    setup3D();
    if (!P3D.ok && n > 0) setTimeout(function () { trySetup3D(n - 1); }, 1200);
  })(4);
  requestAnimationFrame(tick);
  setInterval(render, 2000);
})();
