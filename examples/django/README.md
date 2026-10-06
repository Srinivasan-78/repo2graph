# Django graph example

## Repository

[https://github.com/django/django](https://github.com/django/django)

## Revision

Commit `08e4c0d8e7db6343567e7b02e25da8ea2226a7e0` on `main`, analyzed 2026-10-06T08:00:32Z
by repo2graph 2.2.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

Mature, long-lived, single-language Python framework — decorators, URL routing, an ORM, middleware, and an extensive test suite. Small enough relative to the other four that the whole repository is indexed at once, making it the pipeline's full-repository (non-scoped) baseline.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 5,630 |
| Files parsed (code) | 2,980 |
| Parse errors | 11 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 56,074 |
| Edges | 301,017 |
| Symbols (functions) | 32,841 |
| Symbols (classes) | 11,113 |
| CALLS edges | 186,191 |
| CALLS_EXTERNAL edges | 40,301 |
| IMPORTS edges | 12,388 |
| INHERITS edges | 9,286 |
| DEFINES edges | 43,964 |
| Ambiguous calls (name matched >1 candidate) | 37,498 |
| Entrypoints | 25,353 |

## Supported languages

- python

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does a request travel through Django middleware?
- How does URL resolution reach a view?
- Where does model query execution originate?
- How does a management command get invoked?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/django/django /tmp/django
cd /tmp/django && git checkout 08e4c0d8e7db6343567e7b02e25da8ea2226a7e0
repo2graph build . -o .r2g
repo2graph query "How does a request travel through Django middleware?" -o .r2g
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
37,498 out of 186,191 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo django
```

This clones `https://github.com/django/django` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
