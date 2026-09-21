<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​​​​​​‌‌​​‌​​‌​‌​‌‌‌​​‌‌​​​​​​‌‌​‌​‌​‌​​​​​‌​‌​‌​‌‌‌​‌​‌​‌‌​​‌​​​‌‌​​‌​​​‌‌‌​‌​​​​​‌​‌​‌​‌‌​​​‌‌​‌​‌​‌​​​​​‌​‌‌‌​‌​​​‌‌​​‌​​​‌​‌‌​‌​​​‌​‌‌​‌​‌​​​‌‌‌​‌​‌‌​‌​​‌​​‌‌‌‌​‌​‌​​​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.p2W05AWVFGAV5AtdZ-GZOP
-->
# Django graph example

## Repository

[https://github.com/django/django](https://github.com/django/django)

## Revision

Commit `f91c8897f568a8075f4f0c1c9dcc81b8ec798ef3` on `main`, analyzed 2026-09-17T15:45:24Z.

## Why this repository?

Mature, long-lived, single-language Python framework — decorators, URL routing, an ORM, middleware, and an extensive test suite. Small enough relative to the other four that the whole repository is indexed at once, making it the pipeline's full-repository (non-scoped) baseline.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 5,637 |
| Files parsed (code) | 2,979 |
| Parse errors | 11 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 54,544 |
| Edges | 228,461 |
| Symbols (functions) | 32,799 |
| Symbols (classes) | 11,106 |
| CALLS edges | 104,418 |
| CALLS_EXTERNAL edges | 35,777 |
| IMPORTS edges | 11,579 |
| INHERITS edges | 24,012 |
| DEFINES edges | 43,779 |
| Ambiguous calls (name matched >1 candidate) | 18,797 |
| Entrypoints | 25,543 |

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
cd /tmp/django && git checkout f91c8897f568a8075f4f0c1c9dcc81b8ec798ef3
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
18,797 out of 104,418 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo django
```

This clones `https://github.com/django/django` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
