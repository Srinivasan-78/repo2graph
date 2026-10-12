# Jekyll graph example

## Repository

[https://github.com/jekyll/jekyll](https://github.com/jekyll/jekyll)

## Revision

Commit `541d8b2ee75c8907de4744d4b87a7a6f1f997cae` on `master`, analyzed 2026-10-11T08:46:36Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The gallery's Ruby entry: a widely used static site generator, and small enough to index whole with git history. Its build is a pipeline — read, generate, render, write — driven from one `Site` object. Converters and generators are found at runtime as subclasses of `Jekyll::Plugin` (`Site#instantiate_subclasses`) and Liquid tags are registered by name, so it tests how much of a Ruby codebase's structure a parser recovers when part of the wiring is subclass registration rather than explicit calls.

## Why this scope

The full repository at the pinned commit was indexed — no `--include`/`--exclude` narrowing.

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 771 |
| Files parsed (code) | 168 |
| Parse errors | 0 |
| Files with parse errors | 0 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 2,888 |
| Edges | 10,403 |
| Symbols (all kinds) | 1,431 |
| Symbols (functions) | 25 |
| Symbols (methods) | 1,073 |
| Symbols (classes) | 168 |
| CALLS edges | 3,790 |
| CALLS_EXTERNAL edges | 2,486 |
| IMPORTS edges | 156 |
| INHERITS edges | 94 |
| DEFINES edges | 1,431 |
| Ambiguous calls (name matched >1 candidate) | 797 |
| Entrypoints | 442 |

## Supported languages

- ruby

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does a site build go from reading files to writing output?
- How is a Markdown page converted to HTML?
- Where are plugins loaded?
- What calls the Liquid renderer?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/jekyll/jekyll /tmp/jekyll
cd /tmp/jekyll && git checkout 541d8b2ee75c8907de4744d4b87a7a6f1f997cae
repo2graph build . -o .r2g
repo2graph query "How does a site build go from reading files to writing output?" -o .r2g
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
797 out of 3,790 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo jekyll
```

This clones `https://github.com/jekyll/jekyll` at `master` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
