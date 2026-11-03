/* Log upload options and saved original-file metadata. */
'use strict';
(function () {
  function metadata(value) {
    if (typeof value === 'string') {try {value = JSON.parse(value);} catch (e) {return null;}}
    return value && value.format === 1 && value.kind === 'log' ? value : null;
  }
  function notes(value) {
    var meta = metadata(value);
    if (!meta) {return null;}
    return h('div', {class:'small muted',style:'white-space:normal;overflow-wrap:anywhere'}, [
      '日志原件 · 编码：' + (meta.encoding || '未记录') + ' · ' +
      (Number.isSafeInteger(meta.line_count) && meta.line_count >= 0 ? meta.line_count + ' 行' : '行数未记录')
    ]);
  }
  window.LogImportUI = {metadata:metadata,notes:notes};
}());
