/* Prepared webpage images: explicit per-image review through the existing OCR UI. */
'use strict';
(function () {
  function validId(value) { return Number.isSafeInteger(value) && value > 0; }
  function articleUrl(value) {
    return typeof value === 'string' && /^https:\/\/www\.cnblogs\.com\/wwwen\/p\/(16044077|16048873|16053090|16065818)\.html$/.test(value) ? value : null;
  }
  function text(value, fallback) { return typeof value === 'string' && value ? value : fallback; }
  function stateLabel(item) {
    if (item.state === 'duplicate') { return '相同图片已有资料，请在文档列表检查'; }
    if (!validId(item.document_id)) { return '尚未入库'; }
    if (item.indexed && item.human_review_pending) { return '已入库 · 待核对'; }
    var states = {queued:'排队中',running:'识别中',review:'待核对',ready:'已入库',failed:'识别失败'};
    return Object.prototype.hasOwnProperty.call(states, item.state) ? states[item.state] : '待核对';
  }
  async function mount(host, reload) {
    if (!host) { return; }
    if (host.webImagesController) {
      host.webImagesController.reload = reload;
      return host.webImagesController.load();
    }
    var controller = {reload:reload}, items = [], pending = null, selected = '', page = 1;
    var pageSize = 20;
    var filter = h('select', {class:'input','aria-label':'按所属文章筛选网页图片'});
    var refresh = h('button', {class:'btn btn-outline btn-sm',type:'button'}, ['刷新图片列表']);
    var status = h('p', {class:'small muted',role:'status','aria-live':'polite'});
    var list = h('div', {class:'web-images-list'});
    var previous = h('button', {class:'btn btn-outline btn-sm',type:'button'}, ['上一页']);
    var next = h('button', {class:'btn btn-outline btn-sm',type:'button'}, ['下一页']);
    var pageStatus = h('span', {class:'small muted',role:'status','aria-live':'polite'});
    var pager = h('div', {class:'web-image-actions'}, [previous,pageStatus,next]);
    host.appendChild(h('div', {class:'web-images-tools'}, [
      h('label', {class:'field'}, [h('span', {class:'field-label'}, ['所属文章']),filter]),refresh
    ]));
    host.appendChild(status);
    host.appendChild(list);
    host.appendChild(pager);

    function filteredItems() { return items.filter(function (item) { return !selected || item.article_url === selected; }); }
    function currentPageItems() { return filteredItems().slice((page - 1) * pageSize, page * pageSize); }
    function render() {
      clear(list);
      var filtered = filteredItems(), pages = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.min(page, pages);
      var visible = currentPageItems();
      status.textContent = '当前筛选共 ' + filtered.length + ' 张，本页显示 ' + visible.length + ' 张。可对照原图核对文字。';
      pageStatus.textContent = '第 ' + page + ' / ' + pages + ' 页';
      previous.disabled = page <= 1; next.disabled = page >= pages;
      pager.hidden = filtered.length <= pageSize;
      if (!visible.length) {
        list.appendChild(h('p', {class:'small muted'}, ['暂无可核对的网页图片。已上传的独立图片仍可在文档列表核对。']));
      }
      visible.forEach(function (item) {
        var name = '图片 ' + (Number.isSafeInteger(item.image_number) ? item.image_number : item.id);
        var preview = h('div', {class:'web-image-preview'});
        if (item.original_url === '/api/admin/web-images/' + item.id + '/original') {
          preview.appendChild(h('img', {src:item.original_url,alt:name + '原图',loading:'lazy',decoding:'async'}));
        } else { preview.appendChild(h('span', {class:'small muted'}, ['原图暂不可用'])); }
        var content = h('div', {class:'web-image-content'}, [
          h('h3', {}, [name + ' · ' + text(item.title, '网页图片')]),
          h('p', {class:'small muted'}, [text(item.section, '未记录章节')])
        ]);
        var source = articleUrl(item.article_url);
        if (source) { content.appendChild(h('a', {href:source,target:'_blank',rel:'noopener noreferrer',class:'small'}, ['查看所属原网页 ↗'])); }
        if (typeof item.source_captured_at === 'string') {
          content.appendChild(h('p', {class:'small muted'}, ['图片来源保存时间：' + fmtTime(item.source_captured_at)]));
        }
        var label = stateLabel(item);
        content.appendChild(h('p', {class:label === '已入库' ? 'small muted' : 'small doc-ocr-warn'}, [label]));
        if (item.ocr_status === 'suspect') { content.appendChild(h('p', {class:'small doc-ocr-warn'}, ['识别存在疑点，请重点核对文字。'])); }
        var actionText = item.state === 'ready' && validId(item.document_id) ? '查看已入库文字' : '核对图片文字';
        var button = h('button', {class:'btn btn-outline btn-sm',type:'button'}, [actionText]);
        button.disabled = item.state === 'duplicate';
        var error = h('p', {class:'small web-image-error',role:'alert'});
        var working = false;
        button.addEventListener('click', async function () {
          if (working || item.state === 'duplicate') { return; }
          error.textContent = '';
          if (!window.OCRUI) { error.textContent = '图片核对组件未加载，请刷新页面。'; return; }
          var documentId = item.document_id, filename = text(item.filename, name);
          var version = document.getElementById('docVersion');
          if (!validId(documentId) && (!version || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/.test(version.value.trim()))) {
            error.textContent = '请先填写有效的文档版本。'; if (version) { version.focus(); } return;
          }
          working = true; busy(button, true, '正在读取…');
          try {
            if (!validId(documentId)) {
              var prepared = await api('/api/admin/web-images/' + item.id + '/prepare', {method:'POST',body:{version:version.value.trim()}});
              if (!validId(prepared.document_id)) { throw new Error('图片准备结果缺少文档编号，请刷新列表。'); }
              documentId = item.document_id = prepared.document_id;
              filename = item.filename = text(prepared.filename, filename);
              item.state = prepared.state || 'review';
            }
            // loadDocs refreshes this catalog in its finally block; no second timer.
            await window.OCRUI.open({id:documentId,filename:filename}, controller.reload);
          } catch (e) { error.textContent = e.message || '图片读取失败，请重试。'; }
          finally { working = false; busy(button, false); }
        });
        content.appendChild(h('div', {class:'web-image-actions'}, [button]));
        if (item.state === 'duplicate') {
          content.appendChild(h('a', {href:'#documentList',class:'small'}, ['查看文档列表']));
        }
        content.appendChild(error);
        list.appendChild(h('article', {class:'web-image-card'}, [preview,content]));
      });
    }
    async function load() {
      if (pending) { return pending; }
      refresh.disabled = true; status.textContent = '正在读取网页图片…';
      pending = (async function () {
        try {
          var data = await api('/api/admin/web-images');
          items = data.available === false ? [] : (Array.isArray(data.items) ? data.items.filter(function (item) {return item && validId(item.id);}) : []);
          clear(filter); filter.appendChild(h('option', {value:''}, ['全部文章']));
          var articles = [];
          items.forEach(function (item) {
            if (typeof item.article_url !== 'string' || articles.indexOf(item.article_url) !== -1) { return; }
            articles.push(item.article_url);
            filter.appendChild(h('option', {value:item.article_url}, [text(item.title, '未命名文章')]));
          });
          if (articles.indexOf(selected) === -1) { selected = ''; }
          filter.value = selected;
          render();
          if (data.available === false) { status.textContent = '网页图片尚未接入。接入后可在这里逐张查看、核对并入库。'; }
        } catch (e) { status.textContent = '网页图片读取失败，请重试：' + (e.message || '未知错误'); }
        finally { pending = null; refresh.disabled = false; }
      }());
      return pending;
    }
    filter.addEventListener('change', function () { selected = filter.value; page = 1; render(); });
    previous.addEventListener('click', function () { if (page > 1) {page--; render();} });
    next.addEventListener('click', function () { if (page < Math.ceil(filteredItems().length / pageSize)) {page++; render();} });
    refresh.addEventListener('click', load);
    controller.load = load;
    host.webImagesController = controller;
    return load();
  }
  window.WebImagesUI = {mount:mount};
}());
