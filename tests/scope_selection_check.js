'use strict';
// 合成 DOM/API：验证「全库检索 / 仅选中文档」的范围语义，不使用私有文档或真实模型。
//
// 重点不是"DOM 里有复选框"，而是这些可观察行为：
//   - 仅选中文档却零勾选：拦住发送并说明，绝不悄悄变成全库；
//   - 文档列表加载失败：保留错误与重试入口，不放宽成全库；
//   - 文件名搜索只影响列表显示，不清掉已勾选的文档；
//   - 全选/清空/仅选的作用范围与计数一致；
//   - 刷新/历史恢复显示对话真实范围（含已删除文档提示），且追问只带 conversation_id；
//   - 全库模式仍可正常发送，payload 显式携带 scope_mode。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../app/templates/app.html'), 'utf8');

// ---------- 源码/模板结构守卫 ----------
assert.match(html, /id="documentScopeBody"/, '模板有范围明细区');
assert.match(html, /name="scopeMode"[^>]*value="all"/, '模板有显式全库模式');
assert.match(html, /name="scopeMode"[^>]*value="selected"/, '模板有显式仅选中文档模式');
assert.match(html, /id="documentScopeSearch"/, '模板有文件名搜索框');
assert.match(html, /id="documentScopeSelectAll"/, '模板有全选入口');
assert.match(html, /id="documentScopeClear"/, '模板有清空入口');
assert.match(html, /id="documentScopeReload"/, '模板有列表重试入口');
assert.match(html, /id="documentScopeLock"/, '模板有会话范围锁定提示');
assert.match(html, /id="scopeModeSelected"/);
assert.doesNotMatch(html, /将使用全部文档/, '模板不得再提示失败时改用全部文档');
assert.doesNotMatch(source, /将使用全部文档/, '脚本不得再在加载失败时回退全库');
assert.match(source, /body\.scope_mode = 'selected'/);
assert.match(source, /body\.document_ids = S\.newDocumentIds\.slice\(\)/);
assert.match(source, /scopeBlockReason\(/, '发送前必须有范围校验');

// ---------- 最小 DOM/API 沙箱 ----------
function element() {
  const classes = new Set();
  return {
    children: [], dataset: {}, attrs: {}, events: {}, style: {}, value: '', textContent: '',
    innerHTML: '', checked: false, disabled: false, title: '', scrollTop: 0, scrollHeight: 0,
    classList: {
      add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
      toggle: (x, on) => { if (on === undefined) { classes.has(x) ? classes.delete(x) : classes.add(x); } else if (on) { classes.add(x); } else { classes.delete(x); } }
    },
    appendChild(child) { this.children.push(child); },
    removeChild(child) { this.children = this.children.filter(item => item !== child); },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, fn) { this.events[key] = fn; },
    focus() {}
  };
}
const elements = {};
const deviceButtons = ['PLM', 'PLUSPRO', 'PLUS500', 'LP'].map(device => {
  const btn = element();
  btn.dataset.device = device;
  return btn;
});
const docs = [
  { id: 1, filename: 'Alpha 手册.pdf', version: 'v1' },
  { id: 2, filename: 'Beta 说明.pdf' },
  { id: 3, filename: 'Gamma 规格.txt' },
  { id: 4, filename: 'Delta 报价.xlsx' },
  { id: 5, filename: 'PLM 操作手册.pdf' }
];
const requests = [];
let documentsFail = false;
let conversationDetail = null;
// 服务端在首问时固化范围：mock 也要按请求回放同样的绑定结果。
let boundScope = { document_ids: [], scope_mode: 'all' };

