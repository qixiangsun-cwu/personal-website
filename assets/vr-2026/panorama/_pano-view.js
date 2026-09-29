/* =============================================================================
 * _pano-view.js —— 课堂演示共用的小工具：把一张等距柱状全景按「朝向」渲染到 canvas
 *
 * 为什么单独抽出来：到第四遍的时候，同一段射线反查代码已经在 01 / 04 / 07 / 08 里
 * 各抄了一份，改一处要改四处，而且已经因此踩过一次坑——某一份写成了 forward=+X，
 * 结果画面与公式 u = 0.75 + yaw/360 静默地差了 90°。**基底约定必须只有一份。**
 *
 * 用法：
 *   var v = new PanoView(document.getElementById('cv'), './pano/living.jpg');
 *   v.onready = function(){ v.draw(); };
 *   v.yaw = -47; v.pitch = 0; v.fov = 62;    // 度
 *   v.draw();                                 // 需要时手动重画
 *   v.centerU();                              // → 视线中心的 u（用来验证公式）
 *   v.uvAt(px, py);                           // → 画布上某点反查出的 {u, v}（点一下量 u 用）
 *
 * 基底约定（与 three.js 一致，全项目唯一）：
 *   等距柱状图上的点 u 对应世界方向 d(u) = (-cos 2πu, ·, sin 2πu)
 *   相机 forward = −Z、right = +X、up = +Y
 *   ⇒ 视线中心的 u = 0.75 + yaw / 360
 * ========================================================================== */
function PanoView(canvas, src, opts){
  opts = opts || {};
  this.cv = canvas;
  this.g = canvas.getContext('2d');
  this.src = src;
  this.yaw = opts.yaw || 0;       // 度
  this.pitch = opts.pitch || 0;   // 度
  this.fov = opts.fov || 62;      // 垂直视角，度
  this.exposure = opts.exposure || 1;
  this.onready = null;
  this.ready = false;
  this._sw = 0; this._sh = 0; this._data = null;
  this._img = null;
  var self = this;
  var img = new Image();
  img.onload = function(){
    var c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    var g = c.getContext('2d'); g.drawImage(img, 0, 0);
    self._sw = img.width; self._sh = img.height;
    self._data = g.getImageData(0, 0, img.width, img.height);
    self._img = img; self.ready = true;
    if (self.onready) self.onready();
  };
  img.onerror = function(){
    if (self.onerror) self.onerror(src);
  };
  img.src = src;
}
PanoView.prototype.dirToUV = function(x, y, z){
  var phi = Math.atan2(z, -x); if (phi < 0) phi += Math.PI * 2;
  return [phi / (Math.PI * 2), Math.acos(Math.max(-1, Math.min(1, y))) / Math.PI];
};
PanoView.prototype.centerU = function(){
  var u = 0.75 + this.yaw / 360;
  while (u < 0) u += 1; while (u >= 1) u -= 1;
  return u;
};
/* 逐像素反查：把球面方向上看到的那一点画到平面画布上 */
PanoView.prototype.draw = function(){
  if (!this.ready) return false;
  var cv = this.cv, g = this.g, W = cv.width, H = cv.height;
  var idata = g.createImageData(W, H), d = idata.data;
  var fovV = this.fov * Math.PI / 180;
  var y0 = this.yaw * Math.PI / 180, p0 = this.pitch * Math.PI / 180;
  var cy0 = Math.cos(y0), sy0 = Math.sin(y0), cp = Math.cos(p0), sp = Math.sin(p0);
  var SW = this._sw, SH = this._sh, S = this._data.data, ex = this.exposure;
  for (var py = 0; py < H; py++){
    var ay = (0.5 - (py + 0.5) / H) * fovV;
    for (var px = 0; px < W; px++){
      var ax = ((px + 0.5) / W - 0.5) * fovV * (W / H);
      /* 相机基底：forward = −Z、right = +X、up = +Y */
      var X = Math.sin(ax) * Math.cos(ay), Y = Math.sin(ay), Z = -Math.cos(ax) * Math.cos(ay);
      /* 先绕 right 轴 pitch，再绕 up 轴 yaw */
      var Y1 = Y * cp - Z * sp, Z1 = Y * sp + Z * cp;
      var dx = X * cy0 + Z1 * sy0;
      var dz = -X * sy0 + Z1 * cy0;
      var uv = this.dirToUV(dx, Y1, dz);
      var tx = Math.min(SW - 1, Math.max(0, Math.round(uv[0] * SW - 0.5)));
      var ty = Math.min(SH - 1, Math.max(0, Math.round(uv[1] * SH - 0.5)));
      var si = (ty * SW + tx) * 4, o = (py * W + px) * 4;
      if (ex === 1){
        d[o] = S[si]; d[o+1] = S[si+1]; d[o+2] = S[si+2];
      } else {
        d[o]   = Math.min(255, S[si]   * ex);
        d[o+1] = Math.min(255, S[si+1] * ex);
        d[o+2] = Math.min(255, S[si+2] * ex);
      }
      d[o+3] = 255;
    }
  }
  g.putImageData(idata, 0, 0);
  return true;
};
/* 画布上的某个点对应全景图的哪个 (u,v) —— 用来做「点一下量 u」这类工具 */
PanoView.prototype.uvAt = function(px, py){
  if (!this.ready) return null;
  var W = this.cv.width, H = this.cv.height;
  var fovV = this.fov * Math.PI / 180;
  var ay = (0.5 - (py + 0.5) / H) * fovV;
  var ax = ((px + 0.5) / W - 0.5) * fovV * (W / H);
  var X = Math.sin(ax) * Math.cos(ay), Y = Math.sin(ay), Z = -Math.cos(ax) * Math.cos(ay);
  var y0 = this.yaw * Math.PI / 180, p0 = this.pitch * Math.PI / 180;
  var Y1 = Y * Math.cos(p0) - Z * Math.sin(p0), Z1 = Y * Math.sin(p0) + Z * Math.cos(p0);
  var dx = X * Math.cos(y0) + Z1 * Math.sin(y0);
  var dz = -X * Math.sin(y0) + Z1 * Math.cos(y0);
  var uv = this.dirToUV(dx, Y1, dz);
  return { u: uv[0], v: uv[1] };
};
