# Testing and Coverage

Consolidated from `AGENT-FINDINGS/08-testing.md`, with the orchestrator's own
executed results and **one agent claim experimentally refuted**. Full command
transcripts are in `TEST-EXECUTION-LOG.md`.

## Executed results

| Command | Exit | Result |
|---|---|---|
| `python -m pytest -q -n auto -p no:randomly --durations=25` | 1 | **1 failed, 2763 passed, 18 skipped, 1 xfailed in 66.02s** |
| `python -m pytest tests/test_framework_edges.py::test_pytest_nested_classes_and_shadowed_fixtures -q` | 1 | 1 failed in 6.29s (deterministic, not a parallelism artefact) |
| `python -m ruff check .` | 0 | All checks passed |
| `python -m ruff format --check .` | 0 | 280 files already formatted |
| `python -m mypy repo2graph/` | 0 | Success, 60 source files |

The single failure is the working-tree revert of the issue #523 fix, caught by a
committed regression test. See `BUGS-AND-DEFECTS.md` BUG-02/BUG-03.

## Suite composition

~200 test files, ~2,782 tests. Categories present:

| Category | Representative files |
|---|---|
| Unit | the bulk of `tests/test_*.py` |
| Integration / end-to-end CLI | `test_repo2graph.py`, `test_incremental.py` |
| MCP protocol conformance | `test_mcp_conformance.py`, `test_mcp.py`, `test_mcp_sse.py` |
| Property-based (hypothesis) | `test_properties.py` |
| Golden-file | `tests/golden/*` via `test_compat.py`, `test_output_schema.py` |
| Fuzz | `test_fuzz_inputs.py` |
| Security | `test_secrets_hardening.py`, `test_security_negative.py`, `test_viz_safety.py`, `test_prompt_isolation.py` |
| Performance | `test_perf_bench.py` |
| Packaging & metadata | `test_packaging.py`, `test_version_surfaces.py`, `test_doc_consistency.py` |
| Determinism | `test_determinism.py` |
| Platform-specific | encoding/Windows paths, several |

The breadth is genuinely impressive — MCP protocol conformance against an
official client, prompt-isolation tests, determinism tests, and metadata-drift
tests are all things most projects of this size do not have.

## Coverage

| Fact | Value |
|---|---|
| Configured gate | `fail_under = 80` (**branch** coverage) |
| Where enforced | **One CI cell only** — Ubuntu / Python 3.13 |
| Cells running without `--cov` | All other 11, including **every Windows cell** |

Measured during this audit: a deliberately-labelled **6-file subset** at 41.31%,
reported purely as methodology evidence. **The real full-suite figure was not
re-derived and no project-wide coverage percentage is asserted here.** The
repository's own `.coverage` file was left untouched.

The single-cell enforcement is a real gap: Windows-specific branches — and this
codebase has many, around file locking (`msvcrt`), Job Objects for memory caps,
and stdout re-encoding — are never coverage-measured at all.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| TEST-01 | **HIGH** | Confirmed | `tests/test_store.py:93-115` | Concurrency test neutered by a lock inside the test; see BUG-04 |
| TEST-02 | **HIGH** | Confirmed | suite-wide | No on-disk index format backward-compatibility test |
| TEST-03 | MEDIUM | High | `tests/test_mcp_sse.py:131,170,221` | Fixed `asyncio.sleep(0.4)` as server-readiness wait — flakiness risk |
| TEST-04 | MEDIUM | Confirmed | `pyproject.toml` coverage config / `ci.yml` | Coverage gate enforced on 1 of 12 cells |
| TEST-05 | MEDIUM | High | suite-wide | Concurrency tests assert "didn't crash", not "results were correct" |
| TEST-06 | LOW | Medium | 43 sites | `assert x is not None` as sole/terminal assertion |
| TEST-07 | INFO | Confirmed | `tests/golden/cli_inventory.json` | Golden CLI inventory covers 9 of ~17 subcommands |

### TEST-01 — The neutered concurrency test, and a refuted explanation

The test-integrity defect itself is documented as BUG-04 in
`BUGS-AND-DEFECTS.md`. What belongs here is the methodological correction.

The testing specialist concluded that the original test *could never* have caught
this class of regression, reasoning that CPython's `sqlite3` serialises at the C
level and therefore a dropped per-query lock on a read-only connection has no
crash-level failure mode. On that basis it rated the suite structurally blind.

