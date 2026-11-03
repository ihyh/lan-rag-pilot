/* PDF/image OCR upload/review. Raw OCR text is always rendered as text nodes. */
'use strict';
(function () {
  var timer = null;
  var labels = {queued:'排队中',running:'识别中',review:'等待人工核对',ready:'已确认入库',failed:'处理失败'};
  function selected(file) {
    if (/\.(png|jpe?g)$/i.test(file.name || '')) {return true;}
    var box = document.getElementById('docUseOcr');
    return Boolean(box && box.checked && !box.disabled && /\.pdf$/i.test(file.name || ''));
  }
  function schedule(documents, reload) {
    if (timer) {clearTimeout(timer); timer = null;}
    if (documents.some(function (doc) {return doc.ocr_state === 'queued' || doc.ocr_state === 'running';})) {
      timer = setTimeout(reload, 2500);
    }
  }
  async function open(doc, reload) {
    var body = h('div', {}, [h('p', {}, ['正在读取OCR任务…'])]);
    var footer = h('div', {class:'modal-foot'});
    var modal = openModal({title:'OCR 核对 · ' + doc.filename,body:body,footer:footer});
    modal.modal.classList.add('ocr-review');
    try {
      var job = await api('/api/admin/documents/' + doc.id + '/ocr');
      if (!modal.el.parentNode) {return;}
      var isImage = job.source_kind === 'image';
      var originalUrl = '/api/documents/' + doc.id + '/file';
      clear(body);
      body.appendChild(h('p', {class:'quality-warning'}, [
        '任务状态：' + (labels[job.state] || job.state) + '。请对照原件核对数字、单位、顺序与表格；识别置信度不代表答案正确率。'
      ]));
      body.appendChild(h('a', {href:originalUrl,target:'_blank',rel:'noopener noreferrer'}, [isImage ? '打开原图核对 ↗' : '打开原PDF核对 ↗']));
      if (isImage) {
        body.appendChild(h('p', {class:'quality-warning'}, [job.caption_only ? '仅人工图片说明已入库，OCR 文字未入库；请对照原图核对说明。' : job.indexed_unreviewed ? '图片文字已入库，尚未经人工核对；图表关系和阅读顺序未验证。请对照原图核对。' : job.state === 'ready' ? '图片文字已核对入库；图表关系和阅读顺序仍未验证。' : '图片文字、图表关系未验证。请对照原图核对；即使引擎标记可用，也需手动勾选确认。']));
        if (!job.caption_only) {body.appendChild(h('p', {class:'small muted'}, ['OCR 与原图一致不代表图中标注正确；图中标注有误时，请在图片说明中写明错误及更正。入库后可在资料管理中编辑图片说明。']));}
        body.appendChild(h('img', {src:originalUrl,alt:'待核对原图',style:'display:block;max-width:100%;max-height:60vh;object-fit:contain;margin:12px 0'}));
      }
      if (job.error) {body.appendChild(h('p', {class:'quality-warning'}, [job.error]));}
      var boxes = [];
      (job.pages || []).forEach(function (page) {
        var hasText = Boolean((page.text || '').trim());
        var pageLabel = {ok:'可用文字',suspect:'可疑，需确认',failed:'失败，不入库'}[page.status] || '未知';
        if (isImage && !hasText) {pageLabel = '无可用文字';}
        var section = h('details', {class:'ocr-page'}, [h('summary', {}, [(isImage ? '原图' : '第 ' + page.page + ' 页') + ' · ' + pageLabel])]);
        if (isImage) {section.open = true;}
        if (page.status === 'suspect' && (!isImage || hasText) || isImage && page.status === 'ok' && hasText) {
          var box = h('input', {type:'checkbox',value:String(page.page)});
          box.checked = job.state === 'ready' && (job.accepted_pages || []).indexOf(page.page) !== -1;
          box.disabled = job.state !== 'review';
          boxes.push(box);
          section.appendChild(h('label', {class:'check-item'}, [box, isImage ? '我已确认识别文字与原图一致，同意纳入索引；图中标注及图表关系仍需核实' : '我已核对此页原件，同意纳入索引']));
        }
        if (page.status === 'failed' || isImage && !hasText) {section.appendChild(h('p', {class:'quality-warning'}, [isImage ? (job.state === 'review' ? '本图没有可确认的文字；可以重试识别，或对照原图填写说明入库。' : '本图没有可确认的文字。') : '本页不进入索引，可以先重试失败页。']));}
        if (page.warnings && page.warnings.length) {section.appendChild(h('p', {class:'small muted'}, [isImage ? '存在识别提醒，请重点核对原图。' : '存在识别提醒，请重点检查本页。']));}
        if (page.confidence && typeof page.confidence.mean === 'number') {
          section.appendChild(h('p', {class:'small muted'}, ['引擎行均值：' + page.confidence.mean.toFixed(3) + '（非准确率）']));
        }
        section.appendChild(h('pre', {class:'quality-text'}, [page.text || '没有可显示的识别正文']));
        body.appendChild(section);
      });
      var error = h('p', {class:'quality-warning hidden',role:'alert'}); body.appendChild(error);
      function failed(e) {error.classList.remove('hidden'); error.textContent = e.message || '操作失败，请重试';}
      if (!job.caption_only && (job.state === 'failed' || (job.pages || []).some(function (p) {return p.status === 'failed';}))) {
        var retry = h('button', {class:'btn btn-outline',type:'button'}, [isImage ? '重试图片识别' : '重试失败页']);
        retry.addEventListener('click', async function () {
          busy(retry,true,'提交中…');
          try {await api('/api/admin/documents/' + doc.id + '/ocr/retry',{method:'POST',body:{}}); modal.close(); await reload();}
          catch (e) {failed(e);} finally {busy(retry,false);}
        });
        footer.appendChild(retry);
      }
      var hasReviewableImageText = (job.pages || []).some(function (page) {
        return (page.status === 'ok' || page.status === 'suspect') && Boolean((page.text || '').trim());
      });
      var canPublishCaption = isImage && job.state === 'review' && !hasReviewableImageText;
      if (canPublishCaption) {
        var caption = h('textarea', {class:'input',rows:6,maxLength:2000, 'aria-label':'人工图片说明'});
        caption.value = job.caption || '';
        var confirmed = h('input', {type:'checkbox'});
        body.appendChild(h('p', {class:'quality-warning'}, ['识别未得到可核对的文字。请填写图片内容、连接关系及错误标注的更正；只有人工说明会进入检索，OCR 文字不会入库。']));
        body.appendChild(caption);
        body.appendChild(h('label', {class:'check-item'}, [confirmed,'我已对照原图核对上述人工说明，同意仅将说明入库']));
        var publishCaption = h('button', {class:'btn btn-primary',type:'button'}, ['确认说明并入库']);
        publishCaption.addEventListener('click', async function () {
          if (!caption.value.trim()) {failed(new Error('请先填写人工图片说明。')); return;}
          if (!confirmed.checked) {failed(new Error('请先对照原图核对并勾选确认。')); return;}
          busy(publishCaption,true,'正在入库…');
          try {
            await api('/api/admin/documents/' + doc.id + '/ocr/publish-caption',{method:'POST',body:{
              caption:caption.value.trim(), review_token:job.review_token || null, confirmed:true
            }});
            modal.close(); toast('人工说明已入库；OCR 文字未入库','success'); await reload();
          } catch (e) {failed(e);} finally {busy(publishCaption,false);}
        });
        footer.appendChild(publishCaption);
      }
      if (job.state === 'review' && !canPublishCaption) {
        var acknowledge = h('input', {type:'checkbox'});
        if (!isImage) {body.appendChild(h('label', {class:'check-item'}, [acknowledge,'若存在失败页或未勾选的可疑页，我确认这些页不会入库，问答资料可能不完整。']));}
        var publish = h('button', {class:'btn btn-primary',type:'button'}, [isImage ? (job.indexed_unreviewed ? '确认图片文字已核对' : '确认图片文字并入库') : '确认所选页并入库']);
        publish.addEventListener('click', async function () {
          if (isImage && !boxes.some(function (box) {return box.checked;})) {
            failed(new Error('请先对照原图核对并勾选识别文字；失败或空文字不能入库。')); return;
          }
          busy(publish,true,'正在入库…');
          try {
            var payload = {
              accepted_suspect_pages:boxes.filter(function (box) {return box.checked;}).map(function (box) {return Number(box.value);}),
              acknowledge_excluded_pages:acknowledge.checked
            };
            payload.review_token = job.review_token;
            await api('/api/admin/documents/' + doc.id + '/ocr/publish',{method:'POST',body:payload});
            modal.close(); toast(isImage ? '已确认图片文字入库；图表关系仍未验证' : '已确认页面入库；排除页不会参与问答','success'); await reload();
          } catch (e) {failed(e);} finally {busy(publish,false);}
        });
        footer.appendChild(publish);
      }
      var close = h('button', {class:'btn btn-outline',type:'button'}, ['关闭']);
      close.addEventListener('click',modal.close); footer.appendChild(close);
    } catch (e) {clear(body); body.appendChild(h('p',{class:'quality-warning'},['OCR结果读取失败：' + e.message]));}
  }
  async function boot() {
    var box = document.getElementById('docUseOcr'), hint = document.getElementById('ocrAvailability');
    if (!box || !hint) {return;}
    try {
      var state = await api('/api/admin/ocr/status'); box.disabled = !state.available;
      hint.textContent = state.available ? '图片和扫描 PDF 的识别结果需要人工核对；添加图片说明请使用“图文资料”。' : 'OCR 暂不可用，图片识别和扫描 PDF 处理不可用；添加图片说明请使用“图文资料”。' + (state.message ? ' ' + state.message : '');
    } catch (e) {box.disabled = true; hint.textContent = 'OCR 状态读取失败：' + e.message;}
  }
  window.OCRUI = {open:open,selected:selected,schedule:schedule};
  document.addEventListener('DOMContentLoaded',boot);
})();
