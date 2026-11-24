# Remediation Prompt — repo2graph health 7.3 → production-ready

Paste the block below into a fresh Claude Code session at `E:\Github\repo2graph`.
It is written to be run as-is. Everything it needs is in `audit-reports/`.

---

## PROMPT

You are remediating a 50-finding audit of this repository. The full audit lives in
`audit-reports/` — **read `EXECUTIVE-SUMMARY.md`, `RECOMMENDED-ACTIONS.md`, and
`FINDINGS-REGISTER.md` before touching anything**, and read the specific report for
each finding as you reach it (`BUGS-AND-DEFECTS.md` for BUG-*, `CI-CD-ASSESSMENT.md`
for CI-* and REL-01, and so on). Do not re-derive the findings; they were confirmed
by execution, not inspection. Your job is to fix them.

Baseline: overall health **7.3/10**, twelve dimensions scored 6–9, security strongest
(9), CI/CD configuration weakest (6). 1 CRITICAL, 7 HIGH, 17 MEDIUM, 22 LOW, 3 INFO.
Seven of the eight conditions for production-ready are S-effort (< 1 hour each). The
target is to clear P0 and all four P1s, then sweep P2.

### Hard constraints — violating any of these is worse than leaving the work undone

1. **Do not run `ruff format` over the tree.** A prior incident here had a whole-tree
   format silently revert a fix while every check stayed green. `ruff format --check`
   is already clean on all 280 files. Format only files you yourself edited, and only
   by path.
2. **Do not raise the `tree-sitter-language-pack<1.0` cap.** It is a security
   boundary, not version lag: 1.x fetches ~21MB of unsigned native code on first
   parse, which breaks the no-network guarantee, the lockfile/SBOM's meaning, and
   read-only-container support. Close any Dependabot PR that raises it.
3. **Do not hand-edit the version in `pyproject.toml` or the `[Unreleased]` CHANGELOG
   heading into a release heading.** The version is derived at publish time from
   commit headers and the CHANGELOG. Hand-bumping fights `publish.yml`.
4. **Do not add AI/Claude attribution** — no footers, no `Co-Authored-By` trailers —
   to any commit, PR, issue, or doc.
5. **Do not run subagents that write to the working tree in parallel.** Subagents
   share this tree; concurrent writes have silently clobbered each other here. Fan out
   read-only work only; serialize all edits through yourself.
6. **Do not bulk-regenerate golden files.** Regeneration is opt-in via
   `R2G_REGEN_GOLDEN=1` by design. A failing golden is a signal.
7. **Do not treat `perf_bench.py --check` failures on this Windows host as
   regressions.** Measured ~15× the Linux baseline, root-caused to `open()` latency.
   Compare within-run only. If you do run a benchmark, commit the artifact before the
   next run — provenance is sampled at the end, so editing tracked files mid-run
   stamps `dirty: true`.
8. **Do not toggle the Dependabot alert setting** to force a rescan. The off/on cycle
   preserves stale alerts and silently disables automated-security-fixes.
9. **Do not change GitHub state** (issues, PRs, rulesets, releases, workflow
   dispatches) until Phase 5, and then only the items listed there, reporting each
   change as you make it.

### Phase 0 — Triage the dirty working tree (decision gate; do this first)

Four files are modified and uncommitted: `repo2graph/store.py`,
`repo2graph/frameworks.py`, `repo2graph/graph.py`, `tests/test_store.py`. Per
`BUGS-AND-DEFECTS.md` these partially revert commit `516fe12d`, which fixed #523,
#524 and #525 and shipped in **v5.0.0**.

Before restoring anything:

- `git diff` all four files and `git show 516fe12d` so you can state precisely what
  the working tree removes.
- Check whether the diff contains any *new* work that is not simply a revert — a
  genuine refactor someone started on top of the released state. `develop` here has
  carried parallel rounds of the same work before, and merged mechanisms can fight.
