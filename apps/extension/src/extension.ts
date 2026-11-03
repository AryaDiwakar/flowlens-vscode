import * as vscode from 'vscode';
import { Backend } from './backend';
import * as path from 'node:path';
import * as fs from 'node:fs';

type Finding = { code: string; severity: string; message: string; file: string; line: number; evidence: string; suggestion?: string };
type Scan = { root: string; assets: { path: string; kind: string; size_bytes: number }[]; nodes: { id: string; label: string; kind: string; file?: string }[]; edges: { source: string; target: string; relationship: string; confidence: string }[]; findings: Finding[]; unresolved: string[]; scan_stats: { discovered_files: number; parsed_files: number; reused_files: number; removed_files: number; changed_files: string[] } };

class FlowLensTreeItem extends vscode.TreeItem {
  constructor(label: string, collapsible: vscode.TreeItemCollapsibleState, readonly children: FlowLensTreeItem[] = []) { super(label, collapsible); }
}

class FlowLensTree implements vscode.TreeDataProvider<FlowLensTreeItem>, vscode.Disposable {
  private emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.emitter.event;
  private scan?: Scan;
  refresh(scan?: Scan): void { this.scan = scan; this.emitter.fire(); }
  getTreeItem(item: FlowLensTreeItem): vscode.TreeItem { return item; }
  getChildren(element?: FlowLensTreeItem): vscode.ProviderResult<FlowLensTreeItem[]> {
    if (element) return element.children;
    const groups = [
      ['Assets', this.scan?.assets.map(a => {
        const item = new FlowLensTreeItem(`${a.path} · ${a.kind}`, vscode.TreeItemCollapsibleState.None);
        const root = vscode.workspace.workspaceFolders?.[0]?.uri;
        if (root) item.command = { command: 'vscode.open', title: 'Open asset', arguments: [vscode.Uri.joinPath(root, a.path)] };
        return item;
      }) ?? []],
      ['Workspace Findings', this.scan?.findings.map(f => {
        const item = new FlowLensTreeItem(`${f.severity.toUpperCase()} · ${f.file}:${f.line} · ${f.message}`, vscode.TreeItemCollapsibleState.None);
        const root = vscode.workspace.workspaceFolders?.[0]?.uri;
        if (root) item.command = { command: 'vscode.open', title: 'Open finding', arguments: [vscode.Uri.joinPath(root, f.file), { selection: new vscode.Range(Math.max(0, f.line - 1), 0, Math.max(0, f.line - 1), 0) }] };
        return item;
      }) ?? []],
      ['Pipeline Dependencies', this.scan?.edges.map(edge => new FlowLensTreeItem(
        `${edge.source} → ${edge.target} · ${edge.confidence}`, vscode.TreeItemCollapsibleState.None
      )) ?? []],
      ['Data Quality', [quickAction('Validate a dataset', 'flowlens.validateDataset')]],
      ['Configured KPIs', [quickAction('Evaluate read-only KPI queries', 'flowlens.evaluateMetrics')]],
      ['Anomalies & Investigation', [quickAction('Investigate a dataset', 'flowlens.investigateDataset')]],
      ['Analysis History', [quickAction('Show local run history', 'flowlens.showHistory')]],
      ['Unresolved dependencies', this.scan?.unresolved.map(v => new FlowLensTreeItem(v, vscode.TreeItemCollapsibleState.None)) ?? []]
    ];
    return groups.map(([title, items]) => new FlowLensTreeItem(String(title), vscode.TreeItemCollapsibleState.Expanded, items as FlowLensTreeItem[]));
  }
  dispose(): void { this.emitter.dispose(); }
}

function quickAction(label: string, command: string): FlowLensTreeItem {
  const item = new FlowLensTreeItem(label, vscode.TreeItemCollapsibleState.None);
  item.command = { command, title: label };
  return item;
}

