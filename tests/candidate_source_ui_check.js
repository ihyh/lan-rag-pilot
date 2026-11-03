'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/app.js', 'utf8');
function h(tag, attrs, children) {
  return {tag, attrs:attrs || {}, children:children || [], events:{}, addEventListener(name, fn){this.events[name]=fn;}, appendChild(child){this.children.push(child);}, classList:{add(){},toggle(){}}};
}
const ctx = vm.createContext({document:{addEventListener(){},createTextNode:text=>text}, h, icon:()=>'', fmtTime:()=>'', clear:n=>{n.children=[];}});
vm.runInContext(fs.readFileSync('app/static/js/answer-format.js', 'utf8'), ctx);
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  S.me = {role:'root'}; globalThis.renderSources = sourcesNode; globalThis.renderRecord = appendRecord; globalThis.setMessages = function(n) { els.messages = n; };\n  document.addEventListener('DOMContentLoaded'"), ctx);
function text(node) {
  if (typeof node === 'string') return node;
  return (node.children || []).map(text).join(' ');
}
const image = {document_id:1, filename:'diagram.png', excerpt:'图片说明', evidence_status:'full', source_snapshot:{
  format:1, filename:'diagram.png', source_kind:'image', document_sha256:'a'.repeat(64), image_caption_only:true
}};
let targets = {};
let display = text(ctx.renderSources('没有引用', [image], targets));
assert.match(display, /检索到的候选资料/);
assert.match(display, /不能视为已引用或已核实/);
assert.match(display, /候选图片，未被回答引用/);
assert.doesNotMatch(display, /引用图片 · 点击/);
assert.equal(Object.keys(targets).length, 0);
targets = {};
display = text(ctx.renderSources('答案 [1]', [image], targets));
assert.match(display, /查看引用来源/);
assert.match(display, /引用图片 · 点击/);
assert.doesNotMatch(display, /候选资料/);
assert.ok(targets[1]);
display = text(ctx.renderSources('没有引用', [], {}));
assert.match(display, /无法确认具体来源/);
const doc = {document_id:2,filename:'manual.pdf',excerpt:'手册',evidence_status:'full',source_snapshot:{format:1,filename:'manual.pdf'}};
const duplicate = {...image, chunk_id: 9};
const bad = {...image, document_id:3, evidence_status:'snapshot_unavailable'};
let others = ctx.uncitedDocuments('答案 [1]', [doc, image, duplicate, bad]);
assert.deepEqual(Array.from(others, s => s.document_id), [1]);
display = text(ctx.renderSources('', others, null, true));
assert.match(display, /查看其他检索到的候选资料/);
assert.match(display, /未被本次回答引用/);
assert.match(display, /候选图片，未被回答引用/);
assert.doesNotMatch(display, /回答没有标注引用/);
assert.deepEqual(Array.from(ctx.uncitedDocuments('答案 [1]', [image, duplicate, doc]), s => s.document_id), [2]);
const messages = h('div');
ctx.setMessages(messages);
ctx.renderRecord({id:1,status:'ok',question:'请找图和手册',answer:'参阅手册 [1]',sources:[doc,image,duplicate]});
display=text(messages);
assert.match(display, /查看引用来源/);
assert.match(display, /查看其他检索到的候选资料/);
assert.match(display, /候选图片，未被回答引用/);
const candidatePanel=messages.children[messages.children.length-1];
function flatten(node) {return typeof node === 'string' ? [] : [node,...(node.children||[]).flatMap(flatten)];}
const imgs=flatten(candidatePanel).filter(n => n.tag==='img');
assert.equal(imgs.length,1);
assert.equal(imgs[0].attrs.src,'/api/documents/1/image?sha256='+'a'.repeat(64));
imgs[0].events.error();
assert.equal(imgs[0].hidden,true);
assert.ok(flatten(candidatePanel).some(n => n.tag==='p' && n.attrs.class==='s-image-error' && n.hidden===false));
console.log('candidate source UI checks passed');
