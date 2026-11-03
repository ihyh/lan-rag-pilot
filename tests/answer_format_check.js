'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function h(tag, attrs, children) {
  assert.ok(!attrs || !('html' in attrs), 'answer content must never use innerHTML');
  return {tag, attrs: attrs || {}, children: children || [], events: {},
    appendChild(child) { this.children.push(child); },
    addEventListener(name, handler) { this.events[name] = handler; }};
}
const context = vm.createContext({h, document: {addEventListener() {}, createTextNode: text => String(text)}});
for (const name of ['app.js', 'answer-format.js']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/js/', name), 'utf8'), context);
}
const sources = [{document_id: 10}, {document_id: 11}];
const flat = node => typeof node === 'string' ? [] : [node, ...node.children.flatMap(flat)];
const text = node => typeof node === 'string' ? node : node.children.map(text).join('');
const tags = (node, tag) => flat(node).filter(n => n.tag === tag);
let clicked = [];
let answer = '## 核对结果\r\n\r\n**本次记录**：`HCS E84_MODE AUTO<CR><LF>`。[1]\r\n\r\n> 仅说明该次记录。[2]';
let rendered = context.renderAnswerContent(answer, sources, id => clicked.push(id));
assert.equal(text(tags(rendered, 'h2')[0]), '核对结果');
assert.equal(text(tags(rendered, 'strong')[0]), '本次记录');
assert.equal(text(tags(rendered, 'code')[0]), 'HCS E84_MODE AUTO<CR><LF>');
assert.equal(tags(rendered, 'blockquote').length, 1);
tags(rendered, 'button').forEach(b => b.events.click());
assert.deepEqual(clicked, [1, 2], 'formatting must retain original citation offsets and targets');

answer = '① 保存原参数\n② 对照实际位置\n  - 读取数据\n  - **核对**状态 [2]\n3. 记录结果';
rendered = context.renderAnswerContent(answer, sources, () => {});
assert.equal(tags(rendered, 'ol').length, 1);
assert.equal(tags(rendered, 'ul').length, 1);
assert.deepEqual(tags(rendered, 'li').filter(n => 'value' in n.attrs).map(n => n.attrs.value), [1, 2, 3]);
assert.equal(tags(rendered, 'button')[0].attrs['aria-label'], '查看引用来源 2');
assert.equal(tags(context.renderAnswerContent('1.2 是版本号', [], () => {}), 'li').length, 0);

const packet = '<L[2]\n  <U1[1] 2>\n  <A[25] "Can NOT be performed now.">\n>';
rendered = context.renderAnswerContent('```text\n' + packet + '\n```\n\n实际记录。[2]', sources, () => {});
assert.equal(text(tags(rendered, 'code')[0]), packet);
assert.equal(tags(rendered, 'button').filter(b => b.attrs.class === 'citation-link').length, 1, 'SECS array lengths are not references');
assert.equal(text(tags(context.renderAnswerContent('```\nunfinished <CR>', [], () => {}), 'code')[0]), 'unfinished <CR>');
rendered = context.renderAnswerContent('`[1]` L[2] U1[1] https://example.test/[1] \\[2] [99] **依据[2]**', sources, () => {});
assert.equal(tags(rendered, 'button').length, 1);
assert.equal(text(tags(rendered, 'button')[0]), '[2]');

rendered = context.renderAnswerContent('| 项目 | 记录 |\n| --- | --- |\n| 指令 | `A|B` [1] |\n| 状态 | **待核对** |', sources, () => {});
assert.equal(tags(rendered, 'table').length, 1);
assert.equal(tags(rendered, 'th').length, 2);
assert.equal(tags(rendered, 'td').length, 4);
assert.equal(text(tags(rendered, 'code')[0]), 'A|B');
assert.equal(text(tags(rendered, 'button')[0]), '[1]');

const untrusted = '<script>alert(1)</script> <img src=x onerror=alert(1)> [链接](javascript:alert(1))';
rendered = context.renderAnswerContent(untrusted, sources, () => {});
assert.equal(text(rendered), untrusted);
assert.ok(!flat(rendered).some(n => ['script', 'img', 'a'].includes(n.tag)), 'no active HTML or arbitrary URLs');
assert.equal(tags(context.renderAnswerContent('来源 [1,2] [0] [3]', sources, () => {}), 'button').length, 2);
console.log('Answer formatting PASS: structure, raw commands, citations, tables, HTML safety');
