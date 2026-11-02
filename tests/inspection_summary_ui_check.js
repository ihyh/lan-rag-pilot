'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function node(tag, attrs = {}, children = []) {
  return {tag, attrs, children, events:{}, disabled:false,
    classList:{add(){}, remove(){}, toggle(){}},
    appendChild(item){this.children.push(item);}, addEventListener(name, fn){this.events[name]=fn;}};
}
function text(n) { return typeof n === 'string' ? n : (n.children || []).map(text).join(' '); }
const summary = node('div'), results = node('div'), button = node('button');
const context = vm.createContext({document:{addEventListener(){}}, window:{}, h:node,
  clear(n){n.children=[];}, api:async()=>({total:2, items:[{id:2, filename:'manual.pdf', lifecycle:'active', reasons:['部分片段缺少位置']} ]})});
const source = fs.readFileSync('app/static/js/library.js','utf8').replace(
  "  document.addEventListener('DOMContentLoaded'",
  "  els.qualitySummary=globalThis.summary; els.qualityCheckAllResults=globalThis.results; els.qualityCheckAll=globalThis.button; globalThis.renderTest=renderSummary; globalThis.checkTest=checkAll;\n  document.addEventListener('DOMContentLoaded'");
context.summary=summary; context.results=results; context.button=button;
vm.runInContext(source,context);

(async () => {
  context.renderTest({document:{filename:'diagram.png',status:'ready',lifecycle:'active',direct_image_description:true},
    quality:{chunk_count:1,text_chars:6,indexed_pages:[],warnings:[],ocr:{},pdf:{}}});
  assert.match(text(summary), /图片关联说明不使用页码/);
  assert.match(text(summary), /未写入的图片内容不会自动参与检索/);
  assert.doesNotMatch(text(summary), /有索引文字的页：0/);
  context.renderTest({document:{filename:'old.png',status:'ready',lifecycle:'active',source_type:'image'},
    quality:{chunk_count:1,text_chars:6,indexed_pages:[],warnings:[],ocr:{},pdf:{}}});
  assert.match(text(summary), /图片内容不使用页码/);
  context.renderTest({document:{filename:'manual.pdf',status:'ready',lifecycle:'active'},
    quality:{chunk_count:1,text_chars:6,indexed_pages:[1],warnings:[],ocr:{},pdf:{status:'not_requested'}}});
  assert.match(text(summary), /有索引文字的页：1/);
  assert.match(text(summary), /核对 PDF 原件页数/);
  await context.checkTest();
  assert.match(text(results), /检查了 2 份资料/);
  assert.match(text(results), /manual.pdf：部分片段缺少位置/);
  assert.match(text(results), /不判断原文是否读全或回答是否正确/);
  assert.equal(button.disabled, false);
  console.log('inspection summary UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
