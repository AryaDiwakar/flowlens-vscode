const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const { tmpdir } = require('node:os');
const { downloadAndUnzipVSCode, runTests } = require('@vscode/test-electron');

function installedVSCode() {
  const pathCandidates = (process.env.PATH || '').split(path.delimiter).flatMap(directory =>
    process.platform === 'win32'
      ? [path.join(directory, 'Code.exe')]
      : [path.join(directory, 'code')]
  );
  const candidates = [
    process.env.VSCODE_EXECUTABLE_PATH,
    process.platform === 'darwin' && '/Applications/Visual Studio Code.app/Contents/MacOS/Code',
    process.platform === 'darwin' && path.join(os.homedir(), 'Applications/Visual Studio Code.app/Contents/MacOS/Code'),
    process.platform === 'win32' && path.join(process.env.LOCALAPPDATA || '', 'Programs/Microsoft VS Code/Code.exe'),
    process.platform === 'linux' && '/usr/bin/code',
    process.platform === 'linux' && '/usr/local/bin/code',
    ...pathCandidates
  ].filter(Boolean);
  return candidates.find(candidate => fs.existsSync(candidate));
}

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, '..');
  const extensionTestsPath = path.resolve(__dirname, 'suite', 'index.js');
  const workspace = fs.mkdtempSync(path.join(tmpdir(), 'flowlens-extension-host-'));
  const testStorage = path.join(extensionDevelopmentPath, '.vscode-test');
  const hadTestStorage = fs.existsSync(testStorage);
  fs.writeFileSync(path.join(workspace, 'input.csv'), 'id\n1\n2\n');
  fs.writeFileSync(path.join(workspace, 'model.sql'), 'CREATE TABLE output AS SELECT id FROM input;\n');
  try {
    const launchArgs = [workspace, '--disable-extensions', '--disable-gpu', '--no-sandbox'];
    const options = { extensionDevelopmentPath, extensionTestsPath, launchArgs };
    const executable = installedVSCode();
    if (executable) options.vscodeExecutablePath = executable;
    else {
      let downloaded = await downloadAndUnzipVSCode('stable');
      if (process.platform === 'darwin' && !fs.existsSync(downloaded)) {
        const renamedMacExecutable = path.join(path.dirname(downloaded), 'Code');
        if (fs.existsSync(renamedMacExecutable)) downloaded = renamedMacExecutable;
      }
      options.vscodeExecutablePath = downloaded;
    }
    await runTests(options);
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
    if (!hadTestStorage && fs.existsSync(testStorage)) fs.rmSync(testStorage, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exit(1); });
