/* ============================================================
 * app.js — 问答页逻辑
 *  - 会话校验（未登录跳 /login）
 *  - 左侧对话列表 + 右侧多轮问答（Enter 发送 / Ctrl+Enter 换行）
 *  - 发送后拉取完整对话，逐轮渲染「答案 + 引用来源卡」
 *  - 顶层 try/catch + toast，避免未捕获异常白屏
 * ============================================================ */
'use strict';

// 引用编号对应模型收到的片段顺序；未标注的检索候选不作为回答来源。
function citedSources(answer, sources) {
  var ids = new Set();
  String(answer || '').replace(/\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g, function (_, numbers) {
    numbers.split(/[,，、]/).forEach(function (n) { ids.add(Number(n)); });
    return _;
  });
  var groups = new Map();
  (sources || []).forEach(function (s, index) {
    var number = index + 1;
    if (!ids.has(number)) { return; }
    // 没有页码的片段可能跨段落；保留其摘录，不冒充精确的单段位置。
    var key = s.document_id + ':' + (s.page != null ? 'page:' + s.page : 'source:' + number);
    if (!groups.has(key)) { groups.set(key, { source: s, numbers: [], excerpts: [] }); }
    var group = groups.get(key);
    group.numbers.push(number);
    if (s.excerpt && group.excerpts.indexOf(s.excerpt) < 0) { group.excerpts.push(s.excerpt); }
  });
  return Array.from(groups.values());
}

// 仅依据文件名的明确设备标识；多设备合订文档留给用户手动选择。
function deviceDocumentIds(documents, device) {
  var patterns = {
    PLM: /(^|[^a-z0-9])plm(?:v?\d+(?:\.\d+)+)?(?=$|[^a-z0-9])/i,
    PLUSPRO: /(^|[^a-z0-9])plus[\s_-]*pro(?:v?\d+(?:\.\d+)+)?(?=$|[^a-z0-9])/i,
    PLUS500: /(^|[^a-z0-9])plus[\s_-]*500(?:v?\d+(?:\.\d+)+)?(?=$|[^a-z0-9])/i,
    LP: /(^|[^a-z0-9])lp(?:v?\d+(?:\.\d+)+)?(?=$|[^a-z0-9])/i
  };
  if (!patterns[device]) { return []; }
  return (documents || []).filter(function (doc) {
    var matches = Object.keys(patterns).filter(function (name) {
      return patterns[name].test(doc.filename || '');
    });
    return matches.length === 1 && matches[0] === device;
  }).map(function (doc) { return doc.id; });
}

