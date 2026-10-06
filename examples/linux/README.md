# Linux kernel graph example

## Repository

[https://github.com/torvalds/linux](https://github.com/torvalds/linux)

## Revision

Commit `2c3418fffa9d037b2038a6db48be63f9e2291806` on `master`, analyzed 2026-10-06T08:15:27Z
by repo2graph 2.2.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The full kernel tree is architecture-specific code times every supported CPU family times every driver ever merged — not a size any single machine should attempt to index in one CI-friendly run (see docs/architecture.md, "extreme-scale"). This scope is one representative slice of each category the task calls out: `kernel/` (core subsystems: scheduler, cgroups, module loading), `fs/ext4/` (a representative filesystem), `drivers/net/ethernet/intel/e1000/` (a representative driver), and `include/linux/` (the headers those subsystems share).

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

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
| Parse errors | 14,015 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 185,496 |
| Edges | 310,854 |
| Symbols (functions) | 39,906 |
| Symbols (classes) | 0 |
| CALLS edges | 73,036 |
| CALLS_EXTERNAL edges | 45,354 |
| IMPORTS edges | 13,953 |
| INHERITS edges | 0 |
| DEFINES edges | 174,635 |
| Ambiguous calls (name matched >1 candidate) | 7,202 |
| Entrypoints | 17,854 |

## Supported languages

- c

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- Where is the scheduler subsystem initialized?
- What calls into the e1000 driver's probe function?
- What are the major relationships around ext4's core structures?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/torvalds/linux /tmp/linux
cd /tmp/linux && git checkout 2c3418fffa9d037b2038a6db48be63f9e2291806
repo2graph build . -o .r2g --include kernel/** fs/ext4/** drivers/net/ethernet/intel/e1000/** include/linux/**
repo2graph query "Where is the scheduler subsystem initialized?" -o .r2g
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
7,202 out of 73,036 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo linux
```

This clones `https://github.com/torvalds/linux` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
