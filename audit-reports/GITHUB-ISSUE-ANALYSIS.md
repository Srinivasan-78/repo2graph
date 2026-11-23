# GitHub Issue, PR and Discussion Analysis

Consolidated from `AGENT-FINDINGS/07-github-issues.md`, with orchestrator
verification of the counts.

**No GitHub state was modified during this audit.** No issue was created, closed,
commented on, labelled or reopened; no PR, release, label or repository setting
was touched. The recommended-issue list below is a deliverable, not filed work.

## Repository statistics

Orchestrator-verified via `gh`:

| Metric | Value |
|---|---|
| Issues, all states | **272** |
| Open issues | **0** |
| Pull requests, all states | **256** |
| PRs merged | 198 |
| PRs closed unmerged | 58 |
| Security advisories | 3, all closed |
| Dependabot alerts | 37, all fixed, 0 open |
| Discussions | Disabled / empty |
| Releases | 15, v1.2.0 → v5.0.0 (latest cut 2026-11-01) |

## What the issue tracker actually is

All 272 issues were filed by the repository owner. **There are zero external
issue reports.** This tracker is a maintainer self-audit log, not a public bug
queue, and that reframes how its signals should be read:

- "Zero open issues" is not evidence of a healthy triage process; it reflects
  that issues are filed and closed in self-directed batches.
- `priority/P0` has never been used.
- Closure does not imply independent verification — which is exactly why this
  audit verified fix presence in code rather than trusting issue state.

