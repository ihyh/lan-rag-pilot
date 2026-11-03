'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/admin.js','utf8');
const start = source.indexOf('  var reviewLabels =');
const end = source.indexOf('  async function loadAudit(', start);
let config, payload, sourceButton, sourceStatus, nextSelection, pickerInput;
const originalSource = {document_id:1,chunk_id:2,filename:'old.txt',excerpt:'old',sha256:'a',version:'1',lifecycle:'active',applicability:'x',chunk_content_sha256:'b'};
const newSource = {document_id:3,chunk_id:4,filename:'new.txt',excerpt:'new',sha256:'c',version:'2',lifecycle:'active',applicability:'y',chunk_content_sha256:'d'};
const old = {id:7, failure_type:'recall', expected_answer:'existing answer', evidence:'existing evidence',
  applicability:'existing scope', should_refuse:0, note:'existing note',
  snapshot:{expected_sources:[originalSource], replay_question:'existing question',replay_confirmed:true}};
const data = {feedback:{question:'原始问题 <script>',answer:'原始回答\n第二行',comment:'用户反馈',document_ids:[42],sources:[],context_requires_revalidation:true,prior_turns:[{question:'前问',answer:'前答'},{question:'再问',answer:'再答'}]},
  review:old,review_status:'confirmed',review_stale:true,feedback_hash:'hash',history:[],source_validation:{ready:true}};