export function activate(context: vscode.ExtensionContext): void {
  const backend = new Backend();
  const tree = new FlowLensTree();
  const diagnostics = vscode.languages.createDiagnosticCollection('flowlens');
  context.subscriptions.push(backend, diagnostics, tree, vscode.window.registerTreeDataProvider('flowlens.explorer', tree));
  let latest: Scan | undefined;
  let latestValidation: unknown;
  let analysisInProgress: Promise<void> | undefined;
  let debounce: ReturnType<typeof setTimeout> | undefined;
  const dashboards = new Set<vscode.WebviewPanel>();

  const applyDiagnostics = (scan: Scan): void => {
    diagnostics.clear();
    const grouped = new Map<string, vscode.Diagnostic[]>();
    for (const finding of scan.findings) {
      if (!['.sql', '.py', '.yml', '.yaml', '.json'].includes(path.extname(finding.file).toLowerCase())) continue;
      const uri = vscode.Uri.joinPath(vscode.workspace.workspaceFolders![0].uri, finding.file);
      const range = new vscode.Range(Math.max(0, finding.line - 1), 0, Math.max(0, finding.line - 1), 1000);
      const severity = finding.severity === 'error' ? vscode.DiagnosticSeverity.Error
        : finding.severity === 'info' ? vscode.DiagnosticSeverity.Information : vscode.DiagnosticSeverity.Warning;
      const diagnostic = new vscode.Diagnostic(range, `${finding.message}${finding.suggestion ? ` ${finding.suggestion}` : ''}`, severity);
      diagnostic.code = finding.code;
      diagnostic.source = 'FlowLens';
      diagnostic.relatedInformation = finding.evidence ? [new vscode.DiagnosticRelatedInformation(new vscode.Location(uri, range), `Evidence: ${finding.evidence}`)] : [];
      const list = grouped.get(uri.toString()) ?? [];
      list.push(diagnostic); grouped.set(uri.toString(), list);
    }
    for (const [uri, items] of grouped) diagnostics.set(vscode.Uri.parse(uri), items);
  };

  const analyzeWorkspace = async (quiet = false): Promise<void> => {
    if (analysisInProgress) return analysisInProgress;
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) { vscode.window.showErrorMessage('Open a workspace folder first.'); return; }
    analysisInProgress = Promise.resolve(vscode.window.withProgress({ location: quiet ? vscode.ProgressLocation.Window : vscode.ProgressLocation.Notification, title: quiet ? 'FlowLens: refreshing changed assets' : 'FlowLens: analyzing workspace' }, async () => {
      try {
        const dialect = vscode.workspace.getConfiguration('flowlens').get<string>('sqlDialect', '');
        latest = await backend.post<Scan>(context, '/scan', { root, dialect });
        tree.refresh(latest); applyDiagnostics(latest);
        for (const panel of dashboards) void panel.webview.postMessage({ type: 'scan', scan: latest });
        const summary = `FlowLens parsed ${latest.scan_stats.parsed_files} changed file(s), reused ${latest.scan_stats.reused_files}, and found ${latest.findings.length} finding(s).`;
        if (quiet) vscode.window.setStatusBarMessage(summary, 3000);
        else vscode.window.showInformationMessage(summary);
      } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
    })).finally(() => { analysisInProgress = undefined; });
    return analysisInProgress;
  };

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.analyzeWorkspace', () => analyzeWorkspace(false)));
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.{sql,py,csv,parquet,yml,yaml,json}');
  const scheduleRescan = (uri: vscode.Uri): void => {
    const parts = uri.fsPath.split(path.sep);
    if (parts.some(part => ['.git', '.flowlens', 'node_modules', '.venv', 'venv'].includes(part))) return;
    if (!vscode.workspace.getConfiguration('flowlens').get<boolean>('autoRescan', true)) return;
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(() => { void analyzeWorkspace(true); }, 750);
  };
  context.subscriptions.push(watcher, watcher.onDidChange(scheduleRescan), watcher.onDidCreate(scheduleRescan), watcher.onDidDelete(scheduleRescan), {
    dispose: () => { if (debounce) clearTimeout(debounce); }
  });

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.showDashboard', () => showDashboard(context, () => latest, dashboards)));
  context.subscriptions.push(vscode.commands.registerCommand('flowlens.showDiagnosticEvidence', async (diagnostic: vscode.Diagnostic) => {
    const evidence = diagnostic.relatedInformation?.map(info => info.message).join('\n') ?? 'No source evidence was attached.';
    await vscode.window.showInformationMessage(`${diagnostic.message}\n\n${evidence}`, { modal: true });
  }));
  context.subscriptions.push(vscode.commands.registerCommand('flowlens.profileFile', async (uri?: vscode.Uri, inDashboard = false) => {
    if (typeof uri === 'boolean') { inDashboard = uri; uri = undefined; }
    let target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || !['.csv', '.parquet'].includes(path.extname(target.fsPath).toLowerCase())) target = (await vscode.window.showOpenDialog({ canSelectMany: false, filters: { 'Datasets': ['csv', 'parquet'] } }))?.[0];
    if (!target) return;
    try {
      const root = requireWorkspaceRoot();
      const relative = workspaceRelative(root, target);
      const sensitivity = vscode.workspace.getConfiguration('flowlens').get<number>('anomalySensitivity', 3.5);
      const sampleRows = vscode.workspace.getConfiguration('flowlens').get<number>('profileSampleRows', 100_000);
      const profile = await withFlowLensProgress('FlowLens: profiling dataset', () => backend.post<{ rows: number; columns: number; sampled: boolean; sample_limit: number; duplicate_rows: number; duplicate_percent: number; statistics: string; profiles: { name: string; data_type: string; null_count?: number; null_percent?: number; distinct_count?: number; minimum?: string | number; maximum?: string | number; mean?: number; median?: number; standard_deviation?: number; quantiles: Record<string, number | null>; outlier_count?: number; outlier_percent?: number; top_values: { value: string; count: number }[] }[]; anomalies: { metric: string; explanation: string }[] }>(context, '/profile', { file: target.fsPath, root: root.fsPath, sensitivity, sample_rows: sampleRows }));
      const rows = profile.profiles.map(c => `| ${c.name} | ${c.data_type} | ${c.null_count ?? '—'} (${c.null_percent?.toFixed(2) ?? '—'}%) | ${c.distinct_count ?? '—'} | ${c.minimum ?? '—'} | ${c.maximum ?? '—'} | ${c.mean?.toFixed(3) ?? '—'} | ${c.median?.toFixed(3) ?? '—'} | ${c.quantiles.q25?.toFixed(3) ?? '—'}–${c.quantiles.q75?.toFixed(3) ?? '—'} | ${c.standard_deviation?.toFixed(3) ?? '—'} | ${c.outlier_count ?? '—'} | ${c.outlier_percent?.toFixed(2) ?? '—'}% |`).join('\n');
      const anomalies = profile.anomalies.length ? profile.anomalies.map(a => `- **${a.metric}:** ${a.explanation}`).join('\n') : 'No statistical signals against a baseline (at least five prior profiles are needed).';
      const categories = profile.profiles.filter(c => c.top_values.length).map(c => `### ${c.name}\n\n${c.top_values.map(item => `- ${item.value}: ${item.count}`).join('\n')}`).join('\n\n');
      if (!inDashboard) await showMarkdown('Dataset profile', `# Dataset profile: ${relative}\n\n- **Rows:** ${profile.rows}\n- **Columns:** ${profile.columns}\n- **Duplicate rows:** ${profile.duplicate_rows} (${profile.duplicate_percent.toFixed(2)}%)${profile.sampled ? `, measured in the first ${profile.sample_limit} rows` : ''}\n- **Statistics:** ${profile.statistics}${profile.sampled ? ` (first ${profile.sample_limit} rows for column statistics)` : ''}\n\n| Column | Type | Missing | Distinct | Min | Max | Mean | Median | Q25–Q75 | Std dev | Outliers | Outlier % |\n|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n${rows}\n\n## Categorical frequencies (top 10)\n\n${categories || 'No categorical frequency distributions.'}\n\n## Anomaly signals\n\n${anomalies}`);
      return profile;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.validateDataset', async (uri?: vscode.Uri, inDashboard = false) => {
    if (typeof uri === 'boolean') { inDashboard = uri; uri = undefined; }
    let target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || !['.csv', '.parquet'].includes(path.extname(target.fsPath).toLowerCase())) {
      target = (await vscode.window.showOpenDialog({ canSelectMany: false, filters: { 'Datasets': ['csv', 'parquet'] } }))?.[0];
    }
    if (!target) return;
    try {
      const root = requireWorkspaceRoot();
      const dataset = workspaceRelative(root, target);
      const configFile = vscode.workspace.getConfiguration('flowlens').get<string>('qualityConfig', '.flowlens/quality.yml');
      const result = await withFlowLensProgress('FlowLens: running data quality checks', () => backend.post<{ passed: boolean; total_checks: number; failed_checks: number; comparison: { has_baseline: boolean; previous_at?: string; delta_failed_checks: number }; results: { rule: string; column?: string; passed: boolean; checked_rows: number; failed_rows: number; message: string; samples: Record<string, unknown>[] }[] }>(context, '/validate', { root: root.fsPath, dataset, config_file: configFile }));
      latestValidation = result;
      const checks = result.results.map(r => `### ${r.passed ? 'PASS' : 'FAIL'} · ${r.rule}${r.column ? ` · ${r.column}` : ''}\n\n${r.message} Checked ${r.checked_rows} row(s); ${r.failed_rows} failed.\n${r.samples.length ? `\n\`\`\`json\n${JSON.stringify(r.samples, null, 2)}\n\`\`\`` : ''}`).join('\n\n');
      const comparison = result.comparison.has_baseline ? `\n\nCompared with ${result.comparison.previous_at}: ${result.comparison.delta_failed_checks > 0 ? '+' : ''}${result.comparison.delta_failed_checks} failed check(s).` : '\n\nNo previous validation baseline is stored.';
      if (!inDashboard) await showMarkdown('Data quality report', `# Validation: ${dataset}\n\n**${result.passed ? 'Passed' : 'Failed'}** · ${result.total_checks} check(s), ${result.failed_checks} failed.${comparison}\n\n${checks || 'No checks are configured.'}`);
      return result;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.exportValidationReport', async () => {
    if (!latestValidation) return vscode.window.showInformationMessage('Run a dataset validation before exporting a report.');
    const target = await vscode.window.showSaveDialog({ saveLabel: 'Export FlowLens report', filters: { 'JSON report': ['json'] } });
    if (!target) return;
    try {
      await vscode.workspace.fs.writeFile(target, Buffer.from(`${JSON.stringify(latestValidation, null, 2)}\n`, 'utf8'));
      vscode.window.showInformationMessage(`FlowLens validation report saved to ${target.fsPath}`);
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.evaluateMetrics', async (uri?: vscode.Uri, inDashboard = false) => {
    if (typeof uri === 'boolean') { inDashboard = uri; uri = undefined; }
    let target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || !['.csv', '.parquet'].includes(path.extname(target.fsPath).toLowerCase())) {
      target = (await vscode.window.showOpenDialog({ canSelectMany: false, filters: { 'Datasets': ['csv', 'parquet'] } }))?.[0];
    }
    if (!target) return;
    try {
      const root = requireWorkspaceRoot();
      const dataset = workspaceRelative(root, target);
      const configFile = vscode.workspace.getConfiguration('flowlens').get<string>('metricConfig', '.flowlens/metrics.yml');
      const sensitivity = vscode.workspace.getConfiguration('flowlens').get<number>('anomalySensitivity', 3.5);
      const report = await withFlowLensProgress('FlowLens: evaluating configured KPIs', () => backend.post<{
        dataset: string; timestamp: string; values: { name: string; value: number; baseline_observations: number; baseline_median?: number; anomaly?: { explanation?: string; baseline_median?: number; score?: number } }[]
      }>(context, '/metrics/evaluate', { root: root.fsPath, dataset, config_file: configFile, sensitivity }));
      const metrics = report.values.map(value => `- **${value.name}:** ${value.value}${value.baseline_observations ? ` · baseline median ${value.baseline_median} (${value.baseline_observations} observations)` : ' · baseline warming up'}${value.anomaly ? `\n  - ${value.anomaly.explanation}` : ''}`).join('\n');
      if (!inDashboard) await showMarkdown('KPI observations', `# Configured KPI observations: ${dataset}\n\n${metrics}\n\nThese are read-only query results. Statistical deviations are signals, not confirmed failures.`);
      return report;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.inspectImpact', async (inDashboard = false) => {
    try {
      const root = requireWorkspaceRoot();
      let target = vscode.window.activeTextEditor?.document.uri;
      if (!target && latest) {
        const choice = await vscode.window.showQuickPick(latest.assets.filter(asset => ['sql', 'python'].includes(asset.kind)).map(asset => ({ label: asset.path, detail: asset.kind, file: asset.path })), { placeHolder: 'Choose a transformation to inspect' });
        if (choice) target = vscode.Uri.joinPath(root, choice.file);
      }
      if (!target) return vscode.window.showErrorMessage('Open or select a SQL/Python transformation.');
      const file = workspaceRelative(root, target);
      const dialect = vscode.workspace.getConfiguration('flowlens').get<string>('sqlDialect', '');
      const result = await withFlowLensProgress('FlowLens: calculating downstream impact', () => backend.post<{ impacted_nodes: { id: string; label: string; kind: string }[]; paths: string[][]; circular_dependencies: string[][] }>(context, '/impact', { root: root.fsPath, file, dialect }));
      const impacted = result.impacted_nodes.map(n => `- **${n.kind}** ${n.label}`).join('\n') || 'No statically inferred downstream nodes.';
      const paths = result.paths.map(p => `- ${p.join(' → ')}`).join('\n') || 'No dependency paths found.';
      const cycles = result.circular_dependencies.map(c => `- ${c.join(' → ')}`).join('\n') || 'No circular dependency detected.';
      if (!inDashboard) await showMarkdown('Pipeline impact', `# Impact analysis: ${file}\n\nStatic impact only; runtime execution dependencies may differ.\n\n## Downstream nodes\n\n${impacted}\n\n## Dependency paths\n\n${paths}\n\n## Cycles detected in workspace graph\n\n${cycles}`);
      return result;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.investigateDataset', async (inDashboard = false) => {
    const root = requireWorkspaceRoot();
    const dataset = await vscode.window.showInputBox({ prompt: 'Dataset name or workspace-relative CSV/Parquet path', placeHolder: 'marts/revenue.csv or analytics.revenue' });
    if (!dataset) return;
    try {
      const dialect = vscode.workspace.getConfiguration('flowlens').get<string>('sqlDialect', '');
      const result = await withFlowLensProgress('FlowLens: investigating available evidence', () => backend.post<{ status: string; statement: string; evidence_scope: string; hypotheses: { rank: number; description: string; evidence: string[]; files: string[]; confidence: string; rationale: string; next_steps: string[] }[] }>(context, '/investigate', { root: root.fsPath, dataset, dialect }));
      const hypotheses = result.hypotheses.map(h => `## ${h.rank}. ${h.description}\n\n**Confidence:** ${h.confidence}\n\n${h.rationale}\n\n**Evidence**\n${h.evidence.map(e => `- ${e}`).join('\n')}\n\n**Files:** ${h.files.join(', ') || '—'}\n\n**Next steps**\n${h.next_steps.map(s => `- ${s}`).join('\n')}`).join('\n\n');
      if (!inDashboard) await showMarkdown('Root-cause investigation', `# Investigation: ${dataset}\n\n${result.statement}\n\n${hypotheses || 'No hypotheses were supported by current static analysis and stored local history.'}\n\n---\n\n${result.evidence_scope}`);
      return result;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.schemaDiff', async (uri?: vscode.Uri, inDashboard = false) => {
    if (typeof uri === 'boolean') { inDashboard = uri; uri = undefined; }
    let target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || !['.csv', '.parquet'].includes(path.extname(target.fsPath).toLowerCase())) target = (await vscode.window.showOpenDialog({ canSelectMany: false, filters: { 'Datasets': ['csv', 'parquet'] } }))?.[0];
    if (!target) return;
    try {
      const root = requireWorkspaceRoot();
      const file = workspaceRelative(root, target);
      const sensitivity = vscode.workspace.getConfiguration('flowlens').get<number>('anomalySensitivity', 3.5);
      const sampleRows = vscode.workspace.getConfiguration('flowlens').get<number>('profileSampleRows', 100_000);
      const diff = await withFlowLensProgress('FlowLens: comparing schema snapshot', () => backend.post<{ dataset: string; has_baseline: boolean; previous_at?: string; current_at: string; changes: { kind: string; column: string; before?: string; after?: string }[] }>(context, '/schema/diff', { root: root.fsPath, file, sensitivity, sample_rows: sampleRows }));
      const changes = diff.changes.map(c => `- **${c.kind}** · ${c.column}${c.before ? ` · ${c.before}` : ''}${c.after ? ` → ${c.after}` : ''}`).join('\n');
      if (!inDashboard) await showMarkdown('Schema drift', `# Schema snapshot: ${file}\n\n${diff.has_baseline ? `Compared with ${diff.previous_at}.` : 'First snapshot saved; no prior baseline exists.'}\n\n${changes || (diff.has_baseline ? 'No schema changes detected.' : '')}\n\nNullable behavior is reported from observed nulls in profiled rows, not source-level constraints.`);
      return diff;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.showHistory', async (inDashboard = false) => {
    try {
      const root = requireWorkspaceRoot();
      const result = await backend.post<{ events: { id: number; kind: string; created_at: string; payload: Record<string, unknown> }[] }>(context, '/history', { root: root.fsPath, limit: 100 });
      const events = result.events.map(e => `| ${e.created_at} | ${e.kind} | ${JSON.stringify(e.payload)} |`).join('\n');
      if (!inDashboard) await showMarkdown('Analysis history', `# Local analysis history\n\nStored in \\.flowlens/history.sqlite3. It contains analysis metadata and profile/validation observations; source rows are not copied into history.\n\n| Time | Event | Summary |\n|---|---|---|\n${events || '| — | — | No local history yet. |'}`);
      return result;
    } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
  }));

  context.subscriptions.push(vscode.commands.registerCommand('flowlens.setupBackend', async () => {
    const consent = await vscode.window.showWarningMessage(
      'FlowLens will create an isolated Python environment in VS Code extension storage and download its pinned backend dependencies. No workspace files will be changed.',
      { modal: true }, 'Install Dependencies'
    );
    if (consent !== 'Install Dependencies') return;
    try {
      await backend.install(context);
      vscode.window.showInformationMessage('FlowLens local backend is ready.');
    } catch (error) {
      vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error));
    }
  }));

  context.subscriptions.push(vscode.languages.registerCodeActionsProvider([{ language: 'sql' }, { language: 'python' }, { language: 'yaml' }, { language: 'json' }], {
    provideCodeActions(document, range, context) {
      return context.diagnostics.filter(d => d.source === 'FlowLens').map(d => {
        const action = new vscode.CodeAction('FlowLens: show finding evidence', vscode.CodeActionKind.Empty);
        action.diagnostics = [d];
        action.command = { command: 'flowlens.showDiagnosticEvidence', title: 'Show finding evidence', arguments: [d] };
        return action;
      });
    }
  }));
}

