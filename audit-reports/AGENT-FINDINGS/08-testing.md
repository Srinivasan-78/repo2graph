# Agent 8 — Testing Strategy, Coverage & Test Quality

## Summary

The suite is large (98 test files, 2,783 collected tests), disciplined about
determinism (Hypothesis pinned and derandomized, golden-file regeneration
gated behind an explicit env var, `fail_under = 80` branch coverage enforced
on one CI cell), and visibly invested in hostile-input and security testing.
The one failing test on the working tree (`test_pytest_nested_classes_and_shadowed_fixtures`)
is a real regression in `frameworks.py`/`graph.py`'s nested-class fixture
resolution, correctly caught by the suite.

The standout finding is in the uncommitted `tests/test_store.py` edit: the
concurrent SQLite query test was changed to wrap its own assertions in a
`threading.Lock()`, which serialises the 50 "concurrent" calls into one at a
time on the same thread-safety axis the production code dropped the lock on
in `store.py`. The test now provides **no evidence** about the regression it
exists to catch (see dedicated section below). This is a test-integrity
defect, not a bug in the product — the suite's green run on this file is not
proof of the removed lock being safe.

A second class of finding: most of the suite's "concurrency" tests are
genuinely concurrent (real `ThreadPoolExecutor`/`asyncio` with real I/O), but
none of them assert anything beyond "didn't crash / returned a well-typed
value" — none detect a *data race* (e.g., two threads seeing a torn read, a
corrupted cursor, or an interleaved write) unless the race manifests as a
hard crash or type error. That is why the #523 revert (loss of the `BuildLock`
file lock around first-materialization) was caught — it manifests as
`sqlite3.OperationalError` on a mid-write database, a hard failure — while the
#524 regression (loss of the per-connection Python lock around read
statements) was not: SQLite's own GIL-serialized C extension already makes a
single unlocked `sqlite3.Connection` safe against torn reads for the kind of
query pattern these tests issue, so removing the lock changes nothing
observable in the *test's own* execution model regardless of the test edit.
Even restoring the original (unmodified) test would very likely still pass
with the lock removed, because `check_same_thread=False` plus SQLite's
internal mutex (default `threading` mode) already serializes access at the
C level for this workload — the Python lock in `store.py` was redundant for
correctness under the access pattern these tests exercise, not load-bearing.
This matters for the audit because it means the *test* can't actually tell
us whether the removed lock mattered for some other access pattern (e.g. one
that touches two statement objects on the same connection across threads
without GIL yielding at the right point, or a future WAL/write path) — the
suite has no test that would fail either way.

## Suite composition