The 58 closed-unmerged PRs were spot-checked and are **not** abandoned work:
they are mostly Dependabot churn plus an automated fix-bot account
(~30 PRs), and substantive ones land via combined follow-up PRs (e.g. #413–416
superseded by merged #418). This pattern is a consequence of the solo-account
merge-gate workaround, not of neglect.

## Regression verdict

The audit's central question: given the current working tree, are the recently
closed issues correctly classified as resolved?

| Issue | Title | Issue state | **Actual state in working tree** |
|---|---|---|---|
| #523 | Class-level pytest fixtures dropped during framework graph construction | CLOSED | **FULLY REGRESSED** |
| #524 | `SqliteGraphStore` fails in multi-threaded environments | CLOSED | **PARTIALLY REGRESSED** (worse failure mode than originally reported) |
| #525 | graphdiff conftest matching uses `.endswith()` | CLOSED | Not regressed — `graphdiff.py` untouched |
| #513 | `--affected-tests` cannot see fixtures consumed by injection | CLOSED | Base mechanism intact; class-scoped consumption broken via #523 |
| #514 | Parse-worker memory unbounded on macOS | CLOSED | Not regressed |

### #523 — fully regressed

`frameworks.py` drops the `cls_qualname` construction and `graph.py` collapses the
`base_visible`/`visible` shadow-resolution logic. Confirmed by a currently-red
committed test and by two independent reproductions. See `BUGS-AND-DEFECTS.md`
BUG-02 and BUG-03.

### #524 — partially regressed, with a worse failure mode than filed

`check_same_thread=False` is retained, so the `sqlite3.ProgrammingError` named in
the issue will not resurface. But the `threading.Lock` and all 13 of its guards
are gone, and `__enter__`/`__exit__` were removed from the `GraphStore` Protocol.

The retention of `check_same_thread=False` is what makes this worse than the
original bug: issue #524 described a loud, immediate `ProgrammingError`. The
current state produces **cursor-interleaving corruption and silently wrong
reads** — reproduced at 15/16 threads failing, including an `AssertionError` on
returned-record identity. A loud crash has been replaced by quiet wrong answers.

The suite does not catch it, because `tests/test_store.py` was edited in the same
working tree to take a lock inside the test. The committed test body was proven
capable of catching it (32/40 trials). See `BUGS-AND-DEFECTS.md` BUG-01 and BUG-04.

## Recurring themes

Worth attention because they predict where the next defect will be:

| Subsystem | Historical issue density | Note |
|---|---|---|
| MCP / HTTP transport | ~25 / ~20 keyword hits | Largest cluster |
| tree-sitter parse / fallback | ~25 hits | |
| Auth / JWT / JWKS | ~19 + ~5 hits | Source of 2 of the 3 security advisories |
| **SQLite store** | **#500–#504, #524 — five issues in six weeks** | Concurrency, read-only containers, tmp-file races, stale cache |

The SQLite store cluster is the signal that matters most. Six weeks, five
issues, all in the same module, all about concurrency or lifecycle — and now a
sixth regression in the same place. This is not bad luck; it indicates the
module's concurrency model is under-specified and under-tested relative to how
it is used. Strengthening `tests/test_store.py` (see `TESTING-AND-COVERAGE.md`
TEST-05) addresses the cause rather than the sixth symptom.

Fixes also land in **clusters** (`516fe12d` closed three issues at once;
`8f355073` landed #497–#509). Batch-fixing is efficient but raises the chance
that one hunk of a multi-issue commit gets reverted independently — which is
precisely what happened here.

## Known-issue index, for deduplication

Findings in this audit were checked against these keyword clusters before being
classified as new: MCP/HTTP transport, SSE, stdio; tree-sitter grammar/parse
fallback; auth/JWT/JWKS/OIDC; sqlite store concurrency/read-only/cache;
pytest fixtures/affected-tests; parse budgets/worker memory; encoding/Windows
CP1252; version surfaces; secret scanning; viz safety.

## Recommended issues — NOT filed

Each is a genuinely new or unresolved problem with evidence. Suggested labels and
priorities follow the repository's existing taxonomy.

| # | Suggested title | Labels | Priority | Evidence |
|---|---|---|---|---|
| R1 | Working tree reverts the #523/#524 fixes; restore the hunks from `516fe12d` | `bug`, `priority/P1`, `regression` | **P1** | `BUGS-AND-DEFECTS.md` BUG-01…BUG-05 |
| R2 | `GraphStore` Protocol lost its `__enter__`/`__exit__` contract | `bug`, `priority/P2` | P2 | BUG-05 |
| R3 | `main` is 240 commits and 3 majors behind the published release | `bug`, `priority/P1`, `release` | **P1** | `CI-CD-ASSESSMENT.md` REL-01 |
| R4 | Required checks omit all Python 3.14 cells and the container job; admin bypass always-on | `priority/P1`, `ci` | **P1** | CI-01 |
| R5 | `doctor` falsely warns on every Python 3.13.x except 3.13.0, and its matrix is stale | `bug`, `priority/P2` | P2 | COMPAT-01 |
| R6 | `SECURITY.md` states CI tests Python 3.10–3.13; actual is 3.11–3.14 | `documentation`, `priority/P2` | P2 | DOC-01 |
| R7 | Dependabot does not cover the npm or docker ecosystems | `priority/P2`, `security` | P2 | DEP-02 |
| R8 | No backward-compatibility test for the on-disk `.r2g` format | `priority/P2`, `testing` | P2 | TEST-02 |
| R9 | Concurrency tests assert absence of crashes, not correctness of results | `priority/P2`, `testing` | P2 | TEST-05 |
| R10 | npm publish silently no-ops without `NPM_TOKEN`; `release` does not depend on it | `priority/P2`, `ci` | P2 | CI-02 |
| R11 | README's "no network calls" exception list is incomplete vs SECURITY.md | `documentation`, `priority/P3` | P3 | DOC-02 |
| R12 | Three environment variables are read by code but undocumented | `documentation`, `priority/P3` | P3 | CFG-01 |
| R13 | Each file is read ~3× per build (15,043 `open()` calls for 3,000 files) | `enhancement`, `priority/P3`, `performance` | P3 | PERF-01 |
| R14 | `provenance.yml` generates no build attestation despite its name | `priority/P3`, `ci` | P3 | CI-03 |
| R15 | Add a complexity lint gate to stop `graph.build()`-scale growth recurring | `enhancement`, `priority/P3` | P3 | CQ-06 |
| R16 | Track the `tree-sitter-language-pack` 0.x cap as accepted, unpatchable risk | `security`, `priority/P3` | P3 | DEP-01 |

## Limitations

- Dependabot alert state reflects the API at audit time. This repository has a
  documented history of alerts lagging a clean dependency graph; re-verify after
  any lockfile change.
- Issue bodies were read for all open issues (zero) and for the most recent and
  most substantive closed issues, not all 272.
- Discussions are disabled or empty; nothing could be assessed there.
