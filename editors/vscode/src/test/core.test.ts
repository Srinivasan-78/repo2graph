// Pure data-layer tests: no server, no VS Code.

import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { EDGE_TYPES, EXT_LANG, LANGUAGES, NODE_TYPES } from "../core/generated";
import { groupSections, type NeighbourRow, SECTION_LABELS, SymbolNavigator } from "../core/neighbourhood";
import { graphUrl, languageOf, repoRelative, resolveAgainst, revealPage, serverExecutable } from "../core/paths";
import { contentSecurityPolicy, escapeHtml, isPageCommand, makeNonce, renderBody, shellHtml } from "../core/render";
import { buildArgs, formatAge, serverArgs, summarizeStatus, type IndexStatusReport } from "../core/status";
import { isStaleNote, parseJsonReply, splitNotes } from "../core/toolText";

const SRC = path.resolve(__dirname, "..", "..", "src");

function row(overrides: Partial<NeighbourRow>): NeighbourRow {
  return {
    edge: "CALLS",
    direction: "in",
    node_id: "sym:a.py::f",
    type: "symbol",
    name: "f",
    qualname: "f",
    kind: "function",
    path: "a.py",
    start_line: 3,
    end_line: 5,
    lang: "python",
    via: "sym:a.py::g",
    at: "a.py:4",
    confidence: 1,
    candidate_count: 1,
    count: 1,
    ...overrides,
  };
}

// ---------------------------------------------------------------- layering