function showDashboard(context: vscode.ExtensionContext, getScan: () => Scan | undefined, dashboards: Set<vscode.WebviewPanel>): void {
  const webviewDirectory = vscode.Uri.joinPath(context.extensionUri, 'media', 'webview');
  const panel = vscode.window.createWebviewPanel('flowlens.dashboard', 'FlowLens', vscode.ViewColumn.One, {
    enableScripts: true, localResourceRoots: [webviewDirectory]
  });
  dashboards.add(panel);
  panel.onDidDispose(() => dashboards.delete(panel));
  const indexPath = path.join(context.extensionPath, 'media', 'webview', 'index.html');
  try {
    let html = fs.readFileSync(indexPath, 'utf8');
    html = html.replace(/(src|href)="([^"]+)"/g, (match, attribute: string, resource: string) => {
      if (/^(https?:|data:|\/\/)/i.test(resource)) return match;
      const local = vscode.Uri.joinPath(webviewDirectory, resource.replace(/^\.\//, ''));
      return `${attribute}="${panel.webview.asWebviewUri(local)}"`;
    });
    const csp = `default-src 'none'; img-src ${panel.webview.cspSource} data:; style-src ${panel.webview.cspSource} 'unsafe-inline'; script-src ${panel.webview.cspSource}; font-src ${panel.webview.cspSource}; connect-src 'none';`;
    panel.webview.html = html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${csp}">`);
  } catch {
    panel.webview.html = '<!doctype html><html><body><p>FlowLens dashboard assets are not built. Run <code>npm run webview:build</code> in apps/extension.</p></body></html>';
  }
  panel.webview.onDidReceiveMessage(async message => {
    if (message?.command === 'ready') {
      const current = getScan();
      if (current) await panel.webview.postMessage({ type: 'scan', scan: current });
    } else if (message?.command === 'analyze') {
      await vscode.commands.executeCommand('flowlens.analyzeWorkspace');
    } else if (message?.command === 'history') {
      const result = await vscode.commands.executeCommand('flowlens.showHistory', true);
      if (result) await panel.webview.postMessage({ type: 'result', kind: 'history', result });
    } else if (message?.command === 'exportValidation') {
      await vscode.commands.executeCommand('flowlens.exportValidationReport');
    } else if (['profile', 'validate', 'impact', 'investigate', 'schema', 'metrics'].includes(message?.command)) {
      const command = ({
        profile: 'flowlens.profileFile', validate: 'flowlens.validateDataset', impact: 'flowlens.inspectImpact',
        investigate: 'flowlens.investigateDataset', schema: 'flowlens.schemaDiff', metrics: 'flowlens.evaluateMetrics'
      } as Record<string, string>)[message.command];
      const result = ['impact', 'investigate'].includes(message.command)
        ? await vscode.commands.executeCommand(command, true)
        : await vscode.commands.executeCommand(command, undefined, true);
      if (result) await panel.webview.postMessage({ type: 'result', kind: message.command, result });
    } else if (message?.command === 'openFile' && typeof message.file === 'string') {
      try {
        const root = requireWorkspaceRoot();
        const file = vscode.Uri.joinPath(root, message.file);
        workspaceRelative(root, file);
        const line = Number.isInteger(message.line) ? Math.max(0, Number(message.line) - 1) : 0;
        await vscode.commands.executeCommand('vscode.open', file, { selection: new vscode.Range(line, 0, line, 0) });
      } catch (error) { vscode.window.showErrorMessage(error instanceof Error ? error.message : String(error)); }
    }
  });
}

function requireWorkspaceRoot(): vscode.Uri {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!root) throw new Error('Open a workspace folder first.');
  return root;
}

function workspaceRelative(root: vscode.Uri, target: vscode.Uri): string {
  const relative = path.relative(root.fsPath, target.fsPath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('Select a file inside the current workspace.');
  return relative.split(path.sep).join('/');
}

async function showMarkdown(title: string, content: string): Promise<void> {
  const document = await vscode.workspace.openTextDocument({ language: 'markdown', content });
  await vscode.window.showTextDocument(document, { preview: true, viewColumn: vscode.ViewColumn.Beside });
}

async function withFlowLensProgress<T>(title: string, operation: () => Promise<T>): Promise<T> {
  return await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title }, async () => operation());
}

export function deactivate(): void {}
