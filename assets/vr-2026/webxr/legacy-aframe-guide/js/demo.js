/* ============================================================
   故宫有字 · WebXR 演示 —— 逻辑脚本

   两个目的：
   1) 复现「浏览器 + 3D 场景 + 图文讲解 + 热点跳转」这种课堂演示形态
   2) 自检 Pico 浏览器是否支持 WebXR，并给出可读的结论

   为什么所有文字都用 Canvas 生成贴图？
   - A-Frame 默认字体（Roboto SDF）不含中文字形，a-text 写中文会变方块
   - WebXR 沉浸模式下 HTML 覆盖层不可见，讲解内容必须放进 3D 场景
   ============================================================ */

var CJK_FONT = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Heiti SC", sans-serif';

/* ---------- 1. 贴图工具 ---------- */

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// 中文没有空格，必须逐字量宽来折行
function wrapText(ctx, text, maxWidth) {
  var lines = [], line = '';
  for (var i = 0; i < text.length; i++) {
    var ch = text.charAt(i);
    if (ch === '\n') { lines.push(line); line = ''; continue; }
    var test = line + ch;
    if (ctx.measureText(test).width > maxWidth && line !== '') {
      lines.push(line);
      line = ch;
    } else {
      line = test;
    }
  }
  if (line) { lines.push(line); }
  return lines;
}

function makePanelTexture(title, body, accent) {
  var W = 1024, H = 640;
  var c = document.createElement('canvas');
  c.width = W; c.height = H;
  var ctx = c.getContext('2d');

  ctx.fillStyle = 'rgba(18,24,36,0.94)';
  roundRect(ctx, 0, 0, W, H, 26);
  ctx.fill();

  ctx.strokeStyle = accent;
  ctx.lineWidth = 6;
  roundRect(ctx, 8, 8, W - 16, H - 16, 22);
  ctx.stroke();

  ctx.fillStyle = accent;
  ctx.font = 'bold 56px ' + CJK_FONT;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(title, 56, 46);

  ctx.strokeStyle = 'rgba(255,255,255,0.22)';
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(56, 128); ctx.lineTo(W - 56, 128); ctx.stroke();

  ctx.fillStyle = '#e8eef8';
  ctx.font = '34px ' + CJK_FONT;
  var lines = wrapText(ctx, body, W - 112);
  var y = 164;
  for (var i = 0; i < lines.length && y < H - 70; i++) {
    ctx.fillText(lines[i], 56, y);
    y += 50;
  }
  return c.toDataURL('image/png');
}

function makeTagTexture(text, color) {
  var W = 512, H = 96;
  var c = document.createElement('canvas');
  c.width = W; c.height = H;
  var ctx = c.getContext('2d');
  ctx.fillStyle = 'rgba(10,14,22,0.72)';
  roundRect(ctx, 0, 0, W, H, 22);
  ctx.fill();
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  roundRect(ctx, 4, 4, W - 8, H - 8, 19);
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.font = 'bold 46px ' + CJK_FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, W / 2, H / 2 + 2);
  return c.toDataURL('image/png');
}

function makePlaqueTexture() {
  var W = 512, H = 176;
  var c = document.createElement('canvas');
  c.width = W; c.height = H;
  var ctx = c.getContext('2d');
  ctx.fillStyle = '#123a8c';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#c9a227';
  ctx.lineWidth = 10;
  ctx.strokeRect(5, 5, W - 10, H - 10);
  ctx.fillStyle = '#e8c34a';
  ctx.font = 'bold 88px ' + CJK_FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('建极绥猷', W / 2, H / 2 + 4);
  return c.toDataURL('image/png');
}

/* ---------- 2. 讲解内容（对应四个热点） ---------- */

var INFO = [
  {
    title: '建极绥猷',
    accent: '#ffd166',
    body: '太和殿匾额，乾隆帝御笔。「极」是屋脊栋梁，建极就是要建立中正的治国方略；' +
          '「绥」是顺应之意，「猷」为法则。所谓建极绥猷，是说一国之君要上体天道、下顺民意，' +
          '既承天而建立法则，又要抚民而顺应大道，用中正的法则治理国家。'
  },
  {
    title: '殿门与金扉',
    accent: '#7fd1ff',
    body: '太和殿正面开五间，正中三间设门。门窗采用菱花格扇，殿内铺「金砖」——' +
          '并非黄金所制，而是苏州御窑烧造、质地细密、敲之有声的方砖。' +
          '殿前六根朱红金柱，是支撑重檐屋顶的主要受力构件。'
  },
  {
    title: '金柱与屋顶形制',
    accent: '#6dea9b',
    body: '太和殿面阔九间、进深五间，是中国现存规模最大的木结构大殿。' +
          '屋顶为最高等级的重檐庑殿顶，只有皇家主殿才能使用。' +
          '屋檐下的斗拱层层出挑，把屋顶重量传递到柱子上。'
  },
  {
    title: '三层汉白玉台基',
    accent: '#ff9f7d',
    body: '太和殿坐落于三层汉白玉台基之上，俗称「三台」，总高约八米。' +
          '台基四周设栏板望柱，前面陈列铜龟、铜鹤、日晷、嘉量等，' +
          '象征江山永固、皇权天授。'
  }
];

