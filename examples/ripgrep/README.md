# ripgrep graph example

## Repository

[https://github.com/BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep)

## Revision

Commit `3fce3b5bb0236da2df6d99672afb8a719642eca7` on `master`, analyzed 2026-10-11T08:46:09Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The gallery's Rust entry, and the tool the retrieval benchmark's grep baseline actually runs. A Cargo workspace split into reusable crates (`ignore`, `globset`, `grep-searcher`, `grep-printer`, `grep-regex`) behind one binary, which makes the cross-crate edges — the binary calling into the searcher, the searcher into the matcher — the structure worth looking at. Small enough to index whole with git history.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 229 |
| Files parsed (code) | 114 |
| Parse errors | 1 |
| Files with parse errors | 1 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 4,857 |
| Edges | 20,381 |
| Symbols (all kinds) | 3,959 |
| Symbols (functions) | 2,988 |
| Symbols (methods) | 1 |
| Symbols (classes) | 1 |
| CALLS edges | 11,802 |
| CALLS_EXTERNAL edges | 3,341 |
| IMPORTS edges | 316 |
| INHERITS edges | 161 |
| DEFINES edges | 3,959 |
| Ambiguous calls (name matched >1 candidate) | 2,510 |
| Entrypoints | 1,648 |

## Supported languages

- rust

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does a search get dispatched across files?
- Where are command-line flags parsed?
- How does the searcher detect binary files?
- What decides which files are ignored?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/BurntSushi/ripgrep /tmp/ripgrep
cd /tmp/ripgrep && git checkout 3fce3b5bb0236da2df6d99672afb8a719642eca7
repo2graph build . -o .r2g
repo2graph query "How does a search get dispatched across files?" -o .r2g
```

## Generated graph

**Only this page and `overview.md` are committed.** `4e96b628` dropped the rest — about 28 MB of
generated binary and boilerplate that every clone of this repository had to carry — and
`.gitignore` keeps them out, so a regeneration cannot quietly put them back. Everything below is
what the generator writes into this directory when you run it yourself.

- `overview.md` — the prose repo map: languages, most depended-on files, most called symbols
- `nodes.jsonl.gz` / `edges.jsonl.gz` — the graph structure (identifiers, paths, line ranges; no
  source text), gzipped — JSON lines compress 4-9x and there is no reason to commit that redundancy
  raw; `gunzip -k nodes.jsonl.gz` to read it
- `graph.html` — the interactive map (self-contained, opens in any browser, no network needed), capped
  to the 300 best-connected nodes
- `manifest.json` — what every field in the other files means
- `stats.json` — the raw counters above
- `flows/` — citation-only results of the example queries above

`chunks.jsonl` (the retrieval index, which embeds source text per symbol) is discarded by the
generator rather than merely left uncommitted —
see [ATTRIBUTIONS.md](../ATTRIBUTIONS.md#why-chunksjsonl-is-not-committed).

## Limitations

Call edges are matched by name, not by type — see
[docs/architecture.md](../../docs/architecture.md). Unresolved / ambiguous calls for this example:
2,510 out of 11,802 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo ripgrep
```

This clones `https://github.com/BurntSushi/ripgrep` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
