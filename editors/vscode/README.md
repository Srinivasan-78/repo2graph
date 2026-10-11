# repo2graph for VS Code

The neighbourhood of the symbol at the cursor, from a repo2graph index: its
callers and callees, base classes and subclasses, the file and class that
define it, the tests that reach it, and the files historically edited with it.
It follows the cursor, and every row opens the file at that line.

It is a client of the [MCP server](../../docs/mcp.md), not a second
implementation: the extension starts one `repo2graph-mcp` per workspace folder
and keeps it warm, so a cursor move costs two tool calls (`repo_find_symbol`
with `path` and `line`, then `repo_neighbours` with `format: "json"`) of a few
milliseconds each. The language table and the node and edge kinds it uses are
generated from the Python package (`scripts/generate_vscode_tables.py`), and
the test suite fails if they disagree.

It is a preview, built from source; it is not on the Marketplace.

## Requirements

- repo2graph with the MCP extra: `pip install "repo2graph[mcp]"` (or
  `pip install -e ".[mcp]"` in this repository).
- VS Code 1.85 or newer, and Node.js 18+ to build the extension.

## Build and run

```bash
cd editors/vscode
npm ci
npm run compile
```

Open `editors/vscode` in VS Code and press **F5** (the "Run Extension" launch
configuration) to start an Extension Development Host. In it, open a
repository, then the **Symbol Neighbourhood** view in the Explorer.

With no index yet the view says so and offers **Build index**, which runs
`repo2graph build <folder> -o <folder>/.r2g` with `repo2graph.buildArgs`.
After that, put the cursor inside a function.

## Commands

| Command | What it does |
|---|---|
| Reveal in Graph | Opens the index's `graph.html` in your browser, selected on the symbol (`graph.html#node=<id>`). Offers to draw the page with `repo2graph map` if the index has none. |
| Show Impact | What depends on the symbol (`repo_blast_radius`): callers of callers, subclasses, importers and co-changed files, by hop, in a panel beside the editor. |
| Index Status | `repo2graph index-status`: when the index was built, from which commit, and whether the working tree has moved since; offers a rebuild. |
| Go to Co-changed Files | A quick pick of the files most often committed together with this one. Needs an index built with `--git-history`. |
| Build Index | `repo2graph build` for the workspace folder. |
| Rebuild Index (incremental) | The same with `--incremental`, re-parsing only changed files. |
| Refresh Neighbourhood | Asks again for the symbol at the cursor. |
| Restart MCP Server | Stops the server and starts a new one on the next cursor move. |

When the server reports the index as stale, the view shows its note with a
**Rebuild index** button.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `repo2graph.executable` | `repo2graph` | The CLI. A bare name is looked up on `PATH`; point it at a virtual environment's `bin/repo2graph` otherwise. |
| `repo2graph.mcpExecutable` | (empty) | The server. Empty: `repo2graph-mcp` beside `repo2graph.executable` when that is a path, else on `PATH`. |
| `repo2graph.indexPath` | `.r2g` | The index directory, relative to the workspace folder or absolute. |
| `repo2graph.repoRoot` | (empty) | The source tree the index describes, if not the workspace folder. |
| `repo2graph.buildArgs` | `["--git-history", "300"]` | Extra `repo2graph build` arguments for Build and Rebuild. The default records co-changed files. |
| `repo2graph.debounceMs` | `75` | How long the cursor rests before the view asks. |
| `repo2graph.stalenessTtlSeconds` | `10` | Passed as `repo2graph-mcp --staleness-ttl`: how long the server reuses its working-tree freshness check. `0` re-checks on every call, which costs tens of milliseconds per call on a mid-sized repository. |
| `repo2graph.neighbourLimit` | `50` | Neighbours per page; **Load more** fetches the next. |

The three settings that choose what runs (`repo2graph.executable`,
`repo2graph.mcpExecutable`, `repo2graph.buildArgs`) are machine-scoped: a
repository's own `.vscode/settings.json` cannot set them. The extension does
not run in untrusted workspaces.

## Security

Names, paths and notes in the view come from the indexed repository, which may
not be yours. The webview escapes all of it, runs under a Content-Security-Policy
that allows only its own nonce'd script and style (no remote resources, no
inline handlers), and a row can only refer to a location the extension itself
listed -- never a path the page supplies.

## Limits

- Line numbers are the index's. Edits since the last build shift them; the
  stale note says when, and **Rebuild index** fixes it.
- "Symbol at the cursor" is the innermost indexed symbol whose span contains
  the line; outside every symbol it is the file.
- Reveal in Graph shows only nodes drawn in `graph.html` (300 by default,
  `repo2graph build --viz-nodes`); for one that is not, the page says so.

## Tests

```bash
npm run compile
npm test
```

The tests build a small repository with the real `repo2graph build` and talk
to a real `repo2graph-mcp`. They look for the executables in `$REPO2GRAPH_BIN`,
then this repository's `.venv`, then `PATH`, and print the measured cursor-move
round trip.
