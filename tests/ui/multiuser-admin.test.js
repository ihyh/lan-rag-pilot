import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {JSDOM} from 'jsdom';

const live=resolve('..','..','app'),app=process.env.MULTIUSER_TEST_APP||live;
const read=p=>readFile(join(existsSync(join(app,p))?app:live,p),'utf8');
const flush=()=>new Promise(r=>setTimeout(r,25));
const overview=()=>({counts:{users:2,documents:1,chats:0,chats_today:0,uploads_bytes:0},model:{embed_backend:'mock',model_ready:true,max_upload_mb:25},settings:{},latency:null});

async function page(name='admin') {
  const dom=new JSDOM(await read('templates/'+name+'.html'),{url:'http://localhost:8088/'+(name==='users'?'admin?view=users':name),runScripts:'outside-only'});
  const w=dom.window,style=w.document.createElement('style');
  style.textContent=await read('static/css/app.css');w.document.head.append(style);
  w.eval(await read('static/js/common.js'));Object.assign(w,w.KB);
  return dom;
}
async function boot(role,api,name='users') {
  const dom=await page(name),w=dom.window;
  w.initSession=async()=>({role,username:'test'});w.api=api;w.registerSW=()=>false;w.wireGlobalErrors=()=>{};
  w.eval(await read('static/js/'+(name==='users'?'users':'admin')+'.js'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));await flush();return dom;
}
const button=(node,label)=>[...node.querySelectorAll('button')].find(b=>b.textContent.trim()===label);
const submit=(w,form)=>form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));

test('navigation removes the lone shield and retains role-based visibility',async()=>{
  for(const name of ['app','admin','library','users']) {
    const dom=await page(name),w=dom.window,d=w.document;
    w.fetch=async()=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({username:'operator',role:'user'})});
    await w.initSession();assert.equal(d.querySelector('#adminLink svg'),null);
    assert.equal(d.querySelector('#usersLink').hidden,true);
    assert.equal(d.querySelector('#adminLink').hidden,true);assert.equal(w.getComputedStyle(d.querySelector('#adminLink')).display,'none');
    dom.window.close();
  }
});

