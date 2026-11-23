# Executive Summary

**Repository:** `Srinivasan-78/repo2graph` · **Branch:** `develop` · **HEAD:** `f43b1369`
**Audited:** 2026-11-01 · Windows 11, Python 3.13.15
**Method:** 11 specialist subagents + lead-orchestrator verification; full test
suite, lint, type check and three custom reproductions executed.

---

## Overall health: **7.3 / 10**

## Maturity: **Functional open-source project, trending production-ready. Not enterprise-ready.**

This is a **well-engineered project with a release-process problem**, not a
shaky project with good intentions. The spread of dimension scores is narrow
(6–9), which is itself the finding: there is no single catastrophic weakness and
no dimension that is merely polished. Security is the strongest dimension (9);
CI/CD configuration is the weakest (6) — and its fixes are the cheapest in the
report.

The gap between this project's engineering practice and its release hygiene is
the single most actionable observation in this audit: **the hard part is already
done well, and the cheap part is what holds the score down.**

---

## The most important finding

### A dirty working tree reverts released fixes, and the test that would catch it was disabled

The working tree was found with uncommitted changes to four files. Those changes
partially revert commit `516fe12d` — which fixed issues #523, #524 and #525 and
**shipped in v5.0.0 on the day of this audit**.

What makes this severe rather than ordinary work-in-progress:

| | |
|---|---|
| **Confirmed by execution** | 15 of 16 threads fail against the unlocked SQLite store. 0 fail with the lock restored. Reproduced three times independently. |
| **The failure mode is silent wrong data** | Not a clean error. `fetchone()` returns `None` where a row exists; `json.loads` receives `''`; and in the decisive test an assertion on **record identity** failed — the store returned the wrong node. For a tool whose product is *cited, trustworthy answers about code*, this undermines the central guarantee invisibly. |
| **A test was silenced, not fixed** | `tests/test_store.py` was edited to take a `threading.Lock()` **inside the test**, serialising the eight workers meant to run concurrently. The test now passes whether the store is thread-safe or not. |
| **That test demonstrably worked before** | The committed test body was replayed verbatim against the unlocked store: **32 failures in 40 trials**. CI would have gone red. The edit is what turned a red suite green while the defect remained. |

This audit does **not** assume intent — this may be an interrupted experiment or
an accidental clobber. The remedy is identical either way: **restore the hunks
from `516fe12d` before committing anything.** Effort: one mechanical revert.

### And a second, independent problem: nobody sees the current code

| Fact | Value |
|---|---|
| GitHub default branch | `main` |
| `main` version | **2.2.0**, dated 2026-09-26 |
| `develop` version | **4.1.0** |
| Latest published release | **v5.0.0**, 2026-11-01 |
| `develop` behind/ahead of `main` | **240 commits ahead** |
| v5.0.0 release commit on `develop`? | **No** — never merged back |

Three versions are live simultaneously. Every repository visitor, every
`git clone`, every rendered README, and the PyPI listing's own documentation
links all resolve to **2.2.0** — five weeks and 240 commits stale. Every
documentation improvement this audit verified is invisible to users.

---

## Findings: 50 total

| Severity | Count | |
|---|---|---|
| **CRITICAL** | **1** | Silently wrong data from the unsynchronised SQLite store |
| **HIGH** | **7** | 3 further working-tree regressions · stale default branch · merge-gate gap · `graph.build()` complexity · no index-format compatibility test |
| MEDIUM | 17 | |
| LOW | 22 | |
| INFO | 3 | |

Plus **1 finding rejected** by orchestrator verification and **3 deduplicated**.

**Five of the eight CRITICAL/HIGH findings come from the single uncommitted
diff.** The committed codebase is materially healthier than the raw counts
suggest.

---

## Testing status — executed, not estimated

| Check | Result |
|---|---|
| `pytest -q -n auto` | **1 failed, 2763 passed, 18 skipped, 1 xfailed — 66.02s** |
| The one failure | `test_pytest_nested_classes_and_shadowed_fixtures` — the #523 revert, caught by a committed test |
| `ruff check .` | **Clean** |
| `ruff format --check .` | **Clean**, 280 files |
| `mypy repo2graph/` (strict) | **Clean**, 60 source files |
| Custom reproductions | 3 written and run; all confirmed their findings |

No test result in this audit is estimated, and no coverage percentage is claimed
that was not measured. Blocked checks are listed explicitly in
`TEST-EXECUTION-LOG.md`.

---

## Security: the strongest dimension

**No exploitable CRITICAL or HIGH vulnerability was found.** Five LOW/INFO
hardening items only.

More notable than the absence of findings is that sensitive call sites carry
explicit adversarial-review comments naming the specific attack they defend
against. Every security claim that was tested held:

- "No network calls" during build/query/MCP — **verified** by independent grep;
  outbound HTTP exists only in the opt-in `answer.py`.
- The `tree-sitter-language-pack<1.0` cap is a real, enforced security boundary
  (1.x would fetch ~21MB of unsigned native code at first parse) — **do not raise it**.
- MCP path confinement, `viz.py` XSS escaping, SHA-pinned actions, narrowly
  justified zizmor suppressions — all **verified intact**.

