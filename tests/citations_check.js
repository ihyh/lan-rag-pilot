'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ document: { addEventListener() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/js/app.js'), 'utf8'), context);
const sources = [
  { document_id: 1, page: 5, excerpt: '原文甲' },
  { document_id: 1, page: 5, excerpt: '原文乙' },
  { document_id: 1, page: 6, excerpt: '未引用候选' },
  { document_id: 2, paragraph: 4, excerpt: '原文丙' },
  { document_id: 2, paragraph: 4, excerpt: '不同片段' }
];
const result = JSON.parse(JSON.stringify(context.citedSources('结论[1][2]。另一结论[4, 5][4]。错误编号[99][0]', sources)));
assert.equal(result.length, 3);
assert.deepEqual(result[0].numbers, [1, 2]);
assert.deepEqual(result[0].excerpts, ['原文甲', '原文乙']);
assert.equal(result[1].source.excerpt, '原文丙');
assert.equal(result[2].source.excerpt, '不同片段');
assert.equal(context.citedSources('没有引用', sources).length, 0);
assert.equal(context.citedSources('只有无效引用[99]', sources).length, 0);
assert.equal(context.citedSources('引用[1]', []).length, 0);
console.log('引用筛选、编号对应、去重、缺失来源检查通过');
