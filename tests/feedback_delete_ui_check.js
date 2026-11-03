'use strict';
const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/admin.js','utf8');const html=fs.readFileSync('app/templates/admin.html','utf8');
const start=source.indexOf('  async function deleteFeedback('),end=source.indexOf('  async function loadAudit(',start);
let accepted=false,calls=[],refresh=0,errors=[];
const ctx=vm.createContext({window:{confirm:()=>accepted},busy(){},toast:(text,type)=>{if(type==='error')errors.push(text)},
 api:async(url,options)=>{calls.push({url,options})},loadAudit:async kind=>{assert.equal(kind,'feedback');refresh++}});
vm.runInContext(source.slice(start,end),ctx);
(async()=>{
 const record={id:2,feedback_hash:'hash',review_id:7};
 await ctx.deleteFeedback(record,{});assert.equal(calls.length,0);assert.equal(refresh,0);
 accepted=true;await ctx.deleteFeedback(record,{});assert.equal(calls.length,1);assert.equal(refresh,1);
 assert.equal(calls[0].url,'/api/admin/feedback/2');assert.equal(calls[0].options.method,'DELETE');
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0].options.body)),{feedback_hash:'hash',expected_review_id:7});
 ctx.api=async()=>{throw new Error('反馈或审核已变化，请刷新后重新确认')};await ctx.deleteFeedback(record,{});
 assert.equal(refresh,1);assert.match(errors[0],/刷新/);
 for(const name of ['feedback.csv','feedback-regression-candidates.jsonl','feedback-eval-cases.jsonl'])assert.ok(!html.includes(name));
 assert.match(source,/feedback_delete|deleteFeedback\(x,btn\)/);
 console.log('feedback delete confirmation and refresh UI passed');
})().catch(e=>{console.error(e);process.exitCode=1});
