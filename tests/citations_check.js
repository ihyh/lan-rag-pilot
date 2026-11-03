'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ document: { addEventListener() {} } });
const appSource = fs.readFileSync(path.join(__dirname, '../app/static/js/app.js'), 'utf8');
const appHtml = fs.readFileSync(path.join(__dirname, '../app/templates/app.html'), 'utf8');
vm.runInContext(appSource, context);
const sources = [
  { document_id: 1, page: 5, excerpt: '原文甲' },
  { document_id: 1, page: 5, excerpt: '原文乙' },
  { document_id: 1, page: 6, excerpt: '未引用候选' },
  { document_id: 2, paragraph: 4, excerpt: '原文丙' },
  { document_id: 2, paragraph: 4, excerpt: '不同片段' }
];
const result = JSON.parse(JSON.stringify(context.citedSources('结论[1][2]。另一结论[4, 5][4]。错误编号[99][0]', sources)));
assert.equal(result.length, 4);
assert.deepEqual(result.map(group => group.numbers), [[1], [2], [4], [5]]);
assert.deepEqual(result.map(group => group.excerpts), [['原文甲'], ['原文乙'], ['原文丙'], ['不同片段']]);
assert.equal(context.citedSources('没有引用', sources).length, 0);
assert.equal(context.citedSources('只有无效引用[99]', sources).length, 0);
assert.equal(context.citedSources('引用[1]', []).length, 0);
assert.match(appHtml, /id="documentScopeOptions"/);
assert.match(appSource, /body\.document_ids/);
console.log('引用筛选、编号对应、去重、缺失来源检查通过');
require('./device_scope_check.js');
require('./answer_format_check.js');
