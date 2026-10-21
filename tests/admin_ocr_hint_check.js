'use strict';

// OCR 可疑/失败页提示：纯解析函数 + 管理页接线静态检查（无浏览器依赖）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'app/static/js/common.js'), 'utf8');
const context = vm.createContext({ window: {}, document: {} });
vm.runInContext(source, context);
const parseOcrPages = vm.runInContext('parseOcrPages', context);

assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages('["普通标签","ocr_ok_pages=1,3","ocr_suspect_pages=2","ocr_failed_pages=4"]'))),
  { ok: [1, 3], suspect: [2], failed: [4] },
  'JSON 字符串 tags 解析正确'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages(['ocr_suspect_pages=2', 'ocr_ok_pages=1']))),
  { ok: [1], suspect: [2], failed: [] },
  '数组 tags 解析正确'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages('["ocr_failed_pages=bad,3,0"]'))),
  { ok: [], suspect: [], failed: [3] },
  '坏页码被过滤，不污染页面渲染'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages('["ocr_failed_pages=2bad,1.5,3e2,9007199254740993,3"]'))),
  { ok: [], suspect: [], failed: [3] },
  '2bad/1.5/3e2/超安全整数不被误认成页码'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages(
    ['ocr_failed_pages=2', 'ocr_suspect_pages=1', 'ocr_failed_pages=bad', 'ocr_failed_pages=4']
  ))),
  { ok: [], suspect: [1], failed: [2, 4] },
  '重复状态标签合并，坏值不清掉已有有效页码'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages(['ocr_ok_pages=1,2,3']))),
  { ok: [1, 2, 3], suspect: [], failed: [] },
  '全部页 ok 也保留 OCR 导入身份'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages(['ocr_suspect_pages=2', 'ocr_suspect_pages=2']))),
  { ok: [], suspect: [2], failed: [] },
  '重复页码去重'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages(null))),
  { ok: [], suspect: [], failed: [] },
  '缺失 tags 返回空结构'
);
assert.deepEqual(
  JSON.parse(JSON.stringify(parseOcrPages('not-json'))),
  { ok: [], suspect: [], failed: [] },
  '坏 JSON 返回空结构'
);
assert.equal(context.window.KB.parseOcrPages, parseOcrPages, 'parseOcrPages 挂到 window.KB');

const admin = fs.readFileSync(path.join(root, 'app/static/js/admin.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'app/static/css/app.css'), 'utf8');
assert.match(admin, /parseOcrPages\(d\.tags\)/, '管理页从文档 tags 解析 OCR 页');
assert.match(admin, /OCR 可疑页：第 /, '管理页显示可疑页原文页码');
assert.match(admin, /OCR 失败页（未入库）：第 /, '管理页显示失败页原文页码');
assert.match(admin, /doc-ocr-hint/, '提示有独立 DOM class');
assert.match(admin, /doc-ocr-warn/, '状态列有 OCR 待核对提示');
assert.match(admin, /isOcrImported/, '全部 ok 的 OCR 文档也按导入身份处理');
assert.match(admin, /ocrPages\.ok\.length/, 'ok-only 文档也禁用普通重建');
assert.match(admin, /reindexBtn\.disabled = true/, 'OCR 文档普通重建按钮被禁用');
assert.match(admin, /不支持普通重建索引/, '禁用按钮告知需重新走 OCR 工具');
assert.match(css, /\.doc-ocr-hint\s*\{/, 'CSS 提供 OCR 提示样式');
assert.match(css, /\.doc-ocr-warn\s*\{/, 'CSS 提供状态列提示样式');

console.log('admin OCR hint checks passed');
