# Repository Overview

Established by direct inspection of the working tree at `E:\Github\repo2graph`,
branch `develop`, HEAD `f43b1369`, on 2026-11-01.

## What the project is

`repo2graph` turns a source tree into a code graph with one tree-sitter pass, then
answers natural-language questions about that codebase by retrieving the actual
source that answers them — every block headed `[cite: path:start-end]` and the
whole reply held under an enforced token ceiling.

Its distinguishing claim is what it does **not** need: no model, no API key, no
language server, no database. Retrieval is BM25 plus graph expansion over
recorded relationships (who calls whom, who imports what, which files co-change
in git history), not embeddings — those are strictly optional.

## Intended users and use cases

| User | Use case | Surface |
|---|---|---|
| Coding agents (Claude Code, Cursor, Claude Desktop) | Ask the repo a question, get cited source back under a token budget | MCP server (stdio, optional SSE) |
| Developers at a terminal | `build` an index, then `query` / `rag` / `explain` it | CLI |
| CI pipelines | Semantic PR diffing, affected-test selection | GitHub Action (`action.yml`) |
| Editor users | Callers, callees, base classes, co-changed files for the symbol at the cursor | VS Code extension (preview) |
| Library consumers | Programmatic indexing and retrieval | Python API (`py.typed`) |

## Scale and shape

| Metric | Value |
|---|---|
| Tracked files | 410 |
| Python source (`repo2graph/`) | 31,873 lines across 60 modules |
| Test files | ~200, ~2,782 tests |
| Largest modules | `parse.py` 2300, `graph.py` 2172, `query.py` 2123, `cli.py` 2110, `export.py` 1698, `viz.py` 1328, `security.py` 1181 |
| Documentation | 45 markdown files, plus a 226 KB CHANGELOG and a 28 KB `action.yml` |
| Distribution channels | PyPI, npm (launcher), Docker, GitHub Action, VS Code extension |
| Declared Python support | `>=3.11`, classifiers 3.11–3.14 |
| Runtime dependencies | 2 (`tree-sitter`, `tree-sitter-language-pack<1.0`) |

A two-dependency runtime for a 32k-line static-analysis tool is a deliberate and
unusual achievement, and it is the foundation of most of the project's other
guarantees (offline operation, reproducible lockfile, read-only container).

## Architecture

The pipeline is one-way: discover → parse → graph → store → retrieve → present.

```
                        ┌──────────────────────────────────────────┐
  source tree ─────────▶│ discovery.py   walk / git-aware listing  │
                        │ exclusions.py  ignore rules              │
                        └───────────────────┬──────────────────────┘
                                            ▼
                        ┌──────────────────────────────────────────┐
                        │ parse.py       tree-sitter, per language │
                        │ parsepool.py   process pool + budgets    │
                        │ parsecache.py  incremental reuse         │
                        │ limits.py      time / memory ceilings    │
                        └───────────────────┬──────────────────────┘
                                            ▼
                        ┌──────────────────────────────────────────┐
                        │ graph.py       build(): nodes + edges    │
                        │ imports.py     import resolution         │
                        │ refcapture.py  reference edges           │
                        │ frameworks.py  pytest / web framework    │
                        │ derived.py     derived edges             │
                        │ cochange.py    git history co-change     │
                        │ scip.py        optional SCIP ingest      │
                        └───────────────────┬──────────────────────┘
                                            ▼
                        ┌──────────────────────────────────────────┐
                        │ store.py   GraphStore (Protocol)         │
                        │   ├─ MemoryGraphStore  (default)         │
                        │   └─ SqliteGraphStore  (large repos)     │
                        │ export.py  .r2g artifacts on disk        │
                        │ chunks.py  chunking   security.py redact │
                        └───────────────────┬──────────────────────┘
                                            ▼
                        ┌──────────────────────────────────────────┐
                        │ query.py    Index, retrieve, pack_context│
                        │ rerank.py   RRF fusion                   │
                        │ embed.py    optional dense vectors       │
                        │ answer.py   optional LLM call (opt-in)   │
                        └───────────────────┬──────────────────────┘
                                            ▼
          ┌──────────────────┬──────────────┴──────┬────────────────────┐
          ▼                  ▼                     ▼                    ▼
       cli.py            mcp/server.py          viz.py            neo4j.py
    cli_inspect.py       mcp/tools.py        (HTML graph)       federated.py
    cli_embed.py         mcp/retrieval.py                        graphdiff.py
                         mcp/traversal.py
```

