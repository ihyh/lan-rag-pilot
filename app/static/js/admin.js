/* root 管理页：概览、文档、用户、问答与审计。无前端依赖。 */
'use strict';

(function () {
  var currentUser = null;

  function refreshOverview() {
    return currentUser && currentUser.role === 'root' ? loadOverview() : Promise.resolve();
  }
  function cell(text, cls) { return h('td', { class: cls || '' }, [text == null ? '—' : String(text)]); }
  function badge(text, cls) {
    return h('span', { class: 'badge ' + cls }, [h('span', { class: 'dot' }), text]);
  }
  function empty(text) {
    return h('div', { class: 'empty' }, [h('span', { html: icon('folder') }), h('span', {}, [text])]);
  }
  function table(headers, rows) {
    var head = h('thead', {}, [h('tr', {}, headers.map(function (x) { return h('th', {}, [x]); }))]);
    return h('div', { class: 'table-wrap' }, [h('table', { class: 'tbl' }, [head, h('tbody', {}, rows)])]);
  }
  function actionButton(text, kind, handler) {
    var btn = h('button', { class: 'btn btn-sm ' + (kind || 'btn-outline'), type: 'button' }, [text]);
    btn.addEventListener('click', handler);
    return btn;
  }
  function kv(k, v) {
    return h('div', { class: 'kv' }, [h('div', { class: 'kv-k' }, [k]), h('div', { class: 'kv-v' }, [v == null ? '—' : String(v)])]);
  }

  async function loadOverview() {
    var loading = qs('#overviewLoading');
    var content = qs('#overviewContent');
    var error = qs('#overviewError');
    loading.classList.remove('hidden'); content.classList.add('hidden'); error.classList.add('hidden');
    try {
      var data = await api('/api/admin/overview');
      var c = data.counts;
      var cards = [
        ['用户', c.users, 'user'], ['文档', c.documents, 'doc'], ['切片', c.chunks, 'layers'],
        ['问答总数', c.chats, 'chat'], ['今日问答', c.chats_today, 'clock'], ['文件容量', fmtBytes(c.uploads_bytes), 'folder']
      ];
      clear(qs('#statCards'));
      cards.forEach(function (x) {
        qs('#statCards').appendChild(h('div', { class: 'card stat-card' }, [
          h('span', { class: 'stat-icon', html: icon(x[2]) }),
          h('span', { class: 'stat-num' }, [x[1]]),
          h('span', { class: 'stat-label' }, [x[0]])
        ]));
      });
      var m = data.model;
      clear(qs('#modelCard')).appendChild(h('div', {}, [
        h('div', { class: 'model-state-line' }, [
          badge(m.model_ready ? '嵌入模型已就绪' : '嵌入模型未就绪', m.model_ready ? 'b-ok' : 'b-warn'),
          h('span', { class: 'model-msg' }, [m.model_message || '—'])
        ]),
        h('div', { class: 'kv-grid' }, [
          kv('应用版本', m.version), kv('嵌入模型', m.embed_model), kv('嵌入后端', m.embed_backend),
          kv('生成模型', m.llm_model), kv('切块', m.chunk_max_tokens + ' / 重叠 ' + m.chunk_overlap_tokens),
          kv('最低相似度', m.min_relevance_score),
          kv('对外地址', m.public_origin || '未配置')
        ])
      ]));
      Object.keys(data.settings).forEach(function (key) {
        var input = qs('#cfg-' + key); if (input) { input.value = data.settings[key]; }
      });
      qs('#maxUploadMB').textContent = m.max_upload_mb;
      qs('#uploadMeta').textContent = '最大 ' + m.max_upload_mb + ' MB / 文件';
      content.classList.remove('hidden');
    } catch (e) {
      error.textContent = e.message || '概览加载失败'; error.classList.remove('hidden');
    } finally { loading.classList.add('hidden'); }
  }

  async function loadDocs() {
    var loading = qs('#docsLoading'); var body = qs('#docsBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var params = new URLSearchParams();
      var filterVersion = qs('#docFilterVersion').value.trim();
      var filterTag = qs('#docFilterTag').value.trim();
      var filterFrom = qs('#docFilterFrom').value;
      var filterTo = qs('#docFilterTo').value;
      if (filterVersion) { params.set('version', filterVersion); }
      if (filterTag) { params.set('tag', filterTag); }
      if (filterFrom) { params.set('effective_date_from', filterFrom); }
      if (filterTo) { params.set('effective_date_to', filterTo); }
      var suffix = params.toString() ? '?' + params.toString() : '';
      var data = await api('/api/admin/documents' + suffix);
      if (!data.items.length) { body.appendChild(empty('尚未上传文档')); return; }
      var rows = data.items.map(function (d) {
        var actions = h('td', { class: 'cell-actions' });
        actions.appendChild(actionButton('设置知识库', 'btn-outline', function () { setDocumentKnowledgeBases(d); }));
        actions.appendChild(actionButton('重建索引', 'btn-outline', function () { reindexDoc(d.id, d.filename); }));
        actions.appendChild(actionButton('删除', 'btn-danger', function () { removeDoc(d.id, d.filename); }));
        var state = d.status === 'ready' ? badge('就绪', 'b-ok') : d.status === 'failed' ? badge('失败', 'b-err') : badge('处理中', 'b-warn');
        var name = h('td', { class: 'cell-main', title: d.filename }, [d.filename]);
        if (d.error) { name.appendChild(h('div', { class: 'doc-err', title: d.error }, [d.error])); }
        return h('tr', {}, [name, cell(d.version || '1.0'), cell(d.effective_date || '—'), cell((d.tags || []).join('、') || '—'), cell(fmtBytes(d.size_bytes)), cell(d.num_chunks), h('td', {}, [state]), cell(d.knowledge_base_names || '默认知识库'), cell(d.uploaded_by_name), cell(fmtTime(d.created_at)), actions]);
      });
      body.appendChild(table(['文件', '版本', '生效日期', '标签', '大小', '切片', '状态', '知识库', '上传者', '上传时间', '操作'], rows));
    } catch (e) { body.appendChild(empty(e.message || '文档加载失败')); }
    finally { loading.classList.add('hidden'); }
  }

  async function loadUploadKnowledgeBases() {
    var data = await api('/api/knowledge-bases');
    var select = qs('#docKnowledgeBase');
    var selected = select.value;
    clear(select);
    if (currentUser.role === 'root') {
      select.appendChild(h('option', { value: '' }, ['默认知识库']));
    }
    data.items.forEach(function (k) {
      select.appendChild(h('option', { value: k.id }, [k.name + '（' + k.department_name + '）']));
    });
    if (selected && data.items.some(function (k) { return String(k.id) === selected; })) {
      select.value = selected;
    }
  }

  async function uploadFiles(files) {
    var chips = qs('#fileChips'); clear(chips);
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      var chip = h('span', { class: 'chip' }, [h('span', { class: 'spin' }), h('span', { class: 'chip-name', title: file.name }, [file.name])]);
      chips.appendChild(chip);
      try {
        var form = new FormData(); form.append('file', file, file.name);
        form.append('version', qs('#docVersion').value.trim() || '1.0');
        var effectiveDate = qs('#docEffectiveDate').value;
        if (effectiveDate) { form.append('effective_date', effectiveDate); }
        form.append('tags', qs('#docTags').value.trim());
        var knowledgeBaseId = qs('#docKnowledgeBase').value;
        if (knowledgeBaseId) { form.append('knowledge_base_id', knowledgeBaseId); }
        await api('/api/admin/documents', { method: 'POST', body: form });
        chip.className = 'chip chip-ok'; chip.firstChild.innerHTML = icon('check');
      } catch (e) {
        chip.className = 'chip chip-err'; chip.firstChild.innerHTML = icon('alert'); chip.title = e.message;
      }
    }
    qs('#fileInput').value = ''; await loadDocs(); await refreshOverview();
  }
  async function reindexDoc(id, name) {
    if (!window.confirm('重新索引《' + name + '》？')) { return; }
    try {
      await api('/api/admin/documents/' + id + '/reindex', { method: 'POST', body: {} });
      toast('重新索引完成', 'success'); await loadDocs(); await refreshOverview();
    } catch (e) { toast(e.message || '重新索引失败', 'error'); }
  }
  async function removeDoc(id, name) {
    if (!window.confirm('确认删除《' + name + '》及全部切片？此操作不可撤销。')) { return; }
    try {
      await api('/api/admin/documents/' + id, { method: 'DELETE' });
      toast('文档已删除', 'success'); await loadDocs(); await refreshOverview();
    } catch (e) { toast(e.message || '删除失败', 'error'); }
  }

  async function loadUsers() {
    var loading = qs('#usersLoading'); var body = qs('#usersBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var data = await api('/api/admin/users');
      var rows = data.items.map(function (u) {
        var actions = h('td', { class: 'cell-actions' });
        actions.appendChild(actionButton('设置部门', 'btn-outline', function () { setUserDepartments(u); }));
        actions.appendChild(actionButton('重置密码', 'btn-outline', function () { resetPassword(u); }));
        actions.appendChild(actionButton(u.is_active ? '停用' : '启用', u.is_active ? 'btn-danger' : 'btn-outline', function () { patchUser(u.id, { is_active: !u.is_active }); }));
        actions.appendChild(actionButton('设置角色', 'btn-outline', function () { setUserRole(u); }));
        return h('tr', {}, [
          cell(u.username, 'cell-main'), h('td', {}, [h('span', { class: 'tag-role tag-' + u.role }, [u.role])]),
          h('td', {}, [badge(u.is_active ? '启用' : '停用', u.is_active ? 'b-ok' : 'b-muted')]),
          cell(u.department_names || '默认部门'), cell(fmtTime(u.last_login_at)), cell(fmtTime(u.created_at)), actions
        ]);
      });
      body.appendChild(table(['用户名', '角色', '状态', '部门', '最近登录', '创建时间', '操作'], rows));
    } catch (e) { body.appendChild(empty(e.message || '用户加载失败')); }
    finally { loading.classList.add('hidden'); }
  }
  async function patchUser(id, patch) {
    try {
      await api('/api/admin/users/' + id, { method: 'PATCH', body: patch });
      toast('用户已更新', 'success'); await loadUsers(); await refreshOverview();
    } catch (e) { toast(e.message || '用户更新失败', 'error'); }
  }

  function setUserRole(user) {
    return formModal({
      title: '设置 ' + user.username + ' 的角色', submitText: '保存角色',
      fields: [{ name: 'role', label: '角色', type: 'select', value: user.role, options: [
        { value: 'user', label: '普通用户（user）' },
        { value: 'kb_admin', label: '知识库管理员（kb_admin）' },
        { value: 'root', label: '系统管理员（root）' }
      ]}],
      onSubmit: async function (values) { await patchUser(user.id, { role: values.role }); }
    });
  }
  function parseIdList(raw, label) {
    var parts = String(raw || '').split(',').map(function (x) { return x.trim(); }).filter(Boolean);
    if (!parts.length || parts.some(function (x) { return !/^\d+$/.test(x); })) {
      throw new Error(label + '必须是逗号分隔的数字 ID');
    }
    return Array.from(new Set(parts.map(function (x) { return Number(x); })));
  }

  async function setUserDepartments(user) {
    try {
      var data = await api('/api/admin/departments');
      var choices = data.items.map(function (d) { return d.id + ' · ' + d.name; }).join('\n');
      var promptText = '可选部门：\n' + (choices || '暂无部门') + '\n\n输入部门 ID，多个用逗号分隔。当前：' + (user.department_ids || '无');
      var value = window.prompt(promptText, user.department_ids || '');
      if (value === null) { return; }
      await patchUser(user.id, { department_ids: parseIdList(value, '部门 ID') });
    } catch (e) { toast(e.message || '部门更新失败', 'warn'); }
  }

  async function setDocumentKnowledgeBases(doc) {
    try {
      var data = await api('/api/admin/knowledge-bases');
      var choices = data.items.map(function (k) { return k.id + ' · ' + k.name + '（' + k.department_name + '）'; }).join('\n');
      var promptText = '可选知识库：\n' + (choices || '暂无知识库') + '\n\n输入知识库 ID，多个用逗号分隔。当前：' + (doc.knowledge_base_ids || '无');
      var value = window.prompt(promptText, doc.knowledge_base_ids || '');
      if (value === null) { return; }
      await api('/api/admin/documents/' + doc.id + '/knowledge-bases', {
        method: 'PATCH', body: { knowledge_base_ids: parseIdList(value, '知识库 ID') }
      });
      toast('文档权限已更新', 'success');
      await loadDocs();
    } catch (e) { toast(e.message, 'warn'); }
  }

  async function loadScope() {
    try {
      var deps = await api('/api/admin/departments');
      var kbs = await api('/api/admin/knowledge-bases');
      var depRows = deps.items.map(function (d) {
        return h('tr', {}, [cell(d.id), cell(d.name, 'cell-main'), cell(d.user_count), cell(d.knowledge_base_count)]);
      });
      var kbRows = kbs.items.map(function (k) {
        return h('tr', {}, [cell(k.id), cell(k.name, 'cell-main'), cell(k.department_name), cell(k.document_count)]);
      });
      clear(qs('#departmentsBody')).appendChild(table(['ID', '部门', '用户数', '知识库数'], depRows));
      clear(qs('#knowledgeBasesBody')).appendChild(table(['ID', '知识库', '所属部门', '文档数'], kbRows));
      var select = qs('#newKnowledgeBaseDepartment');
      clear(select);
      deps.items.forEach(function (d) { select.appendChild(h('option', { value: d.id }, [d.id + ' · ' + d.name])); });
      await loadUploadKnowledgeBases();
    } catch (e) {
      toast('权限列表加载失败：' + (e.message || '未知错误'), 'error');
    }
  }

  function resetPassword(user) {
    return formModal({
      title: '重置 ' + user.username + ' 的密码', submitText: '重置密码',
      fields: [{ name: 'password', label: '新密码（至少 6 位）', type: 'password', required: true, minlength: 6, autocomplete: 'new-password' }],
      onSubmit: async function (values) {
        await api('/api/admin/users/' + user.id, { method: 'PATCH', body: { password: values.password } });
        toast('密码已重置', 'success');
      }
    });
  }

  async function loadAudit(kind) {
    kind = kind || 'chats';
    var isChats = kind === 'chats';
    var isFeedback = kind === 'feedback';
    var loading = qs(isChats ? '#chatsLoading' : (isFeedback ? '#feedbackLoading' : '#logsLoading'));
    var body = qs(isChats ? '#chatsBody' : (isFeedback ? '#feedbackBody' : '#logsBody'));
    loading.classList.remove('hidden'); clear(body);
    try {
      var endpoint = isChats ? '/api/admin/chats?limit=100' : (isFeedback ? '/api/admin/feedback?limit=100' : '/api/admin/audit?limit=100');
      var data = await api(endpoint);
      qs(isChats ? '#chatsHint' : (isFeedback ? '#feedbackHint' : '#logsHint')).textContent = '共 ' + data.total + ' 条，显示最近 ' + data.items.length + ' 条';
      if (!data.items.length) { body.appendChild(empty(isChats ? '暂无问答记录' : (isFeedback ? '暂无用户反馈' : '暂无审计日志'))); return; }
      var rows = data.items.map(function (x) {
        if (isChats) {
          return h('tr', {}, [cell(x.username), cell(excerpt(x.question, 80), 'cell-long'), cell(excerpt(x.answer || x.error, 100), 'cell-long'),
            h('td', {}, [badge(x.status === 'ok' ? '成功' : '失败', x.status === 'ok' ? 'b-ok' : 'b-err')]), cell(fmtMs(x.latency_ms)), cell(fmtTime(x.created_at))]);
        }
        if (isFeedback) {
          return h('tr', {}, [cell(x.username), cell(x.rating === 'helpful' ? '有帮助' : '没帮助'),
            cell(excerpt(x.question, 90), 'cell-long'), cell(excerpt(x.comment || '—', 90), 'cell-long'), cell(fmtTime(x.created_at))]);
        }
        return h('tr', {}, [cell(x.username || '系统'), cell(x.action), cell(excerpt(x.detail, 120), 'cell-long'), cell(x.ip), cell(fmtTime(x.created_at))]);
      });
      body.appendChild(table(isChats ? ['用户', '问题', '回答/错误', '状态', '耗时', '时间'] : (isFeedback ? ['用户', '评价', '问题', '备注', '时间'] : ['用户', '动作', '详情', 'IP', '时间']), rows));
    } catch (e) { body.appendChild(empty(e.message || '记录加载失败')); }
    finally { loading.classList.add('hidden'); }
  }

  function bindTabs() {
    qsa('[data-tab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        qsa('[data-tab]').forEach(function (x) { x.classList.toggle('is-active', x === btn); });
        qsa('[data-panel]').forEach(function (x) { x.classList.toggle('is-active', x.dataset.panel === btn.dataset.tab); });
        if (btn.dataset.tab === 'overview') { loadOverview(); }
        if (btn.dataset.tab === 'docs') { loadDocs(); }
        if (btn.dataset.tab === 'users') { loadUsers(); }
        if (btn.dataset.tab === 'audit') { loadAudit('chats'); }
        if (btn.dataset.tab === 'scope') { loadScope(); }
      });
    });
    qsa('[data-subtab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        qsa('[data-subtab]').forEach(function (x) { x.classList.toggle('is-active', x === btn); });
        qsa('.subtab-panel').forEach(function (x) { x.classList.toggle('is-active', x.id === 'subpanel-' + btn.dataset.subtab); });
        loadAudit(btn.dataset.subtab);
      });
    });
  }
  function bindForms() {
    qs('#settingsForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#saveSettingsBtn'); busy(btn, true, '保存中…');
      try {
        var body = {
          top_k: Number(qs('#cfg-top_k').value),
          queries_per_minute: Number(qs('#cfg-queries_per_minute').value),
          max_concurrent_llm: Number(qs('#cfg-max_concurrent_llm').value)
        };
        await api('/api/admin/settings', { method: 'PATCH', body: body });
        toast('运行参数已保存', 'success'); qs('#settingsHint').textContent = '已保存';
      } catch (e) { toast(e.message || '保存失败', 'error'); }
      finally { busy(btn, false); }
    });
    qs('#createUserForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#createUserBtn'); busy(btn, true, '创建中…');
      try {
        await api('/api/admin/users', { method: 'POST', body: { username: qs('#nu-username').value, password: qs('#nu-password').value, role: qs('#nu-role').value } });
        ev.target.reset(); toast('用户已创建', 'success'); await loadUsers(); await refreshOverview();
      } catch (e) { toast(e.message || '创建失败', 'error'); }
      finally { busy(btn, false); }
    });
    qs('#createDepartmentForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#createDepartmentBtn'); busy(btn, true, '创建中…');
      try {
        await api('/api/admin/departments', { method: 'POST', body: { name: qs('#newDepartmentName').value } });
        ev.target.reset(); toast('部门已创建', 'success'); await loadScope();
      } catch (e) { toast(e.message || '部门创建失败', 'error'); }
      finally { busy(btn, false); }
    });
    qs('#createKnowledgeBaseForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#createKnowledgeBaseBtn'); busy(btn, true, '创建中…');
      try {
        await api('/api/admin/knowledge-bases', { method: 'POST', body: {
          name: qs('#newKnowledgeBaseName').value,
          department_id: Number(qs('#newKnowledgeBaseDepartment').value)
        }});
        ev.target.reset(); toast('知识库已创建', 'success'); await loadScope();
      } catch (e) { toast(e.message || '知识库创建失败', 'error'); }
      finally { busy(btn, false); }
    });
    var zone = qs('#uploadZone'); var input = qs('#fileInput');
    zone.addEventListener('click', function () { input.click(); });
    zone.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    input.addEventListener('change', function () { if (input.files.length) { uploadFiles(input.files); } });
    ['dragenter', 'dragover'].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.remove('dragover'); });
    });
    zone.addEventListener('drop', function (e) { if (e.dataTransfer.files.length) { uploadFiles(e.dataTransfer.files); } });
    qs('#refreshDocsBtn').addEventListener('click', loadDocs);
    ['docFilterVersion', 'docFilterTag', 'docFilterFrom', 'docFilterTo'].forEach(function (id) {
      qs('#' + id).addEventListener('keydown', function (e) { if (e.key === 'Enter') { loadDocs(); } });
    });
    qs('#refreshUsersBtn').addEventListener('click', loadUsers);
    qs('#refreshChatsBtn').addEventListener('click', function () { loadAudit('chats'); });
    qs('#refreshFeedbackBtn').addEventListener('click', function () { loadAudit('feedback'); });
    qs('#refreshLogsBtn').addEventListener('click', function () { loadAudit('logs'); });
  }

  async function boot() {
    wireGlobalErrors();
    var me = await initSession(); if (!me) { return; }
    if (me.role !== 'root' && me.role !== 'kb_admin') { location.href = '/app'; return; }
    currentUser = me;
    if (me.role !== 'root') {
      qsa('.root-only').forEach(function (el) { el.hidden = true; el.classList.remove('is-active'); });
      qs('#tabBtn-docs').classList.add('is-active');
      qs('#panel-docs').classList.add('is-active');
    }
    bindTabs(); bindForms(); registerSW(); await loadUploadKnowledgeBases();
    if (me.role === 'root') { await loadOverview(); } else { await loadDocs(); }
  }
  document.addEventListener('DOMContentLoaded', function () {
    boot().catch(function (e) { toast(e.message || '管理页启动失败', 'error'); });
  });
}());