const ctx = vm.createContext({window:{ReviewSources:{async pick(items){pickerInput=items;return nextSelection;}}},busy(){},toast(){},fmtTime:x=>x,JSON,Array,Object,
  api:async (url,options)=>{if(options){payload=options.body;return {}} return data;},
  formModal:options=>{config=options;},loadAudit:async()=>{},
  actionButton:(text,kind,handler)=>({text,kind,click:()=>handler(),insertAdjacentElement(position,node){assert.equal(position,'afterend');sourceStatus=node;}}),
  h:(tag,attrs,children)=>({tag,attrs,children}),
  qs:(selector,form)=>selector==='.review-advanced'?form.advanced:form.fields[selector.match(/name="([^"]+)"/)[1]]});
vm.runInContext(source.slice(start,end),ctx);
function field(value) {
 const label={textContent:''}, wrapper={
  querySelector(){return label},
  insertAdjacentElement(position,node){assert.equal(position,'afterend');sourceButton=node;}
 };
 return {value,required:false,attrs:{},events:{},setAttribute(k,v){this.attrs[k]=v},
 addEventListener(k,fn){this.events[k]=fn},closest(){return wrapper},label};
}
(async()=>{
 assert.equal((source.match(/reviewFeedback\(x,\s*btn(?:,\s*true)?\)/g) || []).length,1,'one review action');
 await ctx.reviewFeedback({id:1},{});
 assert.equal(config.advancedOpen,false);
 const values={}; for(const f of config.fields) values[f.name]=f.value || '';
 const fields={};for(const [k,v] of Object.entries(values)) fields[k]=field(v);
 const form={fields,advanced:{open:false},insertBefore(node){if(node.attrs.class==='review-original') this.original=node; else this.warning=true;},addEventListener(k,fn){this[k]=fn}};
 config.onReady(form);
 assert.equal(form.warning,true);
 assert.equal(form.original.tag,'section');
 assert.equal(form.original.attrs['aria-label'],'原始记录');
 const contents=(node)=>node==null?'':typeof node==='string'?node:(node.children||[]).map(contents).join('');
 const originalText=contents(form.original);
 for(const text of ['原问题','原始问题 <script>','原回答','原始回答\n第二行','用户补充','用户反馈','上下文与资料范围','[42]','前问','前答','再问','再答','这是追问','反馈已变化']) assert.ok(originalText.includes(text), 'missing original review context: '+text);
 const contextNode=form.original.children.find(node=>node && node.tag==='details');
 assert.ok(contextNode, 'context and document scope must be expandable');
 assert.match(contents(contextNode),/上下文与资料范围/);
 assert.equal(config.fields.find(field=>field.name==='replay_question').value,'existing question', 'existing replay question preserved');
 assert.ok(sourceButton,'choose source control is inside the review form');
 assert.equal(fields.expected_answer.required,true); assert.match(fields.expected_answer.label.textContent,/\*$/);
 assert.equal(fields.note.required,false); assert.equal(fields.answer_policy.required,false);
 fields.status.value='resolved'; fields.status.events.change(); assert.equal(fields.note.required,true);
 fields.status.value='pending'; fields.status.events.change(); assert.equal(fields.expected_answer.required,false);
 fields.replay_policy.value='yes'; fields.replay_policy.events.change();
 assert.equal(fields.answer_policy.required,true); assert.equal(fields.replay_question.required,true);
 fields.answer_policy.value='answer'; fields.answer_policy.events.change(); assert.match(fields.correct_sources.label.textContent,/\*$/);
 const section={open:false};form.invalid({target:{closest:()=>section}});assert.equal(section.open,true);
 await assert.rejects(config.onSubmit({...values,status:'pending',replay_policy:'yes',answer_policy:'answer'}),/处理状态/);

 fields.expected_answer.value='unsaved draft';
 const before=fields.correct_sources.value;
 nextSelection=null; await sourceButton.click();
 assert.equal(pickerInput[0].chunk_id,2);
 assert.equal(fields.expected_answer.value,'unsaved draft','cancelling selection preserves draft');
 assert.equal(fields.correct_sources.value,before,'cancelling selection preserves old sources');
 await config.onSubmit({...values,status:'confirmed',replay_policy:'no',answer_policy:'answer'});
 assert.equal(payload.evidence,'existing evidence');assert.equal(payload.applicability,'existing scope');
 assert.equal(payload.expected_sources[0].chunk_id,2);assert.equal(payload.replay_question,'existing question');
 assert.equal(payload.replay_confirmed,false);

 nextSelection=[newSource]; await sourceButton.click();
 assert.match(fields.correct_sources.value,/new\.txt/);
 assert.equal(fields.expected_answer.value,'unsaved draft');
 await config.onSubmit({...values,status:'confirmed',replay_policy:'yes',answer_policy:'answer',expected_answer:fields.expected_answer.value});
 assert.equal(payload.expected_answer,'unsaved draft');assert.equal(payload.replay_confirmed,true);
 assert.deepEqual(JSON.parse(JSON.stringify(payload.expected_sources)), [{document_id:3,chunk_id:4,excerpt:'new',sha256:'c',version:'2',lifecycle:'active',applicability:'y',chunk_content_sha256:'d'}]);

 nextSelection=[]; await sourceButton.click();
 assert.equal(pickerInput[0].chunk_id,4,'picker reopens with latest selection');
 await assert.rejects(config.onSubmit({...values,status:'confirmed',replay_policy:'yes',answer_policy:'answer'}),/来源/);
 await config.onSubmit({...values,status:'confirmed',replay_policy:'yes',answer_policy:'refuse'});
 assert.equal(payload.expected_sources.length,0);assert.equal(payload.should_refuse,true);

 data.source_validation={ready:false,reasons:['所选资料已变化']};
 sourceButton=null; sourceStatus=null;
 await ctx.reviewFeedback({id:1},{});
 const staleFields={};for(const f of config.fields) staleFields[f.name]=field(f.value || '');
 const staleForm={fields:staleFields,advanced:{open:false},insertBefore(){},addEventListener(){}};
 config.onReady(staleForm);
 assert.ok(sourceStatus && sourceStatus.children.join('').includes('所选资料已变化'));
 nextSelection=null; await sourceButton.click();
 assert.ok(sourceStatus.children.join('').includes('所选资料已变化'),'cancel retains validation warning');
 nextSelection=[newSource]; await sourceButton.click();
 assert.match(sourceStatus.textContent,/保存审核后.*重新核对复测状态/);
 console.log('merged feedback review and source selection checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
