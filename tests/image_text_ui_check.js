'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/admin.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../app/templates/admin.html'), 'utf8');
assert.match(html, /id="imageTextFile"[^>]*type="file"/);
assert.doesNotMatch(html, /id="imageTextFile"[^>]*multiple/);
const ids = ['imageTextForm','imageTextFile','imageTextCaption','imageTextDevice','imageTextPreview','imageTextStatus',
  'imageTextConfirmed','imageTextAnalyze','imageTextSubmit','imageTextAnalysis','imageTextAnalysisText','imageTextUseAnalysis'];
const nodes = {};
for (const id of ids) {
  const classes = new Set();
  nodes[id] = {value:'', textContent:'', checked:false, disabled:false, files:[], events:{},
    classList:{add(x){classes.add(x);},remove(x){classes.delete(x);},contains(x){return classes.has(x);}},
    addEventListener(k, fn){this.events[k]=fn;}, removeAttribute(k){delete this[k];},
    reset(){for (const x of ['imageTextCaption','imageTextDevice']) nodes[x].value=''; nodes.imageTextConfirmed.checked=false; nodes.imageTextFile.files=[];}};
}
const calls=[]; let pendingAnalysis=null; let reloads=0; let revoked=[]; let failReload=false;
class FormDataMock { constructor(){this.values={};} append(k,v){this.values[k]=v;} }
const context = vm.createContext({document:{addEventListener(){}}, qs:s=>nodes[s.slice(1)],
  URL:{createObjectURL(){return 'blob:preview';},revokeObjectURL(v){revoked.push(v);}},
  FormData:FormDataMock, testReload(){reloads++; if (failReload) throw Error('refresh failed');},
  api:async (url,options)=>{calls.push({url,body:options.body.values}); if(url.endsWith('/image-analysis')) return await new Promise(resolve=>{pendingAnalysis=resolve;}); return {id:1};}
});
vm.runInContext(source.replace("  document.addEventListener('DOMContentLoaded'", "  loadDocs = async function () { globalThis.testReload(); }; refreshOverview = async function () {}; globalThis.bindTest = bindImageTextForm;\n  document.addEventListener('DOMContentLoaded'"),context);
async function main() {
  context.bindTest();
  const file={name:'diagram.png',size:123,type:'image/png'};
  nodes.imageTextFile.files=[file]; nodes.imageTextFile.events.change();
  assert.equal(nodes.imageTextPreview.src,'blob:preview');
  nodes.imageTextCaption.value='人工说明'; nodes.imageTextDevice.value='Plus_Pro';
  const first=nodes.imageTextAnalyze.events.click();
  assert.equal(calls[0].url,'/api/admin/image-analysis');
  assert.equal(calls[0].body.file,file); assert.equal(calls[0].body.caption,'人工说明');
  nodes.imageTextCaption.value='修改后的说明'; nodes.imageTextCaption.events.input();
  pendingAnalysis({analysis:'过期分析'}); await first;
  assert.equal(nodes.imageTextAnalysisText.textContent,'');
  assert.ok(nodes.imageTextAnalysis.classList.contains('hidden'));
  const staleFileRequest=nodes.imageTextAnalyze.events.click();
  const nextFile={name:'other.jpg',size:456,type:'image/jpeg'};
  nodes.imageTextFile.files=[nextFile]; nodes.imageTextFile.events.change();
  pendingAnalysis({analysis:'第一张图的分析'}); await staleFileRequest;
  assert.equal(nodes.imageTextAnalysisText.textContent,'', 'old analysis is discarded after changing the image');
  nodes.imageTextFile.files=[file]; nodes.imageTextFile.events.change();
  const second=nodes.imageTextAnalyze.events.click(); pendingAnalysis({analysis:'AI 候选说明'}); await second;
  assert.equal(nodes.imageTextCaption.value,'修改后的说明','AI analysis must not change human text');
  nodes.imageTextUseAnalysis.events.click();
  assert.equal(nodes.imageTextCaption.value,'修改后的说明\n\nAI 候选说明');
  assert.equal(nodes.imageTextConfirmed.checked,false);
  const event={preventDefault(){}};
  await nodes.imageTextForm.events.submit(event);
  assert.equal(calls.length,3,'unchecked confirmation prevents upload');
  nodes.imageTextConfirmed.checked=true;
  await nodes.imageTextForm.events.submit(event);
  assert.equal(calls[3].url,'/api/admin/documents/image-text');
  assert.equal(calls[3].body.file,file); assert.equal(calls[3].body.device,'Plus_Pro');
  assert.equal(calls[3].body.caption,'修改后的说明\n\nAI 候选说明');
  assert.match(nodes.imageTextStatus.textContent,/入库/);
  assert.doesNotMatch(nodes.imageTextStatus.textContent,/核对 OCR/);
  assert.equal(reloads,1); assert.equal(revoked.length,3);
  nodes.imageTextFile.files=[file]; nodes.imageTextFile.events.change();
  nodes.imageTextCaption.value='另一份说明'; nodes.imageTextConfirmed.checked=true; failReload=true;
  await nodes.imageTextForm.events.submit(event);
  assert.match(nodes.imageTextStatus.textContent,/已入库，但列表刷新失败/);
  console.log('image text UI checks passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
