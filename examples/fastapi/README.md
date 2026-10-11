# FastAPI graph example

## Repository

[https://github.com/fastapi/fastapi](https://github.com/fastapi/fastapi)

## Revision

Commit `f5c6e9b4f9cadc1bf0f9a1fd672e621206b63007` on `master`, analyzed 2026-10-11T08:47:22Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

A modern async Python web framework built on Starlette and Pydantic, where most of the interesting behaviour — dependency injection, request validation, OpenAPI generation — is reached through decorators and function signatures rather than explicit calls. Indexed whole, with git history, so `CO_CHANGE` edges are present. The repository is also one of the four in the retrieval benchmark (benchmarks/real/), so its graph can be read next to measured questions about it. Over half of the indexed files are Markdown documentation under `docs/`, most of it translations; those are indexed as text, with no symbols.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 2,994 |
| Files parsed (code) | 1,176 |
| Parse errors | 0 |
| Files with parse errors | 0 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 10,384 |
| Edges | 46,738 |
| Symbols (all kinds) | 6,036 |
| Symbols (functions) | 5,297 |
| Symbols (methods) | 13 |
| Symbols (classes) | 726 |
| CALLS edges | 18,371 |
| CALLS_EXTERNAL edges | 7,852 |
| IMPORTS edges | 4,126 |
| INHERITS edges | 113 |
| DEFINES edges | 6,036 |
| Ambiguous calls (name matched >1 candidate) | 4,584 |
| Entrypoints | 4,285 |

## Supported languages

- python

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How are a path operation's dependencies resolved?
- Where is a request body validated into a Pydantic model?
- How is the OpenAPI schema generated?
- What calls solve_dependencies?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/fastapi/fastapi /tmp/fastapi
cd /tmp/fastapi && git checkout f5c6e9b4f9cadc1bf0f9a1fd672e621206b63007
repo2graph build . -o .r2g
repo2graph query "How are a path operation's dependencies resolved?" -o .r2g
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
4,584 out of 18,371 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo fastapi
```

This clones `https://github.com/fastapi/fastapi` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
