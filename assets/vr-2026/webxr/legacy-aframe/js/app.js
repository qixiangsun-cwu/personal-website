/* ============================================================
   app.js — 光照、交互、画质控制

   三个关键性能决策（详见 PERFORMANCE.md）：
   1. 静态场景 -> 关掉阴影贴图的每帧自动更新，只在太阳移动时重算一次
   2. 反复出现的构件（柱/斗拱/望柱）全部用 InstancedMesh，一个构件一个 draw call
   3. 不做任何后处理（bloom / SSAO / 抗锯齿着色器），移动 XR 上性价比极低
   ============================================================ */

(function () {
  'use strict';

  var sceneEl = null, rig = null, camEl = null, sunEl = null, hemiEl = null;
  var marker = null, state = { hour: 14, quality: 'medium', view: 'far' };

  /* ================= 时段与光照 ================= */

  var TIME_PRESETS = { dawn: 6.4, noon: 12.5, dusk: 17.9, night: 21.0 };
  var TIME_LABEL = { dawn: '晨曦', noon: '正午', dusk: '黄昏', night: '夜色' };

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smooth(t) { return t * t * (3 - 2 * t); }

  function mixColor(c1, c2, t) {
    return new AFRAME.THREE.Color(lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t));
  }

  // 太阳位置：t=0 日出，t=1 日落
  function sunAngles(hour) {
    var t = (hour - 5.5) / 13.0;                 // 5:30 - 18:30 为白天
    var day = clamp(t, 0, 1);
    var elev = Math.sin(Math.PI * day) * 58;     // 最高约 58 度
    // 建筑朝南（+z），所以 +x 是东、-x 是西。太阳必须从东边升起、西边落下。
    var azim = lerp(102, -102, day);
    return { t: t, elev: (t < 0 || t > 1) ? -8 : elev, azim: azim, night: (t < 0 || t > 1) };
  }

  var SKY_TEX = null, SKY_CANVAS = null, SKY_CTX = null, SKY_MESH = null;

  function initSky() {
    SKY_CANVAS = document.createElement('canvas');
    SKY_CANVAS.width = 8; SKY_CANVAS.height = 256;
    SKY_CTX = SKY_CANVAS.getContext('2d');
    SKY_TEX = new AFRAME.THREE.CanvasTexture(SKY_CANVAS);
    SKY_TEX.colorSpace = AFRAME.THREE.SRGBColorSpace;
    // 直接用 three 建天空球，不走 <a-sky> 的 src/#asset 加载流程。
    // 两个坑：① A-Frame 默认天空球半径 5000，而相机 far 只有 600，会被远裁面切掉；
    //         ② 场景 loaded 之后再挂 <a-sky>，它的 loaded 事件可能已经错过，纹理接不上，
    //            结果就是一片黑。自己 new Mesh 没有这些时序问题。
    var T = AFRAME.THREE;
    SKY_MESH = new T.Mesh(
      new T.SphereGeometry(450, 24, 16),
      new T.MeshBasicMaterial({ map: SKY_TEX, side: T.BackSide, fog: false, depthWrite: false })
    );
    SKY_MESH.renderOrder = -1;
    sceneEl.object3D.add(SKY_MESH);
  }

  function paintSky(top, bottom) {
    var g = SKY_CTX.createLinearGradient(0, 0, 0, 256);
    g.addColorStop(0.00, top);
    g.addColorStop(0.55, top);
    g.addColorStop(1.00, bottom);
    SKY_CTX.fillStyle = g;
    SKY_CTX.fillRect(0, 0, 8, 256);
    SKY_TEX.needsUpdate = true;
  }

  function applyLighting(hour) {
    var a = sunAngles(hour);
    var E = a.elev * Math.PI / 180;
    var A = a.azim * Math.PI / 180;
    var R = 160;
    var y = Math.sin(E) * R;
    var horiz = Math.cos(E) * R;
    // 用字符串写法。setAttribute('position', {对象}) 在部分 A-Frame 版本下不可靠，
    // 一旦位置没生效，方向光就停在原点，方向退化 -> 阴影贴图是空的、完全看不到影子。
    sunEl.setAttribute('position',
      (Math.sin(A) * horiz).toFixed(2) + ' ' +
      Math.max(y, -20).toFixed(2) + ' ' +
      (-Math.cos(A) * horiz).toFixed(2));

    // 日高越低越暖、越弱
    var k = clamp(Math.sin(Math.max(E, 0)) / 0.85, 0, 1);
    var sunCol = mixColor([1.00, 0.58, 0.26], [1.00, 0.97, 0.90], k);
    var sunInt = a.night ? 0.0 : lerp(0.30, 1.55, Math.pow(k, 0.7));

    sunEl.setAttribute('light', 'color', '#' + sunCol.getHexString());
    sunEl.setAttribute('light', 'intensity', sunInt);

    var hemiInt = a.night ? 0.14 : lerp(0.28, 0.48, k);
    hemiEl.setAttribute('light', 'intensity', hemiInt);

    // 天空与雾保持同一色调，远景才不会「浮起来」
    var dayTop = mixColor([0.36, 0.47, 0.62], [0.42, 0.62, 0.88], k);
    var dayBot = mixColor([0.86, 0.66, 0.48], [0.82, 0.88, 0.95], k);
    if (a.night) {
      dayTop = new AFRAME.THREE.Color(0.05, 0.07, 0.14);
      dayBot = new AFRAME.THREE.Color(0.16, 0.18, 0.26);
    }
    paintSky('#' + dayTop.getHexString(), '#' + dayBot.getHexString());

    var fogCol = dayBot.clone().lerp(dayTop, 0.45);
    sceneEl.setAttribute('fog', 'color', '#' + fogCol.getHexString());

    // 太阳动了 -> 重烤阴影。时段平滑过渡期间按 ~11Hz 节流，
    // 否则 1.3 秒的过渡里要重烤七八十次，反而把帧率拖下去。
    bakeShadowsThrottled();
  }

  var lastBake = 0;
  function bakeShadowsThrottled() {
    var now = performance.now();
    if (now - lastBake < 90) { return; }
    lastBake = now;
    bakeShadows();
  }

  var anim = null;

  function setTime(key, instant) {
    var target = TIME_PRESETS[key];
    if (target === undefined) { return; }
    if (instant) { state.hour = target; applyLighting(target); return; }
    if (anim) { cancelAnimationFrame(anim); }
    var from = state.hour, t0 = performance.now(), DUR = 1300;
    (function step(now) {
      var p = clamp((now - t0) / DUR, 0, 1);
      state.hour = lerp(from, target, smooth(p));
      applyLighting(state.hour);
      if (p < 1) { anim = requestAnimationFrame(step); }
    })(t0);
  }

  /* ================= 画质档位 ================= */

  var QUALITY = {
    low:    { shadows: false, fog: 0.0055, label: '低' },
    medium: { shadows: true,  fog: 0.0038, label: '中' },
    high:   { shadows: true,  fog: 0.0030, label: '高' }
  };

  function setQuality(level) {
    var q = QUALITY[level] || QUALITY.medium;
    state.quality = level;
    sceneEl.setAttribute('fog', 'density', q.fog);
    // 低画质档直接不显示阴影贴花：省一层透明叠绘，也省掉重烤
    SHADOW.decals.forEach(function (d) {
      if (d.mesh) { d.mesh.visible = q.shadows; }
    });
  }

  /* ================= 视角预设 ================= */

  var VIEWS = {
    far:   { name: '远景', x: 0,  z: 88, yaw: 0 },
    mid:   { name: '中景', x: 0,  z: 56, yaw: 0 },
    near:  { name: '近景', x: 0,  z: 40, yaw: 0 },
    stage: { name: '台上', x: 0,  z: 22, yaw: 0 }
  };

  function gotoView(key) {
    var v = VIEWS[key];
    if (!v) { return; }
    state.view = key;
    placeRig(v.x, v.z, v.yaw);
  }

  function placeRig(x, z, yaw) {
    var h = PALACE.groundHeight(x, z);
    rig.object3D.position.set(x, h + 0.02, z);
    if (yaw !== undefined && yaw !== null) {
      rig.object3D.rotation.set(0, yaw, 0);
      // 让视线回到水平：look-controls 会覆盖朝向，但先归零更稳
      sceneEl.emit('recenter-view');
    }
  }

  /* ================= 烘焙阴影 =================
     A-Frame 的 shadow 系统会把 renderer.shadowMap 的记账状态重写掉，
     直接给 three 网格设 castShadow 拿不到稳定结果；而且对
     「静态场景 + 移动 GPU」这个组合，逐帧阴影贴图本来就是最贵、最不划算的一档。

     这里换成太阳投影烘焙：把每个构件的俯视矩形沿太阳方向拉成阴影多边形，
     一次性画进离屏画布，贴到接收面上当贴花。
       · 每帧零成本（只是一张普通贴图）
       · 太阳一动就重烤，所以时段变化、明暗过渡依然跟随
       · 分辨率自选，极端情况下也压得住
     代价：只对清单里的构件生效，是硬边+模糊，不是逐像素精确阴影。
     ponytail: 用烘焙投影替代实时阴影贴图。若将来需要镂空/细碎物体的
     精确阴影，再换回实时阴影贴图。 */

  var SHADOW = { decals: [], canvas: {}, ctx: {}, tex: {}, mask: {}, maskCtx: {} };
  var SHADOW_ALPHA = 0.30;

  // 8 点凸包（两个矩形各 4 个角）——单调链，不用手推几何
  function hull(pts) {
    pts = pts.slice().sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
    function cross(o, a, b) {
      return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    }
    var lower = [], upper = [], i;
    for (i = 0; i < pts.length; i++) {
      while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], pts[i]) <= 0) { lower.pop(); }
      lower.push(pts[i]);
    }
    for (i = pts.length - 1; i >= 0; i--) {
      while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], pts[i]) <= 0) { upper.pop(); }
      upper.push(pts[i]);
    }
    lower.pop(); upper.pop();
    return lower.concat(upper);
  }

  function bakeShadows() {
    if (!SHADOW.decals.length) { return; }
    var a = sunAngles(state.hour);
    var night = a.night;
    // 太阳贴地时阴影长度会趋于无限，把仰角下限锁住
    var elev = Math.max(a.elev, 5) * Math.PI / 180;
    var k = 1 / Math.tan(elev);
    var sx = -Math.sin(a.azim * Math.PI / 180) * k;   // 阴影延伸方向与太阳相反
    var sz = Math.cos(a.azim * Math.PI / 180) * k;
    var casters = PALACE.casters();

    SHADOW.decals.forEach(function (d) {
      var cv = SHADOW.canvas[d.name], ctx = SHADOW.ctx[d.name];
      var mk = SHADOW.mask[d.name], mctx = SHADOW.maskCtx[d.name];
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.clearRect(0, 0, mk.width, mk.height);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cv.width, cv.height);
      if (night) { SHADOW.tex[d.name].needsUpdate = true; return; }

      var s = mk.width / (d.x1 - d.x0);
      mctx.save();
      mctx.beginPath(); mctx.rect(0, 0, mk.width, mk.height); mctx.clip();
      mctx.filter = 'blur(' + Math.max(3, mk.width / 110) + 'px)';
      // 先在遮罩上画成不透明：多个构件的阴影重叠时不会把颜色越叠越深
      mctx.fillStyle = '#000';

      casters.forEach(function (c) {
        var up = c.top - d.y;
        if (up <= 0.05) { return; }                    // 不高于接收面的构件不投影
        var ox = sx * up, oz = sz * up;
        var hw = c.w / 2, hd = c.d / 2;
        var poly = hull([
          [c.x - hw, c.z - hd], [c.x + hw, c.z - hd], [c.x + hw, c.z + hd], [c.x - hw, c.z + hd],
          [c.x - hw + ox, c.z - hd + oz], [c.x + hw + ox, c.z - hd + oz],
          [c.x + hw + ox, c.z + hd + oz], [c.x - hw + ox, c.z + hd + oz]
        ]);
        mctx.beginPath();
        for (var i = 0; i < poly.length; i++) {
          var px = (poly[i][0] - d.x0) * s, py = (poly[i][1] - d.z0) * s;
          if (i === 0) { mctx.moveTo(px, py); } else { mctx.lineTo(px, py); }
        }
        mctx.closePath();
        mctx.fill();
      });
      mctx.restore();
      // 再把整张遮罩以统一透明度铺一次 —— 阴影深浅只由这一个值决定
      ctx.globalAlpha = SHADOW_ALPHA;
      ctx.drawImage(mk, 0, 0);
      ctx.globalAlpha = 1;
      SHADOW.tex[d.name].needsUpdate = true;
    });
  }

  function buildShadowDecals() {
    var T = AFRAME.THREE;
    PALACE.receivers().forEach(function (r) {
      var cv = document.createElement('canvas');
      cv.width = cv.height = r.size;
      SHADOW.canvas[r.name] = cv;
      SHADOW.ctx[r.name] = cv.getContext('2d');
      var mkcv = document.createElement('canvas');
      mkcv.width = mkcv.height = r.size;
      SHADOW.mask[r.name] = mkcv;
      SHADOW.maskCtx[r.name] = mkcv.getContext('2d');
      var tx = new T.CanvasTexture(cv);
      tx.colorSpace = T.SRGBColorSpace;
      SHADOW.tex[r.name] = tx;

      var m = new T.Mesh(
        new T.PlaneGeometry(r.x1 - r.x0, r.z1 - r.z0),
        new T.MeshBasicMaterial({ map: tx, transparent: true, depthWrite: false })
      );
      m.rotation.x = -Math.PI / 2;
      m.position.set((r.x0 + r.x1) / 2, r.y + 0.03, (r.z0 + r.z1) / 2);
      m.renderOrder = 1;
      sceneEl.object3D.add(m);
      r.mesh = m;
      SHADOW.decals.push(r);
    });
  }

  /* ================= A-Frame 组件 ================= */

  // 摇杆移动 + 右摇杆转身
  AFRAME.registerComponent('stick-locomotion', {
    schema: {
      speed: { default: 4.2 },
      snapTurn: { default: 30 },
      deadzone: { default: 0.18 },
      turnHand: { default: 'right' }
    },
    init: function () {
      this.axis = { x: 0, y: 0 };
      this.turned = false;
      this._q = new AFRAME.THREE.Quaternion();
      this._e = new AFRAME.THREE.Euler(0, 0, 0, 'YXZ');
      this._v = new AFRAME.THREE.Vector3();
      var self = this;
      this.el.addEventListener('axismove', function (e) {
        var a = e.detail.axis;
        self.axis.x = a[0] || 0;
        self.axis.y = a[1] || 0;
      });
    },
    tick: function (t, dt) {
      if (!rig || !camEl) { return; }
      var dz = this.data.deadzone;
      var ax = Math.abs(this.axis.x) > dz ? this.axis.x : 0;
      var ay = Math.abs(this.axis.y) > dz ? this.axis.y : 0;
      if (!ax && !ay) { this.turned = false; return; }

      // 按头显当前的偏航角决定前进方向——低头看地时按前仍是水平前进
      camEl.object3D.getWorldQuaternion(this._q);
      this._e.setFromQuaternion(this._q);
      var yaw = this._e.y;
      var fwd = this._v.set(-Math.sin(yaw), 0, -Math.cos(yaw));
      var right = new AFRAME.THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw));

      var move = fwd.multiplyScalar(-ay).add(right.multiplyScalar(ax));
      var mag = Math.min(1, move.length());
      if (mag > 0.001) {
        move.normalize().multiplyScalar(mag * this.data.speed * dt / 1000);
        var p = rig.object3D.position;
        var nx = clamp(p.x + move.x, -140, 140);
        var nz = clamp(p.z + move.z, -100, 100);
        p.set(nx, PALACE.groundHeight(nx, nz) + 0.02, nz);
      }

      // 转身只认右摇杆的左右分量
      var turning = (this.data.turnHand === 'right') ? ax : ax;
      if (Math.abs(turning) > 0.75 && !this.turned) {
        rig.object3D.rotation.y -= Math.sign(turning) * this.data.snapTurn * Math.PI / 180;
        this.turned = true;
      } else if (Math.abs(turning) < 0.5) {
        this.turned = false;
      }
    }
  });

  // 地面瞬移：视线准星凝视或手柄扳机
  AFRAME.registerComponent('teleport-target', {
    init: function () {
      var self = this;
      this.el.addEventListener('raycaster-intersection', function (e) {
        var hit = e.detail.intersections[0];
        if (!hit || !marker) { return; }
        var h = PALACE.groundHeight(hit.point.x, hit.point.z);
        marker.object3D.position.set(hit.point.x, h + 0.06, hit.point.z);
        marker.setAttribute('visible', 'true');
      });
      this.el.addEventListener('raycaster-intersection-cleared', function () {
        if (marker) { marker.setAttribute('visible', 'false'); }
      });
      this.el.addEventListener('click', function (e) {
        var p = e.detail.intersection && e.detail.intersection.point;
        if (!p) { return; }
        placeRig(p.x, p.z, null);
        if (marker) { marker.setAttribute('visible', 'false'); }
      });
    }
  });

  // 场景内按钮
  AFRAME.registerComponent('ui-button', {
    schema: { action: { default: '' }, value: { default: '' } },
    init: function () {
      var self = this;
      this.el.addEventListener('mouseenter', function () {
        self.el.setAttribute('material', 'opacity', 1.0);
      });
      this.el.addEventListener('mouseleave', function () {
        self.el.setAttribute('material', 'opacity', 0.82);
      });
      this.el.addEventListener('click', function () {
        var d = self.data;
        if (d.action === 'time') { setTime(d.value); }
        else if (d.action === 'view') { gotoView(d.value); }
        else if (d.action === 'quality') { setQuality(d.value); }
        self.el.emit('ui-pressed', { action: d.action, value: d.value }, false);
      });
    }
  });

  /* ================= 场景内控制面板 ================= */

  function addButton(parent, x, y, w, h, text, color, action, value) {
    var b = document.createElement('a-plane');
    b.setAttribute('width', w);
    b.setAttribute('height', h);
    b.setAttribute('position', x + ' ' + y + ' 0.06');
    b.setAttribute('material', 'shader: flat; transparent: true; opacity: 0.82; src: url(' + TEX.label(text, color) + ')');
    b.setAttribute('ui-button', 'action: ' + action + '; value: ' + value);
    b.classList.add('clickable');
    parent.appendChild(b);
    return b;
  }

  function buildPanel() {
    var panel = document.createElement('a-entity');
    panel.setAttribute('id', 'uiPanel');
    panel.setAttribute('position', '-11 2.35 66');
    panel.setAttribute('rotation', '0 26 0');

    var bg = document.createElement('a-plane');
    bg.setAttribute('width', 8.4);
    bg.setAttribute('height', 6.2);
    bg.setAttribute('material', 'shader: flat; color: #0e131c; transparent: true; opacity: 0.55');
    panel.appendChild(bg);

    var title = document.createElement('a-plane');
    title.setAttribute('width', 7.6);
    title.setAttribute('height', 0.9);
    title.setAttribute('position', '0 2.5 0.05');
    title.setAttribute('material', 'shader: flat; transparent: true; src: url(' +
      TEX.label('太和殿 · 控制台', '#ffd166', 'rgba(0,0,0,0)') + ')');
    panel.appendChild(title);

    // 时段
    var ty = 1.55;
    ['dawn', 'noon', 'dusk', 'night'].forEach(function (k, i) {
      addButton(panel, -2.6 + i * 1.75, ty, 1.6, 0.62, TIME_LABEL[k], '#7fd1ff', 'time', k);
    });
    // 视角
    ['far', 'mid', 'near', 'stage'].forEach(function (k, i) {
      addButton(panel, -2.6 + i * 1.75, 0.62, 1.6, 0.62, VIEWS[k].name, '#6dea9b', 'view', k);
    });
    // 画质
    ['low', 'medium', 'high'].forEach(function (k, i) {
      addButton(panel, -2.0 + i * 2.0, -0.35, 1.8, 0.62, '画质 ' + QUALITY[k].label, '#ff9f7d', 'quality', k);
    });

    var hint = document.createElement('a-plane');
    hint.setAttribute('width', 7.6);
    hint.setAttribute('height', 1.5);
    hint.setAttribute('position', '0 -1.7 0.05');
    hint.setAttribute('material', 'shader: flat; transparent: true; src: url(' + TEX.panel(
      '怎么操作',
      '移动：左摇杆前后左右（方向跟着你的视线走）。\n' +
      '转身：右摇杆左右，每次 30 度。\n' +
      '瞬移：看向地面，准星停在落点上约 1.6 秒；或用手柄指地面扣扳机。\n' +
      '换时段和视角：点上面的按钮。',
      '#9fb6d6') + ')');
    panel.appendChild(hint);

    sceneEl.appendChild(panel);
  }

  /* ================= 初始化 ================= */

  function addLights() {
    hemiEl = document.createElement('a-entity');
    hemiEl.setAttribute('id', 'hemi');
    hemiEl.setAttribute('light', 'type: hemisphere; color: #b9d3f0; groundColor: #8b7f6a; intensity: 0.5');
    sceneEl.appendChild(hemiEl);

    sunEl = document.createElement('a-entity');
    sunEl.setAttribute('id', 'sun');
    // 不启用实时阴影贴图：阴影由 bakeShadows() 烘焙成贴花。
    // 静态场景 + 移动 GPU 下这一档开销最不划算。
    sunEl.setAttribute('light', 'type: directional; color: #fff2d8; intensity: 1.6');
    sceneEl.appendChild(sunEl);
  }

  function addTeleportPlane() {
    var p = document.createElement('a-plane');
    p.setAttribute('id', 'tp');
    p.setAttribute('rotation', '-90 0 0');
    p.setAttribute('width', PALACE.config.plazaW);
    p.setAttribute('height', PALACE.config.plazaD);
    p.setAttribute('position', '0 0.05 20');
    p.setAttribute('teleport-target', '');
    // 射线只查 .clickable，所以传送平面必须同时挂这个类，否则凝视地面不会触发瞬移
    p.classList.add('clickable');
    p.classList.add('teleportable');
    sceneEl.appendChild(p);
    // 关键：material.visible = false 让它在渲染时被跳过，但射线仍然能打到
    p.addEventListener('loaded', function () {
      var m = p.getObject3D('mesh');
      if (m && m.material) { m.material.visible = false; }
    });

    marker = document.createElement('a-ring');
    marker.setAttribute('id', 'tpMarker');
    marker.setAttribute('rotation', '-90 0 0');
    marker.setAttribute('radius-inner', '0.5');
    marker.setAttribute('radius-outer', '0.75');
    marker.setAttribute('material', 'shader: flat; color: #7fd1ff; transparent: true; opacity: 0.9');
    marker.setAttribute('visible', 'false');
    sceneEl.appendChild(marker);
  }

  function init() {
    sceneEl = document.querySelector('a-scene');
    rig = document.querySelector('#rig');
    camEl = document.querySelector('#cam');

    addLights();
    initSky();
    PALACE.build(document.querySelector('#palace'));
    buildShadowDecals();
    addTeleportPlane();
    buildPanel();

    // URL 参数便于自动化截图验证：?view=near&hour=17.9&q=low
    var qs = new URLSearchParams(location.search);
    setQuality(qs.get('q') || 'medium');

    var v = qs.get('view');
    var h = parseFloat(qs.get('hour'));
    state.hour = isNaN(h) ? TIME_PRESETS.noon : h;
    applyLighting(state.hour);
    bakeShadows();            // 首帧烤一张，之后只在太阳动时重烤

    var startView = (v && VIEWS[v]) ? VIEWS[v] : VIEWS.far;
    state.view = (v && VIEWS[v]) ? v : 'far';
    rig.object3D.position.set(startView.x, PALACE.groundHeight(startView.x, startView.z) + 0.02, startView.z);

    // 把关键运行状态写进页面，方便在桌面端和头显浏览器里直接核对
    function report() {
      var st = document.getElementById('d-status');
      if (!st) { return; }
      st.textContent = '场景就绪 · 时刻 ' + state.hour.toFixed(1) + ' 时' +
        ' · 阴影 ' + (QUALITY[state.quality].shadows ? '烘焙' : '关') +
        ' · 太阳 ' + (sunEl.getAttribute('position')
          ? [sunEl.object3D.position.x, sunEl.object3D.position.y, sunEl.object3D.position.z]
              .map(function (v) { return v.toFixed(0); }).join(',') : '?') +
        ' · 光强 ' + Number(sunEl.getAttribute('light').intensity).toFixed(2) +
        ' · 三角面 ' + sceneEl.renderer.info.render.triangles +
        ' · draw call ' + sceneEl.renderer.info.render.calls;
    }
    setTimeout(report, 900);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var s = document.querySelector('a-scene');
    if (!s) { return; }
    if (s.hasLoaded) { init(); } else { s.addEventListener('loaded', init); }
  });

  window.FCXR = { setTime: setTime, setQuality: setQuality, gotoView: gotoView, state: state };
})();
