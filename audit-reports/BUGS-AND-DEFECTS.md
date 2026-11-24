# Bugs and Defects

Severity and confidence here are the orchestrator's adjudicated values, not the
raw values reported by specialist agents. Where an agent's claim was refuted or
re-rated, the original claim and the reason are recorded.

**Confidence levels:** *Confirmed* = reproduced by executing code. *High /
Medium / Low* = inspection only, decreasing strength.

## Summary

| ID | Severity | Confidence | Location | Defect |
|---|---|---|---|---|
| BUG-01 | **CRITICAL** | Confirmed (×3 independent) | `repo2graph/store.py:146-287` | Unsynchronised shared SQLite connection returns **silently wrong data** under concurrent queries |
| BUG-02 | **HIGH** | Confirmed | `repo2graph/frameworks.py:454-466` | Nested test classes sharing an inner name collapse into one key; fixture scope bleeds between unrelated classes |
| BUG-03 | **HIGH** | Confirmed | `repo2graph/graph.py:1333-1338` | Shadowing fixture's edge to the outer fixture it overrides vanishes silently |
| BUG-04 | **HIGH** | Confirmed | `tests/test_store.py:93-115` | A failing test was silenced by moving the lock into the test rather than fixing the code |
| BUG-05 | LOW | Confirmed | `repo2graph/store.py:54-69` | `GraphStore` Protocol lost `__enter__`/`__exit__`; dormant type-contract gap |

All five originate in the **same uncommitted working-tree diff**, which partially
reverts commit `516fe12d` ("fix: resolve #523, #524, #525"). Nothing in this
report is a defect in committed `HEAD` code.

---

## The working-tree regression (root cause of BUG-01 … BUG-05)

### What happened

Commit `516fe12d`, released in **v5.0.0** on 2026-11-01, fixed three filed
issues. The working tree as found reverts parts of that commit:

| File | Hunk | Effect |
|---|---|---|
| `repo2graph/store.py` | `import threading`, `self._lock`, and 13 `with self._lock:` guards deleted | Removes the synchronisation issue #524 asked for |
| `repo2graph/store.py` | `__enter__`/`__exit__` removed from the `GraphStore` Protocol | Withdraws a contract #524 asked for (concrete classes keep the methods) |
| `repo2graph/frameworks.py` | `cls_qualname` construction reverted to bare `cls_name` | Reverts the issue #523 fix |
| `repo2graph/graph.py` | `base_visible`/`visible` shadow-resolution collapsed to a plain dict lookup | Reverts the other half of #523 |
| `tests/test_store.py` | A `threading.Lock()` added **inside the test**, wrapping its own assertions | Silences the test that detects the `store.py` revert |

`check_same_thread=False` is retained, so the literal `sqlite3.ProgrammingError`
named in issue #524 does **not** resurface. That retention is what makes the
regression dangerous rather than merely broken: Python no longer refuses the
cross-thread call, so instead of a loud error the connection's cursors interleave
and threads steal each other's result rows.

### Why this is reported as CRITICAL rather than "uncommitted scratch work"

Three reasons, and the audit rule against speculation applies — this section
states only what was verified:

1. The fixes being reverted are **released code**. `516fe12d` is contained in tag
   `v5.0.0`, published 2026-11-01. This is not unreleased work in progress.
2. The reverted behaviour is covered by **committed regression tests**, one of
   which is currently red (see `TEST-EXECUTION-LOG.md`).
3. The accompanying test edit means the suite reports **green on the half that
   matters most**. A contributor running `make test` sees one unrelated-looking
   failure and no signal at all about thread safety.

What this audit **cannot** determine is intent — whether this diff is a deliberate
experiment, an interrupted bisect, or an accidental clobber. It is not treated as
intentional. Note only that this repository has a recorded prior incident in
which a whole-tree `ruff format` by a parallel agent silently reverted a fix while
all checks stayed green; the shape here is similar. The recommendation is the
same either way: restore the hunks from `516fe12d` before committing anything.

---

## BUG-01 — Unsynchronised SQLite connection returns silently wrong data

- **Severity:** CRITICAL
- **Confidence:** Confirmed — reproduced three times independently (orchestrator twice, bug-hunting agent once)
- **Location:** `repo2graph/store.py:146-287` (`SqliteGraphStore`)
- **Regresses:** issue #524
- **CWE:** CWE-567 (unsynchronised access to shared data), CWE-662

### Root cause

`SqliteGraphStore.__init__` opens **one** `sqlite3` connection with
`check_same_thread=False` and shares it across all callers. At `HEAD` every query
method held `self._lock` while executing. The working tree deletes the lock but
keeps `check_same_thread=False`, so concurrent `execute()` calls on a single
connection interleave. SQLite's C library is thread-safe for *separate*
connections; it does not make one connection's cursor state safe for simultaneous
use.

### Reproduction

16 threads × 200 iterations calling `node`, `neighbours`, `has`, `iter_nodes`,
`iter_edges`, `node_count` on one store, with no lock — i.e. as a production
caller does. Script: `scratchpad/repro_store_concurrency.py`.

