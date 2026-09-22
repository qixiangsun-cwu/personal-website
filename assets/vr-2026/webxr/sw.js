/* 故宫 · 紫禁城 WebXR —— Service Worker
 * 作用：① 让页面具备"安装为应用"的条件；② 首访之后离线可打开。
 * 策略：同源 GET 请求走 cache-first，未命中则联网并回填缓存；联网失败时回落到主页面。
 */
const CACHE = 'gugong-webxr-v2';
const ASSETS = [
  './gugong_webxr.html',
  './three.min.js',
  './manifest.webmanifest',
  './icon.svg',
  './icon-192.png',
  './icon-512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // 逐个缓存：任何一个文件取不到都不至于让整次安装失败
      .then((c) => Promise.all(ASSETS.map((u) => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  let sameOrigin = false;
  try { sameOrigin = new URL(req.url).origin === self.location.origin; } catch (err) { return; }
  if (!sameOrigin) return;
  /* 网络优先：保证页面更新后立刻生效；断网时才回落到缓存。
     （若用缓存优先，学生装成应用后会一直看到旧版本，很难排查。） */
  e.respondWith(
    fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || caches.match('./gugong_webxr.html')))
  );
});
