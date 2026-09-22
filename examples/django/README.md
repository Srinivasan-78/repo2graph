# Django graph example

## Repository

[https://github.com/django/django](https://github.com/django/django)

## Revision

Commit `dd6f6b1531984823e3dc56740dfa93f3ceb09357` on `main`, analyzed 2026-09-22T08:07:53Z.

## Why this repository?

Mature, long-lived, single-language Python framework — decorators, URL routing, an ORM, middleware, and an extensive test suite. Small enough relative to the other four that the whole repository is indexed at once, making it the pipeline's full-repository (non-scoped) baseline.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 5,629 |
| Files parsed (code) | 2,979 |
| Parse errors | 11 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 55,810 |
| Edges | 303,339 |
| Symbols (functions) | 32,803 |
| Symbols (classes) | 11,105 |
| CALLS edges | 189,381 |
| CALLS_EXTERNAL edges | 39,633 |
| IMPORTS edges | 12,508 |
| INHERITS edges | 9,148 |
| DEFINES edges | 43,782 |
| Ambiguous calls (name matched >1 candidate) | 35,338 |
| Entrypoints | 23,554 |

## Supported languages

- python

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

- How does a request travel through Django middleware?
- How does URL resolution reach a view?
- Where does model query execution originate?
- How does a management command get invoked?

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/django/django /tmp/django
cd /tmp/django && git checkout dd6f6b1531984823e3dc56740dfa93f3ceb09357
repo2graph build . -o .r2g
repo2graph query "How does a request travel through Django middleware?" -o .r2g
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
35,338 out of 189,381 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo django
```

This clones `https://github.com/django/django` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
