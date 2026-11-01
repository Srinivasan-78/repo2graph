# Recommended Actions

Prioritised remediation roadmap. Effort is **S** (< 1 hour), **M** (hours to a
day), **L** (multi-day or longer). Every item links to its finding.

---

## P0 — Immediate

### P0-1 · Restore the reverted hunks from `516fe12d`

**Findings:** BUG-01 (CRITICAL), BUG-02, BUG-03, BUG-04, BUG-05
**Effort:** S · **Impact:** Very high · **Dependencies:** none

Do this before committing, branching, or running anything else against the
working tree. Four files are affected:

| File | Restore |
|---|---|
| `repo2graph/store.py` | `import threading`, `self._lock = threading.Lock()`, the `with self._lock:` guard on every method touching `self._db`, and `__enter__`/`__exit__` on the `GraphStore` Protocol |
| `repo2graph/frameworks.py` | The `cls_qualname` construction in `extract_pytest_facts` |
| `repo2graph/graph.py` | The `base_visible` fallback in `add_pytest_fixture_edges` |
| `tests/test_store.py` | Remove the `threading.Lock()` added inside the test |

Verify with `python -m pytest tests/test_framework_edges.py tests/test_store.py -q`
— `test_pytest_nested_classes_and_shadowed_fixtures` must pass.

**Why P0:** the reverted fixes are released code (in v5.0.0), the regression
produces **silently wrong data** rather than an error, and the test that would
catch it has been disabled. Measured: 15/16 threads fail without the lock; the
original test body detects it in 32/40 trials.

**Do not** take the shortcut of keeping the lock in the test. That is the current
state, and it is the defect.

---

## P1 — High priority

### P1-1 · Make the default branch reflect what ships

**Finding:** REL-01 (HIGH) · **Effort:** S mechanically, plus a process decision
**Impact:** Very high — affects every visitor, cloner and PyPI link

`main` is at 2.2.0 and 240 commits behind; v5.0.0's release commit was never
merged back to `develop`. Pick one model and implement it:

- **Option A** — keep `main` as default: merge the v5.0.0 release commit back into
  `develop`, fast-forward `main` to the released state, and add a `main`-sync step
  to `publish.yml` so this cannot recur.
- **Option B** — make `develop` the default branch and treat `main` as a release
  pointer only, documenting that explicitly.

Either works. The current state is neither, which is why `develop`'s CHANGELOG
still files shipped fixes under `[Unreleased]` and its version reads 4.1.0 after
a 5.0.0 release.

### P1-2 · Close the merge-gate gap

**Finding:** CI-01 (HIGH) · **Effort:** S (settings only) · **Depends on:** nothing

In ruleset 22778968:
1. Add the three `Test Suite (*, Python 3.14)` contexts to required checks.
2. Add `Container Image Build & Read-Only Smoke Test`.
3. Change `bypass_actors` from `bypass_mode: "always"` to pull-request-scoped, or
   remove the admin bypass.

Item 3 is the one that matters most: the permanent bypass is the mechanism by
which a red release PR was previously merged and 3.0.0 tagged on failing tests.

### P1-3 · Make concurrency tests capable of failing

**Findings:** TEST-05, and the root cause behind BUG-01 going undetected
**Effort:** S–M · **Depends on:** P0-1

Across the suite, concurrency tests assert that nothing raised. Change them to
assert that concurrent results **equal a known-correct baseline**. For
`test_sqlite_store_concurrent_thread_queries` specifically, also raise iterations
per worker — 8 workers × 1 pass detected the regression only 80% of the time,
which is too unreliable to gate on.

This is the finding that prevents the next recurrence. The SQLite store has had
**five issues in six weeks**, all concurrency or lifecycle; strengthening the test
addresses the cause rather than the sixth symptom.

### P1-4 · Fix `doctor`'s version check and its stale matrix

**Finding:** COMPAT-01 (MEDIUM, but trivially cheap and highly visible)
**Effort:** S

