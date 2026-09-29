/* ============================================================
   textures.js — 程序化生成可平铺贴图

   为什么不用图片文件：
   - 目标机是 Pico Neo 3（移动 SoC）。每张 2K 照片贴图解压后占几十 MB 显存，
     首屏要等下载 + 解码，XR 里就是一段黑屏。
   - 程序化 Canvas 贴图是纯计算出来的：零网络请求、几十 KB 内存、
     而且天然可平铺（用取模噪声，接缝处连续）。
   - 缺点是细节不如实拍扫描。这是刻意的取舍，见下。

   ponytail: 用程序化平铺贴图而不是照片贴图。质感上限低于实拍材质，
   但如果帧率不达标，换成实拍贴图只会更糟——先保帧率。
   ============================================================ */

var TEX = (function () {
  'use strict';

  var cache = {};

  // 确定性随机：同一个种子每次生成一样的贴图，方便反复对比截图
  function rng(seed) {
    var s = seed >>> 0;
    return function () {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }

  function make(name, size, draw) {
    var c = document.createElement('canvas');
    c.width = c.height = size;
    var g = c.getContext('2d');
    draw(g, size, rng(name.length * 7919 + size));
    cache[name] = c;
    return c;
  }

  // 平铺噪声：在 [0,size) 上做周期性取值，保证左右/上下接缝连续
  function wrapNoise(g, size, cells, alpha, color) {
    var step = size / cells;
    for (var y = 0; y < cells; y++) {
      for (var x = 0; x < cells; x++) {
        var v = Math.floor(Math.random() * 255);
        g.fillStyle = 'rgba(' + color + ',' + (alpha * v / 255).toFixed(3) + ')';
        g.fillRect(x * step, y * step, step + 0.5, step + 0.5);
      }
    }
  }

  /* ---------- 金砖地面：暖灰方砖 + 砖缝 ---------- */
  function floor(g, s) {
    g.fillStyle = '#6f6659';
    g.fillRect(0, 0, s, s);
    // 斑驳
    var r = rng(11);
    for (var i = 0; i < 2600; i++) {
      var v = 84 + Math.floor(r() * 56);
      g.fillStyle = 'rgba(' + v + ',' + (v - 8) + ',' + (v - 22) + ',0.28)';
      g.fillRect(r() * s, r() * s, 1 + r() * 3, 1 + r() * 3);
    }
    // 砖缝：4x4 块
    g.strokeStyle = 'rgba(42,37,30,0.62)';
    g.lineWidth = Math.max(1, s / 256);
    for (var k = 0; k <= 4; k++) {
      var p = k * s / 4;
      g.beginPath(); g.moveTo(p, 0); g.lineTo(p, s); g.stroke();
      g.beginPath(); g.moveTo(0, p); g.lineTo(s, p); g.stroke();
    }
  }

  /* ---------- 汉白玉：台基/栏板 ---------- */
  function marble(g, s) {
    g.fillStyle = '#cfc8ba';
    g.fillRect(0, 0, s, s);
    var r = rng(23);
    for (var i = 0; i < 1800; i++) {
      var v = 186 + Math.floor(r() * 44);
      g.fillStyle = 'rgba(' + v + ',' + (v - 4) + ',' + (v - 16) + ',0.35)';
      g.fillRect(r() * s, r() * s, 1 + r() * 4, 1 + r() * 4);
    }
    // 淡纹路
    g.strokeStyle = 'rgba(152,146,132,0.34)';
    g.lineWidth = Math.max(1, s / 300);
    for (var j = 0; j < 14; j++) {
      g.beginPath();
      var y0 = r() * s;
      g.moveTo(0, y0);
      for (var x = 0; x <= s; x += s / 8) {
        g.lineTo(x, y0 + (r() - 0.5) * s * 0.06);
      }
      g.stroke();
    }
  }

  /* ---------- 朱红宫墙 ---------- */
  function wall(g, s) {
    g.fillStyle = '#9d2b21';
    g.fillRect(0, 0, s, s);
    var r = rng(37);
    // 竖向流挂感
    for (var i = 0; i < 220; i++) {
      g.fillStyle = 'rgba(' + (120 + Math.floor(r() * 50)) + ',30,22,0.22)';
      g.fillRect(r() * s, 0, 1 + r() * 2, s);
    }
    for (var k = 0; k < 1400; k++) {
      g.fillStyle = 'rgba(150,50,38,0.20)';
      g.fillRect(r() * s, r() * s, 1 + r() * 3, 1 + r() * 3);
    }
  }

  /* ---------- 琉璃瓦：竖向瓦垄 + 横向瓦当 ---------- */
  function roof(g, s) {
    g.fillStyle = '#b98f1c';
    g.fillRect(0, 0, s, s);
    var ribs = 8, w = s / ribs;
    for (var i = 0; i < ribs; i++) {
      var x = i * w;
      // 瓦垄：中间亮、两侧暗，形成圆筒感
      var grad = g.createLinearGradient(x, 0, x + w, 0);
      grad.addColorStop(0.00, '#8a6a12');
      grad.addColorStop(0.35, '#e0b32c');
      grad.addColorStop(0.55, '#f0c94a');
      grad.addColorStop(0.75, '#c79a1e');
      grad.addColorStop(1.00, '#8a6a12');
      g.fillStyle = grad;
      g.fillRect(x, 0, w, s);
    }
    // 横向瓦片分层
    g.strokeStyle = 'rgba(90,68,10,0.45)';
    g.lineWidth = Math.max(1, s / 200);
    for (var k = 0; k <= 6; k++) {
      var y = k * s / 6;
      g.beginPath(); g.moveTo(0, y); g.lineTo(s, y); g.stroke();
    }
  }

  /* ---------- 菱花格扇：带透明通道的门窗 ---------- */
  function lattice(g, s) {
    g.clearRect(0, 0, s, s);
    g.fillStyle = '#7d2118';
    g.fillRect(0, 0, s, s);
    // 镂空：斜向菱花格
    g.globalCompositeOperation = 'destination-out';
    var step = s / 6;
    for (var y = 0; y < 6; y++) {
      for (var x = 0; x < 6; x++) {
        var cx = x * step + step / 2, cy = y * step + step / 2;
        g.beginPath();
        g.moveTo(cx, cy - step * 0.30);
        g.lineTo(cx + step * 0.30, cy);
        g.lineTo(cx, cy + step * 0.30);
        g.lineTo(cx - step * 0.30, cy);
        g.closePath();
        g.fill();
      }
    }
    g.globalCompositeOperation = 'source-over';
    // 恢复格条（在镂空之上压一层细线，避免太通透）
    g.strokeStyle = 'rgba(125,33,24,0.85)';
    g.lineWidth = Math.max(1, s / 170);
    for (var k = 0; k <= 6; k++) {
      var p = k * step;
      g.beginPath(); g.moveTo(p, 0); g.lineTo(p, s); g.stroke();
      g.beginPath(); g.moveTo(0, p); g.lineTo(s, p); g.stroke();
    }
  }

  /* ---------- 檐下彩画：青绿底 + 金线 ---------- */
  function beam(g, s) {
    g.fillStyle = '#1f4f45';
    g.fillRect(0, 0, s, s);
    g.fillStyle = '#123a6b';
    g.fillRect(0, 0, s, s * 0.18);
    g.fillRect(0, s * 0.82, s, s * 0.18);
    g.strokeStyle = 'rgba(214,175,60,0.85)';
    g.lineWidth = Math.max(1.5, s / 120);
    for (var k = 0; k < 4; k++) {
      var y = s * (0.22 + k * 0.18);
      g.beginPath();
      g.moveTo(0, y);
      for (var x = 0; x <= s; x += s / 12) {
        g.lineTo(x, y + (k % 2 ? 1 : -1) * s * 0.012);
      }
      g.stroke();
    }
  }

  var BUILDERS = {
    floor:   { size: 512, draw: floor },
    marble:  { size: 256, draw: marble },
    wall:    { size: 256, draw: wall },
    roof:    { size: 256, draw: roof },
    lattice: { size: 256, draw: lattice },
    beam:    { size: 256, draw: beam }
  };

  function canvas(name) {
    if (!cache[name]) {
      var b = BUILDERS[name];
      make(name, b.size, b.draw);
    }
    return cache[name];
  }

  var urlCache = {};

  function url(name) {
    if (!urlCache[name]) { urlCache[name] = canvas(name).toDataURL('image/png'); }
    return urlCache[name];
  }

  /* ---------- 文字贴图（中文必须画进 Canvas，A-Frame 默认字体没有中文字形） ---------- */
  var FONT = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Heiti SC", sans-serif';

  function wrap(g, text, maxW) {
    var lines = [], line = '';
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      if (ch === '\n') { lines.push(line); line = ''; continue; }
      if (g.measureText(line + ch).width > maxW && line) { lines.push(line); line = ch; }
      else { line += ch; }
    }
    if (line) { lines.push(line); }
    return lines;
  }

  function panel(title, body, accent) {
    var W = 1024, H = 640;
    var c = document.createElement('canvas');
    c.width = W; c.height = H;
    var g = c.getContext('2d');
    g.fillStyle = 'rgba(16,20,28,0.93)';
    g.fillRect(0, 0, W, H);
    g.strokeStyle = accent; g.lineWidth = 6;
    g.strokeRect(8, 8, W - 16, H - 16);
    g.fillStyle = accent;
    g.font = 'bold 54px ' + FONT;
    g.textBaseline = 'top';
    g.fillText(title, 52, 42);
    g.strokeStyle = 'rgba(255,255,255,0.20)'; g.lineWidth = 2;
    g.beginPath(); g.moveTo(52, 120); g.lineTo(W - 52, 120); g.stroke();
    g.fillStyle = '#e6ecf6';
    g.font = '33px ' + FONT;
    var lines = wrap(g, body, W - 104);
    for (var i = 0, y = 156; i < lines.length && y < H - 60; i++, y += 48) {
      g.fillText(lines[i], 52, y);
    }
    return c.toDataURL('image/png');
  }

  function label(text, fg, bg) {
    var W = 512, H = 112;
    var c = document.createElement('canvas');
    c.width = W; c.height = H;
    var g = c.getContext('2d');
    g.fillStyle = bg || 'rgba(10,14,20,0.78)';
    g.fillRect(0, 0, W, H);
    g.strokeStyle = fg; g.lineWidth = 5;
    g.strokeRect(4, 4, W - 8, H - 8);
    g.fillStyle = fg;
    g.font = 'bold 50px ' + FONT;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text, W / 2, H / 2 + 2);
    return c.toDataURL('image/png');
  }

  return { canvas: canvas, url: url, panel: panel, label: label };
})();
