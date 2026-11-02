/* 资料管理页：概览、文档、问答与审计。无前端依赖。 */
'use strict';

(function () {
  var currentUser = null;
  var activeAdminRoute = null;
  var lastDocumentRoute = 'docs/list', lastAuditRoute = 'audit/chats';
  var adminRoutes = {
    'overview': {tab: 'overview'},
    'docs/list': {tab: 'docs', view: 'documentList'},
    'docs/upload': {tab: 'docs', view: 'fileImport'},
    'docs/images': {tab: 'docs', view: 'imageTextImport'},
    'audit/chats': {tab: 'audit', view: 'chats'},
    'audit/feedback': {tab: 'audit', view: 'feedback'},
    'audit/logs': {tab: 'audit', view: 'logs'}
  };
  var adminRouteAliases = {docs: 'docs/list', documentList: 'docs/list', fileImport: 'docs/upload',
    webImport: 'docs/upload', imageTextImport: 'docs/images', audit: 'audit/chats',
    chats: 'audit/chats', feedback: 'audit/feedback', logs: 'audit/logs'};


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
          m.embed_backend === 'mock' ? badge('演示环境 · mock', 'b-warn') : null,
          badge(m.model_ready ? '嵌入模型已就绪' : '嵌入模型未就绪', m.model_ready ? 'b-ok' : 'b-warn'),
          badge(!m.llm_check ? '生成接口尚未探测' : (m.llm_check.ok ? '生成接口最近探测正常' : '生成接口最近探测失败'),
            m.llm_check && m.llm_check.ok ? 'b-ok' : 'b-warn')
        ]),
        h('p', { class: 'model-msg overview-note' }, [m.model_message || '—']),
        h('div', { class: 'kv-grid overview-metrics' }, [
          kv('生成模型', m.llm_model), kv('嵌入模型', m.embed_model),
          kv('嵌入设备', m.embed_device || '—'), kv('嵌入后端', m.embed_backend)
        ]),
        h('details', { class: 'overview-details' }, [
          h('summary', {}, ['配置详情']),
          h('div', { class: 'kv-grid overview-metrics' }, [
            kv('应用版本', m.version), kv('切片上限', m.chunk_max_tokens + ' token'),
            kv('切片重叠', m.chunk_overlap_tokens + ' token'), kv('拒答阈值', m.min_relevance_score),
            kv('对外地址', m.public_origin || '未配置')
          ])
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
    if (!lat) { host.appendChild(h('p', { class: 'small muted' }, ['暂无耗时样本'])); return; }
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
    host.appendChild(h('div', { class: 'overview-context' }, [
      h('span', {}, ['模型样本 / 窗口：' + lat.sample_size + ' / ' + lat.window]),
      h('span', {}, ['检索样本：' + (retr ? retr.samples : 0)]),
      h('span', {}, ['拒答 / 失败：' + lat.refusals + ' / ' + lat.errors]),
      h('span', {}, ['统计截至：' + (lat.span ? fmtTime(lat.span.to) : '—')])
    ]));
    host.appendChild(h('div', { class: 'kv-grid overview-metrics' }, [
      kv('模型调用 中位', msLabel(model ? model.p50 : null)),
      kv('检索 中位', msLabel(retr ? retr.p50 : null)),
      kv('模型调用 P95', msLabel(model ? model.p95 : null)),
      kv('检索 P95', msLabel(retr ? retr.p95 : null))
    ]));
    host.appendChild(h('p', { class: 'small muted overview-note' }, [verdict]));
    host.appendChild(h('p', { class: 'small muted overview-note' }, ['仅统计模型调用与检索阶段，非整次请求耗时。']));
    host.appendChild(h('details', { class: 'overview-details' }, [
      h('summary', {}, ['更多统计与口径']),
      h('div', { class: 'kv-grid overview-metrics' }, [
        kv('模型调用 最长', msLabel(model ? model.max : null)),
        kv('输出 token 中位', lat.completion_tokens ? lat.completion_tokens.p50 : null),
        kv('统计起始', lat.span ? fmtTime(lat.span.from) : '—'),
        kv('统计截至', lat.span ? fmtTime(lat.span.to) : '—')
      ]),
      h('p', { class: 'small muted overview-note' }, [lat.note || ''])
    ]));
  }

  function filterDocuments() {
    // 按文件名过滤当前资料列表。
    var field = qs('#docFilterName'); var count = qs('#docFilterCount');
    if (!field || !count) { return; }
    var term = field.value.trim().toLocaleLowerCase();
    var rows = qsa('#docsBody tr[data-document-name]'); var shown = 0;
    rows.forEach(function (row) {
      var match = row.dataset.documentName.toLocaleLowerCase().includes(term);
      row.hidden = !match; if (match) { shown++; }
    });
    count.textContent = rows.length ? (term ? '显示 ' + shown + ' / ' + rows.length + ' 份资料' : '共 ' + rows.length + ' 份资料') : '';
  }

  function showDocumentView(view, focus) {
    var ids = ['documentList', 'fileImport', 'imageTextImport'];
    if (!ids.includes(view)) { return; }
    var activeView = view;
    ids.forEach(function (id) {
      var visible = id === activeView;
      qs('#' + id).classList.toggle('hidden', !visible);
    });
    var importVersionField = qs('#importVersion');
    if (importVersionField) { importVersionField.classList.toggle('hidden', activeView === 'documentList'); }
    qsa('[data-doc-view]').forEach(function (link) {
      var active = link.dataset.docView === activeView;
      link.classList.toggle('btn-primary', active); link.classList.toggle('btn-outline', !active);
      if (active) { link.setAttribute('aria-current', 'page'); } else { link.removeAttribute('aria-current'); }
    });
    if (focus) { qs('#' + view).focus({preventScroll:true}); }
  }

  function bindImageTextForm() {
    var form = qs('#imageTextForm'), fileInput = qs('#imageTextFile'), caption = qs('#imageTextCaption');
    var device = qs('#imageTextDevice'), preview = qs('#imageTextPreview'), status = qs('#imageTextStatus');
    var confirmed = qs('#imageTextConfirmed');
    var analyze = qs('#imageTextAnalyze'), submit = qs('#imageTextSubmit');
    var result = qs('#imageTextAnalysis'), resultText = qs('#imageTextAnalysisText'), useResult = qs('#imageTextUseAnalysis');
    var previewUrl = null, analysisRevision = 0, working = false;
    function clearAnalysis() {
      analysisRevision++;
      result.classList.add('hidden');
      resultText.textContent = '';
      confirmed.checked = false;
    }
    function selectedFile() {
      var files = fileInput.files;
      if (!files || files.length !== 1) { throw new Error('请选择一张图片'); }
      var file = files[0];
      if (!/\.(png|jpe?g)$/i.test(file.name || '') || file.size > 10 * 1024 * 1024 || file.size <= 0) {
        throw new Error('请选择不超过 10 MB 的 PNG 或 JPEG 图片');
      }
      return file;
    }
    function bodyFor(file, text) {
      var body = new FormData();
      body.append('file', file, file.name);
      body.append('caption', text);
      return body;
    }
    fileInput.addEventListener('change', function () {
      clearAnalysis(); status.textContent = '';
      if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
      preview.removeAttribute('src'); preview.classList.add('hidden');
      try {
        var file = selectedFile();
        previewUrl = URL.createObjectURL(file);
        preview.src = previewUrl; preview.classList.remove('hidden');
      } catch (e) { status.textContent = e.message; }
    });
    caption.addEventListener('input', function () { clearAnalysis(); status.textContent = ''; });
    analyze.addEventListener('click', async function () {
      if (working) { return; }
      try {
        var file = selectedFile(), text = caption.value.trim();
        if (Array.from(text).length > 2000) { throw new Error('关联文字最多 2000 字'); }
        clearAnalysis(); var revision = analysisRevision;
        working = true; analyze.disabled = true; submit.disabled = true;
        status.textContent = '正在分析图片…';
        var response = await api('/api/admin/image-analysis', {method:'POST',body:bodyFor(file,text)});
        if (revision !== analysisRevision) { return; }
        if (!response || typeof response.analysis !== 'string' || !response.analysis.trim()) { throw new Error('分析未返回可用文字'); }
        resultText.textContent = response.analysis.trim(); result.classList.remove('hidden');
        status.textContent = '分析完成，请对照原图核对。';
      } catch (e) { if (revision === undefined || revision === analysisRevision) { status.textContent = e.message || '分析失败，请稍后重试'; } }
      finally { working = false; analyze.disabled = false; submit.disabled = false; }
    });
    useResult.addEventListener('click', function () {
      var suggestion = resultText.textContent.trim();
      if (!suggestion) { return; }
      var merged = [caption.value.trim(), suggestion].filter(Boolean).join('\n\n');
      if (Array.from(merged).length > 2000) { status.textContent = '追加后超过 2000 字，请手动选择需要的内容。'; return; }
      caption.value = merged; clearAnalysis(); status.textContent = '已追加，请核对和修改说明后再上传。';
    });
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (working) { return; }
      try {
        var file = selectedFile(), text = caption.value.trim();
        if (!text) { throw new Error('请填写关联文字'); }
        if (Array.from(text).length > 2000) { throw new Error('关联文字最多 2000 字'); }
        if (!confirmed.checked) { throw new Error('请先核对说明并勾选确认'); }
        var body = bodyFor(file,text); body.append('device', device.value); body.append('version', '1.0');
        working = true; analyze.disabled = true; submit.disabled = true;
        fileInput.disabled = true; caption.disabled = true; device.disabled = true; confirmed.disabled = true;
        status.textContent = '正在上传…';
        await api('/api/admin/documents/image-text', {method:'POST',body:body});
        form.reset(); clearAnalysis();
        if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
        preview.removeAttribute('src'); preview.classList.add('hidden');
        status.textContent = '图文资料已入库，可以参与问答。';
        try { await loadDocs(); await refreshOverview(); }
        catch (e) { status.textContent = '图文资料已入库，但列表刷新失败；请刷新资料列表。'; }
      } catch (e) { status.textContent = e.message || '上传失败，请稍后重试'; }
      finally { working = false; analyze.disabled = false; submit.disabled = false; fileInput.disabled = false; caption.disabled = false; device.disabled = false; confirmed.disabled = false; }
    });
  }

  async function loadDocs() {
    var loading = qs('#docsLoading'); var body = qs('#docsBody');
    loading.classList.remove('hidden'); clear(body);
    try {
      var params = new URLSearchParams();
      var filterVersionField = qs('#docFilterVersion');
      var filterVersion = filterVersionField ? filterVersionField.value.trim() : '';
      if (filterVersion) { params.set('version', filterVersion); }
      var deviceSelect = qs('#docFilterDevice');
      if (deviceSelect && deviceSelect.value) { params.set('device', deviceSelect.value); }
      var suffix = params.toString() ? '?' + params.toString() : '';
      var data = await api('/api/admin/documents' + suffix);
      if (window.OCRUI) { window.OCRUI.schedule(data.items || [], loadDocs); }
      if (!data.items.length) { qs('#docFilterCount').textContent = ''; body.appendChild(empty(filterVersion ? '没有符合当前版本的资料' : '尚未上传文档')); return; }
      var rows = data.items.map(function (d) {
        var ocrPages = parseOcrPages(d.tags);
        var ocrNotes = [];
        var isOcrImage = /\.(png|jpe?g)$/i.test(d.filename || '');
        var docTags = []; try {docTags = JSON.parse(d.tags || '[]');} catch (e) { /* ignore invalid tags */ }
        var captionOnly = Array.isArray(docTags) && docTags.indexOf('image_caption_only') !== -1;
        var directImage = captionOnly && docTags.indexOf('image_text_direct') !== -1 && !d.ocr_state;
        if (isOcrImage && captionOnly && !directImage) {ocrNotes.push('人工图片说明 · 无OCR文字');}
        if (ocrPages.suspect.length) {
          ocrNotes.push('OCR 可疑页：第 ' + ocrPages.suspect.join('、') + ' 页');
        }
        if (ocrPages.failed.length) {
          ocrNotes.push('OCR 失败页（未入库）：第 ' + ocrPages.failed.join('、') + ' 页');
        }
        var actions = h('td', { class: 'cell-actions' });
        var primaryActions = h('div', {class:'doc-primary-actions'});
        primaryActions.appendChild(h('a', { class: 'btn btn-sm btn-outline', href: '/library?document_id=' + encodeURIComponent(d.id) }, ['检查正文']));
        if (d.ocr_state && window.OCRUI) {
          primaryActions.appendChild(actionButton('查看 / 核对OCR', 'btn-outline', function () { window.OCRUI.open(d, loadDocs); }));
        }
        var moreActions = h('div', {class:'doc-more-actions'});
        var more = h('details', {class:'doc-more'}, [h('summary', {class:'btn btn-sm btn-outline'}, ['更多']), moreActions]);
        if (d.source_type === 'image' && d.status === 'ready' && (d.ocr_state === 'ready' || directImage)) {
          moreActions.appendChild(actionButton('编辑图片说明', 'btn-outline', function () { editCaption(d, captionOnly); }));
        }
        moreActions.appendChild(actionButton('归档与适用范围', 'btn-outline', function (btn) { editLifecycle(d, data.items, btn); }));
        var reindexBtn = actionButton('重建索引', 'btn-outline', function (btn) { reindexDoc(d.id, d.filename, btn); });
        var isOcrImported = ocrPages.ok.length > 0 || ocrPages.suspect.length > 0 || ocrPages.failed.length > 0;
        if (isOcrImported || d.ocr_state || directImage) {
          // 普通重建会重新解析原 PDF、把 failed 页加回索引，并使旧 OCR tags 失真；
          // 即使所有页都 ok，也必须走 OCR 工具重新导入，不能普通重建。
          reindexBtn.disabled = true;
          reindexBtn.setAttribute('aria-disabled', 'true');
          reindexBtn.classList.add('is-disabled');
          reindexBtn.title = directImage ? '请通过编辑图片说明更新索引。' : 'OCR 导入文档不支持普通重建索引；请重新运行 OCR 预处理后导入新结果。';
        }
        moreActions.appendChild(reindexBtn);
        if (reindexBtn.disabled) { moreActions.appendChild(h('p', {class:'doc-reindex-reason'}, [reindexBtn.title])); }
        if (currentUser && currentUser.role === 'root') {
          moreActions.appendChild(actionButton('设置访问', 'btn-outline', function (btn) { setDocumentAccess(d, btn); }));
        }
        moreActions.appendChild(actionButton('删除', 'btn-danger-outline', function (btn) { removeDoc(d.id, d.filename, btn); }));
        primaryActions.appendChild(more);
        actions.appendChild(primaryActions);
        var state = d.status === 'ready' ? badge('就绪', 'b-ok') : d.status === 'failed' ? badge('失败', 'b-err') : badge('处理中', 'b-warn');
        var stateCell = h('td', {}, [state]);
        if (d.ocr_state) {
          var stages = {queued:'OCR 排队中',running:'OCR 识别中',review:'OCR 等待核对',ready:'OCR 文字已核对',failed:'OCR 失败，可重试'};
          stateCell.appendChild(h('div', {class: d.ocr_state === 'ready' ? 'small muted' : 'doc-ocr-warn'}, [captionOnly && d.ocr_state === 'ready' ? '人工说明已入库' : stages[d.ocr_state] || d.ocr_state]));
          if (d.ocr_error) { stateCell.appendChild(h('div', {class:'doc-err'}, [d.ocr_error])); }
        }
        if (d.lifecycle === 'archived') {
          stateCell.appendChild(h('div', {class:'doc-ocr-warn'}, ['历史版本 · 问答停用']));
        }
        if (d.replacement_document_id) { stateCell.appendChild(h('a', {href: '/library?document_id=' + encodeURIComponent(d.replacement_document_id)}, ['查看替代资料 #' + d.replacement_document_id])); }
        var name = h('td', { class: 'cell-main', title: d.filename }, [d.filename]);
        name.appendChild(h('div', {class: 'small muted'}, [d.visibility === 'shared' ? '所有用户可见' : '仅授权账号或用户组可见']));
        if (window.WebImportUI) {
          var webNotes = window.WebImportUI.notes(d.source_metadata);
          if (webNotes) { name.appendChild(webNotes); }
        }
        if (window.LogImportUI) {
          var logNotes = window.LogImportUI.notes(d.source_metadata);
          if (logNotes) { name.appendChild(logNotes); }
        }
        if (d.applicability) { name.appendChild(h('div', {class: 'small muted'}, ['适用范围：' + d.applicability])); }
        if (d.error) { name.appendChild(h('div', { class: 'doc-err', title: d.error }, [d.error])); }
        if (ocrNotes.length) {
          name.appendChild(h('div', { class: 'doc-ocr-hint', title: ocrNotes.join('；') }, [ocrNotes.join('；')]));
        }
        if (!d.ocr_state && !directImage && (isOcrImage || ocrNotes.length)) {
          stateCell.appendChild(h('div', { class: 'doc-ocr-warn' }, ['OCR 待核对']));
        }
        var sourceType = d.source_type || (/\.(png|jpe?g)$/i.test(d.filename || '') ? 'image'
          : window.WebImportUI && window.WebImportUI.metadata(d.source_metadata) ? 'web' : 'file');
        return h('tr', {dataset:{documentName:d.filename || '', documentType:sourceType}}, [name, h('td', {}, [documentDeviceSelect(d)]), cell(fmtBytes(d.size_bytes)), stateCell, cell(fmtTime(d.created_at)), actions]);
      });
      body.appendChild(table(['文件', '设备类型', '大小', '状态', '上传日期', '操作'], rows));
      filterDocuments();
    } catch (e) { body.appendChild(loadError(e.message || '文档加载失败', loadDocs)); }
    finally {
      loading.classList.add('hidden');
    }
  }

  function editCaption(doc, captionOnly) {
    formModal({
      title: '编辑图片说明 · ' + doc.filename, submitText: '保存说明',
      fields: [
        {name: 'caption', label: '图片内容与关系说明（最多2000字，' + (captionOnly ? '不可留空' : '可留空') + '；用于检索，不会修改 OCR 原文。图中标注有误时，请写明错误及更正）', type: 'textarea', rows: 6, value: doc.caption || '', maxlength: 2000}
      ],
      onSubmit: async function (values) {
        await api('/api/admin/documents/' + doc.id + '/caption', {method: 'PATCH', body: {
          caption: values.caption.trim(), expected_caption: doc.caption || ''
        }});
        toast('图片说明已保存', 'success');
        await loadDocs();
      }
    });
  }

  function documentDeviceSelect(doc) {
    var options = [['', '未指定'], ['通用', '通用'], ['PLM', 'PLM'],
      ['Plus_500', 'Plus_500'], ['Plus_Pro', 'Plus_Pro'], ['LP', 'LP']];
    var current = doc.device || '';
    if (!options.some(function (option) { return option[0] === current; })) { options.push([current, current]); }
    var select = h('select', {class:'input doc-device-select', 'aria-label':'设备类型 · ' + doc.filename},
      options.map(function (option) { return h('option', {value:option[0]}, [option[1]]); }));
    select.value = current;
    if (doc.status === 'parsing') { select.disabled = true; select.title = '资料处理完成后可修改设备类型'; }
    select.addEventListener('change', async function () {
      var selected = select.value;
      if (selected === current) { return; }
      select.disabled = true;
      try {
        await api('/api/admin/documents/' + doc.id + '/device', {method:'PATCH',body:{device:selected,expected_device:current}});
        toast('设备类型已保存；新的问答请刷新资料列表', 'success');
        await loadDocs();
      } catch (e) {
        select.value = current;
        toast('设备类型保存失败：' + (e.message || '请重试'), 'error');
      } finally { select.disabled = false; }
    });
    return select;
  }

  /* 显式设置版本状态，不按上传时间或版本号猜测替代关系。 */
  async function editLifecycle(doc, documents, btn) {
    var options = [{value: '', label: '不指定替代资料'}];
    // 归档候选需要完整资料列表，显式重新拉取一次。
    busy(btn, true, '读取资料…');
    try {
      var latest = await api('/api/admin/documents');
      documents = latest.items || [];
    } catch (e) { toast('版本列表读取失败：' + e.message, 'error'); return; }
    finally { busy(btn, false); }
    documents.filter(function (d) {
      return d.id !== doc.id && d.status === 'ready' && d.lifecycle !== 'archived';
    }).forEach(function (d) { options.push({value: String(d.id), label: d.filename + '（#' + d.id + '）'}); });
    if (doc.replacement_document_id && !options.some(function (option) {return option.value === String(doc.replacement_document_id);})) {
      options.push({value: String(doc.replacement_document_id), label: '原替代资料 #' + doc.replacement_document_id + '（现已停用或不可用，请更换或清空）'});
    }
    formModal({
      title: '归档与适用范围', submitText: '保存归档设置',
      fields: [
        {name: 'device', label: '设备（用于筛选和问答范围）', type: 'select', value: doc.device || '', options: [
          {value: '', label: '未指定'}, {value: '通用', label: '通用（全部设备）'}, {value: 'PLM', label: 'PLM'}, {value: 'Plus_500', label: 'Plus_500'}, {value: 'Plus_Pro', label: 'Plus_Pro'}, {value: 'LP', label: 'LP'}]},
        {name: 'applicability', label: '协议 / 软件版本 / 场景（最多200字，供选择资料时核对）', type: 'textarea', rows: 3, value: doc.applicability || '', maxlength: 200},
        {name: 'lifecycle', label: '问答使用状态（设为历史后，旧对话保留，后续提问需重新选资料）', type: 'select', value: doc.lifecycle || 'active', options: [
          {value: 'active', label: '启用：加入普通问答检索'}, {value: 'archived', label: '历史：保留原件与引用，停止普通问答检索'}]},
        {name: 'replacement_document_id', label: '旧资料的替代版本（仅历史状态可指定；恢复启用请清空）', type: 'select', value: doc.replacement_document_id ? String(doc.replacement_document_id) : '', options: options}
      ],
      onSubmit: async function (values) {
        var replacement = values.replacement_document_id ? Number(values.replacement_document_id) : null;
        if (values.lifecycle === 'active' && replacement !== null) { throw new Error('恢复启用前请清空替代资料。'); }
        if (Array.from(values.applicability.trim()).length > 200) { throw new Error('适用范围最多200字。'); }
        await api('/api/admin/documents/' + doc.id + '/lifecycle', {method: 'PATCH', body: {
          version: doc.version || '1.0', device: values.device || '', applicability: values.applicability.trim(), lifecycle: values.lifecycle, replacement_document_id: replacement
        }});
        toast('归档设置已保存；已打开的问答页面请刷新资料列表', 'success');
        await loadDocs();
      }
    });
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
    var ocrCount = 0;
    // 不再用 setInterval 轮询文档列表：上传本身就是串行等待的，轮询既浪费请求，
    // 又会在结束前一直跑；改为全部结束后刷新一次。
    var jobs = list.map(async function (file) {
      var chip = h('span', { class: 'chip' }, [h('span', { class: 'spin' }), h('span', { class: 'chip-name', title: file.name }, [file.name])]);
      chips.appendChild(chip);
      try {
        if (/\.log$/i.test(file.name || '')) { throw new Error('不再支持日志文件导入，请上传支持的普通文件'); }
        var form = new FormData(); form.append('file', file, file.name);
        form.append('version', qs('#docVersion').value.trim() || '1.0');
        var deviceField = qs('#docDevice');
        if (deviceField && deviceField.value) { form.append('device', deviceField.value); }
        var image = /\.(png|jpe?g)$/i.test(file.name || '');
        if (image && !window.OCRUI) { throw new Error('图片识别功能未加载，请刷新后重试'); }
        var useOcr = image || Boolean(window.OCRUI && window.OCRUI.selected(file));
        await api(useOcr ? '/api/admin/documents/ocr' : '/api/admin/documents', { method: 'POST', body: form });
        chip.className = useOcr ? 'chip' : 'chip chip-ok'; chip.firstChild.innerHTML = icon(useOcr ? 'clock' : 'check');
        if (useOcr) { ocrCount++; chip.appendChild(h('span', {}, ['已提交OCR，等待核对'])); }
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
    else if (list.length) { toast(ocrCount ? '上传已提交，请在文档列表核对OCR结果' : '上传完成', 'success'); }
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
    if (!window.confirm('确认删除《' + name + '》？此操作不可撤销。\n旧回答中的该文档引用也会被删除。若需保留原件及旧引用，请使用“版本与适用范围”将其设为历史。')) { return; }
    busy(btn, true, '删除中…');
    try {
      await api('/api/admin/documents/' + id, { method: 'DELETE' });
      toast('文档已删除', 'success'); await loadDocs(); await refreshOverview();
    } catch (e) { toast(e.message || '删除文档失败', 'error'); }
    finally { busy(btn, false); }
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

  var reviewLabels = {pending:'待审核',confirmed:'已确认问题',resolved:'已解决（人工确认）',dismissed:'不采纳'};
  var failureLabels = {'':'未分类',parsing:'解析错误',recall:'检索漏召回',selection:'选段漏证据',misread:'模型误读',
    unsupported:'无依据扩写',contradiction:'自相矛盾',false_refusal:'误拒答',other:'其他'};

  async function reviewFeedback(record, btn) {
    busy(btn,true,'读取依据…');
    var data;
    try { data = await api('/api/admin/feedback/' + record.id + '/review'); }
    catch (e) {toast('读取审核失败：' + e.message,'error'); return;}
    finally {busy(btn,false);}
    var original = data.feedback, saved = data.review || {};
    var structured = (saved.snapshot && saved.snapshot.expected_sources) || [];
    function describeSources() {
      return structured.map(function (source) {
        return source.filename + ' · 片段ID ' + source.chunk_id + '\n' + source.excerpt;
      }).join('\n\n') || '尚未选择，不会自动采用原模型引用';
    }
    var sources = (original.sources || []).map(function (source,index) {
      return '[' + (index+1) + '] ' + source.filename + ' · 页 ' + (source.page || '—') + ' · 起始段 ' + (source.paragraph || '—') +
        ' · 当前版本标签 ' + source.version + '\n' + source.excerpt;
    }).join('\n\n');
    var context = '原文档范围ID：' + JSON.stringify(original.document_ids) + '（[]为全库，null为缺少记录）';
    if (original.context_requires_revalidation) {
      context += '\n这是追问题，不能脱离上下文直接复测。最近上下文：\n' + (original.prior_turns || []).map(function (turn) {
        return '问：' + turn.question + '\n答：' + turn.answer;
      }).join('\n\n');
    }
    if (data.review_stale) {context += '\n\n反馈已变化，旧审核已转为待审核。下方旧审核内容仅供参考，须重新核对。';}
    var history = (data.history || []).map(function (item) {
      return '#' + item.id + ' ' + reviewLabels[item.status] + ' · ' + item.reviewer_name + ' · ' + fmtTime(item.created_at) + '\n' + item.note;
    }).join('\n\n');
    formModal({title:'反馈审核 · #' + record.id,modalClass:'feedback-review',focusClose:true,submitText:'保存审核',
      advancedTitle:'补充证据和复测（可展开）',advancedOpen:false,fields:[
      {name:'status',label:'处理状态 *',required:true,type:'select',value:data.review_status,options:Object.keys(reviewLabels).map(function (key) {return {value:key,label:reviewLabels[key]};})},
      {name:'failure_type',label:'问题分类（选填）',type:'select',value:saved.failure_type || '',options:Object.keys(failureLabels).map(function (key) {return {value:key,label:failureLabels[key]};})},
      {name:'expected_answer',label:'核对结果 / 期望回答（最多4000字）',type:'textarea',rows:4,maxlength:4000,value:saved.expected_answer || ''},
      {name:'note',label:'处理说明（已解决写复测，不采纳写原因）',type:'textarea',rows:3,maxlength:2000,value:saved.note || ''},
      {name:'original_sources',advanced:true,label:'原回答引用摘录（只读；需核对原件）',type:'textarea',readonly:true,rows:7,value:sources || '原回答没有来源'},
      {name:'correct_sources',advanced:true,label:'人工选择的正确来源（只读）',type:'textarea',readonly:true,rows:3,value:describeSources()},
      {name:'evidence',advanced:true,label:'证据或缺失信息（选填，最多6000字）',type:'textarea',rows:5,maxlength:6000,value:saved.evidence || ''},
      {name:'applicability',advanced:true,label:'适用设备、版本或场景（选填，最多400字）',maxlength:400,value:saved.applicability || ''},
      {name:'answer_policy',advanced:true,label:'资料是否足以回答',type:'select',
        value:saved.should_refuse === 1 || saved.should_refuse === true ? 'refuse' : saved.should_refuse === 0 || saved.should_refuse === false ? 'answer' : '',options:[
        {value:'',label:'尚未核对'},{value:'answer',label:'资料足够，应依据原文回答'},{value:'refuse',label:'资料不足，应明确说明或拒答'}]},
      {name:'replay_question',advanced:true,label:'完整独立的复测问题（最多2000字；追问须补齐上下文）',type:'textarea',rows:3,maxlength:2000,
        value:(saved.snapshot && saved.snapshot.replay_question) || (original.context_requires_revalidation ? '' : original.question)},
      {name:'replay_policy',advanced:true,label:'复测确认（固定当前可见的资料范围）',type:'select',value:'no',options:[
        {value:'no',label:'本次仅保存审核，不确认复测资料'},
        {value:'yes',label:'我已核对完整问题、正确来源和当前资料范围，确认复测资料'}]},
      {name:'review_history',advanced:true,label:'最近10次审核记录（只读）',type:'textarea',readonly:true,rows:3,value:history || '尚无审核记录'}
    ],onReady:function (form) {
      var status = qs('[name="status"]',form), replay = qs('[name="replay_policy"]',form);
      var policy = qs('[name="answer_policy"]',form);
      function originalItem(label, value, css) {
        return h('div',{class:'review-original-item ' + (css || '')},[
          h('span',{class:'review-original-label'},[label]),
          h('div',{class:'review-original-text',tabindex:'0'},[value || '无'])]);
      }
      var originalCard = h('section',{class:'review-original','aria-label':'原始记录'},[
        h('div',{class:'review-original-head'},[h('strong',{},['原始记录']),h('span',{class:'small muted'},['仅供核对，不自动认定正确'])]),
        original.context_requires_revalidation ? h('p',{class:'review-original-warning'},['这是追问，请展开上下文核对后再复测。']) : null,
        data.review_stale ? h('p',{class:'review-original-warning'},['反馈已变化，旧审核仅供参考，须重新核对。']) : null,
        originalItem('原问题',original.question,'review-question'),
        originalItem('用户补充',original.comment,'review-comment'),
        originalItem('原回答',original.answer,'review-answer'),
        h('details',{class:'review-context'},[h('summary',{},['上下文与资料范围']),h('pre',{class:'review-context-text',tabindex:'0'},[context])])]);
      form.insertBefore(originalCard, status.closest('label'));
      var sourceField = qs('[name="correct_sources"]',form);
      var sourceButton = actionButton('选择 / 修改来源', 'btn-outline', async function () {
        if (!window.ReviewSources) { toast('来源选择功能不可用，请刷新页面后重试', 'error'); return; }
        try {
          var selected = await window.ReviewSources.pick(structured);
          if (selected === null) { return; }
          structured = selected;
          sourceField.value = describeSources();
          if (sourceStatus) { sourceStatus.textContent = '来源已重新选择；保存审核后可重新核对复测状态。'; }
        } catch (e) { toast('选择来源失败：' + (e.message || '请重试'), 'error'); }
      });
      sourceField.closest('label').insertAdjacentElement('afterend', sourceButton);
      var sourceStatus = null;
      if (data.source_validation && !data.source_validation.ready) {
        sourceStatus = h('p',{class:'small muted'},['已保存的复测资料尚未就绪：' + data.source_validation.reasons.join('；')]);
        sourceButton.insertAdjacentElement('afterend', sourceStatus);
      }
      if (saved.snapshot && saved.snapshot.replay_confirmed) {
        form.insertBefore(h('p',{class:'small muted'},['此前已确认复测资料；本次默认不重新确认，旧记录保留。如需重新确认，请展开下方内容。']),qs('.review-advanced',form));
      }
      form.addEventListener('invalid',function (event) {
        var section = event.target.closest('details');
        if (section) { section.open = true; }
      },true);
      function mark(name, label, required) {
        var field = qs('[name="' + name + '"]',form);
        field.required = required;
        field.setAttribute('aria-required', required ? 'true' : 'false');
        field.closest('label').querySelector('.field-label').textContent = label + (required ? ' *' : '');
      }
      function update() {
        var replaying = replay.value === 'yes';
        mark('expected_answer','核对结果 / 期望回答（最多4000字）',status.value === 'confirmed' || status.value === 'resolved' || replaying);
        mark('note','处理说明（已解决写复测，不采纳写原因）',status.value === 'resolved' || status.value === 'dismissed');
        mark('answer_policy','资料是否足以回答',replaying);
        mark('replay_question','完整独立的复测问题（最多2000字；追问须补齐上下文）',replaying);
        var sourceLabel = qs('[name="correct_sources"]',form).closest('label').querySelector('.field-label');
        sourceLabel.textContent = '人工选择的正确来源（只读）' + (replaying && policy.value === 'answer' ? ' *' : '');
      }
      status.addEventListener('change',update); replay.addEventListener('change',update); policy.addEventListener('change',update); update();
    },onSubmit:async function (values) {
      if (values.replay_policy === 'yes' && values.status !== 'confirmed' && values.status !== 'resolved') {
        throw new Error('确认复测资料前，请将处理状态设为已确认问题或已解决');
      }
      if (values.replay_policy === 'yes' && values.answer_policy === 'answer' && !structured.length) {
        throw new Error('确认资料足够回答时，请先选择正确来源');
      }
      if (values.replay_policy === 'yes' && values.answer_policy === 'refuse' && structured.length) {
        throw new Error('资料不足的复测题不能选择答案支持来源，请先移除所选来源');
      }
      var body = {expected_review_id:saved.id || 0,feedback_hash:data.feedback_hash,status:values.status,
        failure_type:values.failure_type,should_refuse:values.answer_policy ? values.answer_policy === 'refuse' : null,
        replay_confirmed:values.replay_policy === 'yes',expected_sources:structured.map(function (source) {
          var selected={};['document_id','chunk_id','excerpt','sha256','version','lifecycle','applicability','chunk_content_sha256'].forEach(function (key) {selected[key]=source[key];});return selected;
        })};
      var limits = {expected_answer:4000,evidence:6000,applicability:400,note:2000,replay_question:2000};
      Object.keys(limits).forEach(function (key) {
        body[key] = values[key].trim();
        if (Array.from(body[key]).length > limits[key]) {throw new Error(key + '超过' + limits[key] + '字');}
      });
      try { await api('/api/admin/feedback/' + record.id + '/review',{method:'PATCH',body:body}); }
      catch (e) {
        if (structured.length) { throw new Error((e.message || '保存失败') + '；若所选来源已变化，请重新选择或移除来源。'); }
        throw e;
      }
      toast('审核已保存；原答案与旧审核记录未改写','success'); await loadAudit('feedback');
    }});
  }

  async function deleteFeedback(record, btn) {
    if (!window.confirm('确认删除这条反馈？\n\n该反馈及其所有审核记录将被删除，原问答、引用和资料保留。')) { return; }
    busy(btn,true,'删除中…');
    try {
      await api('/api/admin/feedback/' + record.id, {method:'DELETE',body:{
        feedback_hash:record.feedback_hash,expected_review_id:record.review_id || 0
      }});
      toast('反馈已删除','success');
      await loadAudit('feedback');
    } catch (e) { toast(e.message || '删除失败，请刷新后重试','error'); }
    finally { busy(btn,false); }
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
      var filter = isFeedback && qs('#feedbackFilterStatus');
      if (filter && filter.value) {endpoint += '&review_status=' + encodeURIComponent(filter.value);}
      var data = await api(endpoint);
      qs(isChats ? '#chatsHint' : (isFeedback ? '#feedbackHint' : '#logsHint')).textContent = '共 ' + data.total + ' 条，显示最近 ' + data.items.length + ' 条';
      if (!data.items.length) { body.appendChild(empty(isChats ? '暂无对话记录' : (isFeedback ? '暂无用户反馈' : '暂无审计日志'))); return; }
      var rows = data.items.map(function (x) {
        if (isChats) {
          return h('tr', {}, [cell(x.username), cell(x.title, 'cell-long'), cell(x.turn_count), cell(fmtTime(x.created_at)), cell(fmtTime(x.updated_at)),
            h('td', { class: 'cell-actions' }, [actionButton('删除', 'btn-danger', function (btn) { deleteConversation(x, btn); })])]);
        }
        if (isFeedback) {
          var correction = x.comment ? h('td', {}, [h('details', {}, [
            h('summary', {}, [x.rating === 'helpful' ? '用户补充' : '用户补充（待核对）']),
            h('div', { class: 'detail-answer' }, [x.comment])
          ])]) : cell('—');
          var status = x.review_status === 'not_required' ? '无需审核' : (reviewLabels[x.review_status] || reviewLabels.pending);
          if (x.review_stale) {status += ' · 内容变化，须重审';}
          return h('tr', {}, [cell(x.username), cell(x.rating === 'helpful' ? '有帮助' : '没帮助'),
            cell(excerpt(x.question, 90), 'cell-long'), correction, cell(status),cell(fmtTime(x.created_at)),
            h('td',{class:'cell-actions'},(x.rating === 'helpful' ? [] : [
              actionButton('核对 / 处理','btn-outline',function (btn) {reviewFeedback(x,btn);})]).concat([
              actionButton('删除','btn-danger-outline',function (btn) {deleteFeedback(x,btn);})]))]);
        }
        var detail = (x.detail || '').replace(/\s*切片数:\d+/g, '');
        return h('tr', {}, [cell(x.username || '系统'), cell(x.action), cell(excerpt(detail, 120), 'cell-long'), cell(x.ip), cell(fmtTime(x.created_at))]);
      });
      body.appendChild(table(isChats ? ['用户', '对话标题', '问答轮数', '创建时间', '最后更新', '操作'] : (isFeedback ? ['用户', '评价', '问题', '备注', '审核状态', '时间', '操作'] : ['用户', '动作', '详情', 'IP', '时间']), rows));
    } catch (e) { body.appendChild(loadError(e.message || '记录加载失败', function () { loadAudit(kind); })); }
    finally { loading.classList.add('hidden'); }
  }

  async function navigateAdmin(route, push, focus) {
    route = adminRouteAliases[route] || route;
    var page = Object.hasOwn(adminRoutes, route) ? adminRoutes[route] : null;
    if (!page || (page.tab !== 'docs' && currentUser.role !== 'root')) {
      route = currentUser.role === 'root' ? 'overview' : 'docs/list';
      page = adminRoutes[route];
    }
    var hash = '#' + route;
    if (location.hash !== hash) {
      history[push ? 'pushState' : 'replaceState'](null, '', location.pathname + location.search + hash);
    }
    // Back/forward can dispatch both popstate and hashchange for one transition.
    if (activeAdminRoute === route) { return; }
    var previous = activeAdminRoute && adminRoutes[activeAdminRoute];
    activeAdminRoute = route;
    qsa('[data-tab]').forEach(function (button) {
      var active = button.dataset.tab === page.tab;
      button.classList.toggle('is-active', active); button.setAttribute('aria-selected', String(active));
    });
    qsa('[data-panel]').forEach(function (panel) { panel.classList.toggle('is-active', panel.dataset.panel === page.tab); });
    if (page.tab === 'docs') {
      lastDocumentRoute = route; showDocumentView(page.view, focus);
      if (page.view === 'documentList' || !previous || previous.tab !== 'docs') { await loadDocs(); }
    } else if (page.tab === 'audit') {
      lastAuditRoute = route;
      qsa('[data-subtab]').forEach(function (button) {
        var active = button.dataset.subtab === page.view;
        button.classList.toggle('is-active', active); button.setAttribute('aria-selected', String(active));
      });
      qsa('.subtab-panel').forEach(function (panel) { panel.classList.toggle('is-active', panel.id === 'subpanel-' + page.view); });
      await loadAudit(page.view);
    } else { await loadOverview(); }
  }
  function restoreAdminNavigation() { return navigateAdmin(location.hash.slice(1), false, false); }

  function bindTabs() {
    qsa('[data-doc-view]').forEach(function (link) {
      link.addEventListener('click', function (event) { event.preventDefault(); navigateAdmin(link.dataset.docView, true, true); });
    });
    qsa('[data-tab]').forEach(function (button) {
      button.addEventListener('click', function () {
        var route = button.dataset.tab === 'docs' ? lastDocumentRoute : button.dataset.tab === 'audit' ? lastAuditRoute : 'overview';
        navigateAdmin(route, true, false);
      });
    });
    qsa('[data-subtab]').forEach(function (button) {
      button.addEventListener('click', function () { navigateAdmin(button.dataset.subtab, true, false); });
    });
    window.addEventListener('popstate', restoreAdminNavigation);
    window.addEventListener('hashchange', restoreAdminNavigation);
  }
  function bindForms() {
    bindImageTextForm();
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
    qs('#docFilterName').addEventListener('input', filterDocuments);
    qs('#docFilterDevice').addEventListener('change', function () { loadDocs(); });
    if (qs('#docFilterVersion')) qs('#docFilterVersion').addEventListener('keydown', function (e) { if (e.key === 'Enter') { loadDocs(); } });
    qs('#refreshChatsBtn').addEventListener('click', function () { loadAudit('chats'); });
    qs('#refreshFeedbackBtn').addEventListener('click', function () { loadAudit('feedback'); });
    qs('#feedbackFilterStatus').addEventListener('change', function () {loadAudit('feedback');});
    qs('#refreshLogsBtn').addEventListener('click', function () { loadAudit('logs'); });
  }

  async function boot() {
    wireGlobalErrors();
    var me = await initSession(); if (!me) { return; }
    if (me.role !== 'root' && me.role !== 'kb_admin') { location.href = '/app'; return; }
    currentUser = me;
    if (me.role !== 'root') {
      qsa('.root-only').forEach(function (el) { el.hidden = true; el.classList.remove('is-active'); });
    }
    bindTabs(); bindForms(); registerSW();
    qs('#docToday').textContent = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
    await restoreAdminNavigation();
  }
  document.addEventListener('DOMContentLoaded', function () {
    boot().catch(function (e) { toast(e.message || '管理页启动失败', 'error'); });
  });
}());
