# Kubernetes graph example

## Repository

[https://github.com/kubernetes/kubernetes](https://github.com/kubernetes/kubernetes)

## Revision

Commit `50b66f3c122cb1553cd4d46d4eba81a101e05b50` on `master`, analyzed 2026-09-22T08:08:15Z.

## Why this repository?

Kubernetes' full tree is on the order of hundreds of thousands of files once vendor/ and generated clients are counted — not a repository size that a single reproducible CI-friendly run should attempt whole. This is a scoped benchmark (see docs/limitations.md and section "Why this scope" below): the controller-manager entrypoint, the built-in controllers, the scheduler, the pod registry, and the API server's request-handling package (apiserver/pkg/endpoints) — a coherent slice that a "how does a request flow through the API server" or "what calls a specific controller" question can actually be answered against.

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/limitations.md](../../docs/limitations.md#extreme-scale).

**Indexed paths:**
- `cmd/kube-controller-manager/**`
- `pkg/controller/**`
- `pkg/scheduler/**`
- `pkg/registry/core/pod/**`
- `staging/src/k8s.io/apiserver/pkg/endpoints/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 1,084 |
| Files parsed (code) | 1,007 |
| Parse errors | 260 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 14,451 |
| Edges | 110,246 |
| Symbols (functions) | 5,287 |
| Symbols (classes) | 0 |
| CALLS edges | 62,630 |
| CALLS_EXTERNAL edges | 27,056 |
| IMPORTS edges | 8,693 |
| INHERITS edges | 0 |
| DEFINES edges | 10,526 |
| Ambiguous calls (name matched >1 candidate) | 10,948 |
| Entrypoints | 3,017 |

## Supported languages

- go

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

- How does a request flow through the API server?
- Where are controller entry points?
- What calls the pod controller?
- What is the impact radius of changing the scheduler's core interface?

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/kubernetes/kubernetes /tmp/kubernetes
cd /tmp/kubernetes && git checkout 50b66f3c122cb1553cd4d46d4eba81a101e05b50
repo2graph build . -o .r2g --include cmd/kube-controller-manager/** pkg/controller/** pkg/scheduler/** pkg/registry/core/pod/** staging/src/k8s.io/apiserver/pkg/endpoints/**
repo2graph query "How does a request flow through the API server?" -o .r2g
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
10,948 out of 62,630 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo kubernetes
```

This clones `https://github.com/kubernetes/kubernetes` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
