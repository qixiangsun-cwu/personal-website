// 智安守护 · Service Worker
// 作用域限定在 /zhian-2026/（由 sw.js 所在位置决定），
// 因此不会影响主站其它页面的缓存策略。
// 改了任何被缓存的文件，请把 CACHE 版本号 +1（如 zhian-v2），否则老用户拿不到更新。
const CACHE = 'zhian-v2';

// 应用外壳：安装时预缓存，保证离线也能打开
const APP_SHELL = [
  './',
  './index.html',
  './style.css',
  './script.js',
  './manifest.webmanifest',
  './logo.webp',
  './dengmama.jpg',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // 单个文件失败不应让整次安装失败
      .then((cache) => Promise.allSettled(APP_SHELL.map((f) => cache.add(f))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // 后端接口绝不缓存：对话内容是实时生成的
  if (url.pathname.startsWith('/api/')) return;

  // 跨域资源（如 CDN）交给浏览器自行处理
  if (url.origin !== self.location.origin) return;

  // 页面导航：网络优先，失败时回退到缓存的首页（离线可用）
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put('./index.html', copy));
          return resp;
        })
        .catch(() => caches.match('./index.html').then((r) => r || caches.match('./')))
    );
    return;
  }

  // 静态资源：缓存优先，命中后后台更新
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return resp;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});