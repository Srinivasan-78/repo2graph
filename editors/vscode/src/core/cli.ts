// Run the repo2graph CLI and collect its output. No `vscode` import.

import { spawn } from "node:child_process";

export interface RunResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export interface RunOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  /** Called with each chunk of stderr, for a live log. */
  onStderr?: (chunk: string) => void;
  timeoutMs?: number;
}

/** Run `command args`, never through a shell, and resolve with its output. */
export function run(command: string, args: readonly string[], options: RunOptions = {}): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, [...args], {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.setEncoding("utf8");
    proc.stderr.setEncoding("utf8");
    proc.stdout.on("data", (chunk: string) => (stdout += chunk));
    proc.stderr.on("data", (chunk: string) => {
      stderr += chunk;
      options.onStderr?.(chunk);
    });
    const timer = options.timeoutMs
      ? setTimeout(() => proc.kill(), options.timeoutMs)
      : undefined;
    proc.on("error", (error) => {
      if (timer) clearTimeout(timer);
      reject(new Error(`could not run ${command}: ${error.message}`));
    });
    proc.on("close", (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
  });
}
