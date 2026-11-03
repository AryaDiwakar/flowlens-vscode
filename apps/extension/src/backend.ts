import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { randomBytes } from 'node:crypto';
import * as path from 'node:path';
import * as fs from 'node:fs';

export class Backend implements vscode.Disposable {
  private process?: ChildProcess;
  private port?: number;
  private token?: string;

  async start(context: vscode.ExtensionContext, offerSetup = true): Promise<string> {
    if (this.port && await this.isHealthy()) return `http://127.0.0.1:${this.port}`;
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!root) throw new Error('Open a workspace folder before running FlowLens.');
    if (!vscode.workspace.isTrusted) throw new Error('FlowLens requires a trusted workspace to start its local analysis service.');
    const config = vscode.workspace.getConfiguration('flowlens');
    const configuredPython = config.get<string>('pythonPath', 'python3');
    const managedPython = context.globalState.get<string>('managedPythonPath');
    const python = managedPython && configuredPython === context.globalState.get<string>('managedPythonBase', 'python3')
      ? managedPython : configuredPython;
    const port = await this.freePort();
    const devBackend = path.resolve(context.extensionPath, '..', '..', 'services', 'backend');
    const bundledBackend = path.join(context.extensionPath, 'backend');
    const isDevelopment = fs.existsSync(devBackend);
    const backendRoot = isDevelopment ? devBackend : bundledBackend;
    const pythonPathRoot = isDevelopment ? path.join(backendRoot, 'src') : backendRoot;
    this.token = randomBytes(32).toString('hex');
    this.process = spawn(python, ['-m', 'flowlens.server', '--port', String(port)], {
      cwd: backendRoot,
      env: { ...process.env, FLOWLENS_TOKEN: this.token, PYTHONPATH: `${pythonPathRoot}${process.env.PYTHONPATH ? `${path.delimiter}${process.env.PYTHONPATH}` : ''}` },
      stdio: 'ignore',
      windowsHide: true
    });
    this.port = port;
    this.process.once('error', () => { this.port = undefined; });
    this.process.once('exit', () => { this.port = undefined; });
    for (let i = 0; i < 40; i++) {
      if (await this.isHealthy()) return `http://127.0.0.1:${port}`;
      if (!this.process.pid || this.process.exitCode !== null) break;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    this.dispose();
    const message = `FlowLens backend did not start with '${python}'. Check the Python interpreter and installed backend dependencies.`;
    if (offerSetup) {
      const action = await vscode.window.showErrorMessage(message, 'Set Up Local Backend');
      if (action) {
        await this.install(context);
        return this.start(context, false);
      }
    }
    throw new Error(message);
  }

  async install(context: vscode.ExtensionContext): Promise<void> {
    const configuredPython = vscode.workspace.getConfiguration('flowlens').get<string>('pythonPath', 'python3');
    const environment = path.join(context.globalStorageUri.fsPath, 'python-env');
    await fs.promises.mkdir(context.globalStorageUri.fsPath, { recursive: true });
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'FlowLens: setting up local Python backend', cancellable: false }, async progress => {
      progress.report({ message: 'Creating an isolated Python environment' });
      await runProcess(configuredPython, ['-m', 'venv', environment]);
      const executable = process.platform === 'win32'
        ? path.join(environment, 'Scripts', 'python.exe')
        : path.join(environment, 'bin', 'python');
      const devBackend = path.resolve(context.extensionPath, '..', '..', 'services', 'backend');
      const requirements = fs.existsSync(devBackend)
        ? path.join(devBackend, 'requirements.txt')
        : path.join(context.extensionPath, 'backend', 'requirements.txt');
      if (!fs.existsSync(requirements)) throw new Error('Bundled backend requirements file is missing. Rebuild the FlowLens VSIX.');
      progress.report({ message: 'Installing pinned dependencies (network access required)' });
      await runProcess(executable, ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', requirements]);
      await context.globalState.update('managedPythonPath', executable);
      await context.globalState.update('managedPythonBase', configuredPython);
    });
  }

  async post<T>(context: vscode.ExtensionContext, path: string, body: unknown): Promise<T> {
    const base = await this.start(context);
    const response = await fetch(`${base}${path}`, {
      method: 'POST', headers: { 'content-type': 'application/json', 'x-flowlens-token': this.token ?? '' }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(60_000)
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`FlowLens request failed (${response.status}): ${detail}`);
    }
    return await response.json() as T;
  }

  private async isHealthy(): Promise<boolean> {
    if (!this.port) return false;
    try { return (await fetch(`http://127.0.0.1:${this.port}/health`, { signal: AbortSignal.timeout(300) })).ok; }
    catch { return false; }
  }

  private async freePort(): Promise<number> {
    const server = createServer();
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Could not allocate a local port.');
    const port = address.port;
    await new Promise<void>(resolve => server.close(() => resolve()));
    return port;
  }

  dispose(): void {
    this.process?.kill();
    this.process = undefined;
    this.port = undefined;
    this.token = undefined;
  }
}

async function runProcess(executable: string, args: string[]): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(executable, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    let output = '';
    const collect = (chunk: Buffer) => { output = (output + chunk.toString()).slice(-6000); };
    child.stdout?.on('data', collect);
    child.stderr?.on('data', collect);
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error(`${path.basename(executable)} exited with code ${code ?? 'unknown'}:\n${output}`)));
  });
}