const context = vm.createContext({
  document: {
    getElementById: id => (elements[id] ||= element()),
    addEventListener() {},
    createTextNode: x => x
  },
  qsa(selector, root) {
    if (selector === '[data-device]') { return deviceButtons; }
    if (selector.startsWith('input')) {
      const boxes = (root.children || []).flatMap(x => x.children).filter(x => x && x.type === 'checkbox');
      return selector.endsWith(':checked') ? boxes.filter(x => x.checked) : boxes;
    }
    return [];
  },
  h(tag, attrs, children) {
    const el = Object.assign(element(), attrs);
    (children || []).forEach(child => {
      if (child && typeof child === 'object') { el.children.push(child); }
      else if (child !== null && child !== undefined) { el.textContent += String(child); }
    });
    return el;
  },
  clear(el) { el.children = []; },
  icon: () => '', toast() {}, busy() {},
  api: async (url, options) => {
    if (url === '/api/documents') {
      if (documentsFail) { throw new Error('无法连接服务器，请检查网络后重试'); }
      return { items: docs.map(doc => Object.assign({}, doc)), total: docs.length };
    }
    if (url === '/api/query?stream=true') {
      requests.push(JSON.parse(JSON.stringify(options.body)));
      if (options.body.conversation_id === undefined) {
        boundScope = {
          document_ids: (options.body.document_ids || []).slice(),
          scope_mode: options.body.scope_mode || 'all'
        };
      }
      if (options.onDelta) { options.onDelta('答'); options.onDelta('案'); }
      return { conversation_id: 99, scope_mode: boundScope.scope_mode };
    }
    if (url === '/api/conversations/99') {
      return conversationDetail
        || { id: 99, document_ids: boundScope.document_ids.slice(), scope_mode: boundScope.scope_mode, turns: [] };
    }
    if (url.startsWith('/api/conversations')) { return { items: [], total: 0 }; }
    return {};
  }
});
vm.runInContext(source.replace(
  "  document.addEventListener('DOMContentLoaded'",
  "  globalThis.testScope = { S, cacheEls, bindEvents, loadDocuments, setDocumentScope, sendQuestion, "
  + "startNewChat, resetScopeDraft, renderScope, renderConversation, scopeBlockReason };\n"
  + "  document.addEventListener('DOMContentLoaded'"
), context);

const optionRows = () => elements.documentScopeOptions.children;
const checkboxFor = id => optionRows().map(row => row.children[0]).find(box => String(box.value) === String(id));
const setFilter = value => {
  elements.documentScopeSearch.value = value;
  elements.documentScopeSearch.events.input();
};
const chooseSelectedMode = () => {
  // 真实浏览器里 radio 组会自动取消另一个选项；沙箱里手动模拟同样的状态。
  elements.scopeModeAll.checked = false;
  elements.scopeModeSelected.checked = true;
  elements.scopeModeSelected.events.change();
};
const chooseAllMode = () => {
  elements.scopeModeSelected.checked = false;
  elements.scopeModeAll.checked = true;
  elements.scopeModeAll.events.change();
};
const checkDoc = id => {
  const box = checkboxFor(id);
  assert.ok(box, `文档 ${id} 应出现在当前列表`);
  box.checked = true;
  box.events.change();
};
const lastRequest = () => requests[requests.length - 1];

