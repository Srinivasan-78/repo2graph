// The VS Code layer: settings, one MCP session per workspace folder, the
// cursor listener, the webview and the commands. Everything it decides is
// delegated to src/core, which is tested without VS Code.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { run } from "./core/cli";
import { McpClient } from "./core/mcpClient";
import { type GraphNode, type Neighbourhood, SymbolNavigator, ToolCallError } from "./core/neighbourhood";
import {
  graphHtmlPath,
  indexPresent,
  languageOf,
  repoRelative,
  resolveAgainst,
  revealPage,
  serverExecutable,
} from "./core/paths";
import { isPageCommand, makeNonce, type PageCommand, renderBody, shellHtml, type Target, type ViewState } from "./core/render";
import { buildArgs, mapArgs, parseStatus, serverArgs, statusArgs, summarizeStatus } from "./core/status";

interface Settings {
  cli: string;
  server: string;
  indexPath: string;
  repoRoot: string;
  buildArgs: string[];
  debounceMs: number;
  stalenessTtl: number;
  limit: number;
}

function settings(scope?: vscode.Uri): Settings {
  const c = vscode.workspace.getConfiguration("repo2graph", scope);
  const cli = c.get<string>("executable", "repo2graph").trim() || "repo2graph";
  return {
    cli,
    server: serverExecutable(c.get<string>("mcpExecutable", ""), cli),
    indexPath: c.get<string>("indexPath", ".r2g"),
    repoRoot: c.get<string>("repoRoot", ""),
    buildArgs: c.get<string[]>("buildArgs", ["--git-history", "300"]).map(String),
    debounceMs: c.get<number>("debounceMs", 75),
    stalenessTtl: c.get<number>("stalenessTtlSeconds", 10),
    limit: c.get<number>("neighbourLimit", 50),
  };
}

/** One workspace folder: its index, and the server answering for it. */
class Session {
  private client: McpClient | undefined;
  private starting: Promise<McpClient> | undefined;
  navigator: SymbolNavigator | undefined;

  constructor(
    readonly folder: vscode.WorkspaceFolder,
    readonly repoRoot: string,
    readonly indexDir: string,
    private readonly config: Settings,
    private readonly log: vscode.OutputChannel,
  ) {}

  async ready(): Promise<SymbolNavigator> {
    if (this.client?.alive && this.navigator) {
      return this.navigator;
    }
    if (!this.starting) {
      this.log.appendLine(`starting ${this.config.server} for ${this.repoRoot}`);
      this.starting = McpClient.start({
        command: this.config.server,
        args: serverArgs(this.repoRoot, this.indexDir, this.config.stalenessTtl),
        cwd: this.repoRoot,
      }).finally(() => (this.starting = undefined));
    }
    const client = await this.starting;
    this.client = client;
    client.onExit((error) => this.log.appendLine(`repo2graph-mcp stopped: ${error.message}`));
    this.navigator = new SymbolNavigator(client, { limit: this.config.limit });
    return this.navigator;
  }

  dispose(): void {
    this.client?.dispose();
    this.client = undefined;
    this.navigator = undefined;
  }
}

