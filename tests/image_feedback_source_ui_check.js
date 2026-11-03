'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/review-sources.js','utf8');
let modalConfig;
function h(tag,attrs={},children=[]) {
  return {tag,attrs,children,events:{},value:'',checked:false,textContent:'',
    classList:{toggle(){}},
    appendChild(child){this.children.push(child);},
    addEventListener(type,fn){this.events[type]=fn;}};
}
function flatten(item) {
  if (typeof item === 'string') return [item];
  return [item,...(item.children || []).flatMap(flatten)];
}
const image={document_id:1,chunk_id:2,filename:'diagram.png',source_type:'image',page:null,paragraph:null,
  line_start:null,line_end:null,content:'图说明：连接关系',version:'1.0',lifecycle:'active',applicability:'',
  sha256:'a'.repeat(64),chunk_content_sha256:'b'.repeat(64)};
const ctx=vm.createContext({window:{},h,clear:node=>{node.children=[];},busy(){},
  api:async()=>({total:1,items:[image]}),openModal(config){modalConfig=config;return {modal:{classList:{add(){}}},close(){config.onClose();}};}});
vm.runInContext(source,ctx);
(async()=>{
  const promise=ctx.window.ReviewSources.pick([]);
  const form=flatten(modalConfig.body).find(item=>item && item.tag==='form');
  const query=flatten(form).find(item=>item && item.tag==='input' && item.attrs['aria-label']==='查找正确来源');
  query.value='连接关系';
  await form.events.submit({preventDefault(){}});
  const content=flatten(modalConfig.body);
  assert.ok(content.some(item=>typeof item==='string' && item.includes('图片资料（无页码）')));
  assert.ok(!content.some(item=>typeof item==='string' && item.includes('片段起始段落 未知')));
  const checkbox=content.find(item=>item && item.tag==='input' && item.attrs['aria-label']==='选择片段 2');
  checkbox.checked=true; checkbox.events.change();
  const accept=flatten(modalConfig.footer).find(item=>item && item.tag==='button' && item.children.includes('使用所选来源'));
  accept.events.click();
  const selected=await promise;
  assert.equal(selected.length,1);
  assert.equal(selected[0].chunk_id,2);
  assert.equal(selected[0].sha256,image.sha256);
  assert.equal(selected[0].page,null);
  assert.equal(selected[0].paragraph,null);
  assert.equal(selected[0].location_kind,undefined); // location is decided by the server
  console.log('image feedback source UI checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
