/* ============================================================
   导演组流程脚本工作站 · Service Worker（P3 新增）

   用途：彩排/正式演出期间会场网络不可靠时，断网不黑屏。

   缓存策略的两条硬约束：
   1) 「改完刷新生效」不能被破坏 —— 所以页面 / CSS / JS / flow.json 一律走
      **network-first**（先要网络，拿不到才用缓存）。只有大体积、几乎不变的
      媒体文件走 cache-first（重下 40 MB 视频在现场是灾难）。
   2) 只在用户于设置面板里手动打开时才注册。默认关闭。
   ============================================================ */

const VERSION = 'ws-v9';

/* 预缓存：小体积、决定「能不能打开」的壳。
   视频（五段合计约 85 MB）不预缓存 —— 首次播放时按需写入缓存。 */
const SHELL = [
  './',
  './index.html',
  './screen.html',
  './assets/css/workstation.css',
  './assets/js/workstation.js',
  './assets/js/flow-data.js',
  './data/flow.json',
  /* 法规与政策参考库：只预缓存目录，20 份正文按需缓存（约 1 MB，不进首屏） */
  './data/laws/index.json',
  './law.html',
  /* 封面图实际文件名是 01.jpg … 05.jpg（不是视频名同名的 jpg）。
     写错路径不会报错，只会被 .catch(()=>{}) 静默吞掉 —— 改这里时务必对一下 assets/poster/ 实际文件。 */
  './assets/poster/01.jpg',
  './assets/poster/02.jpg',
  './assets/poster/03.jpg',
  './assets/poster/04.jpg',
  './assets/poster/05.jpg'
];

const MEDIA_RE = /\.(mp4|webm|jpg|jpeg|png|webp|gif|ico)(\?.*)?$/i;

self.addEventListener('install', e=>{
  e.waitUntil((async ()=>{
    const cache = await caches.open(VERSION);
    /* 逐个请求，单项失败不拖垮整体安装（比如某张封面被删了） */
    await Promise.all(SHELL.map(url=>
      cache.add(new Request(url, {cache:'reload'})).catch(()=>{})
    ));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e=>{
  e.waitUntil((async ()=>{
    /* 只保留当前版本，清掉历史版本缓存 */
    const keys = await caches.keys();
    await Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('message', e=>{
  if(e.data && e.data.type === 'ACTIVATE') self.skipWaiting();
});

self.addEventListener('fetch', e=>{
  const req = e.request;
  if(req.method !== 'GET') return;
  if(new URL(req.url).origin !== self.location.origin) return;

  /* 媒体：cache-first，命中就不联网（避免现场反复拉大文件） */
  if(MEDIA_RE.test(req.url)){
    e.respondWith((async ()=>{
      const hit = await caches.match(req, {ignoreSearch:true});
      if(hit) return hit;
      try{
        const res = await fetch(req);
        if(res && res.ok){
          const cache = await caches.open(VERSION);
          cache.put(req, res.clone());
        }
        return res;
      }catch(err){
        const hit2 = await caches.match(req, {ignoreSearch:true});
        if(hit2) return hit2;
        throw err;
      }
    })());
    return;
  }

  /* 其余一切（HTML / CSS / JS / flow.json）：network-first
     —— 只要在线就拿到最新的，改完刷新仍然立刻生效；
        网络失败才退回缓存，保证现场断网不黑屏。 */
  e.respondWith((async ()=>{
    try{
      const res = await fetch(req);
      if(res && res.ok){
        const cache = await caches.open(VERSION);
        cache.put(req, res.clone());
      }
      return res;
    }catch(err){
      const hit = await caches.match(req, {ignoreSearch:true});
      if(hit) return hit;
      /* 导航请求兜底到首页，避免「断网点开二级页直接白屏」 */
      if(req.mode === 'navigate'){
        const home = await caches.match('./index.html');
        if(home) return home;
      }
      throw err;
    }
  })());
});
