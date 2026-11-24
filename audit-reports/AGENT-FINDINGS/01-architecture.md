# Agent 1 — Architecture & Structure Audit

## Architecture Overview

repo2graph turns a source tree into a queryable code graph (nodes: repo/dir/file/symbol/module/external/route;
edges: CONTAINS/DEFINES/IMPORTS/CALLS/CALLS_EXTERNAL/INHERITS/CO_CHANGE/TESTS/READS/WRITES/REFERENCES/ROUTES_TO/INJECTS/MODELS),
persists it as an on-disk `.r2g` artifact directory, and serves it to humans (CLI, `graph.html`) and
agents (MCP server, `repo2graph.query.Index`/`pack_context` for RAG).

Entry points: `repo2graph.cli:main` (console script `repo2graph`), `repo2graph.mcp:main`
(console script `repo2graph-mcp`), and the public Python API re-exported from `repo2graph/__init__.py`
(`Graph`, `build`, `build_chunks`, `iter_chunks`, `write_html`) — intentionally small; `docs/python-api.md`
documents using `query.Index` directly for programmatic retrieval.

Pipeline, matching `docs/architecture.md`'s diagram (confirmed against the real import graph):

```
discovery.py --> parse.py --> graph.py --> {chunks.py, export.py, viz.py} --> query.py --> {cli.py, mcp/, explain.py, answer.py}
                     |            |
              parsepool.py    derived.py / cochange.py / frameworks.py / imports.py / refcapture.py / scip.py
              parsecache.py
```

- **parse.py** (2300 lines) — tree-sitter grammar dispatch, file discovery glue, symbol/import extraction.
  `parsepool.py` runs it in worker processes with a watchdog/timeout; `parsecache.py` memoizes parsed
  files keyed on content+config so unchanged files skip reparsing.
- **graph.py** (2172 lines) — `build()` is the orchestrator: turns parsed files into nodes/edges, resolves
  `CALLS`/`INHERITS` through a tiered heuristic (same-file, import-resolved, receiver-typed, name-only),
  optionally merges in SCIP (`scip.py`) and framework edges (`frameworks.py`), delegates reference-edge
  derivation to `derived.py` and git-history `CO_CHANGE` to `cochange.py`.
- **store.py** (587 lines) — `GraphStore` `Protocol` with two implementations, `MemoryGraphStore` (dict +
  adjacency list, everything resident — the historical default) and `SqliteGraphStore` (on-disk cache with
  a versioned schema, `SQLITE_STORE_FORMAT`). Clean abstraction; `query.Index` depends only on the Protocol.
- **chunks.py** — cuts the graph's symbols into retrieval units (one per symbol + a file residual).
- **export.py** (1698 lines) — owns the `.r2g` on-disk layout (`agent/` for machines, `human/` for people;
  `local.json` kept out of `agent/` so it never gets published), writes `nodes.jsonl`/`edges.jsonl`/
  `chunks.jsonl`/`manifest.json`/`stats.json`/`graph.graphml`/`graph.cypher`/`CHANGELOG.md`, and the BM25
  sidecar via a lazy import into `query.py` (see ARCH-02).
- **query.py** (2123 lines) — `Index` class: BM25 scoring, RRF fusion with optional dense embeddings
  (`embed.py`), cross-encoder reranking (`rerank.py`), graph expansion and `pack_context` (the RAG protocol
  both `cli rag` and the MCP server call into).