**That reasoning is wrong, and was refuted by direct measurement.** The committed
test body was replayed verbatim — 8 workers, one pass, no lock in the test —
against the current unlocked store, 40 trials:

```
TRIALS=40 FAILED_TRIALS=32
---- first exception ----
AssertionError
```

The original test detected the regression in **32 of 40 runs**. CI would have
gone red. Two corrections follow:

1. The test edit is not a neutral refactor of a test that never worked; it is
   what silenced a genuinely failing test.
2. The failure mode is an `AssertionError` on `rec["id"] == nid` — **wrong data
   returned**, not a SQLite exception. This is the experimental basis for rating
   BUG-01 CRITICAL.

The specialist's *conclusion* — that the suite's concurrency tests are weak — is
nonetheless correct, just for a different reason. See TEST-05.

### TEST-02 — No backward-compatibility test for the on-disk format

The highest-value genuine gap. No test opens a `.r2g` index built by an earlier
`repo2graph` version with current code. A schema change therefore breaks every
existing user's cache with **no CI signal at all** — and this project ships
frequent major versions (five majors in under two months), making format drift a
live risk rather than a theoretical one.

**Recommendation:** commit a small index fixture built by an older release and
assert the current code loads it, or explicitly assert-and-document that indexes
are version-scoped and auto-rebuild on mismatch. Effort: **M**.

### TEST-05 — Concurrency tests assert absence of crashes, not correctness

Across the suite, concurrency tests check that operations completed without
raising. None was found asserting that concurrent results were *correct*. Given
that BUG-01's real failure mode is wrong data, this is precisely the blind spot
that matters.

**Recommendation:** in concurrent tests, assert returned values equal a
known-correct baseline, not merely that no exception escaped. Effort: **S–M**.

### TEST-03 — `asyncio.sleep(0.4)` readiness waits

Three sites in `test_mcp_sse.py` wait a fixed 0.4s for the server to come up. On
a loaded CI runner this is a flake waiting to happen. It has not been observed
failing (run history shows no flakiness), so it is rated on risk, not incidence.

**Recommendation:** poll for readiness with a timeout instead. Effort: **S**.

## Skips and xfail

All 18 skips and the 1 xfail were reviewed and are **legitimate** — platform
capability gaps (Windows symlinks, non-UTF-8 filenames, POSIX-only paths) or
genuinely optional extras (`model2vec`, a real-embedder smoke test). None masks a
fixable failure. This is worth stating: skipped tests are a common hiding place
and these are clean.

## Other scenario gaps

Identified but lower priority than TEST-02:

- Interrupted operations — SIGINT mid-build leaving a partial index.
- Corrupt or truncated index files (the `except sqlite3.DatabaseError` paths in
  `store.py` are designed for this but not all are exercised).
- Concurrent *builds* against one index directory.
- Disk-full and permission-denied during export.
- Network failure handling in `fetch.py` and `rag --answer`.

## Positive patterns

- **Golden-file regeneration is opt-in** via `R2G_REGEN_GOLDEN=1`, and
  `test_compat.py` layers key-set assertions on top of byte comparison. This is
  the correct design — it prevents the classic failure where goldens are blindly
  regenerated and rubber-stamp a regression.
- Fixtures use `tmp_path` discipline consistently; no order-dependence or shared
  mutable state was found.
- `-n auto` parallelism is safe because nothing shares a database, port or
  working directory — verified by the suite completing identically in parallel
  and the one failure reproducing serially.

## Recommended improvements, prioritised

1. Revert the `tests/test_store.py` edit, then strengthen the test (more
   iterations, assert correctness not absence-of-exception). — **TEST-01/TEST-05**
2. Add an on-disk format backward-compatibility test. — **TEST-02**
3. Extend the coverage gate beyond one cell, at minimum to one Windows cell. — **TEST-04**
4. Replace fixed-sleep readiness waits with polling. — **TEST-03**
5. Complete `cli_inventory.json` so CLI drift is actually guarded. — **TEST-07**

## Limitations

- Full-suite coverage was not re-measured; only a labelled 6-file subset was,
  and no project-wide percentage is claimed.
- The suite was run once in parallel and the failing test once serially.
  Flakiness findings are risk-based, not from repeated-run statistics.
- Tests were never modified. The one modification present in `tests/test_store.py`
  was pre-existing and is reported, not corrected.
