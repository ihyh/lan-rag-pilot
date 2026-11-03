'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/admin.js', 'utf8');
const html = fs.readFileSync('app/templates/admin.html', 'utf8');
assert.doesNotMatch(html, /id="docCaption"/);
const nodes = {
  fileChips: {children: [], appendChild(node) { this.children.push(node); }},
  fileInput: {value: ''}, docVersion: {value: '1.0'}, docDevice: {value: 'Plus_Pro'}
};
const calls = [], notices = [];
class FormDataMock { constructor() { this.values = {}; } append(key, value) { this.values[key] = value; } }
function h(tag, attrs, children = []) {
  const node = {tag, attrs, children, className: '', appendChild(child) { this.children.push(child); }};
  node.firstChild = children[0];
  return node;
}
let pdfOcr = false;
const ctx = vm.createContext({
  document: {addEventListener() {}}, FormData: FormDataMock, h, icon: () => '',
  qs: selector => nodes[selector.slice(1)], clear: node => { node.children = []; }, toast: (message,kind) => notices.push({message,kind}),
  window: {OCRUI: {selected: file => /\.pdf$/i.test(file.name) && pdfOcr}},
  api: async (url, options) => { calls.push({url, fields: options.body.values}); }
});
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'",
  "  loadDocs = async function () {}; refreshOverview = async function () {}; globalThis.uploadTest = uploadFiles;\n  document.addEventListener('DOMContentLoaded'"), ctx);
(async () => {
  await ctx.uploadTest([{name: 'a.png'}, {name: 'b.jpg'}, {name:'notes.txt'}, {name:'normal.pdf'}]);
  assert.deepEqual(calls.map(call => call.url), ['/api/admin/documents/ocr', '/api/admin/documents/ocr', '/api/admin/documents', '/api/admin/documents']);
  assert.ok(calls.every(call => !Object.hasOwn(call.fields, 'caption')));
  assert.ok(calls.every(call => call.fields.device === 'Plus_Pro'));
  pdfOcr = true;
  await ctx.uploadTest([{name: 'scan.pdf'}]);
  assert.equal(calls[4].url, '/api/admin/documents/ocr');
  ctx.window.OCRUI = null;
  await ctx.uploadTest([{name: 'broken.png'}]);
  assert.equal(calls.length, 5, 'image does not bypass OCR when the module is missing');
  assert.equal(nodes.fileChips.children[0].className, 'chip chip-err chip-block');
  assert.ok(notices.some(notice => notice.kind === 'error'));
  console.log('ordinary image OCR and mixed upload flow checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
