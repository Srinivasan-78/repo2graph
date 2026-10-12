# Rails graph example

## Repository

[https://github.com/rails/rails](https://github.com/rails/rails)

## Revision

Commit `5cdfd5d87800e79ee9d951a4f5a86d4e9c47c423` on `main`, analyzed 2026-10-12T08:37:58Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The gallery's second Ruby entry, and the one that shows where a name-based call graph thins out. Rails builds much of its own API at load time — `ActiveSupport::Concern`'s `included do` blocks, `delegate`, `define_method`, and Active Record's `method_missing` fallbacks — so for a large part of what application code calls there is no call site in the source to find, and the graph is honest about the structure it can see rather than guessing at the rest. Jekyll is the Ruby entry indexed whole with history; this is the four libraries a request actually passes through: routing and controllers (`actionpack`), the ORM (`activerecord`), the boot sequence (`railties`) and the core extensions underneath all of it (`activesupport`).

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

**Indexed paths:**
- `actionpack/lib/**`
- `activerecord/lib/**`
- `activesupport/lib/**`
- `railties/lib/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 1,276 |
| Files parsed (code) | 1,036 |
| Parse errors | 0 |
| Files with parse errors | 0 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 18,774 |
| Edges | 99,912 |
| Symbols (all kinds) | 15,372 |
| Symbols (functions) | 6 |
| Symbols (methods) | 11,946 |
| Symbols (classes) | 1,375 |
| CALLS edges | 67,967 |
| CALLS_EXTERNAL edges | 8,074 |
| IMPORTS edges | 1,483 |
| INHERITS edges | 816 |
| DEFINES edges | 15,372 |
| Ambiguous calls (name matched >1 candidate) | 14,743 |
| Entrypoints | 6,184 |

## Supported languages

- ruby

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does a request travel through the Rails middleware stack?
- How does routing reach a controller action?
- Where does an Active Record relation become SQL?
- How does a Rails application boot?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/rails/rails /tmp/rails
cd /tmp/rails && git checkout 5cdfd5d87800e79ee9d951a4f5a86d4e9c47c423
repo2graph build . -o .r2g --include actionpack/lib/** activerecord/lib/** activesupport/lib/** railties/lib/**
repo2graph query "How does a request travel through the Rails middleware stack?" -o .r2g
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
14,743 out of 67,967 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo rails
```

This clones `https://github.com/rails/rails` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
