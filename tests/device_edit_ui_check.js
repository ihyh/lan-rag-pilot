'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/admin.js'), 'utf8');
let request, resolveRequest, rejectRequest, reloads = 0, notices = [];
function h(tag, attrs, children) {
  const node = {
    tag, attrs: attrs || {}, children: children || [], value: '', disabled: false, handlers: {},
    appendChild(child) { this.children.push(child); },
    setAttribute(key, value) { this.attrs[key] = value; },
    getAttribute(key) { return this.attrs[key]; },
    addEventListener(event, handler) { this.handlers[event] = handler; }
  };
  if (attrs && Object.hasOwn(attrs, 'value')) node.value = attrs.value;
  if (attrs && Object.hasOwn(attrs, 'disabled')) node.disabled = true;
  return node;
}
const context = vm.createContext({
  document: { addEventListener() {} }, h,
  api(url, options) {
    request = {url, options};
    return new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
  },
  toast(message, type) { notices.push({message, type}); },
  testReload() { reloads++; }
});
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  loadDocs = async function () { globalThis.testReload(); }; globalThis.testSelect = documentDeviceSelect;\n  document.addEventListener('DOMContentLoaded'"), context);
async function main() {
  const select = context.testSelect({id:42, filename:'protocol.pdf', device:'Plus_Pro', status:'ready'});
  assert.equal(select.tag, 'select');
  assert.match(select.getAttribute('aria-label'), /设备/);
  assert.equal(select.value, 'Plus_Pro');
  assert.deepEqual(Array.from(select.children, option => option.value), ['', '通用', 'PLM', 'Plus_500', 'Plus_Pro', 'LP']);
  await select.handlers.change();
  assert.equal(request, undefined, 'unchanged selection sends no request');
  select.value = '通用';
  const saving = select.handlers.change();
  assert.equal(select.disabled, true, 'selection is disabled while saving');
  assert.equal(request.url, '/api/admin/documents/42/device');
  assert.deepEqual(JSON.parse(JSON.stringify(request.options)), {method:'PATCH',body:{device:'通用',expected_device:'Plus_Pro'}});
  resolveRequest({id:42, device:'通用'});
  await saving;
  assert.equal(reloads, 1, 'successful change refreshes the list');
  assert.equal(select.disabled, false);
  const failed = context.testSelect({id:43, filename:'other.pdf', device:'LP', status:'ready'});
  failed.value = 'PLM';
  const savingFailure = failed.handlers.change();
  assert.equal(failed.disabled, true);
  rejectRequest(new Error('设备类型已更改，请刷新资料列表后重试'));
  await savingFailure;
  assert.equal(failed.value, 'LP', 'failed change restores the original device');
  assert.equal(failed.disabled, false);
  assert.ok(notices.some(n => n.type === 'error' && /设备类型已更改/.test(n.message)));
  assert.equal(reloads, 1, 'failed change does not refresh the list');
  const processing = context.testSelect({id:44, filename:'processing.pdf', device:'', status:'parsing'});
  assert.equal(processing.disabled, true, 'documents being processed cannot change device');
  console.log('device select UI checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
