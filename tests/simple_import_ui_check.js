'use strict';
// Exercise the document view switch and verify that uploads and search stay available.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = p => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const admin = read('app/static/js/admin.js');
const adminHtml = read('app/templates/admin.html');
const libraryHtml = read('app/templates/library.html');
const library = read('app/static/js/library.js');
assert.doesNotMatch(adminHtml, /id="webImport"|id="docFilterType"/);
assert.doesNotMatch(libraryHtml, /id="qualityTypeFilter"/);
assert.match(adminHtml, /accept="[^"]*\.md[^"]*\.png[^"]*\.jpe?g/);
assert.match(admin, /OCRUI\.selected\(file\)/);
assert.match(library, /api\('\/api\/library\/search'/);
const nodes = {};
for (const id of ['documentList', 'fileImport', 'imageTextImport']) {
  nodes[id] = {hidden:false, classList:{toggle(name, hidden){nodes[id].hidden=hidden;}}, focus(){}};
}
const context = vm.createContext({
  document:{addEventListener(){}},
  qs(selector){return nodes[selector.slice(1)] || null;}, qsa(){return [];}
});
vm.runInContext(admin.replace("  document.addEventListener('DOMContentLoaded'", "  globalThis.showView = showDocumentView;\n  document.addEventListener('DOMContentLoaded'"),context);
context.showView('fileImport',true);
assert.equal(nodes.documentList.hidden,true);
assert.equal(nodes.fileImport.hidden,false);
context.showView('documentList',true);
assert.equal(nodes.documentList.hidden,false);
assert.equal(nodes.fileImport.hidden,true);
context.showView('imageTextImport',true);
assert.equal(nodes.imageTextImport.hidden,false);
assert.equal(nodes.documentList.hidden,true);
console.log('simplified import UI checks passed');