Caveat: `bandit`, `pip-audit` and `zizmor` are absent from this environment **and
from CI**, so the review was manual. Adding them is a P2 item.

---

## Top recommendations

| | Action | Effort |
|---|---|---|
| **P0** | Restore the reverted hunks from `516fe12d` across the four modified files | **S** |
| **P1** | Make the default branch reflect the released state; merge v5.0.0 back | **S** + decision |
| **P1** | Add the 3 Python-3.14 cells and the container job to required checks; end permanent admin bypass | **S** |
| **P1** | Make concurrency tests assert *result correctness*, not absence of crashes | **S–M** |
| **P1** | Fix `doctor`'s tuple comparison and stale support matrix — it currently warns most users their Python is unsupported | **S** |
| **P2** | Correct `SECURITY.md`'s Python matrix; extend Dependabot to npm and docker; add an index-format compatibility test | **S**–**M** |
| **P3** | Decompose `graph.build()` (792 lines, ~155 branches) — the highest-value refactor available | **L** |

Seven of the eight conditions for production-ready status are **S** effort. That
milestone is close, not distant.

---

## Critical risks

1. **Committing the working tree as-is** would ship silent data corruption under
   concurrency with no test coverage to catch it.
2. **The SQLite store has had five issues in six weeks**, all concurrency or
   lifecycle — now a sixth. The concurrency model is under-specified relative to
   how the module is used; strengthening its tests addresses the cause rather
   than the next symptom.
3. **The merge gate is narrower than CI**, and permanent admin bypass is the exact
   mechanism that previously tagged a release on failing tests.
4. **No on-disk index format compatibility test**, against five major releases in
   under two months. A schema change breaks every user's cache with no CI signal.
5. **The `tree-sitter-language-pack` 0.x pin is correct but unpatchable** — the
   line appears frozen upstream, so a future grammar vulnerability has nowhere to
   bump to. Track it as accepted risk; vendoring is the eventual exit.

---

## What is genuinely strong

Each verified, not assumed:

- **Two runtime dependencies** for a 32k-line static-analysis tool — the
  structural basis of the offline, reproducible and read-only-container
  guarantees, and defended deliberately even when upgrading would be easier.
- **Strict mypy clean across 60 files**, `ruff` clean, zero TODO/FIXME, no
  duplicated logic, and prose comments that were checked and found *accurate*
  rather than stale.
- **Documentation accuracy is measurable and measured.** Zero broken links
  across 45 files; README's benchmark percentages independently recomputed from
  committed raw results and matched **exactly** — rare in this product category.
- **Windows is a first-class platform**, verified live: file locking via
  `msvcrt`, Job Object memory caps, stdout re-encoding, BOM/CRLF/unicode handling
  all already correct.
- **No silent error swallowing.** Every broad `except` sampled is a commented,
  deliberate resilience boundary — the specific risk looked for in a retrieval
  tool, and not found.
- **The release pipeline's response to past incidents was substantive**:
  test-before-tag, re-checking required checks before tagging, signed release
  commits via GraphQL.

---

## Report index

| Report | Contents |
|---|---|
| `EXECUTIVE-SUMMARY.md` | This document |
| `REPOSITORY-OVERVIEW.md` | What the project is, architecture diagram, scale, branch topology |
| `FINDINGS-REGISTER.md` | All 50 findings, deduplicated, with the rejected one |
| `BUGS-AND-DEFECTS.md` | The working-tree regression, in full, with reproductions |
| `ARCHITECTURE-ASSESSMENT.md` | Layering, coupling, extensibility |
| `CODE-QUALITY-REPORT.md` | Maintainability, complexity, tooling baseline |
| `SECURITY-ASSESSMENT.md` | Trust boundaries, findings, claims verified |
| `DOCUMENTATION-AUDIT.md` | Doc-vs-implementation, links, claims |
| `GITHUB-ISSUE-ANALYSIS.md` | 272 issues, regression verdict, 16 recommended issues |
| `TESTING-AND-COVERAGE.md` | Suite composition, coverage, gaps |
| `CI-CD-ASSESSMENT.md` | Pipeline, gating, release engineering, REL-01 |
| `PERFORMANCE-ASSESSMENT.md` | Measured results separated from theory |
| `DEPENDENCY-AND-COMPATIBILITY.md` | Dependencies, licensing, platform support |
| `PRODUCTION-READINESS.md` | Twelve scored dimensions, conditions to advance |
| `RECOMMENDED-ACTIONS.md` | P0–P3 roadmap, and what *not* to do |
| `TEST-EXECUTION-LOG.md` | Every command run, with exit codes and blocked checks |
| `AGENT-FINDINGS/` | The 11 raw specialist reports |

---

## Assurance

- **No production code was modified.** The four pre-existing uncommitted
  modifications were left exactly as found; they were analysed, never corrected.
- **No test was modified.** The one test modification present was pre-existing
  and is reported as a finding.
- **No GitHub state was changed** — no issue, PR, label, release, setting or
  workflow run was created, closed or triggered.
- **No git history was altered.** Nothing was committed, pushed, stashed or
  checked out.
- All temporary artifacts live in the session scratchpad, outside the repository.
- The only files added to the repository are the reports in `audit-reports/`.
