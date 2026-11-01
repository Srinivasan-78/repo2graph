# Agent 7 — GitHub Issues, PRs & Discussions Investigation

Repository: Srinivasan-78/repo2graph. All `gh` calls were read-only (`list`/`view`/`api` GET). No GitHub state was modified. No repo files other than this one were written; no git state was changed; `pytest` was not run.

## Summary & repository issue statistics

- **Total issues (state=all, limit 600): 272.** All 272 are `CLOSED`. **Open issues: 0.**
- Issue numbers span 21 → 525, but the tracker is **not** 272 sequential issues — numbers are shared with PRs in one counter (GitHub issues+PRs share a repo-wide number sequence), so "numbered into the 500s" reflects 272 issues interleaved with 256 PRs (total 529 numbered items), not 525 issues.
- **Every issue (272/272) was authored by the repo owner, `Srinivasan-78`.** Zero issues were externally filed, despite the repo having public contributors on PRs. This is a maintainer-self-audit pattern, not a community bug tracker.
- Label taxonomy observed: `bug` (133), `enhancement` (122), `priority/P2` (110), `priority/P3` (85), `security` (74), `priority/P1` (52), `documentation` (41), `area/mcp` (27), `type/feat` (24), `backlog` (24), `area/graph` (23), `priority/high` (21 — legacy/alternate scheme predating `priority/P*`), `type/fix` (18), `type/ci` (16), `performance` (14), `good first issue` (13), `type/refactor` (12), `area/cli` (11), `area/workflows` (10), `question` (8), `area/docs` (6), `area/query` (6), `area/tests` (4), `type/chore` (4), `docs` (4), `type/docs` (3), `area/walker` (2), `help wanted` (2), plus singletons (`dependencies`, `chore`, `area/action`, `area/embed`).
- **No `priority/P0` issue exists in the history** — the project has apparently never classified anything as P0, or P0 issues (if any existed) were filed and resolved without that exact label, or the label itself was never used despite its presence in the taxonomy description given to this agent.
- **Pull requests (state=all, limit 600): 256 total.** 198 `MERGED`, 58 `CLOSED` (unmerged). No open PRs.
- PR author breakdown: `Srinivasan-78` 175, `app/dependabot` 30, `dyk1454683243-sudo` 30 (an automated fix-generation account, not a human contributor — see PR analysis), `app/prod-igy-bot` 11 (release-automation bot), and genuine external human contributors: `AbhishekKallolimath` (2), `llllgt` (1), `theluckystrike` (1), `Savage676` (1), `Parsiffall1` (1), `DYNOSuprovo` (1), `HarshRajSinghania` (1, closed/unmerged), `adnandispatch9-jpg` (1, closed/unmerged), `gane5hvarma` (1, closed/unmerged).
- Security advisories: **3**, all `closed` (GHSA-6wrx-c2rg-mvm9 medium — `verify_artifacts` path-traversal oracle; GHSA-f896-f643-87cf medium — OIDC `jwks_uri` not origin-constrained; GHSA-mqm8-mc66-wjvj high — OIDC JWKS fetch HTTP-redirect downgrade).
- Dependabot alerts: **37 total, all `fixed`, 0 open.** Severity split: 1 critical, 11 high, 18 medium, 7 low. (Consistent with the memory note that the alert-count toggle doesn't rescan — these are genuinely resolved, not hidden.)
- Discussions: **disabled/empty** — `gh api repos/Srinivasan-78/repo2graph/discussions` returned `[]`.
- Releases: 15 tags from `v1.2.0` (2026-09-09) to `v5.0.0` (2026-11-01, latest), consistent with the automated `publish.yml` release cadence noted in memory.

## Issue lifecycle analysis

Every issue in the tracker is closed, and every issue was filed by the owner — this is used as a self-audit log, not a public bug queue: the owner files a tightly-scoped `[P#] <symptom>` issue (often several in a single sitting, numbered consecutively — e.g. #500–#514, #523–#525), fixes it the same day or within a few days, and closes with a one-line comment naming the resolving commit hash. There is no triage lag, no stale-issue backlog, and no issue left unaddressed — on paper. The catch (see Regressions section) is that "closed with a commit hash" is **not independently re-verified against the working tree** by this process; it is trusted at face value.

Clustering: issues are frequently batch-filed and batch-fixed in groups of 2–5 against a single commit (`fcfece04` resolved both #513 and #514; `516fe12d` resolved #523, #524, and #525 together). This is efficient but creates exactly the risk realized in the regression below: a single revert of that commit's working tree re-breaks multiple issues at once, and nothing detects that the issues are still marked closed.

## Pull request analysis

- 198/256 PRs merged, 58 closed unmerged, 0 open.
- Of the 58 closed-unmerged PRs, 30 are from `app/dependabot` (routine dependency bumps superseded by newer bump PRs — expected churn, not abandoned work) and most of the remainder are from the automated account `dyk1454683243-sudo`, which filed ~30 narrowly-scoped `fix(<area>): ...` PRs (e.g. #170–#231) that were individually closed unmerged.
- **This is not evidence of abandoned fixes.** Spot-checked: PRs #413–#416 ("secrets lowercase detection", "namespace separators", "ci hardening", "docs") were all closed unmerged, but `git log` shows commit `67e15bbf` — *"Combine #413, #414, #415 and #416: parser and secret-scanner fixes, workflow hardening, packaging and security docs (#418)"* — which **is** merged (PR #418, merged 2026-09-25). The repo's workflow (consistent with the "Solo-account merge gates" memory note) is: open several small bot/automation PRs, close them individually, and land the combined diff through a single PR that passes the required-signature/merge gates. Similarly, PR #476 ("Security hardening, audit fixes, and retrieval/MCP improvements") was closed unmerged but its named features (`[tool.repo2graph]` config file, `TESTS`/`USES_FIXTURE` edges, MCP pagination cursors) are all verified present in the current tree (`repo2graph/config.py`, `tests/test_tests_edges.py`, `tests/test_mcp_pagination.py`) — so it too landed via other, smaller merged PRs rather than being abandoned.
- Genuine human external contributions are small (docs, one language-support PR, one test-coverage PR, one map-checksum fix) and all either merged or trivially closed (duplicate/typo PRs like #91 vs #93 "recognize .mts/.cts as TypeScript", both closed, functionality present under #92's sibling work — not independently reconfirmed here, flagged as low-confidence).
- No closed-unmerged PR surveyed carries content this agent could positively identify as **lost** (i.e., a real fix that never landed anywhere). Given the volume (58 PRs), this is a sampled conclusion, not exhaustive — see Limitations.

## Discussions, advisories, Dependabot alerts, releases

- **Discussions:** disabled or never used — API returns an empty array, no error. Cannot be used as a signal source.
- **Security advisories (3, all closed):**
  - GHSA-6wrx-c2rg-mvm9 (medium) — `verify_artifacts()` treated manifest.json checksum keys as trusted paths → arbitrary file read / hash-disclosure oracle via `repo2graph doctor`. Reachable via `doctor.py:414`.
  - GHSA-f896-f643-87cf (medium) — OIDC `jwks_uri` not constrained to the issuer's origin.
  - GHSA-mqm8-mc66-wjvj (high) — OIDC JWKS fetch could be downgraded to cleartext via HTTP redirect, enabling token forgery.
  - All three are in the auth/OIDC and integrity-verification subsystems — consistent with `auth`/`jwt`/`jwks` being a recurring theme in the issue titles (19 and 5 keyword hits respectively, see index below).
- **Dependabot alerts:** 37 total, all state `fixed`, 0 currently open. No permission error encountered (the token has `security_events`/alerts read access in this session).
- **Releases:** 15 releases, v1.2.0 → v5.0.0, latest v5.0.0 tagged 2026-11-01T11:45:43Z — i.e., **the v5.0.0 release was cut the same day this audit is running**, which is worth flagging to the orchestrator: whatever the uncommitted working-tree regression described below is, it has NOT yet been released (it's uncommitted), but the repository is mid-release-cycle.

## Per-issue investigation

Format: Issue #, Title, State, Classification, Fix commit, Verification method and result, Confidence.

### #523 — "[P2] Class-level pytest fixtures are dropped during framework graph construction"
- **State:** CLOSED. **Classification: REGRESSION** (previously verified fixed; now reproducible again if the working tree is committed as-is).
- **Fix commit:** `516fe12d` ("fix: resolve #523 (class fixture scoping), #524 ..., #525 ...").
- **Original problem:** `extract_pytest_facts()` in `repo2graph/frameworks.py` didn't record the enclosing class on fixture records, so `_find_symbol(f["fn_name"], None, ...)` in `graph.py` couldn't match class-scoped fixture symbols (whose actual parent was the class), causing the fixture to be dropped entirely (no `is_fixture=True`, no `USES_FIXTURE` edges, breaking `--affected-tests` for class-scoped suites). The fix additionally required class fixtures to be visible only within their class and to shadow outer fixtures.
- **Verification method and result:** Read `git diff -- repo2graph/frameworks.py`. The committed fix at `516fe12d` built a `cls_qualname = f"{enclosing_class}.{cls_name}" if enclosing_class else cls_name"` and used that qualname for nested-class fixture registration/walk. **The current uncommitted working tree reverts this**: it drops the qualname construction and uses the bare `cls_name` again, exactly the pre-fix state. This is a verbatim revert of the fix for nested/qualified class scoping.
- **Confidence:** High (direct diff read against the issue's described root cause).

### #524 — "[P2] SqliteGraphStore fails in multi-threaded environments (MCP/ThreadPool) with sqlite3.ProgrammingError and leaks handles on Windows"
- **State:** CLOSED. **Classification: REGRESSION (partial).**
- **Fix commit:** `516fe12d`.
- **Original problem:** `sqlite3.connect(...)` lacked `check_same_thread=False`; no `__enter__`/`__exit__` on `SqliteGraphStore`/the `GraphStore` Protocol; `MemoryGraphStore` lacked `close()`/context-manager duck-typing; unclosed handles caused `PermissionError [WinError 32]` on Windows.
- **Verification method and result:** `git diff -- repo2graph/store.py`. The `check_same_thread=False` connect argument **is still present** (not reverted) — so the headline `sqlite3.ProgrammingError` symptom from the issue body would not reproduce. However, the working tree **does** revert two other parts of the same fix: (1) it removes `import threading` and the `self._lock = threading.Lock()` plus every `with self._lock:` guard around the ~12 `SqliteGraphStore` methods (`node`, `has`, `neighbours`, `iter_nodes`, `iter_edges`, `node_count`, `has_chunks`, `chunk_count`, `get_chunk`, `get_chunks_by_node`, `close`, the meta read) — removing the serialization that made concurrent access to the single shared connection safe beyond what `check_same_thread=False` alone guarantees; (2) it removes `__enter__`/`__exit__` from the `GraphStore` Protocol entirely, so any code relying on `with store:` / duck-typed context-manager usage (per the issue's own suggested fix item 2) no longer type-checks or works via the Protocol. The orchestrator independently confirmed `tests/test_store.py::test_sqlite_store_concurrent_thread_queries` still passes only because the lock was **moved into the test itself** (`git diff -- tests/test_store.py` shows `threading.Lock()` added inside the test, wrapping the query calls) — meaning the test no longer exercises the store's own thread-safety at all; it tests a lock the test file provides.
- **Confidence:** High. The regression is partial: the specific `sqlite3.ProgrammingError` cited in the issue text would not resurface (connect flag intact), but the lock removal and Protocol-method removal are direct reverts of the rest of the same commit, and the test was weakened to mask it.

### #525 — "[P3] graphdiff conftest matching uses .endswith() causing nested conftest path collisions"
- **State:** CLOSED. **Classification: Previously reported and verified fixed — NOT regressed.**
- **Fix commit:** `516fe12d`.
- **Verification method and result:** `git diff --stat` on the current working tree shows only `repo2graph/frameworks.py`, `repo2graph/graph.py`, `repo2graph/store.py`, and `tests/test_store.py` modified. **`repo2graph/graphdiff.py` is untouched** (no diff). The exact-match normalization fix for conftest path matching described in #525 is intact in the current working tree.
- **Confidence:** High (confirmed by absence of diff on the relevant file).

### #513 — "[P2] --affected-tests cannot see tests that consume a fixture by injection"
- **State:** CLOSED. **Classification: Previously reported and verified fixed — NOT regressed, but DEPENDENT on #523/#524's fixture-edge machinery.**
- **Fix commit:** `fcfece04` (separate from `516fe12d`; this issue predates and is foundational to #523's fix — #513 added the base `USES_FIXTURE` edge machinery and fixture parsing that #523 then had to extend for class scoping).
- **Verification method and result:** Grepped for `USES_FIXTURE`, `tested_symbol_fraction`, `repo_neighbours` — present across `graph.py`, `frameworks.py`, `query.py`, `tests/test_tests_edges.py`. The base fixture-injection edge mechanism is present. However, because #523's class-scoping layer on top of this machinery is now reverted, **class-scoped fixture consumption specifically** (the scenario #523 was filed to cover) is broken again even though module/conftest-level fixture consumption (#513's original scenario) is not.
- **Confidence:** High for #513 itself being intact; the interaction with #523's regression is the important nuance for the orchestrator.

### Regression test confirmation
`tests/test_framework_edges.py::test_pytest_nested_classes_and_shadowed_fixtures` (lines 569–611) builds a repo with `conftest.py` defining `value`, and `tests/test_nested.py` with `class TestOuter: class TestInner:` containing a fixture `value(self, value)` that *overrides* the outer conftest fixture (classic fixture-shadowing pattern), plus `test_inner(self, value)` consuming the class-scoped override. The test asserts a `USES_FIXTURE` edge from `sym:...::TestOuter.TestInner.value` → `sym:conftest.py::value` (the override depending on the shadowed outer fixture) and from `...::TestOuter.TestInner.test_inner` → `...::TestOuter.TestInner.value` (the test using the class fixture). Both assertions depend on (a) `frameworks.py` recording the full nested qualname `TestOuter.TestInner` so the symbol IDs line up, and (b) `graph.py`'s `base_visible` vs `visible` distinction so an override fixture can still see the shadowed outer one by name. Both mechanisms are the exact lines reverted in the current working tree (confirmed above). The orchestrator has independently run the suite and confirmed this test now fails. This agent did not re-run pytest (per instructions) but the static diff fully explains the failure mode: the reverted `graph.py` code collapses `target_fix = base_visible.get(p) if (parent is not None and p == fix["name"]) else visible.get(p)` down to a plain `visible.get(p)`, which cannot represent "the name is shadowed, but when the shadowing fixture's own body also uses that name, resolve to the thing it shadows" — the self-referential override case the test is built around.

### #514 — "[P3] Parse-worker memory is unbounded on macOS; only the wall clock applies" / #507 — "[P2] Parsepool worker memory is uncapped on Windows and macOS"
- **State:** both CLOSED. **Classification: Previously reported and verified fixed.**
- **Fix commit:** `fcfece04` (both resolved by the same commit — another batch-fix cluster).
- **Verification method and result:** Grepped `repo2graph/parsepool.py` for `WORKER_MEMORY_MB`, `GetProcessMemoryInfo`, `proc_pidinfo` — all present, matching the close-comment's described implementation (Darwin `libproc`/`ps` fallback, Linux `/proc/<pid>/statm`, Windows `GetProcessMemoryInfo`/Job Object cap).
- **Confidence:** Medium-high (presence confirmed via grep; did not trace full call graph or run `tests/test_parse_budget.py`).

## Regressions identified

| Issue | Was fixed by | Reverted in working tree? | Symptom if committed as-is |
|---|---|---|---|
| #523 (class fixture scoping) | `516fe12d` | **Yes** — `frameworks.py` qualname construction removed | Nested/qualified class fixtures mis-resolve again; shadowing broken; confirmed failing test `test_pytest_nested_classes_and_shadowed_fixtures` |
| #524 (sqlite concurrency & lifecycle) | `516fe12d` | **Partially** — `threading.Lock` and Protocol `__enter__`/`__exit__` removed; `check_same_thread=False` retained | Loses the store's own thread-safety serialization (now only safe by accident of SQLite's C-level read-only thread safety plus the test file's own lock, which doesn't protect production callers); any code depending on `with store:` typed against `GraphStore` breaks |
| #525 (conftest matching) | `516fe12d` | **No** — `graphdiff.py` untouched | None — this one remains correctly fixed |
| #513 (fixture-injection edges, base) | `fcfece04` | **No**, but class-scoped consumption on top of it (via #523) is broken | Module/conftest-level `--affected-tests` fixture tracing still works; class-scoped tracing does not |

**Verdict:** If the current uncommitted working tree is committed, #523 and #524 become **reproducible regressions** — #523 fully (confirmed by a failing assertion in the existing regression test suite), #524 partially (the specific exception in the issue title would not resurface, but the concurrency-safety guarantee and the Protocol-level context-manager API the issue asked for are both gone, and the test that was supposed to catch this was altered to no longer exercise the store's locking). #525 is unaffected and remains correctly fixed.

## Known-issue keyword/subsystem index (for orchestrator dedupe)

Use this to check "has this already been filed and closed" before raising a new finding. Counts are keyword hits across issue titles (272 issues, all closed).

| Keyword/subsystem | Issue-title hits | Representative issues |
|---|---|---|
| `mcp` | 25 | #506 (SSE transport), #218, #224, #212 (http layer under mcp) |
| `parse` | 25 | #231 (timeout), #507/#514 (worker memory), #171 (cpp fallback encoding) |
| `http` | 20 | #223, #224, #212, #187, #506 |
| `auth` / `jwt` / `jwks` | 19 + 5 + 1 | #176, #170, #227, #225, GHSA-f896-f643-87cf, GHSA-mqm8-mc66-wjvj |
| `git` | 17 | #191 (fetch missing ref), GIT_HARDENING_ARGS work in PR #476 |
| `cli` | 11 | #218, #188 |
| `rag` | 11 | #210 (answer model default), rag answer provider disambiguation |
| `audit` | 11 | #214, #220, #217 |
| `sqlite`/`store` | 5 + 4 | **#524**, #500–#503 (stale cache, read-only container crash, tmp-file race, non-regular-file crash) |
| `fixture`/`conftest` | 4 + 1 | **#523**, **#525**, #513 |
| `query` | 9 | #189 (per_hop), #211 (BM25 avgdl) |
| `export` | 9 | #193, #222 (UTF-8 chunking) |
| `cache` | 8 | parse cache keying (PR #476), #500 |
| `secret` | 7 | #413 (lowercase secret detection), redaction work |
| `windows` | 7 | #171 (UnicodeDecodeError), #507 (memory cap), handle leaks in #524 |
| `unicode` | 3 | #171, hidden-unicode handling in PR #476 |
| `graphdiff`/`affected-test` | 1 + 2 | **#525**, #513, #508 (semantic PR diffing feature) |

**Subsystems with the most historical bug volume:** MCP/HTTP transport layer, the tree-sitter parse/fallback path, and the auth/JWT/JWKS stack are the three biggest recurring sources of filed bugs and all three also produced published security advisories — this is the highest-risk area for new findings to check against first. The sqlite store (#500–#503, #524) is a second hotspot specifically around concurrency, read-only containers, and tmp-file races — **any new store-related finding should be checked against #500–#504 and #524 before being raised as new**, since this area has had five issues in six weeks.

## Recommended new issues (NOT filed)

### 1. Regression: #523/#524 fixes partially reverted in working tree (would resurrect closed bugs if committed)
- **Suggested title:** "Working-tree changes to frameworks.py/graph.py/store.py revert #523 (class fixture scoping) and partially revert #524 (sqlite store locking/Protocol)"
- **Body outline:** Describe the diff (qualname removal in `frameworks.py`; `base_visible`/`visible` collapse in `graph.py`; `threading.Lock` and `GraphStore.__enter__`/`__exit__` removal in `store.py`); cite failing test `tests/test_framework_edges.py::test_pytest_nested_classes_and_shadowed_fixtures`; note the test-file change in `tests/test_store.py` that moved the lock into the test, masking the loss of production-side locking; recommend reverting these three files to the `516fe12d` state before committing, or re-deriving whatever change motivated touching them without dropping the qualname/locking logic.
- **Suggested labels:** `bug`, `priority/P1` (confirmed regression of two previously-fixed bugs, one with a failing test already identified), `area/graph`, `area/query` (fixture resolution feeds `--affected-tests`).
- **Suggested priority:** P1.
- **Evidence:** `git diff -- repo2graph/frameworks.py repo2graph/graph.py repo2graph/store.py tests/test_store.py`; `tests/test_framework_edges.py:569-611`; issues #523, #524; commit `516fe12d`.

### 2. GraphStore Protocol context-manager contract silently dropped
- **Suggested title:** "GraphStore Protocol no longer declares __enter__/__exit__ — anything typed against the Protocol for `with store:` usage breaks silently"
- **Body outline:** Same root cause as #1 above but called out separately because it's an API-contract regression distinct from the concurrency-locking regression — any caller (including possibly MCP server code or CLI tooling) that does `with store_instance:` against a variable typed as `GraphStore` will fail `mypy`/structural typing even though `SqliteGraphStore` itself might still define the dunder methods (check whether it does — the diff only showed the Protocol declaration removed, not necessarily the concrete class's methods, which should be independently confirmed by whichever agent owns store.py review).
- **Suggested labels:** `bug`, `priority/P2`, `area/graph`.
- **Suggested priority:** P2 (folds into recommendation #1 if the orchestrator prefers a single combined issue).
- **Evidence:** `git diff -- repo2graph/store.py`, lines removing `def __enter__(self) -> GraphStore: ...` / `def __exit__(...) -> None: ...` from the `GraphStore` Protocol.

No other genuinely new/unresolved problems were identified in the time available for this investigation beyond the regression already flagged by the orchestrator; this agent's contribution is confirming and fully documenting its scope and mechanism rather than discovering an independent third issue.

## Limitations

- PR survey of the 58 closed-unmerged PRs was **sampled, not exhaustive** — 5–6 were spot-checked against `git log`/grep to confirm the "combined into a later merged PR" pattern; the remaining ~50 were not individually traced to a landing commit. It is possible (though this agent found no evidence of it) that a small number of these represent genuinely lost work.
- Deep per-issue investigation (full discussion read, fix-commit identification, working-tree verification) was performed for the 6 issues explicitly named in the task (#523, #524, #525, #513) plus #514/#507 as a spot-check of the batch-fix pattern. With 0 open issues and 272 closed issues, exhaustively deep-diving all 272 was not attempted within the available budget; the per-issue section above should be read as a deep dive of the highest-priority/most-recent subset, not the full history.
- Dependabot alerts and security advisories were read via `gh api` with no permission errors in this session; if a different token/session lacks `security_events` scope, that call will 403 — this was not the case here.
- `pytest` was not run (per instructions); the regression conclusion for #523 rests on static diff analysis plus the orchestrator's independently-reported failing-test result, not on this agent's own test execution.
- Discussions are confirmed empty/disabled; this agent cannot distinguish "disabled by repo setting" from "enabled but zero discussions ever created" from the API response alone (`gh api .../discussions` returning `[]` is consistent with either).
