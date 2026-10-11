# Guava graph example

## Repository

[https://github.com/google/guava](https://github.com/google/guava)

## Revision

Commit `6b3a8d7498a7fde122967ba37a023ba3dcb47792` on `master`, analyzed 2026-10-11T08:46:30Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The gallery's Java entry: Google's core libraries for Java, a codebase built from interfaces, abstract base classes and many small implementations of each. A call made through an interface has many same-named candidates, which name-based resolution prices at `1/n` rather than picks — the ambiguous-call count below is that cost, shown rather than avoided. The repository carries the same library twice (`guava/` for the JRE and `android/guava/` for Android) plus test suites, a test library and a GWT variant; this scope is the JRE library's production source, `guava/src/`, once.

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

**Indexed paths:**
- `guava/src/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 611 |
| Files parsed (code) | 611 |
| Parse errors | 16 |
| Files with parse errors | 5 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 16,268 |
| Edges | 80,339 |
| Symbols (all kinds) | 14,441 |
| Symbols (functions) | 0 |
| Symbols (methods) | 12,975 |
| Symbols (classes) | 1,281 |
| CALLS edges | 54,751 |
| CALLS_EXTERNAL edges | 2,769 |
| IMPORTS edges | 6,501 |
| INHERITS edges | 1,241 |
| DEFINES edges | 14,441 |
| Ambiguous calls (name matched >1 candidate) | 12,383 |
| Entrypoints | 6,435 |

## Supported languages

- java

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does an immutable list get built?
- Where does a LoadingCache load a missing value?
- How are listeners notified when a ListenableFuture completes?
- What implements the Multimap interface?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/google/guava /tmp/guava
cd /tmp/guava && git checkout 6b3a8d7498a7fde122967ba37a023ba3dcb47792
repo2graph build . -o .r2g --include guava/src/**
repo2graph query "How does an immutable list get built?" -o .r2g
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
12,383 out of 54,751 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo guava
```

This clones `https://github.com/google/guava` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
