/* 网页导入：只保留粘贴网址，导入正文快照。 */
'use strict';
(function () {
  function safeUrl(value) {
    return typeof value === 'string' && /^https?:\/\/[^\s\\/?#@]+(?:[/?#][^\s\\]*)?$/.test(value) ? value : null;
  }
  function metadata(value) {
    if (typeof value === 'string') { try {value = JSON.parse(value);} catch (e) {return null;} }
    return value && value.format === 1 && value.kind === 'web' ? value : null;
  }
  function warningLabel(value) {
    var labels = {image_text_not_extracted:'网页正文未提取图片文字',embedded_content_not_extracted:'嵌入内容尚未提取',table_spans_not_expanded:'合并单元格未展开，关系需核对'};
    return typeof labels[value] === 'string' ? labels[value] : '文档含未核对内容';
  }
  function notes(value) {
    var meta = metadata(value);
    if (!meta) {return null;}
    var box = h('div', {class:'small muted',style:'white-space:normal;overflow-wrap:anywhere'}, []);
    box.appendChild(h('div', {}, ['网页：' + (meta.title || '未记录标题')]));
    var url = safeUrl(meta.url);
    box.appendChild(url ? h('a', {href:url,target:'_blank',rel:'noopener noreferrer'}, [url]) : h('div', {}, ['来源网址：' + (meta.url || '未记录')]));
    box.appendChild(h('div', {}, ['采集时间：' + fmtTime(meta.fetched_at)]));
    var count = Number.isSafeInteger(meta.image_count) && meta.image_count >= 0 ? meta.image_count : '未知';
    box.appendChild(h('div', {class:meta.status === 'partial' ? 'doc-ocr-warn' : ''}, [
      '正文提取' + (meta.status === 'partial' ? '不完整' : '完成') + ' · 图片 ' + count + ' 张（正文未提取图片文字）'
    ]));
    (Array.isArray(meta.warnings) ? meta.warnings : []).forEach(function (warning) {
      if (typeof warning === 'string') {box.appendChild(h('div', {class:'doc-ocr-warn'}, [warningLabel(warning)]));}
    });
    return box;
  }
  function versionValue() {
    var input = document.getElementById('docVersion');
    var value = input && typeof input.value === 'string' ? input.value.trim() : '';
    return /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/.test(value) ? value : '1.0';
  }
  async function mount(host, reload) {
    if (!host || host.dataset.mounted) {return;}
    host.dataset.mounted = 'true';
    var urlInput = h('input', {class:'input',id:'webPageUrl',type:'url',required:true,maxlength:2048,placeholder:'https://…','aria-label':'网页网址'});
    var urlButton = h('button', {class:'btn btn-primary',type:'submit'}, ['导入网页']);
    var urlStatus = h('p', {class:'small muted',role:'status','aria-live':'polite'});
    var urlForm = h('form', {class:'web-url-form'}, [h('label', {for:'webPageUrl'}, ['粘贴网页网址']),urlInput,urlButton,urlStatus]);
    host.appendChild(urlForm);
    host.appendChild(h('p', {class:'small muted'}, ['导入公开网页的文字，支持UTF-8、单页不超过2MB。登录后内容、动态页面和图片不会自动采集。']));
    urlForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var url = (urlInput.value || '').trim();
      if (!safeUrl(url)) {urlStatus.textContent = '请粘贴完整的HTTP或HTTPS网页网址。';urlInput.focus();return;}
      urlInput.disabled = true;urlButton.disabled = true;
      urlStatus.textContent = '正在读取网页并保存正文，请稍候…';
      try {
        var doc = await api('/api/admin/web-import', {method:'POST',body:{url:url,version:versionValue()}});
        urlStatus.textContent = '已入库 #' + doc.id + '。图片未自动采集；如需图片文字，请下载后使用“上传文件”核对入库。';
        try {await reload();} catch (e) {urlStatus.textContent += ' 列表刷新失败：' + e.message;}
      } catch (e) {
        urlStatus.textContent = (e.status === 409 && e.data && e.data.detail && e.data.detail.code === 'duplicate' ? '已存在：' : '导入失败：') + e.message;
      } finally {urlInput.disabled = false;urlButton.disabled = false;}
    });
  }
  window.WebImportUI = {mount:mount,notes:notes,metadata:metadata,warningLabel:warningLabel};
}());
