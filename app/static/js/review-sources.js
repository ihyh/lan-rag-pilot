/* Human-selected indexed evidence; model citations are never selected by default. */
'use strict';
(function () {
  function pick(existing) {
    return new Promise(function (resolve) {
      var chosen = Object.create(null), closed = false;
      (existing || []).forEach(function (source) {chosen[source.document_id + ':' + source.chunk_id] = Object.assign({},source);});
      var query = h('input',{class:'input',maxlength:200,placeholder:'输入正确原文关键词（字面检索）','aria-label':'查找正确来源'});
      var historical = h('input',{type:'checkbox'});
      var search = h('button',{class:'btn btn-outline',type:'submit'},['查找原文']);
      var form = h('form',{},[query,h('label',{class:'check-item'},[historical,'包含历史资料（只用于诊断，不可普通复测）']),search]);
      var selected = h('div'),results = h('div'),message = h('p',{class:'small muted',role:'status'});
      var body = h('div',{class:'review-source-picker'},[h('p',{class:'small muted'},['请核对原件后勾选支持答案的片段；可在勾选后保留连续逐字原文。']),form,selected,message,results]);
      var footer = h('div',{class:'modal-foot'});
      var cancel = h('button',{class:'btn btn-outline',type:'button'},['取消']);
      var accept = h('button',{class:'btn btn-primary',type:'button'},['使用所选来源']);
      footer.appendChild(cancel);footer.appendChild(accept);
      var modal = openModal({title:'人工选择正确来源',body:body,footer:footer,onClose:function () {closed=true;resolve(null);}});
      modal.modal.classList.add('feedback-review');
      function renderSelected() {
        clear(selected);
        selected.appendChild(h('p',{class:'small muted'},['已选 ' + Object.keys(chosen).length + ' 条（最多20条）；旧选择如已变动，请移除后重选。']));
        Object.keys(chosen).forEach(function (key) {
          var source = chosen[key];
          var remove = h('button',{class:'btn btn-sm btn-outline',type:'button'},['移除']);
          remove.addEventListener('click',function () {delete chosen[key]; renderSelected(); clear(results);});
          selected.appendChild(h('div',{class:'quality-chunk'},[h('span',{},[source.filename + ' · 片段ID ' + source.chunk_id + ' · ' + source.excerpt.slice(0,100)]),remove]));
        });
      }
      form.addEventListener('submit',async function (event) {
        event.preventDefault(); var text = query.value.trim();
        if (!text) {message.textContent='请先填写原文关键词';return;}
        busy(search,true,'查找中…');clear(results);
        try {
          var data = await api('/api/library/search',{method:'POST',body:{query:text,limit:20,include_archived:historical.checked}});
          if (closed) {return;}
          message.textContent='命中 ' + data.total + ' 条，显示前 ' + data.items.length + ' 条；可细化关键词。';
          data.items.forEach(function (item) {
            var key = item.document_id + ':' + item.chunk_id;
            var checkbox = h('input',{type:'checkbox','aria-label':'选择片段 ' + item.chunk_id}); checkbox.checked=Boolean(chosen[key]);
            var excerpt = h('textarea',{class:'input',rows:6,maxlength:8000,'aria-label':'逐字正确原文'});
            excerpt.value=chosen[key] ? chosen[key].excerpt : item.content;
            var editor = h('label',{class:'review-source-editor' + (checkbox.checked ? '' : ' hidden')},[
              h('span',{class:'field-label'},['所选原文：可删减为一段连续原文，不要改写']),excerpt]);
            function capture() {
              chosen[key]={document_id:item.document_id,chunk_id:item.chunk_id,excerpt:excerpt.value,
                sha256:item.sha256,version:item.version,lifecycle:item.lifecycle,applicability:item.applicability,
                chunk_content_sha256:item.chunk_content_sha256,filename:item.filename,page:item.page,paragraph:item.paragraph,
                line_start:item.line_start,line_end:item.line_end};
            }
            checkbox.addEventListener('change',function () {
              if (checkbox.checked) {capture();} else {delete chosen[key];}
              editor.classList.toggle('hidden',!checkbox.checked);
              renderSelected();
            });
            excerpt.addEventListener('input',function () {if (checkbox.checked) {capture();renderSelected();}});
            var location = item.page ? '第' + item.page + '页' : '片段起始段落 ' + (item.paragraph || '未知');
            var isLog = item.source_metadata && item.source_metadata.kind === 'log';
            if (item.source_type === 'image' && !item.page && !item.paragraph) {location = '图片资料（无页码）';}
            if (isLog) {
              location = Number.isSafeInteger(item.line_start) && item.line_start > 0
                && Number.isSafeInteger(item.line_end) && item.line_end >= item.line_start
                ? '日志第 ' + item.line_start + (item.line_end === item.line_start ? '' : '–' + item.line_end) + ' 行'
                : '日志行号未记录';
            }
            var match = [];
            (data.terms || text.split(/\s+/).slice(0,8)).forEach(function (term) {
              var matchAt = item.content.toLowerCase().indexOf(term.toLowerCase());
              if (matchAt < 0) { return; }
              var begin = Math.max(0,matchAt - 100), end = Math.min(item.content.length,matchAt + term.length + 100);
              match.push(h('div',{class:'review-source-match'},[
                h('span',{class:'field-label'},['关键词「' + term + '」附近']),
                (begin ? '…' : '') + item.content.slice(begin,matchAt),
                h('mark',{},[item.content.slice(matchAt,matchAt + term.length)]),
                item.content.slice(matchAt + term.length,end) + (end < item.content.length ? '…' : '')]));
            });
            results.appendChild(h('article',{class:'quality-chunk review-source-result'},[
              h('label',{class:'check-item review-source-heading'},[checkbox,h('span',{},[item.filename + ' · ' + location + ' · 版本 ' + item.version + ' · 片段ID ' + item.chunk_id + (item.lifecycle === 'archived' ? ' · 历史资料' : '')])]),
              h('div',{class:'review-source-content'},match.concat([
                h('span',{class:'field-label'},['完整片段（可滚动查看）']),
                h('pre',{class:'quality-text review-source-text' + (isLog ? ' log-text' : ''),tabindex:'0'},[item.content]),editor,
                h('a',{href:'/library?document_id=' + item.document_id,target:'_blank',rel:'noopener noreferrer'},['对照资料检查 ↗'])]))]));
          });
        } catch (error) {message.textContent='原文检索失败：' + error.message;}
        finally {busy(search,false);}
      });
      cancel.addEventListener('click',modal.close);
      accept.addEventListener('click',function () {
        var items=Object.keys(chosen).map(function (key) {return chosen[key];});
        if (items.length>20 || items.some(function (item) {return !item.excerpt.trim() || Array.from(item.excerpt).length>8000;})) {
          message.textContent='最多选择20条，每条需有1–8000字逐字原文';return;
        }
        resolve(items);modal.close();
      });
      renderSelected();
    });
  }
  window.ReviewSources={pick:pick};
})();