Two defects in `repo2graph/doctor.py:89-96`:
1. `vi <= (3, 13)` compares a 5-tuple to a 2-tuple. Use `vi[:2]`.
2. The matrix says "tested <= 3.13" and warns about "3.14+" — but CI tests 3.14
   and `pyproject.toml` advertises it. Update to 3.11–3.14.

Add a test asserting `doctor` reports `ok` on the running interpreter. Promoted to
P1 above its severity because `doctor` is the onboarding trust tool and it
currently misfires for the large majority of users.

---

## P2 — Important improvements

| ID | Action | Finding | Effort | Depends on |
|---|---|---|---|---|
| P2-1 | Correct `SECURITY.md`'s CI matrix (3.11–3.14) and make its network-exception list the single source of truth, with README linking to it | DOC-01, DOC-02 | S | — |
| P2-2 | Add `npm` (both manifests) and `docker` ecosystems to `.github/dependabot.yml` | DEP-02 | S | — |
| P2-3 | Add an on-disk `.r2g` format backward-compatibility test, or assert-and-document that indexes are version-scoped and auto-rebuild | TEST-02 | M | — |
| P2-4 | Make npm publish a hard failure without `NPM_TOKEN`, **or** add `npm` to the `release` job's `needs` | CI-02 | S | — |
| P2-5 | Add a complexity lint gate (ruff `C901`) with a generous threshold and a per-file ignore list for current offenders | CQ-06 | S | — |
| P2-6 | Document `REPO2GRAPH_CACHE_DIR`, `REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL` — the Ollama one is a security bypass currently discoverable only by tripping it | CFG-01 | S | — |
| P2-7 | Extend the coverage gate beyond one CI cell, at minimum to one Windows cell | TEST-04 | S | — |
| P2-8 | Add `bandit` + `pip-audit` to CI as required checks; make `dependency-audit`/`dependency-review` required | CI-05, SECURITY limitations | S | — |
| P2-9 | Record the `tree-sitter-language-pack` 0.x cap in `SECURITY.md` as accepted, tracked, unpatchable risk | DEP-01 | S | — |
| P2-10 | `chmod 0600` index artifacts when `--secret-policy` disables redaction | SEC-01 | S | — |
| P2-11 | Replace fixed `asyncio.sleep(0.4)` readiness waits with polling | TEST-03 | S | — |
| P2-12 | Extract the build logic from `mcp/indexes.py` into a neutral core module so `tasks.py` stops importing through the MCP layer | ARCH-01 | S | — |
| P2-13 | Either implement real build attestation in `publish.yml`, or rename `provenance.yml` to describe what it does | CI-03 | S rename / M implement | — |
| P2-14 | Fix README's "3.x CLI" semver line and `docs/mcp.md`'s tool count; complete `cli_inventory.json` | DOC-03, DOC-04, DOC-05 | S | — |
| P2-15 | Remove dead `tomli` fallbacks and stale "supports 3.10" docstrings | COMPAT-02 | S | — |

Thirteen of the fifteen P2 items are **S**. A single focused session would clear
most of this list.

---

## P3 — Long-term

