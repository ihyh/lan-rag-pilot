/* ============================================================
 * app.js — 问答页逻辑
 *  - 会话校验（未登录跳 /login）
 *  - 左侧对话列表 + 右侧多轮问答（Enter 发送 / Ctrl+Enter 换行）
 *  - 发送后拉取完整对话，逐轮渲染「答案 + 引用来源卡」
 *  - 顶层 try/catch + toast，避免未捕获异常白屏
 * ============================================================ */
'use strict';

// 沿用显式 [n]/[n,m] 语法；屏蔽代码、SECS长度与URL，保持原始文本偏移。
function citationSpans(answer, sources) {
  var text = String(answer || '');
  function mask(value) { return value.replace(/[^\r\n]/g, ' '); }
  var visible = text
    .replace(/(^|\n)[ \t]*(`{3,}|~{3,})[^\n]*(?:\n|$)[\s\S]*?(?:\n[ \t]*\2[ \t]*(?=\r?\n|$)|$)/g, mask)
    .replace(/`+[^`]*`+/g, mask)
    .replace(/!?\[[^\]\r\n]*\]\([^\)\r\n]*\)/g, mask)
    .replace(/https?:\/\/[^\s<>"`，。；！？、）]+/g, mask)
    .replace(/(?:A|B|L|[UI][1248]|F[48])\[\d+\]/g, function (token, offset, value) {
      return offset && /[A-Za-z0-9]/.test(value[offset - 1]) ? token : mask(token);
    });
  var spans = [], pattern = /\[(\d+(?:\s*[,，、]\s*\d+)*)\]/g, match;
  while ((match = pattern.exec(visible))) {
    var numbers = match[1].split(/[,，、]/).map(Number);
    if (text[match.index - 1] === '\\' || text[match.index - 1] === '[' || text[pattern.lastIndex] === ']') { continue; }
    if (!numbers.every(function (number) { return number > 0 && number <= (sources || []).length && sources[number - 1]; })) { continue; }
    spans.push({ start: match.index, end: pattern.lastIndex, numbers: numbers });
  }
  return spans;
}

// 一条原始来源编号对应独立卡片；同页片段也不能合并身份。
function citedSources(answer, sources) {
  var ids = new Set();
  citationSpans(answer, sources).forEach(function (span) {
    span.numbers.forEach(function (number) { ids.add(number); });
  });
  var groups = [];
  (sources || []).forEach(function (s, index) {
    var number = index + 1;
    if (!ids.has(number)) { return; }
    groups.push({ source: s, numbers: [number], excerpts: s.excerpt ? [s.excerpt] : [] });
  });
  return groups;
}

// 不把回答未引用的来源当成证据；同一文档只列一次。
function uncitedDocuments(answer, sources) {
  var seen = new Set(citedSources(answer, sources).map(function (group) { return group.source.document_id; }));
  return (sources || []).filter(function (source) {
    var snapshot = source && source.source_snapshot;
    if (!source || !Number.isSafeInteger(source.document_id) || source.document_id <= 0
        || seen.has(source.document_id) || source.evidence_status !== 'full'
        || !snapshot || snapshot.format !== 1) { return false; }
    seen.add(source.document_id);
    return true;
  });
}

// 设备快捷范围包含该设备的资料和明确标记为通用的资料。
function deviceDocumentIds(documents, device) {
  if (['PLM', 'Plus_500', 'Plus_Pro', 'LP'].indexOf(device) === -1) { return []; }
  return (documents || []).filter(function (doc) { return doc.device === device || doc.device === '通用'; })
    .map(function (doc) { return doc.id; });
}

(function () {
  var S = {
    me: null,
    historyPage: 0, historyRequest: 0, historyTotal: 0,
    busy: false,           // 是否正在生成回答
    currentId: null,       // 当前展开的对话 id
    detailState: 'idle',   // idle / loading / ready / error；未就绪时不能追问
    detailRequest: 0,      // 防止 A→B→A 时第一条 A 的迟到响应覆盖最新 A
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
    followupTo: null,         // 只有用户点击“补充这题”才关联
    followupText: '',
    followupTurns: [],
    conversationUserId: null,
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
    els.historyPages = document.getElementById('historyPages');
    els.historyPageStatus = document.getElementById('historyPageStatus');
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
    els.historyPanel = document.getElementById('historyPanel');
    els.libraryPanel = document.getElementById('libraryPanel');
    els.composerScope = document.getElementById('composerScope');
    els.openSourcesBtn = document.getElementById('openSourcesBtn');
    els.quickNewChatBtn = document.getElementById('quickNewChatBtn');
    els.libraryContent = document.getElementById('libraryContent');
    els.finishSelectionBtn = document.getElementById('finishSelectionBtn');
    els.followupNotice = document.getElementById('followupNotice');
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

  function answerBubble(text, sources, openSource) {
    text = text || '（本次没有返回文本）';
    var bubble = h('div', { class: 'bubble bubble-a' });
    bubble.appendChild(renderAnswerContent(text, sources, openSource));
    return h('div', { class: 'msg msg-a' }, [
      bubble
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
    if (s.source_snapshot && s.source_snapshot.format === 1 && s.source_snapshot.source_kind === 'log') {
      var start = s.source_snapshot.line_start, end = s.source_snapshot.line_end;
      return Number.isSafeInteger(start) && start > 0 && Number.isSafeInteger(end) && end >= start
        ? '日志第 ' + start + (end === start ? '' : '–' + end) + ' 行' : '日志行号未记录';
    }
    if (s.source_snapshot && s.source_snapshot.source_kind === 'image') { if (s.source_snapshot.image_caption_only === true) { return '原图 · 人工图片说明（未包含识别文字）'; } if (s.source_snapshot.content_scope === 'image_visual_and_text') { return s.source_snapshot.ocr_human_review_pending === true ? '原图 · 视觉模型分析与OCR（OCR未人工核对，图文关系需核对）' : '原图 · 视觉模型分析与OCR（图文关系需核对）'; } return s.source_snapshot.ocr_human_review_pending === true ? '原图 · 图片说明或识别文字（OCR尚未经人工核对）' : '原图 · 图片说明或识别文字（图表关系需核对）'; }
    if (s.source_snapshot && s.source_snapshot.format === 1 && s.source_snapshot.source_kind === 'web') {
      return s.paragraph ? '网页提取块 ' + s.paragraph + '（非页码）' : '网页提取块位置未记录（非页码）';
    }
    if (s.page !== null && s.page !== undefined && s.page !== '') { return '第 ' + s.page + ' 页'; }
    if (/\.xlsx$/i.test(s.filename || '')) { return '工作表与单元格位置见下方摘录'; }
    if (s.paragraph !== null && s.paragraph !== undefined && s.paragraph !== '') { return '片段起始：第 ' + s.paragraph + ' 段'; }
    return '未记录具体位置';
  }

  function sourceUrl(s) {
    var url = '/api/documents/' + encodeURIComponent(s.document_id) + '/file';
    if (!(s.source_snapshot && s.source_snapshot.source_kind === 'log') && s.page !== null && s.page !== undefined && s.page !== '') {
      url += '#page=' + encodeURIComponent(s.page);
    }
    return url;
  }

  function sharedImageUrl(s) {
    var snapshot = s && s.source_snapshot;
    if (!(S.me && snapshot && snapshot.format === 1 && snapshot.source_kind === 'image'
      && Number.isSafeInteger(s.document_id) && s.document_id > 0
      && typeof snapshot.document_sha256 === 'string' && /^[0-9a-f]{64}$/.test(snapshot.document_sha256))) { return null; }
    // 检查当前访问权限，并核对回答时的原图版本。
    return '/api/documents/' + encodeURIComponent(s.document_id) + '/image?sha256=' + encodeURIComponent(snapshot.document_sha256);
  }

  function answerImagesNode(answer, sources) {
    var images = [], seen = new Set(), cited = citedSources(answer, sources);
    function addImage(s, number, label) {
      var url = sharedImageUrl(s);
      if (!url || s.evidence_status !== 'full' || seen.has(url)) { return; }
      seen.add(url);
      var image = h('img', {class:'answer-image-preview', src:url, alt:label + '：' + s.source_snapshot.filename, loading:'lazy'});
      var error = h('p', {class:'answer-image-error', hidden:true}, ['图片暂时无法显示，可能已不可访问或与回答时的版本不同。']);
      var note = s.source_snapshot.ocr_human_review_pending === true ? ' · OCR 未人工核对' : '';
      var captionText = label + ' [' + number + ']' + note + ' · 图表关系需核对';
      var caption = h('figcaption', {}, [captionText + ' · 点击查看原图']);
      var link = h('a', {href:url, target:'_blank', rel:'noopener noreferrer'}, [image]);
      image.addEventListener('error', function () {
        image.hidden = true; link.classList.add('hidden'); error.hidden = false; caption.textContent = captionText;
      });
      images.push(h('figure', {class:'answer-image'}, [
        link, error, caption
      ]));
    }
    cited.forEach(function (group) { addImage(group.source, group.numbers[0], '引用图片'); });
    cited.forEach(function (group) {
      var source = group.source, snapshot = source.source_snapshot;
      if (source.evidence_status !== 'full' || !snapshot || snapshot.format !== 1 || snapshot.source_kind !== 'web'
          || !Array.isArray(snapshot.related_images)) { return; }
      snapshot.related_images.forEach(function (related) {
        if (!related || !Number.isSafeInteger(related.document_id) || related.document_id <= 0 || typeof related.filename !== 'string') { return; }
        var matches = (sources || []).filter(function (candidate) { return candidate && candidate.document_id === related.document_id; });
        var urls = new Set(matches.map(function (candidate) {
          return candidate.evidence_status === 'full' && candidate.source_snapshot && candidate.source_snapshot.filename === related.filename
            ? sharedImageUrl(candidate) : null;
        }));
        if (urls.size === 1 && !urls.has(null)) { addImage(matches[0], group.numbers[0], '正文关联图片'); }
      });
    });
    return images.length ? h('div', {class:'msg-a answer-images', 'aria-label':'回答引用或正文关联的图片'}, images) : null;
  }

  function sourcesNode(answer, sources, targets, extraCandidates) {
    var items = extraCandidates ? [] : citedSources(answer, sources);
    var candidates = !items.length && (sources || []).length > 0;
    if (candidates) {
      items = sources.map(function (source, index) {
        return {source: source, numbers: [index + 1], excerpts: source.excerpt ? [source.excerpt] : []};
      });
    }
    var wrap = h('details', { class: 'msg-a source-details' }, [
      h('summary', { class: 'sources-title' }, [(candidates ? (extraCandidates ? '查看其他检索到的候选资料' : '查看检索到的候选资料') : '查看引用来源') + (items.length ? '（' + items.length + '）' : '')])
    ]);
    if (candidates) {
      wrap.appendChild(h('p', { class: 'no-sources' }, [extraCandidates ? '以下资料未被本次回答引用，不能视为已引用或已核实，请对照原文。' : '回答没有标注引用；以下是检索到的候选资料，不能视为已引用或已核实，请对照原文。']));
    }
    if (!items.length) {
      wrap.appendChild(h('p', { class: 'no-sources' }, ['本次回答未提供可对应的引用编号，无法确认具体来源。']));
      return wrap;
    }
    var cards = items.map(function (group) {
      var s = group.source;
      var snapshot = s.source_snapshot;
      var saved = snapshot && snapshot.format === 1;
      var name = saved ? snapshot.filename : s.filename;
      var kind = !saved ? '类型待确认' : snapshot.source_kind === 'image' ? '图片'
        : snapshot.source_kind === 'web' ? '网页文章' : snapshot.source_kind === 'log' ? '日志' : '文档';
      var imageUrl = sharedImageUrl(s);
      var meta = [h('span', { class: 's-loc' }, [sourceLoc(s)])];
      if (saved) {
        [['设备', snapshot.device], ['生效日期', snapshot.effective_date], ['适用范围', snapshot.applicability]].forEach(function (entry) {
          if (entry[1]) { meta.push(h('span', {}, [entry[0] + '：' + entry[1]])); }
        });
        if (snapshot.source_kind === 'web' && snapshot.web) {
          var web = snapshot.web;
          if (web.title) {meta.push(h('span', {style:'max-width:100%;overflow-wrap:anywhere'}, ['网页标题：' + web.title]));}
          var safeWebUrl = typeof web.url === 'string' && /^https?:\/\/[^\s\\/?#@]+(?:[/?#][^\s\\]*)?$/.test(web.url);
          meta.push(safeWebUrl ? h('a', {class:'s-open',href:web.url,target:'_blank',rel:'noopener noreferrer',style:'max-width:100%;overflow-wrap:anywhere'}, ['原网页：' + web.url]) : h('span', {}, ['原网页地址不可用']));
          meta.push(h('span', {}, ['采集时间：' + fmtTime(web.fetched_at)]));
          (Array.isArray(snapshot.related_images) ? snapshot.related_images : []).forEach(function (image) {
            if (Number.isSafeInteger(image.document_id) && image.document_id > 0 && typeof image.filename === 'string') {
              meta.push(h('a', {class:'s-open',href:'/api/documents/' + encodeURIComponent(image.document_id) + '/web-image',target:'_blank',rel:'noopener noreferrer'}, ['正文关联图片：' + image.filename]));
              meta.push(h('span', {}, ['对应关系来自网页相邻位置或明确图注；图片文字和图中关系仍需核对。']));
            }
          });
          meta.push(h('span', {}, [web.status === 'partial' ? '正文提取不完整；图片文字未从网页正文提取。' : '已保存网页正文；图片文字未从网页正文提取。']));
          var webWarnings = {image_text_not_extracted:'网页正文未提取图片文字',embedded_content_not_extracted:'嵌入内容尚未提取',table_spans_not_expanded:'合并单元格未展开，关系需核对'};
          (Array.isArray(web.warnings) ? web.warnings : []).forEach(function (warning) {
            if (typeof warning === 'string') {meta.push(h('span', {style:'max-width:100%;overflow-wrap:anywhere'}, [typeof webWarnings[warning] === 'string' ? webWarnings[warning] : '文档含未核对内容']));}
          });
        }
        if (snapshot.source_kind === 'log' && snapshot.log) {
          meta.push(h('span', {}, ['回答时日志编码：' + (snapshot.log.encoding || '未记录')]));
          meta.push(h('span', {}, ['行号对应回答时的日志原件；当前原件可能已变化。']));
        }
      }
      if (S.me && (S.me.role === 'root' || S.me.role === 'kb_admin')) {
        meta.push(h('a', { class: 's-open', href: sourceUrl(s), target: '_blank', rel: 'noopener noreferrer',
          title: '打开当前文件；可能已替换，不保证与回答时的版本相同' }, [saved && snapshot.source_kind === 'log' ? '打开当前日志原件（保留原编码）' : '打开当前文件']));
      } else if (imageUrl) {
        meta.push(h('a', {class:'s-open',href:imageUrl,target:'_blank',rel:'noopener noreferrer',
          title:'打开原图；访问权限以当前资料状态为准'}, [candidates ? '打开候选原图' : '打开引用原图']));
      }
      var status = s.evidence_status === 'full' && saved
        ? '已保存完整检索片段；不代表整页或完整文档，也不代表答案事实已核实。'
        : s.evidence_status === 'snapshot_unavailable' || s.evidence_status === 'full'
          ? '来源快照不可用；以下仅为保留的摘录，不能确认为回答时的完整来源。'
          : '旧版摘录，可能截断；未补填当前索引内容。';
      var preview = null;
      if (imageUrl) {
        var previewImage = h('img', {class:'s-image', src:imageUrl, alt:(candidates ? '候选图片预览：' : '引用图片预览：') + (name || '原图'), loading:'lazy'});
        var previewError = h('p', {class:'s-image-error', hidden:true}, ['图片暂时无法预览，可能已不可访问或与回答时的版本不同。']);
        previewImage.addEventListener('error', function () { previewImage.hidden = true; previewError.hidden = false; });
        preview = h('div', {class:'s-image-preview'}, [
          h('a', {href:imageUrl, target:'_blank', rel:'noopener noreferrer', title:candidates ? '查看检索到的候选原图' : '查看回答时引用的原图'}, [previewImage]),
          previewError,
          h('p', {class:'s-image-caption'}, [candidates ? '候选图片，未被回答引用 · 点击查看原图' : '引用图片 · 点击查看原图'])
        ]);
      }
      var excerptNode = h('div', { class: 's-excerpt' + (saved && snapshot.source_kind === 'log' ? ' log-text' : ''),
        tabindex: saved && snapshot.source_kind === 'log' ? '0' : null,
        'aria-label': saved && snapshot.source_kind === 'log' ? '日志原文' : null }, [group.excerpts.join('\n\n') || '（未保存原文摘录）']);
      var evidence = saved && snapshot.source_kind === 'image'
        ? h('details', {class:'s-ocr-details'}, [h('summary', {}, ['查看图片说明或识别文字']), excerptNode]) : excerptNode;
      var card = h('div', { class: 'source-card', tabindex: '-1', 'aria-label': candidates ? '候选资料' : '引用来源 ' + group.numbers[0] }, [
        h('div', { class: 'doc-ic', html: icon('doc') }),
        h('div', { class: 's-main' }, [
          h('div', { class: 's-top' }, [
            h('span', { class: 's-name', title: name || '' }, [(candidates ? '候选：' : '[' + group.numbers[0] + '] ') + (name || '未知文档')]),
            h('span', {class:'s-kind'}, [kind])
          ]),
          h('div', { class: 's-meta' }, meta),
          h('p', { class: 's-evidence-status' }, [status]),
          h('p', { class: 's-file-notice' }, ['原件入口指向当前文件，可能与回答时的版本不同；请先对照已保存来源。']),
          preview || '',
          evidence
        ])
      ]);
      if (targets && !candidates) { targets[group.numbers[0]] = card; }
      return card;
    });
    var grid = h('div', { class: 'sources' }, cards);
    wrap.appendChild(grid);
    return wrap;
  }

  function feedbackNode(rec) {
    var current = rec.feedback && rec.feedback.rating;
    var comment = (rec.feedback && rec.feedback.comment) || '';
    var block = h('div', { class: 'feedback-block' });
    var wrap = h('div', { class: 'feedback-row' });
    var note = h('details', { class: 'source-details' });
    var editBtn;
    block.appendChild(wrap);
    block.appendChild(note);
    wrap.appendChild(h('span', { class: 'feedback-label' }, ['这条回答有帮助吗？']));
    var buttons = {};
    function setDisabled(disabled) {
      Object.keys(buttons).forEach(function (key) { buttons[key].disabled = disabled; });
      if (editBtn) { editBtn.disabled = disabled; }
    }
    function renderFeedback() {
      Object.keys(buttons).forEach(function (key) {
        buttons[key].classList.toggle('is-selected', key === current);
      });
      clear(note);
      note.hidden = !comment;
      if (comment) {
        note.appendChild(h('summary', { class: 'sources-title' }, ['用户补充（待核对）']));
        note.appendChild(h('div', { class: 'detail-answer' }, [comment]));
      }
    }
    async function saveFeedback(rating, text) {
      var saved = await api('/api/chats/' + rec.id + '/feedback', {
        method: 'POST', body: { rating: rating, comment: text || null }
      });
      rec.feedback = saved;
      current = saved.rating;
      comment = saved.comment || '';
      renderFeedback();
    }
    ['helpful', 'unhelpful'].forEach(function (rating) {
      var label = rating === 'helpful' ? '有帮助' : '没帮助';
      var btn = h('button', {
        class: 'feedback-btn' + (current === rating ? ' is-selected' : ''),
        type: 'button',
        'aria-label': label
      }, [label]);
      btn.addEventListener('click', async function () {
        if (btn.disabled) { return; }
        setDisabled(true);
        try {
          // 评价与补充说明共用一条记录；只改评价时保留已经保存的纠错依据。
          await saveFeedback(rating, comment);
          toast('反馈已保存', 'success');
        } catch (e) {
          toast('反馈保存失败：' + (e.message || '请稍后重试'), 'error');
        } finally { setDisabled(false); }
      });
      buttons[rating] = btn;
      wrap.appendChild(btn);
    });
    editBtn = h('button', { class: 'feedback-btn', type: 'button' }, ['纠错 / 补充依据']);
    editBtn.addEventListener('click', function () {
      if (editBtn.disabled) { return; }
      formModal({
        title: '纠错 / 补充依据（待核对）',
        submitText: '保存补充',
        fields: [
          { name: 'rating', label: '这条回答的评价', type: 'select', value: current || 'unhelpful',
            options: [{ value: 'unhelpful', label: '没帮助' }, { value: 'helpful', label: '有帮助' }] },
          { name: 'comment', label: '问题、正确原文与来源（最多 1000 字，仅作为待核对记录）', type: 'textarea', rows: 8,
            value: comment, placeholder: '错误原因：\n正确原文：\n来源文件、页码或段落：' }
        ],
        onSubmit: async function (values) {
          var text = values.comment.trim();
          // 与 API 的 Unicode 字符长度口径一致；超长时让弹层保留输入供修改。
          if (Array.from(text).length > 1000) { throw new Error('补充说明最多 1000 字，请缩短后再保存。'); }
          if (!text && !comment) { throw new Error('请填写错误原因或补充依据。'); }
          setDisabled(true);
          try {
            await saveFeedback(values.rating, text);
            toast(text ? '补充已保存，待核对；原回答保持不变' : '补充说明已清空', 'success');
          } finally { setDisabled(false); }
        }
      });
    });
    wrap.appendChild(editBtn);
    if (canFollowup(rec)) {
      var followup = h('button', { class: 'feedback-btn', type: 'button', 'data-followup': String(rec.id) }, ['补充这题']);
      followup.disabled = S.busy || S.composing;
      followup.addEventListener('click', function () { chooseFollowup(rec); });
      wrap.appendChild(followup);
    }
    renderFeedback();
    return block;
  }

  function canFollowup(rec) {
    return S.me && S.currentId !== null && S.conversationUserId === S.me.id
      && rec.user_id === S.me.id && rec.status === 'ok' && !rec.redacted;
  }

  function clearFollowup() {
    S.followupTo = null;
    S.followupText = '';
    renderFollowupNotice();
  }

  function renderFollowupNotice() {
    if (!els.followupNotice) { return; }
    clear(els.followupNotice);
    els.followupNotice.classList.toggle('hidden', S.followupTo === null);
    if (S.followupTo === null) { return; }
    els.followupNotice.appendChild(h('span', {}, ['正在补充这题（检索范围不变）：']));
    var target = h('details', { class: 'source-details' }, [
      h('summary', {}, ['关联问题：' + excerpt(S.followupText, 80)]),
      h('div', { class: 'followup-text' }, [S.followupText])
    ]);
    els.followupNotice.appendChild(target);
    var cancel = h('button', { class: 'feedback-btn', type: 'button' }, ['取消补充']);
    cancel.disabled = S.busy || S.composing;
    cancel.addEventListener('click', function () {
      if (!S.busy && !S.composing) { clearFollowup(); }
    });
    els.followupNotice.appendChild(cancel);
  }

  function chooseFollowup(rec) {
    if (S.busy || S.composing || S.detailState !== 'ready' || !canFollowup(rec)) { return; }
    if (S.followupTo !== null && S.followupTo !== rec.id && els.input.value.trim()) {
      toast('请先发送或清空当前草稿，再选择要补充的问题。草稿已保留。', 'warn');
      return;
    }
    S.followupTo = rec.id;
    S.followupText = rec.followup && rec.followup.effective_question || rec.question;
    renderFollowupNotice();
    els.input.focus();
  }

  /**
   * 将一轮问答追加到消息区。
   */
  function appendRecord(rec) {
    if (!rec) { return; }
    els.messages.appendChild(askBubble(rec.question));
    if (!rec.redacted && rec.followup && rec.followup.effective_question) {
      els.messages.appendChild(h('details', { class: 'source-details' }, [
        h('summary', { class: 'sources-title' }, ['本次为补充提问 · 查看完整关联问题']),
        h('div', { class: 'detail-answer' }, [rec.followup.effective_question])
      ]));
    }
    if (rec.status === 'error') {
      var why = rec.error || '未知错误';
      els.messages.appendChild(errorBubble('回答失败：' + why + '（记录 #' + rec.id + '）'));
    } else {
      var targets = {};
      var sourcePanel = sourcesNode(rec.answer, rec.sources, targets);
      els.messages.appendChild(answerBubble(rec.answer, rec.sources, function (number) {
        var target = targets[number];
        if (!target) { return; }
        sourcePanel.open = true;
        target.focus({ preventScroll: true });
        target.scrollIntoView({ block: 'nearest', behavior: 'instant' });
      }));
      if (rec.status === 'ok' && !rec.redacted && (rec.sources || []).length && !citedSources(rec.answer, rec.sources).length) {
        els.messages.appendChild(h('p', {class:'msg-a no-sources', role:'status'}, ['回答没有标注引用，请查看下方候选资料并核对原文。']));
      }
      if (rec.status === 'ok' && !rec.redacted) {
        var answerImages = answerImagesNode(rec.answer, rec.sources);
        if (answerImages) { els.messages.appendChild(answerImages); }
      }
      els.messages.appendChild(feedbackNode(rec));
      els.messages.appendChild(sourcePanel);
      if (rec.status === 'ok' && !rec.redacted && citedSources(rec.answer, rec.sources).length) {
        var candidates = uncitedDocuments(rec.answer, rec.sources);
        if (candidates.length) { els.messages.appendChild(sourcesNode('', candidates, null, true)); }
      }
    }
  }

  function renderConversation(conversation) {
    if (!conversation) { return; }
    S.detailState = 'ready';
    S.conversationUserId = conversation.user_id;
    S.followupTurns = conversation.turns || [];
    if (S.followupTo !== null && !S.followupTurns.some(function (rec) {
      return rec.id === S.followupTo && canFollowup(rec);
    })) { clearFollowup(); }
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
    if (els.scroll) { els.scroll.scrollTop = 0; }
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
    return (doc.filename || ('文档 ' + doc.id)) + (doc.version ? '（' + doc.version + '）' : '')
      + (doc.device ? ' · ' + doc.device : '') + (doc.applicability ? ' · ' + doc.applicability : '');
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
      return ((doc.filename || '') + ' ' + (doc.version || '') + ' ' + (doc.device || '') + ' ' + (doc.applicability || '')).toLowerCase().indexOf(needle) !== -1;
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
    var buttons = qsa('[data-device]');
    var matchingButtons = [];
    buttons.forEach(function (btn) {
      var matching = deviceDocumentIds(S.documents, btn.dataset.device);
      var whole = matching.length > 0 && matching.length === chosen.length
        && matching.every(function (id) { return chosen.indexOf(id) !== -1; });
      if (whole) { matchingButtons.push(btn); }
      btn.disabled = lockedScope() || S.busy || !S.documentsLoaded || !matching.length;
      btn.title = matching.length ? '限定 ' + matching.length + ' 个该设备或通用的文档' : '暂无该设备或通用的文档';
    });
    if (matchingButtons.length === 1) { selectedDevice = matchingButtons[0].dataset.device; }
    buttons.forEach(function (btn) { btn.setAttribute('aria-pressed', String(btn.dataset.device === selectedDevice)); });
    if (els.deviceScopeHint) {
      els.deviceScopeHint.textContent = !S.documentsLoaded
        ? (S.documentsLoading ? '正在加载设备文档…' : '设备文档未加载成功，可手动选择文档或刷新文档列表。')
        : selectedDevice ? '已选择 ' + selectedDevice + '，限定 ' + chosen.length + ' 个文档（含通用资料）。'
        : matchingButtons.length > 1 ? '所选通用资料适用于多个设备。'
        : activeMode() === 'selected' ? '已手选文档；点设备按钮会替换下方手选范围。'
        : '未选择设备时为全库检索；点设备按钮会切换为「仅选中文档」并替换手选范围。';
      if (S.documentsLoaded) {
        var unassigned = S.documents.filter(function (doc) { return !doc.device; }).length;
        if (unassigned) { els.deviceScopeHint.textContent += ' 有 ' + unassigned + ' 份资料未指定设备，不包含在设备范围内；可手动选择或在资料管理中标记。'; }
      }
    }
  }

  function updateScopeActionLabels() {
    var docs = filteredDocs();
    if (els.documentScopeSelectAll) {
      els.documentScopeSelectAll.textContent = S.docFilter ? '全选结果' : '全选';
      els.documentScopeSelectAll.title = '添加当前显示的 ' + docs.length + ' 个文档';
      els.documentScopeSelectAll.disabled = lockedScope() || S.busy || !S.documentsLoaded || !docs.length;
    }
    if (els.documentScopeClear) {
      els.documentScopeClear.textContent = '清空';
      els.documentScopeClear.title = '移除全部 ' + S.newDocumentIds.length + ' 个已选文档';
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
      var box = h('input', { type: 'checkbox', value: String(doc.id), 'data-scope-focus': 'check:' + doc.id });
      box.checked = S.newDocumentIds.indexOf(doc.id) !== -1;
      // 生成中或会话已固定时，勾选本身也不可修改（旧版是禁用的，不能退化）。
      box.disabled = S.busy;
      var only = h('button', {
        class: 'doc-only', type: 'button', title: '仅选此文档', 'aria-label': '仅选择 ' + label,
        'data-scope-focus': 'only:' + doc.id
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
      var name = h('span', { class: 'doc-name' }, [doc.filename || ('文档 ' + doc.id)]);
      if (doc.device || doc.applicability) {
        name.appendChild(h('span', { class: 'doc-meta' }, [
          [doc.device ? '设备 ' + doc.device : '', doc.applicability || ''].filter(Boolean).join(' · ')
        ]));
      }
      els.documentScopeOptions.appendChild(h('label', { class: 'document-scope-option', title: label }, [box, name, only]));
    });
  }

  function renderScope() {
    if (!els.documentScope) { return; }
    var active = document.activeElement;
    var focusKey = active && active.getAttribute ? active.getAttribute('data-scope-focus') : null;
    var scrollNodes = [els.libraryPanel, els.libraryContent, els.documentScopeChips].filter(Boolean);
    var scrollPositions = scrollNodes.map(function (node) { return node.scrollTop || 0; });
    var locked = lockedScope();
    var detailPending = locked && (S.detailState === 'loading' || S.detailState === 'error');
    var mode = activeMode();
    var ids = activeIds();
    var selected = mode === 'selected';
    var names = scopeNames(ids);
    var missing = scopeMissingIds(ids);
    var visibleIds = new Set(filteredDocs().map(function (doc) { return doc.id; }));
    var outsideIds = ids.filter(function (id) { return !visibleIds.has(id); });
    // 服务端回显 selected + 空 ID：首问选定的文档已删除或不再可见。
    var unavailable = locked && selected && ids.length === 0;

    if (els.scopeIcon) { els.scopeIcon.innerHTML = icon('layers'); }
    if (els.documentScopeValue) {
      if (detailPending) {
        els.documentScopeValue.textContent = S.detailState === 'loading' ? '正在加载对话范围…' : '对话未加载，请重试';
      } else if (unavailable) {
        els.documentScopeValue.textContent = '仅选中文档 · 原文档不可用（已固定）';
      } else {
        els.documentScopeValue.textContent = selected
          ? '仅选中文档 · ' + ids.length + ' 个' + (locked ? '（已固定）' : '')
          : '全库检索' + (locked ? '（已固定）' : '');
      }
      els.documentScopeValue.className = 'scope-badge ' + (selected ? 'is-selected' : 'is-all');
    }
    if (els.composerScope && els.documentScopeValue) {
      els.composerScope.textContent = els.documentScopeValue.textContent;
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
      if (detailPending) {
        els.documentScopeHint.textContent = '对话详情就绪后才能确认原检索范围并继续提问，不会沿用上一段对话的范围。';
      } else if (S.busy && !locked) {
        els.documentScopeHint.textContent =
          '正在生成回答，本次检索范围已固定、暂时不能修改；回答结束后才能继续调整。';
      } else if (unavailable) {
        els.documentScopeHint.textContent =
          '本对话原为「仅选中文档」，但首问选定的文档已删除或不再可见；请新建对话并重新选择。';
      } else if (locked) {
        els.documentScopeHint.textContent = selected
          ? '仅检索首问选定的资料。更换范围请新建对话。'
          : '检索全部可见资料。更换范围请新建对话。';
      } else if (selected) {
        els.documentScopeHint.textContent = ids.length
          ? '仅检索勾选的资料，首问后固定范围。'
          : '请至少勾选一份资料，再开始提问。';
      } else {
        els.documentScopeHint.textContent = ids.length
          ? '全库检索：当前在全部可见文档中检索（已选的 ' + ids.length + ' 个文档暂不生效，切回「仅选中文档」可继续使用）。'
          : '检索全部可见资料，首问后固定范围。';
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
      els.documentScopeLockText.textContent = detailPending
        ? (S.detailState === 'loading' ? '正在加载本对话，请稍候。' : '加载失败，请重试或新建对话。') : lockText;
      els.documentScopeLockText.classList.toggle('is-missing', unavailable || missing.length > 0);
    }
    if (els.documentScopeSelected) {
      if (locked) {
        els.documentScopeSelected.textContent = '';
      } else if (selected) {
        els.documentScopeSelected.textContent = ids.length
          ? '已选 ' + ids.length + ' 个文档'
            + (S.docFilter && outsideIds.length ? ' · 筛选外 ' + outsideIds.length + ' 个' : '')
            + (names.length < ids.length ? '（其中 ' + (ids.length - names.length) + ' 个不在当前列表）' : '')
          : '尚未选择文档';
      } else {
        els.documentScopeSelected.textContent = '未限定文档（全库检索）';
      }
    }
    if (els.documentScopeChips) {
      clear(els.documentScopeChips);
      if (!locked && selected) {
        outsideIds.forEach(function (id) {
          var doc = findDocument(id);
          var label = doc ? docLabel(doc) : '文档 ID ' + id;
          var x = h('button', {
            class: 'scope-chip-x', type: 'button', title: '取消选择',
            'aria-label': '取消选择 ' + label, 'data-scope-focus': 'remove:' + id
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
    if (els.finishSelectionBtn) {
      els.finishSelectionBtn.textContent = locked ? '返回对话'
        : selected ? '完成选择（' + ids.length + '）· 返回提问' : '全库检索 · 返回提问';
    }
    if (focusKey) {
      var controls = qsa('[data-scope-focus]');
      var target = controls.find(function (node) { return node.getAttribute('data-scope-focus') === focusKey; });
      if (!target && focusKey.indexOf('remove:') === 0) {
        target = controls.find(function (node) { return node.getAttribute('data-scope-focus') === focusKey.replace('remove:', 'check:'); });
        if (!target) { target = els.documentScopeSearch; }
      }
      if (target && !target.disabled) { target.focus({ preventScroll: true }); }
    }
    scrollNodes.forEach(function (node, index) { node.scrollTop = scrollPositions[index]; });
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

    els.historyFoot.textContent = '共 ' + (total != null ? total : items.length) + ' 条对话';
  }

  function selectChat(id) {
    if (S.busy || S.composing) { toast('正在生成或输入，请稍候再切换', 'warn'); return; }
    if (id === S.currentId && (S.detailState === 'loading' || S.detailState === 'ready')
        && !els.messages.classList.contains('hidden')) { return; }
    clearFollowup();
    S.currentId = id;
    markActive(id);
    loadConversationDetail(id);
    if (compactLayout() && els.historyPanel) { els.historyPanel.open = false; }
  }

  function updateDeleteButton() {
    els.deleteChatBtn.disabled = S.busy || S.currentId === null || S.detailState === 'loading';
    els.sendBtn.disabled = S.busy || S.detailState === 'loading' || S.detailState === 'error';
    qsa('[data-followup]').forEach(function (button) { button.disabled = S.busy || S.composing; });
    renderFollowupNotice();
  }

  async function deleteSelectedChat() {
    if (S.busy || S.composing || S.currentId === null) { return; }
    var chatId = S.currentId;
    if (!window.confirm('确定删除当前选中的对话？其中全部问答将被删除且无法恢复。')) { return; }
    els.deleteChatBtn.disabled = true;
    try {
      await api('/api/conversations/' + chatId, { method: 'DELETE' });
      clearFollowup();
      S.currentId = null;
      S.detailRequest += 1;
      S.detailState = 'idle';
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
    var request = ++S.detailRequest;
    S.detailState = 'loading';
    updateDeleteButton();
    renderScope();
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
      if (S.currentId !== id || request !== S.detailRequest) { return; }
      renderConversation(conversation);
    } catch (e) {
      if (S.currentId !== id || request !== S.detailRequest) { return; }
      S.detailState = 'error';
      if (e && e.status === 401) { return; }
      clear(els.messages);
      els.messages.appendChild(errorBubble('加载对话详情失败：' + (e && e.message ? e.message : '未知错误')));
      var retry = h('button', { class: 'btn btn-outline', type: 'button' }, ['重试加载对话']);
      retry.addEventListener('click', function () { selectChat(id); });
      els.messages.appendChild(retry);
    } finally {
      if (S.currentId === id && request === S.detailRequest) {
        updateDeleteButton();
        renderScope();
      }
    }
  }

  function updateHistoryPager(loading) {
    var pages = Math.ceil(S.historyTotal / 25);
    clear(els.historyPages);
    els.historyPageStatus.textContent = pages ? '每页 25 条 · 共 ' + pages + ' 页' : '';
    if (!pages) { return; }
    var choices = pages <= 5 ? Array.from({length: pages}, function (_, i) { return i; })
      : [0, S.historyPage - 1, S.historyPage, S.historyPage + 1, pages - 1]
        .filter(function (page, i, list) { return page >= 0 && page < pages && list.indexOf(page) === i; })
        .sort(function (a, b) { return a - b; });
    choices.forEach(function (page, index) {
      if (index && page - choices[index - 1] > 1) { els.historyPages.appendChild(h('span', {class:'history-page-gap', 'aria-hidden':'true'}, ['…'])); }
      var btn = h('button', {class:'btn btn-outline btn-sm history-page', type:'button', 'aria-label':'第 ' + (page + 1) + ' 页', 'aria-current':page === S.historyPage ? 'page' : null}, [String(page + 1)]);
      btn.disabled = loading;
      btn.addEventListener('click', function () {
        if (page === S.historyPage) { return; }
        loadHistory(page).then(function () {
          var current = qs('[aria-current="page"]', els.historyPages);
          if (current) { current.focus({preventScroll:true}); }
        });
      });
      els.historyPages.appendChild(btn);
    });
  }

  async function loadHistory(page, silent) {
    var requestedPage = Math.max(0, Number.isInteger(page) ? page : S.historyPage);
    var request = ++S.historyRequest;
    updateHistoryPager(true);
    if (!silent) { els.historyLoading.classList.remove('hidden'); }
    try {
      var data = await api('/api/conversations?limit=25&offset=' + requestedPage * 25);
      if (request !== S.historyRequest) { return null; }
      var lastPage = Math.max(0, Math.ceil(data.total / 25) - 1);
      if (requestedPage > lastPage) { return loadHistory(lastPage, silent); }
      S.historyPage = requestedPage; S.historyTotal = data.total;
      S.historyItems = data.items || [];
      renderHistoryList(S.historyItems, data.total);
      markActive(S.currentId);
      return data;
    } catch (e) {
      if (request === S.historyRequest && !silent && !(e && e.status === 401)) {
        toast('加载历史记录失败：' + (e && e.message ? e.message : '未知错误'), 'error');
      }
      return null;
    } finally {
      if (request === S.historyRequest) {
        els.historyLoading.classList.add('hidden');
        els.historyList.classList.remove('hidden');
        updateHistoryPager(false);
      }
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
    if (S.busy || S.composing) { return; }
    if (S.detailState === 'loading' || S.detailState === 'error') {
      toast('请先等待对话加载完成，或重试加载/新建对话。草稿已保留。', 'warn');
      return;
    }
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
    var followupTo = S.followupTo;
    var submitted = false;
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
        if (followupTo !== null) { body.followup_to = followupTo; }
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
      submitted = true;
      clearFollowup();
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
      if (!submitted && followupTo !== null && !els.input.value) {
        els.input.value = q;
        autoSize();
      }
      S.abortController = null;
      setGenerating(false);
      busy(els.sendBtn, false);
      S.busy = false;
      updateDeleteButton();
      renderScope();
      if (!compactLayout()) { els.input.focus(); }
    }
  }

  /** 只重拉历史列表（不自动选中，保持当前视图） */
  async function loadHistoryOnly() {
    await loadHistory(S.historyPage, true);
  }

  function autoSize() {
    els.input.style.height = 'auto';
    els.input.style.height = Math.min(els.input.scrollHeight, 180) + 'px';
    if (els.questionCount) { els.questionCount.textContent = els.input.value.length + ' / 2000'; }
  }

  /* ---------- 事件绑定 ---------- */

  function startNewChat() {
    if (S.busy || S.composing) { toast('正在生成或输入，请稍候', 'warn'); return; }
    clearFollowup();
    S.followupTurns = [];
    S.conversationUserId = null;
    S.currentId = null;
    S.detailRequest += 1;
    S.detailState = 'idle';
    resetScopeDraft();
    S.currentDocumentIds = [];
    S.currentScopeMode = 'all';
    renderScope();
    updateDeleteButton();
    showGreeting();
    els.input.value = '';
    autoSize();
    if (!compactLayout()) { els.input.focus(); }
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

  function compactLayout() {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(max-width: 1000px)').matches;
  }

  function openLibrary() {
    if (!els.libraryPanel) { return; }
    els.libraryPanel.open = true;
    if (compactLayout()) { els.libraryPanel.scrollIntoView({ block: 'start' }); }
    els.libraryPanel.focus({ preventScroll: true });
  }

  function finishSelection() {
    var reason = scopeBlockReason();
    if (reason) {
      S.scopeNotice = reason;
      renderScope();
      toast(reason, 'warn');
      return;
    }
    if (compactLayout()) { els.libraryPanel.open = false; }
    els.input.focus();
    els.input.scrollIntoView({ block: 'center' });
  }

  function bindEvents() {
    if (compactLayout()) {
      if (els.historyPanel) { els.historyPanel.open = false; }
      if (els.libraryPanel) { els.libraryPanel.open = false; }
    }
    if (els.openSourcesBtn) { els.openSourcesBtn.addEventListener('click', openLibrary); }
    if (els.quickNewChatBtn) { els.quickNewChatBtn.addEventListener('click', startNewChat); }
    if (els.finishSelectionBtn) { els.finishSelectionBtn.addEventListener('click', finishSelection); }
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
        openLibrary();
        if (!compactLayout()) { els.input.focus(); }
      });
    });
    els.input.addEventListener('compositionstart', function () { S.composing = true; updateDeleteButton(); });
    els.input.addEventListener('compositionend', function () {
      setTimeout(function () { S.composing = false; updateDeleteButton(); }, 0);
    });
    els.input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' && ev.ctrlKey && !ev.shiftKey && !ev.metaKey && !ev.altKey) {
        if (ev.isComposing || S.composing) { return; }
        ev.preventDefault();
        if (els.input.value.length - (els.input.selectionEnd - els.input.selectionStart) >= els.input.maxLength) { return; }
        els.input.setRangeText('\n', els.input.selectionStart, els.input.selectionEnd, 'end');
        autoSize();
        return;
      }
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
    clearFollowup();
    wireGlobalErrors();
    bindEvents();
    startNewChat();

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
  // Safari 后退缓存不重新触发 DOMContentLoaded，也应回到新对话。
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('pageshow', function (event) {
      if (event.persisted && !S.busy) { startNewChat(); }
    });
  }
})();