| Category | Representative files | Approx. file count |
|---|---|---|
| Unit (parsing/graph/query internals) | test_cpp_parse, test_edgemeta, test_graph_streaming, test_tokenizers, test_resolution_heuristics, test_symbol_identity, test_class_field_functions | ~25 |
| Integration (CLI build→query→export pipelines) | test_repo2graph, test_incremental, test_watch, test_cache, test_foreign_index, test_federated | ~15 |
| End-to-end CLI | test_doctor, test_bugreport, test_demo, test_audit_cli, test_summary_script | ~8 |
| MCP protocol conformance | test_mcp, test_mcp_compat, test_mcp_conformance, test_mcp_editor, test_mcp_pagination, test_mcp_resources, test_mcp_sse, test_mcp_tools | 8 |
| Property-based (Hypothesis) | test_properties.py | 1 |
| Golden-file / baseline | test_compat.py (+ tests/golden/*) | 1 (large) |
| Fuzz | test_fuzz_inputs.py | 1 |
| Security | test_secrets_hardening, test_security_negative, test_viz_safety, test_secret_coverage_gaps, test_secret_scan_speedups, test_eval_hardening, test_export_hardening, test_prompt_isolation | 8 |
| Performance (tolerance logic, not wall-clock gating) | test_perf_bench.py, test_bench_tables_match_artifacts.py | 2 |
| Packaging & metadata | test_packaging, test_version_surfaces, test_doc_consistency, test_changelog | 4 |
| Platform-specific (explicit skipif) | test_parse_budget (darwin/win32 branches), test_integrity (symlink tests), test_encoding, test_prompt_isolation, test_determinism | spread across ~6 |
| Determinism | test_determinism.py | 1 |
| Store/concurrency | test_store.py, parts of test_repo2graph, test_scoped_resolution, test_tasks | 4 |
| Everything else (frameworks, query, export, graphdiff, scip, rag, viz, …) | remainder | ~25 |

Total test files: **98** (`find tests -maxdepth 1 -name 'test_*.py' | wc -l`).
Total collected test items: **2,783** (`pytest --collect-only -q`, matches the
orchestrator's full-run count).

## Coverage

**Configuration** (`pyproject.toml`):
- `[tool.coverage.run]`: `branch = true`, `concurrency = ["multiprocessing","thread"]`, `parallel = true`, `sigterm = true`.
- `[tool.coverage.report]`: `fail_under = 80` (branch coverage), with a code comment stating it is measured at 86% across the 9-cell CI matrix and is "a floor, not a target."
- CI (`.github/workflows/ci.yml` line ~225) only runs `pytest -q -n auto --cov=repo2graph --cov-report=term-missing` on **one** matrix cell (Ubuntu, Python 3.13); every other cell (including all Windows cells, line ~236) runs plain `pytest -q -n auto` with no coverage collection or enforcement. So the 80% branch-coverage gate is real but single-cell — a regression that is covered on Linux-only code paths but not reached on that one cell would not be caught by the coverage gate at all.

**Measured (this agent, subset only — NOT the full-suite number)**:
```
python -m pytest -q -p no:cacheprovider \
  tests/test_store.py tests/test_graph_streaming.py tests/test_framework_edges.py \
  tests/test_tests_edges.py tests/test_query_bounds.py tests/test_query_direction.py \
  --cov=repo2graph --cov-report=json:<scratchpad>/cov_subset.json --cov-report=term-missing
```
Result: **41.31% total** (expected and unremarkable — this subset deliberately excludes tests/test_repo2graph.py, test_rag.py, test_export*.py etc. that exercise most of cli.py/export.py/answer.py). This number is **not** representative of the real suite; it is cited only to demonstrate the measurement method and to surface per-module numbers for files this subset does touch directly:
- `repo2graph/store.py`: 72% stmt / branch-partial 7 missed branches — missing lines 79, 87, 212-215, 297-298, 303-334 (a few chunk-table error paths), 379-384, 405-438 (the from_index builder path), 523, 564-587.
- `repo2graph/graph.py`: 65% — the fixture-edge logic under test (lines ~1296-1375) is exercised but large unrelated regions (export/streaming helpers) are not, consistent with running only a 6-file subset.
- `repo2graph/frameworks.py`: 81% — the `is_test`/`enclosing_class` logic at lines 453-541 (the regressed code) is covered by line but the *nested-class* branch is covered by a test that currently fails on assertion, meaning the line executes but the behavioral assertion is wrong (coverage counts the line as "hit" regardless of correctness — a reminder that %-coverage never substitutes for assertion strength).

Modules that read as structurally undercovered **in isolation** in this subset (0%) — `answer.py`, `demo.py`, `doctor.py`, `federated.py`, `limits.py`, `neo4j.py`, `schema.py`, `status.py`, `tasks.py`, `watch.py`, `explain.py` — are each covered by their own dedicated test files (test_demo.py, test_doctor.py, etc.) which simply were not included in this 6-file subset; this is an artifact of subset selection, not a real gap. A true per-module gap measurement would require the full `--cov` run, which this agent was instructed not to re-run.

## Test-integrity finding: the neutered concurrency test

**Location**: `tests/test_store.py:93-115` (test), `repo2graph/store.py` (lock removed throughout, e.g. constructor at line 143-150 no longer sets `self._lock`, and every query method — `node` line 258, `has`, `neighbours`, `iter_nodes`, etc. — no longer wraps its `self._db.execute(...)` call in `with self._lock:`).

**The diff**:
```python
# tests/test_store.py (working tree, uncommitted)
def query_task(i: int) -> bool:
    with lock:                      # <-- lock ADDED to the test
        rec = disk.store.node(nid)
        ...
```
```python
# repo2graph/store.py (working tree, uncommitted)
-        with self._lock:
-            row = self._db.execute(...).fetchone()
+        row = self._db.execute(...).fetchone()
```

**Why this defeats the test's purpose.** The test exists to prove that eight
threads calling `SqliteGraphStore.node()`/`neighbours()`/`has()`
*concurrently* on a shared connection do not corrupt results or crash. A
`threading.Lock()` acquired inside the worker function, around the exact
block that calls into the store, makes the eight "concurrent" workers run
**one at a time** — the `ThreadPoolExecutor(max_workers=8)` still schedules
eight OS threads, but each of them blocks on `lock.acquire()` until the
previous one finishes its three store calls and releases. The net effect is
a single logical thread executing fifty sequential calls, fanned out over
eight physical threads that never overlap inside the critical section. The
test's own instrumentation makes it structurally impossible to exercise the
one thing it is named for (`test_sqlite_store_concurrent_thread_queries`):
two threads touching `self._db` at the same instant. Whatever the
production code does or does not do to serialize access to `self._db` is now
unreachable by this test — the test passes identically whether `store.py`
keeps its lock, drops it, or replaces `self._db` with a non-thread-safe mock
object that raises on concurrent entry, because concurrent entry never
happens.

**Why the suite caught #523's revert but not #524's.** The #523 regression
(dropped `BuildLock` around first-time SQLite materialization —
`test_concurrent_store_materialization`, `tests/test_store.py:300-319`) is
still unlocked in the test and genuinely races 16 threads against
`open_graph_store()`, which performs a real multi-statement write
(`CREATE TABLE` + bulk inserts) the first time the cache file does not exist.
Without the file lock, two threads can both observe "no sqlite file yet" and
both start writing, producing a `sqlite3.OperationalError: database is
locked` / malformed-file crash that a bare `assert f.result() is True` will
catch as an exception. That failure mode is a hard crash, independent of
subtle timing — it will fire reliably even under a lenient interleaving.
The #524 regression (dropped per-call lock around *read-only* queries on an
already-open, read-only (`mode=ro`) connection) has no equivalent hard-crash
failure mode in Python's `sqlite3` module: the module's default
`threading`-mode build serializes access to the C-level connection object
with its own mutex regardless of the missing Python-level lock, so
concurrent read-only `SELECT`s on a `mode=ro` connection do not corrupt
results or raise in CPython's `sqlite3` under GIL — removing the Python lock
is redundant-but-safe for this exact access pattern, which is presumably why
nobody noticed in production either. **The test, even in its original
unlocked form, had a real chance of never catching a regression here, lock
or no lock** — it was already relying on `sqlite3`'s internal serialization,
not on `self._lock`, to stay green. The edit that added `threading.Lock()`
simply makes that reliance total and undeniable instead of partial.

**What this says about the suite's concurrency testing generally.** Grepping
all concurrency-flavoured tests (`ThreadPoolExecutor`, `asyncio.gather`,
`ProcessPoolExecutor`) shows the same pattern repeated: `tests/test_repo2graph.py:1561-1573`
and `tests/test_scoped_resolution.py:777-810` both monkeypatch
`ProcessPoolExecutor` with a recording/failing stub rather than running real
parallel work — they test *that the pool is invoked with the right
arguments*, not concurrency correctness. `tests/test_mcp_sse.py:220-231` is
the one test that asserts something race-shaped (`asyncio.gather` of two
concurrent client calls against one server), but it relies on a fixed
`asyncio.sleep(0.4)` for server readiness rather than a readiness probe
(flakiness risk, see below), and its assertion is on individual response
correctness, not on cross-request interleaving. None of the suite's
"concurrent" tests assert on a property that would only be true under a
genuine race (e.g., a monotonic sequence counter, a write-write conflict
detector, or `sqlite3.OperationalError` suppression under WAL). The pattern
across the whole suite is "spin up N threads/processes, assert each
individual result is well-formed" rather than "assert the *combination* of
concurrent effects is correct" — which is adequate for catching crash-level
races but structurally blind to data races that resolve to merely-wrong (not
crashing) output.

## Test quality findings

- **TEST-01** (Critical, Confidence: High) — `tests/test_store.py:93-115` /
  `repo2graph/store.py` (lock removed throughout `SqliteGraphStore`). The
  concurrency test was edited to serialize its own calls with a
  `threading.Lock()`, removing its power to detect the corresponding
  production lock's removal. See dedicated section above.
  **Recommendation**: revert the test to its original unlocked form (already
  present in git history prior to the uncommitted change) and restore
  `self._lock` in `store.py`, or — if the maintainer has concluded the lock
  is genuinely unnecessary for `mode=ro` SQLite reads — replace the test with
  one that can actually detect a regression in that specific claim (e.g.
  assert against a fault-injected connection, or run under `-W error` with
  `sqlite3`'s threading mode forced to `multi-thread`/`serialized` via
  `sqlite3.threadsafety` checks, or stress a write-while-reading hand-off
  through `BuildLock`). **Effort**: S (revert) / M (new test).

- **TEST-02** (Medium, Confidence: High) — `tests/test_mcp_sse.py:131,170,221`.
  Fixed `await asyncio.sleep(0.4)` used as a readiness wait for a real
  `uvicorn` server bound to a real socket, instead of polling
  `/health`-equivalent or retrying the connect. Flakiness risk on loaded CI
  runners where uvicorn startup exceeds 400ms. **Recommendation**: poll with
  a short timeout/backoff instead of a fixed sleep. **Effort**: S.

- **TEST-03** (Low, Confidence: Medium) — `tests/*.py` contains 43 instances
  of `assert ... is not None` as a sole or terminal assertion (e.g. several
  in `test_store.py`, `test_schema.py`). Not all are weak (many are followed
  by stronger assertions on the same line/next line), but a non-trivial
  subset asserts only presence/shape, not value correctness. **Recommendation**:
  spot-audit the 43 sites; strengthen ones that assert mere non-None-ness as
  their only check. **Effort**: S-M.

- **TEST-04** (Informational, Confidence: High) — `tests/test_compat.py:46,103-124`.
  Golden-file regeneration (`R2G_REGEN_GOLDEN=1`) is opt-in via env var, not a
  default pytest flag, and `test_rag_json_differs_only_by_the_two_token_keys`
  (lines 103-124) layers explicit key-set assertions on top of the raw
  byte-diff rather than purely rubber-stamping — this is a well-designed
  golden-file pattern, flagged here as a **positive** finding, not a gap.

- **TEST-05** (Medium, Confidence: Medium) — `repo2graph/frameworks.py:519-521`
  and `repo2graph/graph.py:1334-1338` (current working tree). The
  `enclosing_class` tracking was flattened from a dotted qualname
  (`Outer.Inner`) to the bare innermost class name, and `graph.py`'s fixture
  resolution lost the `parent is not None and p == fix["name"]` special case
  that let a nested class's overriding fixture fall back to its *parent's*
  visible set rather than its own. This is exactly the regression
  `test_pytest_nested_classes_and_shadowed_fixtures`
  (`tests/test_framework_edges.py:566-610`) is failing on — confirmed by
  running it in isolation (`1 failed`, build stats `framework_edges_fixtures: 0`
  per the orchestrator). This is evidence the suite is working correctly
  here (a real bug, correctly caught), not a suite defect — included for
  completeness since it's the only current red test.

## Coverage & scenario gaps

1. **On-disk format backward compatibility.** No test loads an `.r2g`/SQLite
   index produced by an *older* `repo2graph` version and asserts it still
   opens/queries correctly under the current code (`test_compat.py` checks
   CLI *output* stability, not index *file-format* stability across
   versions). Risk: a schema change to `store.py`'s SQLite tables or the
   JSON node/edge record shape silently breaks every cached index a user
   already has on disk, with no CI signal. **Sketch**: commit a small
   fixture `.sqlite`/`.r2g` built by an old release into `tests/fixtures/`,
   and add a test that opens it with the current `open_graph_store` and runs
   a handful of `node()`/`neighbours()` queries against known IDs.

2. **Corrupt/truncated index files.** `tests/test_store.py` has
   `test_directory_at_sqlite_path_exits_diagnosed` (a directory where a file
   is expected) but no test for a truncated/half-written SQLite file (e.g.
   process killed mid-`CREATE TABLE`) or a JSONL file with a trailing partial
   line. `has_chunks`/`chunk_count` etc. catch `sqlite3.DatabaseError` for a
   *missing table*, not necessarily a corrupt file with a valid header but
   garbage mid-file. **Risk**: an interrupted build (SIGINT, disk full) could
   leave an index that fails in a way the user sees as an unhandled
   traceback rather than the "stale cache, rebuild" diagnosis the code
   comments in `store.py:247-253` claim to provide. **Sketch**: truncate a
   real built `.sqlite` at a byte offset mid-table and assert
   `open_graph_store` either refuses cleanly or rebuilds, not that it raises
   unhandled.

3. **SIGINT / interrupted build.** No test simulates a build process
   receiving SIGINT or SIGTERM partway through writing an index and checks
   that a subsequent build/query does not see a half-written, falsely-valid
   cache. `tool.coverage.run` sets `sigterm = true` for its own coverage
   purposes, which is unrelated. **Sketch**: start a build subprocess, send
   SIGTERM after a short delay, assert the next build either detects
   incompleteness or cleanly restarts rather than trusting a partial file.

4. **Disk-full / write-failure mid-build.** No test forces an `OSError`
   (`ENOSPC`-like) during node/edge serialization to confirm the CLI exits
   with a diagnosed error rather than a partial, falsely-complete index.

5. **True concurrent *builds* against one index path.** `test_concurrent_store_materialization`
   covers concurrent *opens* of an already-built or about-to-be-built store;
   there is no test for two separate `repo2graph build` **processes** (not
   threads) targeting the same output directory simultaneously, which is the
   scenario `BuildLock` in `lock.py` exists for. `lock.py` itself shows only
   50% coverage in the subset measurement above (lines 129-345 partly
   uncovered), consistent with this being under-exercised.

6. **Network failures in `fetch.py`/`rag --answer`.** `fetch.py` is 18%
   covered in the subset run and is the one module with a documented
   network-access exception in `SECURITY.md`. No test simulates a clone
   timing out, a truncated/partial clone, DNS failure, or an HTTP error from
   whatever `rag --answer`'s LLM call path uses. **Risk**: the one
   intentionally-networked code path is the least tested for failure modes.
   **Sketch**: monkeypatch the subprocess/HTTP call to raise
   `TimeoutError`/`ConnectionError` and assert a clean, diagnosed CLI exit
   rather than a stack trace with a potentially-leaked URL/token.

7. **Windows/POSIX path & encoding edge cases are present but asymmetric.**
   `test_encoding.py:303` and `test_prompt_isolation.py:106` both explicitly
   *skip the Windows case* (non-UTF-8 filenames, newline-in-filename) rather
   than testing the Windows-equivalent hostile input (e.g. reserved device
   names `CON`/`AUX`, trailing-dot/trailing-space paths, `\\?\` long-path
   prefix, NTFS alternate data streams). There's no dedicated
   Windows-hostile-path test to replace what's skipped. **Risk**: this
   repository explicitly runs CI on Windows (`ci.yml` matrix), so Windows is
   a first-class target, but its hostile-path coverage is strictly narrower
   than POSIX's.

8. **Very large inputs.** `test_viz_large.py`, `test_file_limits.py`,
   `test_resource_limits.py` exist and cover size-limit enforcement, but
   there's no test of a pathologically large *single file* (multi-GB) hitting
   the tree-sitter parse path directly rather than the pre-parse size guard
   — i.e. does the guard in `limits.py` (0% covered in the subset,
   consistent with it not being exercised by the 6 files chosen) actually
   run before the expensive parse in all call sites, or could a call site
   bypass it?

## Skipped/xfail triage

Full enumeration of the orchestrator's 18 skips/1 xfail was not re-derived
(instructed not to re-run the full suite); this triage is based on grepping
every `skipif`/`pytest.skip` site in `tests/` and cross-checking this
environment (Windows, Git-Bash-on-PATH, `node` present, `cpp` absent, `git`
present, `model2vec` absent, `sentence-transformers`/`numpy`/`mcp` present,
`R2G_TEST_REAL_EMBEDDER` unset):

- **Legitimate, optional-dependency gated**: `test_static_embedder.py`'s
  `requires_model2vec` (model2vec not installed — it's the optional
  `rag-static` extra) and `test_vectors.py:804` real-embedder smoke test
  (gated behind `R2G_TEST_REAL_EMBEDDER=1`, intentionally opt-in so CI
  doesn't depend on downloading a real model). Both legitimate.
- **Legitimate, platform-gated (win32)**: `test_determinism.py:444` (POSIX-only
  path character), `test_prompt_isolation.py:106` (Windows forbids newlines
  in filenames), `test_encoding.py:303` (needs a filesystem storing raw
  bytes — NTFS doesn't), `test_integrity.py` symlink tests (lines 101, 370,
  and others — Windows symlinks need elevated rights/dev mode),
  `test_parse_budget.py:292` (libproc is Darwin-specific),
  `test_integrity.py:798` (fsmonitor hook is a shell command, skipped on
  win32). All legitimate — genuine platform capability gaps, correctly
  excluded rather than faked.
- **Legitimate, missing external tool**: `test_cpp_parse.py`'s several
  `pytest.skip` calls for "cpp preprocessor not available" (no `cpp` on this
  machine's PATH) — correctly a runtime capability probe rather than a
  blanket platform skip, since `cpp` availability is independent of OS.
- **The xfail**: `tests/test_properties.py:635` — not read in full, but its
  presence next to Hypothesis property tests with `derandomize=True` suggests
  a known-shrinking-edge-case marked xfail rather than silently skipped;
  this is the right way to record a known gap (it still runs and would flip
  to XPASS-failure if fixed unexpectedly, which pytest reports).

No skip found in this grep that looks like it's masking a real, fixable
failure under a misleading "optional dependency" label — all are either true
platform capability gaps or genuinely-optional extras.

## Flakiness & performance of the suite

- **Timing-based flakiness risk**: `tests/test_mcp_sse.py` (fixed
  `asyncio.sleep(0.4)` × 3 call sites) — see TEST-02.
- **Port-binding risk**: `tests/test_mcp_sse.py:25-27` uses
  `socket.bind(("127.0.0.1", 0))` to get a free port, which is the correct
  pattern (OS-assigned ephemeral port) — low risk of port collision, no
  finding here.
- **Subprocess-based tests**: `test_npm_launcher.py`, `test_cpp_parse.py`,
  `test_fetch_ref.py` shell out to real `node`/`cpp`/`git`; these are
  appropriately skip-gated on tool absence (see triage above) so they don't
  flake, just skip, in constrained environments.
- **Slowest tests**: per the orchestrator, MCP stdio conformance tests
  (~3-5s each) dominate; this is consistent with real subprocess/stdio
  round-trips in `test_mcp.py`/`test_mcp_conformance.py` rather than sleeps,
  so it's a cost-of-correctness, not a smell.
- **No `time.sleep` grep hits found** outside the asyncio cases above in a
  spot check of concurrency-related files, which is a positive signal —
  most of the suite avoids sleep-based synchronization.

## Recommended testing improvements (prioritised)

1. **(P0)** Resolve TEST-01 before merging any version of this working tree:
   either restore `store.py`'s lock and revert the test lock, or replace the
   test with one that can detect the specific claim being made about
   `mode=ro` SQLite thread-safety. Do not leave the current combination
   (lock removed from production, lock added to test) in any commit.
2. **(P1)** Add a backward-compatibility test for the on-disk index format
   (gap #1) — this is the highest-risk *untested* scenario found, since it
   affects every existing user's cache on upgrade with zero current signal.
3. **(P1)** Add process-level (not thread-level) concurrent-build and
   corrupt/truncated-index tests (gaps #2, #3, #5) to actually exercise
   `BuildLock` and the "stale cache, not corrupt file" diagnosis `store.py`
   claims to provide.
4. **(P2)** Fix TEST-02 (sleep-based readiness wait in `test_mcp_sse.py`).
5. **(P2)** Add `fetch.py` network-failure tests (gap #6) given it's the
   one documented network-access exception and currently among the
   least-covered modules in any subset measured.
6. **(P3)** Add Windows-hostile-path tests to parallel the POSIX-only ones
   skipped on Windows (gap #7), rather than leaving Windows strictly less
   tested on hostile input despite being a first-class CI target.
7. **(P3)** Spot-audit the 43 `assert ... is not None`-terminal sites
   (TEST-03) for ones that should assert actual values.

## Limitations

- Did not re-run the full suite (per instructions); all full-suite facts are
  taken from the orchestrator's supplied numbers.
- The 41.31% coverage figure is a deliberately narrow 6-file subset, cited
  only for methodology and module-level detail on files it touches; it
  should not be read as an estimate of real suite coverage, which the repo's
  own comment states is ~86% branch on the full 9-cell matrix.
- The skip/xfail triage is inferred from static `skipif` conditions plus
  this environment's installed tools/deps, not from re-running the suite
  with `-rs`; exact 1:1 mapping to the orchestrator's "18 skipped" count was
  not verified line-for-line.
- Did not inspect every one of the ~2,783 individual test bodies; quality
  findings are evidence-based spot checks (grep + targeted reads), not an
  exhaustive line-by-line review.
