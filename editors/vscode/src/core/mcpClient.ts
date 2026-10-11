// A minimal MCP client for one long-lived `repo2graph-mcp` process over stdio.
//
// The transport is newline-delimited JSON-RPC 2.0: `initialize`, then the
// `notifications/initialized` notification, then `tools/call` as often as the
// editor likes. Keeping one warm process is what makes a cursor move cheap --
// starting the server (and loading the index) costs about a second; a call on
// a warm server costs milliseconds.
//
// No `vscode` import: this module is exercised by `node --test` against a real
// server process.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

export const PROTOCOL_VERSION = "2025-06-18";

/** What a `tools/call` answered: its text, and whether the server flagged it as an error. */
export interface ToolResult {
  text: string;
  isError: boolean;
  /** `_meta.nextCursor`, when the reply is one page of several. */
  nextCursor?: string;
}

/** Anything that can call a tool: the real client, or a stand-in in a test. */
export interface ToolCaller {
  callTool(name: string, args: Record<string, unknown>): Promise<ToolResult>;
}

export interface McpClientOptions {
  command: string;
  args: readonly string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  /** For `initialize`, which waits for the server to open the index (default 60 s). */
  initTimeoutMs?: number;
  /** For every later request (default 30 s). */
  requestTimeoutMs?: number;
  clientName?: string;
  clientVersion?: string;
}

/** The server process is gone, or never started. */
export class McpClosedError extends Error {
  constructor(
    message: string,
    readonly stderr: string,
  ) {
    super(message);
    this.name = "McpClosedError";
  }
}

/** A JSON-RPC error response. */
export class McpRpcError extends Error {
  constructor(
    message: string,
    readonly code: number,
  ) {
    super(message);
    this.name = "McpRpcError";
  }
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

// Bounds on what is buffered from the server: a reply that never ends its line
// must not grow without limit, and only the tail of stderr is worth keeping.
const MAX_LINE_CHARS = 64 * 1024 * 1024;
const MAX_STDERR_CHARS = 16 * 1024;

export class McpClient implements ToolCaller {
  private readonly proc: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<number, Pending>();
  private readonly exitListeners: Array<(error: McpClosedError) => void> = [];
  private nextId = 1;
  private buffer = "";
  private stderrTail = "";
  private closedWith: McpClosedError | undefined;
  private disposed = false;
  serverInfo: { name?: string; version?: string } = {};
  protocolVersion = "";

  /** Spawn the server and complete the MCP handshake. */
  static async start(options: McpClientOptions): Promise<McpClient> {
    const client = new McpClient(options);
    try {
      await client.initialize(options);
    } catch (error) {
      client.dispose();
      throw error;
    }
    return client;
  }

