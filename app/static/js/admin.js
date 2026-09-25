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
  /* 加载失败与「暂无数据」是两件事：前者要能看出是出错，并给出重试入口。 */
  function loadError(text, retry) {
    var box = h('div', { class: 'load-error' }, [
      h('span', { class: 'err-ic', html: icon('alert') }),
      h('span', { class: 'err-text' }, [text || '加载失败'])
    ]);
    if (retry) { box.appendChild(actionButton('重试', 'btn-outline', retry)); }
    return box;
  }
  function table(headers, rows) {
    var head = h('thead', {}, [h('tr', {}, headers.map(function (x) { return h('th', {}, [x]); }))]);
    return h('div', { class: 'table-wrap' }, [h('table', { class: 'tbl' }, [head, h('tbody', {}, rows)])]);
  }
  function actionButton(text, kind, handler) {
    var btn = h('button', { class: 'btn btn-sm ' + (kind || 'btn-outline'), type: 'button' }, [text]);
    // 把按钮自身传给处理函数：长耗时操作需要给自己加忙碌态，防止重复提交。
    btn.addEventListener('click', function () { handler(btn); });
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
        ['用户', c.users, 'user'], ['文档', c.documents, 'doc'],
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
          kv('嵌入设备', m.embed_device || '—'),
          kv('生成模型', m.llm_model),
          kv('切片上限', m.chunk_max_tokens + ' token'), kv('切片重叠', m.chunk_overlap_tokens + ' token'),
          kv('拒答阈值', m.min_relevance_score),
          kv('对外地址', m.public_origin || '未配置')
        ])
      ]));
      Object.keys(data.settings).forEach(function (key) {
        var input = qs('#cfg-' + key); if (input) { input.value = data.settings[key]; }
      });
      renderLatency(data.latency);
      qs('#maxUploadMB').textContent = m.max_upload_mb;
      qs('#uploadMeta').textContent = '最大 ' + m.max_upload_mb + ' MB / 文件';
      content.classList.remove('hidden');
    } catch (e) {
      error.textContent = e.message || '概览加载失败'; error.classList.remove('hidden');
    } finally { loading.classList.add('hidden'); }
  }

  // 分阶段耗时：明确样本量和时间范围，避免把历史统计当成当前请求耗时。
  function msLabel(v) {
    if (v === null || v === undefined) { return '—'; }
    return v >= 1000 ? (v / 1000).toFixed(1) + ' 秒' : v + ' 毫秒';
  }

  function renderLatency(lat) {
    var host = qs('#latencyCard');
    if (!host) { return; }
    clear(host);
    if (!lat) { return; }
    var model = lat.model_ms;
    var retr = lat.retrieval_ms;
    var verdict = '样本不足，请结合具体问答记录判断';
    if (model && retr && model.samples >= 5 && retr.samples >= 5) {
      if (retr.p50 >= 1000) {
        verdict = '检索中位耗时已到秒级，请检查嵌入设备与索引规模';
      } else if (model.p50 >= 1000 && model.p50 >= retr.p50 * 10) {
        verdict = '模型调用明显慢于检索，请检查生成模型与硬件';
      } else {
        verdict = '两段中位耗时较低；若仍感觉慢，请检查并发排队与网络';
      }
    }
    host.appendChild(h('div', { class: 'kv-grid' }, [
      kv('模型样本 / 窗口', lat.sample_size + ' / ' + lat.window),
      kv('统计截至', lat.span ? fmtTime(lat.span.to) : '—'),
      kv('拒答 / 失败', lat.refusals + ' / ' + lat.errors),
      kv('模型调用 中位', msLabel(model ? model.p50 : null)),
      kv('模型调用 P95', msLabel(model ? model.p95 : null)),
      kv('模型调用 最长', msLabel(model ? model.max : null)),
      kv('检索样本', retr ? retr.samples : 0),
      kv('检索 中位', msLabel(retr ? retr.p50 : null)),
      kv('检索 P95', msLabel(retr ? retr.p95 : null)),
      kv('输出 token 中位', lat.completion_tokens && lat.completion_tokens.p50 !== null ? lat.completion_tokens.p50 : '—')
    ]));
    host.appendChild(h('p', { class: 'small muted', style: 'margin-top:10px' }, [verdict]));
    host.appendChild(h('p', { class: 'small muted', style: 'margin-top:6px' }, [lat.note || '']));
  }

  async function loadDocs() {
    var loading = qs('#docsLoading'); var body = qs('#docsBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var params = new URLSearchParams();
      var filterVersion = qs('#docFilterVersion').value.trim();
      if (filterVersion) { params.set('version', filterVersion); }
      var suffix = params.toString() ? '?' + params.toString() : '';
      var data = await api('/api/admin/documents' + suffix);
      if (!data.items.length) { body.appendChild(empty('尚未上传文档')); return; }
      var rows = data.items.map(function (d) {
        var actions = h('td', { class: 'cell-actions' });
        actions.appendChild(actionButton('重建索引', 'btn-outline', function (btn) { reindexDoc(d.id, d.filename, btn); }));
        actions.appendChild(actionButton('删除', 'btn-danger', function (btn) { removeDoc(d.id, d.filename, btn); }));
        // 可见范围属于访问控制，只由 root 决定（kb_admin 管内容、不管权限）。
        if (currentUser && currentUser.role === 'root') {
          actions.appendChild(actionButton('设置访问', 'btn-outline', function (btn) { setDocumentAccess(d, btn); }));
        }
        var state = d.status === 'ready' ? badge('就绪', 'b-ok') : d.status === 'failed' ? badge('失败', 'b-err') : badge('处理中', 'b-warn');
        var restricted = d.visibility === 'restricted';
        var grantSummary = (d.granted_user_count || 0) + ' 个账号 / ' +
          (d.granted_group_count || 0) + ' 个组';
        var scope = h('td', {}, [
          h('span', { class: restricted ? 'doc-scope-restricted' : '' },
            [restricted ? ('仅授权 ' + grantSummary) : '所有人'])
        ]);
        var name = h('td', { class: 'cell-main', title: d.filename }, [d.filename]);
        if (d.error) { name.appendChild(h('div', { class: 'doc-err', title: d.error }, [d.error])); }
        return h('tr', {}, [name, cell(d.version || '1.0'), cell(fmtBytes(d.size_bytes)), h('td', {}, [state]), scope, cell(d.uploaded_by_name), cell(fmtTime(d.created_at)), actions]);
      });
      body.appendChild(table(['文件', '版本', '大小', '状态', '可见范围', '上传者', '上传日期', '操作'], rows));
    } catch (e) { body.appendChild(loadError(e.message || '文档加载失败', loadDocs)); }
    finally { loading.classList.add('hidden'); }
  }

  /* 设置单份文档的可见范围与授权名单（仅 root） */
  async function setDocumentAccess(doc, btn) {
    busy(btn, true, '读取中…');
    var detail;
    try {
      detail = await api('/api/admin/documents/' + doc.id + '/access');
    } catch (e) {
      toast(e.message || '读取可见范围失败', 'error');
      return;
    } finally { busy(btn, false); }

    return formModal({
      title: '设置《' + doc.filename + '》的可见范围',
      submitText: '保存',
      fields: [
        {
          name: 'visibility', label: '可见范围', type: 'select', value: detail.visibility,
          options: [
            { value: 'shared', label: '所有人可见（默认）' },
            { value: 'restricted', label: '仅下列账号或用户组可见' }
          ]
        },
        {
          name: 'user_ids', type: 'checkboxes', numeric: true,
          label: '可见账号（选择受限范围时生效）',
          value: detail.granted_user_ids,
          emptyText: '暂无可授权的账号，请先在“用户管理”里创建',
          options: (detail.candidates || []).map(function (u) {
            return {
              value: u.id,
              label: u.username + '（' + roleLabel(u.role) + '）' + (u.is_active ? '' : ' · 已停用')
            };
          })
        },
        {
          name: 'group_ids', type: 'checkboxes', numeric: true,
          label: '可见用户组（组内成员都会获得访问权）',
          value: detail.granted_group_ids,
          emptyText: '暂无用户组，请先在“用户组”里创建',
          options: (detail.group_candidates || []).map(function (g) {
            return {
              value: g.id,
              label: g.name + '（' + (g.member_ids || []).length + ' 名成员）'
            };
          })
        }
      ],
      onSubmit: async function (v) {
        await api('/api/admin/documents/' + doc.id + '/access', {
          method: 'PUT',
          body: { visibility: v.visibility, user_ids: v.user_ids, group_ids: v.group_ids }
        });
        toast('可见范围已更新', 'success');
        await loadDocs();
      }
    });
  }

  async function uploadFiles(files) {
    var list = Array.from(files);
    var chips = qs('#fileChips'); clear(chips);
    var failed = 0;
    // 不再用 setInterval 轮询文档列表：上传本身就是串行等待的，轮询既浪费请求，
    // 又会在结束前一直跑；改为全部结束后刷新一次。
    var jobs = list.map(async function (file) {
      var chip = h('span', { class: 'chip' }, [h('span', { class: 'spin' }), h('span', { class: 'chip-name', title: file.name }, [file.name])]);
      chips.appendChild(chip);
      try {
        var form = new FormData(); form.append('file', file, file.name);
        form.append('version', qs('#docVersion').value.trim() || '1.0');
        await api('/api/admin/documents', { method: 'POST', body: form });
        chip.className = 'chip chip-ok'; chip.firstChild.innerHTML = icon('check');
      } catch (e) {
        // 失败原因不能只写进 title：触屏与键盘用户看不到 tooltip。
        var reason = e.message || '上传失败';
        failed += 1;
        chip.className = 'chip chip-err chip-block';
        chip.firstChild.innerHTML = icon('alert');
        chip.title = reason;
        chip.appendChild(h('span', { class: 'chip-reason' }, ['上传失败：' + reason]));
      }
    });
    try { await Promise.all(jobs); }
    finally {
      qs('#fileInput').value = '';
      await loadDocs(); await refreshOverview();
    }
    if (failed) { toast(failed + ' 个文件上传失败，原因见文件名下方', 'error'); }
    else if (list.length) { toast('上传完成', 'success'); }
  }
  async function reindexDoc(id, name, btn) {
    if (!window.confirm('重建《' + name + '》的检索索引？\n\n系统会使用已上传的原文件重新解析、分段并生成检索向量，不会重复上传文件。')) { return; }
    // 重建可能耗时数分钟；不加忙碌态就可以被重复点击，触发多次重建。
    busy(btn, true, '重建中…');
    try {
      await api('/api/admin/documents/' + id + '/reindex', { method: 'POST', body: {} });
      toast('索引重建完成', 'success'); await loadDocs(); await refreshOverview();
    } catch (e) { toast(e.message || '索引重建失败', 'error'); }
    finally { busy(btn, false); }
  }
  async function removeDoc(id, name, btn) {
    if (!window.confirm('确认删除《' + name + '》？此操作不可撤销。')) { return; }
    busy(btn, true, '删除中…');
    try {
      await api('/api/admin/documents/' + id, { method: 'DELETE' });
      toast('文档已删除', 'success'); await loadDocs(); await refreshOverview();
    } catch (e) { toast(e.message || '删除文档失败', 'error'); }
    finally { busy(btn, false); }
  }

  async function loadUsers() {
    var loading = qs('#usersLoading'); var body = qs('#usersBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var data = await api('/api/admin/users');
      if (!data.items.length) { body.appendChild(empty('暂无用户')); return; }
      var rows = data.items.map(function (u) {
        var actions = h('td', { class: 'cell-actions' });
        actions.appendChild(actionButton('重置密码', 'btn-outline', function () { resetPassword(u); }));
        actions.appendChild(actionButton(u.is_active ? '停用' : '启用', u.is_active ? 'btn-danger' : 'btn-outline', function () { patchUser(u.id, { is_active: !u.is_active }); }));
        actions.appendChild(actionButton('设置角色', 'btn-outline', function () { setUserRole(u); }));
        return h('tr', {}, [
          cell(u.username, 'cell-main'), h('td', {}, [h('span', { class: 'tag-role tag-' + u.role }, [roleLabel(u.role)])]),
          h('td', {}, [badge(u.is_active ? '启用' : '停用', u.is_active ? 'b-ok' : 'b-muted')]),
          cell(fmtTime(u.last_login_at)), cell(fmtTime(u.created_at)), actions
        ]);
      });
      body.appendChild(table(['用户名', '角色', '状态', '最近登录', '创建时间', '操作'], rows));
    } catch (e) { body.appendChild(loadError(e.message || '用户加载失败', loadUsers)); }
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
        { value: 'kb_admin', label: '文档管理员' },
        { value: 'root', label: '系统管理员（root）' }
      ]}],
      onSubmit: async function (values) { await patchUser(user.id, { role: values.role }); }
    });
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

  async function deleteConversation(conversation, btn) {
    if (!window.confirm('确定删除 ' + conversation.username + ' 的对话“' + conversation.title + '”？其中全部问答将被删除且无法恢复。')) { return; }
    busy(btn, true, '删除中…');
    try {
      await api('/api/conversations/' + conversation.id, { method: 'DELETE' });
      toast('对话已删除', 'success');
      await loadAudit('chats');
      await refreshOverview();
    } catch (e) { toast(e.message || '删除对话失败', 'error'); }
    finally { busy(btn, false); }
  }

  async function loadAudit(kind) {
    kind = kind || 'chats';
    var isChats = kind === 'chats';
    var isFeedback = kind === 'feedback';
    var loading = qs(isChats ? '#chatsLoading' : (isFeedback ? '#feedbackLoading' : '#logsLoading'));
    var body = qs(isChats ? '#chatsBody' : (isFeedback ? '#feedbackBody' : '#logsBody'));
    loading.classList.remove('hidden'); clear(body);
    try {
      var endpoint = isChats ? '/api/admin/conversations?limit=100' : (isFeedback ? '/api/admin/feedback?limit=100' : '/api/admin/audit?limit=100');
      var data = await api(endpoint);
      qs(isChats ? '#chatsHint' : (isFeedback ? '#feedbackHint' : '#logsHint')).textContent = '共 ' + data.total + ' 条，显示最近 ' + data.items.length + ' 条';
      if (!data.items.length) { body.appendChild(empty(isChats ? '暂无对话记录' : (isFeedback ? '暂无用户反馈' : '暂无审计日志'))); return; }
      var rows = data.items.map(function (x) {
        if (isChats) {
          return h('tr', {}, [cell(x.username), cell(x.title, 'cell-long'), cell(x.turn_count), cell(fmtTime(x.created_at)), cell(fmtTime(x.updated_at)),
            h('td', { class: 'cell-actions' }, [actionButton('删除', 'btn-danger', function (btn) { deleteConversation(x, btn); })])]);
        }
        if (isFeedback) {
          return h('tr', {}, [cell(x.username), cell(x.rating === 'helpful' ? '有帮助' : '没帮助'),
            cell(excerpt(x.question, 90), 'cell-long'), cell(excerpt(x.comment || '—', 90), 'cell-long'), cell(fmtTime(x.created_at))]);
        }
        var detail = (x.detail || '').replace(/\s*切片数:\d+/g, '');
        return h('tr', {}, [cell(x.username || '系统'), cell(x.action), cell(excerpt(detail, 120), 'cell-long'), cell(x.ip), cell(fmtTime(x.created_at))]);
      });
      body.appendChild(table(isChats ? ['用户', '对话标题', '问答轮数', '创建时间', '最后更新', '操作'] : (isFeedback ? ['用户', '评价', '问题', '备注', '时间'] : ['用户', '动作', '详情', 'IP', '时间']), rows));
    } catch (e) { body.appendChild(loadError(e.message || '记录加载失败', function () { loadAudit(kind); })); }
    finally { loading.classList.add('hidden'); }
  }

  /* ---------------- 用户组 ---------------- */
  //
  // 组只影响"哪些文档对谁可见"，不参与检索范围或任何自动归类。
  // 这是新建的概念，不是恢复 v2 取消掉的部门/知识库划分。

  var groupState = { items: [], candidates: [] };

  async function loadGroups() {
    var loading = qs('#groupsLoading'); var body = qs('#groupsBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var data = await api('/api/admin/groups');
      groupState.items = data.items || [];
      groupState.candidates = data.candidates || [];
      if (!groupState.items.length) { body.appendChild(empty('尚未创建用户组')); return; }
      var byId = {};
      groupState.candidates.forEach(function (u) { byId[u.id] = u.username; });
      var rows = groupState.items.map(function (g) {
        var names = (g.member_ids || []).map(function (id) { return byId[id] || ('#' + id); });
        var actions = h('td', { class: 'cell-actions' });
        actions.appendChild(actionButton('管理成员', 'btn-outline', function (btn) { editGroupMembers(g, btn); }));
        actions.appendChild(actionButton('修改', 'btn-outline', function (btn) { editGroup(g, btn); }));
        actions.appendChild(actionButton('删除', 'btn-danger', function (btn) { deleteGroup(g, btn); }));
        return h('tr', {}, [
          cell(g.name, 'cell-main'),
          cell(g.description || '—', 'cell-long'),
          cell(names.length ? names.join('、') : '（无成员）', 'cell-long'),
          cell(g.granted_document_count),
          cell(fmtTime(g.updated_at)),
          actions
        ]);
      });
      body.appendChild(table(['组名', '说明', '成员', '已授权文档', '更新时间', '操作'], rows));
    } catch (e) { body.appendChild(loadError(e.message || '用户组加载失败', loadGroups)); }
    finally { loading.classList.add('hidden'); }
  }

  function editGroup(g) {
    return formModal({
      title: '修改用户组',
      submitText: '保存',
      fields: [
        { name: 'name', label: '组名（1–32 位）', type: 'text', value: g.name, required: true, maxlength: 32 },
        { name: 'description', label: '说明（可选）', type: 'text', value: g.description || '', maxlength: 200 }
      ],
      onSubmit: async function (v) {
        await api('/api/admin/groups/' + g.id, {
          method: 'PATCH', body: { name: v.name, description: v.description || '' }
        });
        toast('用户组已更新', 'success'); await loadGroups();
      }
    });
  }

  function editGroupMembers(g) {
    return formModal({
      title: '管理「' + g.name + '」的成员',
      submitText: '保存成员',
      fields: [{
        name: 'user_ids', type: 'checkboxes', numeric: true,
        label: '组内成员（本组当前被 ' + g.granted_document_count + ' 份文档授权）',
        value: g.member_ids,
        emptyText: '暂无可加入的账号，请先在“用户”里创建',
        options: groupState.candidates.map(function (u) {
          return {
            value: u.id,
            label: u.username + '（' + roleLabel(u.role) + '）' + (u.is_active ? '' : ' · 已停用')
          };
        })
      }],
      onSubmit: async function (v) {
        await api('/api/admin/groups/' + g.id + '/members', {
          method: 'PUT', body: { user_ids: v.user_ids }
        });
        toast('成员已更新', 'success'); await loadGroups();
      }
    });
  }

  async function deleteGroup(g, btn) {
    var msg = '确认删除用户组「' + g.name + '」？';
    if (g.granted_document_count > 0) {
      msg += '\n\n该组当前被 ' + g.granted_document_count + ' 份文档授权。'
           + '删除后这些授权一并失效，组内成员会立即失去对相应文档的访问权。';
    }
    if (!window.confirm(msg)) { return; }
    busy(btn, true, '删除中…');
    try {
      await api('/api/admin/groups/' + g.id, { method: 'DELETE' });
      toast('用户组已删除', 'success'); await loadGroups();
    } catch (e) { toast(e.message || '删除用户组失败', 'error'); }
    finally { busy(btn, false); }
  }

  function bindTabs() {
    qsa('[data-tab]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        qsa('[data-tab]').forEach(function (x) { x.classList.toggle('is-active', x === btn); });
        qsa('[data-panel]').forEach(function (x) { x.classList.toggle('is-active', x.dataset.panel === btn.dataset.tab); });
        if (btn.dataset.tab === 'overview') { loadOverview(); }
        if (btn.dataset.tab === 'docs') { loadDocs(); }
        if (btn.dataset.tab === 'users') { loadUsers(); }
        if (btn.dataset.tab === 'groups') { loadGroups(); }
        if (btn.dataset.tab === 'audit') { loadAudit('chats'); }
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
    qs('#createGroupForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#createGroupBtn'); busy(btn, true, '创建中…');
      try {
        await api('/api/admin/groups', {
          method: 'POST',
          body: { name: qs('#ng-name').value.trim(), description: qs('#ng-description').value.trim() }
        });
        toast('用户组已创建', 'success');
        qs('#ng-name').value = ''; qs('#ng-description').value = '';
        await loadGroups();
      } catch (e) { toast(e.message || '创建用户组失败', 'error'); }
      finally { busy(btn, false); }
    });
    qs('#docFilterVersion').addEventListener('keydown', function (e) { if (e.key === 'Enter') { loadDocs(); } });
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
    bindTabs(); bindForms(); registerSW();
    qs('#docToday').textContent = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
    if (me.role === 'root') { await loadOverview(); } else { await loadDocs(); }
  }
  document.addEventListener('DOMContentLoaded', function () {
    boot().catch(function (e) { toast(e.message || '管理页启动失败', 'error'); });
  });
}());
