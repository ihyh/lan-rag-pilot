'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
let modalConfig, resultCalls=[];
function h(tag,attrs={},children=[]) {
 const classes=new Set((attrs.class||'').split(/\s+/).filter(Boolean));
 return {tag,attrs,children,events:{},value:'',checked:false,textContent:'',classList:{toggle(k,on){on?classes.add(k):classes.delete(k)},contains(k){return classes.has(k)}},
 appendChild(child){this.children.push(child)},addEventListener(type,fn){this.events[type]=fn}};
}
function flatten(item){if(typeof item==='string')return [item];return [item,...(item.children||[]).flatMap(flatten)]}
const raw='第一行 HCS\n'+('长文本'.repeat(80))+'\n命令 E84_MODE\n<script>alert(1)</script>\nAUTO 最后一行';
const item={document_id:1,chunk_id:4,filename:'手册.xlsx',page:null,paragraph:52,version:'1',lifecycle:'active',applicability:'',sha256:'a',chunk_content_sha256:'b',content:raw};
const ctx=vm.createContext({window:{},h,clear:n=>{n.children=[]},busy(){},api:async(_u,args)=>{resultCalls.push(args.body);return {total:1,items:[item]}},
 openModal(config){modalConfig=config;return {modal:{classList:{add(){}}},close(){config.onClose()}}}});
vm.runInContext(fs.readFileSync('app/static/js/review-sources.js','utf8'),ctx);
(async()=>{
 const promise=ctx.window.ReviewSources.pick([]);
 const form=flatten(modalConfig.body).find(x=>x&&x.tag==='form');
 const query=flatten(form).find(x=>x&&x.tag==='input'&&x.attrs['aria-label']==='查找正确来源');
 query.value='HCS e84_mode AUTO';await form.events.submit({preventDefault(){}});
 const nodes=flatten(modalConfig.body), marks=nodes.filter(x=>x&&x.tag==='mark'), pre=nodes.find(x=>x&&x.tag==='pre');
 assert.deepEqual(marks.map(x=>x.children[0]),['HCS','E84_MODE','AUTO']);assert.equal(pre.children[0],raw);assert.equal(pre.attrs.tabindex,'0');
 assert.ok(!nodes.some(x=>x&&x.tag==='script'),'untrusted contents must remain text');
 const checkbox=nodes.find(x=>x&&x.tag==='input'&&x.attrs['aria-label']==='选择片段 4');
 const editor=nodes.find(x=>x&&x.tag==='label'&&(x.attrs.class||'').includes('review-source-editor'));
 const excerpt=nodes.find(x=>x&&x.tag==='textarea');assert.equal(editor.classList.contains('hidden'),true);assert.equal(excerpt.value,raw);
 checkbox.checked=true;checkbox.events.change();assert.equal(editor.classList.contains('hidden'),false);
 excerpt.value='命令 E84_MODE';excerpt.events.input();
 const accept=flatten(modalConfig.footer).find(x=>x&&x.tag==='button'&&x.children.includes('使用所选来源'));
 accept.events.click();const selected=await promise;
 assert.equal(selected[0].excerpt,'命令 E84_MODE');assert.equal(selected[0].chunk_content_sha256,'b');
 assert.deepEqual(JSON.parse(JSON.stringify(resultCalls[0])),{query:'HCS e84_mode AUTO',limit:20,include_archived:false});
 console.log('source result readability and exact selection passed');
})().catch(e=>{console.error(e);process.exitCode=1});
