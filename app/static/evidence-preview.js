'use strict';
const el = id => document.getElementById(id);
let requestId = null;
let busy = false;
const statuses = {source_excerpt: '资料原文（未核验语义）', partial: '资料原文保留；现场证据不足', insufficient: '证据不足', needs_clarification: '请明确用途'};
function reset() {
  requestId = null;
  el('candidates').replaceChildren();
  el('result').hidden = true;
  el('answer').textContent = '';
  el('sources').replaceChildren();
  el('render').disabled = true;
}
async function api(path, body) {
  const response = await fetch('/api/preview/' + path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  if (!response.ok) {
    const error = await response.json();
    if (response.status === 401 || response.status === 403) reset();
    throw new Error(typeof error.detail === 'string' ? error.detail : '输入格式错误，请重新检索。');
  }
  return response;
}
async function act(work) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = true; });
  el('notice').textContent = '处理中…';
  try { await work(); } catch (error) { reset(); el('notice').textContent = error.message; }
  finally {
    busy = false;
    document.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = false; });
    el('render').disabled = !requestId;
  }
}
async function documents() {
  const data = await (await api('documents')).json();
  el('document').replaceChildren(new Option('所有已授权文档', ''));
  data.documents.forEach(doc => el('document').add(new Option(doc.filename, String(doc.id))));
  el('login-panel').hidden = true;
  el('workspace').hidden = false;
  el('notice').textContent = '已进入独立预览。';
}
el('login-form').addEventListener('submit', event => {
  event.preventDefault();
  act(async () => {
    await api('session', {token: el('token').value});
    el('token').value = '';
    reset();
    await documents();
  });
});
el('logout').addEventListener('click', () => act(async () => {
  await api('logout', {});
  reset(); el('workspace').hidden = true; el('login-panel').hidden = false;
  el('notice').textContent = '已退出预览。';
}));
el('search-form').addEventListener('input', reset);
el('search-form').addEventListener('change', reset);
el('search-form').addEventListener('submit', event => {
  event.preventDefault();
  act(async () => {
    reset();
    const purposes = [...document.querySelectorAll('input[name=purpose]:checked')].map(input => input.value);
    if (!purposes.length) throw new Error('请至少选择一项用途。');
    const data = await (await api('search', {
      question: el('question').value, purposes,
      document_ids: el('document').value ? [Number(el('document').value)] : []
    })).json();
    requestId = data.request_id;
    data.candidates.forEach(source => {
      const item = document.createElement('div'); item.className = 'candidate';
      const label = document.createElement('label');
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = true;
      input.dataset.sourceId = String(source.source_id);
      input.addEventListener('change', () => { el('result').hidden = true; });
      label.append(input, document.createTextNode(' [' + source.source_id + '] ' + source.filename + ' · 页 ' + (source.page ?? '未记录')));
      const details = document.createElement('details');
      const summary = document.createElement('summary'); summary.textContent = '查看完整检索片段';
      const text = document.createElement('pre'); text.textContent = source.content;
      details.append(summary, text); item.append(label, details); el('candidates').append(item);
    });
    el('notice').textContent = data.message + ' 召回 ' + data.candidates.length + ' 个片段。';
  });
});
el('render').addEventListener('click', () => act(async () => {
  el('result').hidden = true;
  const response = await api('render', {
    request_id: requestId,
    source_ids: [...el('candidates').querySelectorAll('input:checked')].map(input => Number(input.dataset.sourceId)),
    stream: el('stream').checked
  });
  let answer, sources, status;
  if (el('stream').checked) {
    // Buffer the entire checked response: incomplete/malformed frames never reach the UI.
    const events = (await response.text()).trim().split('\n').map(line => JSON.parse(line));
    if (events.length !== 3 || events[0].type !== 'sources' || events[1].type !== 'delta' || events[2].type !== 'done'
        || events[2].semantic_verified !== false || events[2].onsite_verified !== false) throw new Error('响应不完整，请重新检索。');
    sources = events[0].sources; answer = events[1].text; status = events[2].status;
  } else {
    const data = await response.json(); sources = data.sources; answer = data.answer; status = data.status;
  }
  el('answer').textContent = answer;
  el('sources').replaceChildren();
  const bySourceId = new Map(sources.map(source => [source.source_id, source]));
  bySourceId.forEach(source => {
    const row = document.createElement('li');
    row.textContent = '[' + source.source_id + '] ' + source.filename + ' · 版本 ' + source.document_version
      + ' · 页 ' + (source.page ?? '未记录') + ' · 段 ' + (source.paragraph ?? '未记录');
    el('sources').append(row);
  });
  el('result-status').textContent = statuses[status] ?? status;
  el('result').hidden = false;
  el('notice').textContent = '已完成来源与授权检查后展示；不是现场结论。';
}));