| ID | Action | Finding | Effort | Note |
|---|---|---|---|---|
| P3-1 | Read each file once per build and pass bytes through the pipeline | PERF-01 | M | Highest-value perf work; measured at ~3 reads/file, 15,043 `open()` calls for 3,000 files |
| P3-2 | Decompose `graph.build()` (792 lines, ~155 branches, 6 state-capturing closures) into independently testable resolution functions | CQ-01 | L | **The highest-value refactor in the codebase.** Do it incrementally with the existing build-level tests as a net. Would have localised BUG-02/BUG-03 instantly |
| P3-3 | Separate `pack_context()` into candidate selection, budget accounting and rendering so budget arithmetic can be property-tested | CQ-02 | M–L | Sits on the citation-correctness path |
| P3-4 | Introduce a `LanguageAdapter` protocol so adding a language is one registration rather than edits across 6 modules | ARCH-05 | L | Main growth axis; schedule deliberately |
| P3-5 | Extract `viz.py`'s inline HTML/CSS/JS template to an asset file loaded via `importlib.resources` | CQ-04 | M | Makes 870 lines lintable and reviewable |
| P3-6 | Split `cli.py:main()`'s argparse construction into per-subcommand builders; stop `cli_inspect`/`cli_embed` importing private helpers | ARCH-03, ARCH-04, CQ-05 | M | Navigability |
| P3-7 | Make the version lookup in `repo2graph/__init__.py` lazy | PERF-02 | S | Recovers ~40ms of ~120ms cold start; matters for MCP and the editor extension |
| P3-8 | Warn (or require opt-in) when a sqlite-backed `Index` fully materialises via `.nodes`/`.edges`/`.adj` | PERF-06 | S | Latent trap, not currently reachable from the CLI |
| P3-9 | Pin the IP after SSRF validation in `answer.py` to close the DNS-rebinding TOCTOU | SEC-05 | M | Maintainer has already scoped this |
| P3-10 | Add rate limiting and a documented minimum token entropy for SSE auth; session-scope build-task ids | SEC-02, SEC-03 | S–M | Only matters for multi-tenant SSE |
| P3-11 | Add an explicit test pinning the JSON-as-Cypher escaping contract | SEC-04 | S | Guards an implicit assumption |
| P3-12 | Digest-pin the Docker base image; clarify or pin the npm launcher's version semantics | DEP-04, DEP-03 | S | |
| P3-13 | Consider `threading.local()` connections in `SqliteGraphStore` instead of one shared locked connection | PERF-04 note | M | **Only after P0-1.** Removes contention without reintroducing the race. Not required — measured lock cost is ~5% |
| P3-14 | Scope vendoring tree-sitter grammars as the eventual exit from the unpatchable 0.x pin | DEP-01 | L | Multi-week; the cap stays until then |
| P3-15 | Add a stated API/CLI/index-format stability policy and slow the major cadence | release churn, DOC-03 | Process | Five majors in under two months is unplannable for consumers |
| P3-16 | Measure before acting on PERF-05 and PERF-07 | PERF-05, PERF-07 | S to measure | Inspection-only; do not optimise unmeasured |

---

## Suggested sequencing

```
Now        P0-1  restore the hunks            ─── blocks everything else
           │
Week 1     P1-1  default branch               ─┐
           P1-2  merge gate                    │ independent, do in parallel
           P1-4  doctor                        │
           P1-3  concurrency tests  ───────────┘ (needs P0-1)
           │
Week 2     P2-1 … P2-15   mostly S; one focused session clears the bulk
           │
Backlog    P3-1, P3-7, P3-8   cheap, measurable wins
           P3-2, P3-3         the real refactors — schedule, don't improvise
           P3-4, P3-14        strategic, multi-week
```

## Explicitly not recommended

| Tempting action | Why not |
|---|---|
| Raising the `tree-sitter-language-pack<1.0` cap | It is a **security boundary**, not version lag. 1.x downloads ~21MB of unsigned native code on first parse, breaking the no-network guarantee, the lockfile/SBOM's meaning, and read-only-container support. 1.x offers no offline switch. Verified; the rationale holds. Close Dependabot PRs that raise it. |
| Keeping the lock inside `tests/test_store.py` "since the test passes" | That is the defect. The production code is what needs the lock. |
| Removing the lock for performance | Measured: ~141ns against ~2,698ns per query, roughly 5%. There is no performance case. |
| Treating `perf_bench.py --check` failures on a Windows dev machine as regressions | Measured ~15× the Linux baseline, root-caused to `open()` latency, not code. Compare within-run only. |
| Reformatting the tree during remediation | A prior incident in this repository had a whole-tree `ruff format` silently revert a fix while all checks stayed green. `ruff format --check` is already clean on all 280 files. |
| Bulk-regenerating golden files to clear failures | Regeneration is opt-in via `R2G_REGEN_GOLDEN=1` by design. A failing golden is a signal, not noise. |
| Hand-editing the version in `pyproject.toml` | The version is derived at publish time from commit headers and the CHANGELOG. Hand-bumping fights the pipeline. |
