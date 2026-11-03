'use strict';
// Synthetic DOM/API only; no production documents or model requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../app/templates/app.html'), 'utf8');
function element() {
  const classes = new Set();
  return {
    children: [], dataset: {}, attrs: {}, events: {}, style: {}, value: '', textContent: '',
    classList: {
      add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      toggle: (x, on) => on ? classes.add(x) : classes.delete(x)
    },
    appendChild(child) { this.children.push(child); },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, fn) { this.events[key] = fn; }, focus() {}
  };
}
const elements = {};
const buttons = ['PLM', 'Plus_Pro', 'Plus_500', 'LP'].map(device => {
  assert.match(html, new RegExp('data-device="' + device + '"'));
  const btn = element(); btn.dataset.device = device; return btn;
});
assert.doesNotMatch(html, /data-question=/);
const requests = [];
const progressive = [];
const docs = [
  { id: 1, filename: 'PLM操作说明.pdf' },
  { id: 2, filename: 'plus_pro_操作说明.pdf' },
  { id: 3, filename: 'PLUS PRO-参数.pdf' },
  { id: 4, filename: 'plus-500说明.pdf' },
  { id: 5, filename: 'PLUS5000.pdf' },
  { id: 6, filename: 'XPLM.pdf' },
  { id: 7, filename: 'PLM_PLUSPRO合订.pdf' },
  { id: 8, filename: '通用说明.pdf' },
  { id: 9, filename: 'PLUSPROXY.pdf' },
  { id: 10, filename: 'PLM2.0操作说明.pdf' },
  { id: 11, filename: 'PLMv2.1维修手册.pdf' },
  { id: 12, filename: 'PLM20.pdf' },
  { id: 13, filename: 'PLUSPRO2.0操作说明.pdf' },
  { id: 14, filename: 'PLUSPROv2.1维修手册.pdf' },
  { id: 15, filename: 'PLUSPRO20.pdf' },
  { id: 16, filename: 'PLUS5002.0操作说明.pdf' },
  { id: 17, filename: 'PLUS500v2.1维修手册.pdf' },
  { id: 18, filename: 'Fortrend LP PxM Software Manual.pdf' },
  { id: 19, filename: 'LP2.0操作说明.pdf' },
  { id: 20, filename: 'LPv2.1维修手册.pdf' },
  { id: 21, filename: 'XLP.pdf' },
  { id: 22, filename: 'LP20.pdf' },
  { id: 23, filename: 'LP_PLM合订.pdf' },
  { id: 24, filename: 'Plus_Pro-名称与标注冲突.pdf', device: 'Plus_500' },
  { id: 25, filename: 'PLM_PLUSPRO合订.pdf', device: 'Plus_Pro' },
  { id: 26, filename: 'Plus_Pro-未指定设备.pdf', device: '' }
];
const tagged = { PLM: [1, 10, 11], Plus_Pro: [2, 3, 13, 14], Plus_500: [4, 16, 17], LP: [18, 19, 20] };
Object.entries(tagged).forEach(([device, docIds]) => docs.filter(doc => docIds.includes(doc.id)).forEach(doc => { doc.device = device; }));
let failDocuments = false;
const context = vm.createContext({
  document: { getElementById: id => elements[id] ||= element(), addEventListener() {}, createTextNode: x => x },
  qsa(selector, root) {
    if (selector === '[data-device]') { return buttons; }
    if (selector.startsWith('input')) {
      const boxes = (root.children || []).flatMap(x => x.children).filter(x => x && x.type === 'checkbox');
      return selector.endsWith(':checked') ? boxes.filter(x => x.checked) : boxes;
    }
    return [];
  },
  h(tag, attrs, children) { return Object.assign(element(), attrs, { children: children || [] }); },
  clear(el) { el.children = []; }, icon: () => '', toast() {}, busy() {},
  api: async (url, options) => {
    if (url === '/api/documents') { if (failDocuments) { throw Error('offline'); } return { items: docs }; }
    if (url === '/api/query?stream=true') {
      requests.push(JSON.parse(JSON.stringify(options.body)));
      options.onDelta('分'); options.onDelta('段');
      progressive.push(elements.messages.children.at(-1).children[0].textContent);
      return { conversation_id: 12 };
    }
    if (url === '/api/conversations/12') { return { document_ids: [2, 3, 13, 14, 25], turns: [] }; }
    return { items: [], total: 0 };
  }
});
// Expose the existing closure to this isolated harness, not to the shipped browser code.
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  globalThis.testScope = { S, cacheEls, bindEvents, loadDocuments, setDocumentScope, sendQuestion };\n  document.addEventListener('DOMContentLoaded'"), context);
const ids = device => Array.from(context.deviceDocumentIds(docs, device));
assert.deepEqual(ids('PLM'), [1, 10, 11]);
assert.deepEqual(ids('Plus_Pro'), [2, 3, 13, 14, 25]);
assert.deepEqual(ids('Plus_500'), [4, 16, 17, 24]);
assert.deepEqual(ids('LP'), [18, 19, 20]);
assert.deepEqual(ids('UNKNOWN'), []);
assert.deepEqual(Array.from(context.deviceDocumentIds([
  { id: 30, filename: 'Plus_Pro.pdf', device: '' },
  { id: 31, filename: 'Plus_Pro.pdf', device: 'LP' },
  { id: 32, filename: 'Plus_Pro.pdf' }
], 'Plus_Pro')), [], 'no fallback to filenames when there are no tagged documents');
assert.deepEqual(Array.from(context.deviceDocumentIds([
  { id: 33, filename: '接线图.png', device: 'Plus_Pro' }
], 'Plus_Pro')), [33], 'the explicit tag works even when the filename contains no device name');
async function main() {
  const t = context.testScope;
  t.cacheEls(); t.bindEvents();
  elements.questionInput.value = '保留用户正在输入的问题';
  buttons[1].events.click();
  assert.equal(t.S.newDocumentIds.length, 0, 'loading cannot select');
  await t.loadDocuments();
  assert.match(elements.deviceScopeHint.textContent, /11 份资料未指定设备/);
  t.S.documents = docs.concat(Array.from({length: 101}, (_, i) => ({id: 1000 + i, filename: '图.png', device: 'Plus_Pro'})));
  t.setDocumentScope([1], false);
  buttons[1].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1], 'over-limit device must not truncate or replace prior selection');
  assert.equal(t.S.scopeMode, 'selected', 'over-limit device must not fall back to all documents');
  assert.match(t.S.scopeNotice, /超过上限/);
  t.setDocumentScope([], false);
  buttons[1].events.click();
  assert.equal(t.S.scopeMode, 'selected', 'over-limit device from all-documents mode must block sending');
  assert.equal(t.S.newDocumentIds.length, 0);
  t.S.documents = docs;
  t.setDocumentScope([], false);
  assert.equal(buttons[3].disabled, false, 'LP document enables LP button');
  buttons[3].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [18, 19, 20]);
  buttons[1].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [2, 3, 13, 14, 25]);
  assert.equal(buttons[1].attrs['aria-pressed'], 'true');
  assert.equal(elements.questionInput.value, '保留用户正在输入的问题');
  buttons[0].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1, 10, 11], 'switch replaces, never unions');
  assert.equal(buttons[1].attrs['aria-pressed'], 'false');
  t.setDocumentScope([2], false);
  assert.equal(buttons[1].attrs['aria-pressed'], 'false', 'partial manual selection is not whole device');
  const box = elements.documentScopeOptions.children[0].children[0];
  box.checked = true; box.events.change();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1, 2], 'manual scope still works');
  t.setDocumentScope([26], false);
  assert.deepEqual(Array.from(t.S.newDocumentIds), [26], 'untagged documents remain manually selectable');
  buttons[1].events.click();
  t.S.busy = true; buttons[0].events.click(); t.S.busy = false;
  assert.deepEqual(Array.from(t.S.newDocumentIds), [2, 3, 13, 14, 25]);
  await t.sendQuestion();
  assert.deepEqual(requests[0], { question: '保留用户正在输入的问题', scope_mode: 'selected', document_ids: [2, 3, 13, 14, 25] });
  assert.equal(progressive[0], '分段', 'browser displays text before request completes');
  assert.ok(buttons.every(btn => btn.disabled), 'persisted scope locked');
  buttons[0].events.click();
  assert.deepEqual(Array.from(t.S.currentDocumentIds), [2, 3, 13, 14, 25]);
  elements.questionInput.value = '继续提问';
  await t.sendQuestion();
  assert.deepEqual(requests[1], { question: '继续提问', conversation_id: 12 });
  elements.newChatBtn.events.click();
  assert.equal(t.S.currentId, null);
  assert.equal(t.S.newDocumentIds.length, 0);
  assert.ok(buttons.every(btn => btn.attrs['aria-pressed'] === 'false'));
  t.S.documents = docs.filter(doc => ![4, 16, 17, 24].includes(doc.id));
  t.setDocumentScope([1], false);
  assert.equal(buttons[2].disabled, true);
  buttons[2].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1], 'missing device must not reset to global');
  const common = { id: 27, filename: 'SECS 通用协议.pdf', device: '通用' };
  t.S.documents = docs.concat([common]);
  t.setDocumentScope([], false);
  buttons[1].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [2, 3, 13, 14, 25, 27], 'Plus_Pro includes common documents');
  assert.equal(buttons[1].attrs['aria-pressed'], 'true');
  buttons[3].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [18, 19, 20, 27], 'LP includes the same common document');
  t.S.documents = [common];
  t.setDocumentScope([], false);
  buttons[1].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [27], 'a device can use common documents without dedicated ones');
  assert.ok(buttons.every(btn => btn.attrs['aria-pressed'] === 'false'), 'identical common-only scopes must not claim a different selected device');
  assert.match(elements.deviceScopeHint.textContent, /通用资料适用于多个设备/);
  t.S.documents = []; t.S.documentsLoaded = false; failDocuments = true;
  await t.loadDocuments();
  assert.ok(buttons.every(btn => btn.disabled));
  console.log('Device scope PASS: metadata, untagged/conflicting names, click/switch/manual/reset, payload, locking, missing/loading/error');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