- **answer.py** (880 lines) — turns packed context into a cited answer (used by `cli rag`/explain paths).
- **mcp/** (3667 lines across 10 modules) — a `server.py` stdio loop, `tools.py` the dispatch table,
  `retrieval.py`/`traversal.py`/`nodes.py` the actual tool implementations (thin wrappers over
  `query.Index`), `indexes.py` open/auto-build/cache lifecycle, `guardrails.py` + `schemas.py` input
  validation, `pagination.py`, `resources.py` for MCP resource URIs.

Data mostly does flow one way, as the docs claim, with two documented/acknowledged exceptions handled
via lazy (function-body) imports rather than restructuring — see ARCH-01/ARCH-02 below. No actual
*runtime* import cycle exists (Python would fail to import otherwise); several apparent cycles found by
static analysis are `TYPE_CHECKING`-only forward references (`derived.py`→`graph.Graph`,
`cochange.py`→`graph.Graph`, `refcapture.py`→`parse.Symbol`) and are not a real coupling problem.

## Findings

### ARCH-01: `tasks.py` (core package) depends on `mcp/` (the outer interface layer)

- **Severity:** MEDIUM
- **Confidence:** Confirmed
- **Location:** `repo2graph/tasks.py:244-248` (`_default_builder` does `from .mcp.tools import _build_index`),
  which re-exports `repo2graph/mcp/indexes.py:59` (`_build_index`) through `repo2graph/mcp/tools.py:34,97`.
- **Description:** `tasks.py` is a top-level package module (background-task bookkeeping for async builds)
  with no other dependency on `mcp/`. Its only way to actually build an index is to reach into
  `repo2graph.mcp.tools._build_index`, a private helper that lives inside the MCP subpackage. The real
  "build and dump an index" logic (`graph.build` + `export.dump_all` + config/lock handling) is itself
  defined inline inside `mcp/indexes.py:_build_index`, not in a neutral module `tasks.py` and `cli.py`
  could share.
- **Why it is a problem:** Per the project's own architecture doc, `mcp/` sits *downstream* of the core
  pipeline (parallel to `cli.py`, consuming `query.py`). A core utility module depending on that outer
  layer inverts the intended dependency direction. It also means the "auto-build on demand" behavior is
  logically core functionality (any consumer could want it) but is implemented and reachable only through
  the MCP package, reducing its reusability and making `tasks.py` harder to reason about in isolation.
  The cycle is only avoided at import time because both sides use function-local (deferred) imports
  (`tasks.py:246`, `mcp/indexes.py:168`) — a documented pattern elsewhere in the codebase to work around
  layering problems rather than fix them.
- **Recommendation:** Extract `_build_index`'s body (validate_outdir → load config → BuildLock →
  `graph.build` → `export.dump_all` → gitignore stamp) into a core module (e.g. a new `repo2graph/build.py`,
  or a function on `graph.py`/`export.py`), and have both `mcp/indexes.py` and `tasks.py` import it from
  there. No change in behavior, just moves the shared logic out of the interface layer.
- **Estimated effort:** S

### ARCH-02: `export.py` reaches back up into `query.py` for BM25 writing

- **Severity:** LOW
- **Confidence:** Confirmed
- **Location:** `repo2graph/export.py:1642` (`from .query import write_bm25`, inside `dump_all`'s body);
  acknowledged directly in the code at `repo2graph/export.py:41` and `:956` ("export is the lower layer of
  the two and query.py imports it").
- **Description:** The pipeline diagram and the docs' "data flows one way" claim put `export.py` upstream
  of `query.py` (stage 5 before stage 6). In practice, `export.dump_all` must itself call
  `query.write_bm25` to produce the `bm25.jsonl` sidecar, which is the reverse direction. The project is
  aware of this (two separate code comments explain it) and resolves the load-time cycle by deferring the
  import to inside the function body rather than, say, moving `write_bm25` into `export.py` or a shared
  module.
- **Why it is a problem:** It is a minor, self-acknowledged, already-mitigated layering inversion — no
  import-time breakage, no behavioral risk — but it does mean "export knows nothing about query" is not
  quite true, and a reader relying on the architecture doc's one-way-flow claim would be mildly surprised.
- **Recommendation:** Low priority; if ever revisited, move `write_bm25` (or just the BM25 index format) to
  a module both `export.py` and `query.py` can depend on without inversion (e.g. alongside `chunks.py`).
  Not worth doing opportunistically given the low risk.
- **Estimated effort:** S

### ARCH-03: `cli.py` is a ~2100-line god module built around one ~900-line `main()`

- **Severity:** MEDIUM
- **Confidence:** Confirmed
- **Location:** `repo2graph/cli.py:1119` (`def main`) runs to roughly line 2010 — a single function
  constructing all ~17 argparse subcommands (`version`, `build`, `github`, `query`, `rag`, `embed`, `map`,
  `stats`, `diff`, `explain` (+3 sub-subcommands), `bugreport`, `inspect`, `demo`, `doctor`, `compat`,
  `export`, …), plus `cmd_build` (`:187-456`, ~270 lines) and `cmd_query`/`cmd_rag`/`cmd_github` defined in
  the same file.
- **Why it is a problem:** Most of the actual business logic is well delegated to focused modules
  (`status.py`, `doctor.py`, `audit.py`, `explain.py`, `demo.py`, `graphdiff.py`, `cli_inspect.py`,
  `cli_embed.py` all live outside `cli.py` and are imported lazily by `main()` to keep import cost down).
  What remains is almost entirely argparse plumbing: one function wiring 17+ subcommands and their flags.
  That's still a maintenance cost — finding or changing one subcommand's flags means navigating an 850+
  line function — but it is a narrower problem than "2100 lines of mixed concerns" would suggest.
- **Recommendation:** Split `main()`'s parser construction into one `_add_<subcommand>_parser(sub)`
  function per subcommand (or per related group), called from `main()`. Mechanical, low-risk, improves
  navigability without touching behavior.
- **Estimated effort:** M

### ARCH-04: Satellite CLI modules import private helpers from `cli.py`, with the dependency only working because of a one-sided lazy import

- **Severity:** LOW
- **Confidence:** Confirmed
- **Location:** `repo2graph/cli_inspect.py:11` (`from .cli import _effective_exclude, _emit, _require_index, _with_repo_config`),
  `repo2graph/cli_embed.py:10` (`from .cli import _emit, _require_index`); `cli.py` imports both modules back,
  but only inside `main()`'s body (`repo2graph/cli.py:1122-1123`), not at module level.
- **Description:** `cli_inspect.py` and `cli_embed.py` depend on underscore-prefixed (private) helpers
  defined in `cli.py` at module scope. `cli.py` depends on `cmd_embed`/`cmd_verify_rag`/inspect commands
  from those modules, but only via a deferred import inside `main()`, which is what keeps this from being
  an unresolvable circular import.
- **Why it is a problem:** The three modules are mutually dependent in practice; the split into separate
  files buys organization but not real decoupling, and the shared helpers being "private" to `cli.py`
  signals they were factored out of `cli.py` reactively (to support the satellite modules) rather than
  designed as a shared surface.
- **Recommendation:** If `_emit`/`_require_index`/`_effective_exclude`/`_with_repo_config` are genuinely
  shared CLI infrastructure, move them to a small `cli_common.py` that all three modules import
  one-directionally. Cosmetic/organizational, not urgent.
- **Estimated effort:** S

### ARCH-05: Language-specific branching is scattered across 6+ modules instead of one adapter point

- **Severity:** MEDIUM
- **Confidence:** High
- **Location:** `if lang == "python"` / per-language dispatch appears independently in `parse.py` (e.g.
  `:1095`, `:1661`, `:2009`, `:2082`), `graph.py`, `imports.py`, `refcapture.py`, `frameworks.py`
  (`FRAMEWORK_LANGS = frozenset({"python", "javascript", "typescript", "tsx"})`, `frameworks.py:45`), and
  `bugreport.py`.
- **Description:** There is no single "language adapter" interface (e.g. a `Language` protocol with
  `parse`, `resolve_import`, `extract_routes`, etc.); instead each module that needs language-aware
  behavior keeps its own `if lang == "python"` / `_py_ref if lang == "python" else _js_ref`-style branch,
  independently of the others.
- **Why it is a problem:** Adding a new language (or extending an existing one's coverage — e.g. Java/
  Kotlin framework annotations, called out as "not covered yet" in `docs/architecture.md`) requires
  touching parse.py, graph.py, imports.py, refcapture.py and frameworks.py separately, each with its own
  local convention for the branch, rather than implementing one documented interface. This raises the cost
  and error surface of the single most natural extension point for the project (one more language). By
  contrast, route/dependency-injection extraction in `frameworks.py` itself is reasonably well generalized
  via lookup tables (`HTTP_METHODS`, `NON_ROUTE_RECEIVERS`) rather than per-framework branches — that part
  of the extensibility story is good (see Strengths).
- **Recommendation:** Not urgent to fix wholesale, but worth tracking: a `languages.py` registry
  (suffix → language name → capability flags/callables) would let new-language work touch one file's
  table plus the grammar-specific logic, instead of hunting for every `lang ==` string. Given the scope
  (tree-sitter query patterns differ per grammar) this is a genuine design project, not a quick refactor.
- **Estimated effort:** L

### ARCH-06: Five modules exceed ~1700 lines and mix several responsibilities each

- **Severity:** LOW
- **Confidence:** High
- **Location:** `repo2graph/parse.py` (2300 lines, 49 top-level defs), `repo2graph/graph.py` (2172, 23),
  `repo2graph/query.py` (2123, 25), `repo2graph/cli.py` (2110, 39), `repo2graph/export.py` (1698, 39).
- **Description:** `parse.py` mixes file discovery re-export, tree-sitter grammar loading/caching,
  per-language symbol/import extraction, and docstring/comment handling. `graph.py` mixes the
  node/edge data model, the multi-tier `CALLS`/`INHERITS` resolution heuristics, SCIP merge orchestration,
  and git-history wiring (though the latter two are at least delegated to `scip.py`/`cochange.py` for the
  heavy lifting). `query.py` mixes BM25 indexing, RRF fusion, graph expansion, context packing, and
  per-field confidence filtering.
- **Why it is a problem:** Each of these is large enough that a reader needs to hold a lot of unrelated
  context to make a safe change in one corner of it; it increases review burden and the chance that an
  unrelated change in the same file causes a merge conflict or an inadvertent side effect. This is less
  severe than it looks, though: within each file, responsibilities are reasonably well separated into
  distinct, consistently-named top-level functions/classes (not spaghetti) — the problem is file size and
  the number of concerns per file, not internal structure.
- **Recommendation:** Lowest priority of the findings here; splitting any of these is a large, mechanical,
  and fairly risky refactor for marginal benefit given the internal organization is already decent. Only
  worth doing if a specific future feature keeps needing to touch unrelated code in the same file.
- **Estimated effort:** L (each)

## Strengths

- **The storage abstraction is clean.** `store.py`'s `GraphStore` `Protocol` (7 methods) with
  `MemoryGraphStore`/`SqliteGraphStore` implementations is a textbook example of the right-sized
  abstraction: `query.Index` depends only on the protocol, the schema is explicitly versioned
  (`SQLITE_STORE_FORMAT`), and nothing elsewhere in the codebase reaches past the protocol into either
  implementation's internals.
- **No real import cycles.** Despite several large, long-lived modules, nothing in the codebase has an
  actual circular-import problem; every apparent cycle this audit found is either a `TYPE_CHECKING`-only
  forward reference or a deliberately deferred (function-body) import, and both patterns are used
  consistently and are explained in code comments where they exist (`export.py:41,956`;
  `mcp/indexes.py:168`).
- **The on-disk artifact format is well thought through.** `export.py`'s `agent/`/`human/` split, the
  explicit exclusion of `local.json` from what gets published, and the `manifest.json` describing which
  optional features (`--scip`, `--reference-edges`, `--framework-edges`) are present in a given build are
  all genuinely good design for a tool whose output is meant to be committed/shared and consumed by both
  humans and other tools.
- **Opt-in feature flags keep the default build's output stable.** `--reference-edges`,
  `--framework-edges`, and `--scip` are all designed so that not passing them produces an artifact
  byte-identical to a build that never had the feature — a real discipline that prevents feature creep
  from silently changing everyone's default output.
- **The framework/route extraction is reasonably generalized**, not per-framework special-cased:
  `frameworks.py`'s `route_from_decorator` works off a `HTTP_METHODS` lookup table and a
  `NON_ROUTE_RECEIVERS` exclusion set rather than branching per framework, so FastAPI/Flask/Starlette/
  Sanic/NestJS are handled by one code path.
- **`docs/architecture.md` is unusually accurate and detailed** for a project this size — the pipeline
  diagram, node/edge type tables, and the documented limits of each heuristic (e.g. `TESTS` edges, SCIP
  merge rules, reference-edge binding rules) all matched what's actually in the code; divergences found
  were minor and already self-acknowledged in code comments (ARCH-01, ARCH-02), not surprises.

## Limitations of this assessment

- Import-graph analysis was static (AST-based) and resolved only `repo2graph.*` imports; it does not model
  dynamic imports via strings (e.g. `importlib.import_module`), of which none were observed via grep but a
  targeted search was not exhaustive.
- Pytest was not run (per instructions); no runtime/behavioral verification of any claim here — this is a
  structural read of the source only.
- `mcp/` was reviewed at the module/import level, not function-by-function; its internal cohesion
  (e.g. whether `retrieval.py` vs `traversal.py` vs `nodes.py` boundaries make sense) was not deeply audited
  and may be worth a closer pass by whichever agent covers the MCP surface in detail.
- Did not benchmark or measure actual coupling metrics (fan-in/fan-out counts); findings on "god modules"
  are based on line counts, top-level definition counts, and spot-reading, not a full dependency-weight
  analysis.
