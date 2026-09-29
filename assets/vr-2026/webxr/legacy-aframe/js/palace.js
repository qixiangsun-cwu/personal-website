/* ============================================================
   palace.js — 太和殿广场的几何构建

   比例基准（米，参考太和殿实测数据）：
     太和殿面阔 63.96 / 进深 37.17 / 通高约 35
     三台（三层汉白玉台基）总高约 6（真实约 8，这里压到 6 以便观察取景）
   场景里所有尺寸都以这些常量为基准。

   屋顶做法：庑殿顶是一个「高度场」。
     设脊线在 z=0、长 WR = W - D（四坡等角），屋面上任意点的「坡距」
       d = max(|z|, |x| - WR/2)
     则高度 y = H * (1 - d/(D/2))^curve。
     curve > 1 就得到中式屋面特有的「举折」——靠近屋脊陡、靠近檐口缓。
     再叠加一个四角起翘项，得到翼角。
   这样一块 40x40 的网格就能生成完整屋顶，不用手工搭面。
   ============================================================ */

var PALACE = (function () {
  'use strict';

  var THREE = null; // 在 init 时取 AFRAME.THREE

  /* ---------- 尺寸常量 ---------- */
  var C = {
    // 三台：三层，每层向内收 4m
    terrace: [
      { w: 104, d: 66, h: 2.2 },
      { w: 96,  d: 58, h: 2.0 },
      { w: 88,  d: 50, h: 1.8 }
    ],
    hallW: 64,        // 太和殿面阔
    hallD: 37,        // 太和殿进深
    colH: 12,         // 檐柱高
    lowerRoof: { w: 74, d: 46, h: 7.2, curve: 1.5, uplift: 0.085 },
    upperWall: { w: 50, d: 29, h: 3.2 },
    upperRoof: { w: 58, d: 34, h: 12.5, curve: 1.45, uplift: 0.10 },
    plazaW: 300, plazaD: 260,
    corridorX: 118,   // 东西廊庑所在 x
    gateZ: 108        // 太和门所在 z
  };

  C.terraceTop = C.terrace.reduce(function (s, t) { return s + t.h; }, 0); // 6.0
  C.lowerEaveY = C.terraceTop + C.colH;                                    // 18.0
  C.upperWallY0 = C.lowerEaveY + 3.0;                                      // 21.0
  C.upperWallY1 = C.upperWallY0 + C.upperWall.h;                           // 24.2

  /* ---------- 地面高度（决定瞬移落点和站立高度） ---------- */
  function groundHeight(x, z) {
    for (var i = C.terrace.length - 1, y = C.terraceTop; i >= 0; i--) {
      var t = C.terrace[i];
      if (Math.abs(x) <= t.w / 2 && Math.abs(z) <= t.d / 2) { return y; }
      y -= t.h;
    }
    return 0;
  }

  /* ---------- 庑殿顶几何 ---------- */
  function roofGeometry(w, d, h, segs, curve, uplift, tileW, tileL) {
    var WR = Math.max(0.5, w - d);           // 脊长
    var hw = w / 2, hd = d / 2;
    var pos = [], uv = [], idx = [];

    function heightAt(x, z, out) {
      var dist = Math.max(Math.abs(z), Math.abs(x) - WR / 2);
      var t = Math.min(1, Math.max(0, dist / hd));
      var y = h * Math.pow(1 - t, curve);
      // 翼角起翘：越靠四角抬得越高。指数取 2 而不是 3——3 次幂会把起翘
      // 全挤在角尖上，看上去像插了两根尖刺，而不是檐口缓缓上扬。
      var cx = Math.abs(x) / hw, cz = Math.abs(z) / hd;
      y += uplift * h * Math.pow(cx * cz, 2);
      out.y = y;
      out.d = dist;
    }

    var tmp = { y: 0, d: 0 };

    function surface(dir) {
      // dir = 1 顶面（法线朝上）；dir = -1 底面（檐下，法线朝下）
      var base = pos.length / 3;
      for (var j = 0; j <= segs; j++) {
        for (var i = 0; i <= segs; i++) {
          var x = -hw + (i / segs) * w;
          var z = -hd + (j / segs) * d;
          heightAt(x, z, tmp);
          pos.push(x, tmp.y + (dir < 0 ? -0.35 : 0), z);
          continue;
        }
      }
      // 先占位再补 UV，避免第二次循环重复计算
      var v0 = base;
      for (var jj = 0; jj <= segs; jj++) {
        for (var ii = 0; ii <= segs; ii++) {
          var p = v0 + jj * (segs + 1) + ii;
          var xx = pos[p * 3], zz = pos[p * 3 + 2];
          var dd = Math.max(Math.abs(zz), Math.abs(xx) - WR / 2);
          uv.push(xx / tileW, (hd - dd) / tileL);
        }
      }
      for (var y = 0; y < segs; y++) {
        for (var x2 = 0; x2 < segs; x2++) {
          var a = v0 + y * (segs + 1) + x2;
          var b = a + 1, c = a + segs + 1, e = c + 1;
          if (dir > 0) { idx.push(a, c, b, b, c, e); }
          else { idx.push(a, b, c, b, e, c); }
        }
      }
    }

    surface(1);   // 瓦面
    surface(-1);  // 檐下（用彩画材质）

    var g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  /* ---------- 材质（共享，减少着色器编译与状态切换） ---------- */
  var MAT = {};

  function tex(name, rx, ry) {
    var t = new THREE.CanvasTexture(TEX.canvas(name));
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(rx || 1, ry || 1);
    t.anisotropy = 1;              // 移动 GPU 上各向异性过滤很贵
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  function buildMaterials() {
    MAT.floor   = new THREE.MeshStandardMaterial({ map: tex('floor', 22, 19), roughness: 0.92, metalness: 0.0 });
    MAT.marble  = new THREE.MeshStandardMaterial({ map: tex('marble', 10, 6), roughness: 0.85, metalness: 0.0 });
    MAT.marbleDark = new THREE.MeshStandardMaterial({ color: 0x8e887c, roughness: 0.9, metalness: 0.0 });
    MAT.wall    = new THREE.MeshStandardMaterial({ map: tex('wall', 6, 2), roughness: 0.78, metalness: 0.0 });
    // 琉璃瓦是釉面但不是镜面：粗糙度给高、金属度压到接近 0，
    // 否则正午顶光下屋面会整片过曝成白色。
    MAT.roof    = new THREE.MeshStandardMaterial({ map: tex('roof', 1, 1), roughness: 0.66, metalness: 0.04 });
    MAT.soffit  = new THREE.MeshStandardMaterial({ map: tex('beam', 12, 1), roughness: 0.7, metalness: 0.0, side: THREE.BackSide });
    MAT.beam    = new THREE.MeshStandardMaterial({ map: tex('beam', 10, 1), roughness: 0.7, metalness: 0.0 });
    MAT.wood    = new THREE.MeshStandardMaterial({ color: 0x7d2118, roughness: 0.72, metalness: 0.0 });
    MAT.lattice = new THREE.MeshStandardMaterial({
      map: tex('lattice', 1, 1), transparent: true, alphaTest: 0.35,
      roughness: 0.7, metalness: 0.0, side: THREE.DoubleSide
    });
    MAT.gold    = new THREE.MeshStandardMaterial({ color: 0xa8871d, roughness: 0.62, metalness: 0.08 });
    MAT.dougong = new THREE.MeshStandardMaterial({ map: tex('beam', 4, 1), roughness: 0.72, metalness: 0.0 });
  }

  /* ---------- 实例化辅助 ---------- */
  function addInstanced(parent, name, geo, mat, mats, castShadow) {
    if (!mats.length) { return null; }
    var m = new THREE.InstancedMesh(geo, mat, mats.length);
    var mat4 = new THREE.Matrix4();
    var q = new THREE.Quaternion();
    var v = new THREE.Vector3();
    var s = new THREE.Vector3();
    for (var i = 0; i < mats.length; i++) {
      var it = mats[i];
      v.set(it.x, it.y, it.z);
      s.set(it.sx || 1, it.sy || 1, it.sz || 1);
      if (it.ry) { q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), it.ry); } else { q.identity(); }
      mat4.compose(v, q, s);
      m.setMatrixAt(i, mat4);
    }
    m.instanceMatrix.needsUpdate = true;
    m.castShadow = !!castShadow;
    m.receiveShadow = false;
    m.frustumCulled = false;   // 实例分布很广，整体包围盒判定没有收益
    parent.setObject3D(name, m);
    return m;
  }

  function box(parent, name, w, h, d, x, y, z, mat, cast, receive) {
    var m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z);
    m.castShadow = !!cast;
    m.receiveShadow = !!receive;
    parent.setObject3D(name, m);
    return m;
  }

  /* ---------- 各构件 ---------- */

  function buildTerrace(root) {
    var y = 0;
    C.terrace.forEach(function (t, i) {
      box(root, 'tier' + i, t.w, t.h, t.d, 0, y + t.h / 2, 0, MAT.marble, true, true);
      // 须弥座上枋：每层顶沿加一道略微出挑的深色压边。
      // 没有这道线，三层台基在远处会糊成一整块灰板，完全看不出层级。
      box(root, 'tierCap' + i, t.w + 0.7, 0.28, t.d + 0.7,
          0, y + t.h - 0.14, 0, MAT.marbleDark, true, false);
      y += t.h;
    });

    // 望柱：沿每层台基边缘等距排布，全部塞进一个 InstancedMesh
    var postGeo = new THREE.BoxGeometry(0.34, 1.05, 0.34);
    var posts = [];
    y = 0;
    C.terrace.forEach(function (t) {
      var hw = t.w / 2 - 0.5, hd = t.d / 2 - 0.5, step = 2.6;
      var top = y + t.h;
      for (var x = -hw; x <= hw + 0.01; x += step) {
        posts.push({ x: x, y: top + 0.52, z: hd });
        posts.push({ x: x, y: top + 0.52, z: -hd });
      }
      for (var z = -hd + step; z <= hd - step + 0.01; z += step) {
        posts.push({ x: hw, y: top + 0.52, z: z });
        posts.push({ x: -hw, y: top + 0.52, z: z });
      }
      y += t.h;
    });
    addInstanced(root, 'posts', postGeo, MAT.marble, posts, true);
  }

  function buildHall(root) {
    var top = C.terraceTop;
    var colY = top + C.colH / 2;

    // 檐柱：沿面阔/进深方向等距
    var colGeo = new THREE.CylinderGeometry(0.55, 0.62, C.colH, 10, 1);
    var cols = [];
    var hw = C.hallW / 2, hd = C.hallD / 2, step = 4.0;
    for (var x = -hw; x <= hw + 0.01; x += step) {
      cols.push({ x: x, y: colY, z: hd });
      cols.push({ x: x, y: colY, z: hd - 6 });   // 内圈金柱
    }
    for (var z = -hd + step; z <= hd - step + 0.01; z += step) {
      cols.push({ x: hw, y: colY, z: z });
      cols.push({ x: -hw, y: colY, z: z });
    }
    addInstanced(root, 'columns', colGeo, MAT.wood, cols, true);

    // 殿身墙体（略小于柱网，避免和柱子打架）
    box(root, 'wallBody', C.hallW - 2, C.colH + 0.6, C.hallD - 2, 0, top + (C.colH + 0.6) / 2, -0.6, MAT.wall, true, true);

    // 正面菱花格扇门：9 间隔扇
    var bays = 9, bw = C.hallW / bays;
    for (var i = 0; i < bays; i++) {
      var cx = -hw + bw * (i + 0.5);
      var door = new THREE.Mesh(new THREE.PlaneGeometry(bw - 0.7, C.colH - 1.2), MAT.lattice);
      door.position.set(cx, top + (C.colH - 1.2) / 2 + 0.4, hd - 0.55);
      root.setObject3D('door' + i, door);
    }

    // 檐下彩画额枋
    var arch = new THREE.Mesh(new THREE.BoxGeometry(C.hallW + 1, 1.5, C.hallD + 1), MAT.beam);
    arch.position.set(0, top + C.colH + 0.75, -0.6);
    root.setObject3D('arch', arch);

    // 斗拱：两跳，简化为「斗 + 拱」两块体，沿檐口等距
    var dou = new THREE.BoxGeometry(0.9, 0.7, 1.5);
    var mats = [];
    for (var xx = -hw - 2; xx <= hw + 2.01; xx += 2.0) {
      mats.push({ x: xx, y: top + C.colH + 1.9, z: hd + 1.4 });
    }
    for (var zz = -hd - 1; zz <= hd + 1.01; zz += 2.0) {
      mats.push({ x: hw + 1.4, y: top + C.colH + 1.9, z: zz });
      mats.push({ x: -hw - 1.4, y: top + C.colH + 1.9, z: zz });
    }
    addInstanced(root, 'dougong', dou, MAT.dougong, mats, false);

    // 上层殿身
    box(root, 'upperBody', C.upperWall.w, C.upperWall.h, C.upperWall.d,
        0, C.upperWallY0 + C.upperWall.h / 2, -0.6, MAT.wall, true, true);
  }

  function buildRoofs(root) {
    // 下檐（重檐的下层），脊线被上层殿身遮住，所以直接把整块高度场埋进去
    var L = C.lowerRoof;
    var lower = new THREE.Group();
    lower.position.set(0, C.lowerEaveY, 0);
    var lm = new THREE.Mesh(roofGeometry(L.w, L.d, L.h, 36, L.curve, L.uplift, 0.62, 1.15), MAT.roof);
    lm.castShadow = true; lm.receiveShadow = true;
    lower.add(lm);
    var ls = new THREE.Mesh(roofGeometry(L.w, L.d, L.h, 36, L.curve, L.uplift, 1, 1), MAT.soffit);
    ls.position.y = 0;
    lower.add(ls);
    root.setObject3D('lowerRoof', lower);

    // 上檐（主屋顶）
    var U = C.upperRoof;
    var upper = new THREE.Group();
    upper.position.set(0, C.upperWallY1 - 0.4, 0);
    var um = new THREE.Mesh(roofGeometry(U.w, U.d, U.h, 40, U.curve, U.uplift, 0.62, 1.15), MAT.roof);
    um.castShadow = true; um.receiveShadow = true;
    upper.add(um);
    var us = new THREE.Mesh(roofGeometry(U.w, U.d, U.h, 40, U.curve, U.uplift, 1, 1), MAT.soffit);
    upper.add(us);
    root.setObject3D('upperRoof', upper);

    // 正脊 + 鸱吻
    var ridgeLen = U.w - U.d;
    var ridgeY = C.upperWallY1 - 0.4 + U.h;
    box(root, 'ridge', ridgeLen + 2.4, 1.1, 1.0, 0, ridgeY + 0.3, 0, MAT.gold, true, false);
    box(root, 'chiwenL', 1.9, 2.6, 1.6, -ridgeLen / 2 - 1.2, ridgeY + 1.0, 0, MAT.gold, true, false);
    box(root, 'chiwenR', 1.9, 2.6, 1.6,  ridgeLen / 2 + 1.2, ridgeY + 1.0, 0, MAT.gold, true, false);
  }

  function buildCorridors(root) {
    // 东西廊庑：柱列 + 单坡顶，远景用，不做细节
    var colGeo = new THREE.CylinderGeometry(0.35, 0.38, 5.5, 6, 1);
    var mats = [];
    [-1, 1].forEach(function (sgn) {
      box(root, 'corrWall' + sgn, 1.2, 6.2, 150, sgn * (C.corridorX + 7), 3.1, 0, MAT.wall, false, true);
      box(root, 'corrRoof' + sgn, 16, 0.7, 152, sgn * (C.corridorX + 1), 6.6, 0, MAT.roof, false, false);
      for (var z = -74; z <= 74.01; z += 6) {
        mats.push({ x: sgn * C.corridorX, y: 2.75, z: z });
      }
    });
    addInstanced(root, 'corrColumns', colGeo, MAT.wood, mats, false);
  }

  function buildGate(root) {
    // 南侧太和门：远景剪影
    box(root, 'gateBase', 70, 3.0, 22, 0, 1.5, C.gateZ, MAT.marble, false, true);
    box(root, 'gateBody', 56, 10, 16, 0, 8.0, C.gateZ, MAT.wall, false, true);
    var gm = new THREE.Mesh(roofGeometry(66, 24, 8, 20, 1.4, 0.18, 0.7, 1.2), MAT.roof);
    gm.position.set(0, 13.0, C.gateZ);
    root.setObject3D('gateRoof', gm);
  }

  function buildCourtyard(root) {
    // 广场地面
    var g = new THREE.Mesh(new THREE.PlaneGeometry(C.plazaW, C.plazaD), MAT.floor);
    g.rotation.x = -Math.PI / 2;
    g.position.set(0, 0.02, 20);
    g.receiveShadow = true;
    root.setObject3D('plaza', g);

    // 东西宫墙（限定空间的远景边界）
    box(root, 'wallE', 2.5, 7, 240, 146, 3.5, 0, MAT.wall, false, true);
    box(root, 'wallW', 2.5, 7, 240, -146, 3.5, 0, MAT.wall, false, true);
  }

  /* ---------- 总装 ---------- */
  function build(root) {
    THREE = AFRAME.THREE;
    buildMaterials();
    buildCourtyard(root);
    buildTerrace(root);
    buildHall(root);
    buildRoofs(root);
    buildCorridors(root);
    buildGate(root);
  }

  return {
    config: C,
    build: build,
    groundHeight: groundHeight,
    materials: function () { return MAT; },
    /* 阴影投射体清单：每个构件用「俯视矩形 + 顶面高度」近似。
       烘焙阴影只需要这两样，不必用真实网格。 */
    casters: function () {
      var list = [], y = 0;
      C.terrace.forEach(function (t) {
        y += t.h;
        list.push({ x: 0, z: 0, w: t.w, d: t.d, top: y });
      });
      list.push({ x: 0, z: -0.6, w: C.hallW, d: C.hallD, top: C.terraceTop + C.colH + 1.5 });
      list.push({ x: 0, z: -0.6, w: C.upperWall.w, d: C.upperWall.d, top: C.upperWallY1 });
      // 屋顶不能整块按脊高投影，否则影子会大得离谱。
      // 拆成「檐口宽扁 + 屋脊窄高」两段，接近真实侧影。
      list.push({ x: 0, z: 0, w: C.lowerRoof.w, d: C.lowerRoof.d, top: C.lowerEaveY + 0.8 });
      list.push({ x: 0, z: 0, w: C.upperWall.w + 6, d: C.upperWall.d + 6,
                  top: C.lowerEaveY + C.lowerRoof.h });
      list.push({ x: 0, z: 0, w: C.upperRoof.w, d: C.upperRoof.d, top: C.upperWallY1 - 0.4 + 1.0 });
      list.push({ x: 0, z: 0, w: Math.max(6, C.upperRoof.w - C.upperRoof.d), d: 2.5,
                  top: C.upperWallY1 - 0.4 + C.upperRoof.h });
      list.push({ x: -C.corridorX - 1, z: 0, w: 16, d: 152, top: 6.9 });
      list.push({ x: C.corridorX + 1, z: 0, w: 16, d: 152, top: 6.9 });
      list.push({ x: 0, z: C.gateZ, w: 66, d: 24, top: 21 });
      return list;
    },
    /* 接收面清单：广场 + 三层台基顶面 */
    receivers: function () {
      var out = [{ name: 'plaza', y: 0.0, x0: -C.plazaW / 2, x1: C.plazaW / 2,
                   z0: 20 - C.plazaD / 2, z1: 20 + C.plazaD / 2, size: 1024 }];
      var y = 0;
      C.terrace.forEach(function (t, i) {
        y += t.h;
        out.push({ name: 'terrace' + i, y: y - 0.02,
                   x0: -t.w / 2, x1: t.w / 2, z0: -t.d / 2, z1: t.d / 2, size: 256 });
      });
      return out;
    }
  };
})();
