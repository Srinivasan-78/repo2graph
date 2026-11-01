# Code Quality Report

Consolidated from `AGENT-FINDINGS/02-code-quality.md`, with orchestrator
verification. Correctness defects are in `BUGS-AND-DEFECTS.md`; this report
covers maintainability and design.

## Verdict

**This is a clean, disciplined codebase with one concentrated maintainability
problem.** The tooling baseline is spotless, typing is strict and complete, error
handling is deliberate, and the prose comments are accurate rather than stale.
The problem is function size in the hottest modules — a small number of very
large functions on the most important code paths.

## Measured baseline

All commands executed by the orchestrator on the working tree:

| Command | Exit | Result |
|---|---|---|
| `python -m ruff check .` | 0 | `All checks passed!` |
| `python -m ruff format --check .` | 0 | `280 files already formatted` |
| `python -m mypy repo2graph/` | 0 | `Success: no issues found in 60 source files` |

Also observed: **no `TODO`/`FIXME` markers anywhere** in the package, and mypy
runs strict with no per-module relaxations other than numpy. For 32k lines this
is a genuinely strong baseline, and it should be read as such — a clean ruff and
strict-mypy pass on a codebase this size is not the default state of the world.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| CQ-01 | **HIGH** | Confirmed (orchestrator-verified) | `graph.py:1380-2172` | `build()` is 792 lines, ~155 branches, 6 state-capturing nested closures |
| CQ-02 | MEDIUM-HIGH | Confirmed (orchestrator-verified) | `query.py:1537-1923`, `1397-1517` | `pack_context()` 384 lines/~89 branches; `retrieve()` 116 lines |
| CQ-03 | MEDIUM | High | 8 modules | 10 further functions of 160–350 lines, 17–104 branches each |
| CQ-04 | MEDIUM | Confirmed | `viz.py:459-1328` | 65% of the module is an inline HTML/CSS/JS string, invisible to ruff and mypy |
| CQ-05 | LOW | Confirmed | `cli.py:1119-2078` | `main()` is 959 lines, but ~182 `add_argument` calls and only ~10 real branches |
| CQ-06 | LOW | Confirmed | `pyproject.toml:184-205` | No McCabe/complexity lint gate, so CQ-01…CQ-03 can grow unchecked |

### CQ-01 — `graph.build()` is the single biggest maintainability risk

**Orchestrator-verified:** `def build(` is at `graph.py:1380`; the file ends at
2172. 792 lines, confirmed.

It implements the call-resolution tiers using six nested closures that capture
mutable build state. The consequence is not aesthetic: **no resolution tier can
be unit-tested in isolation.** Every test of resolution behaviour must run a full
build and inspect the resulting graph, which is why the regression tests for
issues #513/#523 take seconds each and assert on build statistics rather than on
resolution logic directly.

This directly amplifies BUG-02 and BUG-03 in `BUGS-AND-DEFECTS.md`: both are
small logic errors in fixture resolution, and both were detectable only through
whole-build assertions. Finer-grained tests would have localised them instantly.

**Recommendation:** extract the resolution tiers into a module of pure functions
taking explicit state, rather than closures over build-local variables. Effort:
**L**, and best done incrementally with the existing build-level tests as a
safety net. This is the highest-value refactor in the codebase.

### CQ-02 — `pack_context()` sits on the correctness-critical path

**Orchestrator-verified:** `pack_context` at `query.py:1537`, `retrieve` at
`query.py:1397`.

`pack_context()` is 384 lines with ~89 branches, and it is the function that
enforces the **token budget** and assembles the **citations** — the two things the
product most loudly promises. Complexity concentrated exactly where correctness
matters most is worse than the same complexity elsewhere. Budget-boundary and
citation-fidelity behaviour is hard to reason about and hard to test
exhaustively at this size.

**Recommendation:** separate candidate selection, budget accounting, and
rendering into distinct functions so budget arithmetic can be property-tested
independently. Effort: **M–L**.

### CQ-04 — `viz.py`'s template is outside all tooling

Roughly 870 lines of `viz.py` are an inline HTML/CSS/JavaScript string. Neither
ruff nor mypy nor any JS linter sees it. A syntax error in that JavaScript is
discoverable only by opening the generated page, and the security review's
XSS-escaping analysis had to be done by reading the string by hand.

**Recommendation:** move the template to a separate asset file loaded via
`importlib.resources`; it becomes lintable and reviewable. Effort: **M**.

### CQ-06 — Nothing prevents recurrence

There is no complexity gate in the ruff configuration. `mccabe` via ruff's `C901`
rule would cap this. Adding it with a generous initial threshold and a per-file
ignore list for existing offenders makes the debt visible and stops it growing.
Effort: **S**. This is the cheapest durable improvement in this report.

## Confirmed strengths

Verified by sampling, not assumed:

- **Resource handling is disciplined.** Context managers are used consistently
  for files, connections and pools.
- **No silent error swallowing.** Every broad `except Exception` site sampled is
  a commented, deliberate resilience boundary. None was found converting an
  error into a silently wrong result. For a tool of this kind that was the
  specific risk looked for, and it was not found.
- **The long prose comments are accurate.** Every comment sampled matched the
  code it described. The `pyproject.toml` dependency comments in particular
  encode real, still-valid engineering decisions. This repository's unusual
  comment density is an asset, not noise.
- **No duplicated logic**, despite several same-named functions existing across
  modules — they were checked and are genuinely distinct.

## Style observations (explicitly not defects)

- Function and module sizes exceed common conventions, but the project's own
  idiom is large cohesive modules; CQ-01…CQ-05 are rated on *testability and
  navigability* consequences, not on line counts against an external style rule.
- Comment-to-code ratio is far above typical. Judged an asset here.

## Limitations

- Branch counts are AST-derived approximations, not calibrated McCabe scores.
- Modules outside the listed set received lighter grep-based coverage.
- The four files with uncommitted changes were read as found but not critiqued
  for quality, since that work is in flight; they are assessed for correctness in
  `BUGS-AND-DEFECTS.md` instead.
