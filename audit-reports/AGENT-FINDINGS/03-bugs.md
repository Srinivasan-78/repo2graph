# Agent 3 — Bug Detection, Logic Errors & Regressions

## Summary

The working tree contains an uncommitted partial revert of commit `516fe12d`
(`fix: resolve #523, #524, #525`). I reproduced three distinct regressions
from that revert, two of them with a full repo2graph build producing a
visibly wrong (not just different) graph, and one with a direct concurrency
stress test that corrupts query results. These are the headline findings.
Independent hunting elsewhere in the codebase did not turn up further
confirmed defects within the time budget; two suspected issues are listed as
inspection-only.

| ID | Severity | Confidence | Area | One-line |
|---|---|---|---|---|
| BUG-01 | CRITICAL | Confirmed | store.py | Removing the `threading.Lock` around the shared read-only sqlite3 connection causes concurrent queries to return corrupted/garbled records and raise `InterfaceError`/`JSONDecodeError` |
| BUG-02 | HIGH | Confirmed | frameworks.py | Reverting class-qualname scoping collapses nested classes with the same name across a file into one `classes[name]` key, silently dropping/mis-assigning `usefixtures` |
| BUG-03 | HIGH | Confirmed | graph.py | Reverting the shadow-fixture special case drops the edge from an overriding fixture to the outer-scope fixture of the same name entirely (no edge, no error) |
| BUG-04 | LOW | Inspection only | store.py | `GraphStore` Protocol no longer declares `__enter__`/`__exit__`, a static-typing-only regression |
| BUG-05 | LOW | Medium | tests/test_store.py | The concurrency test moved its own `threading.Lock` into the test, so it can no longer detect BUG-01 — it now asserts the test's own serialization, not the store's |

## Working-tree regression analysis

