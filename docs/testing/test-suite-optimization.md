# Test Suite Consolidation & CI Optimization

An audit of the whole suite (1,730 collected cases across 44 files), what was
consolidated, what was deliberately left alone, and why.

The short version: the duplication budget here was much smaller than the headline
count suggests, and almost all of the available *runtime* win was in one line of
production code and one missing dev dependency. Those were taken. The test count
barely moved, on purpose — see [Why the count barely
moved](#why-the-count-barely-moved).

---

## Baseline

Measured on this machine (16 cores, Windows) before any change:

```text
Total collected cases:   1,730  (1,718 pass, 12 skip)
Test functions:          1,153  (577 cases come from parametrize expansion)
Test files:                 44
Frameworks:              pytest 9.x + pytest-cov; no other runner
Full-suite runtime:      ~134s serial  (observed 130-158s across runs)
Slowest single file:     test_http_transport.py, 40.2s (21% of all file time)
Slowest single test:     test_file_limits.py::test_file_limits, 5.5s
Branch coverage:         87.21%  (fail_under = 80)
Known flaky tests:       none found
Skipped:                 12, all platform or optional-extra gates
```

### Test types

There is no unit/integration/e2e directory split, and adding one was rejected:
the suite is organised by the module or behaviour under test
(`test_auth.py`, `test_encoding.py`, `test_determinism.py`), which is the axis
people actually search along. The layers are present but interleaved:

| Layer | Roughly | Examples |
| :--- | :--- | :--- |
| Pure unit | ~70% | `test_auth.py` crypto, `test_encoding.py`, `test_schema.py` |
| Integration (real files, real git, real subprocess) | ~25% | `test_incremental.py`, `test_determinism.py`, `test_fetch_ref.py` |
| End-to-end (real server / real CLI process) | ~5% | `test_http_transport.py`, `test_compat.py`, `test_summary_script.py` |

No snapshot tests, no E2E browser layer, no factories, one `conftest.py`.

---

## Why the count barely moved

Three measurements set the ceiling before any editing began.

**1. There is no cross-file duplication.** Every test function in the suite was
parsed to an AST, normalised (docstrings, comments and whitespace stripped) and
compared pairwise across files. At a 0.85 similarity threshold: **zero pairs.**
Each file owns its domain and does not restate another file's assertions. The
usual big win in this kind of audit — the same behaviour tested at three layers —
is not available here.

**2. In-file duplication was 51 pairs, and most were legitimately distinct
inputs.** Of those, 23 were genuine banding candidates (consolidated below). The
rest differ in a way that matters: `..._crlf` variants of the same test exist
because CRLF-vs-LF is a live bug class in this repo, not because someone
copy-pasted.

**3. Two thirds of the "test count" is already the consolidated form.** 577 of
the 1,730 cases are `parametrize` rows. Parameterisation is what a consolidated
input table *looks like* — so most of the obvious consolidation had already been
done, and re-banding it cannot reduce the number pytest reports.

That last point is worth stating plainly, because it is the difference between
improving a suite and improving a metric:

> Collapsing nine near-identical test functions into one `parametrize` with nine
> rows removes eight function bodies and ~60 lines of duplication. It removes
> **zero** tests from the count — pytest still collects nine. The count only
> falls when a *case* is deleted, and a case should only be deleted when it is
> genuinely redundant.

So the numbers below report both, separately, and never present a banding as a
removal.

---

## Changes

### Duplicate cases removed — 2

Both were verified against the implementation, not just by name.

| Removed | Kept | Why it was a duplicate |
| :--- | :--- | :--- |
| `test_integrity.py::test_allows_dir_with_r2g_marker` | `test_ok_for_an_existing_index_dir` | Identical call. Differed only in naming the directory `idx` instead of `.r2g`, and `validate_outdir` never inspects the name. Its comment claimed it "populate[d] a marker so it's recognized" — it wrote no marker. |
| `test_mcp.py::test_serve_without_a_repo_still_preflights` | `test_serve_preflight_checks_index` | Byte-identical call and assertion. Its docstring claimed the "no repo to build from" condition, but the test it duplicated also passes no `repo`, so that was already the other test's condition. |

In both cases the surviving test absorbed the intent that made the duplicate look
distinct, so the reason the pair existed is still written down.

### Tests consolidated by parameterisation — 23 functions → 9

No case was dropped in any of these; the collected count is unchanged.

| File | Functions | → | New test | Rows |
| :--- | :---: | :-: | :--- | :---: |
| `test_auth.py` | 9 | → | `test_an_invalid_claim_is_refused` | 9 |
| `test_auth.py` | 6 | → | `test_rsa_verify_rejects` | 6 |
| `test_auth.py` | 3 | → | `test_a_jwk_missing_its_rsa_parameters_is_refused` | 3 |
| `test_auth.py` | 2 | → | `test_a_jwks_uri_off_the_issuers_origin_is_refused` | 2 |
| `test_http_transport.py` | 2 | → | `test_a_bad_static_credential_returns_401_and_does_not_run_the_tool` | 2 |
| `test_http_transport.py` | 3 | → | `test_a_bad_jwt_returns_401_and_does_not_run_the_tool` | 3 |
| `test_http_transport.py` | 2 | → | `test_a_rebinding_header_is_rejected` | 2 |
| `test_http_transport.py` | 2 | → | `test_auth_config_precedence_between_the_flag_and_the_env_var` | 2 |
| `test_compat.py` | 4 | → | `test_importing_a_module_pulls_in_no_optional_dependency` | 4 |
| `test_cache.py` | 2 | → | `test_zero_disables_the_cache` | 2 |
| `test_doctor.py` | 2 | → | `test_doctor_mcp_client_config_warns_on_an_unusable_target` | 2 |
| `test_tasks.py` | 2 | → | `test_run_writes_task_state_under_the_lock` | 2 |
| `test_summary_script.py` | 2 | → | `test_summary_omits_graph_delta_without_a_changelog` | 2 |

Every security rationale that lived in a deleted docstring was moved onto its
table row rather than dropped — the NaN/±Infinity reasoning on the `exp`/`nbf`
rows, the Bleichenbacher note on the padding row, the ISS-238 KeyError note on the
RSA-parameter rows, the RFC references on the JWKS rows. A row without its reason
is a row a future reader deletes.

**Two consolidations also strengthened assertions.** The wrong-issuer and
wrong-audience HTTP rows previously asserted only `status == 401`; as rows of
`test_a_bad_jwt_returns_401_and_does_not_run_the_tool` they now also assert the
tool never executed, which is the property that actually matters. The `ttl=0`
cache row now asserts `enabled is False` alongside the missing read.

### Diagnostics fixed — 1 loop unrolled (+2 cases)

`test_fetch_ref.py::test_iss237_parse_ref_rejects_empty_and_nul` looped over
three refs inside one test, so a failure on any of them reported under a single
name and the first failure hid the other two. Those refs became
`REJECTED_DEGENERATE` rows on the existing parametrize. This **adds two** to the
count and is still the right change.

They were kept out of the main `REJECTED` list deliberately: that list is also
driven through `clone()`, where every entry costs a subprocess-tripwire run, and
a degenerate ref only needs the parser.

### Duplicate setup extracted — 6 call sites

| Helper | File | Replaced |
| :--- | :--- | :--- |
| `_no_subprocess(monkeypatch)` | `test_fetch_ref.py` | 3 copies of an identical `tripwire` closure |
| `_record_subprocess(monkeypatch)` | `test_fetch_ref.py` | 3 copies of an identical `recorder` closure |
| `_auth_args(**over)` | `test_http_transport.py` | 3 copies of a 5-field `argparse.Namespace` |

These are deliberately small and local to their file. No shared global fixture
was introduced — the point was to stop repeating a closure, not to move setup
somewhere a reader has to go find it.

### Removed: nothing else

No test was deleted for being slow, short, simple, failing, or awkward. No test
was skipped, renamed out of discovery, or had assertions weakened. Coverage
thresholds were untouched (`fail_under = 80`, unchanged).

---

## What was deliberately NOT changed

### The 175-case encoding matrix stays

`test_encoding.py::test_encodable_output_is_always_writable` is 5 encodings × 7
error handlers × 5 characters = **175 cases, 10% of the entire suite**. It is the
single most tempting target in the repo and it was kept in full.

The reason is empirical. Which cells actually raise is not predictable from the
codec, so there is no slice that is safely redundant:

- `utf8` looks like the trivial row. It refuses a lone surrogate under both
  `strict` **and** `surrogateescape`.
- `cp932` accepts the arrow but not a surrogate. `latin-1` is the reverse.
- `surrogatepass` rescues a surrogate on the UTF codecs and raises on all four
  narrow ones.

The matrix costs ~0.2s of a 35s suite and pins the bug class `AGENTS.md` names as
the source of every historical regression in this repo (ISS-06/17/22/27). Thinning
it would have moved the headline number by 10% while deleting the most
load-bearing coverage in the suite. A comment now explains this at the parametrize
site so the next person to run this audit does not have to rediscover it.

### No unit/integration/e2e reorganisation

Moving ~1,700 tests into layer directories would produce a large, review-hostile
diff, break every `pytest tests/test_auth.py` in the docs and in developer muscle
memory, and buy nothing: the suite is already navigable by subject, and CI does
not need the split because it runs everything (see below).

### The ISS-numbered regression tests stay

A large share of the suite is named for the bug it pins (`test_iss237_*`,
`test_iss90_*`, `test_iss19_*`). These look like clutter and are the opposite: each
is a closed bug that a future refactor can reopen. None were consolidated across
issue boundaries, because "these two bugs had similar symptoms" is not a reason to
let one test cover both.

### The 12 skips stay

All 12 are environment gates, not disabled tests: Windows symlink privilege
(4), POSIX-only `fcntl`/path characters (3), the 1.x MCP SDK API (3), the real
embedder behind `R2G_TEST_REAL_EMBEDDER` (1), and an absent C++ preprocessor (1).
Each runs somewhere in the CI matrix.

---

## Runtime

The real win, and it was not in the tests.

### `HTTPTransport.stop()` was waiting on a poll interval

`test_http_transport.py` was 40.2s for 64 tests — 21% of all file runtime — and
almost none of it was work. `serve_forever()` was called without a
`poll_interval`, so it used socketserver's 0.5s default; `shutdown()` sets a flag
and then waits for the serving loop to notice it on its next `select()`. Every
one of the ~60 servers the file starts paid up to 0.5s to stop.

`SHUTDOWN_POLL_SECONDS = 0.05` (`repo2graph/http_server.py`) wakes the selector 20
times a second instead of twice:

```text
tests/test_http_transport.py:  40.2s → 13.9s   (-65%)
```

This is a production fix, not a test fix: that same 0.5s was the delay on Ctrl-C
and on every real `stop()`.

### The suite parallelises almost linearly

Nothing in the suite shares a database, a port, or a working directory —
`tmp_path` and `monkeypatch` throughout, ephemeral ports for servers. `pytest-xdist`
was added to the `dev` extra (and the lockfile) and the whole suite passes
unchanged under it:

```text
serial                    ~134s
-n auto (16 cores)          36s     (-73%)
-n 4    (CI-sized runner)   49s     (-63%)
-n auto + branch coverage   48s
```

Coverage combines correctly because `[tool.coverage.run]` already set
`parallel = true` and `concurrency = ["multiprocessing", "thread"]` for the
multiprocessing build path. No configuration change was needed.

`-n auto` also serves as a standing order-independence check: workers receive
tests in a different distribution every run, and three parallel runs produced
identical results.

---

## CI changes

### There was no duplicate execution to remove

The audit expected the usual finding — the same suite running in four workflows —
and did not find it. All 13 workflows were read:

| Workflow | Runs pytest? | Guard |
| :--- | :--- | :--- |
| `ci.yml` → `tests` | yes, 3 OS × 4 Python | `if: github.event_name != 'schedule'` |
| `ci.yml` → `drift` | yes, 2 OS × 2 Python, unlocked deps | `if: schedule \|\| workflow_dispatch` |
| `publish.yml` → `pypi` | yes, once | release only |
| other 10 | no | — |

`tests` and `drift` are mutually exclusive by construction, and they answer
different questions ("is this change wrong" vs "did upstream move"). `publish`
runs on a tag. Nothing was consolidated because nothing was duplicated.

### What changed

| Change | Where | Effect |
| :--- | :--- | :--- |
| `pytest -q` → `pytest -q -n auto` | `ci.yml` (`tests`, `drift`), `publish.yml` | ~60% off each of 12 matrix legs |
| `pytest-xdist>=3.5` added | `pyproject.toml` `dev`, `uv.lock` | makes the above installable under `--require-hashes` |
| `make test` → parallel, `make test-serial` added | `Makefile` | fast by default, debuggable on demand |

Job names were **not** changed, so required status checks keep matching. Branch
protection and repository settings were not touched.

Caching was already correct (`actions/setup-python` with `cache: pip`, a
`uv export --locked` + `--require-hashes` install). No retries exist anywhere in
the CI — there was no `retry: 3` to remove, and none was added.

### Test selection was considered and rejected

Skipping the suite on docs-only changes would save runner minutes, but this repo
has `tests/test_doc_consistency.py`, `test_i18n_consistency.py`,
`test_version_surfaces.py` and `test_output_schema.py`, all of which fail when a
*document* and the code disagree. A docs-only path filter would route changes
around the tests written specifically to catch docs-only mistakes. Not worth the
runner minutes.

---

## Testing strategy

What belongs where, for anyone adding tests.

**Unit** — the default. A test that needs no subprocess, no server and no git
repository belongs here. Assert literal values derived by hand from the fixture;
never a value the code under test computed (`AGENTS.md`: *tests must pin values,
not compare the implementation to itself*).

**Integration** — real files, real `git`, real `subprocess`. Use when the thing
under test *is* the interaction: incremental rebuild byte-equality, discovery
order, ref validation reaching an argv. Prefer `tmp_path` over any shared state.

**End-to-end** — a real server or a real CLI process. Keep to the wiring property
that no lower layer can show: that a refusal reaches the HTTP surface as a 401 and
the tool never ran; that a published artifact's flags exist. `test_auth.py` already
proves *why* a token is refused, so the HTTP test does not re-prove it.

**Parameterise when** the body is identical and only inputs and expected output
differ. Give every row an `id` — the id is the failure diagnostic. **Do not**
parameterise when rows need substantially different setup, or when a row is a
different business rule that deserves to fail under its own name.

**Prove a new regression test is a detector.** Revert the fix in the working copy,
watch the new test fail and the old ones pass, then restore. A test that has never
been seen to fail is a test of nothing.

---

## Before / after

```text
                         BEFORE        AFTER      DELTA
Collected cases           1,730        1,730          0
  ├─ duplicates removed                              -2
  └─ loop unrolled to rows                           +2
Test functions            1,153        1,123        -30   (-2.6%)
Test files                   44           44          0
Duplicate setup blocks        6            0         -6
Runtime, serial            ~134s        ~134s          0
Runtime, default           ~134s          36s       -73%   (make test, -n auto)
Runtime, CI-sized leg      ~134s          49s       -63%   (-n 4)
  └─ test_http_transport    40.2s        13.9s       -65%   (production fix)
Branch coverage           87.21%       87.21%      0.00
Flaky tests                    0            0          0
Skips                         12           12          0
```

**Coverage is unchanged to two decimals**, which is the expected result: two
removed cases were exact duplicates of surviving ones, and every banding kept all
of its rows.

### Honest read of the reduction

The test count did not meaningfully fall, and this document does not present a
2.6% function reduction as a victory. What the audit actually established:

- The suite had **2** genuinely redundant cases in 1,730. Both are gone.
- It had **23** redundant test *functions*, now 9 tables. Same coverage, ~200
  fewer lines, better failure ids.
- It had **one** real runtime defect, in production code, worth 26s.
- It had **no** cross-file duplication, no flaky tests, no disabled tests, no
  duplicate CI execution, and no unjustified snapshots — because it has none at
  all.

An aggressive deletion pass was available and was not taken. The 175-case
encoding matrix alone would have delivered a 10% headline reduction by removing
the coverage this project has regressed against four times.

---

## Potential future improvements

Left undone because each needs a decision that is not a test-suite decision.

1. **Trim the CI matrix.** 3 OS × 4 Python = 12 legs run the full suite. Full
   coverage on Linux plus edge Pythons (3.10, 3.13) on macOS/Windows would cut it
   to ~8 while keeping the `windows-latest` leg `AGENTS.md` requires. This is a
   support-policy call, not a cleanup.
2. **Split `test_repo2graph.py`** (191 functions, 228 cases). It is the catch-all
   and the only file whose name does not say what it tests. Splitting along the
   module boundaries it already groups by would help navigation, at the cost of a
   large diff and stale references in docs and issues.
3. **`test_file_limits.py::test_file_limits`, 5.5s** — the slowest single test,
   generating large files to hit a byte cap. A smaller synthetic cap injected via
   config would make it near-instant, but that trades a real limit for a mocked
   one.
4. **Group-scope the `mini_index` fixtures.** Several files rebuild a small index
   per test. `scope="module"` with a copy-on-write `tmp_path` would save a few
   seconds but weakens isolation — worth it only if measured, and `-n auto`
   already bought more than this would.
5. **Drop `pytest-cov` for `coverage run -m pytest`.** Marginal, and would
   complicate the xdist + parallel-combine path that currently works untouched.
