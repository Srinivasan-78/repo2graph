# TensorFlow graph example

## Repository

[https://github.com/tensorflow/tensorflow](https://github.com/tensorflow/tensorflow)

## Revision

Commit `c18cff96cf94b76c8318a0e826d93a1e65ecb4dc` on `master`, analyzed 2026-10-06T08:03:52Z
by repo2graph 2.2.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

TensorFlow's full tree includes generated bindings, third-party vendoring and a bazel build graph that dwarf the hand-written source. This scope covers the Python/C++ boundary directly: `python/framework` and `python/eager` are the Python-side op/eager machinery, `core/framework` and `core/common_runtime` are the C++ execution core they call into — the pair this project exists to test cross-language behavior against (see docs/architecture.md, "cross-language resolution").

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

**Indexed paths:**
- `tensorflow/python/framework/**`
- `tensorflow/python/eager/**`
- `tensorflow/core/framework/**`
- `tensorflow/core/common_runtime/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 1,022 |
| Files parsed (code) | 969 |
| Parse errors | 562 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 24,730 |
| Edges | 145,790 |
| Symbols (functions) | 15,719 |
| Symbols (classes) | 1,574 |
| CALLS edges | 79,792 |
| CALLS_EXTERNAL edges | 35,123 |
| IMPORTS edges | 10,578 |
| INHERITS edges | 669 |
| DEFINES edges | 18,587 |
| Ambiguous calls (name matched >1 candidate) | 16,849 |
| Entrypoints | 8,023 |

## Supported languages

- python
- cpp

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- Where does a Python API cross into C++?
- What participates in eager execution?
- What is the dependency path between the Python framework and the C++ core runtime?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/tensorflow/tensorflow /tmp/tensorflow
cd /tmp/tensorflow && git checkout c18cff96cf94b76c8318a0e826d93a1e65ecb4dc
repo2graph build . -o .r2g --include tensorflow/python/framework/** tensorflow/python/eager/** tensorflow/core/framework/** tensorflow/core/common_runtime/**
repo2graph query "Where does a Python API cross into C++?" -o .r2g
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
16,849 out of 79,792 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo tensorflow
```

This clones `https://github.com/tensorflow/tensorflow` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