(function () {
  var S = {
    me: null,
    busy: false,           // 是否正在生成回答
    currentId: null,       // 当前展开的对话 id
    typingEl: null,        // “正在生成”占位气泡
    composing: false,      // 中文输入法组合中
    documents: [],
    documentsLoaded: false,   // 最近一次文档列表是否成功加载
    documentsLoading: false,
    documentsError: '',       // 加载失败原因；失败时绝不当成"全库"
    scopeMode: 'all',         // 新对话范围模式：'all' 全库 / 'selected' 仅选中文档
    newDocumentIds: [],       // 新对话已勾选文档（scopeMode==='selected' 时生效）
    currentDocumentIds: [],   // 当前对话已固定的范围
    currentScopeMode: 'all',
    docFilter: '',            // 文件名搜索词（只影响列表显示，不改变勾选）
    scopeNotice: '',          // 发送前的范围校验提示
    abortController: null  // 生成中用于“停止生成”；非生成期为 null
  };

  var els = {};
  function cacheEls() {
    els.scroll = document.getElementById('chatScroll');
    els.messages = document.getElementById('messages');
    els.greeting = document.getElementById('chatGreeting');
    els.input = document.getElementById('questionInput');
    els.sendBtn = document.getElementById('sendBtn');
    els.stopBtn = document.getElementById('stopBtn');
    els.newChatBtn = document.getElementById('newChatBtn');
    els.deleteChatBtn = document.getElementById('deleteChatBtn');
    els.historyList = document.getElementById('historyList');
    els.historyEmpty = document.getElementById('historyEmpty');
    els.historyLoading = document.getElementById('historyLoading');
    els.historyFoot = document.getElementById('historyFoot');
    els.notice = document.getElementById('meNotice');
    els.questionCount = document.getElementById('questionCount');
    els.documentScope = document.getElementById('documentScope');
    els.documentScopeValue = document.getElementById('documentScopeValue');
    els.documentScopeHint = document.getElementById('documentScopeHint');
    els.documentScopeWarn = document.getElementById('documentScopeWarn');
    els.documentScopeBody = document.getElementById('documentScopeBody');
    els.documentScopeSearch = document.getElementById('documentScopeSearch');
    els.documentScopeSelectAll = document.getElementById('documentScopeSelectAll');
    els.documentScopeClear = document.getElementById('documentScopeClear');
    els.documentScopeSelected = document.getElementById('documentScopeSelected');
    els.documentScopeChips = document.getElementById('documentScopeChips');
    els.documentScopeOptions = document.getElementById('documentScopeOptions');
    els.documentScopeError = document.getElementById('documentScopeError');
    els.documentScopeLock = document.getElementById('documentScopeLock');
    els.documentScopeLockText = document.getElementById('documentScopeLockText');
    els.documentScopeNewChat = document.getElementById('documentScopeNewChat');
    els.documentScopeReload = document.getElementById('documentScopeReload');
    els.scopeModeAll = document.getElementById('scopeModeAll');
    els.scopeModeSelected = document.getElementById('scopeModeSelected');
    els.scopeModeAllLabel = document.getElementById('scopeModeAllLabel');
    els.scopeModeSelectedLabel = document.getElementById('scopeModeSelectedLabel');
    els.scopeIcon = document.getElementById('scopeIcon');
    els.deviceScopeHint = document.getElementById('deviceScopeHint');
  }

  function showNotice(text) {
    if (!els.notice) { return; }
    if (!text) { els.notice.classList.add('hidden'); return; }
    els.notice.innerHTML = '';
    els.notice.appendChild(h('span', { html: icon('alert') }));
    els.notice.appendChild(h('span', null, [text]));
    els.notice.classList.remove('hidden');
  }

  function scrollBottom() {
    if (els.scroll) { els.scroll.scrollTop = els.scroll.scrollHeight; }
  }

  /* ---------- 消息渲染 ---------- */

  function askBubble(text) {
    return h('div', { class: 'msg msg-q' }, [
      h('div', { class: 'bubble bubble-q' }, [text])
    ]);
  }

  function answerBubble(text) {
    return h('div', { class: 'msg msg-a' }, [
      h('div', { class: 'bubble bubble-a' }, [text || '（本次没有返回文本）'])
    ]);
  }

  function errorBubble(text) {
    return h('div', { class: 'msg msg-a' }, [
      h('div', { class: 'bubble bubble-error' }, [text || '出错了，请稍后重试'])
    ]);
  }

  function typingBubble() {
    return h('div', { class: 'msg msg-a' }, [
      h('div', { class: 'bubble bubble-a bubble-load' }, [
        h('span', { class: 'spin' }),
        '正在检索知识库并生成回答…'
      ])
    ]);
  }

  /** 单条来源位置文案 */
  function sourceLoc(s) {
    if (s.page !== null && s.page !== undefined && s.page !== '') { return '第 ' + s.page + ' 页'; }
    if (/\.xlsx$/i.test(s.filename || '')) { return '工作表与单元格位置见下方摘录'; }
    if (s.paragraph !== null && s.paragraph !== undefined && s.paragraph !== '') { return '片段起始：第 ' + s.paragraph + ' 段'; }
    return '未记录具体位置';
  }

  function sourceUrl(s) {
    var url = '/api/documents/' + encodeURIComponent(s.document_id) + '/file';
    if (s.page !== null && s.page !== undefined && s.page !== '') {
      url += '#page=' + encodeURIComponent(s.page);
    }
    return url;
  }

  function sourcesNode(answer, sources) {
    var items = citedSources(answer, sources);
    var wrap = h('details', { class: 'msg-a source-details' }, [
      h('summary', { class: 'sources-title' }, ['查看引用来源' + (items.length ? '（' + items.length + '）' : '')])
    ]);
    if (!items.length) {
      wrap.appendChild(h('p', { class: 'no-sources' }, ['本次回答未提供可对应的引用编号，无法确认具体来源。']));
      return wrap;
    }
    var cards = items.map(function (group) {
      var s = group.source;
      var meta = [h('span', { class: 's-loc' }, [sourceLoc(s)])];
      if (S.me && (S.me.role === 'root' || S.me.role === 'kb_admin')) {
        meta.push(h('a', { class: 's-open', href: sourceUrl(s), target: '_blank', rel: 'noopener noreferrer' }, ['打开原文']));
      }
      return h('div', { class: 'source-card' }, [
        h('div', { class: 'doc-ic', html: icon('doc') }),
        h('div', { class: 's-main' }, [
          h('div', { class: 's-top' }, [
            h('span', { class: 's-name', title: s.filename || '' }, [group.numbers.map(function (n) { return '[' + n + ']'; }).join('') + ' ' + (s.filename || '未知文档')])
          ]),
          h('div', { class: 's-meta' }, meta),
          h('div', { class: 's-excerpt' }, [group.excerpts.join('\n\n') || '（未保存原文摘录）'])
        ])
      ]);
    });
    var grid = h('div', { class: 'sources' }, cards);
    wrap.appendChild(grid);
    return wrap;
  }

  function feedbackNode(rec) {
    var current = rec.feedback && rec.feedback.rating;
    var wrap = h('div', { class: 'feedback-row' });
    wrap.appendChild(h('span', { class: 'feedback-label' }, ['这条回答有帮助吗？']));
    var buttons = {};
    ['helpful', 'unhelpful'].forEach(function (rating) {
      var label = rating === 'helpful' ? '有帮助' : '没帮助';
      var btn = h('button', {
        class: 'feedback-btn' + (current === rating ? ' is-selected' : ''),
        type: 'button',
        'aria-label': label
      }, [label]);
      btn.addEventListener('click', async function () {
        if (btn.disabled) { return; }
        Object.keys(buttons).forEach(function (key) { buttons[key].disabled = true; });
        try {
          await api('/api/chats/' + rec.id + '/feedback', {
            method: 'POST',
            body: { rating: rating }
          });
          current = rating;
          Object.keys(buttons).forEach(function (key) {
            buttons[key].classList.toggle('is-selected', key === current);
            buttons[key].disabled = false;
          });
          toast('反馈已保存', 'success');
        } catch (e) {
          Object.keys(buttons).forEach(function (key) { buttons[key].disabled = false; });
          toast('反馈保存失败：' + (e.message || '请稍后重试'), 'error');
        }
      });
      buttons[rating] = btn;
      wrap.appendChild(btn);
    });
    return wrap;
  }

  /**
   * 将一轮问答追加到消息区。
   */
  function appendRecord(rec) {
    if (!rec) { return; }
    els.messages.appendChild(askBubble(rec.question));
    if (rec.status === 'error') {
      var why = rec.error || '未知错误';
      els.messages.appendChild(errorBubble('回答失败：' + why + '（记录 #' + rec.id + '）'));
    } else {
      els.messages.appendChild(answerBubble(rec.answer));
      els.messages.appendChild(feedbackNode(rec));
      els.messages.appendChild(sourcesNode(rec.answer, rec.sources));
    }
  }

  function renderConversation(conversation) {
    if (!conversation) { return; }
    // 优先使用服务端回显的真实模式：当原限定文档已不可见时，详情会返回
    // scope_mode="selected" + document_ids=[]，不能按空列表推断成"全库"。
    setDocumentScope(conversation.document_ids || [], true, conversation.scope_mode);
    hideGreeting();
    clear(els.messages);
    els.messages.classList.remove('hidden');
    (conversation.turns || []).forEach(appendRecord);
    scrollBottom();
  }

  function showGreeting() {
    clear(els.messages);
    els.messages.classList.add('hidden');
    if (els.greeting) { els.greeting.classList.remove('hidden'); }
    scrollBottom();
  }

  function hideGreeting() {
    if (els.greeting) { els.greeting.classList.add('hidden'); }
  }

  /* ---------- 检索范围：全库检索 / 仅选中文档 ---------- */

  // 与后端 QueryBody.document_ids 的 max_length=100 保持一致：前端先校验并说明，
  // 不静默截取、不自动改用全库。
  var MAX_SCOPE_DOCUMENTS = 100;

  function normalizeIds(ids) {
    return Array.from(new Set((ids || []).map(Number).filter(function (id) {
      return id > 0;
    }))).sort(function (a, b) { return a - b; });
  }

  function lockedScope() { return S.currentId !== null; }
  function activeMode() { return lockedScope() ? S.currentScopeMode : S.scopeMode; }
  function activeIds() { return lockedScope() ? S.currentDocumentIds : S.newDocumentIds; }

  function findDocument(id) {
    for (var i = 0; i < S.documents.length; i++) {
      if (S.documents[i].id === id) { return S.documents[i]; }
    }
    return null;
  }

  function docLabel(doc) {
    return (doc.filename || ('文档 ' + doc.id)) + (doc.version ? '（' + doc.version + '）' : '');
  }

  function scopeNames(ids) {
    return (ids || []).map(findDocument).filter(Boolean).map(docLabel);
  }

  // 已选但不在当前文档列表中的 id（被删除/无权限/列表未加载时不可判定）。
  function scopeMissingIds(ids) {
    if (!S.documentsLoaded) { return []; }
    var known = new Set(S.documents.map(function (doc) { return doc.id; }));
    return (ids || []).filter(function (id) { return !known.has(id); });
  }

  function filteredDocs() {
    var needle = S.docFilter;
    if (!needle) { return S.documents.slice(); }
    return S.documents.filter(function (doc) {
      return ((doc.filename || '') + ' ' + (doc.version || '')).toLowerCase().indexOf(needle) !== -1;
    });
  }

  // 勾选/取消只改这一个文档：搜索隐藏的其它已选文档必须保持勾选。
  // 返回是否真的改变了范围；生成中/会话已固定/超过上限时拒绝修改并说明。
  function toggleDocument(id, on) {
    if (lockedScope() || S.busy) { return false; }
    if (on && S.newDocumentIds.indexOf(id) === -1
        && S.newDocumentIds.length >= MAX_SCOPE_DOCUMENTS) {
      S.scopeNotice = '最多只能选择 ' + MAX_SCOPE_DOCUMENTS
        + ' 个文档；请先取消部分已选，或缩小搜索范围。';
      renderScope();
      return false;
    }
    var picked = new Set(S.newDocumentIds);
    if (on) { picked.add(id); } else { picked.delete(id); }
    S.newDocumentIds = normalizeIds(Array.from(picked));
    S.scopeNotice = '';
    renderScope();
    return true;
  }

  function resetScopeDraft() {
    S.scopeMode = 'all';
    S.newDocumentIds = [];
    S.docFilter = '';
    S.scopeNotice = '';
    if (els.documentScopeSearch) { els.documentScopeSearch.value = ''; }
  }

  function setDocumentScope(ids, locked, mode) {
    ids = normalizeIds(ids);
    if (locked) {
      // 优先信任服务端返回的 scope_mode；仅在旧响应没有该字段时按 ID 推断。
      // selected + 空列表是合法组合（原限定文档已删除/不可见），不能当成全库。
      var serverMode = (mode === 'selected' || mode === 'all')
        ? mode : (ids.length ? 'selected' : 'all');
      S.currentDocumentIds = ids;
      S.currentScopeMode = serverMode;
      S.scopeMode = 'all';
      S.newDocumentIds = [];
    } else {
      if (ids.length > MAX_SCOPE_DOCUMENTS) {
        S.scopeNotice = '已选 ' + ids.length + ' 个文档，超过上限 ' + MAX_SCOPE_DOCUMENTS
          + ' 个；请缩小范围后再选择。不会自动截取，也不会改用全库检索。';
        renderScope();
        return;
      }
      S.currentDocumentIds = [];
      S.currentScopeMode = 'all';
      S.newDocumentIds = ids;
      S.scopeMode = ids.length ? 'selected' : 'all';
    }
    S.scopeNotice = '';
    renderScope();
  }

  // 发送前校验：宁可拦住并说明，也不把"仅选中文档却零勾选"变成全库检索。
  function scopeBlockReason() {
    if (S.currentId !== null) { return ''; }  // 追问沿用后端已固化的范围
    if (S.scopeMode !== 'selected') { return ''; }
    if (!S.newDocumentIds.length) {
      return '已选择「仅选中文档」，但没有勾选任何文档。请先勾选文档，或切换为「全库检索」；不会自动改用全库检索。';
    }
    if (S.newDocumentIds.length > MAX_SCOPE_DOCUMENTS) {
      return '已选 ' + S.newDocumentIds.length + ' 个文档，超过上限 ' + MAX_SCOPE_DOCUMENTS
        + ' 个。请减少选择后再发送；不会自动截取前 ' + MAX_SCOPE_DOCUMENTS + ' 个，也不会改用全库检索。';
    }
    if (!S.documentsLoaded) {
      return '文档列表未成功加载，无法确认所选文档是否仍可用。请先点「刷新」重新加载，或切换为「全库检索」。';
    }
    var missing = scopeMissingIds(S.newDocumentIds);
    if (missing.length) {
      return '有 ' + missing.length + ' 个已选文档已删除或不可用（ID：' + missing.join('、')
        + '）。请刷新文档列表后重新选择；不会回退到全库检索。';
    }
    return '';
  }

  function renderDeviceButtons() {
    var chosen = activeMode() === 'selected' ? activeIds() : [];
    var selectedDevice = '';
    qsa('[data-device]').forEach(function (btn) {
      var matching = deviceDocumentIds(S.documents, btn.dataset.device);
      var whole = matching.length > 0 && matching.length === chosen.length
        && matching.every(function (id) { return chosen.indexOf(id) !== -1; });
      if (whole) { selectedDevice = btn.dataset.device; }
      btn.setAttribute('aria-pressed', String(whole));
      btn.disabled = lockedScope() || S.busy || !S.documentsLoaded || !matching.length;
      btn.title = matching.length ? '限定 ' + matching.length + ' 个对应文档' : '暂无可明确匹配的设备文档';
    });
    if (els.deviceScopeHint) {
      els.deviceScopeHint.textContent = !S.documentsLoaded
        ? (S.documentsLoading ? '正在加载设备文档…' : '设备文档未加载成功，可手动选择文档或刷新文档列表。')
        : selectedDevice ? '已选择 ' + selectedDevice + '，限定 ' + chosen.length + ' 个文档。'
        : activeMode() === 'selected' ? '已手选文档；点设备按钮会替换下方手选范围。'
        : '未选择设备时为全库检索；点设备按钮会切换为「仅选中文档」并替换手选范围。';
    }
  }

  function updateScopeActionLabels() {
    var docs = filteredDocs();
    if (els.documentScopeSelectAll) {
      els.documentScopeSelectAll.textContent = S.docFilter
        ? '全选筛选结果（' + docs.length + '）' : '全选文档（' + docs.length + '）';
      els.documentScopeSelectAll.disabled = lockedScope() || S.busy || !S.documentsLoaded || !docs.length;
    }
    if (els.documentScopeClear) {
      els.documentScopeClear.textContent = '清空已选（' + S.newDocumentIds.length + '）';
      els.documentScopeClear.disabled = lockedScope() || S.busy || !S.newDocumentIds.length;
    }
    if (els.documentScopeReload) {
      els.documentScopeReload.disabled = S.documentsLoading || S.busy;
    }
  }

  function renderDocumentOptions() {
    if (!els.documentScopeOptions) { return; }
    clear(els.documentScopeOptions);
    if (lockedScope() || activeMode() !== 'selected') { return; }
    if (!S.documents.length) {
      els.documentScopeOptions.appendChild(h('p', { class: 'document-scope-empty' }, [
        S.documentsError ? '文档列表未加载成功，请点「刷新」重试（不会改用全库检索）。'
          : (S.documentsLoading ? '正在加载文档列表…' : '暂无可用文档；请先在管理页上传并等待处理完成。')
      ]));
      return;
    }
    var docs = filteredDocs();
    if (!docs.length) {
      els.documentScopeOptions.appendChild(h('p', { class: 'document-scope-empty' }, [
        '没有文件名匹配「' + S.docFilter + '」的文档；已勾选的文档不会被搜索清掉。'
      ]));
      return;
    }
    docs.forEach(function (doc) {
      var label = docLabel(doc);
      var box = h('input', { type: 'checkbox', value: String(doc.id) });
      box.checked = S.newDocumentIds.indexOf(doc.id) !== -1;
      // 生成中或会话已固定时，勾选本身也不可修改（旧版是禁用的，不能退化）。
      box.disabled = S.busy;
      var only = h('button', {
        class: 'doc-only', type: 'button', title: '仅选此文档', 'aria-label': '仅选择 ' + label
      }, ['仅选']);
      only.disabled = S.busy;
      only.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (lockedScope() || S.busy) { return; }
        S.newDocumentIds = [doc.id];
        S.scopeNotice = '';
        renderScope();
        toast('已限定为：' + label, 'info');
      });
      box.addEventListener('change', function (ev) {
        var target = (ev && ev.target) || box;
        if (!toggleDocument(doc.id, !!target.checked)) {
          // 被拒绝（生成中/已固定/超上限）时把勾选状态恢复成模型状态。
          target.checked = S.newDocumentIds.indexOf(doc.id) !== -1;
        }
      });
      els.documentScopeOptions.appendChild(h('label', { class: 'document-scope-option', title: label }, [
        box,
        h('span', { class: 'doc-name' }, [label]),
        only
      ]));
    });
  }

  function renderScope() {
    if (!els.documentScope) { return; }
    var locked = lockedScope();
    var mode = activeMode();
    var ids = activeIds();
    var selected = mode === 'selected';
    var names = scopeNames(ids);
    var missing = scopeMissingIds(ids);
    // 服务端回显 selected + 空 ID：首问选定的文档已删除或不再可见。
    var unavailable = locked && selected && ids.length === 0;

    if (els.scopeIcon) { els.scopeIcon.innerHTML = icon('layers'); }
    if (els.documentScopeValue) {
      if (unavailable) {
        els.documentScopeValue.textContent = '仅选中文档 · 原文档不可用（已固定）';
      } else {
        els.documentScopeValue.textContent = selected
          ? '仅选中文档 · ' + ids.length + ' 个' + (locked ? '（已固定）' : '')
          : '全库检索' + (locked ? '（已固定）' : '');
      }
      els.documentScopeValue.className = 'scope-badge ' + (selected ? 'is-selected' : 'is-all');
    }
    if (els.scopeModeAll && els.scopeModeSelected) {
      els.scopeModeAll.checked = !selected;
      els.scopeModeSelected.checked = selected;
      els.scopeModeAll.disabled = locked || S.busy;
      els.scopeModeSelected.disabled = locked || S.busy;
    }
    if (els.scopeModeAllLabel && els.scopeModeSelectedLabel) {
      els.scopeModeAllLabel.classList.toggle('is-active', !selected);
      els.scopeModeSelectedLabel.classList.toggle('is-active', selected);
      els.scopeModeAllLabel.classList.toggle('is-disabled', locked || S.busy);
      els.scopeModeSelectedLabel.classList.toggle('is-disabled', locked || S.busy);
    }
    if (els.documentScopeHint) {
      if (S.busy && !locked) {
        els.documentScopeHint.textContent =
          '正在生成回答，本次检索范围已固定、暂时不能修改；回答结束后才能继续调整。';
      } else if (unavailable) {
        els.documentScopeHint.textContent =
          '本对话原为「仅选中文档」，但首问选定的文档已删除或不再可见；请新建对话并重新选择。';
      } else if (locked) {
        els.documentScopeHint.textContent = selected
          ? '本对话固定为「仅选中文档」：只在本对话首问选定的 ' + ids.length + ' 个文档中检索。'
          : '本对话固定为「全库检索」：在你可见的全部已就绪文档中查找。';
      } else if (selected) {
        els.documentScopeHint.textContent = ids.length
          ? '仅选中文档：只在你勾选的 ' + ids.length + ' 个文档中检索，范围外文档不会作为来源。'
          : '仅选中文档：请至少勾选一个文档；未勾选时不会发送，也不会自动改用全库检索。';
      } else {
        els.documentScopeHint.textContent = ids.length
          ? '全库检索：当前在全部可见文档中检索（已选的 ' + ids.length + ' 个文档暂不生效，切回「仅选中文档」可继续使用）。'
          : '全库检索：在你可见的全部已就绪文档中查找。';
      }
    }
    if (els.documentScopeBody) {
      els.documentScopeBody.classList.toggle('hidden', locked || !selected);
    }
    if (els.documentScopeLock) {
      els.documentScopeLock.classList.toggle('hidden', !locked);
    }
    if (locked && els.documentScopeLockText) {
      var lockText;
      if (unavailable) {
        lockText = '本对话范围：仅选中文档（首问选定的文档已删除或不再可见，无法显示文件名）。'
          + '范围仍固定在原文档；如需更换，请新建对话并重新选择。';
      } else {
        lockText = selected
          ? '本对话范围：仅选中文档 · ' + ids.length + ' 个'
            + (names.length ? '：' + names.join('、') : '')
          : '本对话范围：全库检索';
      }
      if (missing.length) {
        lockText += '；其中 ' + missing.length + ' 个文档已删除或不可用（ID：' + missing.join('、') + '）';
      }
      els.documentScopeLockText.textContent = lockText;
      els.documentScopeLockText.classList.toggle('is-missing', unavailable || missing.length > 0);
    }
    if (els.documentScopeSelected) {
      if (locked) {
        els.documentScopeSelected.textContent = '';
      } else if (selected) {
        els.documentScopeSelected.textContent = ids.length
          ? '已选 ' + ids.length + ' 个文档'
            + (names.length < ids.length ? '（其中 ' + (ids.length - names.length) + ' 个不在当前列表）' : '')
          : '尚未选择文档';
      } else {
        els.documentScopeSelected.textContent = '未限定文档（全库检索）';
      }
    }
    if (els.documentScopeChips) {
      clear(els.documentScopeChips);
      if (!locked && selected) {
        ids.forEach(function (id) {
          var doc = findDocument(id);
          var label = doc ? docLabel(doc) : '文档 ID ' + id;
          var x = h('button', {
            class: 'scope-chip-x', type: 'button', title: '取消选择',
            'aria-label': '取消选择 ' + label
          }, ['×']);
          x.disabled = S.busy;
          x.addEventListener('click', function (ev) {
            ev.preventDefault();
            toggleDocument(id, false);
          });
          els.documentScopeChips.appendChild(h('span', { class: 'scope-chip' }, [
            h('span', { class: 'scope-chip-name', title: label }, [label]),
            x
          ]));
        });
      }
    }
    if (els.documentScopeError) {
      if (!locked && S.documentsError) {
        els.documentScopeError.textContent = '文档列表加载失败：' + S.documentsError
          + '。不会改用全库检索；请点「刷新」重试。';
        els.documentScopeError.classList.remove('hidden');
      } else {
        els.documentScopeError.textContent = '';
        els.documentScopeError.classList.add('hidden');
      }
    }
    if (els.documentScopeWarn) {
      var notices = [];
      if (!locked && selected) {
        if (missing.length) {
          notices.push('有 ' + missing.length + ' 个已选文档已删除或不可用（ID：' + missing.join('、') + '）。');
        }
        if (S.scopeNotice) { notices.push(S.scopeNotice); }
      } else if (!locked && S.scopeNotice) {
        notices.push(S.scopeNotice);
      }
      if (notices.length) {
        els.documentScopeWarn.textContent = notices.join(' ');
        els.documentScopeWarn.classList.remove('hidden');
      } else {
        els.documentScopeWarn.textContent = '';
        els.documentScopeWarn.classList.add('hidden');
      }
    }
    renderDocumentOptions();
    updateScopeActionLabels();
    renderDeviceButtons();
  }

  async function loadDocuments() {
    if (S.documentsLoading) { return; }
    S.documentsLoading = true;
    renderScope();
    try {
      var data = await api('/api/documents');
      S.documents = Array.isArray(data && data.items) ? data.items : [];
      S.documentsLoaded = true;
      S.documentsError = '';
    } catch (e) {
      // 加载失败不等于全库检索：保留失败状态并给出重试入口。
      S.documentsLoaded = false;
      S.documentsError = (e && e.message) ? e.message : '未知错误';
      if (!(e && e.status === 401)) {
        toast('文档列表加载失败：' + S.documentsError + '（不会自动改用全库检索）', 'error');
      }
    } finally {
      S.documentsLoading = false;
      renderScope();
    }
  }

  function renderHistoryList(items, total) {
    clear(els.historyList);
    items = items || [];
    if (!items.length) {
      els.historyEmpty.classList.remove('hidden');
      els.historyLoading.classList.add('hidden');
      els.historyFoot.textContent = '';
      return;
    }
    els.historyEmpty.classList.add('hidden');
    els.historyLoading.classList.add('hidden');

    items.forEach(function (item) {
      var btn = h('button', {
        class: 'history-item' + (item.id === S.currentId ? ' is-active' : ''),
        type: 'button',
        title: item.title || ''
      }, [
        h('span', { class: 'h-dot' + (item.has_error ? ' err' : '') }),
        h('span', { class: 'h-body' }, [
          h('span', { class: 'h-q' }, [excerpt(item.title, 80)]),
          h('span', { class: 'h-time' }, [
            fmtTime(item.updated_at),
            ' · ' + item.turn_count + ' 轮',
            item.has_error ? ' · 含失败记录' : ''
          ])
        ])
      ]);
      btn.dataset.id = String(item.id);
      btn.addEventListener('click', function () { selectChat(item.id); });
      els.historyList.appendChild(btn);
    });

    var footText = '共 ' + (total != null ? total : items.length) + ' 条';
    if (total > 25) { footText += '（仅显示最近 25 条）'; }
    els.historyFoot.textContent = footText;
  }

  function selectChat(id) {
    if (S.busy) { toast('正在生成回答，请稍候再切换', 'warn'); return; }
    if (id === S.currentId && !els.messages.classList.contains('hidden')) { return; }
    S.currentId = id;
    updateDeleteButton();
    renderScope();
    markActive(id);
    loadConversationDetail(id);
  }

  function updateDeleteButton() {
    els.deleteChatBtn.disabled = S.busy || S.currentId === null;
  }

  async function deleteSelectedChat() {
    if (S.busy || S.currentId === null) { return; }
    var chatId = S.currentId;
    if (!window.confirm('确定删除当前选中的对话？其中全部问答将被删除且无法恢复。')) { return; }
    els.deleteChatBtn.disabled = true;
    try {
      await api('/api/conversations/' + chatId, { method: 'DELETE' });
      S.currentId = null;
      resetScopeDraft();
      setDocumentScope([], false);
      showGreeting();
      await loadHistory();
      toast('对话已删除', 'success');
    } catch (e) {
      toast('删除失败：' + (e.message || '请稍后重试'), 'error');
    } finally {
      updateDeleteButton();
    }
  }

  function markActive(id) {
    qsa('.history-item').forEach(function (it) {
      it.classList.toggle('is-active', it.dataset.id === String(id));
    });
  }

  async function loadConversationDetail(id) {
    hideGreeting();
    clear(els.messages);
    els.messages.classList.remove('hidden');
    var loadBox = h('div', { class: 'msg msg-a' }, [
      h('div', { class: 'bubble bubble-a bubble-load' }, [h('span', { class: 'spin' }), '正在加载对话…'])
    ]);
    els.messages.appendChild(loadBox);
    scrollBottom();
    try {
      var conversation = await api('/api/conversations/' + id);
      renderConversation(conversation);
    } catch (e) {
      if (e && e.status === 401) { return; }
      clear(els.messages);
      els.messages.appendChild(errorBubble('加载对话详情失败：' + (e && e.message ? e.message : '未知错误')));
    }
  }

  async function loadHistory() {
    els.historyLoading.classList.remove('hidden');
    els.historyList.classList.add('hidden');
    try {
      var data = await api('/api/conversations?limit=25');
      els.historyList.classList.remove('hidden');
      els.historyLoading.classList.add('hidden');
      S.historyItems = data.items || [];
      renderHistoryList(S.historyItems, data.total);
      // 自动展开最新一条
      if (S.historyItems.length && S.currentId === null) {
        selectChat(S.historyItems[0].id);
      }
      return data;
    } catch (e) {
      if (e && e.status === 401) { return null; }
      els.historyLoading.classList.add('hidden');
      els.historyList.classList.remove('hidden');
      toast('加载历史记录失败：' + (e && e.message ? e.message : '未知错误'), 'error');
      return null;
    }
  }

  /* ---------- 发送问答 ---------- */

  function showTyping() {
    S.typingEl = typingBubble();
    els.messages.appendChild(S.typingEl);
    scrollBottom();
  }

  function removeTyping() {
    if (S.typingEl && S.typingEl.parentNode) {
      S.typingEl.parentNode.removeChild(S.typingEl);
    }
    S.typingEl = null;
  }

  /** 生成中：显示“停止生成”并让读屏知道正在输出 */
  function setGenerating(on) {
    if (els.stopBtn) { els.stopBtn.classList.toggle('hidden', !on); }
    if (els.messages) { els.messages.setAttribute('aria-busy', on ? 'true' : 'false'); }
  }

  function stopGenerating() {
    if (S.abortController) { S.abortController.abort(); }
  }

  async function sendQuestion() {
    if (S.busy) { return; }
    var q = els.input.value.trim();
    if (!q) { toast('请输入问题', 'warn'); els.input.focus(); return; }
    if (q.length > 2000) {
      toast('问题过长（最多 2000 字）', 'warn');
      return;
    }
    // 范围校验放在清空输入框之前：零勾选/列表不可用/文档失效都留在原页面说明。
    var scopeError = scopeBlockReason();
    if (scopeError) {
      S.scopeNotice = scopeError;
      renderScope();
      toast(scopeError, 'warn');
      els.input.focus();
      return;
    }
    S.scopeNotice = '';
    var conversationId = S.currentId;
    S.busy = true;
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    S.abortController = controller;
    updateDeleteButton();
    renderScope();
    hideGreeting();
    els.messages.classList.remove('hidden');
    els.input.value = '';
    autoSize();
    busy(els.sendBtn, true, '生成中…');
    setGenerating(true);

    // 新对话清空欢迎页；继续追问时保留之前的轮次。
    if (conversationId === null) { clear(els.messages); }
    els.messages.appendChild(askBubble(q));
    showTyping();
    scrollBottom();
    var liveBubble = null;
    var liveText = null;

    try {
      var body = { question: q };
      if (conversationId !== null) {
        // 追问沿用后端固化的范围；改范围必须新建对话。
        body.conversation_id = conversationId;
      } else if (S.scopeMode === 'selected') {
        body.scope_mode = 'selected';
        body.document_ids = S.newDocumentIds.slice();
      } else {
        body.scope_mode = 'all';
      }
      var res = await api('/api/query?stream=true', {
        method: 'POST', body: body,
        signal: controller ? controller.signal : undefined,
        onDelta: function (text) {
          if (!liveText) {
            removeTyping();
            liveText = h('div', { class: 'bubble bubble-a' });
            liveBubble = h('div', { class: 'msg msg-a' }, [liveText]);
            els.messages.appendChild(liveBubble);
          }
          liveText.textContent += text;
          scrollBottom();
        }
      });
      // 先把详情取回来再认领 currentId：否则详情请求失败时状态会停在“已切换”，
      // 左侧历史列表和“限定文档”提示都会与实际不一致。
      var conversation = await api('/api/conversations/' + res.conversation_id);
      S.currentId = res.conversation_id;
      renderConversation(conversation);
      await loadHistoryOnly();
    } catch (e) {
      removeTyping();
      if (e && e.status === 401) {
        if (liveBubble && liveBubble.parentNode) { liveBubble.parentNode.removeChild(liveBubble); }
        return;
      }
      if (e && e.aborted) {
        // 用户主动停止：保留已经生成的内容并明确标注，不算错误。
        if (liveText && liveText.textContent) {
          liveText.textContent += '\n（已停止生成）';
        } else if (liveBubble && liveBubble.parentNode) {
          liveBubble.parentNode.removeChild(liveBubble);
          els.messages.appendChild(errorBubble('已停止生成。'));
        }
        S.currentId = conversationId;
        try { await loadHistoryOnly(); } catch (e2) { /* 历史刷新失败不影响已停止的事实 */ }
        return;
      }
      if (liveBubble && liveBubble.parentNode) { liveBubble.parentNode.removeChild(liveBubble); }
      // 后端 502 时 detail 里带 conversation_id，可回拉失败对话展示
      var failedConversationId = e && e.data && e.data.detail && e.data.detail.conversation_id;
      if (failedConversationId) {
        try {
          var failedConversation = await api('/api/conversations/' + failedConversationId);
          S.currentId = failedConversation.id;
          renderConversation(failedConversation);
          await loadHistoryOnly();
          return;
        } catch (e2) { /* 拉取失败：继续走下方通用失败展示 */ }
      }
      // 选定文档可能在发送间隙被删除/重新处理：刷新列表让用户重新确认，而不是沿用旧勾选。
      if (e && e.status === 400 && conversationId === null && S.scopeMode === 'selected') {
        S.scopeNotice = e.message || '';
        await loadDocuments();
      }
      // 通用失败：回滚会话状态到本轮开始之前，保持界面与实际一致。
      S.currentId = conversationId;
      if (conversationId === null) {
        clear(els.messages);
        els.messages.appendChild(askBubble(q));
      }
      els.messages.appendChild(errorBubble('回答失败：' + (e.message || '未知错误')));
      try { await loadHistoryOnly(); } catch (e3) { /* 历史刷新失败不影响错误提示 */ }
    } finally {
      S.abortController = null;
      setGenerating(false);
      busy(els.sendBtn, false);
      S.busy = false;
      updateDeleteButton();
      renderScope();
      els.input.focus();
    }
  }

  /** 只重拉历史列表（不自动选中，保持当前视图） */
  async function loadHistoryOnly() {
    try {
      var data = await api('/api/conversations?limit=25');
      S.historyItems = data.items || [];
      renderHistoryList(S.historyItems, data.total);
      markActive(S.currentId);
    } catch (e) { /* 静默：下一轮会再拉 */ }
  }

  function autoSize() {
    els.input.style.height = 'auto';
    els.input.style.height = Math.min(els.input.scrollHeight, 180) + 'px';
    if (els.questionCount) { els.questionCount.textContent = els.input.value.length + ' / 2000'; }
  }

  /* ---------- 事件绑定 ---------- */

  function startNewChat() {
    if (S.busy) { toast('正在生成回答，请稍候', 'warn'); return; }
    S.currentId = null;
    resetScopeDraft();
    S.currentDocumentIds = [];
    S.currentScopeMode = 'all';
    renderScope();
    updateDeleteButton();
    showGreeting();
    els.input.focus();
    qsa('.history-item').forEach(function (it) { it.classList.remove('is-active'); });
  }

  function onScopeModeChange() {
    if (lockedScope() || S.busy) {
      renderScope();
      toast('本对话的检索范围已固定；更换范围请新建对话', 'warn');
      return;
    }
    S.scopeMode = els.scopeModeSelected.checked ? 'selected' : 'all';
    S.scopeNotice = '';
    renderScope();
    // 不自动聚焦搜索框：手机端切模式就弹出键盘会遮住文档列表。
  }

  function bindEvents() {
    els.sendBtn.innerHTML = '';
    els.sendBtn.appendChild(h('span', { html: icon('send') }));
    els.sendBtn.appendChild(document.createTextNode('发送'));
    var hi = document.getElementById('historyIcon');
    if (hi) { hi.innerHTML = icon('clock'); }
    var hei = document.getElementById('historyEmptyIcon');
    if (hei) { hei.innerHTML = icon('chat'); }
    var gi = document.getElementById('greetIcon');
    if (gi) { gi.innerHTML = icon('chat'); }

    els.sendBtn.addEventListener('click', function () { sendQuestion(); });
    if (els.stopBtn) { els.stopBtn.addEventListener('click', stopGenerating); }
    els.deleteChatBtn.addEventListener('click', deleteSelectedChat);
    els.newChatBtn.addEventListener('click', startNewChat);
    if (els.documentScopeNewChat) {
      els.documentScopeNewChat.addEventListener('click', startNewChat);
    }

    els.input.addEventListener('input', autoSize);
    if (els.scopeModeAll) { els.scopeModeAll.addEventListener('change', onScopeModeChange); }
    if (els.scopeModeSelected) {
      els.scopeModeSelected.addEventListener('change', onScopeModeChange);
    }
    if (els.documentScopeSearch) {
      els.documentScopeSearch.addEventListener('input', function () {
        S.docFilter = (els.documentScopeSearch.value || '').trim().toLowerCase();
        renderScope();
      });
    }
    if (els.documentScopeSelectAll) {
      els.documentScopeSelectAll.addEventListener('click', function () {
        if (lockedScope() || S.busy || !S.documentsLoaded || S.scopeMode !== 'selected') { return; }
        var docs = filteredDocs();
        if (!docs.length) { return; }
        var picked = new Set(S.newDocumentIds);
        docs.forEach(function (doc) { picked.add(doc.id); });
        if (picked.size > MAX_SCOPE_DOCUMENTS) {
          // 整批拒绝，不做"静默截取前 100 个"：否则实际范围与用户看到的筛选结果不一致。
          S.scopeNotice = '当前筛选结果加入后共 ' + picked.size + ' 个文档，超过上限 '
            + MAX_SCOPE_DOCUMENTS + ' 个；请先缩小搜索范围或取消部分已选。不会自动截取，也不会改用全库检索。';
          renderScope();
          toast('已选文档超过 ' + MAX_SCOPE_DOCUMENTS + ' 个上限，未加入任何文档', 'warn');
          return;
        }
        S.newDocumentIds = normalizeIds(Array.from(picked));
        S.scopeNotice = '';
        renderScope();
        toast('已把当前筛选的 ' + docs.length + ' 个文档加入已选', 'info');
      });
    }
    if (els.documentScopeClear) {
      els.documentScopeClear.addEventListener('click', function () {
        if (lockedScope() || S.busy || !S.newDocumentIds.length) { return; }
        S.newDocumentIds = [];
        S.scopeNotice = '';
        renderScope();
        toast('已清空已选文档；检索范围仍是「仅选中文档」，不会自动变成全库', 'info');
      });
    }
    if (els.documentScopeReload) {
      els.documentScopeReload.addEventListener('click', function () { loadDocuments(); });
    }
    qsa('[data-device]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (S.busy || S.currentId !== null || !S.documentsLoaded) { return; }
        var ids = deviceDocumentIds(S.documents, btn.dataset.device);
        if (!ids.length) {
          toast('没有找到该设备的可用文档，请在「检索范围」中手动选择', 'warn');
          return;
        }
        if (ids.length > MAX_SCOPE_DOCUMENTS) {
          S.scopeMode = 'selected';
          S.scopeNotice = '该设备匹配 ' + ids.length + ' 个文档，超过上限 ' + MAX_SCOPE_DOCUMENTS
            + ' 个；请手动缩小范围后再试。不会自动截取或改用全库检索。';
          renderScope();
          toast('设备匹配文档超过上限，未替换检索范围', 'warn');
          return;
        }
        setDocumentScope(ids, false);
        toast('已切换为「仅选中文档」：' + btn.dataset.device + '（' + ids.length + ' 个）', 'info');
        els.input.focus();
      });
    });
    els.input.addEventListener('compositionstart', function () { S.composing = true; });
    els.input.addEventListener('compositionend', function () {
      setTimeout(function () { S.composing = false; }, 0);
    });
    els.input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
        if (ev.isComposing || S.composing) { return; }
        ev.preventDefault();
        sendQuestion();
      }
    });
  }

  /* ---------- 启动 ---------- */

  async function boot() {
    cacheEls();
    wireGlobalErrors();
    bindEvents();
    els.input.focus();

    var me = await initSession();
    if (!me) { return; }
    S.me = me;

    // 模型未就绪提示（嵌入模型在后台线程加载中）
    if (me.model_ready === false) {
      showNotice(me.model_message || '知识库模型仍在初始化，暂时无法问答与上传文档，请稍候刷新。');
    }

    registerSW();

    await loadDocuments();
    await loadHistory();
  }

  document.addEventListener('DOMContentLoaded', function () {
    boot().catch(function (e) {
      try {
        if (!(e && e.status === 401)) {
          toast('问答页初始化失败：' + (e && e.message ? e.message : '未知错误'), 'error');
        }
      } catch (x) { /* ignore */ }
    });
  });
})();
