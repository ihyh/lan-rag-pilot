/* 找内容：直接检索已入库正文并定位到文件/页码/片段，不调用生成模型。 */
'use strict';
(function () {
  var S = {docs: [], docId: null, loaded: false, inspection: 0, search: 0, listing: 0, checkPdf: false, pdf: null, searchMode: true};
  var els = {};
  var LIMIT = 10;
  var labels = {ready: '已就绪', failed: '处理失败', parsing: '处理中'};

  function message(target, text, error) {
    clear(target);
    target.appendChild(h('p', {class: error ? 'quality-warning' : 'quality-empty'}, [text]));
  }
  function doc() { return S.docs.find(function (d) { return d.id === S.docId; }); }
  function mode(search) {
    S.searchMode = Boolean(search);
    els.qualityInspect.classList.toggle('hidden', search);
    els.qualitySearch.classList.toggle('hidden', !search);
    [[els.qualitySearchTab, search], [els.qualityInspectTab, !search]].forEach(function (entry) {
      entry[0].classList.toggle('btn-primary', entry[1]);
      entry[0].classList.toggle('btn-outline', !entry[1]);
      entry[0].setAttribute('aria-pressed', String(entry[1]));
    });
  }
  function resetSearch(text) {
    S.search++;
    els.qualitySearchBtn.disabled = false;
    clear(els.qualityResults); clear(els.qualitySearchPager);
    if (els.qualityGroupSummary) { clear(els.qualityGroupSummary); }
    els.qualitySearchStatus.textContent = text || '输入关键词开始检索。';
  }
  function renderGroupSummary(target, groups) {
    if (!target) { return; }
    clear(target);
    (groups || []).forEach(function (group) {
      var button = h('button', {type: 'button',
        style: 'display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);border-radius:999px;padding:4px 10px;background:transparent;color:inherit;font-size:14px;cursor:pointer',
        title: '只看这份资料'}, [h('span', {}, [group.filename]), h('strong', {}, [String(group.hits) + ' 处'])]);
      button.addEventListener('click', function () { selectDoc(group.document_id); search(0); });
      target.appendChild(button);
    });
  }
  function scopeLabel() {
    var current = doc();
    els.qualityScope.textContent = S.docId === null ? '原文检索范围：全部已就绪资料（默认仅启用版本；历史资料需勾选）。'
      : current ? '当前资料：' + current.filename
      : '所选资料不可用，请重新选择；不会自动切换到全库。';
  }
  function renderDocs() {
    var focusedId = document.activeElement && document.activeElement.getAttribute('data-quality-doc');
    clear(els.qualityDocs);
    var keyword = els.qualityDocFilter.value.trim().toLowerCase();
    var docs = S.docs.filter(function (d) {
      return (d.filename + ' ' + (d.version || '')).toLowerCase().indexOf(keyword) !== -1;
    });
    els.qualityDocCount.textContent = keyword ? docs.length + ' / ' + S.docs.length + ' 份资料' : S.docs.length + ' 份资料';
    if (!docs.length) { message(els.qualityDocs, S.docs.length ? '没有匹配的文件名。' : '还没有资料，请先在资料管理中上传。'); }
    docs.forEach(function (d) {
      var meta = [labels[d.status] || '未知状态',
        d.lifecycle === 'archived' ? '历史版本' : '', d.applicability || ''].filter(Boolean).join(' · ');
      var button = h('button', {type: 'button', class: 'quality-doc' + (d.id === S.docId ? ' is-active' : ''), 'aria-pressed': String(d.id === S.docId), 'data-quality-doc': String(d.id)}, [
        h('span', {class: 'quality-doc-name'}, [d.filename]),
        h('span', {class: 'quality-doc-meta'}, [meta])
      ]);
      button.addEventListener('click', function () { selectDoc(d.id); });
      els.qualityDocs.appendChild(button);
      if (focusedId === String(d.id)) { button.focus({preventScroll: true}); }
    });
  }
  async function loadDocs() {
    var ticket = ++S.listing;
    S.loaded = false;
    message(els.qualityDocs, '正在加载资料…');
    els.qualityDocCount.textContent = '';
    if (S.docId !== null) {
      clear(els.qualityChunks); clear(els.qualityInspectPager);
      message(els.qualitySummary, '正在刷新所选资料…');
    }
    els.qualityReload.disabled = true;
    try {
      var data = await api('/api/admin/documents');
      if (ticket !== S.listing) { return; }
      S.docs = data.items || []; S.loaded = true;
      renderDocs(); scopeLabel();
      if (S.docId !== null) { await inspect(0); }
    } catch (e) {
      if (ticket === S.listing) {
        message(els.qualityDocs, '资料加载失败：' + e.message + '。请点刷新重试。', true);
        if (S.docId !== null) { message(els.qualitySummary, '无法确认所选资料的最新状态，请刷新重试。', true); }
      }
    } finally { if (ticket === S.listing) { els.qualityReload.disabled = false; } }
  }
  function selectDoc(id) {
    S.docId = id; S.checkPdf = false; S.pdf = null; S.inspection++;
    resetSearch('范围已更新，点击“查找原文”重新检索。');
    scopeLabel(); renderDocs();
    if (id === null) { mode(true); return; }
    if (S.searchMode) { return; }  // 找内容模式下只切换检索范围，不离开检索框
    clear(els.qualityChunks); clear(els.qualityInspectPager);
    mode(false); inspect(0);
  }
  function pageList(pages) {
    return pages.slice(0, 60).join('、') + (pages.length > 60 ? '…（共 ' + pages.length + ' 页）' : '');
  }
  async function checkAll() {
    var target = els.qualityCheckAllResults;
    els.qualityCheckAll.disabled = true;
    message(target, '正在检查资料的入库状态…');
    try {
      var result = await api('/api/library/inspection-summary');
      clear(target);
      target.appendChild(h('p', {class: 'small muted'}, ['检查了 ' + result.total + ' 份资料，发现 ' + result.items.length + ' 份有状态或索引提示。此检查不判断原文是否读全或回答是否正确。']));
      result.items.forEach(function (item) {
        var button = h('button', {type:'button', class:'btn btn-outline btn-sm'},
          [item.filename + (item.lifecycle === 'archived' ? '（历史）' : '') + '：' + item.reasons.join('；')]);
        button.addEventListener('click', function () {
          if (!S.loaded || !S.docs.some(function (d) { return d.id === item.id; })) {
            message(target, '资料列表已变化，请刷新后重新检查。', true); return;
          }
          mode(false); selectDoc(item.id);
        });
        target.appendChild(button);
      });
    } catch (e) { message(target, '检查失败：' + (e.message || '请稍后重试'), true); }
    finally { els.qualityCheckAll.disabled = false; }
  }
  function renderSummary(data) {
    clear(els.qualitySummary);
    var d = data.document, q = data.quality;
    els.qualitySummary.appendChild(h('h2', {class: 'quality-summary-title'}, [d.filename]));
    var isWeb = d.source_metadata && d.source_metadata.kind === 'web';
    var isLog = d.source_metadata && d.source_metadata.kind === 'log';
    if (typeof window !== 'undefined' && window.WebImportUI) {
      var webNotes = window.WebImportUI.notes(d.source_metadata);
      if (webNotes) {els.qualitySummary.appendChild(webNotes);}
    }
    if (typeof window !== 'undefined' && window.LogImportUI) {
      var logNotes = window.LogImportUI.notes(d.source_metadata);
      if (logNotes) {els.qualitySummary.appendChild(logNotes);}
    }
    if (d.applicability) { els.qualitySummary.appendChild(h('p', {class: 'quality-scope'}, ['适用范围：' + d.applicability])); }
    if (d.lifecycle === 'archived') {
      els.qualitySummary.appendChild(h('p', {class: 'quality-warning'}, ['这份资料已设为历史版本，普通问答不会检索。原件与旧引用保留。']));
      if (d.replacement_document_id) { els.qualitySummary.appendChild(h('a', {href: '/library?document_id=' + encodeURIComponent(d.replacement_document_id)}, ['查看替代资料 #' + d.replacement_document_id])); }
    }
    els.qualitySummary.appendChild(h('div', {class: 'quality-facts'}, [
      h('span', {}, ['状态：' + (labels[d.status] || d.status)]),
      h('span', {}, ['实际入库：' + q.chunk_count + ' 个片段']),
      h('span', {}, ['正文字符数：' + q.text_chars + '（含重叠）']),
      h('span', {}, [d.direct_image_description ? '图片关联说明不使用页码' : d.source_type === 'image' ? '图片内容不使用页码'
        : isLog ? '日志按原件行号定位，不使用页码' : isWeb ? '网页按提取块定位，不使用页码'
        : /\.pdf$/i.test(d.filename) ? '有索引文字的页：' + q.indexed_pages.length : '有段落位置：' + q.paragraph_count])
    ]));
    els.qualitySummary.appendChild(h('p', {class: 'small muted'}, [d.direct_image_description
      ? '下方是用于检索的人工关联说明，请对照原图核对；未写入的图片内容不会自动参与检索。'
      : '下方是检索实际使用的正文片段。片段数和“就绪”不代表原件已完整读取；表格请对照原文件核对行列。']));
    if (!isWeb && !isLog && q.indexed_pages.length) {
      els.qualitySummary.appendChild(h('p', {class: 'small muted'}, ['索引页码：' + pageList(q.indexed_pages)]));
    }
    (q.warnings || []).forEach(function (warning) {
      els.qualitySummary.appendChild(h('p', {class: 'quality-warning'}, [warning]));
    });
    ['suspect', 'failed'].forEach(function (status) {
      var pages = q.ocr && q.ocr[status];
      if (pages && pages.length) { els.qualitySummary.appendChild(h('p', {class: 'quality-warning'}, [
        (status === 'suspect' ? 'OCR 可疑页：' : 'OCR 失败页：') + pageList(pages)
      ])); }
    });
    if (d.error_message) { els.qualitySummary.appendChild(h('p', {class: 'quality-warning'}, ['处理记录：' + d.error_message])); }
    if (/\.pdf$/i.test(d.filename)) {
      var pdf = q.pdf;
      if (pdf.status === 'ok') {
        els.qualitySummary.appendChild(h('p', {class: 'quality-facts'}, ['原文件共 ' + pdf.page_count + ' 页（本次核对）。']));
        els.qualitySummary.appendChild(h('p', {class: pdf.pages_without_indexed_text.length ? 'quality-warning' : 'small muted'}, [
          pdf.pages_without_indexed_text.length ? '以下页未见索引文字：' + pageList(pdf.pages_without_indexed_text) + '。可能为空白、图片或漏读，请查看原件核对。'
            : '每页均有索引文字；仍需核对文字和表格是否完整、准确。'
        ]));
      } else { els.qualitySummary.appendChild(h('p', {class: 'quality-warning'}, [pdf.note || '尚未核对原件总页数，不能据此判断有无漏页。'])); }
      var verify = h('button', {class: 'btn btn-outline btn-sm', type: 'button'}, ['核对 PDF 原件页数']);
      verify.addEventListener('click', function () { S.checkPdf = true; inspect(0); });
      els.qualitySummary.appendChild(verify);
    }
  }
  function originalUrl(item) {
    return '/api/documents/' + encodeURIComponent(item.document_id) + '/file' + (item.page && !(item.source_metadata && item.source_metadata.kind === 'log') ? '#page=' + encodeURIComponent(item.page) : '');
  }
  function textWithHighlight(content, query, isLog) {
    var block = h('pre', {class: 'quality-text' + (isLog ? ' log-text' : ''),
      tabindex: isLog ? '0' : null, 'aria-label': isLog ? '日志原文' : null});
    var text = String(content || ''), needle = query || '';
    // Exact matching preserves source characters; no HTML from documents is executed.
    var folded = text.replace(/[A-Z]/g, function (char) {return char.toLowerCase();});
    var foldedNeedle = needle.replace(/[A-Z]/g, function (char) {return char.toLowerCase();});
    var cursor = 0, position = needle ? folded.indexOf(foldedNeedle) : -1;
    while (position !== -1) {
      block.appendChild(document.createTextNode(text.slice(cursor, position)));
      block.appendChild(h('mark', {}, [text.slice(position, position + needle.length)]));
      cursor = position + needle.length;
      position = folded.indexOf(foldedNeedle, cursor);
    }
    block.appendChild(document.createTextNode(text.slice(cursor)));
    return block;
  }
  function renderChunks(target, items, query, groups) {
    var groupHits = {};
    (groups || []).forEach(function (group) { groupHits[group.document_id] = group.hits; });
    var typeLabels = {file: '文件', web: '网页文章', image: '图片', log: '日志'};
    clear(target);
    var lastDoc = null;
    items.forEach(function (item) {
      if (groups && item.document_id !== lastDoc) {
        lastDoc = item.document_id;
        var suffix = groupHits[item.document_id] ? ' · 命中 ' + groupHits[item.document_id] + ' 处' : '';
        target.appendChild(h('p', {class: 'quality-file-heading', style: 'margin:18px 0 8px;font-weight:600'}, [
          item.filename + ' · ' + (typeLabels[item.source_type] || '文件') + (item.device ? ' · ' + item.device : '') + suffix
        ]));
      }
      var isWeb = item.source_metadata && item.source_metadata.kind === 'web';
      var isLog = item.source_metadata && item.source_metadata.kind === 'log';
      var lineRange = Number.isSafeInteger(item.line_start) && item.line_start > 0 && Number.isSafeInteger(item.line_end) && item.line_end >= item.line_start
        ? '日志第 ' + item.line_start + (item.line_end === item.line_start ? '' : '–' + item.line_end) + ' 行' : '日志行号未记录';
      var where = item.source_type === 'image' && !item.page && !item.paragraph ? '图片内容（无页码）' : isLog ? lineRange : isWeb ? (item.paragraph ? '网页提取块 ' + item.paragraph + '（非页码）' : '网页提取块位置未记录（非页码）')
        : item.page ? '第 ' + item.page + ' 页' : item.paragraph ? '片段起始：第 ' + item.paragraph + ' 段' : '未记录位置';
      var header = h('div', {class: 'quality-chunk-head'}, [h('span', {}, [item.filename + ' · ' + where + ' · 片段 ' + item.chunk_index
        + (item.lifecycle === 'archived' ? ' · 历史版本（问答停用）' : '') + (item.applicability ? ' · ' + item.applicability : '')])]);
      if (item.can_open_file) { header.appendChild(h('a', {href: originalUrl(item), target: '_blank', rel: 'noopener noreferrer'}, [isLog ? '打开日志原件（保留原编码） ↗' : '打开原文件 ↗'])); }
      var body = [header, textWithHighlight(item.content, query, isLog)];
      if (item.source_type === 'image' && item.can_open_file) {
        body.splice(1, 0, h('img', {src: '/api/documents/' + encodeURIComponent(item.document_id) + '/file',
          alt: item.filename, loading: 'lazy',
          style: 'display:block;max-width:100%;max-height:220px;margin:10px 14px;border:1px solid var(--border);border-radius:8px'}));
      }
      target.appendChild(h('article', {class: 'quality-chunk'}, body));
    });
  }
  function paginate(target, data, load) {
    clear(target);
    if (!data.total) { return; }
    var previous = h('button', {class: 'btn btn-outline btn-sm', type: 'button'}, ['上一组']);
    previous.disabled = data.offset === 0;
    previous.addEventListener('click', function () { load(Math.max(0, data.offset - LIMIT)); });
    var next = h('button', {class: 'btn btn-outline btn-sm', type: 'button'}, ['下一组']);
    next.disabled = data.offset + data.items.length >= data.total;
    next.addEventListener('click', function () { load(data.offset + LIMIT); });
    target.appendChild(previous);
    target.appendChild(h('span', {class: 'small muted'}, [(data.offset + 1) + '–' + (data.offset + data.items.length) + ' / ' + data.total]));
    target.appendChild(next);
  }
  async function inspect(offset) {
    var ticket = ++S.inspection;
    var restoreFocus = els.qualityInspectPager.contains(document.activeElement) ? els.qualityInspectPager
      : els.qualitySummary.contains(document.activeElement) ? els.qualitySummary : null;
    var checkPdf = S.checkPdf; S.checkPdf = false;
    clear(els.qualityChunks); clear(els.qualityInspectPager);
    if (!S.loaded || !doc()) { message(els.qualitySummary, '所选资料不可用，请刷新列表或重新选择。', true); return; }
    message(els.qualitySummary, checkPdf ? '正在核对 PDF 页数与索引正文…' : '正在读取已入库正文…');
    try {
      var data = await api('/api/library/documents/' + S.docId + '/inspection?limit=' + LIMIT + '&offset=' + offset + '&check_pdf=' + checkPdf);
      if (ticket !== S.inspection) { return; }
      if (S.pdf && S.pdf.updated !== data.document.updated_at) { S.pdf = null; }
      if (checkPdf) { S.pdf = data.quality.pdf.status === 'ok' ? {updated: data.document.updated_at, value: data.quality.pdf} : null; }
      if (!checkPdf && S.pdf) {
        data.quality.pdf = Object.assign({}, S.pdf.value, {pages_without_indexed_text:
          Array.from({length: S.pdf.value.page_count}, function (_, i) {return i + 1;})
            .filter(function (page) {return data.quality.indexed_pages.indexOf(page) === -1;})});
      }
      renderSummary(data); renderChunks(els.qualityChunks, data.items, '');
      if (!data.items.length) { message(els.qualityChunks, '没有已入库的正文片段。可回到资料管理查看处理状态。'); }
      paginate(els.qualityInspectPager, data, inspect);
      if (restoreFocus && document.activeElement === document.body) { restoreFocus.focus(); }
    } catch (e) {
      if (ticket === S.inspection) {
        message(els.qualitySummary, '正文加载失败：' + e.message, true);
        var retry = h('button', {class: 'btn btn-outline', type: 'button'}, ['重试读取']);
        retry.addEventListener('click', function () { inspect(offset); }); els.qualitySummary.appendChild(retry);
      }
    }
  }
  async function search(offset) {
    var query = els.qualityQuery.value.trim();
    var restoreFocus = els.qualitySearchPager.contains(document.activeElement);
    resetSearch();
    if (!S.loaded || (S.docId !== null && !doc())) { els.qualitySearchStatus.textContent = '请先刷新资料列表并确认检索范围。'; return; }
    if (doc() && doc().status !== 'ready') { els.qualitySearchStatus.textContent = '这份资料尚未就绪，请先在文档体检中查看处理状态。'; return; }
    if (doc() && doc().lifecycle === 'archived' && !els.qualityIncludeArchived.checked) { els.qualitySearchStatus.textContent = '这份资料为历史版本；核对原文请勾选“包含历史版本”。'; return; }
    if (!query) { els.qualitySearchStatus.textContent = '请输入要在原文中查找的连续文字。'; return; }
    var ticket = S.search;
    els.qualitySearchStatus.textContent = '正在查找原文…'; els.qualitySearchBtn.disabled = true;
    try {
      var deviceFilter = els.qualityDeviceFilter ? els.qualityDeviceFilter.value : '';
      var data = await api('/api/library/search', {method: 'POST', body: {
        query: query, document_ids: S.docId === null ? null : [S.docId], limit: LIMIT, offset: offset,
        include_archived: els.qualityIncludeArchived.checked,
        device: deviceFilter || null
      }});
      if (ticket !== S.search) { return; }
      var shownTerms = (data.terms && data.terms.length) ? data.terms.join(' + ') : query;
      var groupText = (data.groups && data.groups.length) ? '，涉及 ' + data.groups.length + ' 份资料' : '';
      els.qualitySearchStatus.textContent = '「' + shownTerms + '」匹配 ' + data.total + ' 个片段' + groupText + '。' + (els.qualityIncludeArchived.checked ? '本次包含历史版本，请核对适用性。' : '仅检索启用资料。');
      renderGroupSummary(els.qualityGroupSummary, data.groups);
      renderChunks(els.qualityResults, data.items, query, data.groups);
      if (!data.total) { message(els.qualityResults, '当前范围的入库正文没有匹配项。可以缩短关键词、减少关键词，或核对文档是否完整入库。'); }
      paginate(els.qualitySearchPager, data, search);
      if (restoreFocus && document.activeElement === document.body) { els.qualitySearchPager.focus(); }
    } catch (e) { if (ticket === S.search) { els.qualitySearchStatus.textContent = '检索失败：' + e.message + '。可以点击“查找原文”重试。'; } }
    finally { if (ticket === S.search) { els.qualitySearchBtn.disabled = false; } }
  }
  async function boot() {
    wireGlobalErrors();
    var me = await initSession();
    if (!me) { return; }
    if (!['root', 'kb_admin'].includes(me.role)) { location.href = '/app'; return; }
    ['qualityDocs','qualityDocFilter','qualityDocCount','qualityReload','qualityAll','qualityScope',
      'qualityInspectTab','qualitySearchTab','qualityInspect','qualitySearch','qualitySummary','qualityChunks','qualityCheckAll','qualityCheckAllResults',
      'qualityInspectPager','qualitySearchForm','qualityQuery','qualityDeviceFilter','qualityGroupSummary','qualitySearchBtn','qualitySearchStatus','qualityResults','qualitySearchPager','qualityIncludeArchived'
    ].forEach(function (id) { els[id] = document.getElementById(id); });
    var id = new URLSearchParams(location.search).get('document_id');
    if (id && /^\d+$/.test(id) && Number.isSafeInteger(Number(id)) && Number(id) > 0) { S.docId = Number(id); }
    els.qualityDocFilter.addEventListener('input', renderDocs);
    els.qualityReload.addEventListener('click', function () { S.inspection++; S.pdf = null; S.checkPdf = false; resetSearch('正在刷新资料，请确认范围后重新检索。'); loadDocs(); });
    els.qualityAll.addEventListener('click', function () { selectDoc(null); });
    els.qualityInspectTab.addEventListener('click', function () { mode(false); if (S.docId !== null) { inspect(0); } });
    els.qualityCheckAll.addEventListener('click', checkAll);
    els.qualitySearchTab.addEventListener('click', function () { mode(true); });
    els.qualitySearchForm.addEventListener('submit', function (event) { event.preventDefault(); search(0); });
    els.qualityQuery.addEventListener('input', function () { resetSearch(); });
    els.qualityIncludeArchived.addEventListener('change', function () { resetSearch('版本范围已更改，请重新检索。'); });
    els.qualityDeviceFilter.addEventListener('change', function () {
      if (els.qualityQuery.value.trim()) { search(0); } else { resetSearch('设备已更改，请输入关键词再检索。'); }
    });
    registerSW(); await loadDocs();
    mode(S.docId !== null ? false : true);
  }
  document.addEventListener('DOMContentLoaded', function () { boot().catch(function (e) { toast('页面加载失败：' + e.message, 'error'); }); });
})();