```
$ python repro_store_concurrency.py
nodes=8 probe_node=repo:sample_repo
THREAD_ERRORS=15
```

Observed exceptions, verbatim:

```
TypeError: 'NoneType' object is not subscriptable
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
ValueError: not enough values to unpack (expected 1, got 0)
```

Cause isolated by re-running the identical script with the lock reinstated:

```
$ python repro_store_locked.py
THREAD_ERRORS=0
```

15 errors without, 0 with, same script and index. The deleted `threading.Lock`
is the sole differentiator. The bug-hunting agent reproduced the same workload
separately and also saw 15/16 threads fail, plus
`InterfaceError: bad parameter or other API misuse`.

### Actual vs expected

| | Behaviour |
|---|---|
| Expected | Concurrent reads of a read-only index return correct records, or fail loudly |
| Actual | `fetchone()` returns `None` where a row exists; `json.loads` receives `''`; row unpacking gets 0 columns — **wrong answers as well as crashes** |

### Why this is the most severe finding

The failure mode is not confined to exceptions. In the decisive experiment under
BUG-04, the failing assertion was `rec is not None and rec["id"] == nid` — the
store returned a record that was *not the record asked for*. For a tool whose
entire product is **cited, trustworthy answers about code**, a store that
occasionally returns the wrong node under concurrency undermines the central
guarantee, and does so invisibly. A wrong citation is worse than a crash,
because the user cannot tell it happened.

### Exposed call paths

`SqliteGraphStore` is reached through `Index` from `repo2graph/mcp/retrieval.py`
and `mcp/traversal.py`. The MCP server handles concurrent tool calls — and
`tests/test_mcp_conformance.py::test_concurrent_calls_on_one_session_all_complete`
exists precisely because concurrent calls on one session are a supported
scenario. The SSE transport widens this further. `SqliteGraphStore` is the
backend recommended for large repositories, so exposure rises with repo size.

### Remediation

Restore the `store.py` hunk from `516fe12d`: re-add `import threading`,
`self._lock = threading.Lock()`, and the `with self._lock:` guard on every method
that touches `self._db`. Effort: **S** (mechanical revert).

Longer term, consider `threading.local()` connections instead of one shared
locked connection — it removes the contention the lock imposes without
reintroducing the race. That is an enhancement, not required for the fix.

---

## BUG-02 — Nested classes sharing an inner name collapse together

- **Severity:** HIGH
- **Confidence:** Confirmed (reproduced by the bug-hunting agent)
- **Location:** `repo2graph/frameworks.py:454-466` (`extract_pytest_facts`)
- **Regresses:** issue #523

### Root cause

`HEAD` keys the `classes` dict by qualname (`TestA.Meta`, `TestB.Meta`). The
working tree reverts to the bare class name, so two differently-nested classes
sharing an inner name collapse to a single `classes["Meta"]` entry. One class's
`usefixtures` list is lost, and both classes' tests are tagged with the same
parent — wrong for at least one of them.

### Actual vs expected

Given two files each containing a nested `Meta` under a different outer class:

| | Behaviour |
|---|---|
| Expected | Two disjoint entries, `TestA.Meta` and `TestB.Meta`, each with its own fixtures |
| Actual | One entry `Meta`; one `usefixtures` list silently discarded |

### Impact

Wrong `USES_FIXTURE` edges feed `diff --affected-tests`, which CI consumers use
to decide **which tests to run**. A wrong fixture edge means a genuinely affected
test is not selected, and a real regression ships un-tested. Silent, and in the
one subsystem whose errors propagate into other projects' CI decisions.

### Remediation

Restore the `cls_qualname` construction from `516fe12d`. Effort: **S**.

---

## BUG-03 — A shadowing fixture's edge to the fixture it overrides vanishes

- **Severity:** HIGH
- **Confidence:** Confirmed (reproduced by the bug-hunting agent with a full `build --framework-edges`)
- **Location:** `repo2graph/graph.py:1333-1338` (`add_pytest_fixture_edges`)
- **Regresses:** issue #523

### Root cause

`HEAD` resolves a fixture parameter that shares its own name against
`base_visible` — the enclosing scope — which is how pytest's fixture overriding
works: `def db(self, db)` requests the *outer* `db`. The working tree collapses
this to `if p in visible`, so a fixture that shadows an outer fixture of the same
name resolves to itself, is rejected by the `!= csid` self-edge guard, and emits
**no edge at all**.

### Actual vs expected

A repo with a `conftest.py` fixture `db` and a class override `def db(self, db)`:

| | Edges emitted |
|---|---|
| Expected (`HEAD`) | `test_x → TestFoo.db` **and** `TestFoo.db → conftest.db` |
| Actual (working tree) | `test_x → TestFoo.db` only — the override link is gone |

No error, no warning, no parse failure. The graph is simply incomplete.

### Impact

Fixture override is an extremely common pytest idiom, so this is not an edge
case. The dependency chain from a test to the conftest fixture it ultimately
depends on is broken, which again degrades `--affected-tests` selection — and
degrades it toward **under-selection**, the unsafe direction.

