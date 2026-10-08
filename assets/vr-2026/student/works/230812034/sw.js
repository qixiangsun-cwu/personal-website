/* 极简 Service Worker：缓存应用外壳，二次打开可离线启动
   （仅在 https / localhost 等安全上下文下才会注册成功；http://127.0.0.1 也算安全源） */
const CACHE = 'gugong-webxr-v2';
const FILES = [
  './',
  './index.html',
  './three.min.js',
  './manifest.webmanifest',
  './icon.svg',
  './icon-192.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(FILES); })
      .catch(function () { /* 个别文件缺失不影响主流程 */ })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) return caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  if (e.request.method !== 'GET') return;
  var url = new URL(e.request.url);
  if (url.origin !== location.origin) return;      // 跨域请求不接管
  // HTML 文档走「网络优先」：老师在服务器上更新了文件，学生刷新就能拿到新版，不会被旧缓存卡住
  var isDoc = e.request.mode === 'navigate' || /\.html?($|\?)/i.test(url.pathname);
  if (isDoc) {
    e.respondWith(
      fetch(e.request).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, copy); }).catch(function () {});
        return res;
      }).catch(function () { return caches.match(e.request).then(function (hit) { return hit || caches.match('./index.html'); }); })
    );
    return;
  }
  // 其余静态资源（three.min.js / 图标等）走「缓存优先」：二次打开秒开
  e.respondWith(
    caches.match(e.request).then(function (hit) {
      return hit || fetch(e.request).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, copy); }).catch(function () {});
        return res;
      }).catch(function () {
        return caches.match('./index.html');
      });
    })
  );
});
