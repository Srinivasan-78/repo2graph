// Paths: where the index is, what a document is called inside it, and which
// executables to run. No `vscode` import.

import * as fs from "node:fs";
import * as nodePath from "node:path";
import { pathToFileURL } from "node:url";
import { EXT_LANG } from "./generated";

type PathApi = typeof nodePath.posix;

/** `setting` resolved against `base`; empty means `fallback`. */
export function resolveAgainst(base: string, setting: string, fallback: string, api: PathApi = nodePath): string {
  const value = setting.trim() || fallback;
  return api.isAbsolute(value) ? api.normalize(value) : api.join(base, value);
}

/** `file` relative to `root`, with forward slashes, or null when it is outside. */
export function repoRelative(root: string, file: string, api: PathApi = nodePath): string | null {
  const rel = api.relative(root, file);
  if (!rel || rel.startsWith("..") || api.isAbsolute(rel)) {
    return null;
  }
  return rel.split(api.sep).join("/");
}

/** The language repo2graph reads `file` as, or null when it does not parse it. */
export function languageOf(file: string): string | null {
  const dot = file.lastIndexOf(".");
  const slash = Math.max(file.lastIndexOf("/"), file.lastIndexOf("\\"));
  if (dot <= slash) {
    return null;
  }
  return EXT_LANG[file.slice(dot).toLowerCase()] ?? null;
}

/** Whether `indexDir` holds an index the server can open. */
export function indexPresent(indexDir: string): boolean {
  return (
    fs.existsSync(nodePath.join(indexDir, "agent", "manifest.json")) &&
    fs.existsSync(nodePath.join(indexDir, "agent", "chunks.jsonl"))
  );
}

export function graphHtmlPath(indexDir: string): string {
  return nodePath.join(indexDir, "human", "graph.html");
}

/**
 * The server executable: the setting when given; else the `repo2graph-mcp`
 * beside `cli` when `cli` is a path (a virtual environment's bin directory);
 * else the bare name, looked up on PATH.
 */
export function serverExecutable(setting: string, cli: string, platform: NodeJS.Platform = process.platform): string {
  if (setting.trim()) {
    return setting.trim();
  }
  const api = platform === "win32" ? nodePath.win32 : nodePath.posix;
  if (cli.includes("/") || (platform === "win32" && cli.includes("\\"))) {
    const ext = platform === "win32" ? api.extname(cli) : "";
    return api.join(api.dirname(cli), `repo2graph-mcp${ext}`);
  }
  return "repo2graph-mcp";
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** The URL of graph.html opened on one node: `file:///.../graph.html#node=<id>`. */
export function graphUrl(graphHtml: string, nodeId: string): string {
  return `${pathToFileURL(graphHtml).href}#node=${encodeURIComponent(nodeId)}`;
}

/**
 * A one-line page that forwards to graph.html on `nodeId`.
 *
 * Some OS handlers (xdg-open, Windows' file association) drop the `#fragment`
 * of a file URL they open. Opening this page -- whose own URL needs no
 * fragment -- and letting it navigate keeps the fragment intact.
 */
export function revealPage(graphHtml: string, nodeId: string): string {
  const target = escapeHtml(graphUrl(graphHtml, nodeId));
  return [
    "<!doctype html>",
    '<html lang="en"><head><meta charset="utf-8">',
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'\">",
    `<meta http-equiv="refresh" content="0;url=${target}">`,
    "<title>repo2graph</title></head>",
    `<body><a href="${target}">Open the repo2graph graph</a></body></html>`,
    "",
  ].join("\n");
}
