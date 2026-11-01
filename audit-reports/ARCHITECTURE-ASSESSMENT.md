# Architecture Assessment

Consolidated from the architecture specialist's findings
(`AGENT-FINDINGS/01-architecture.md`), with orchestrator verification noted per
finding. The architecture overview itself is in `REPOSITORY-OVERVIEW.md`.

## Verdict

The architecture is **sound and better disciplined than typical for a 32k-line
single-maintainer project**. The pipeline is genuinely one-way, the storage
abstraction is clean and respected, and `docs/architecture.md` is unusually
accurate about the real design. There are no critical architectural defects.

The two real weaknesses are both about *growth*, not correctness: one layering
inversion, and a language-dispatch pattern that makes adding a language a
multi-module edit.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| ARCH-01 | MEDIUM | Confirmed (orchestrator-verified) | `repo2graph/tasks.py:246` | Core module imports from the MCP interface layer |
| ARCH-02 | LOW | Confirmed | `export.py` → `query.py` | `export` calls back into `query` for BM25 writing |
| ARCH-03 | MEDIUM | Confirmed | `cli.py:1119-2078` | `main()` is a ~959-line argparse construction function |
| ARCH-04 | LOW | Confirmed | `cli_inspect.py`, `cli_embed.py` | Import private helpers from `cli.py`; one-sided lazy import |
| ARCH-05 | MEDIUM | High | 6+ modules | Language dispatch (`if lang == "python"`) scattered, no adapter interface |
| ARCH-06 | LOW | High | 5 modules >1700 lines | Module size (see `CODE-QUALITY-REPORT.md`, CQ-01/CQ-03) |

### ARCH-01 — Core depends on the interface layer

**Orchestrator-verified.** `repo2graph/tasks.py:246` contains
`from .mcp.tools import _build_index`. The function is not defined there: it
lives at `repo2graph/mcp/indexes.py:59` and is merely re-exported through
`mcp/tools.py` and `mcp/__init__.py`.

So a core module reaches *through* the MCP interface layer to obtain
functionality that is not conceptually MCP's at all — building an index is the
project's central operation. The dependency arrow points backwards.

Why it matters: it couples core task execution to the MCP package's import
graph, meaning MCP-layer changes can break non-MCP code paths, and it makes
`tasks.py` untestable without importing the MCP stack.

**Recommendation:** extract the build logic from `mcp/indexes.py` into a neutral
core module (e.g. `repo2graph/building.py`) that both `tasks.py` and `mcp/` import.
Effort: **S**.

### ARCH-02 — `export` reaches back into `query`

`export.py` calls `query.write_bm25`, inverting the documented one-way pipeline.
The code comments already acknowledge this. Low risk, cosmetic ordering issue.
Effort: **S**. Not urgent.

### ARCH-03 / ARCH-04 — CLI structure

`cli.py:main()` is ~959 lines, but the specialist's analysis found it is
overwhelmingly declarative — roughly 182 `add_argument` calls against only ~10
real branches. This is a **navigability** problem, not a complexity one, and is
rated accordingly. Splitting per-subcommand parser builders into separate
functions or modules would address both ARCH-03 and ARCH-04 (which exists because
`cli_inspect.py` and `cli_embed.py` must reach into `cli.py` for private helpers).
Effort: **M**.

### ARCH-05 — Language extensibility is the real structural limit

Adding a language requires touching independent `if lang == "python"`-style
branches across `parse.py`, `graph.py`, `imports.py`, `refcapture.py` and
`frameworks.py`. There is no single adapter or registration point.

This is the finding with the longest-term cost, because language coverage is a
primary axis of this product's growth, and the repository has a
`language_support.yml` issue template inviting exactly those requests. Each new
language currently costs a careful sweep of five modules with no compiler or type
checker to tell the author what was missed.

**Recommendation:** define a `LanguageAdapter` protocol with per-language
implementations registered in one table. This is a substantial refactor (**L**)
and should be scheduled deliberately, not attempted opportunistically.

## Confirmed strengths

These were checked, not assumed, and are recorded because an audit that reports
only problems misrepresents the codebase.

- **No genuine circular imports.** Every cycle found by static import-graph
  analysis resolved to either a `TYPE_CHECKING`-only forward reference or a
  deliberately deferred function-body import. The pattern is applied
  consistently, indicating intent rather than accident.
- **The storage abstraction is the design's best feature.** `store.py`'s
  `GraphStore` Protocol with `MemoryGraphStore`/`SqliteGraphStore` implementations
  is minimal, clean, and honoured throughout the rest of the codebase. (That this
  is also where the audit's most severe *defect* sits is a property of the
  uncommitted diff, not of the design.)
- **`docs/architecture.md` matches reality.** The real pipeline, node and edge
  types, and feature-flag behaviour all corresponded closely to the documented
  design — rare at this size, and it materially reduces onboarding cost.
- **Two runtime dependencies** for a static-analysis tool of this scope. This is
  an architectural achievement, not merely a packaging detail: it is what makes
  the offline guarantee, the reproducible lockfile, and read-only container
  support possible at all.

## Limitations

- Import-graph analysis was static; dynamic import behaviour at runtime was not
  traced.
- Extensibility cost (ARCH-05) was assessed by reading the dispatch sites, not by
  actually attempting to add a language.
