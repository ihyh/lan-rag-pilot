'use strict';

// Isolated DOM-free regression for modal confirmation buttons.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/common.js'), 'utf8');
const appHtml = fs.readFileSync(path.join(__dirname, '../app/templates/app.html'), 'utf8');
const adminHtml = fs.readFileSync(path.join(__dirname, '../app/templates/admin.html'), 'utf8');
const appCss = fs.readFileSync(path.join(__dirname, '../app/static/css/app.css'), 'utf8');
const context = vm.createContext({ window: {}, document: {} });
vm.runInContext(source, context);
const realApi = context.api;

let form;
let confirmButton;
let closeCount = 0;
let requestSubmitCount = 0;
const fields = {};

context.icon = () => '';
context.busy = () => {};
context.h = (tag, attrs, children) => {
  const classes = new Set();
  const el = {
    tag, attrs: attrs || {}, children: children || [], events: {}, dataset: {},
    value: attrs && attrs.value != null ? attrs.value : '',
    classList: {
      add: value => classes.add(value),
      remove: value => classes.delete(value)
    },
    appendChild(child) { this.children.push(child); },
    addEventListener(name, handler) { this.events[name] = handler; }
  };
  if (attrs && attrs.name) { fields[attrs.name] = el; }
  if (tag === 'form') {
    form = el;
    el.checkValidity = () => true;
    el.reportValidity = () => {};
    el.requestSubmit = () => {
      requestSubmitCount += 1;
      el.events.submit({ preventDefault() {} });
    };
  }
  if (tag === 'button' && attrs && /btn-primary/.test(attrs.class || '')) {
    confirmButton = el;
  }
  return el;
};
context.qs = selector => {
  const match = /^\[name="(.+)"\]$/.exec(selector);
  if (match) { return fields[match[1]]; }
  return { textContent: '' };
};
context.openModal = () => ({ close() { closeCount += 1; } });

async function main() {
  let submittedValues;
  const result = context.formModal({
    fields: [{ name: 'role', label: '角色', type: 'select', value: 'kb_admin', options: [] }],
    onSubmit(values) { submittedValues = values; }
  });

  assert.equal(confirmButton.attrs.type, 'button');
  assert.equal(typeof confirmButton.events.click, 'function', 'confirm button must submit its sibling form');
  fields.role.value = 'kb_admin';
  confirmButton.events.click();
  const resolved = await result;

  assert.equal(requestSubmitCount, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(submittedValues)), { role: 'kb_admin' });
  assert.deepEqual(JSON.parse(JSON.stringify(resolved)), { role: 'kb_admin' });
  assert.equal(closeCount, 1);

  for (const role of ['root', 'kb_admin', 'user']) {
    assert.match(adminHtml, new RegExp(`data-role-permission="${role}"`));
  }
  assert.match(adminHtml, /创建、停用用户，重置密码和设置角色/);
  assert.match(adminHtml, /上传、查看、重新处理和删除文档/);
  assert.match(adminHtml, /不能管理文档、用户或系统设置/);
  assert.match(appCss, /\.role-permission-grid\s*\{/);

  for (const html of [appHtml, adminHtml]) {
    assert.match(html, /<button class="user-chip" id="userChip" type="button" aria-label="查看我的权限"/);
  }
  let modalOptions;
  let focusCount = 0;
  context.openModal = options => { modalOptions = options; return { modal: {} }; };
  context.bindNavActions = () => {};
  const flatten = value => typeof value === 'string' ? value
    : (value.children || []).map(flatten).join('');
  for (const role of ['root', 'kb_admin', 'user']) {
    const chip = context.h('button', {}, []);
    chip.focus = () => { focusCount += 1; };
    context.qs = selector => selector === '.user-chip' ? chip
      : selector === '.modal-head .btn' ? { focus() { focusCount += 1; } } : null;
    context.api = async () => ({ username: 'example', role });
    await context.initSession();
    assert.equal(typeof chip.events.click, 'function');
    chip.events.click();
    assert.equal(modalOptions.title, '我的权限');
    const shown = flatten(modalOptions.body);
    assert.match(shown, new RegExp(role));
    assert.match(shown, role === 'root' ? /管理用户（创建、停用、重置密码和设置角色）/ : role === 'kb_admin' ? /上传、重新处理和删除文档/ : /不能管理文档/);
    for (const other of ['root', 'kb_admin', 'user'].filter(value => value !== role)) {
      assert.equal(shown.includes(other), false);
    }
    modalOptions.onClose();
  }
  assert.equal(focusCount, 6);
  context.TextDecoder = TextDecoder;
  context.Uint8Array = Uint8Array;
  const bytes = new TextEncoder().encode('{"type":"delta","text":"你好"}\n{"type":"done","conversation_id":7}\n');
  const splitAt = new TextEncoder().encode('{"type":"delta","text":"').length + 1;
  let chunks = [bytes.slice(0, splitAt), bytes.slice(splitAt)];
  context.fetch = async () => ({
    ok: true, status: 200, headers: { get: () => 'application/x-ndjson' },
    body: { getReader: () => ({ read: async () => chunks.length
      ? { done: false, value: chunks.shift() } : { done: true } }) }
  });
  let streamed = '';
  const done = await realApi('/api/query?stream=true', { method: 'POST', body: {}, onDelta: text => { streamed += text; } });
  assert.equal(streamed, '你好');
  assert.equal(done.conversation_id, 7);
  chunks = [new TextEncoder().encode('{"type":"error","message":"模型中断","conversation_id":8}\n')];
  await assert.rejects(realApi('/api/query?stream=true', { onDelta() {} }), error =>
    error.data.detail.conversation_id === 8 && error.message === '模型中断');
  console.log('Form modal PASS: confirm click submits once, returns values, and closes');
  console.log('Role permissions PASS: root, kb_admin, and user permissions are displayed');
  console.log('My permissions PASS: each role can open only its own permission summary');
  console.log('Stream API PASS: UTF-8 chunks, completion, and interrupted response');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
