# Linux kernel graph example

## Repository

[https://github.com/torvalds/linux](https://github.com/torvalds/linux)

## Revision

Commit `238650ef6c7c7cca08e032527329424c9fbd70e5` on `master`, analyzed 2026-09-17T15:32:06Z.

## Why this repository?

The full kernel tree is architecture-specific code times every supported CPU family times every driver ever merged — not a size any single machine should attempt to index in one CI-friendly run (see docs/limitations.md, "extreme-scale"). This scope is one representative slice of each category the task calls out: `kernel/` (core subsystems: scheduler, cgroups, module loading), `fs/ext4/` (a representative filesystem), `drivers/net/ethernet/intel/e1000/` (a representative driver), and `include/linux/` (the headers those subsystems share).

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/limitations.md](../../docs/limitations.md#extreme-scale).

**Indexed paths:**
- `kernel/**`
- `fs/ext4/**`
- `drivers/net/ethernet/intel/e1000/**`
- `include/linux/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 3,660 |
| Files parsed (code) | 3,574 |
| Parse errors | 14,663 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 136,182 |
| Edges | 257,655 |
| Symbols (functions) | 39,944 |
| Symbols (classes) | 0 |
| CALLS edges | 70,079 |
| CALLS_EXTERNAL edges | 44,350 |
| IMPORTS edges | 13,949 |
| INHERITS edges | 0 |
| DEFINES edges | 125,401 |
| Ambiguous calls (name matched >1 candidate) | 227 |
| Entrypoints | 16,471 |

## Supported languages

- c

## Example queries

These are real `repo2graph query` runs against this index (see `flows/`), not invented text:

- Where is the scheduler subsystem initialized?
- What calls into the e1000 driver's probe function?
- What are the major relationships around ext4's core structures?

`flows/` holds each query's real results as citations (node id, path, line range, why it matched)
with the source text stripped out. To run these queries yourself against a live, queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/torvalds/linux /tmp/linux
cd /tmp/linux && git checkout 238650ef6c7c7cca08e032527329424c9fbd70e5
repo2graph build . -o .r2g --include kernel/** fs/ext4/** drivers/net/ethernet/intel/e1000/** include/linux/**
repo2graph query "Where is the scheduler subsystem initialized?" -o .r2g
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
227 out of 70,079 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo linux
```

This clones `https://github.com/torvalds/linux` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/benchmarks.md](../../docs/benchmarks.md#staleness)).
