# Spring Framework graph example

## Repository

[https://github.com/spring-projects/spring-framework](https://github.com/spring-projects/spring-framework)

## Revision

Commit `8caa935ae95078f8973314393a219d03cb7f43af` on `main`, analyzed 2026-10-12T08:39:54Z
by repo2graph 3.0.0.

Both halves of that line matter. The upstream commit says which *source* produced these
numbers; the repo2graph version says which *analyser* did. Call resolution has changed
across minor versions before — `b98fc46b` stopped binding builtin method calls on untyped
receivers to in-repo methods, which moved `CALLS`, ambiguous-call counts and the
most-called-symbols ranking below — so a figure here is only comparable to a run from the
same version.

## Why this repository?

The gallery's second Java entry, and a different limit from Guava's. Where Guava shows what interface dispatch costs a name-based resolver, Spring shows what dependency injection hides from one: the container decides at runtime which implementation a bean receives, and the decision lives in annotations, component scans and XML rather than in a call. So the graph maps the framework's own machinery — bean definition, post-processing, request dispatch, argument resolution — while the application-level wiring it exists to perform is not in the source to be found. The scope is the container (`spring-beans`, `spring-context`) and the web stack on top of it (`spring-web`, `spring-webmvc`), production source only.

## Why this scope

Only the subtrees listed below were cloned and indexed (a **scoped benchmark**, not the whole repository) — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour).

**Indexed paths:**
- `spring-beans/src/main/java/**`
- `spring-context/src/main/java/**`
- `spring-web/src/main/java/**`
- `spring-webmvc/src/main/java/**`

## Repository statistics

| Metric | Value |
|---|---:|
| Files indexed | 2,121 |
| Files parsed (code) | 2,121 |
| Parse errors | 92 |
| Files with parse errors | 31 |

## Graph statistics

| Metric | Value |
|---|---:|
| Nodes | 27,469 |
| Edges | 136,027 |
| Symbols (all kinds) | 21,943 |
| Symbols (functions) | 0 |
| Symbols (methods) | 19,258 |
| Symbols (classes) | 2,165 |
| CALLS edges | 76,524 |
| CALLS_EXTERNAL edges | 13,282 |
| IMPORTS edges | 19,512 |
| INHERITS edges | 2,458 |
| DEFINES edges | 21,943 |
| Ambiguous calls (name matched >1 candidate) | 17,278 |
| Entrypoints | 8,289 |

## Supported languages

- java

## Example queries

These are real `repo2graph query` runs against this index, not invented text — the generator
records each one under `flows/`, which it writes locally and does not commit (see below):

- How does the bean factory create and wire a bean?
- Where is a web request dispatched to a handler method?
- How is an annotated configuration class turned into bean definitions?
- What resolves the arguments of a handler method?

Those files hold each query's real results as citations (node id, path, line range, why it
matched) with the source text stripped out. To run these queries yourself against a live,
queryable index —
i.e. one that still has `chunks.jsonl` and can return actual source text — clone the repository at
the commit above and build it directly:

```bash
git clone --filter=blob:none https://github.com/spring-projects/spring-framework /tmp/spring-framework
cd /tmp/spring-framework && git checkout 8caa935ae95078f8973314393a219d03cb7f43af
repo2graph build . -o .r2g --include spring-beans/src/main/java/** spring-context/src/main/java/** spring-web/src/main/java/** spring-webmvc/src/main/java/**
repo2graph query "How does the bean factory create and wire a bean?" -o .r2g
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
17,278 out of 76,524 total CALLS edges.

## Reproduce

```bash
python scripts/generate_examples.py --repo spring-framework
```

This clones `https://github.com/spring-projects/spring-framework` at `main` (pinned to the commit above only via
`examples/repositories.yaml`; re-running against a moving ref will get a newer commit and
different numbers — see [docs/architecture.md](../../docs/architecture.md#3-indexing-behaviour)).
