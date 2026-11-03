const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');

test('FlowLens extension build output exports activation entry point', () => {
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === 'vscode') return { TreeItem: class {} };
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const extension = require('../out/extension.js');
    assert.equal(typeof extension.activate, 'function');
  } finally {
    Module._load = originalLoad;
  }
});