### Storage abstraction

`store.py` defines a `GraphStore` Protocol with two implementations — everything
resident (`MemoryGraphStore`, the default) or backed by a read-only SQLite file
(`SqliteGraphStore`, for repos too large to hold in memory). The abstraction is
respected throughout the rest of the codebase and is one of the design's clear
strengths. It is also where this audit's most severe finding lives.

### Critical execution paths

1. **Build** — `cli build` → discovery → parse pool → `graph.build()` → `export` → `.r2g/`
2. **Query** — `cli query/rag` → `Index` → store → BM25 + graph expansion → `pack_context()` → cited markdown
3. **MCP** — stdio/SSE server → `mcp/tools.py` → `mcp/retrieval.py` / `traversal.py` → same `Index`
4. **CI diff** — `cli diff --affected-tests` → `graphdiff.py` → fixture/test edges from `frameworks.py`

Paths 2, 3 and 4 all depend on graph correctness produced in path 1, which is why
a silent graph-construction defect is more damaging here than a crash would be.

## Compatibility and guarantees the project makes

- **No network calls** during build, query, or the MCP server. Enumerated
  exceptions: `rag --answer`, `repo2graph github` and GitHub-spec targets (clone),
  and `--vectors`/`embed` (model download). Verified by inspection: outbound HTTP
  exists only in `answer.py`; `fetch.py` shells out to `git`.
- **Grammars ship compiled in the wheel.** `tree-sitter-language-pack` is capped
  `<1.0` specifically because 1.x downloads ~21 MB of unsigned native code on
  first parse. This is a security boundary, not a compatibility lag.
- **Determinism** — asserted by `tests/test_determinism.py`.
- **Typed public API** — `py.typed` ships in the wheel, enforced by tests.

## Release and branch topology — as found

This is not incidental background; it materially affects what users receive, and
it is reported as a finding (`REL-01`) in `CI-CD-ASSESSMENT.md`.

| Fact | Value |
|---|---|
| GitHub default branch | `main` |
| `main` HEAD | `63a73503` "chore(release): bump version to 2.2.0", dated **2026-09-26** |
| `main` `pyproject.toml` version | **2.2.0** |
| `develop` HEAD (this working tree) | `f43b1369`, dated 2026-11-01 |
| `develop` `pyproject.toml` version | **4.1.0** |
| Latest published GitHub release | **v5.0.0**, 2026-11-01 |
| Is the v5.0.0 release commit on `develop`? | **No** |
| Commits `develop` is ahead of `main` | **240** |
| Commits `main` is ahead of `develop` | 0 |

Three different versions are live simultaneously: the default branch says 2.2.0,
the development branch says 4.1.0, and the published release is 5.0.0. The
`v5.0.0` bump commit (`2a30c17e`) exists only on the release branch and was never
merged back, which is why `develop`'s CHANGELOG still files the #523/#524/#525
fixes under `## [Unreleased]` even though they shipped in v5.0.0.

## Working tree state

The tree was **dirty** when the audit began, and was deliberately left untouched:

```
 M repo2graph/frameworks.py
 M repo2graph/graph.py
 M repo2graph/store.py
 M tests/test_store.py
```

These four files are the subject of the audit's highest-severity findings. See
`BUGS-AND-DEFECTS.md`.
