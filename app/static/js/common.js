/* ============================================================
 * common.js — 三个页面共享的工具库（纯 ES2017+，无依赖）
 * 以全局函数形式提供：api / fmtTime / fmtBytes / fmtMs / esc /
 * toast / busy / icon / h(dom 构建) / 弹层 / registerSW 等。
 * 页面内直接以同名标识符调用（经典脚本共享全局作用域），
 * 同时挂到 window.KB 便于统一引用。
 * ============================================================ */
'use strict';

/* ---------------- 基础查询 ---------------- */

function qs(sel, root) {
  return (root || document).querySelector(sel);
}
function qsa(sel, root) {
  return Array.prototype.slice.call((root || document).querySelectorAll(sel));
}

/* ---------------- DOM 构建（文本一律 textContent，天然防注入） ---------------- */

function h(tag, attrs, children) {
  var el = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined) { return; }
      if (k === 'class') { el.className = v; }
      else if (k === 'style') { el.style.cssText = v; }
      else if (k === 'dataset') { Object.assign(el.dataset, v); }
      else if (k === 'html') { el.innerHTML = v; /* 仅限静态/白名单片段 */ }
      else if (k in el && k !== 'type' && k !== 'value' && k !== 'for') {
        try {
          if (v === '' && typeof el[k] === 'boolean') { el[k] = true; }
          else { el[k] = v; }
        } catch (e) { el.setAttribute(k, v); }
      } else { el.setAttribute(k, v); }
    });
  }
  (children || []).forEach(function (c) {
    if (c === null || c === undefined) { return; }
    el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
  });
  return el;
}

function clear(el) {
  while (el && el.firstChild) { el.removeChild(el.firstChild); }
  return el;
}

/* ---------------- 转义 ---------------- */

