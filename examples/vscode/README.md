# VS Code graph example

## Repository

[https://github.com/microsoft/vscode](https://github.com/microsoft/vscode)

## Revision

Commit `8c7b66b0e3d9364afd5a59a7a9843f191d169fa1` on `main`, analyzed 2026-09-17T15:36:00Z.

## Why this repository?

`extensions/` (bundled extensions, each with its own dependency tree) and `node_modules`-shaped build tooling dominate file count without adding architecturally interesting graph structure. `src/vs/` is the actual editor/workbench/platform source — base services, the editor, the workbench UI, and the Electron/Node entry points — where the "command to handler" and "editor action to service" flows this example set is built around actually live.

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/limitations.md](../../docs/limitations.md#extreme-scale).

**Indexed paths:**
- `src/vs/**`

**File cap reached:** the indexed paths above contain more than `max_files: 6000` files; discovery is truncated at that count (deterministic — `git ls-files` order — so re-running gets the same 6000 files for the same commit, not a random sample). This graph is therefore a subset of even the scoped paths, not their complete contents.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 6,000 |
| Files parsed (code) | 5,488 |
| Parse errors | 6 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 113,115 |
| Edges | 431,453 |
| Symbols (functions) | 13,076 |
| Symbols (classes) | 7,577 |
| CALLS edges | 223,594 |
| CALLS_EXTERNAL edges | 25,166 |
| IMPORTS edges | 75,900 |
| INHERITS edges | 10,581 |
| DEFINES edges | 88,981 |
| Ambiguous calls (name matched >1 candidate) | 73,028 |
| Entrypoints | 19,390 |

## Supported languages

- typescript

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

- How does a command reach its handler?
- What is the flow from an editor action to a service?
- What are the core platform services other layers depend on?

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/microsoft/vscode /tmp/vscode
cd /tmp/vscode && git checkout 8c7b66b0e3d9364afd5a59a7a9843f191d169fa1
repo2graph build . -o .r2g --include src/vs/**
repo2graph query "How does a command reach its handler?" -o .r2g
```

## Generated graph

- `nodes.jsonl.gz` / `edges.jsonl.gz` — the graph structure (identifiers, paths, line ranges; no
  source text), gzipped — JSON lines compress 4-9x and there is no reason to commit that redundancy
  raw; `gunzip -k nodes.jsonl.gz` to read it
- `graph.html` — the interactive map (self-contained, opens in any browser, no network needed), capped
  to the 300 best-connected nodes
- `overview.md` — the prose repo map: languages, most depended-on files, most called symbols
- `manifest.json` — what every field in the other files means
- `stats.json` — the raw counters above
- `flows/` — citation-only results of the example queries above

`chunks.jsonl` (the retrieval index, which embeds source text per symbol) is **not** committed —
see [ATTRIBUTIONS.md](../ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed).

## Limitations

Call edges are matched by name, not by type — see
[docs/limitations.md](../../docs/limitations.md). Unresolved / ambiguous calls for this example:
73,028 out of 223,594 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo vscode
```

This clones `https://github.com/microsoft/vscode` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
