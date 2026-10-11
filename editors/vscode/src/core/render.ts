// HTML for the webview. Pure string building, no `vscode` import, so the
// escaping can be tested.
//
// Every name, path and note comes from the indexed repository, which is
// untrusted, so all of it goes through escapeHtml. The page itself runs one
// nonce'd script under a CSP that allows nothing else: no remote resources, no
// inline handlers, no eval. Rows do not carry paths back to the extension --
// each carries an index into the `targets` list the extension kept, so a page
// can only ask to open something the extension itself listed.

import type { CochangeRow, GraphNode, Impact, ImpactRow, Neighbourhood, NeighbourRow } from "./neighbourhood";

/** Where a row navigates: a repo-relative path and a 1-based line. */
export interface Target {
  path: string;
  line: number;
  nodeId?: string;
}

/** Commands a page may ask for; the extension refuses any other. */
export const PAGE_COMMANDS = [
  "build",
  "rebuild",
  "status",
  "reveal",
  "impact",
  "cochange",
  "more",
  "refresh",
  "settings",
  "restart",
] as const;
export type PageCommand = (typeof PAGE_COMMANDS)[number];

export type ViewState =
  | { kind: "idle"; message: string }
  | { kind: "loading"; message: string }
  | { kind: "noIndex"; indexDir: string; building: boolean }
  | { kind: "unsupported"; path: string }
  | { kind: "notIndexed"; path: string }
  | { kind: "error"; message: string; actions: PageCommand[] }
  | { kind: "neighbourhood"; model: Neighbourhood; elapsedMs?: number }
  | { kind: "impact"; node: GraphNode; impact: Impact };

export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** A fresh nonce for one page. */
export function makeNonce(random: () => number = Math.random): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 32; i++) {
    out += chars[Math.floor(random() * chars.length)];
  }
  return out;
}

export function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'none'",
    `style-src 'nonce-${nonce}'`,
    `script-src 'nonce-${nonce}'`,
    "img-src 'none'",
    "font-src 'none'",
    "connect-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
  ].join("; ");
}

const STYLE = `
body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); padding: 0 8px 8px; }
h2 { font-size: 1em; margin: 10px 0 4px; text-transform: uppercase; letter-spacing: .04em; opacity: .8; }
h1 { font-size: 1.1em; margin: 8px 0 2px; word-break: break-all; }
.sub { opacity: .75; margin: 0 0 6px; word-break: break-all; }
.note { border-left: 3px solid var(--vscode-editorWarning-foreground); padding: 4px 8px; margin: 6px 0; }
.row { display: block; width: 100%; text-align: left; background: none; border: none; color: inherit; padding: 2px 4px; cursor: pointer; font: inherit; }
.row:hover, .row:focus { background: var(--vscode-list-hoverBackground); outline: none; }
.where { opacity: .7; margin-left: 6px; }
.amb { color: var(--vscode-editorWarning-foreground); margin-left: 6px; }
.actions { margin: 8px 0; }
.btn { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 4px 10px; margin: 2px 4px 2px 0; cursor: pointer; font: inherit; }
.btn.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
.empty { opacity: .6; padding: 2px 4px; }
.timing { opacity: .5; font-size: .9em; margin-top: 10px; }
`;

const SCRIPT = `
const vscode = acquireVsCodeApi();
const root = document.getElementById("root");
window.addEventListener("message", (event) => {
  const msg = event.data;
  if (msg && msg.type === "render" && typeof msg.html === "string") root.innerHTML = msg.html;
});
document.addEventListener("click", (event) => {
  const el = event.target instanceof Element ? event.target.closest("[data-ref],[data-cmd]") : null;
  if (!el) return;
  event.preventDefault();
  if (el.hasAttribute("data-ref")) vscode.postMessage({ type: "open", ref: Number(el.getAttribute("data-ref")) });
  else vscode.postMessage({ type: "command", command: el.getAttribute("data-cmd") });
});
vscode.postMessage({ type: "ready" });
`;

/** The page shell: CSP, one nonce'd style and script, and the body. */
export function shellHtml(nonce: string, body: string): string {
  return [
    "<!DOCTYPE html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="UTF-8">',
    `<meta http-equiv="Content-Security-Policy" content="${contentSecurityPolicy(nonce)}">`,
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
    `<style nonce="${nonce}">${STYLE}</style>`,
    "</head>",
    "<body>",
    `<div id="root">${body}</div>`,
    `<script nonce="${nonce}">${SCRIPT}</script>`,
    "</body>",
    "</html>",
  ].join("\n");
}