- Save the current diff to the session scratchpad (`git diff > <scratchpad>/pre-remediation.patch`)
  so nothing is lost. Do not `git stash` — that modifies the tree the audit describes.

**Then stop and report to me**: what the diff reverts, whether it contains salvageable
new work, and your proposed restore plan. Wait for my go-ahead before Phase 1. If the
diff turns out to be deliberate in-progress work, the plan changes and that is my call,
not yours.

### Phase 1 — P0-1: restore the reverted hunks (blocks everything else)

Restore, per `RECOMMENDED-ACTIONS.md` P0-1:

| File | Restore |
|---|---|
| `repo2graph/store.py` | `import threading`, `self._lock = threading.Lock()`, the `with self._lock:` guard on every method touching `self._db`, and `__enter__`/`__exit__` on the `GraphStore` Protocol |
| `repo2graph/frameworks.py` | the `cls_qualname` construction in `extract_pytest_facts` |
| `repo2graph/graph.py` | the `base_visible` fallback in `add_pytest_fixture_edges` |
| `tests/test_store.py` | remove the `threading.Lock()` added **inside** the test |

The lock belongs in the production store, not in the test. Keeping it in the test is
the defect: it serialises the eight workers meant to run concurrently, so the test
passes whether the store is thread-safe or not. Measured — 15/16 threads fail without
the production lock, 0 with it; the committed test body detects the regression in
32/40 trials. The failure mode is **silently wrong data** (`fetchone()` returning
`None` where a row exists, wrong node identity returned), not a clean error.

Removing the lock for performance is also not on the table: measured ~141ns against
~2,698ns per query, roughly 5%.

Verify before moving on:

```
python -m pytest tests/test_framework_edges.py tests/test_store.py -q
python -m pytest -q -n auto
```

`test_pytest_nested_classes_and_shadowed_fixtures` must pass, and the full suite must
reach **0 failed** (baseline was 1 failed, 2763 passed, 18 skipped, 1 xfailed, ~66s).
Then `ruff check .` and `mypy repo2graph/` (strict) must both stay clean.

Commit this on its own, as a `fix:` commit referencing #523/#524/#525, before anything
else lands.

### Phase 2 — the four P1s

Do P1-1, P1-2 and P1-4 in any order; P1-3 depends on Phase 1.

**P1-3 · Make concurrency tests capable of failing** (findings TEST-05, root cause of
BUG-01 going undetected). Across the suite, concurrency tests assert only that nothing
raised. Change them to assert concurrent results **equal a known-correct baseline**.
For `test_sqlite_store_concurrent_thread_queries`, also raise iterations per worker —
8 workers × 1 pass detected the regression only 80% of the time, too flaky to gate on.
Prove the strengthened test fails against the unlocked store (patch the lock out in a
scratchpad copy, never in the tree) and passes with it. **This is the item that
prevents recurrence** — the SQLite store has had five issues in six weeks, all
concurrency or lifecycle, now a sixth.

**P1-1 · Make the default branch reflect what ships** (REL-01). `main` is the default
at 2.2.0 (2026-09-26), 240 commits behind `develop` (4.1.0), while v5.0.0 is published
and its release commit was never merged back. Three versions are live; every visitor,
clone, rendered README and PyPI doc link resolves to 2.2.0. Present both options and
your recommendation, then implement the one I pick:

- **A** — keep `main` default: merge the v5.0.0 release commit back into `develop`,
  fast-forward `main` to the released state, and add a `main`-sync step to
  `publish.yml` so it cannot recur.
- **B** — make `develop` default, treat `main` as a release pointer only, documented.

Note the local constraints: ruleset 23758757 protects `develop` from deletion, so
promotion PRs using `develop` as the head branch will refuse post-merge branch
deletion — that is intended, not a failure. Solo-account merge gates (including
required signatures) mean API-created commits are the workaround for release commits.

