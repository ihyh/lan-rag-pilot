'use strict';
// Exercise numbered history paging against synthetic responses, never real data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/app.js', 'utf8');
const html = fs.readFileSync('app/templates/app.html', 'utf8');
for (const id of ['historyPages', 'historyPageStatus']) assert.match(html, new RegExp('id="' + id + '"'));
assert.doesNotMatch(html, /id="history(?:Prev|Next)"/);
function element() {
  const classes = new Set();
  return {children:[], dataset:{}, events:{}, attrs:{}, value:'', textContent:'', disabled:false, style:{},
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},
    appendChild(child){this.children.push(child)}, addEventListener(name,fn){this.events[name]=fn},
    setAttribute(k,v){this.attrs[k]=v}, focus(){}, scrollIntoView(){}};
}
const elements={}, requests=[], notices=[];
const context=vm.createContext({
  document:{getElementById:id=>elements[id] ||= element(),addEventListener(){},createTextNode:x=>x},
  window:{matchMedia:()=>({matches:false}),addEventListener(){}},
  qs:(selector,root)=>root && root.children.find(node=>node && node['aria-current']==='page'),
  qsa:()=>[], h:(tag,attrs,children)=>Object.assign(element(),attrs,{tag,children:children||[]}),
  clear:node=>{node.children=[]}, icon:()=>'', excerpt:(text,max)=>String(text||'').slice(0,max), fmtTime:x=>x,
  toast:(text,type)=>notices.push({text,type}), busy(){},
  api:(url,opts)=>new Promise((resolve,reject)=>requests.push({url,opts,resolve,reject}))
});
const injected=source.replace("  document.addEventListener('DOMContentLoaded'", "  globalThis.historyTest={S,cacheEls,bindEvents,loadHistory,loadHistoryOnly};\n  document.addEventListener('DOMContentLoaded'");
assert.notEqual(injected,source);
vm.runInContext(injected,context);
const t=context.historyTest; t.cacheEls(); t.bindEvents();
function row(id){return {id,title:'问题 '+id,updated_at:'2026-10-30',turn_count:1,has_error:0}}
function content(node) { if(node==null)return ''; if(typeof node==='string'||typeof node==='number')return String(node); return (node.children||[]).map(content).join(''); }
function buttons(){return elements.historyPages.children.filter(node=>node && node.tag==='button');}
function numericButtons(){return buttons().filter(node=>/^\d+$/.test(content(node)));}
function number(n){return numericButtons().find(node=>content(node)===String(n));}
function aria(node,key){return node[key] ?? node.attrs?.[key];}
async function settle(){await new Promise(resolve=>setImmediate(resolve));}
(async()=>{
  let p=t.loadHistory();
  assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=0');
  requests.at(-1).resolve({items:Array.from({length:25},(_,i)=>row(i+1)),total:51}); await p;
  assert.equal(t.S.historyPage,0);
  assert.deepEqual(numericButtons().map(content),['1','2','3']);
  assert.equal(aria(number(1),'aria-current'),'page');
  assert.equal(elements.historyList.children.length,25);
  assert.match(elements.historyFoot.textContent,/51/);

  number(2).events.click();
  assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=25');
  assert.ok(numericButtons().every(node=>node.disabled), 'page controls disabled during request');
  requests.at(-1).resolve({items:[row(26)],total:51}); await settle();
  assert.equal(t.S.historyPage,1); assert.equal(aria(number(2),'aria-current'),'page');
  number(1).events.click();
  assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=0');
  requests.at(-1).resolve({items:[row(1)],total:51}); await settle();
  assert.equal(t.S.historyPage,0);

  // The last request wins, and failure preserves the previously committed page.
  const old=t.loadHistory(1),reqOld=requests.at(-1);
  const newer=t.loadHistory(2),reqNew=requests.at(-1);
  reqNew.resolve({items:[row(51)],total:51}); await newer;
  reqOld.resolve({items:[row(26)],total:51}); await old;
  assert.equal(t.S.historyPage,2); assert.equal(elements.historyList.children[0].dataset.id,'51');
  assert.equal(aria(number(3),'aria-current'),'page');
  p=t.loadHistory(1); requests.at(-1).reject(new Error('network failure')); await p;
  assert.equal(t.S.historyPage,2); assert.equal(aria(number(3),'aria-current'),'page');
  assert.equal(elements.historyList.children[0].dataset.id,'51');
  assert.ok(notices.some(n=>n.type==='error' && n.text.includes('network failure')));

  // If another client deletes the last page, move back to the new last page.
  p=t.loadHistory(2); requests.at(-1).resolve({items:[],total:25}); await settle();
  assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=0');
  requests.at(-1).resolve({items:[row(1)],total:25}); await p;
  assert.equal(t.S.historyPage,0); assert.equal(aria(number(1),'aria-current'),'page');
  assert.equal(elements.historyList.children[0].dataset.id,'1');
  p=t.loadHistoryOnly(); assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=0');
  requests.at(-1).resolve({items:[row(1)],total:25}); await p;

  // Many stored pages remain directly reachable without rendering hundreds of buttons.
  p=t.loadHistory(49); requests.at(-1).resolve({items:[row(1226)],total:2500}); await p;
  assert.equal(t.S.historyPage,49); assert.equal(aria(number(50),'aria-current'),'page');
  assert.ok(number(1) && number(49) && number(51) && number(100));
  assert.ok(numericButtons().length<=5, 'large histories use a bounded page window');
  assert.ok(content(elements.historyPages).includes('…'), 'omitted pages are indicated');
  number(100).events.click();
  assert.equal(requests.at(-1).url,'/api/conversations?limit=25&offset=2475');
  requests.at(-1).resolve({items:[row(2476)],total:2500}); await settle();
  assert.equal(t.S.historyPage,99); assert.equal(aria(number(100),'aria-current'),'page');
  assert.ok(number(1) && number(99) && numericButtons().length<=5);
  assert.ok(requests.every(req=>req.url.startsWith('/api/conversations?') && req.opts === undefined), 'pagination reads only and never writes user history');
  console.log('numbered history pagination, bounded pages and request safety passed');
})().catch(error=>{console.error(error);process.exitCode=1});
