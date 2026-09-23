// Service Worker：只缓存与身份无关的静态资源，绝不缓存 HTML 页面或导航请求。
//
// 为什么这么改：企业部署走 HTTPS，浏览器才会注册 Service Worker，因此下面这些
// 问题在正式环境会真实生效（HTTP 试点阶段一直没暴露）：
//   1) 旧版把 /login、/app、/admin 纳入预缓存。安装发生在已登录页面，但
//      cache.addAll 是对页面发起普通 GET——未登录时会拿到 302 后的登录页，
//      并被存到 /app 这个键上；之后离线回退就会把登录页当成 /app 返回。
//   2) 旧版对所有同源 GET 做缓存，且缓存不区分用户。共享电脑上，离线回退可能
//      把上一位用户的页面内容给到下一位。
// 离线访问页面并不是本项目的需求，所以最稳妥的做法是：导航请求完全不碰缓存。
//
// 静态资源走「网络优先 + 失败回退缓存」，在线始终拿最新，离线仍有壳可用。

const CACHE = 'rag-static-v4';
const STATIC_PREFIX = '/static/';

// 只预缓存体积固定、与登录状态无关的资源。
const PRECACHE = [
  '/static/css/app.css',
  '/static/js/common.js',
  '/static/js/app.js',
  '/static/js/admin.js',
  '/static/manifest.webmanifest',
  '/static/favicon.svg',
  '/static/icons/icon.svg'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      // 单个资源缺失不应让整个 Service Worker 安装失败。
      .then(cache => cache.addAll(PRECACHE).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE).map(key => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') { return; }

  // 导航请求（HTML 页面）一律交给网络，不进缓存、也不回退缓存。
  if (request.mode === 'navigate') { return; }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }
  if (!url.pathname.startsWith(STATIC_PREFIX)) { return; }

  event.respondWith(
    caches.open(CACHE).then(cache =>
      fetch(request).then(response => {
        // 只缓存成功响应：错误页、重定向都不应该进缓存。
        if (response && response.ok) { cache.put(request, response.clone()); }
        return response;
      }).catch(() => cache.match(request).then(hit => hit || Response.error()))
    )
  );
});
