/* 用户与权限：独立账号和用户组管理，敏感操作沿用服务端 root 权限。 */
'use strict';
(function () {
  function cell(value, cls) { return h('td', {class: cls || ''}, [value == null ? '—' : String(value)]); }
  function badge(text, cls) { return h('span', {class: 'badge ' + cls}, [h('span', {class: 'dot'}), text]); }
  function empty(text) { return h('div', {class: 'empty'}, [text]); }
  function table(headers, rows) {
    return h('div', {class: 'table-wrap'}, [h('table', {class: 'tbl'}, [
      h('thead', {}, [h('tr', {}, headers.map(function (label) { return h('th', {}, [label]); }))]), h('tbody', {}, rows)
    ])]);
  }
  function actionButton(text, kind, handler) {
    var button = h('button', {type: 'button', class: 'btn btn-sm ' + kind}, [text]);
    button.addEventListener('click', function () { handler(button); }); return button;
  }
  function loadError(text, retry) {
    return h('div', {class: 'load-error'}, [h('span', {class: 'err-text'}, [text]), actionButton('重试', 'btn-outline', retry)]);
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
      toast('用户已更新', 'success'); await loadUsers();
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

  function bindForms() {
    if (qs('#createUserForm')) qs('#createUserForm').addEventListener('submit', async function (ev) {
      ev.preventDefault(); var btn = qs('#createUserBtn'); busy(btn, true, '创建中…');
      try {
        await api('/api/admin/users', { method: 'POST', body: { username: qs('#nu-username').value, password: qs('#nu-password').value, role: qs('#nu-role').value } });
        ev.target.reset(); toast('用户已创建', 'success'); await loadUsers();
      } catch (e) { toast(e.message || '创建失败', 'error'); }
      finally { busy(btn, false); }
    });
    if (qs('#createGroupForm')) qs('#createGroupForm').addEventListener('submit', async function (ev) {
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
    qs('#refreshUsersBtn').addEventListener('click', loadUsers);
  }
  async function boot() {
    wireGlobalErrors();
    var me = await initSession(); if (!me) { return; }
    if (me.role !== 'root') { location.href = '/app'; return; }
    qs('#usersWorkspace').hidden = false;
    qsa('[data-tab]').forEach(function (button) {
      button.addEventListener('click', function () {
        qsa('[data-tab]').forEach(function (tab) {
          tab.classList.toggle('is-active', tab === button); tab.setAttribute('aria-selected', String(tab === button));
        });
        qsa('[data-panel]').forEach(function (panel) { panel.classList.toggle('is-active', panel.dataset.panel === button.dataset.tab); });
        if (button.dataset.tab === 'groups') { loadGroups(); } else { loadUsers(); }
      });
    });
    bindForms(); registerSW(); await loadUsers();
  }
  document.addEventListener('DOMContentLoaded', function () {
    boot().catch(function (error) { toast(error.message || '用户与权限加载失败', 'error'); });
  });
}());
