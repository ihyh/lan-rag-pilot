'use strict';
// Service Worker 缓存路由的行为测试 + 管理页反馈的源码级回归守卫。
//
// 为什么需要它：Service Worker 只在 HTTPS 下注册，而企业部署正是 HTTPS，
// 因此 sw.js 的缓存策略在正式环境会真实生效，却一直没有任何自动化覆盖。
// 这里用最小沙箱**真正执行** sw.js 并触发它的三个事件，验证路由行为。

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ORIGIN = 'https://rag.example';
const swSource = fs.readFileSync(path.join(__dirname, '../app/static/sw.js'), 'utf8');
const adminSource = fs.readFileSync(path.join(__dirname, '../app/static/js/admin.js'), 'utf8');

// ---------------- 沙箱：真跑 sw.js ----------------

const listeners = {};
const precached = [];
const putUrls = [];
const failedPrecache = '/static/favicon.svg';
let fetchOk = true;

function makeCache() {
  return {
    add: async url => {
      if (url === failedPrecache) { throw new Error('simulated missing static resource'); }
      precached.push(url);
    },
    put: request => new Promise(resolve => {
      setTimeout(() => { putUrls.push(request.url); resolve(); }, 0);
    }),
    match: async () => null
  };
}

const context = vm.createContext({
  URL,
  console,
  Response: { error: () => ({ __isError: true }) },
  self: {
    location: { origin: ORIGIN },
    addEventListener: (type, handler) => { listeners[type] = handler; },
    skipWaiting() {},
    clients: { claim() {} }
  },
  caches: {
    open: async () => makeCache(),
    keys: async () => [],
    delete: async () => true
  },
  fetch: async () => ({ ok: fetchOk, clone() { return this; } })
});

vm.runInContext(swSource, context);

assert.ok(listeners.install, 'sw.js 注册了 install');
assert.ok(listeners.activate, 'sw.js 注册了 activate');
assert.ok(listeners.fetch, 'sw.js 注册了 fetch');

async function fire(type, request) {
  const event = {
    request,
    responded: false,
    waitUntil(promise) { event._pending = promise; },
    respondWith(promise) { event.responded = true; event._pending = promise; }
  };
  listeners[type](event);
  if (event._pending) { await event._pending; }
  return event;
}

function request(url, { method = 'GET', mode = 'no-cors' } = {}) {
  return { url, method, mode };
}

async function main() {
  // 先触发 install / activate，拿到预缓存清单
  await fire('install', undefined);
  await fire('activate', undefined);

  // ---- 1. 预缓存清单不得包含任何页面 ----
  for (const page of ['/app', '/admin', '/login', '/']) {
    assert.ok(
      !precached.includes(page),
      `预缓存不得包含页面 ${page}（未登录时会把 302 后的登录页缓存到该键上）`
    );
  }
  assert.ok(precached.length > 0, '预缓存清单非空');
  assert.ok(!precached.includes(failedPrecache), '单个缺失资源不会被误记为已缓存');
  assert.ok(
    precached.includes('/static/css/app.css') && precached.includes('/static/js/common.js'),
    '单个资源预缓存失败后，其余静态资源仍会完成缓存'
  );
  for (const url of precached) {
    assert.ok(url.startsWith('/static/'), `预缓存项应全部位于 /static/ 下，实际出现 ${url}`);
  }

  // ---- 2. 导航请求（HTML 页面）一律不拦截 ----
  for (const page of ['/app', '/admin', '/login', '/']) {
    const event = await fire('fetch', request(ORIGIN + page, { mode: 'navigate' }));
    assert.equal(event.responded, false, `导航请求 ${page} 不得被 Service Worker 接管`);
  }

  // ---- 3. 静态资源会被接管并缓存 ----
  putUrls.length = 0;
  const staticEvent = await fire('fetch', request(ORIGIN + '/static/js/app.js'));
  assert.equal(staticEvent.responded, true, '/static/ 资源应由 Service Worker 接管');
  assert.deepEqual(putUrls, [ORIGIN + '/static/js/app.js'], '成功的静态资源应写入缓存');

  // ---- 4. API 请求不拦截（身份相关，绝不能进缓存）----
  const apiEvent = await fire('fetch', request(ORIGIN + '/api/admin/documents'));
  assert.equal(apiEvent.responded, false, '/api/ 请求不得被接管');

  // ---- 5. Service Worker 自身不拦截 ----
  const swEvent = await fire('fetch', request(ORIGIN + '/sw.js'));
  assert.equal(swEvent.responded, false, '/sw.js 自身不得被接管');

  // ---- 6. 跨域请求不拦截 ----
  const crossEvent = await fire('fetch', request('https://cdn.example.com/static/x.js'));
  assert.equal(crossEvent.responded, false, '跨域请求不得被接管');

  // ---- 7. 非 GET 不拦截 ----
  const postEvent = await fire('fetch', request(ORIGIN + '/static/x.js', { method: 'POST' }));
  assert.equal(postEvent.responded, false, '非 GET 请求不得被接管');

  // ---- 8. 非成功响应（如 302 到登录页）不得写入缓存 ----
  putUrls.length = 0;
  fetchOk = false;
  await fire('fetch', request(ORIGIN + '/static/js/common.js'));
  assert.equal(putUrls.length, 0, '非 2xx 响应不得写入缓存（避免把跳转页当成资源缓存）');
  fetchOk = true;

  // ---------------- 管理页反馈：源码级回归守卫 ----------------
  // 说明：以下是对源码的静态断言，不是行为测试；用于防止已经修好的问题被改回去。

  assert.ok(
    !/setInterval\s*\(/.test(adminSource),
    '管理页不得再用 setInterval 轮询文档列表（上传本身是串行等待，结束后刷新一次即可）'
  );
  assert.match(
    adminSource,
    /chip-reason/,
    '上传失败原因必须渲染成可见文本，不能只写进 title（触屏与键盘用户看不到）'
  );
  assert.ok(
    !/empty\(\s*e\.message/.test(adminSource),
    '加载失败不得复用「暂无数据」组件，必须用 loadError 并提供重试入口'
  );
  assert.match(adminSource, /loadError\(/, '管理页应使用 loadError 渲染加载失败');
  assert.match(
    adminSource,
    /busy\(btn, true, '重建中…'\)/,
    '重建索引必须有忙碌态，否则可被重复点击触发多次重建'
  );
  assert.match(adminSource, /busy\(btn, true, '删除中…'\)/, '删除操作必须有忙碌态');
  assert.equal(
    (adminSource.match(/window\.confirm\(/g) || []).length >= 3,
    true,
    '破坏性操作仍应保留二次确认'
  );

  console.log('Service Worker 缓存路由与管理页反馈检查通过');
}

main().catch(error => {
  console.error(error.message);
  process.exit(1);
});