async function main() {
  const t = context.testScope;
  t.cacheEls();
  t.bindEvents();
  t.renderScope();  // 真实启动流程里 loadDocuments 首次渲染前也会先按默认「全库」渲染

  // ---- 1. 初始：显式全库，未加载列表时设备按钮不可点 ----
  assert.equal(t.S.scopeMode, 'all');
  assert.equal(t.S.newDocumentIds.length, 0);
  assert.match(elements.documentScopeValue.textContent, /全库检索/);
  assert.equal(elements.scopeModeAll.checked, true);
  assert.ok(deviceButtons.every(btn => btn.disabled), '文档列表未加载时设备按钮禁用');
  await t.loadDocuments();
  assert.equal(t.S.documentsLoaded, true);
  assert.ok(deviceButtons.some(btn => !btn.disabled), '加载后可用设备按钮解除禁用');

  // ---- 2. 显式全库发送：payload 带 scope_mode，且不带 document_ids ----
  elements.questionInput.value = '全库问题';
  await t.sendQuestion();
  assert.deepEqual(lastRequest(), { question: '全库问题', scope_mode: 'all' });
  assert.match(elements.documentScopeValue.textContent, /全库检索（已固定）/);

  // ---- 3. 新对话进入「仅选中文档」：零勾选必须被拦住 ----
  elements.newChatBtn.events.click();
  assert.equal(t.S.currentId, null);
  assert.equal(t.S.scopeMode, 'all');
  chooseSelectedMode();
  assert.equal(t.S.scopeMode, 'selected');
  assert.ok(!elements.documentScopeBody.classList.contains('hidden'), '仅选中文档时显示明细区');
  const beforeBlocked = requests.length;
  elements.questionInput.value = '零勾选的问题';
  await t.sendQuestion();
  assert.equal(requests.length, beforeBlocked, '零勾选不得发出请求');
  assert.equal(elements.questionInput.value, '零勾选的问题', '拦截后保留用户输入');
  assert.match(elements.documentScopeWarn.textContent, /勾选/, '零勾选要明确说明');
  assert.match(elements.documentScopeWarn.textContent, /不会自动改用全库检索/);
  assert.equal(t.S.busy, false);

  // ---- 4. 勾选多个文档 ----
  checkDoc(1);
  checkDoc(2);
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1, 2]);
  assert.match(elements.documentScopeSelected.textContent, /已选 2 个文档/);
  assert.match(elements.documentScopeValue.textContent, /仅选中文档 · 2 个/);
  assert.equal(elements.documentScopeChips.children.length, 2, '已选文档逐个显示');

  // ---- 5. 文件名搜索只过滤显示，不清掉已勾选 ----
  setFilter('beta');
  assert.equal(t.S.docFilter, 'beta');
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1, 2], '搜索不能丢失原来的勾选');
  assert.equal(optionRows().length, 1, '列表只剩匹配项');
  assert.match(optionRows()[0].children[1].textContent, /Beta/);
  assert.equal(t.S.newDocumentIds.length, 2);
  assert.match(elements.documentScopeSelected.textContent, /已选 2 个文档/);

  // ---- 6. 全选只作用于当前筛选结果 ----
  elements.documentScopeSelectAll.events.click();
  assert.deepEqual(Array.from(t.S.newDocumentIds), [1, 2], '筛选结果已在已选中，不新增别的文档');
  setFilter('');
  assert.equal(optionRows().length, docs.length, '清空搜索后恢复完整列表');
  setFilter('a');
  elements.documentScopeSelectAll.events.click();
  const addedByFilter = t.S.newDocumentIds.filter(id => [1, 2].indexOf(id) === -1);
  assert.ok(addedByFilter.length >= 1, '全选筛选结果会加入匹配文档');
  addedByFilter.forEach(id => {
    const doc = docs.find(item => item.id === id);
    assert.ok(doc.filename.toLowerCase().includes('a'), `全选只加入筛选命中的文档，实际加入 ${doc.filename}`);
  });

  // ---- 7. 清空已选：保持仅选中文档模式，不偷偷改全库 ----
  elements.documentScopeClear.events.click();
  assert.equal(t.S.newDocumentIds.length, 0);
  assert.equal(t.S.scopeMode, 'selected', '清空勾选不得切回全库');
  assert.match(elements.documentScopeSelected.textContent, /尚未选择文档/);

  // ---- 8. 每行的「仅选」= 单文档选择 ----
  setFilter('');
  const row = optionRows()[2];
  row.children[2].events.click({ preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(Array.from(t.S.newDocumentIds), [3], '仅选按钮把范围设为单个文档');

  // ---- 9. 切回全库：已选保留但不生效，发送显式全库 ----
  chooseAllMode();
  assert.equal(t.S.scopeMode, 'all');
  assert.deepEqual(Array.from(t.S.newDocumentIds), [3], '切回全库不清掉草稿勾选');
  assert.ok(elements.documentScopeBody.classList.contains('hidden'));
  elements.questionInput.value = '切回全库';
  await t.sendQuestion();
  assert.deepEqual(lastRequest(), { question: '切回全库', scope_mode: 'all' });

  // ---- 10. 选中文档发送：payload 与锁定后的显示 ----
  elements.newChatBtn.events.click();
  chooseSelectedMode();
  checkDoc(4);
  elements.questionInput.value = '只看 Delta';
  await t.sendQuestion();
  assert.deepEqual(lastRequest(), { question: '只看 Delta', scope_mode: 'selected', document_ids: [4] });
  assert.match(elements.documentScopeValue.textContent, /仅选中文档 · 1 个（已固定）/);
  assert.equal(elements.scopeModeSelected.disabled, true, '会话开始后不能再改范围');
  assert.equal(elements.scopeModeAll.disabled, true);
  assert.ok(!elements.documentScopeLock.classList.contains('hidden'), '显示会话范围锁定提示');
  assert.match(elements.documentScopeLockText.textContent, /仅选中文档 · 1 个/);
  assert.match(elements.documentScopeLockText.textContent, /Delta/);
  assert.equal(t.S.newDocumentIds.length, 0, '锁定后清空新对话草稿');

  // ---- 11. 追问只带 conversation_id（范围由后端固化） ----
  elements.questionInput.value = '继续追问';
  await t.sendQuestion();
  assert.deepEqual(lastRequest(), { question: '继续追问', conversation_id: 99 });

  // ---- 12. 历史恢复显示真实范围；已删除文档必须提示 ----
  conversationDetail = { id: 99, document_ids: [4, 42], turns: [] };
  t.S.currentId = 99;
  t.renderConversation(conversationDetail);
  assert.match(elements.documentScopeLockText.textContent, /仅选中文档 · 2 个/);
  assert.match(elements.documentScopeLockText.textContent, /Delta/, '仍能显示可用文档名');
  assert.match(elements.documentScopeLockText.textContent, /1 个文档已删除或不可用/, '已删除文档明确提示');
  assert.ok(elements.documentScopeLockText.classList.contains('is-missing'));

  // ---- 13. 列表加载失败：不放宽成全库，保留重试入口 ----
  t.S.currentId = null;
  t.resetScopeDraft();
  chooseSelectedMode();
  documentsFail = true;
  await t.loadDocuments();
  assert.equal(t.S.documentsLoaded, false);
  assert.ok(!elements.documentScopeError.classList.contains('hidden'), '加载失败要显示错误');
  assert.match(elements.documentScopeError.textContent, /不会改用全库检索/);
  assert.ok(deviceButtons.every(btn => btn.disabled), '列表加载失败时设备按钮禁用');
  t.S.newDocumentIds = [1];
  t.S.scopeMode = 'selected';
  t.renderScope();
  const beforeFailSend = requests.length;
  elements.questionInput.value = '列表失败但已选';
  await t.sendQuestion();
  assert.equal(requests.length, beforeFailSend, '列表失败且无法确认已选文档时不得发送');
  assert.match(elements.documentScopeWarn.textContent, /文档列表未成功加载/);

  // ---- 14. 重试成功后恢复正常 ----
  documentsFail = false;
  await t.loadDocuments();
  assert.equal(t.S.documentsLoaded, true);
  assert.ok(elements.documentScopeError.classList.contains('hidden'), '重试成功后隐藏错误');

  // ---- 15. 已选文档已删除/不可用：拦住发送并说明，不回退全库 ----
  t.S.newDocumentIds = [999];
  t.renderScope();
  const beforeMissingSend = requests.length;
  elements.questionInput.value = '已删除文档';
  await t.sendQuestion();
  assert.equal(requests.length, beforeMissingSend, '已删除的已选文档不得发送');
  assert.match(elements.documentScopeWarn.textContent, /已删除或不可用/);
  assert.match(elements.documentScopeWarn.textContent, /不会回退到全库检索/);

  // ---- 16. 全库模式在列表加载失败时仍可用（范围不依赖列表） ----
  t.S.newDocumentIds = [];
  t.resetScopeDraft();
  documentsFail = true;
  await t.loadDocuments();
  assert.equal(t.S.scopeMode, 'all');
  elements.questionInput.value = '全库在失败后仍可用';
  await t.sendQuestion();
  assert.deepEqual(lastRequest(), { question: '全库在失败后仍可用', scope_mode: 'all' });

  // ---- 17. 新建对话回到全库并清掉草稿 ----
  documentsFail = false;
  elements.newChatBtn.events.click();
  assert.equal(t.S.currentId, null);
  assert.equal(t.S.scopeMode, 'all');
  assert.equal(t.S.newDocumentIds.length, 0);
  assert.equal(t.S.docFilter, '');
  assert.match(elements.documentScopeValue.textContent, /全库检索$/);
  assert.equal(elements.scopeModeAll.disabled, false);

  console.log('Scope selection PASS: 全库/仅选中文档载荷、零勾选拦截、搜索保留勾选、全选作用域、清空不改模式、单文档选择、会话锁定与历史恢复、已删除文档提示、加载失败与重试');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