class Builder {
  readonly parts: string[] = [];
  readonly targets: Target[] = [];

  add(html: string): void {
    this.parts.push(html);
  }

  target(target: Target): number {
    this.targets.push(target);
    return this.targets.length - 1;
  }

  button(command: PageCommand, label: string, secondary = false): string {
    return `<button class="btn${secondary ? " secondary" : ""}" data-cmd="${command}">${escapeHtml(label)}</button>`;
  }
}

function lineOf(node: { start_line: number | null }): number {
  return typeof node.start_line === "number" && node.start_line > 0 ? node.start_line : 1;
}

function where(node: { path: string; start_line: number | null }): string {
  return node.start_line ? `${node.path}:${node.start_line}` : node.path;
}

function displayName(node: { qualname?: string; name?: string; node_id: string }): string {
  return node.qualname || node.name || node.node_id;
}

function notesHtml(b: Builder, notes: readonly string[]): void {
  for (const note of notes) {
    b.add(`<div class="note">${escapeHtml(note)}</div>`);
  }
}

function neighbourRow(b: Builder, row: NeighbourRow): void {
  const ref = b.target({ path: row.path, line: lineOf(row), nodeId: row.node_id });
  const kind = row.kind || row.type || "";
  const ambiguous =
    row.confidence !== null && row.confidence < 1
      ? `<span class="amb" title="ambiguous call: ${escapeHtml(
          `${row.confidence}${row.candidate_count ? ` of ${row.candidate_count} candidates` : ""}`,
        )}">?</span>`
      : "";
  const count = row.edge === "CO_CHANGE" && row.count ? ` (${row.count} commits)` : "";
  b.add(
    `<button class="row" data-ref="${ref}" title="${escapeHtml(row.node_id)}">` +
      `${escapeHtml(displayName(row))}${escapeHtml(count)}` +
      `<span class="where">${escapeHtml(kind ? `${kind} · ` : "")}${escapeHtml(where(row))}</span>` +
      `${ambiguous}</button>`,
  );
}

function cochangeRows(b: Builder, rows: readonly CochangeRow[]): void {
  for (const row of rows) {
    const ref = b.target({ path: row.path, line: 1, nodeId: row.node_id });
    b.add(
      `<button class="row" data-ref="${ref}">${escapeHtml(row.path)}` +
        `<span class="where">${escapeHtml(`${row.count} commits together`)}</span></button>`,
    );
  }
}

function renderNeighbourhood(b: Builder, model: Neighbourhood, elapsedMs?: number): void {
  const node = model.node;
  const ref = b.target({ path: node.path, line: lineOf(node), nodeId: node.node_id });
  b.add(`<h1><button class="row" data-ref="${ref}">${escapeHtml(displayName(node))}</button></h1>`);
  b.add(
    `<p class="sub">${escapeHtml([node.kind || node.type, where(node)].filter(Boolean).join(" · "))}` +
      (node.end_line && node.start_line ? escapeHtml(`-${node.end_line}`) : "") +
      "</p>",
  );
  if (model.stale) {
    b.add(`<div class="actions">${b.button("rebuild", "Rebuild index")}${b.button("status", "Index status", true)}</div>`);
  }
  notesHtml(b, model.notes);
  b.add(
    `<div class="actions">${b.button("impact", "Show impact", true)}${b.button("reveal", "Reveal in graph", true)}` +
      `${b.button("cochange", "Co-changed files", true)}</div>`,
  );
  if (model.sections.length === 0) {
    b.add('<p class="empty">No callers, callees or other relationships in the index.</p>');
  }
  for (const section of model.sections) {
    b.add(`<h2>${escapeHtml(section.label)} (${section.rows.length})</h2>`);
    for (const row of section.rows) {
      neighbourRow(b, row);
    }
  }
  b.add(`<h2>Co-changed files (${model.cochange.length})</h2>`);
  if (model.cochange.length) {
    cochangeRows(b, model.cochange);
  } else {
    b.add('<p class="empty">No co-change history recorded for this file (build with --git-history).</p>');
  }
  if (model.nextCursor) {
    b.add(`<div class="actions">${b.button("more", "Load more", true)}</div>`);
  } else if (model.truncated) {
    b.add('<p class="empty">Listing stops at the server\'s ceiling.</p>');
  }
  if (elapsedMs !== undefined) {
    b.add(`<p class="timing">${escapeHtml(`${Math.round(elapsedMs)} ms`)}</p>`);
  }
}

