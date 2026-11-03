'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/admin.js', 'utf8');
function element(tag, attrs = {}, children = []) {
  return {tag, attrs, children, disabled: false, classList: {add() {}, remove() {}},
    appendChild(child) { this.children.push(child); }, setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {}};
}
const body = element('div');
const nodes = {docsBody: body, docsLoading: element('div'), docFilterName: {value:''}, docFilterCount: {textContent:''}};
const documents = [
  {id:1, filename:'direct.png', source_type:'image', status:'ready', tags:JSON.stringify(['image_caption_only','image_text_direct']), ocr_state:null},
  {id:2, filename:'old.png', source_type:'image', status:'ready', tags:JSON.stringify(['image_caption_only']), ocr_state:'ready'}
];
const ctx = vm.createContext({URLSearchParams, document:{addEventListener() {}}, window:{OCRUI:{schedule(){}, open(){}}},
  qs:s => nodes[s.slice(1)] || null, qsa:() => [], h:element, icon:() => '',
  clear:e => {e.children=[]; return e;}, fmtBytes:()=>'', fmtTime:()=>'', parseOcrPages:()=>({ok:[],suspect:[],failed:[]}),
  api:async () => ({items:documents})});
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  globalThis.loadTest = loadDocs;\n  document.addEventListener('DOMContentLoaded'"), ctx);
function flatten(node) {
  if (typeof node === 'string') return [node];
  return [node, ...(node.children || []).flatMap(flatten)];
}
(async () => {
  await ctx.loadTest();
  const rows = flatten(body).filter(x => x && x.tag === 'tr' && x.attrs.dataset);
  assert.equal(rows.length, 2, flatten(body).filter(x => typeof x === 'string').join(' '));
  const direct = flatten(rows[0]);
  assert.ok(direct.includes('编辑图片说明'));
  assert.ok(!direct.includes('OCR 待核对'));
  assert.ok(direct.some(x => x && x.tag === 'button' && x.disabled));
  const old = flatten(rows[1]);
  assert.ok(old.includes('查看 / 核对OCR'));
  assert.ok(old.includes('编辑图片说明'));
  console.log('image list checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
