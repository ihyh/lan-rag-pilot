/* ============================================================
 * app.js — 问答页逻辑
 *  - 会话校验（未登录跳 /login）
 *  - 左侧历史列表 + 右侧问答（Enter 发送 / Ctrl+Enter 换行）
 *  - 发送后拉取完整问答记录渲染「答案 + 引用来源卡」
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

(function () {
  var S = {
    me: null,
    busy: false,           // 是否正在生成回答
    currentId: null,       // 当前展开的历史记录 id
    typingEl: null,        // “正在生成”占位气泡
    composing: false       // 中文输入法组合中
  };

  var els = {};
  function cacheEls() {
    els.scroll = document.getElementById('chatScroll');
    els.messages = document.getElementById('messages');
    els.greeting = document.getElementById('chatGreeting');
    els.input = document.getElementById('questionInput');
    els.sendBtn = document.getElementById('sendBtn');
    els.newChatBtn = document.getElementById('newChatBtn');
    els.deleteChatBtn = document.getElementById('deleteChatBtn');
    els.historyList = document.getElementById('historyList');
    els.historyEmpty = document.getElementById('historyEmpty');
    els.historyLoading = document.getElementById('historyLoading');
    els.historyFoot = document.getElementById('historyFoot');
    els.notice = document.getElementById('meNotice');
    els.questionCount = document.getElementById('questionCount');
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
      return h('div', { class: 'source-card' }, [
        h('div', { class: 'doc-ic', html: icon('doc') }),
        h('div', { class: 's-main' }, [
          h('div', { class: 's-top' }, [
            h('span', { class: 's-name', title: s.filename || '' }, [group.numbers.map(function (n) { return '[' + n + ']'; }).join('') + ' ' + (s.filename || '未知文档')])
          ]),
          h('div', { class: 's-meta' }, [
            h('span', { class: 's-loc' }, [sourceLoc(s)]),
            h('a', { class: 's-open', href: sourceUrl(s), target: '_blank', rel: 'noopener noreferrer' }, ['打开原文'])
          ]),
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
   * 将一条问答记录渲染到消息区（覆盖当前视图）
   * rec: GET /api/chats/{id} 返回的记录（含 sources）
   */
  function renderRecord(rec) {
    if (!rec) { return; }
    hideGreeting();
    clear(els.messages);
    els.messages.classList.remove('hidden');
    els.messages.appendChild(askBubble(rec.question));
    if (rec.status === 'error') {
      var why = rec.error || '未知错误';
      els.messages.appendChild(errorBubble('回答失败：' + why + '（记录 #' + rec.id + '）'));
    } else {
      els.messages.appendChild(answerBubble(rec.answer));
      els.messages.appendChild(feedbackNode(rec));
      els.messages.appendChild(sourcesNode(rec.answer, rec.sources));
    }
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

  /* ---------- 历史列表 ---------- */

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
        title: item.question || ''
      }, [
        h('span', { class: 'h-dot' + (item.status === 'error' ? ' err' : '') }),
        h('span', { class: 'h-body' }, [
          h('span', { class: 'h-q' }, [excerpt(item.question, 80)]),
          h('span', { class: 'h-time' }, [
            fmtTime(item.created_at),
            item.status === 'error' ? ' · 失败' : ''
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
    markActive(id);
    loadChatDetail(id);
  }

  function updateDeleteButton() {
    els.deleteChatBtn.disabled = S.busy || S.currentId === null;
  }

  async function deleteSelectedChat() {
    if (S.busy || S.currentId === null) { return; }
    var chatId = S.currentId;
    if (!window.confirm('确定删除当前选中的对话？删除后无法恢复。')) { return; }
    els.deleteChatBtn.disabled = true;
    try {
      await api('/api/chats/' + chatId, { method: 'DELETE' });
      S.currentId = null;
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

  async function loadChatDetail(id) {
    hideGreeting();
    clear(els.messages);
    els.messages.classList.remove('hidden');
    var loadBox = h('div', { class: 'msg msg-a' }, [
      h('div', { class: 'bubble bubble-a bubble-load' }, [h('span', { class: 'spin' }), '正在加载对话详情…'])
    ]);
    els.messages.appendChild(loadBox);
    scrollBottom();
    try {
      var rec = await api('/api/chats/' + id);
      renderRecord(rec);
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
      var data = await api('/api/chats?limit=25');
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

  async function sendQuestion() {
    if (S.busy) { return; }
    var q = els.input.value.trim();
    if (!q) { toast('请输入问题', 'warn'); els.input.focus(); return; }
    if (q.length > 2000) {
      toast('问题过长（最多 2000 字）', 'warn');
      return;
    }
    S.busy = true;
    updateDeleteButton();
    hideGreeting();
    els.messages.classList.remove('hidden');
    els.input.value = '';
    autoSize();
    busy(els.sendBtn, true, '生成中…');

    // 新的提问先清空当前对话视图，立即展示问题气泡
    clear(els.messages);
    els.messages.appendChild(askBubble(q));
    showTyping();
    scrollBottom();

    try {
      var res = await api('/api/query', { method: 'POST', body: { question: q } });
      var rec = await api('/api/chats/' + res.chat_id);
      renderRecord(rec);
      S.currentId = rec.id;
      await loadHistoryOnly();
    } catch (e) {
      removeTyping();
      if (e && e.status === 401) { return; }
      // 后端 502 时 detail 里带 chat_id，可回拉失败记录展示
      var cid = e && e.data && e.data.detail && e.data.detail.chat_id;
      if (cid) {
        try {
          var errRec = await api('/api/chats/' + cid);
          renderRecord(errRec);
          S.currentId = errRec.id;
          await loadHistoryOnly();
        } catch (e2) {
          clear(els.messages);
          els.messages.appendChild(askBubble(q));
          els.messages.appendChild(errorBubble('回答失败：' + (e.message || '未知错误')));
        }
      } else {
        clear(els.messages);
        els.messages.appendChild(askBubble(q));
        els.messages.appendChild(errorBubble('回答失败：' + (e.message || '未知错误')));
      }
    } finally {
      busy(els.sendBtn, false);
      S.busy = false;
      updateDeleteButton();
      els.input.focus();
    }
  }

  /** 只重拉历史列表（不自动选中，保持当前视图） */
  async function loadHistoryOnly() {
    try {
      var data = await api('/api/chats?limit=25');
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
    els.deleteChatBtn.addEventListener('click', deleteSelectedChat);
    els.newChatBtn.addEventListener('click', function () {
      if (S.busy) { toast('正在生成回答，请稍候', 'warn'); return; }
      S.currentId = null;
      updateDeleteButton();
      showGreeting();
      els.input.focus();
      qsa('.history-item').forEach(function (it) { it.classList.remove('is-active'); });
    });

    els.input.addEventListener('input', autoSize);
    qsa('.suggestion').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (S.busy) { return; }
        els.input.value = btn.dataset.question || '';
        autoSize();
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