test("the data layer never imports vscode", () => {
  for (const file of fs.readdirSync(path.join(SRC, "core"))) {
    const text = fs.readFileSync(path.join(SRC, "core", file), "utf8");
    assert.doesNotMatch(text, /from\s+["']vscode["']|require\(["']vscode["']\)/, file);
  }
});

// ---------------------------------------------------------- generated tables

test("every core edge type has a section label in both directions", () => {
  const labelled = new Set(SECTION_LABELS.map(([edge, dir]) => `${edge} ${dir}`));
  for (const edge of Object.keys(EDGE_TYPES)) {
    for (const dir of ["in", "out"]) {
      assert.ok(labelled.has(`${edge} ${dir}`), `no label for ${edge} ${dir}`);
    }
  }
});

test("the generated tables hold the core's languages and kinds", () => {
  assert.equal(EXT_LANG[".py"], "python");
  assert.ok(LANGUAGES.includes("python") && LANGUAGES.includes("typescript"));
  assert.ok("symbol" in NODE_TYPES && "file" in NODE_TYPES);
  assert.ok("CALLS" in EDGE_TYPES && "CO_CHANGE" in EDGE_TYPES);
  // Every grammar language is reachable from some extension.
  const reachable = new Set(Object.values(EXT_LANG));
  for (const lang of LANGUAGES) {
    assert.ok(reachable.has(lang), `${lang} has no extension`);
  }
});

// ------------------------------------------------------------------ toolText

test("notes are split from the JSON body and the stale note is recognised", () => {
  const text =
    "_note: index may be stale relative to the working tree (1 modified). Rebuild._\n\n" +
    "_note: limit='x' is not an integer; used the default 20._\n\n" +
    '[{"node_id": "x"}]';
  const { notes, value } = parseJsonReply<Array<{ node_id: string }>>(text);
  assert.equal(notes.length, 2);
  assert.ok(isStaleNote(notes[0]));
  assert.ok(!isStaleNote(notes[1]));
  assert.ok(!notes[0].startsWith("_note"));
  assert.deepEqual(value, [{ node_id: "x" }]);
  assert.deepEqual(splitNotes("plain"), { notes: [], body: "plain" });
  assert.throws(() => parseJsonReply("neighbours of x:"), /expected JSON/);
});

// ------------------------------------------------------------- neighbourhood

test("rows are grouped into labelled sections in a fixed order", () => {
  const sections = groupSections([
    row({ edge: "DEFINES", direction: "in", node_id: "file:a.py" }),
    row({ edge: "CALLS", direction: "out", node_id: "sym:b" }),
    row({ edge: "CALLS", direction: "in", node_id: "sym:c" }),
    row({ edge: "CO_CHANGE", direction: "in", node_id: "file:x" }),
    row({ edge: "CO_CHANGE", direction: "out", node_id: "file:y" }),
    row({ edge: "SOMETHING_NEW", direction: "out", node_id: "sym:z" }),
  ]);
  assert.deepEqual(
    sections.map((s) => [s.label, s.rows.length]),
    [
      ["Callers", 1],
      ["Callees", 1],
      ["Defined in", 1],
      ["Co-changed with", 2],
      ["SOMETHING_NEW out", 1],
    ],
  );
});

test("a navigator surfaces an isError reply as an error, not as data", async () => {
  const nav = new SymbolNavigator({
    callTool: async () => ({ text: "node not found: 'x'.", isError: true }),
  });
  await assert.rejects(nav.neighbours("x"), /node not found/);
});

// -------------------------------------------------------------------- status

test("ages match the CLI's buckets", () => {
  assert.equal(formatAge(null), "unknown");
  assert.equal(formatAge(42), "42s ago");
  assert.equal(formatAge(600), "10m ago");
  assert.equal(formatAge(7200), "2h ago");
  assert.equal(formatAge(3 * 86400), "3d ago");
});

test("the status summary names staleness and its reasons", () => {
  const report: IndexStatusReport = {
    index: { path: "/r/.r2g", created_at: "2026-01-01T00:00:00+00:00", age_seconds: 7200 },
    source: { path: "/r", commit: "0123456789abcdef", short_commit: "0123456" },
    contents: { files_parsed: 3, symbols: 9, nodes: 15, edges: 24 },
    freshness: {
      status: "stale",
      reasons: ["HEAD moved since the build", "2 modified"],
      notes: [],
      counts: { added: 0, removed: 0, modified: 2 },
    },
  };
  const summary = summarizeStatus(report);
  assert.equal(summary.status, "stale");
  assert.match(summary.headline, /stale \(HEAD moved since the build; 2 modified\): built 2h ago at 0123456/);
});

test("the argument lists are what the CLI and server accept", () => {
  assert.deepEqual(buildArgs("/r", "/r/.r2g", ["--git-history", "300"], true), [
    "build",
    "/r",
    "-o",
    "/r/.r2g",
    "--git-history",
    "300",
    "--incremental",
  ]);
  assert.deepEqual(serverArgs("/r", "/r/.r2g", 10), ["/r", "--out", "/r/.r2g", "--no-auto-build", "--staleness-ttl", "10"]);
  // Clamped to the server's own bounds, so a bad setting cannot stop it starting.
  assert.equal(serverArgs("/r", "/i", 9999)[5], "300");
  assert.equal(serverArgs("/r", "/i", Number.NaN)[5], "0");
});

// --------------------------------------------------------------------- paths

test("documents map to repo-relative paths, and outsiders to null", () => {
  assert.equal(repoRelative("/r", "/r/pkg/a.py", path.posix), "pkg/a.py");
  assert.equal(repoRelative("/r", "/other/a.py", path.posix), null);
  assert.equal(repoRelative("/r", "/r", path.posix), null);
  assert.equal(repoRelative("C:\\r", "C:\\r\\pkg\\a.py", path.win32), "pkg/a.py");
  assert.equal(repoRelative("C:\\r", "D:\\a.py", path.win32), null);
  assert.equal(resolveAgainst("/w", "", ".r2g", path.posix), "/w/.r2g");
  assert.equal(resolveAgainst("/w", "/abs/idx", ".r2g", path.posix), "/abs/idx");
});

test("languages come from the core's extension table", () => {
  assert.equal(languageOf("/r/a.py"), "python");
  assert.equal(languageOf("/r/a.TSX"), "tsx");
  assert.equal(languageOf("/r/README"), null);
  assert.equal(languageOf("/r.d/Makefile"), null);
  assert.equal(languageOf("/r/notes.xyz"), null);
});

test("the server sits beside a CLI given as a path", () => {
  assert.equal(serverExecutable("", "repo2graph", "linux"), "repo2graph-mcp");
  assert.equal(serverExecutable("", "/v/bin/repo2graph", "linux"), "/v/bin/repo2graph-mcp");
  assert.equal(serverExecutable("", "C:\\v\\Scripts\\repo2graph.exe", "win32"), "C:\\v\\Scripts\\repo2graph-mcp.exe");
  assert.equal(serverExecutable(" /x/mcp ", "/v/bin/repo2graph", "linux"), "/x/mcp");
});

test("reveal links seed graph.html by node id, escaped for the page", () => {
  // `graphUrl` goes through `pathToFileURL`, which is deliberately
  // platform-bound: on Windows a drive-relative path like `/i/human/graph.html`
  // resolves against the current drive and comes back as `file:///C:/i/...`.
  // The behaviour under test is the fragment encoding, so the origin half is
  // derived rather than spelled out -- hardcoding the POSIX form made this the
  // one test in the suite that could not pass on a Windows checkout, and the
  // extension's CI job is ubuntu-only, so nothing caught it.
  const graphHtml = path.resolve("/i/human/graph.html");
  const origin = pathToFileURL(graphHtml).href;
  const url = graphUrl(graphHtml, "sym:a b.py::f&<g>");
  assert.equal(url, `${origin}#node=sym%3Aa%20b.py%3A%3Af%26%3Cg%3E`);
  const page = revealPage(graphHtml, 'sym:x.py::"><script>alert(1)</script>');
  assert.doesNotMatch(page, /<script/i);
  assert.match(page, /default-src 'none'/);
  assert.ok(page.includes(`http-equiv="refresh" content="0;url=${origin}#node=`));
  assert.ok(origin.startsWith("file:///") && origin.endsWith("/i/human/graph.html"));
});

// -------------------------------------------------------------------- render

const HOSTILE = '<img src=x onerror="alert(1)"><script>alert(2)</script>';

test("graph-derived text is escaped wherever it is rendered", () => {
  const evil = row({ name: HOSTILE, qualname: HOSTILE, path: `${HOSTILE}.py`, node_id: `sym:${HOSTILE}`, kind: HOSTILE });
  const { html, targets } = renderBody({
    kind: "neighbourhood",
    model: {
      node: { ...evil },
      enclosing: [],
      sections: groupSections([evil]),
      cochange: [{ node_id: `file:${HOSTILE}`, path: HOSTILE, count: 3 }],
      truncated: false,
      nextCursor: null,
      notes: [HOSTILE],
      stale: true,
    },
  });
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  // Rows carry an index into the extension's list, never the path itself.
  assert.ok(targets.length >= 3);
  assert.ok(targets.every((t) => typeof t.path === "string"));
  for (const ref of html.matchAll(/data-ref="([^"]*)"/g)) {
    assert.match(ref[1], /^\d+$/);
  }
  for (const cmd of html.matchAll(/data-cmd="([^"]*)"/g)) {
    assert.ok(isPageCommand(cmd[1]), cmd[1]);
  }
  assert.equal(escapeHtml(`'"&`), "&#39;&quot;&amp;");
});

test("the page shell allows only its own nonce'd script and style", () => {
  const nonce = makeNonce();
  assert.match(nonce, /^[A-Za-z0-9]{32}$/);
  const page = shellHtml(nonce, "");
  const csp = contentSecurityPolicy(nonce);
  assert.match(csp, /^default-src 'none'/);
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval|https?:|\*/);
  assert.ok(page.includes(`content="${csp}"`));
  assert.equal((page.match(/<script\b/g) ?? []).length, 1);
  assert.ok(page.includes(`<script nonce="${nonce}">`));
  assert.ok(page.includes(`<style nonce="${nonce}">`));
  assert.doesNotMatch(page, /\bsrc=|\bhref=|https?:\/\//);
  assert.doesNotMatch(page, /\son[a-z]+=/);
});

test("no index renders an actionable prompt, not an empty panel", () => {
  const { html } = renderBody({ kind: "noIndex", indexDir: "/w/.r2g", building: false });
  assert.match(html, /No repo2graph index yet/);
  assert.match(html, /data-cmd="build"[^>]*>Build index</);
});
