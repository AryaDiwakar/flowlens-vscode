const fs = require('node:fs');
const path = require('node:path');

const source = path.resolve(__dirname, '../../../services/backend/src/flowlens');
const target = path.resolve(__dirname, '../backend/flowlens');
const requirements = path.resolve(__dirname, '../../../services/backend/requirements.txt');
if (!fs.existsSync(source)) throw new Error(`Backend source not found: ${source}`);
fs.cpSync(source, target, {
  recursive: true,
  force: true,
  filter: item => !item.includes(`${path.sep}__pycache__`) && !item.endsWith('.pyc')
});
fs.rmSync(path.join(target, '__pycache__'), { recursive: true, force: true });
fs.copyFileSync(requirements, path.resolve(__dirname, '../backend/requirements.txt'));
console.log(`Bundled FlowLens Python sources to ${target}`);