function esc(html) {
  return String(html == null ? '' : html)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ---------------- 格式化 ---------------- */

function fmtTime(iso) {
  if (!iso) { return '—'; }
  var d = new Date(iso);
  if (isNaN(d.getTime())) { return String(iso); }
  var p = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
    ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function fmtDate(iso) {
  if (!iso) { return '—'; }
  var d = new Date(iso);
  if (isNaN(d.getTime())) { return String(iso); }
  var p = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function fmtBytes(n) {
  if (n === null || n === undefined) { return '—'; }
  n = Number(n);
  if (isNaN(n) || n < 0) { return '—'; }
  if (n < 1024) { return n + ' B'; }
  var units = ['KB', 'MB', 'GB', 'TB'];
  var i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  var num = n >= 100 ? Math.round(n) : (Math.round(n * 10) / 10);
  return num + ' ' + units[i];
}

function fmtMs(ms) {
  if (ms === null || ms === undefined || ms === '') { return '—'; }
  ms = Number(ms);
  if (isNaN(ms)) { return '—'; }
  if (ms < 1000) { return Math.round(ms) + ' 毫秒'; }
  return (Math.round((ms / 1000) * 10) / 10) + ' 秒';
}

/* 文本单行摘要 */
function excerpt(text, max) {
  var s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
  max = max || 42;
  return s.length > max ? s.slice(0, max) + '…' : s;
}

/* ---------------- 内联 SVG 图标 ---------------- */

var ICON_PATHS = {
  logout: '<path d="M9.5 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.5"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9.5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  send: '<path d="M21 3L10.5 13.5"/><path d="M21 3l-6.5 18-4-8.5L2 8.5z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4.5 7h15"/><path d="M9.5 7V4.8A1.3 1.3 0 0 1 10.8 3.5h2.4a1.3 1.3 0 0 1 1.3 1.3V7"/><path d="M6.5 7l.9 12.2a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5L17.5 7"/><path d="M10 11v6M14 11v6"/>',
  refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.4-6"/><path d="M21 3.5V8h-4.5"/>',
  upload: '<path d="M12 15V4"/><path d="M7.5 8.5L12 4l4.5 4.5"/><path d="M4.5 15.5V18a2.5 2.5 0 0 0 2.5 2.5h10A2.5 2.5 0 0 0 19.5 18v-2.5"/>',
  doc: '<path d="M6.5 3.5h7l4 4V20a1 1 0 0 1-1 1H6.5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z"/><path d="M13.5 3.5V7.5h4"/><path d="M9 12.5h6M9 16h4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  key: '<path d="M4.5 13.5a5.5 5.5 0 1 0 7.6-7.3L15 3h2.5v3H21v3h-4"/><path d="M6.5 14.5L7.3 13"/>',
  user: '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
  alert: '<path d="M12 3.8L22 19.8H2z"/><path d="M12 10v4.2"/><path d="M12 17.4h.01"/>',
  chat: '<path d="M20 15.5A3.5 3.5 0 0 0 16.5 12H7.5A3.5 3.5 0 0 0 4 15.5v0A3.5 3.5 0 0 0 7.5 19h7l3.8 2.6v-2.7a3.2 3.2 0 0 0 1.7-2.9z"/><path d="M9 8V5.5M15 8V5.5"/>',
  search: '<circle cx="10.8" cy="10.8" r="6.3"/><path d="M15.5 15.5L21 21"/>',
  shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 7.9-7.5 9.5-4.3-1.6-7.5-4.9-7.5-9.5V6z"/><path d="M9 11.6l2.2 2.2 3.8-4"/>',
  check: '<path d="M4.5 12.5l5 5L19.5 7"/>',
  gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v3M12 18.2v3M2.8 12h3M18.2 12h3M5.5 5.5l2.1 2.1M16.4 16.4l2.1 2.1M18.5 5.5l-2.1 2.1M7.6 16.4l-2.1 2.1"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5"/>',
  folder: '<path d="M3.5 6A2.5 2.5 0 0 1 6 3.5h4l2.2 2.8h5.8A2.5 2.5 0 0 1 20.5 8.8v0"/><path d="M3.5 6v0l1.8 11a2 2 0 0 0 2 1.6h9.6a2 2 0 0 0 2-1.6L20.5 8.8v0"/>',
  gauge: '<path d="M4.5 18.5a9 9 0 1 1 15 0"/><path d="M12 13.5l3.4-4.2"/><path d="M8.5 18.5a3.5 3.5 0 0 1 7 0"/>',
  check_circle: '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.4 2.4 4.6-5.2"/>',
  bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 5.5V8"/><circle cx="12" cy="5" r="1.6"/><path d="M9 12.5h.01M15 12.5h.01M9 15.5h6"/>',
  dash: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/>'
};

function icon(name) {
  var body = ICON_PATHS[name] || '';
  return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">' + body + '</svg>';
}

/* ---------------- fetch 封装 ---------------- */

/**
 * api(path, opts)
 *  - 默认 GET、credentials=same-origin（会话 Cookie 自动携带）
 *  - 对象 body 自动 JSON 序列化并带 Content-Type；FormData 原样发送
 *  - 解析 JSON 响应；204 / 空响应返回 null
 *  - res.ok=false 时抛出 Error，文案优先取 body.detail
 *    （detail 支持字符串 / {code,message} / 422 数组，逐级兜底）
 *  - HTTP 401 默认自动跳转 /login（登录请求可传 redirectOn401:false）
 */
async function api(path, opts) {
  opts = opts || {};
  var method = String(opts.method || 'GET').toUpperCase();
  var headers = Object.assign({}, opts.headers || {});
  var body = opts.body;
  var isForm = typeof FormData !== 'undefined' && body instanceof FormData;

  if (body !== undefined && body !== null && !isForm && typeof body !== 'string') {
    body = JSON.stringify(body);
  }
  if (body && !isForm && typeof body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  var res;
  try {
    res = await fetch(path, {
      method: method,
      headers: headers,
      body: body,
      credentials: 'same-origin'
    });
  } catch (e) {
    var netErr = new Error('无法连接服务器，请检查网络后重试');
    netErr.network = true;
    throw netErr;
  }

  if (res.status === 401 && opts.redirectOn401 !== false && location.pathname !== '/login') {
    try { location.href = '/login'; } catch (e) { /* 跳转失败继续抛错 */ }
    var unauthed = new Error('未登录或会话已过期');
    unauthed.status = 401;
    throw unauthed;
  }

  var data = null;
  if (res.status !== 204 && res.status !== 205) {
    var ct = res.headers.get('content-type') || '';
    if (ct.indexOf('application/json') !== -1) {
      try { data = await res.json(); } catch (e) { data = null; }
    }
  }

  if (!res.ok) {
    var msg = detailText(data, res.status);
    var err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/* 从响应 body 中提取错误文案（detail 可能是 字符串 / {code,message} / 数组） */
function detailText(data, status) {
  var fallback = '请求失败（HTTP ' + status + '）';
  if (!data) { return fallback; }
  var d = data.detail !== undefined ? data.detail : data;
  if (typeof d === 'string' && d) { return d; }
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    if (typeof d.message === 'string' && d.message) { return d.message; }
    if (typeof d.detail === 'string' && d.detail) { return d.detail; }
    if (typeof d.msg === 'string' && d.msg) { return d.msg; }
    return fallback;
  }
  if (Array.isArray(d) && d.length) {
    // Pydantic 422：数组元素 {loc, msg, type}
    var parts = [];
    d.forEach(function (it) {
      var where = Array.isArray(it && it.loc) ? it.loc.join('.') : '';
      if (it && typeof it.msg === 'string') {
        parts.push(where ? where + ': ' + it.msg : it.msg);
      }
    });
    if (parts.length) { return parts.join('；'); }
    return fallback;
  }
  return fallback;
}

/* ---------------- Toast ---------------- */

function toast(msg, type) {
  var wrap = qs('.toast-wrap');
  if (!wrap) {
    wrap = h('div', { class: 'toast-wrap' });
    document.body.appendChild(wrap);
  }
  type = type || 'info';
  var icons = { success: 'check_circle', error: 'alert', warn: 'alert', info: 'chat' };
  var t = h('div', { class: 'toast t-' + type, role: 'status' }, [
    h('span', { class: 'toast-ic', html: icon(icons[type] || 'chat') }),
    h('span', null, [String(msg == null ? '' : msg)])
  ]);
  while (wrap.children.length >= 5) { wrap.removeChild(wrap.firstChild); }
  wrap.appendChild(t);
  var timer = setTimeout(function () { dismiss(t); }, 3600);
  t.addEventListener('click', function () { clearTimeout(timer); dismiss(t); });
  return t;
}

function dismiss(t) {
  if (!t || !t.parentNode) { return; }
  t.classList.add('out');
  setTimeout(function () {
    if (t.parentNode) { t.parentNode.removeChild(t); }
  }, 320);
}

/* ---------------- 按钮忙碌态 ---------------- */

function busy(btn, on, busyText) {
  if (!btn) { return; }
  if (on) {
    if (!btn.dataset.kbOrig) {
      btn.dataset.kbOrig = btn.innerHTML;
      btn.dataset.kbDisabled = btn.disabled ? '1' : '';
    }
    btn.disabled = true;
    btn.classList.add('is-busy');
    if (busyText) {
      btn.innerHTML = '<span class="spin"></span>' + esc(busyText);
    }
  } else {
    btn.disabled = btn.dataset.kbDisabled === '1';
    btn.classList.remove('is-busy');
    if (btn.dataset.kbOrig) {
      btn.innerHTML = btn.dataset.kbOrig;
      delete btn.dataset.kbOrig;
      delete btn.dataset.kbDisabled;
    }
  }
}

/* ---------------- 弹层 ---------------- */

/**
 * openModal(opts) -> { el, close, body }
 * opts: { title, body(Node), footer(actions Node 可空), closable(默认 true) }
 */
function openModal(opts) {
  opts = opts || {};
  var overlay = h('div', { class: 'modal-overlay' });
  var bodyWrap = h('div', { class: 'modal-body' });
  if (opts.body) { bodyWrap.appendChild(opts.body); }

  var modal = h('div', { class: 'modal card', role: 'dialog', 'aria-modal': 'true' }, [
    h('div', { class: 'modal-head' }, [
      h('h3', null, [opts.title || '']),
      h('button', { class: 'btn btn-icon', type: 'button', 'aria-label': '关闭', title: '关闭', html: icon('close') })
    ]),
    bodyWrap,
    opts.footer || h('div')
  ]);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  var closed = false;
  function close() {
    if (closed || !overlay.parentNode) { return; }
    closed = true;
    document.removeEventListener('keydown', onKey);
    overlay.parentNode.removeChild(overlay);
    if (opts.onClose) { try { opts.onClose(); } catch (e) { /* ignore */ } }
  }
  function onKey(ev) {
    if (ev.key === 'Escape' && opts.closable !== false) { close(); }
  }
  var closeBtn = qs('.modal-head .btn', modal);
  closeBtn.addEventListener('click', close);
  if (opts.closable !== false) {
    overlay.addEventListener('mousedown', function (ev) {
      if (ev.target === overlay) { close(); }
    });
  }
  document.addEventListener('keydown', onKey);
  var firstInput = qs('input, select, textarea', modal);
  if (firstInput) { setTimeout(function () { try { firstInput.focus(); } catch (e) { } }, 30); }
  return { el: overlay, modal: modal, body: bodyWrap, close: close };
}

/**
 * formModal(opts) 表单弹层
 * fields: [{name,label,type,required,min,max,step,placeholder,value,options:[{value,label}],autocomplete,pattern,title,numeric}]
 * onSubmit(values) 异步：成功自动关闭；抛错(Error)留在弹层内显示 message
 * 返回 Promise<values | null>（null = 取消）
 */
function formModal(opts) {
  opts = opts || {};
  var form = h('form', { novalidate: false });
  var errBox = h('div', { class: 'modal-err hidden' }, [
    h('span', { class: 'err-ic', html: icon('alert') }),
    h('span', { class: 'err-text' })
  ]);
  form.appendChild(errBox);

  (opts.fields || []).forEach(function (f) {
    var attrs = {
      class: 'input' + (f.type === 'textarea' ? '' : ''),
      name: f.name,
      placeholder: f.placeholder || '',
      required: f.required ? '' : null
    };
    if (f.type === 'textarea') {
      var ta = h('textarea', Object.assign({ rows: f.rows || 4 }, attrs));
      if (f.value) { ta.value = f.value; }
      form.appendChild(h('label', { class: 'field' }, [
        h('span', { class: 'field-label' }, [f.label]),
        ta
      ]));
      return;
    }
    var input;
    if (f.type === 'select') {
      input = h('select', attrs);
      (f.options || []).forEach(function (o) {
        var opt = h('option', { value: o.value }, [o.label]);
        if (String(o.value) === String(f.value)) { opt.selected = true; }
        input.appendChild(opt);
      });
    } else {
      var type = f.type || 'text';
      input = h('input', Object.assign({
        type: type,
        value: f.value !== undefined && f.value !== null ? f.value : '',
        min: f.min !== undefined ? f.min : null,
        max: f.max !== undefined ? f.max : null,
        step: f.step !== undefined ? f.step : null,
        minlength: f.minlength !== undefined ? f.minlength : null,
        pattern: f.pattern || null,
        title: f.title || null,
        autocomplete: f.autocomplete || null
      }, attrs));
    }
    form.appendChild(h('label', { class: 'field' }, [
      h('span', { class: 'field-label' }, [f.label]),
      input
    ]));
  });

  var foot = h('div', { class: 'modal-foot' });
  var cancelBtn = h('button', { class: 'btn btn-outline', type: 'button' }, [opts.cancelText || '取消']);
  var okBtn = h('button', {
    class: 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary'),
    type: 'submit'
  }, [opts.submitText || '保存']);
  foot.appendChild(cancelBtn);
  foot.appendChild(okBtn);

  var m = openModal({ title: opts.title || '', body: form, footer: foot, closable: opts.closable !== false });

  function showErr(msg) {
    errBox.classList.remove('hidden');
    qs('.err-text', errBox).textContent = msg || '操作失败，请重试';
  }

  return new Promise(function (resolve) {
    cancelBtn.addEventListener('click', function () { m.close(); resolve(null); });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      var values = {};
      (opts.fields || []).forEach(function (f) {
        var node = qs('[name="' + f.name + '"]', form);
        if (!node) { return; }
        var v = node.value;
        if (f.type === 'checkbox') { v = node.checked; }
        else if (f.numeric || f.type === 'number') { v = v === '' ? null : Number(v); }
        values[f.name] = v;
      });
      busy(okBtn, true, opts.busyText || '提交中…');
      errBox.classList.add('hidden');
      Promise.resolve()
        .then(function () { return opts.onSubmit ? opts.onSubmit(values) : null; })
        .then(function () { m.close(); resolve(values); })
        .catch(function (e) {
          busy(okBtn, false);
          showErr(e && e.message ? e.message : '操作失败，请重试');
        });
    });
  });
}

/* ---------------- 顶层错误兜底 ---------------- */

function wireGlobalErrors() {
  if (wireGlobalErrors.done) { return; }
  wireGlobalErrors.done = true;
  window.addEventListener('error', function (ev) {
    if (ev.target && ev.target !== window) { return; } // 资源加载错误忽略
    try { toast('页面发生异常：' + (ev.message || '未知错误'), 'error'); } catch (e) { /* ignore */ }
  });
  window.addEventListener('unhandledrejection', function (ev) {
    var e = ev.reason;
    if (!e) { return; }
    var msg = (e && e.message) ? e.message : String(e);
    if (e && e.status === 401) { return; } // 已在 api() 内处理
    try { toast('操作失败：' + msg, 'error'); } catch (ex) { /* ignore */ }
  });
}

/* ---------------- Service Worker（仅 HTTPS 注册） ---------------- */

/**
 * 按约定仅在 HTTPS 下注册；HTTP 局域网试点阶段不注册、不生效。
 * sw.js 只缓存静态壳与 /static/ 资源，绝不缓存 /api/ 动态接口。
 */
function registerSW() {
  try {
    if (location.protocol !== 'https:') { return false; }
    if (!('serviceWorker' in navigator)) { return false; }
    navigator.serviceWorker.register('/sw.js').catch(function () { /* 注册失败静默 */ });
    return true;
  } catch (e) { return false; }
}

/* ---------------- 登录/导航公共逻辑（由页面调用） ---------------- */

/** 页面启动：校验会话并渲染顶部导航 */
async function initSession(opts) {
  opts = opts || {};
  var me;
  try {
    me = await api('/api/me');
  } catch (e) {
    if (e.status === 401) { location.href = '/login'; return null; }
    throw e; // 网络错误等交页面处理
  }
  if (!me || !me.username) { location.href = '/login'; return null; }
  var root = me.role === 'root';
  var kbAdmin = me.role === 'kb_admin';
  var userChip = qs('.user-chip');
  if (userChip) {
    userChip.innerHTML = '';
    userChip.appendChild(h('span', { class: 'u-ic', html: icon('user') }));
    userChip.appendChild(h('span', { class: 'u-name' }, [me.username]));
    userChip.appendChild(h('span', { class: 'u-role' }, [root ? '系统管理员' : (kbAdmin ? '文档管理员' : '普通用户')]));
  }
  // 静态图标注入（保持 HTML 纯净，由 JS 统一放图标）
  var toggle = qs('#navToggle');
  if (toggle && !toggle.innerHTML) { toggle.innerHTML = icon('menu'); }
  var appLink = qs('#appLink');
  if (appLink && !appLink.firstChild) { appLink.insertBefore(h('span', { html: icon('chat') }), appLink.firstChild); }
  var adminLinkEl = qs('#adminLink');
  if (adminLinkEl && !adminLinkEl.firstChild) {
    adminLinkEl.insertBefore(h('span', { html: icon('shield') }), adminLinkEl.firstChild);
    adminLinkEl.hidden = !(root || kbAdmin);
  }
  var pwdBtnEl = qs('#chgPwdBtn');
  if (pwdBtnEl && !pwdBtnEl.querySelector('svg')) {
    pwdBtnEl.insertBefore(h('span', { html: icon('key') }), pwdBtnEl.firstChild);
  }
  var logoutBtnEl = qs('#logoutBtn');
  if (logoutBtnEl && !logoutBtnEl.querySelector('svg')) {
    logoutBtnEl.insertBefore(h('span', { html: icon('logout') }), logoutBtnEl.firstChild);
  }
  // 导航折叠：点菜单外任意处收起
  var nav = qs('.topnav');
  var navToggle = qs('#navToggle');
  if (nav && navToggle) {
    navToggle.addEventListener('click', function (ev) {
      ev.stopPropagation();
      nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', nav.classList.contains('open') ? 'true' : 'false');
    });
    document.addEventListener('click', function (ev) {
      if (nav.classList.contains('open') && !nav.contains(ev.target)) {
        nav.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }
  bindNavActions();
  return me;
}

/** 修改密码 / 退出登录 绑定（app/admin 均有同一结构） */
function bindNavActions() {
  var pwdBtn = qs('#chgPwdBtn');
  if (pwdBtn && !pwdBtn.dataset.bound) {
    pwdBtn.dataset.bound = '1';
    pwdBtn.addEventListener('click', function () {
      try { openChangePasswordModal(); } catch (e) {
        toast(e && e.message || '打开弹层失败', 'error');
      }
    });
  }
  var logoutBtn = qs('#logoutBtn');
  if (logoutBtn && !logoutBtn.dataset.bound) {
    logoutBtn.dataset.bound = '1';
    logoutBtn.addEventListener('click', async function () {
      busy(logoutBtn, true, '退出中…');
      try {
        await api('/api/logout', { method: 'POST', body: {} });
        location.href = '/login';
      } catch (e) {
        busy(logoutBtn, false);
        toast(e && e.message || '退出失败', 'error');
      }
    });
  }
}

function openChangePasswordModal() {
  return formModal({
    title: '修改密码',
    submitText: '确认修改',
    fields: [
      { name: 'old_password', label: '当前密码', type: 'password', required: true, autocomplete: 'current-password' },
      { name: 'new_password', label: '新密码（至少 6 位）', type: 'password', required: true, minlength: 6, autocomplete: 'new-password' },
      { name: 'confirm', label: '确认新密码', type: 'password', required: true, autocomplete: 'new-password' }
    ],
    onSubmit: async function (values) {
      if (values.new_password !== values.confirm) {
        throw new Error('两次输入的新密码不一致');
      }
      await api('/api/me/password', { method: 'POST', body: { old_password: values.old_password, new_password: values.new_password } });
      toast('密码修改成功', 'success');
    }
  });
}

/* ---------------- 导出 ---------------- */

window.KB = {
  api: api, esc: esc, fmtTime: fmtTime, fmtDate: fmtDate, fmtBytes: fmtBytes,
  fmtMs: fmtMs, excerpt: excerpt, toast: toast, busy: busy, icon: icon,
  h: h, clear: clear, qs: qs, qsa: qsa, openModal: openModal, formModal: formModal,
  registerSW: registerSW, initSession: initSession, detailText: detailText,
  openChangePasswordModal: openChangePasswordModal, wireGlobalErrors: wireGlobalErrors
};
