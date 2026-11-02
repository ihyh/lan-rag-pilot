import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {JSDOM} from 'jsdom';

const live=resolve('..','..','app'),app=process.env.ADMIN_NAV_TEST_APP||live;
const read=p=>readFile(join(existsSync(join(app,p))?app:live,p),'utf8');
const flush=()=>new Promise(resolve=>setTimeout(resolve,25));
async function waitFor(condition) {
  for(let i=0;i<30;i++){if(condition()){await flush();return;}await flush();}
  assert.fail('navigation did not reach the expected route');
}
async function boot(hash='',role='root') {
  const dom=new JSDOM(await read('templates/admin.html'),{url:'http://localhost:8088/admin'+hash,runScripts:'outside-only'});
  const w=dom.window,calls=[];
  w.eval(await read('static/js/common.js'));Object.assign(w,w.KB);
  w.initSession=async()=>({role,username:'test'});w.registerSW=()=>false;w.wireGlobalErrors=()=>{};w.toast=()=>{};
  w.api=async path=>{
    calls.push(path);
    if(path==='/api/admin/overview')return {counts:{users:2,documents:0,chats:0,chats_today:0,uploads_bytes:0},model:{embed_backend:'mock',model_ready:true,max_upload_mb:25},settings:{},latency:null};
    if(path==='/api/admin/documents')return {items:[]};
    if(/^\/api\/admin\/(conversations|feedback|audit)\?/.test(path))return {items:[],total:0};
    throw Error('unexpected '+path);
  };
  w.eval(await read('static/js/admin.js'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));await flush();
  return {dom,w,d:w.document,calls};
}
function selected(d,tab,view) {
  assert.equal(d.querySelectorAll('[data-tab].is-active').length,1);
  assert.equal(d.querySelector('[data-tab].is-active').dataset.tab,tab);
  assert.equal(d.querySelector('[data-tab][aria-selected="true"]').dataset.tab,tab);
  if(tab==='docs'){
    assert.equal(d.querySelector('[data-doc-view][aria-current="page"]').dataset.docView,view);
    for(const id of ['documentList','fileImport','imageTextImport'])assert.equal(d.getElementById(id).classList.contains('hidden'),id!==view);
  }
  if(tab==='audit'){
    assert.equal(d.querySelector('[data-subtab][aria-selected="true"]').dataset.subtab,view);
    assert.equal(d.querySelector('.subtab-panel.is-active').id,'subpanel-'+view);
  }
}

test('a document deep link and reload restore both parent and child without loading the overview',async()=>{
  const first=await boot('#docs/upload');
  try{
    selected(first.d,'docs','fileImport');assert.equal(first.calls.includes('/api/admin/overview'),false);
    assert.equal(first.w.location.hash,'#docs/upload');assert.equal(first.w.history.length,1);
    const refreshed=await boot(first.w.location.hash);
    try{selected(refreshed.d,'docs','fileImport');}finally{refreshed.dom.window.close();}
  }finally{first.dom.window.close();}
});

test('every audit deep link restores its own parent and child after reload',async()=>{
  for(const view of ['chats','feedback','logs']){
    const p=await boot('#audit/'+view);
    try{
      selected(p.d,'audit',view);assert.equal(p.calls.includes('/api/admin/overview'),false);
      assert.equal(p.calls.length,1);assert.equal(p.w.history.length,1);
      const refreshed=await boot(p.w.location.hash);
      try{selected(refreshed.d,'audit',view);}finally{refreshed.dom.window.close();}
    }finally{p.dom.window.close();}
  }
});

test('parent menus remember children and browser history restores selection without duplicate requests',async()=>{
  const {dom,w,d,calls}=await boot('#docs/list');
  try{
    d.querySelector('[data-doc-view="fileImport"]').click();await flush();assert.equal(w.location.hash,'#docs/upload');
    d.getElementById('tabBtn-audit').click();await flush();selected(d,'audit','chats');
    d.querySelector('[data-subtab="feedback"]').click();await flush();selected(d,'audit','feedback');assert.equal(w.location.hash,'#audit/feedback');
    d.getElementById('tabBtn-docs').click();await flush();selected(d,'docs','fileImport');
    const feedbackBefore=calls.filter(p=>p.startsWith('/api/admin/feedback?')).length;
    w.history.back();await waitFor(()=>w.location.hash==='#audit/feedback');selected(d,'audit','feedback');
    assert.equal(calls.filter(p=>p.startsWith('/api/admin/feedback?')).length,feedbackBefore+1);
    w.history.forward();await waitFor(()=>w.location.hash==='#docs/upload');selected(d,'docs','fileImport');
    const length=w.history.length,count=calls.length;d.getElementById('tabBtn-docs').click();await flush();
    assert.equal(w.history.length,length);assert.equal(calls.length,count);
    w.location.hash='audit/logs';await waitFor(()=>d.querySelector('[data-subtab="logs"]').getAttribute('aria-selected')==='true');selected(d,'audit','logs');
    assert.ok(calls.some(p=>p.startsWith('/api/admin/audit?')));
  }finally{dom.window.close();}
});

test('legacy links are normalized and invalid fragments do not create extra history entries',async()=>{
  for(const hash of ['#fileImport','#webImport']){
    const p=await boot(hash);try{selected(p.d,'docs','fileImport');assert.equal(p.w.location.hash,'#docs/upload');assert.equal(p.w.history.length,1);}finally{p.dom.window.close();}
  }
  const p=await boot('#unknown');try{selected(p.d,'overview');assert.equal(p.w.location.hash,'#overview');assert.equal(p.w.history.length,1);}finally{p.dom.window.close();}
});

test('restricted routes and hidden parent buttons cannot trigger root APIs for document administrators',async()=>{
  const p=await boot('#audit/feedback','kb_admin');
  try{
    selected(p.d,'docs','documentList');assert.equal(p.w.location.hash,'#docs/list');assert.equal(p.d.getElementById('tabBtn-audit').hidden,true);
    p.d.getElementById('tabBtn-audit').click();await flush();
    p.w.location.hash='overview';await waitFor(()=>p.w.location.hash==='#docs/list');
    assert.equal(p.calls.some(path=>/overview|feedback|conversations|audit/.test(path)),false);
    p.w.location.hash='imageTextImport';await waitFor(()=>p.w.location.hash==='#docs/images');selected(p.d,'docs','imageTextImport');
  }finally{p.dom.window.close();}
});