  private constructor(private readonly options: McpClientOptions) {
    this.proc = spawn(options.command, [...options.args], {
      cwd: options.cwd,
      env: options.env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stderr.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    this.proc.stderr.on("data", (chunk: string) => {
      this.stderrTail = (this.stderrTail + chunk).slice(-MAX_STDERR_CHARS);
    });
    // A write to a server that already died fails with EPIPE; the exit handler
    // below is what reports that, with the server's own last words.
    this.proc.stdin.on("error", () => undefined);
    this.proc.on("error", (error) => {
      this.close(`could not start ${options.command}: ${error.message}`);
    });
    // `close` rather than `exit`: it fires once stderr has drained, so the
    // reason the server gave for stopping is in the error it produces.
    this.proc.on("close", (code, signal) => {
      const how = signal ? `was killed by ${signal}` : `exited with code ${code}`;
      this.close(`${options.command} ${how}`);
    });
  }

  /** True until the process exits or the client is disposed. */
  get alive(): boolean {
    return this.closedWith === undefined;
  }

  /** The last lines the server wrote to stderr: where it explains an exit. */
  get stderr(): string {
    return this.stderrTail;
  }

  /** Called once if the server goes away without being disposed. */
  onExit(listener: (error: McpClosedError) => void): void {
    this.exitListeners.push(listener);
  }

  async callTool(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<ToolResult> {
    const result = (await this.request(
      "tools/call",
      { name, arguments: args },
      timeoutMs ?? this.options.requestTimeoutMs ?? 30_000,
    )) as {
      content?: Array<{ type?: string; text?: string }>;
      isError?: boolean;
      _meta?: { nextCursor?: unknown };
    };
    const text = (result?.content ?? [])
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text as string)
      .join("\n");
    const cursor = result?._meta?.nextCursor;
    return {
      text,
      isError: result?.isError === true,
      ...(typeof cursor === "string" && cursor ? { nextCursor: cursor } : {}),
    };
  }

  /** Stop the server. Pending calls fail with McpClosedError. */
  dispose(): void {
    if (this.disposed) {
      return;
    }
    this.disposed = true;
    this.close("the client was disposed");
    try {
      this.proc.stdin.end();
    } catch {
      // already closed
    }
    if (this.proc.exitCode === null && this.proc.signalCode === null) {
      this.proc.kill();
    }
  }

  private async initialize(options: McpClientOptions): Promise<void> {
    const result = (await this.request(
      "initialize",
      {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: {
          name: options.clientName ?? "repo2graph-vscode",
          version: options.clientVersion ?? "0",
        },
      },
      options.initTimeoutMs ?? 60_000,
    )) as { protocolVersion?: string; serverInfo?: { name?: string; version?: string } };
    this.protocolVersion = String(result?.protocolVersion ?? "");
    this.serverInfo = result?.serverInfo ?? {};
    this.notify("notifications/initialized", {});
  }

  private request(method: string, params: unknown, timeoutMs: number): Promise<unknown> {
    if (this.closedWith) {
      return Promise.reject(this.closedWith);
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new McpRpcError(`${method} timed out after ${timeoutMs} ms`, -32001));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  private notify(method: string, params: unknown): void {
    this.send({ jsonrpc: "2.0", method, params });
  }

  private send(message: unknown): void {
    if (this.closedWith) {
      return;
    }
    this.proc.stdin.write(JSON.stringify(message) + "\n");
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        this.onLine(line);
      }
      newline = this.buffer.indexOf("\n");
    }
    if (this.buffer.length > MAX_LINE_CHARS) {
      this.close(`${this.options.command} wrote a line longer than ${MAX_LINE_CHARS} characters`);
      this.proc.kill();
    }
  }

  private onLine(line: string): void {
    let message: {
      id?: unknown;
      method?: unknown;
      result?: unknown;
      error?: { message?: unknown; code?: unknown };
    };
    try {
      message = JSON.parse(line);
    } catch {
      return; // stdout carries only JSON-RPC; anything else is not ours to act on
    }
    if (!message || typeof message !== "object") {
      return;
    }
    if (typeof message.method === "string") {
      if (message.id !== undefined && message.id !== null) {
        // A request from the server. `ping` is the only one a client with no
        // declared capabilities should see; anything else is politely refused.
        this.send(
          message.method === "ping"
            ? { jsonrpc: "2.0", id: message.id, result: {} }
            : {
                jsonrpc: "2.0",
                id: message.id,
                error: { code: -32601, message: `method not supported: ${message.method}` },
              },
        );
      }
      return; // a notification (logging, progress): nothing the view needs
    }
    if (typeof message.id !== "number") {
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) {
      return; // answered after its timeout
    }
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) {
      pending.reject(
        new McpRpcError(
          String(message.error.message ?? "JSON-RPC error"),
          Number(message.error.code ?? -32603),
        ),
      );
    } else {
      pending.resolve(message.result);
    }
  }

  private close(reason: string): void {
    if (this.closedWith) {
      return;
    }
    const stderr = this.stderrTail.trim();
    const error = new McpClosedError(
      stderr ? `${reason}: ${stderr.split("\n").slice(-3).join(" ")}` : reason,
      stderr,
    );
    this.closedWith = error;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    if (!this.disposed) {
      for (const listener of this.exitListeners) {
        listener(error);
      }
    }
  }
}