class Controller implements vscode.WebviewViewProvider {
  private view: vscode.WebviewView | undefined;
  private readonly sessions = new Map<string, Session>();
  private timer: NodeJS.Timeout | undefined;
  private sequence = 0;
  private targets: Target[] = [];
  private current: { session: Session; model: Neighbourhood } | undefined;
  private building = false;
  private state: ViewState = { kind: "idle", message: "Place the cursor in a source file." };

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: vscode.OutputChannel,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, enableCommandUris: false, localResourceRoots: [] };
    view.webview.html = shellHtml(makeNonce(), "");
    view.webview.onDidReceiveMessage((message) => this.onMessage(message));
    view.onDidChangeVisibility(() => view.visible && this.schedule(0));
    this.schedule(0);
  }

  dispose(): void {
    for (const session of this.sessions.values()) {
      session.dispose();
    }
    this.sessions.clear();
  }

  reset(): void {
    this.dispose();
    this.current = undefined;
    this.schedule(0);
  }

  schedule(delay?: number): void {
    if (this.timer) {
      clearTimeout(this.timer);
    }
    const wait = delay ?? settings().debounceMs;
    this.timer = setTimeout(() => void this.update(false), Math.max(0, wait));
  }

  private render(state: ViewState): void {
    this.state = state;
    const { html, targets } = renderBody(state);
    this.targets = targets;
    void this.view?.webview.postMessage({ type: "render", html });
  }

  private sessionFor(uri: vscode.Uri): Session | undefined {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) {
      return undefined;
    }
    const key = folder.uri.toString();
    let session = this.sessions.get(key);
    if (!session) {
      const config = settings(folder.uri);
      const root = folder.uri.fsPath;
      const repoRoot = resolveAgainst(root, config.repoRoot, ".");
      const indexDir = resolveAgainst(root, config.indexPath, ".r2g");
      session = new Session(folder, repoRoot, indexDir, config, this.log);
      this.sessions.set(key, session);
    }
    return session;
  }

  /** Re-render for the active editor's cursor. */
  async update(force: boolean): Promise<void> {
    if (!this.view?.visible) {
      return;
    }
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file") {
      return; // keep showing the last symbol while focus is elsewhere
    }
    const file = editor.document.uri.fsPath;
    const session = this.sessionFor(editor.document.uri);
    if (!session) {
      this.render({ kind: "idle", message: "This file is not in a workspace folder." });
      return;
    }
    const rel = repoRelative(session.repoRoot, file);
    if (rel === null) {
      this.render({ kind: "idle", message: "This file is outside the indexed repository." });
      return;
    }
    if (!languageOf(file)) {
      this.render({ kind: "unsupported", path: rel });
      return;
    }
    if (!indexPresent(session.indexDir)) {
      this.render({ kind: "noIndex", indexDir: session.indexDir, building: this.building });
      return;
    }
    const line = editor.selection.active.line + 1;
    const seq = ++this.sequence;
    const started = Date.now();
    try {
      const navigator = await session.ready();
      const model = await navigator.at(rel, line, force);
      if (seq !== this.sequence) {
        return; // the cursor moved on while this was in flight
      }
      if (!model) {
        this.render({ kind: "notIndexed", path: rel });
        return;
      }
      this.current = { session, model };
      this.render({ kind: "neighbourhood", model, elapsedMs: Date.now() - started });
    } catch (error) {
      if (seq !== this.sequence) {
        return;
      }
      this.showError(error);
    }
  }

  private showError(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    this.log.appendLine(`error: ${message}`);
    const actions: PageCommand[] =
      error instanceof ToolCallError ? ["refresh", "status"] : ["restart", "settings", "status"];
    this.render({
      kind: "error",
      message:
        error instanceof ToolCallError
          ? message
          : `repo2graph-mcp is not answering: ${message}. Check repo2graph.mcpExecutable (it needs pip install "repo2graph[mcp]").`,
      actions,
    });
  }

  private async onMessage(message: unknown): Promise<void> {
    const m = message as { type?: unknown; ref?: unknown; command?: unknown };
    if (m?.type === "ready") {
      this.render(this.state);
    } else if (m?.type === "open" && typeof m.ref === "number" && Number.isInteger(m.ref)) {
      const target = this.targets[m.ref];
      if (target) {
        await this.open(target);
      }
    } else if (m?.type === "command" && isPageCommand(m.command)) {
      await this.command(m.command);
    }
  }

  private async open(target: Target, owner?: Session, column?: vscode.ViewColumn): Promise<void> {
    const session = owner ?? this.current?.session ?? this.activeSession();
    if (!session) {
      return;
    }
    // Targets are repo-relative paths straight from the index: refuse any that
    // would land outside the repository.
    const file = path.resolve(session.repoRoot, target.path);
    if (repoRelative(session.repoRoot, file) === null || !fs.existsSync(file)) {
      void vscode.window.showWarningMessage(`repo2graph: ${target.path} is not in the working tree.`);
      return;
    }
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    const position = new vscode.Position(Math.max(0, target.line - 1), 0);
    await vscode.window.showTextDocument(doc, { selection: new vscode.Range(position, position), viewColumn: column });
  }

  private activeSession(): Session | undefined {
    const editor = vscode.window.activeTextEditor;
    if (editor) {
      return this.sessionFor(editor.document.uri);
    }
    const folder = vscode.workspace.workspaceFolders?.[0];
    return folder ? this.sessionFor(folder.uri) : undefined;
  }

  async command(command: PageCommand): Promise<void> {
    switch (command) {
      case "build":
        return this.build(false);
      case "rebuild":
        return this.build(true);
      case "status":
        return this.indexStatus();
      case "reveal":
        return this.revealInGraph();
      case "impact":
        return this.showImpact();
      case "cochange":
        return this.goToCoChanged();
      case "refresh":
        this.current?.session.navigator?.invalidate();
        return this.update(true);
      case "restart":
        this.reset();
        return;
      case "settings":
        await vscode.commands.executeCommand("workbench.action.openSettings", "repo2graph");
        return;
      case "more":
        if (this.current) {
          const navigator = await this.current.session.ready();
          const model = await navigator.more(this.current.model);
          this.current = { ...this.current, model };
          this.render({ kind: "neighbourhood", model });
        }
        return;
    }
  }

  /** The node under the cursor, resolving it if the view has not yet. */
  private async currentNode(): Promise<{ session: Session; node: GraphNode; model?: Neighbourhood } | undefined> {
    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.uri.scheme === "file") {
      const session = this.sessionFor(editor.document.uri);
      const rel = session && repoRelative(session.repoRoot, editor.document.uri.fsPath);
      if (session && rel && indexPresent(session.indexDir)) {
        const navigator = await session.ready();
        const model = await navigator.at(rel, editor.selection.active.line + 1);
        if (model) {
          return { session, node: model.node, model };
        }
      }
    }
    if (this.current) {
      return { session: this.current.session, node: this.current.model.node, model: this.current.model };
    }
    void vscode.window.showInformationMessage("repo2graph: place the cursor on an indexed symbol first.");
    return undefined;
  }

  async revealInGraph(): Promise<void> {
    const found = await this.guard(() => this.currentNode());
    if (!found) {
      return;
    }
    const graph = graphHtmlPath(found.session.indexDir);
    if (!fs.existsSync(graph)) {
      const choice = await vscode.window.showWarningMessage(
        "repo2graph: this index has no graph.html.",
        "Draw graph",
      );
      if (choice !== "Draw graph") {
        return;
      }
      const config = settings(found.session.folder.uri);
      const result = await this.runCli(config.cli, mapArgs(found.session.indexDir), found.session.repoRoot, "Drawing graph.html");
      if (result !== 0 || !fs.existsSync(graph)) {
        return;
      }
    }
    // Opened through a small forwarding page, because some OS handlers drop
    // the #fragment of a file URL -- and the fragment is the seed.
    const dir = this.context.globalStorageUri.fsPath;
    fs.mkdirSync(dir, { recursive: true });
    const page = path.join(dir, "reveal-graph.html");
    fs.writeFileSync(page, revealPage(graph, found.node.node_id), "utf8");
    await vscode.env.openExternal(vscode.Uri.file(page));
  }

  async showImpact(): Promise<void> {
    await this.guard(async () => {
      const found = await this.currentNode();
      if (!found) {
        return;
      }
      const navigator = await found.session.ready();
      const impact = await navigator.impact(found.node.node_id);
      const panel = vscode.window.createWebviewPanel(
        "repo2graph.impact",
        `Impact: ${found.node.qualname || found.node.name}`,
        vscode.ViewColumn.Beside,
        { enableScripts: true, enableCommandUris: false, localResourceRoots: [] },
      );
      const { html, targets } = renderBody({ kind: "impact", node: found.node, impact });
      panel.webview.html = shellHtml(makeNonce(), html);
      panel.webview.onDidReceiveMessage(async (message: { type?: unknown; ref?: unknown }) => {
        if (message?.type === "open" && typeof message.ref === "number" && targets[message.ref]) {
          await this.open(targets[message.ref], found.session, vscode.ViewColumn.One);
        }
      });
    });
  }

  async goToCoChanged(): Promise<void> {
    await this.guard(async () => {
      const found = await this.currentNode();
      if (!found?.model) {
        return;
      }
      const rows = found.model.cochange;
      if (!rows.length) {
        void vscode.window.showInformationMessage(
          `repo2graph: no co-change history for ${found.node.path}. Build with --git-history (see repo2graph.buildArgs) to record it.`,
        );
        return;
      }
      const pick = await vscode.window.showQuickPick(
        rows.map((row) => ({ label: row.path, description: `${row.count} commits together`, row })),
        { placeHolder: `Files edited together with ${found.node.path}` },
      );
      if (pick) {
        await this.open({ path: pick.row.path, line: 1 }, found.session);
      }
    });
  }

  async indexStatus(): Promise<void> {
    const session = this.activeSession();
    if (!session) {
      return;
    }
    if (!indexPresent(session.indexDir)) {
      const choice = await vscode.window.showWarningMessage(
        `repo2graph: no index at ${session.indexDir}.`,
        "Build index",
      );
      if (choice) {
        await this.build(false);
      }
      return;
    }
    const config = settings(session.folder.uri);
    try {
      const result = await run(config.cli, statusArgs(session.indexDir, session.repoRoot), { cwd: session.repoRoot });
      if (result.code !== 0) {
        throw new Error(result.stderr.trim() || `exit code ${result.code}`);
      }
      const summary = summarizeStatus(parseStatus(result.stdout));
      const show = summary.status === "stale" ? vscode.window.showWarningMessage : vscode.window.showInformationMessage;
      const choice = await show(summary.headline, "Rebuild index", "Show report");
      if (choice === "Rebuild index") {
        await this.build(true);
      } else if (choice === "Show report") {
        const report = await run(config.cli, statusArgs(session.indexDir, session.repoRoot, false), { cwd: session.repoRoot });
        this.log.appendLine(report.stdout || report.stderr);
        this.log.show(true);
      }
    } catch (error) {
      void vscode.window.showErrorMessage(`repo2graph index-status failed: ${error instanceof Error ? error.message : error}`);
    }
  }

  async build(incremental: boolean): Promise<void> {
    const session = this.activeSession();
    if (!session || this.building) {
      return;
    }
    const config = settings(session.folder.uri);
    this.building = true;
    if (!indexPresent(session.indexDir)) {
      this.render({ kind: "noIndex", indexDir: session.indexDir, building: true });
    }
    try {
      const code = await this.runCli(
        config.cli,
        buildArgs(session.repoRoot, session.indexDir, config.buildArgs, incremental && indexPresent(session.indexDir)),
        session.repoRoot,
        incremental ? "Rebuilding the repo2graph index" : "Building the repo2graph index",
      );
      if (code === 0) {
        session.navigator?.invalidate();
        this.current = undefined;
      }
    } finally {
      this.building = false;
      void this.update(true);
    }
  }

  private async runCli(cli: string, args: string[], cwd: string, title: string): Promise<number | null> {
    this.log.appendLine(`$ ${cli} ${args.join(" ")}`);
    return vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `repo2graph: ${title}` },
      async () => {
        try {
          const result = await run(cli, args, { cwd, onStderr: (chunk) => this.log.append(chunk) });
          if (result.code !== 0) {
            this.log.show(true);
            void vscode.window.showErrorMessage(`repo2graph: ${title} failed (exit ${result.code}). See the repo2graph output.`);
          }
          return result.code;
        } catch (error) {
          void vscode.window.showErrorMessage(
            `repo2graph: ${error instanceof Error ? error.message : error}. Set repo2graph.executable to the repo2graph CLI.`,
          );
          return null;
        }
      },
    );
  }

  private async guard<T>(action: () => Promise<T>): Promise<T | undefined> {
    try {
      return await action();
    } catch (error) {
      void vscode.window.showErrorMessage(`repo2graph: ${error instanceof Error ? error.message : error}`);
      return undefined;
    }
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("repo2graph");
  const controller = new Controller(context, log);
  const commands: Array<[string, () => unknown]> = [
    ["repo2graph.revealInGraph", () => controller.revealInGraph()],
    ["repo2graph.showImpact", () => controller.showImpact()],
    ["repo2graph.indexStatus", () => controller.indexStatus()],
    ["repo2graph.goToCoChanged", () => controller.goToCoChanged()],
    ["repo2graph.buildIndex", () => controller.build(false)],
    ["repo2graph.rebuildIndex", () => controller.build(true)],
    ["repo2graph.refresh", () => controller.command("refresh")],
    ["repo2graph.restartServer", () => controller.reset()],
  ];
  context.subscriptions.push(
    log,
    { dispose: () => controller.dispose() },
    vscode.window.registerWebviewViewProvider("repo2graph.neighbourhood", controller),
    ...commands.map(([id, fn]) => vscode.commands.registerCommand(id, fn)),
    vscode.window.onDidChangeTextEditorSelection(() => controller.schedule()),
    vscode.window.onDidChangeActiveTextEditor(() => controller.schedule()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("repo2graph")) {
        controller.reset();
      }
    }),
  );
}

export function deactivate(): void {
  // The controller is disposed through context.subscriptions.
}