/* ---------- 3. 装配场景 ---------- */

function setTexture(id, dataUrl) {
  var el = document.getElementById(id);
  if (!el) { return; }
  var apply = function () {
    el.setAttribute('material', 'src', 'url(' + dataUrl + ')');
    el.setAttribute('material', 'transparent', true);
    el.setAttribute('material', 'shader', 'flat');
    el.setAttribute('material', 'side', 'double');
  };
  if (el.hasLoaded) { apply(); }
  else { el.addEventListener('loaded', apply); }
}

function showInfo(index) {
  var d = INFO[index];
  if (!d) { return; }
  setTexture('panel', makePanelTexture(d.title, d.body, d.accent));
}

function setup() {
  setTexture('plaque', makePlaqueTexture());
  showInfo(0);

  var tags = [['匾额', '#ffd166'], ['殿门', '#7fd1ff'], ['金柱', '#6dea9b'], ['台基', '#ff9f7d']];
  for (var i = 0; i < tags.length; i++) {
    setTexture('tag' + i, makeTagTexture(tags[i][0], tags[i][1]));
  }

  var onClick = function (evt) {
    var idx = parseInt(evt.target.getAttribute('data-info'), 10);
    if (!isNaN(idx)) { showInfo(idx); }
  };
  var nodes = document.querySelectorAll('.clickable');
  for (var j = 0; j < nodes.length; j++) {
    nodes[j].addEventListener('click', onClick);
  }

  // 贴图是同步生成的，但 A-Frame 把它上传成纹理还要几帧；
  // 等两帧后再报告就绪，避免用户看到空白面板以为坏了。
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      fill('d-ready', '场景 ：<span class="ok">就绪</span>（默认显示「建极绥猷」，点热点切换）');
    });
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setup);
} else {
  setup();
}

/* ---------- 4. WebXR 能力自检 ---------- */

function fill(id, html) {
  var el = document.getElementById(id);
  if (el) { el.innerHTML = html; }
}

function runDiagnostics() {
  var vrSupported = false;
  var secure = (window.isSecureContext === true);

  var uaEl = document.getElementById('d-ua');
  if (uaEl) { uaEl.textContent = '浏览器 UA：' + (navigator.userAgent || '(无)'); }

  // WebXR 只在安全上下文里暴露。http://<局域网IP> 不算安全上下文，
  // 所以那种情况下 navigator.xr 缺失是正常的，不能据此判断设备支不支持。
  fill('d-secure', '安全上下文 ：' + (secure
    ? '<span class="ok">是</span>'
    : '<span class="bad">否</span> → 浏览器会隐藏 navigator.xr，本次结果无效'));

  if (!navigator.xr) {
    fill('d-xr', 'navigator.xr ：<span class="bad">不存在</span>');
    if (secure) {
      fill('d-vr', 'immersive-vr ：<span class="bad">不支持</span> → 此浏览器确实没有 WebXR');
    } else {
      fill('d-vr', 'immersive-vr ：<span class="warn">无法判断</span> → 因为不是安全上下文，本次结果无效');
    }
    fill('d-btn', '进入 VR 按钮 ：<span class="bad">不会出现</span>');
    return;
  }

  fill('d-xr', 'navigator.xr ：<span class="ok">存在</span>');

  navigator.xr.isSessionSupported('immersive-vr').then(function (ok) {
    vrSupported = ok;
    if (ok) {
      fill('d-vr', 'immersive-vr ：<span class="ok">支持</span> → 可以进入沉浸式 VR');
      fill('d-btn', '进入 VR 按钮 ：<span class="ok">应已出现在右下角</span>');
    } else {
      fill('d-vr', 'immersive-vr ：<span class="warn">不支持</span> → 只能当 3D 网页看');
      fill('d-btn', '进入 VR 按钮 ：<span class="warn">不会出现</span>');
    }
  }).catch(function (e) {
    fill('d-vr', 'immersive-vr ：<span class="warn">检测失败</span>（' + e + '）');
  });

  // A-Frame 的 VR 按钮是异步注入的，延迟回查一次
  setTimeout(function () {
    var b = document.querySelector('.a-enter-vr');
    if (!b || getComputedStyle(b).display === 'none') { return; }
    if (vrSupported) {
      fill('d-btn', '进入 VR 按钮 ：<span class="ok">已出现</span>（右下角）');
    } else {
      fill('d-btn', '进入 VR 按钮 ：<span class="warn">已出现，但只能全屏</span>（当前没有可用 VR 设备）');
    }
  }, 3000);
}

runDiagnostics();
