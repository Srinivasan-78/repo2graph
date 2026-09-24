#!/usr/bin/env node
// SPDX-FileCopyrightText: 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
// SPDX-License-Identifier: MIT
"use strict";

// npx launcher for the repo2graph MCP server. This package ships no server
// code of its own -- the real implementation is `repo2graph-mcp`, a Python
// console script published on PyPI as part of `repo2graph[mcp]`. This file's
// only job is finding a way to run that script and handing off to it with
// this process's stdio wired straight through (the MCP transport is stdio
// JSON-RPC, so anything this script prints itself would corrupt the stream --
// every message below goes to stderr, matching the discipline the Python
// server keeps for its own diagnostics).
//
// Resolution order, matching docs/mcp.md's own install precedence:
//   1. `uvx` on PATH -- `uvx --from "repo2graph[mcp]" repo2graph-mcp <args>`.
//      Fetches and runs on demand; nothing to install ahead of time.
//   2. `repo2graph-mcp` already on PATH -- an existing `pip install
//      "repo2graph[mcp]"` or a virtualenv script, run directly.
//   3. `pipx` on PATH -- `pipx run --spec "repo2graph[mcp]" repo2graph-mcp
//      <args>`, pipx's own on-demand-run equivalent of `uvx`.
//   4. None of the above: print the actual install instructions and exit
//      non-zero. This script never installs a Python toolchain behind the
//      user's back.

const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const PACKAGE_SPEC = "repo2graph[mcp]";
const CONSOLE_SCRIPT = "repo2graph-mcp";

function commandExists(cmd) {
  // A manual PATH walk rather than spawning `cmd --version`: this only needs
  // to know whether something is on PATH, not run it, and `--version` is not
  // a contract any of these three commands actually promises to support.
  const pathEnv = process.env.PATH || process.env.Path || "";
  const dirs = pathEnv.split(path.delimiter).filter(Boolean);
  const isWindows = process.platform === "win32";
  const exts = isWindows
    ? (process.env.PATHEXT || ".EXE;.CMD;.BAT;.COM").split(";")
    : [""];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = path.join(dir, cmd + ext);
      try {
        const st = fs.statSync(candidate);
        if (st.isFile()) return true;
      } catch {
        // not found here, keep looking
      }
    }
  }
  return false;
}

function run(cmd, args) {
  const result = spawnSync(cmd, args, { stdio: "inherit" });
  if (result.error) {
    process.stderr.write(
      `repo2graph-mcp: failed to run ${cmd}: ${result.error.message}\n`
    );
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

function main() {
  const forwardedArgs = process.argv.slice(2);

  if (commandExists("uvx")) {
    run("uvx", ["--from", PACKAGE_SPEC, CONSOLE_SCRIPT, ...forwardedArgs]);
    return;
  }

  if (commandExists(CONSOLE_SCRIPT)) {
    run(CONSOLE_SCRIPT, forwardedArgs);
    return;
  }

  if (commandExists("pipx")) {
    run("pipx", ["run", "--spec", PACKAGE_SPEC, CONSOLE_SCRIPT, ...forwardedArgs]);
    return;
  }

  process.stderr.write(
    [
      "repo2graph-mcp: no working Python launcher found on PATH.",
      "",
      "This package is a thin npx launcher; the actual MCP server is Python.",
      "Install one of the following, then re-run this command:",
      "",
      "  - uv (recommended, no separate install step after this):",
      "      https://docs.astral.sh/uv/getting-started/installation/",
      "  - pipx:",
      "      https://pipx.pypa.io/stable/installation/",
      "  - pip, then run the server directly instead of through npx:",
      `      pip install "${PACKAGE_SPEC}"`,
      `      ${CONSOLE_SCRIPT} <path-to-your-project>`,
      "",
      "Full install and client configuration:",
      "  https://github.com/Srinivasan-78/repo2graph/blob/main/docs/mcp.md",
      "",
    ].join("\n")
  );
  process.exit(1);
}

main();