test('root can create and maintain accounts, groups and document grants through the restored UI',async()=>{
  const calls=[],users=[{id:1,username:'root',role:'root',is_active:true},{id:7,username:'alice',role:'user',is_active:true}];
  const groups=[{id:3,name:'team',description:'',member_ids:[7],granted_document_count:0}];let visibility='shared';
  const api=async(path,opts={})=>{
    calls.push({path,...opts});
    if(path==='/api/admin/overview')return overview();
    if(path==='/api/admin/users'){
      if(opts.method==='POST'){
        if(opts.body.username==='exists')throw Error('用户名已存在');
        users.push({id:8,...opts.body,is_active:true});return users.at(-1);
      }
      return {items:users};
    }
    if(path.startsWith('/api/admin/users/')){
      Object.assign(users.find(u=>u.id===Number(path.split('/').at(-1))),opts.body);return {};
    }
    if(path==='/api/admin/groups'){
      if(opts.method==='POST'){groups.push({id:4,...opts.body,member_ids:[],granted_document_count:0});return groups.at(-1);}
      return {items:groups,candidates:users};
    }
    if(path==='/api/admin/groups/3/members'){groups[0].member_ids=opts.body.user_ids;return {};}
    if(path==='/api/admin/documents')return {items:[{id:5,filename:'manual.pdf',visibility,status:'ready',tags:'[]',size_bytes:12}]};
    if(path==='/api/admin/documents/5/access'){
      if(opts.method==='PUT'){visibility=opts.body.visibility;return {};}
      return {visibility,granted_user_ids:[],granted_group_ids:[],candidates:users,group_candidates:groups};
    }
    throw Error('unexpected '+path);
  };
  const dom=await boot('root',api);
  const w=dom.window,d=w.document,el=id=>d.getElementById(id),modal=()=>d.querySelector('.modal-overlay');
  el('tabBtn-users').click();await flush();assert.equal(el('usersBody').querySelectorAll('tbody tr').length,2);
  el('nu-username').value='engineer';el('nu-password').value='synthetic-password';el('nu-role').value='kb_admin';
  submit(w,el('createUserForm'));await flush();assert.equal(users.at(-1).role,'kb_admin');assert.equal(el('createUserBtn').disabled,false);
  let row=el('usersBody').querySelectorAll('tbody tr')[2];button(row,'设置角色').click();
  modal().querySelector('[name="role"]').value='user';modal().querySelector('.modal-foot .btn-primary').click();await flush();
  assert.equal(users.at(-1).role,'user');
  row=el('usersBody').querySelectorAll('tbody tr')[2];button(row,'停用').click();await flush();assert.equal(users.at(-1).is_active,false);
  row=el('usersBody').querySelectorAll('tbody tr')[2];button(row,'重置密码').click();
  modal().querySelector('[name="password"]').value='replacement-test-password';modal().querySelector('.modal-foot .btn-primary').click();await flush();
  assert.ok(calls.some(c=>c.path==='/api/admin/users/8'&&c.body?.password==='replacement-test-password'));

  el('tabBtn-groups').click();await flush();assert.match(el('groupsBody').textContent,/team/);
  el('ng-name').value='new-team';el('ng-description').value='test';submit(w,el('createGroupForm'));await flush();assert.equal(groups.length,2);
  button(el('groupsBody').querySelector('tbody tr'),'管理成员').click();
  modal().querySelector('[name="user_ids"][value="8"]').checked=true;modal().querySelector('.modal-foot .btn-primary').click();await flush();
  assert.equal(JSON.stringify(groups[0].member_ids),'[7,8]');

  const docDom=await boot('root',api,'admin'),docWindow=docDom.window,docPage=docWindow.document;
  assert.equal(docPage.querySelector('#panel-users'),null);assert.equal(docPage.querySelector('#createUserForm'),null);
  docPage.querySelector('#tabBtn-docs').click();await flush();button(docPage.querySelector('#docsBody'),'设置访问').click();await flush();
  const grantModal=docPage.querySelector('.modal-overlay');
  grantModal.querySelector('[name="visibility"]').value='restricted';
  grantModal.querySelector('[name="user_ids"][value="7"]').checked=true;
  grantModal.querySelector('[name="group_ids"][value="3"]').checked=true;
  grantModal.querySelector('.modal-foot .btn-primary').click();await flush();
  const grant=calls.find(c=>c.path==='/api/admin/documents/5/access'&&c.method==='PUT');
  assert.equal(JSON.stringify(grant.body),'{"visibility":"restricted","user_ids":[7],"group_ids":[3]}');
  assert.match(docPage.querySelector('#docsBody').textContent,/仅授权账号或用户组可见/);docDom.window.close();

  el('tabBtn-users').click();await flush();el('nu-username').value='exists';el('nu-password').value='synthetic-password';
  submit(w,el('createUserForm'));await flush();assert.equal(el('nu-username').value,'exists');
  assert.equal(el('createUserBtn').disabled,false);assert.match(d.querySelector('.toast-wrap').textContent,/用户名已存在/);
  dom.window.close();
});

test('document administrators retain document management without account or authorization controls',async()=>{
  const calls=[];
  const dom=await boot('kb_admin',async path=>{
    calls.push(path);if(path==='/api/admin/documents')return {items:[{id:5,filename:'manual.pdf',visibility:'shared',status:'ready',tags:'[]'}]};throw Error(path);
  },'admin');
  const w=dom.window,d=w.document;
  for(const id of ['tabBtn-users','tabBtn-groups','panel-users','panel-groups']){
    assert.equal(d.getElementById(id),null);
  }
  assert.match(d.querySelector('#docsBody').textContent,/manual.pdf/);
  assert.equal(button(d.querySelector('#docsBody'),'设置访问'),undefined);
  assert.equal(calls.some(p=>/users|groups|access/.test(p)),false);dom.window.close();
});


test('non-root users cannot load the independent account workspace or user APIs',async()=>{
  for(const role of ['user','kb_admin']){
    const calls=[],dom=await boot(role,async path=>{calls.push(path);throw Error(path);});
    assert.equal(dom.window.document.querySelector('#usersWorkspace').hidden,true);
    assert.equal(calls.length,0);dom.window.close();
  }
});
