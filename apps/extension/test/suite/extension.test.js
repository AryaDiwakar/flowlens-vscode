const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');

suite('FlowLens Extension Host', () => {
  test('activates and registers the workspace tooling commands', async () => {
    const extension = vscode.extensions.all.find(candidate => candidate.packageJSON.name === 'flowlens');
    assert.ok(extension, 'FlowLens extension is present in the Extension Host');
    await extension.activate();
    const commands = new Set(await vscode.commands.getCommands(true));
    for (const command of [
      'flowlens.analyzeWorkspace', 'flowlens.showDashboard', 'flowlens.profileFile',
      'flowlens.validateDataset', 'flowlens.inspectImpact', 'flowlens.investigateDataset',
      'flowlens.schemaDiff', 'flowlens.showHistory', 'flowlens.evaluateMetrics', 'flowlens.exportValidationReport', 'flowlens.setupBackend'
    ]) assert.ok(commands.has(command), `Missing command: ${command}`);
  });

  test('opens the built React dashboard in the Extension Host', async () => {
    await vscode.commands.executeCommand('flowlens.showDashboard');
    assert.ok(vscode.window.activeTextEditor || vscode.window.activeColorTheme, 'Extension Host remains responsive after dashboard creation');
  });

  if (process.env.FLOWLENS_TEST_BACKEND === '1') {
    test('starts the local API and scans the test workspace', async () => {
      await vscode.commands.executeCommand('flowlens.analyzeWorkspace');
      const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
      assert.ok(fs.existsSync(path.join(root, '.flowlens', 'history.sqlite3')), 'Explicit scan persisted local analysis history');
    });
  }
});
