# Gin graph example

## Repository

[https://github.com/gin-gonic/gin](https://github.com/gin-gonic/gin)

## Revision

Commit `0f09c3a9b4626d9fe9979cf4d0521d6ae32ab646` on `master`, analyzed 2026-10-11T08:46:01Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

One of the most widely used Go web frameworks, and small enough to index whole with git history — the counterpoint to Kubernetes, which is Go at a scale that has to be scoped. A radix-tree router, a middleware chain driven by `Context.Next`, and pluggable binding and rendering packages make it a compact test of method resolution on Go receivers.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 128 |
| Files parsed (code) | 99 |
| Parse errors | 0 |
| Files with parse errors | 0 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 2,106 |
| Edges | 19,552 |
| Symbols (all kinds) | 1,554 |
| Symbols (functions) | 912 |
| Symbols (methods) | 430 |
| Symbols (classes) | 0 |
| CALLS edges | 5,366 |
| CALLS_EXTERNAL edges | 2,759 |
| IMPORTS edges | 521 |
| INHERITS edges | 0 |
| DEFINES edges | 1,554 |
| Ambiguous calls (name matched >1 candidate) | 1,060 |
| Entrypoints | 828 |

## Supported languages

- go

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does a request reach a handler through the router?
- How are middleware handlers chained with Next?
- Where is a request bound to a struct?
- What renders a JSON response?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/gin-gonic/gin /tmp/gin
cd /tmp/gin && git checkout 0f09c3a9b4626d9fe9979cf4d0521d6ae32ab646
repo2graph build . -o .r2g
repo2graph query "How does a request reach a handler through the router?" -o .r2g
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
1,060 out of 5,366 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo gin
```

This clones `https://github.com/gin-gonic/gin` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
