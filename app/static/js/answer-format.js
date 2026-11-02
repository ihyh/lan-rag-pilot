/* Answer typography only: build DOM nodes, never interpret model text as HTML. */
'use strict';

function renderAnswerContent(answer, sources, openSource) {
  var text = String(answer || '').replace(/\r\n?/g, '\n');
  var root = h('div', {class: 'answer-content'}), citations = new Map();
  citationSpans(text, sources).forEach(function (span) { citations.set(span.start, span); });
  var offset = 0;
  var lines = text.split('\n').map(function (value) {
    var line = {text: value, offset: offset}; offset += value.length + 1; return line;
  });

  function inline(parent, value, base, depth) {
    var pattern = /(`+)([^`]*?)\1(?!`)|\*\*([^\n]+?)\*\*|\[\d+(?:\s*[,，、]\s*\d+)*\]/g;
    var cursor = 0, match;
    while ((match = pattern.exec(value))) {
      parent.appendChild(document.createTextNode(value.slice(cursor, match.index)));
      var span = citations.get(base + match.index);
      if (match[1]) {
        parent.appendChild(h('code', null, [match[2]]));
      } else if (match[3] && depth < 6) {
        var strong = h('strong');
        inline(strong, match[3], base + match.index + 2, depth + 1);
        parent.appendChild(strong);
      } else if (span && span.end === base + pattern.lastIndex) {
        span.numbers.forEach(function (number) {
          var button = h('button', {class: 'citation-link', type: 'button',
            'aria-label': '查看引用来源 ' + number, title: '查看引用来源 ' + number}, ['[' + number + ']']);
          button.addEventListener('click', function () { openSource(number); });
          parent.appendChild(button);
        });
      } else {
        parent.appendChild(document.createTextNode(match[0]));
      }
      cursor = pattern.lastIndex;
    }
    parent.appendChild(document.createTextNode(value.slice(cursor)));
  }
  function paragraph(parent, value, base) {
    var p = h('p'); inline(p, value, base, 0); parent.appendChild(p);
  }
  function listItem(line) {
    var m = /^(\s*)(?:([-+*])\s+|(\d{1,6})[.)]\s+|(\d{1,6})、\s*|([①-⑳])\s*)(.+)$/.exec(line.text);
    return m && {indent: m[1].length, kind: m[2] ? 'ul' : 'ol',
      number: m[5] ? m[5].charCodeAt(0) - 0x2460 + 1 : Number(m[3] || m[4]),
      value: m[6], offset: line.offset + m[0].length - m[6].length};
  }
  function tableCells(line) {
    if (!line || line.text.indexOf('|') === -1) { return null; }
    var cells = [], start = 0, code = '', pattern = /`+|\\\||\|/g, m;
    function cell(end) {
      var raw = line.text.slice(start, end), leading = raw.length - raw.trimStart().length;
      cells.push({value: raw.trim(), offset: line.offset + start + leading}); start = end + 1;
    }
    while ((m = pattern.exec(line.text))) {
      if (m[0][0] === '`') { if (!code) { code = m[0]; } else if (code === m[0]) { code = ''; } }
      else if (m[0] === '|' && !code) { cell(m.index); }
    }
    if (!cells.length) { return null; }
    cell(line.text.length);
    if (cells[0].value === '') { cells.shift(); }
    if (cells.length && cells[cells.length - 1].value === '') { cells.pop(); }
    return cells.length > 1 ? cells : null;
  }
  function tableStart(index) {
    var head = tableCells(lines[index]), rule = tableCells(lines[index + 1]);
    return head && rule && head.length === rule.length && rule.every(function (cell) {
      return /^:?-{3,}:?$/.test(cell.value);
    }) ? head : null;
  }
  function startsBlock(index) {
    return /^\s*$|^ {0,3}(?:#{1,6}\s|`{3,}|~{3,}|>\s?)/.test(lines[index].text)
      || listItem(lines[index]) || tableStart(index);
  }

  for (var i = 0; i < lines.length;) {
    var line = lines[i], fence = /^ {0,3}(`{3,}|~{3,})([^`]*)$/.exec(line.text);
    if (!line.text.trim()) { i++; continue; }
    if (fence) {
      var codeLines = [], closing = new RegExp('^ {0,3}' + fence[1][0] + '{' + fence[1].length + ',}\\s*$');
      i++;
      while (i < lines.length && !closing.test(lines[i].text)) { codeLines.push(lines[i++].text); }
      if (i < lines.length) { i++; }
      root.appendChild(h('pre', {class: 'answer-code', tabindex: '0', 'aria-label': '指令或代码'}, [h('code', null, [codeLines.join('\n')])]));
      continue;
    }
    var heading = /^ {0,3}(#{1,6})\s+(.+)$/.exec(line.text);
    if (heading) {
      var title = h('h' + Math.max(2, Math.min(4, heading[1].length)));
      inline(title, heading[2], line.offset + line.text.indexOf(heading[2]), 0);
      root.appendChild(title); i++; continue;
    }
    if (/^ {0,3}>\s?/.test(line.text)) {
      var quote = h('blockquote');
      while (i < lines.length && /^ {0,3}>\s?/.test(lines[i].text)) {
        var prefix = /^ {0,3}>\s?/.exec(lines[i].text)[0].length;
        paragraph(quote, lines[i].text.slice(prefix), lines[i].offset + prefix); i++;
      }
      root.appendChild(quote); continue;
    }
    if (listItem(line)) {
      var stack = [];
      while (i < lines.length) {
        var item = listItem(lines[i]);
        if (!item) {
          if (stack.length && /^\s{2,}\S/.test(lines[i].text) && !startsBlock(i)) {
            paragraph(stack[stack.length - 1].last, lines[i].text.trimStart(), lines[i].offset + lines[i].text.length - lines[i].text.trimStart().length);
            i++; continue;
          }
          break;
        }
        while (stack.length && (item.indent < stack[stack.length - 1].indent
          || (item.indent === stack[stack.length - 1].indent && item.kind !== stack[stack.length - 1].kind))) { stack.pop(); }
        if (!stack.length || item.indent > stack[stack.length - 1].indent) {
          var list = h(item.kind);
          (stack.length ? stack[stack.length - 1].last : root).appendChild(list);
          stack.push({node: list, indent: item.indent, kind: item.kind, last: null});
        }
        var li = h('li', item.kind === 'ol' ? {value: item.number} : null);
        inline(li, item.value, item.offset, 0);
        stack[stack.length - 1].node.appendChild(li); stack[stack.length - 1].last = li; i++;
      }
      continue;
    }
    var headers = tableStart(i);
    if (headers) {
      var table = h('table'), head = h('tr'), body = h('tbody');
      headers.forEach(function (cell) { var th = h('th', {scope: 'col'}); inline(th, cell.value, cell.offset, 0); head.appendChild(th); });
      table.appendChild(h('thead', null, [head])); table.appendChild(body); i += 2;
      while (i < lines.length) {
        var cells = tableCells(lines[i]);
        if (!cells || cells.length !== headers.length) { break; }
        var tr = h('tr');
        cells.forEach(function (cell) { var td = h('td'); inline(td, cell.value, cell.offset, 0); tr.appendChild(td); });
        body.appendChild(tr); i++;
      }
      root.appendChild(h('div', {class: 'answer-table', tabindex: '0', role: 'region', 'aria-label': '回答中的表格'}, [table]));
      continue;
    }
    var first = i++;
    while (i < lines.length && !startsBlock(i)) { i++; }
    paragraph(root, lines.slice(first, i).map(function (entry) { return entry.text; }).join('\n'), lines[first].offset);
  }
  return root;
}