function impactSection(b: Builder, label: string, rows: readonly ImpactRow[]): void {
  b.add(`<h2>${escapeHtml(label)} (${rows.length})</h2>`);
  if (!rows.length) {
    b.add('<p class="empty">none</p>');
  }
  for (const row of rows) {
    const ref = b.target({ path: row.path, line: lineOf(row), nodeId: row.node_id });
    b.add(
      `<button class="row" data-ref="${ref}" title="${escapeHtml(row.node_id)}">${escapeHtml(row.node_id.replace(/^(sym|file):/, ""))}` +
        `<span class="where">${escapeHtml(`hop ${row.hop} · ${where(row)}`)}</span></button>`,
    );
  }
}

function renderImpact(b: Builder, node: GraphNode, impact: Impact): void {
  b.add(`<h1>${escapeHtml(`Impact of ${displayName(node)}`)}</h1>`);
  const s = impact.summary;
  b.add(
    `<p class="sub">${escapeHtml(
      `${s.symbols_affected} symbols in ${s.files_affected} files, up to ${s.max_hop_reached} hops` +
        (s.truncated ? " (truncated)" : ""),
    )}</p>`,
  );
  notesHtml(b, impact.notes);
  impactSection(b, "Callers", impact.callers);
  impactSection(b, "Subclasses", impact.subclasses);
  impactSection(b, "Importers", impact.importers);
  b.add(`<h2>Co-changed files (${impact.cochange.length})</h2>`);
  cochangeRows(
    b,
    impact.cochange.map((c) => ({ node_id: `file:${c.path}`, path: c.path, count: c.count })),
  );
}

/** The body HTML for a state, and the navigation targets its rows refer to. */
export function renderBody(state: ViewState): { html: string; targets: Target[] } {
  const b = new Builder();
  switch (state.kind) {
    case "idle":
    case "loading":
      b.add(`<p class="empty">${escapeHtml(state.message)}</p>`);
      break;
    case "noIndex":
      b.add("<h1>No repo2graph index yet</h1>");
      b.add(
        `<p class="sub">${escapeHtml(`Nothing at ${state.indexDir}. Build one to see callers, callees and co-changed files for the symbol at the cursor.`)}</p>`,
      );
      b.add(
        state.building
          ? '<p class="empty">Building...</p>'
          : `<div class="actions">${b.button("build", "Build index")}${b.button("settings", "Settings", true)}</div>`,
      );
      break;
    case "unsupported":
      b.add(`<p class="empty">${escapeHtml(`repo2graph does not parse ${state.path}.`)}</p>`);
      break;
    case "notIndexed":
      b.add(
        `<p class="empty">${escapeHtml(`${state.path} is not in the index. It may be new, excluded, or the index may be stale.`)}</p>`,
      );
      b.add(`<div class="actions">${b.button("rebuild", "Rebuild index")}${b.button("status", "Index status", true)}</div>`);
      break;
    case "error":
      b.add(`<div class="note">${escapeHtml(state.message)}</div>`);
      b.add(`<div class="actions">${state.actions.map((a) => b.button(a, actionLabel(a), a !== state.actions[0])).join("")}</div>`);
      break;
    case "neighbourhood":
      renderNeighbourhood(b, state.model, state.elapsedMs);
      break;
    case "impact":
      renderImpact(b, state.node, state.impact);
      break;
  }
  return { html: b.parts.join("\n"), targets: b.targets };
}

function actionLabel(command: PageCommand): string {
  return {
    build: "Build index",
    rebuild: "Rebuild index",
    status: "Index status",
    reveal: "Reveal in graph",
    impact: "Show impact",
    cochange: "Co-changed files",
    more: "Load more",
    refresh: "Retry",
    settings: "Settings",
    restart: "Restart server",
  }[command];
}

export function isPageCommand(value: unknown): value is PageCommand {
  return typeof value === "string" && (PAGE_COMMANDS as readonly string[]).includes(value);
}
