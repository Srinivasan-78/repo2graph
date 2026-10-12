# React graph example

## Repository

[https://github.com/facebook/react](https://github.com/facebook/react)

## Revision

Commit `b618bbb4422693bbb8d387180687b3f510300f42` on `main`, analyzed 2026-10-11T08:45:33Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

One of the most widely used UI libraries, and a JavaScript monorepo wired together partly at build time: the reconciler imports a host-config module (`ReactFiberConfig.js`) that only throws, and the bundler swaps it for each renderer's real one — an edge no parser of the source can see. The repository's other half — the React Compiler (`compiler/`), DevTools and test fixtures — is excluded, as are `__tests__/` directories; this scope is the runtime a "how does setState become a DOM update" question is actually answered from. React's `.js` files carry Flow type annotations, which repo2graph parses with the plain JavaScript grammar, which has no Flow support. That costs real structure: read "Files with parse errors" below, and treat the symbol and CALLS counts as a lower bound on what the code actually contains. It is in the gallery because it is the honest picture of a Flow codebase today, not a showcase.

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

**Indexed paths:**
- `packages/react/**`
- `packages/react-reconciler/**`
- `packages/react-dom/**`
- `packages/react-dom-bindings/**`
- `packages/scheduler/**`
- `packages/shared/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 396 |
| Files parsed (code) | 386 |
| Parse errors | 19,161 |
| Files with parse errors | 241 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 3,468 |
| Edges | 8,093 |
| Symbols (all kinds) | 1,989 |
| Symbols (functions) | 947 |
| Symbols (methods) | 407 |
| Symbols (classes) | 3 |
| CALLS edges | 1,997 |
| CALLS_EXTERNAL edges | 2,048 |
| IMPORTS edges | 1,628 |
| INHERITS edges | 0 |
| DEFINES edges | 1,989 |
| Ambiguous calls (name matched >1 candidate) | 276 |
| Entrypoints | 711 |

## Supported languages

- javascript

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does setState schedule a re-render?
- How does the reconciler begin work on a fiber?
- Where does a committed fiber tree reach the DOM?
- What calls the scheduler to run a callback?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/facebook/react /tmp/react
cd /tmp/react && git checkout b618bbb4422693bbb8d387180687b3f510300f42
repo2graph build . -o .r2g --include packages/react/** packages/react-reconciler/** packages/react-dom/** packages/react-dom-bindings/** packages/scheduler/** packages/shared/**
repo2graph query "How does setState schedule a re-render?" -o .r2g
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
276 out of 1,997 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo react
```

This clones `https://github.com/facebook/react` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
