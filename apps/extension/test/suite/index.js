const path = require('node:path');
const Mocha = require('mocha');

function run() {
  const mocha = new Mocha({ ui: 'tdd', color: true, timeout: 30_000 });
  mocha.addFile(path.resolve(__dirname, 'extension.test.js'));
  return new Promise((resolve, reject) => mocha.run(failures => failures ? reject(new Error(`${failures} Extension Host test(s) failed.`)) : resolve()));
}

module.exports = { run };
