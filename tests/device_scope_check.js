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
const buttons = ['PLM', 'PLUSPRO', 'PLUS500'].map(device => {
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
  { id: 17, filename: 'PLUS500v2.1维修手册.pdf' }
];
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
    if (url === '/api/conversations/12') { return { document_ids: [2, 3, 13, 14], turns: [] }; }
    return { items: [], total: 0 };
  }
});
// Expose the existing closure to this isolated harness, not to the shipped browser code.
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  globalThis.testScope = { S, cacheEls, bindEvents, loadDocuments, setDocumentScope, sendQuestion };\n  document.addEventListener('DOMContentLoaded'"), context);
const ids = device => Array.from(context.deviceDocumentIds(docs, device));
assert.deepEqual(ids('PLM'), [1, 10, 11]);
assert.deepEqual(ids('PLUSPRO'), [2, 3, 13, 14]);
assert.deepEqual(ids('PLUS500'), [4, 16, 17]);
assert.deepEqual(ids('UNKNOWN'), []);
async function main() {
  const t = context.testScope;
  t.cacheEls(); t.bindEvents();
  elements.questionInput.value = '保留用户正在输入的问题';
  buttons[1].events.click();
  assert.equal(t.S.newDocumentIds.length, 0, 'loading cannot select');
  await t.loadDocuments();
  buttons[1].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [2, 3, 13, 14]);
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
  buttons[1].events.click();
  t.S.busy = true; buttons[0].events.click(); t.S.busy = false;
  assert.deepEqual(Array.from(t.S.newDocumentIds), [2, 3, 13, 14]);
  await t.sendQuestion();
  assert.deepEqual(requests[0], { question: '保留用户正在输入的问题', document_ids: [2, 3, 13, 14] });
  assert.equal(progressive[0], '分段', 'browser displays text before request completes');
  assert.ok(buttons.every(btn => btn.disabled), 'persisted scope locked');
  buttons[0].events.click();
  assert.deepEqual(Array.from(t.S.currentDocumentIds), [2, 3, 13, 14]);
  elements.questionInput.value = '继续提问';
  await t.sendQuestion();
  assert.deepEqual(requests[1], { question: '继续提问', conversation_id: 12 });
  elements.newChatBtn.events.click();
  assert.equal(t.S.currentId, null);
  assert.equal(t.S.newDocumentIds.length, 0);
  assert.ok(buttons.every(btn => btn.attrs['aria-pressed'] === 'false'));
  t.S.documents = docs.filter(doc => ![4, 16, 17].includes(doc.id));
  t.setDocumentScope([1], false);
  assert.equal(buttons[2].disabled, true);
  buttons[2].events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1], 'missing device must not reset to global');
  t.S.documents = []; t.S.documentsLoaded = false; failDocuments = true;
  await t.loadDocuments();
  assert.ok(buttons.every(btn => btn.disabled));
  console.log('Device scope PASS: aliases, exact boundaries, ambiguity, click/switch/manual/reset, payload, locking, missing/loading/error');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