Commit `516fe12d` (`fix: resolve #523, #524, #525`) touched `store.py`,
`graph.py`, `frameworks.py`, and `graphdiff.py`. The uncommitted working-tree
diff reverts the substance of the `store.py` and `graph.py`/`frameworks.py`
changes while leaving `graphdiff.py` (the #525 conftest-path fix) untouched.

### Hunk 1 — `store.py`: removes `threading.Lock` from `SqliteGraphStore` (#524 revert)

`SqliteGraphStore.__init__` still opens the connection with
`check_same_thread=False` (that part of the revert was *not* undone), but
every method that previously did `with self._lock: self._db.execute(...)`
now calls `self._db.execute(...)` unprotected. `close()` is likewise
unprotected. This leaves a connection that is *declared* thread-shareable
(`check_same_thread=False`) but is no longer *actually* serialized for
concurrent access — see BUG-01 for the reproduction.

### Hunk 2 — `store.py`: removes `__enter__`/`__exit__` from the `GraphStore` Protocol

`close()` stays on the Protocol; only `__enter__`/`__exit__` were removed.
The concrete classes (`MemoryGraphStore`, `SqliteGraphStore`) keep their own
`__enter__`/`__exit__`, so `with SqliteGraphStore.from_index(...) as store:`
(`tests/test_store.py:122`) still works at runtime — nothing crashes. The
regression is purely a static-typing contract mismatch: any code that holds
a value typed as `GraphStore` (the Protocol, e.g. `query.py:662`,
`query.py:655`) and tries `with store:` would now fail a type checker,
because the Protocol no longer promises `__enter__`/`__exit__` even though
every real implementation has them. I did not find a call site in
`repo2graph/` that actually does `with <GraphStore-typed value>:` today, so
this is dormant rather than reachable — see BUG-04.

### Hunk 3 — `frameworks.py`: reverts class-qualname scoping (#523 revert)

`extract_pytest_facts` goes back to keying `classes[cls_name]` (and
propagating `enclosing_class=cls_name or enclosing_class`) by the bare class
name instead of the dotted qualname (`cls_qualname = f"{enclosing_class}.{cls_name}"`)
that `516fe12d` introduced. The qualname only differs from the bare name for
*nested* classes, so this specifically re-breaks disambiguation between two
differently-nested classes that happen to share an inner name. See BUG-02.

Also reverted: the `is_test` heuristic. The committed fix simplified
`is_test = fn_name.startswith("test_") or (...)` down to
`fn_name.startswith("test_")`, since the first disjunct already subsumes the
second (`A or (B and A)` ≡ `A`). The working tree restores the old
three-clause form. This is functionally a no-op (verified: the restored
condition can never produce a different boolean than plain
`fn_name.startswith("test_")`) — it is dead/redundant code, not a behavioural
regression, so it is not listed as a separate bug.

### Hunk 4 — `graph.py`: reverts the fixture-shadowing special case (#523 revert)

`add_pytest_fixture_edges` goes back to a flat `p in visible` / `uf in
visible` lookup for a fixture's own `params`/`usefixtures`, dropping the
branch that special-cased `parent is not None and p == fix["name"]` to look
the name up in `base_visible` (the enclosing/conftest scope) instead of
`visible` (which, for a class-scoped fixture, has already been overwritten
with that very fixture). This is the standard pytest "override a fixture
and request the same name to get the next-outer definition" pattern. See
BUG-03. Note this hunk is scoped only to the fixture-to-fixture edge loop
(`graph.py:1333-1338`); the equivalent test-to-fixture loop a few lines below
was not touched by the revert and still resolves correctly.

### Does the modified `tests/test_store.py` still catch the regression?

No. `test_sqlite_store_concurrent_thread_queries` was changed to create its
own `threading.Lock()` inside the test and wrap each thread's three store
calls in `with lock:` before handing off to `ThreadPoolExecutor`. This
serializes the *test's* access to the store, which defeats the purpose of a
concurrency test: it can no longer exercise unserialized concurrent access
to `SqliteGraphStore`, so it passes whether or not the store itself is
thread-safe. I confirmed this by running the reproduction below directly
against `SqliteGraphStore` (bypassing the test's lock) — see BUG-01 for the
result. The test file was explicitly called out as a pre-existing
investigation target and left untouched per instructions; this analysis is
descriptive, not a proposed change.

### Who calls `SqliteGraphStore` from threads?

- `tests/test_store.py` itself is the one deliberate, documented concurrent
  caller, and it is the reason `#524`'s fix (`threading.Lock` +
  `check_same_thread=False`) exists at all.
- `repo2graph/mcp/indexes.py` keeps a process-wide `_INDEXES: dict[str,
  Index]` cache (`mcp/indexes.py:19`), one `Index` reused per output
  directory across tool calls. If a server is run with `--store sqlite`,
  concurrent MCP tool calls against the same index directory share one
  `SqliteGraphStore` instance. Whether that produces genuinely parallel
  OS-thread access depends on the MCP transport: `mcp/server.py` defines
  `async def` handlers that call into the `Index`/store synchronously, and
  `handle_sse` (`mcp/server.py:543`) is the multi-client entry point. I could
  not confirm, without running the server under a concurrent-client load
  test (out of scope for a read-only audit), whether the ASGI/SSE stack used
  here ever dispatches two tool calls onto two different OS threads
  simultaneously against the same `Index`. I flag this as a **plausible**,
  not confirmed, production call path for BUG-01; the defect at the store
  layer itself is Confirmed regardless of whether the MCP path reaches it
  today.
- `repo2graph/tasks.py`'s `TaskManager` spawns a real `threading.Thread` per
  build (`tasks.py:156`), but that thread calls the builder to *write* a new
  index, not query an existing `SqliteGraphStore` — not implicated here.

## Confirmed defects

### BUG-01 — `SqliteGraphStore` concurrent queries return corrupted data without the lock

- **Severity:** CRITICAL
- **Confidence:** Confirmed (reproduced)
- **Location:** `repo2graph/store.py:146-148` (`check_same_thread=False` with
  no serialization) and every query method below it, e.g. `:259`
  (`node()`), `:264` (`has()`), `:270-276` (`neighbours()`), `:287`
  (`close()`).
- **Description:** The connection is opened with `check_same_thread=False`
  (declaring it safe to share across threads) but, after the revert, no
  lock serializes access to it. Concurrent `execute()`/`fetchone()` calls
  from different threads on the same `sqlite3.Connection` interleave at the
  Python level and corrupt each other's cursor state.
- **Root cause:** The working-tree diff deletes `import threading`, the
  `self._lock = threading.Lock()` field, and every `with self._lock:` guard
  that `516fe12d` added, while leaving `check_same_thread=False` in place —
  the two must travel together; removing only the lock produces a
  connection that *looks* shareable but isn't actually serialized.
- **Reproduction:** Built a 2000-node/2000-edge graph into a `SqliteGraphStore`
  on disk, then ran 16 threads × 2000 iterations each calling
  `store.node()`, `store.neighbours()`, `store.has()`, `store.iter_nodes()`
  with no external lock (script in scratchpad,
  `repro_store_race.py`/`repro_store_race_fixed.py`):
  - **Current working-tree code (no lock):** 15 exceptions across the run,
    including:
    - `sqlite3.InterfaceError: bad parameter or other API misuse`
    - `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`
    - `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`
      — i.e. `node()`/`neighbours()` returned a row whose `rec` column was
      corrupted or empty, which is a **silently wrong result** whenever the
      interleaving happens to still produce a *parseable but wrong* string
      rather than an outright exception; the exceptions observed are only
      the cases where corruption was severe enough to not parse at all.
    - Ran against a standalone copy of `repo2graph/store.py` loaded via
      `importlib`, confirmed identical to the current working tree file's
      logic (same unprotected query methods).
  - **Pre-regression code (`git show HEAD:repo2graph/store.py`, with the
    lock restored), identical workload:** 0 errors.
- **Actual vs expected:** Expected: queries against a shared read-only
  `SqliteGraphStore` are safe from any number of threads, as the method
  names (`node`, `has`, `neighbours`, `iter_nodes`) and `check_same_thread=False`
  promise. Actual: concurrent access corrupts or crashes queries.
- **Recommended fix:** Restore `threading.Lock` and wrap every
  `self._db.execute(...)` call (including `close()`) in `with self._lock:`,
  exactly as in `516fe12d`.
- **Effort:** Trivial (re-apply the reverted hunk).

### BUG-02 — Nested pytest classes with the same name collide in `extract_pytest_facts`

- **Severity:** HIGH
- **Confidence:** Confirmed (reproduced)
- **Location:** `repo2graph/frameworks.py:454-466` (`classes[cls_name] = uf`
  and `walk(child, enclosing_class=cls_name or enclosing_class)`)
- **Description:** When a source file defines two differently-nested
  classes that happen to share an inner class name (e.g. two unrelated
  `class Meta:` nested inside two different outer test classes — a real
  pytest idiom for grouping marks/config), their `usefixtures` and their
  child tests' `parent` both key off the bare inner name, so the second
  definition silently overwrites the first in the `classes` dict, and both
  sets of child tests are tagged with the same (wrong, for one of them)
  `parent`.
- **Root cause:** `516fe12d` introduced `cls_qualname =
  f"{enclosing_class}.{cls_name}" if enclosing_class else cls_name` so
  nested classes get a unique dotted key; the working-tree revert goes back
  to the bare `cls_name`.
- **Reproduction:** `extract_pytest_facts` on:
  ```python
  class TestA:
      @pytest.mark.usefixtures("fixture_a")
      class Meta:
          def test_one(self): pass

  class TestB:
      @pytest.mark.usefixtures("fixture_b")
      class Meta:
          def test_two(self): pass
  ```
  - **Current working-tree code:** `classes == {"Meta": ["fixture_b"]}`;
    `fixture_a` is gone entirely, and *both* `test_one` and `test_two` get
    `"parent": "Meta"` — `test_one` (really under `TestA.Meta`) would be
    graph-wired to `fixture_b`, which belongs to the unrelated `TestB.Meta`.
  - **Pre-regression code (`git show HEAD:repo2graph/frameworks.py`), same
    input:** `classes == {"TestA.Meta": ["fixture_a"], "TestB.Meta":
    ["fixture_b"]}`; `test_one`'s parent is `"TestA.Meta"`, `test_two`'s is
    `"TestB.Meta"` — correctly disjoint.
  - Scripts: scratchpad `repro_frameworks_nested.py` (current code) vs.
    `repro_frameworks_nested_fixed.py` (HEAD code loaded standalone via
    `importlib`).
- **Actual vs expected:** Expected: each nested class's fixture scope is
  independent of a same-named nested class elsewhere in the file. Actual:
  they alias each other, producing a silently wrong (not missing, not
  erroring) graph for one of the two classes' tests.
- **Recommended fix:** Restore the `cls_qualname` computation and propagate
  it as `enclosing_class` on recursion, as in `516fe12d`.
- **Effort:** Trivial (re-apply the reverted hunk).

### BUG-03 — Overriding a same-named fixture from an outer scope drops the edge to the original

- **Severity:** HIGH
- **Confidence:** Confirmed (reproduced via full `repo2graph build --framework-edges`)
- **Location:** `repo2graph/graph.py:1333-1338` (`add_pytest_fixture_edges`,
  the `for p in fix["params"]` / `for uf in fix["usefixtures"]` loop)
- **Description:** A class-scoped fixture that shadows a conftest/outer
  fixture of the same name (the standard pytest override pattern:
  `def db(self, db): return db + "..."`, where the inner `db` parameter
  must resolve to the *outer* `db` fixture, not itself) no longer produces
  any edge from the override to the fixture it overrides. The edge is not
  rewired to something wrong — it simply vanishes, because `visible[p]`
  now resolves to the override itself (`sid == csid`), which the `!=
  csid` guard then rejects, and nothing falls back to `base_visible`.
- **Root cause:** `516fe12d` added:
  ```python
  target_fix = (
      base_visible.get(p)
      if (parent is not None and p == fix["name"])
      else visible.get(p)
  )
  ```
  so a same-name param inside a class scope is looked up in the *outer*
  scope instead of the (self-shadowing) class scope. The working-tree
  revert removes this branch and goes back to plain `visible.get(p)`.
- **Reproduction:** Built a 2-file scratch repo:
  ```
  conftest.py:
      import pytest
      @pytest.fixture
      def db():
          return "base"

  test_shadow.py:
      import pytest
      class TestFoo:
          @pytest.fixture
          def db(self, db):
              return db + "+override"
          def test_x(self, db):
              assert db
  ```
  Ran `repo2graph build <repo> --out <out> --framework-edges` (via
  `repo2graph.cli.main`) using (a) the current working tree, and (b) a
  scratch copy of `repo2graph/` with `graph.py`, `frameworks.py`, `store.py`
  replaced by their `git show HEAD:...` content (loaded as a sibling
  package `repo2graph_fixed`, no repo files touched).
  - **(a) Current working-tree code**, `edges.jsonl` `USES_FIXTURE` edges:
    only one edge, `TestFoo.test_x -> TestFoo.db`. The edge
    `TestFoo.db -> conftest.db` is absent.
  - **(b) Pre-regression code**, same input: two edges —
    `TestFoo.db -> conftest.db` (`resolution_kind: param`) *and*
    `TestFoo.test_x -> TestFoo.db`.
- **Actual vs expected:** Expected: the override fixture's dependency on
  the base fixture of the same name is a graph edge (this is exactly the
  kind of resolution repo2graph exists to compute). Actual: that edge is
  silently missing, with no error and no stats counter to flag it — a
  consumer asking "what does `TestFoo.db` depend on" gets an empty answer
  instead of `conftest.db`.
- **Recommended fix:** Restore the `base_visible`-fallback branch for
  `p == fix["name"]` / `uf == fix["name"]` under a non-`None` parent, as in
  `516fe12d`.
- **Effort:** Trivial (re-apply the reverted hunk).

## Suspected defects (inspection only)

### BUG-04 — `GraphStore` Protocol no longer declares `__enter__`/`__exit__`

- **Severity:** LOW
- **Confidence:** Low / inspection only (not reproduced — this is a static-typing
  contract mismatch, not a runtime failure, and I did not run a type
  checker per the task's constraints)
- **Location:** `repo2graph/store.py:54-69` (`class GraphStore(Protocol)`)
- **Description:** `516fe12d` added `close()`, `__enter__`, and `__exit__`
  to the `GraphStore` Protocol so a `GraphStore`-typed value could safely be
  used as a context manager. The working-tree revert removes `__enter__`
  and `__exit__` again (but, notably, leaves `close()` — it's a partial
  revert of that hunk). `MemoryGraphStore` and `SqliteGraphStore` both still
  implement `__enter__`/`__exit__` themselves, so nothing breaks at
  runtime, and `tests/test_store.py:122`'s `with
  SqliteGraphStore.from_index(...) as store:` still works because it's
  typed as the concrete class, not the Protocol.
- **Why not confirmed as a live bug:** I did not find a call site in
  `repo2graph/` holding a `GraphStore`-typed (Protocol-typed) value and
  doing `with store:` on it — `query.py:655/662` type-hints `self.store:
  GraphStore` but never uses it as a context manager. This makes the
  regression dormant under current usage; it would surface as a type-checker
  error (not a runtime bug) the moment someone adds such a call site typed
  against the Protocol.
- **Recommended fix:** Restore `__enter__`/`__exit__` on the Protocol
  alongside `close()`.
- **Effort:** Trivial.

### BUG-05 — The concurrency test can no longer detect the store race

- **Severity:** LOW (test-quality issue, not a product defect on its own)
- **Confidence:** Medium (behavioural claim about the test is verified by
  reading it and by BUG-01's reproduction script bypassing its lock; not
  filed as a numbered "bug" against production code)
- **Location:** `tests/test_store.py` (the uncommitted diff to
  `test_sqlite_store_concurrent_thread_queries`)
- **Description:** See "Working-tree regression analysis" above. The test
  was modified to acquire a `threading.Lock()` it creates itself around each
  thread's store calls, which serializes the test's own access and prevents
  it from ever exercising concurrent, unserialized access to the store —
  meaning it passes identically whether or not `SqliteGraphStore` is
  internally thread-safe. This is listed separately from BUG-01 because the
  defect is in the test's power to detect the regression, not in production
  code.
- **Recommended fix:** Remove the lock from the test body (restore the
  committed version) so it genuinely stresses unserialized concurrent
  access to the shared connection.
- **Effort:** Trivial (revert the test hunk alongside BUG-01's fix).

## Non-issues investigated and cleared

- **`resource`/`setrlimit` POSIX-only calls** (`repo2graph/parsepool.py:113-138`,
  `repo2graph/cli.py:432-453`): both are correctly guarded by `sys.platform`
  checks (`win32` branch uses a separate `_win_cap_child_memory` Windows job
  object; the `resource` import only happens on Linux/macOS branches). No
  cross-platform defect found here.
- **The restored `is_test` heuristic in `frameworks.py`** (part of the #523
  revert): textually different from the committed version but logically
  equivalent (`A or (B and A) ≡ A`), so it does not change test-detection
  behaviour. Not filed as a bug.
- **`SqliteGraphStore.build()`'s atomic-write path** (`store.py:171-216`,
  `tmp.replace(path)`): not touched by the uncommitted diff; did not
  independently find a Windows `PermissionError`-on-replace issue here in
  the time available (the temp file and target are on the same filesystem,
  and nothing else holds `tmp` open at replace time in the code as
  written).

## Limitations

- Per instructions, I did not run the project's pytest suite; all
  reproductions used ad-hoc scripts and throwaway repos under the
  scratchpad, plus direct calls into `repo2graph.cli.main(...)` for the
  build-level reproduction (BUG-03), and a sibling copy of the `repo2graph`
  package with three files swapped to their `git show HEAD:...` content for
  side-by-side comparison (no files in the actual repo were modified).
- I did not confirm whether the MCP server's SSE/multi-client transport
  actually dispatches concurrent tool calls onto separate OS threads against
  a shared `SqliteGraphStore` in practice — that would require standing up
  the server under concurrent load, which is out of scope for a read-only
  audit. The store-layer defect (BUG-01) is Confirmed independent of that;
  only the "is this reachable from the MCP server today" claim is marked
  plausible-but-unconfirmed.
- Given the time budget, independent hunting beyond the priority lead
  covered `store.py`, `graph.py`, `frameworks.py`, `parsepool.py`, and
  `cli.py`'s platform-guarded resource code; I did not get to a comparably
  deep pass over `query.py`/`chunks.py`/`rerank.py`/`answer.py`/`embed.py`
  (ranking math, budget/pagination, citation fidelity) or
  `export.py`/`viz.py`/`graphdiff.py`/`neo4j.py`/`federated.py`/`watch.py`/`fetch.py`
  (encoding/path handling) beyond what the priority lead already touched
  (`graphdiff.py`'s #525 fix, left intact by the working tree and not
  re-examined for new defects). Those areas should be treated as unaudited
  by this pass, not cleared.