**P1-2 · Close the merge-gate gap** (CI-01, settings only, in ruleset 22778968):
add the three `Test Suite (*, Python 3.14)` contexts and
`Container Image Build & Read-Only Smoke Test` to required checks; and change
`bypass_actors` from `bypass_mode: "always"` to pull-request-scoped, or remove the
admin bypass. **Item 3 matters most** — the permanent bypass is the exact mechanism
by which a red release PR was merged and 3.0.0 tagged on failing tests.

**P1-4 · Fix `doctor`** (COMPAT-01) at `repo2graph/doctor.py:89-96`: `vi <= (3, 13)`
compares a 5-tuple to a 2-tuple — use `vi[:2]`; and the matrix says "tested <= 3.13"
while CI tests 3.14 and `pyproject.toml` advertises it — update to 3.11–3.14. Add a
test asserting `doctor` reports `ok` on the running interpreter. Cheap and highly
visible: `doctor` is the onboarding trust tool and currently misfires for most users.

### Phase 3 — P2 sweep

Work the P2 table in `RECOMMENDED-ACTIONS.md` (P2-1 … P2-15). Thirteen of fifteen are
S-effort; one focused pass should clear most of it. Group them into a small number of
coherent commits by theme (docs accuracy, CI gates, Dependabot/supply chain, tests,
the `mcp/indexes.py` extraction) rather than fifteen micro-commits or one giant one.

Two to handle with care:

- **P2-8** (add `bandit` + `pip-audit` to CI as required checks) — the audit's security
  review was manual because these and `zizmor` are absent from both this environment
  and CI. Add them as CI checks; do not install them globally here to "verify" first.
- **P2-9** — record the `tree-sitter-language-pack` 0.x cap in `SECURITY.md` as an
  accepted, tracked, currently unpatchable risk. Constraint 2 still applies.

Known CI-only failure modes to expect rather than debug from scratch: `FORCE_COLOR`
plus Python 3.14 argparse colour output, the 3.10 `tomllib` floor, sibling-directory
writes failing in the read-only container, and the Windows memory cap interacting with
wall-clock limits.

### Phase 4 — Do not start the P3 backlog

P3 items (notably P3-2, decomposing `graph.build()` — 792 lines, ~155 branches, the
highest-value refactor in the codebase) are explicitly **out of scope** for this
session. List them in your final report as a backlog with my recommended sequencing.
Do not improvise a large refactor at the end of a remediation pass.

### Phase 5 — Verification, GitHub state, and exit

1. Full gate: `python -m pytest -q -n auto`, `ruff check .`,
   `ruff format --check .`, `mypy repo2graph/` (strict). All must be clean, with
   **0 failed** tests. Paste the actual output — do not summarise a run you did not do.
2. Re-run `git diff` against `516fe12d` to confirm no reverted hunk crept back in.
3. Open PRs per the repository's normal flow. If you are on `develop`, branch first.
4. File the 16 recommended issues in `GITHUB-ISSUE-ANALYSIS.md` — but **show me the
   list of titles first** and let me approve it before creating any.
5. Update `CHANGELOG.md` under `[Unreleased]` only (constraint 3).

### Final report

Report, in this order: what you changed and where; what each gate actually output;
which findings are now closed by ID; which you left open and why; every GitHub state
change you made; and the P3 backlog with sequencing. If any phase was blocked, finish
every other phase in full and say explicitly what you left out — scaling the work down
is my call, not yours.

Report outcomes faithfully. If a test fails, show the output. The audit's own standard
was that nothing was claimed that was not executed; hold your remediation to it.

---

## Notes on using this prompt

- Phase 0 ends in a deliberate stop. If you want an unattended run, delete that stop
  and state up front whether the dirty tree is a revert to discard or WIP to keep.
- The P1-1 branch decision is the one real judgment call. Deciding it before you start
  saves a round trip.
- Effort: P0 + P1 is one focused session. P2 is a second. P3 is scheduled work, not a
  session.
