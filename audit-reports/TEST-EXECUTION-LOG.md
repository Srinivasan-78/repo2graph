# Test Execution Log

Every command in this file was actually executed by the lead orchestrator on the
working tree as found. Nothing here is estimated, and no result is reported that
was not observed. Commands that were *not* run are listed in
[Not executed](#not-executed) with the reason.

## Environment

| Property | Value |
|---|---|
| Platform | Windows 11 Pro 10.0.26200 (win32) |
| Shell | Git Bash (POSIX sh) |
| Python | 3.13.15 |
| pytest | 9.1.1 |
| Repository | `E:\Github\repo2graph`, branch `develop` |
| HEAD | `f43b1369` ("docs: correct the Dependabot alert counts in the changelog") |
| Working tree | **DIRTY** — 4 modified files (see below) |
| Date | 2026-11-01 |

### Working tree state at time of testing

```
 M repo2graph/frameworks.py
 M repo2graph/graph.py
 M repo2graph/store.py
 M tests/test_store.py
```

This matters for every result below: **the suite was run against the modified
working tree, not against `HEAD`.** The working tree partially reverts commit
`516fe12d` ("fix: resolve #523, #524, #525"). See `BUGS-AND-DEFECTS.md`.

Per the audit's no-modification rule, the working tree was left exactly as found.
It was **not** stashed, reverted, or committed, so no result below describes the
behaviour of committed `HEAD` code except where explicitly stated.

---

## 1. Full test suite

```
$ python -m pytest -q -n auto -p no:randomly --durations=25
```

| Metric | Value |
|---|---|
| Exit code | **1** |
| Failed | **1** |
| Passed | **2763** |
| Skipped | 18 |
| xfailed | 1 |
| Duration | 66.02 s (`-n auto`, 16 workers) |

### The failure

```
FAILED tests/test_framework_edges.py::test_pytest_nested_classes_and_shadowed_fixtures
```

```
>       assert any(
E       assert False
E        +  where False = any(<generator object ...>)
tests/test_framework_edges.py:596: AssertionError
```

Build statistics emitted by the failing run show the cause directly:

```
"framework_edges_fixtures": 0,
```

No pytest fixture edges were produced at all. Confirmed by re-running the single
test in isolation (`python -m pytest tests/test_framework_edges.py::test_pytest_nested_classes_and_shadowed_fixtures -q`
→ `1 failed in 6.29s`), so this is deterministic, not a parallelism artefact.

This is the working tree's revert of the issue **#523** fix (class-level pytest
fixture scoping) being caught by a committed regression test.

---

## 2. Static analysis and type checking

| Command | Exit | Result |
|---|---|---|
| `python -m ruff check .` | 0 | `All checks passed!` |
| `python -m ruff format --check .` | 0 | `280 files already formatted` |
| `python -m mypy repo2graph/` | 0 | `Success: no issues found in 60 source files` |

Note the significance of the clean mypy run: the working tree removed
`__enter__`/`__exit__` from the `GraphStore` **Protocol** in `repo2graph/store.py`
while leaving them on both concrete implementations. mypy still passes, which
tells us no call site currently uses `with` on a value *typed as* the
`GraphStore` protocol. The removal is therefore a latent typing gap rather than
an active type error — but it silently withdraws a guarantee that issue #524
explicitly asked for.

`ruff format --check` passing on 280 files is also recorded here deliberately:
formatting was **not** run during this audit. Per a prior incident in this
repository, a whole-tree `ruff format` by a parallel agent once silently
reverted a fix while leaving all checks green. All subagents in this audit were
instructed read-only and forbidden from running formatters.

---

## 3. Orchestrator reproduction: `SqliteGraphStore` thread safety

Purpose: independently verify the severity of the `store.py` hunk of the working
tree diff, which deletes the `threading.Lock` that guarded the single shared
`sqlite3` connection. Issue #524 predicted `sqlite3.ProgrammingError`. The
observed behaviour is worse.

Script: `scratchpad/repro_store_concurrency.py`. It builds a real index from
`tests/fixtures/sample_repo` into a temp directory, opens
`SqliteGraphStore.from_index(...)`, then calls `node`, `neighbours`, `has`,
`iter_nodes`, `iter_edges` and `node_count` from **16 threads × 200 iterations**
with **no lock** — i.e. the way a production caller does, rather than the way the
modified test now does.

### Run A — no lock (reproduces the working tree's production behaviour)

```
$ python repro_store_concurrency.py
nodes=8 probe_node=repo:sample_repo
THREAD_ERRORS=15
```

15 of 16 threads raised. Representative exceptions, verbatim:

```
TypeError: 'NoneType' object is not subscriptable
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
ValueError: not enough values to unpack (expected 1, got 0)
```

These are **cursor-interleaving corruption**, not a clean thread-affinity
error. Because `check_same_thread=False` is still passed to `sqlite3.connect`,
Python no longer refuses the cross-thread call; concurrent `execute()` calls on
one connection instead interleave and steal each other's result rows. A
`fetchone()` that should return a row returns `None`; a `json.loads` receives an
empty string. **The failure mode includes silently wrong reads, not only
exceptions** — the most dangerous possible outcome for a tool whose product is
cited answers about code.

### Run B — identical script, lock reinstated around the same calls

```
$ python repro_store_locked.py
nodes=8 probe_node=repo:sample_repo
THREAD_ERRORS=0
```

### Conclusion

0 errors with the lock, 15 without, same script, same index, same machine. The
deleted `threading.Lock` is confirmed as the sole differentiator. Confidence:
**Confirmed (reproduced, with isolated cause)**.

Critically, the suite does **not** catch this, because
`tests/test_store.py::test_sqlite_store_concurrent_thread_queries` was edited in
the same working tree to take a `threading.Lock` *inside the test body*. The
test still passes while the production code it exists to protect is unsafe.

---

## 4. Not executed

| Check | Status | Reason |
|---|---|---|
| `pytest` against clean `HEAD` | **Not executed** | Would require stashing or checking out, which the audit's no-modification rule forbids. The working-tree-vs-HEAD difference was established by reading `git diff` and `git show 516fe12d` instead. |
| Full-suite coverage measurement | Delegated / partial | Assigned to the testing specialist with instructions to write coverage output to the scratchpad only, so as not to overwrite the repository's existing `.coverage` file. See `TESTING-AND-COVERAGE.md` for what was and was not measured. |
| `bandit`, `pip-audit`, `zizmor` | **Blocked** | Not installed in this environment. Installing tooling was out of scope by rule. Security review was therefore manual; recorded as a limitation in `SECURITY-ASSESSMENT.md`. |
| CI workflow execution | **Not executed** | Deliberately never triggered. Workflow assessment used read-only `gh run list` / `gh run view` history instead. |
| Publishing / release dry-run | **Not executed** | Would touch PyPI, npm, Docker, the VS Code marketplace or git tags. Out of scope for an assessment. |
| Network-dependent subcommands (`github`, `rag --answer`) | **Not executed** | Make outbound network calls; excluded to keep the audit offline and avoid consuming credentials. |
| macOS / Linux platform verification | **Blocked** | Single Windows host available. Cross-platform findings are inspection-based and labelled as such. |