### Remediation

Restore the `base_visible` fallback from `516fe12d`. Effort: **S**.

---

## BUG-04 — A failing test was silenced instead of the code being fixed

- **Severity:** HIGH (process and test-integrity defect)
- **Confidence:** Confirmed — decisive experiment below
- **Location:** `tests/test_store.py:93-115`

### What the edit does

`test_sqlite_store_concurrent_thread_queries` exists to prove that concurrent
threads can query `SqliteGraphStore` safely. The working tree adds a
`threading.Lock()` created **by the test** and wraps each worker's store calls in
it. Eight workers that were meant to run concurrently now execute strictly one at
a time. The test cannot observe a race, so it passes whether the store is
thread-safe, unsafe, or absent a lock entirely.

### The decisive experiment

The testing specialist argued the original test could *never* have caught this
class of regression, reasoning that CPython's `sqlite3` serialises at the C
level. That reasoning is **wrong**, and was refuted by direct measurement.

The committed (`HEAD`) test body was replayed verbatim — 8 workers, one pass
each, no lock in the test — against the current unlocked store, 40 independent
trials. Script: `scratchpad/repro_original_test_body.py`.

```
$ python repro_original_test_body.py
TRIALS=40 FAILED_TRIALS=32
---- first exception ----
AssertionError
```

Two conclusions, both load-bearing:

1. **The original test did catch the regression**, in 32 of 40 runs (80%). CI
   would have gone red. The test edit was therefore not a cleanup — it is the
   change that turned a red suite green while the defect remained.
2. **The failure is `AssertionError`, not a SQLite exception.** The assertion that
   failed is `rec is not None and rec["id"] == nid`. The store returned the wrong
   record. This is the direct experimental proof that BUG-01's failure mode
   includes silently wrong data, which is why BUG-01 is rated CRITICAL.

### Impact

A test that cannot fail is worse than no test: it occupies the slot where the
real guarantee should be, and reports success. Anyone reading
`tests/test_store.py` would reasonably conclude thread safety is covered.

### Remediation

Revert the `tests/test_store.py` hunk so the test is genuinely concurrent again.
Then strengthen it beyond `HEAD`'s version: 8 workers × 1 pass detected the
regression only 80% of the time, which is too flaky-in-reverse to rely on.
Raise iterations per worker and assert **returned values are correct**, not
merely that no exception was raised. Effort: **S** to revert, **S–M** to
strengthen.

---

## BUG-05 — `GraphStore` Protocol lost its context-manager contract

- **Severity:** LOW
- **Confidence:** Confirmed (dormant — no live call site)
- **Location:** `repo2graph/store.py:54-69`

`__enter__`/`__exit__` were removed from the `GraphStore` Protocol while both
concrete implementations retain them. `python -m mypy repo2graph/` still passes
(`Success: no issues found in 60 source files`), which confirms no current call
site uses `with` on a value typed as the protocol. So nothing breaks today.

It remains a real regression of an explicit issue #524 requirement: the protocol
no longer promises deterministic handle closure, and the next author who writes
`with store:` against the protocol type gets a type error for no discoverable
reason. Windows handle-release — the original motivation, `PermissionError`
[WinError 32] on cache removal — is now guaranteed only by convention.

**Remediation:** restore the two protocol members. Effort: **S**.

---

## Cleared during investigation

Recorded so the absence of findings is legible, not mistaken for an unexamined area.

| Area | Result |
|---|---|
| Issue #525 (conftest `.endswith()` path collisions) | **Not regressed.** `graphdiff.py` is untouched by the working-tree diff. |
| Issue #513 (fixture injection in `--affected-tests`) | **Not regressed directly.** The base mechanism is intact; class-scoped consumption layered on it is broken via BUG-02/BUG-03. |
| Circular imports | None genuine. Apparent cycles are all `TYPE_CHECKING`-only or deliberately deferred function-body imports. |
| Broad `except Exception` handlers | Reviewed across the codebase; all sampled sites are commented, deliberate resilience boundaries. None found swallowing an error into a silently wrong result. |
| Error-to-empty-result conversions in `store.py` | The `except sqlite3.DatabaseError: return None/[]` handlers are deliberate ("a stale cache to be rebuilt, not a corrupt file to fail on") and documented as such. Not a defect — though they do mean BUG-01's corruption can surface as empty results rather than errors in the chunk methods, which reinforces BUG-01. |
| `ruff` / `mypy` cleanliness | `ruff check .` clean, `ruff format --check` clean on 280 files, `mypy` clean on 60 files. |

## Limitations

- Committed `HEAD` was never tested in isolation, because stashing or checking
  out would have modified the working tree. HEAD's behaviour is established from
  `git show 516fe12d` and from reproductions that re-add the lock in-harness.
- Cross-platform defects are inspection-only: a single Windows host was
  available. Linux and macOS behaviour was not verified.
- Reproductions used `tests/fixtures/sample_repo`, a small fixture. Concurrency
  outcomes are load- and timing-dependent; the observed failure *counts* will
  vary by machine, though the failure *mode* reproduced every time.
