# TensorFlow graph example

## Repository

[https://github.com/tensorflow/tensorflow](https://github.com/tensorflow/tensorflow)

## Revision

Commit `41283216e55ebfb30fe61b9ae4bd034404584c57` on `master`, analyzed 2026-09-17T15:46:14Z.

## Why this repository?

TensorFlow's full tree includes generated bindings, third-party vendoring and a bazel build graph that dwarf the hand-written source. This scope covers the Python/C++ boundary directly: `python/framework` and `python/eager` are the Python-side op/eager machinery, `core/framework` and `core/common_runtime` are the C++ execution core they call into — the pair this project exists to test cross-language behavior against (see docs/limitations.md, "cross-language resolution").

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/limitations.md](../../docs/limitations.md#extreme-scale).

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
| Parse errors | 12,469 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 20,641 |
| Edges | 96,013 |
| Symbols (functions) | 15,388 |
| Symbols (classes) | 956 |
| CALLS edges | 42,395 |
| CALLS_EXTERNAL edges | 27,313 |
| IMPORTS edges | 9,634 |
| INHERITS edges | 662 |
| DEFINES edges | 14,968 |
| Ambiguous calls (name matched >1 candidate) | 5,631 |
| Entrypoints | 5,676 |

## Supported languages

- python
- cpp

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

- Where does a Python API cross into C++?
- What participates in eager execution?
- What is the dependency path between the Python framework and the C++ core runtime?

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/tensorflow/tensorflow /tmp/tensorflow
cd /tmp/tensorflow && git checkout 41283216e55ebfb30fe61b9ae4bd034404584c57
repo2graph build . -o .r2g --include tensorflow/python/framework/** tensorflow/python/eager/** tensorflow/core/framework/** tensorflow/core/common_runtime/**
repo2graph query "Where does a Python API cross into C++?" -o .r2g
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
5,631 out of 42,395 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo tensorflow
```

This clones `https://github.com/tensorflow/tensorflow` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
