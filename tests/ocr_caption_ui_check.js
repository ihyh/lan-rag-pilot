'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/ocr.js'), 'utf8');
let job, modal, requests;
const context = vm.createContext({
  window: {}, document: {addEventListener() {}}, setTimeout, clearTimeout,
  h(tag, attrs, children) {
    return {tag, attrs:attrs || {}, children:children || [], events:{}, value:'', checked:false,
      classList:{add(){},remove(){}}, appendChild(child){this.children.push(child);},
      addEventListener(name, callback){this.events[name]=callback;}};
  },
  clear(node){node.children=[];}, busy(){}, toast(){},
  openModal(options){
    modal={el:{parentNode:true}, modal:{classList:{add(){}}}, body:options.body, footer:options.footer, close(){}};
    return modal;
  },
  async api(url, opts){
    if (!opts) {return job;}
    requests.push({url, body:opts.body}); return {};
  }
});
vm.runInContext(source, context);
function all(node) {return [node].concat((node.children || []).filter(x => x && typeof x === 'object').flatMap(all));}
function text(node) {return (node.children || []).map(x => typeof x === 'string' ? x : text(x)).join('');}
function button(label) {return all(modal.footer).find(x => x.tag === 'button' && text(x) === label);}

async function main() {
  requests=[];
  job={source_kind:'image',state:'review',caption:'上传说明',review_token:'token.json',pages:[{page:1,status:'failed',text:''}]};
  await context.window.OCRUI.open({id:1,filename:'diagram.png'}, async () => {});
  const textarea=all(modal.body).find(x => x.tag === 'textarea');
  const checkbox=all(modal.body).find(x => x.tag === 'input' && x.attrs.type === 'checkbox');
  const submit=button('确认说明并入库');
  assert.equal(textarea.value, '上传说明');
  await submit.events.click();
  assert.equal(requests.length, 0, 'requires explicit confirmation');
  checkbox.checked=true;
  textarea.value='  控制器连接设备  ';
  await submit.events.click();
  assert.equal(requests[0].url, '/api/admin/documents/1/ocr/publish-caption');
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), {caption:'控制器连接设备',review_token:'token.json',confirmed:true});

  job={source_kind:'image',state:'ready',caption_only:true,pages:[{page:1,status:'failed',text:''}]};
  await context.window.OCRUI.open({id:1,filename:'diagram.png'}, async () => {});
  assert.match(text(modal.body), /仅人工图片说明已入库，OCR 文字未入库/);
  assert.equal(button('重试图片识别'), undefined);
  assert.equal(button('确认说明并入库'), undefined);

  job={source_kind:'image',state:'failed',pages:[]};
  await context.window.OCRUI.open({id:1,filename:'diagram.png'}, async () => {});
  assert.ok(button('重试图片识别'));
  assert.equal(button('确认说明并入库'), undefined);

  job={source_kind:'image',state:'review',review_token:'token.json',pages:[{page:1,status:'ok',text:'识别文字'}]};
  await context.window.OCRUI.open({id:1,filename:'diagram.png'}, async () => {});
  assert.ok(button('确认图片文字并入库'));
  assert.equal(button('确认说明并入库'), undefined);
  console.log('OCR caption UI checks passed');
}
main().catch(error => {console.error(error); process.exitCode=1;});
