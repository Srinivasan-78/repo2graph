# Build State

Status: DONE
Iteration: 1
Scenario: refactor
Baseline: 81519d6a3943ac44a72d200279efecb45820ec9a
Started: 2026-09-09

## Request

check for issues loop it through everything, use subagents to simplify and spread the work and also list out all issues ina table

_Scenario call:_ refactor — the ask is a whole-codebase issue audit followed by
fixes that must preserve existing behavior (existing test suite is the safety
net). Issues found may include real bugs; the loop adapts per finding. The
required deliverable is a table of every issue found.

## Plan

### Goal

Audit every source file in repo2graph (11 modules + `__init__`), the test suite,
`action.yml` and the five workflows; record every defect in a single numbered
master table; then fix the High and Medium severity items without changing any
observable output the existing suite pins. The audit is done and is recorded
below. Three of the findings are confirmed real bugs reproduced at the
interpreter (ISS-06, ISS-22, ISS-27), the rest are read-verified. Fixes must
keep the public artifact contract intact: node/edge id grammar, the
`human/` + `agent/` split, chunk field names, manifest keys and CLI output JSON
are all asserted by tests and documented in the README, so they do not move.

### Non-goals

- No new features (no file-scope call edges, no new languages, no new formats).
- No dependency additions, no NetworkX/numpy, no lint tooling adoption this loop.
- No changes to the HTML template's look, controls or layout algorithm.
- No changes to the authormark header blocks (repo rule: they move with the file
  and are refreshed with `node .authormark/authormark.mjs stamp <file>`, never
  deleted). Every file touched this loop must be re-stamped.
- Low-severity items are catalogued but explicitly deferred (see Partition).

### Baseline

`python -m pytest -q` -> **53 passed, 2 skipped in 2.09s** (the 2 skips are the
networkx-gated GraphML tests). This is the behaviour-preservation net.

---

### MASTER ISSUE TABLE

| ID | File:line | Sev | Category | Description | Proposed fix |
|---|---|---|---|---|---|
| ISS-01 | parse.py:35-36, 225 | Med | dead-code | `Symbol.start_byte` / `end_byte` are populated for every symbol and never read anywhere; they also inflate what the process pool pickles back per file. | Delete both fields and the two constructor args. |
| ISS-02 | parse.py:49, 186, 191, 209, 239 | Med | dead-code | `ParsedFile.file_calls` accumulates every call made at file scope; `graph.build` never reads it, so the data is computed and thrown away (file-scope calls produce no edge at all). | Drop the field and append only when `owner is not None`. Backlog the actual feature (file-scope CALLS edges). |
| ISS-03 | parse.py:110 | Low | bug | `_text(...).strip("\"'\n ")` strips *any* leading/trailing quote or space, so a docstring that legitimately begins or ends with `'` or `"` loses characters. | Strip the delimiter run only (`removeprefix`/`removesuffix` of the detected quote style). |
| ISS-04 | parse.py:24 | Low | bug | Bare `except Exception` in `parser_for` swallows every grammar-load failure, including a broken install, and reports it as "language unsupported". | Narrow to the loader's error, or count it into `stats["grammar_errors"]`. |
| ISS-05 | parse.py:97 | Low | bug | `txt.strip("!&* \n\t")` strips `*` and `&` from both ends, so a C callee legitimately named `x_` style with trailing symbols, or a Rust macro `foo!`, can be renamed. | Strip only the leading pointer/deref run and a single trailing `!`. |
| **ISS-06** | graph.py:361-378 | **High** | bug/encoding | `add_cochange` runs git with `text=True`, which decodes with the *locale* encoding (cp1252 on Windows) -> `UnicodeDecodeError` on a non-ASCII path; and git's default `core.quotepath=true` returns `"caf\303\251.py"`, which never matches `file_index`, so CO_CHANGE edges silently vanish for any non-ASCII filename. Same bug class the repo already fixed in `walker._git_files`. | Drop `text=True`, add `-c core.quotepath=false`, decode `stdout` as `utf8`/`surrogateescape` exactly as `_git_files` does. |
| ISS-07 | graph.py:203-209 | Med | bug | The parallel-parse fallback catches only `OSError, ValueError`. `BrokenProcessPool`, a worker `ImportError` or a pickling `TypeError` propagate and abort the whole build instead of falling back to the serial path the comment promises. | Catch `Exception` (or add `BrokenProcessPool`/`PicklingError`) and fall back to serial. |
| ISS-08 | graph.py:133 | Low | simplify | `(ctx or {}).get("go_module")` — `ctx` was already normalised to a dict at line 94. | `ctx.get("go_module")`. |
| ISS-09 | graph.py:90-95 | Low | simplify | `resolve_import` rebuilds `path_index` whenever `ctx` lacks `by_name`; every production call site passes a full ctx, so the branch exists only for three tests. | Make `ctx` required and have the tests pass `path_index(files)`. |
| ISS-10 | graph.py:341, 351-353 | Low | bug | `out` is a `defaultdict(list)`; `len(out[nid])` in the sort key and `out[stack.pop()]` in `_reach` insert an empty list for every node touched, growing the dict during the ranking pass. | Use `out.get(nid, ())` in both places. |
| ISS-11 | graph.py:33 | Low | bug | `add_node`'s merge filter `if v not in (None, "", [])` also drops a legitimate `0` or `False` on re-add (`0 == False`, and a future `count=0`/`external=False` attribute would be discarded). | Filter on `v is not None and v != ""` plus an explicit empty-list check. |
| ISS-12 | graph.py:374 | Low | bug | Commits touching more than 25 files are skipped for CO_CHANGE with no counter, so a repo of big merges reports zero co-change and looks like a bug. | Record `stats["cochange_commits_skipped"]`. |
| ISS-13 | walker.py:38-44 vs 22-35 | Med | bug | The `os.walk` fallback drops every dot-directory; the git path does not. So `.github/**` (and any dot-dir source) is indexed in a git checkout and invisible in a plain folder — discovery differs by whether `.git` exists. | Apply one filter inside `discover()` for both sources; pick the git behaviour (keep dot-dirs, honour `DEFAULT_SKIP_DIRS`) since that is what ships today for the common case. |
| ISS-14 | walker.py:91-96, 125 | Low | simplify | `is_binary` opens and reads every candidate a second time after `lstat`; the same bytes are read again moments later by `_read_and_parse`. | Fold the NUL check into the single read in `graph._read_and_parse`, or leave and document the cost. |
| ISS-15 | walker.py:26-29 | Low | bug | The 60s `git ls-files` timeout degrades silently to `os.walk`, which ignores `.gitignore` — the output then quietly includes build junk with no signal. | Record `stats["discovery"] = "git" \| "walk"` and print it in the build report. |
| ISS-16 | fetch.py:34-43 | Med | security | The token is interpolated into the clone URL and passed as an argv element, so it is visible in `ps`/`/proc` to every other user on the machine (and in CI to any co-running step). | Clone the clean URL and pass credentials out of band: `git -c http.extraheader="AUTHORIZATION: basic <b64>"` fed through `GIT_CONFIG_*` env, or `GIT_ASKPASS`. |
| ISS-17 | fetch.py:43, 53, 58-59 | Med | bug/encoding | All three subprocess calls use `text=True` -> locale decoding. On Windows a non-UTF-8 byte in git's stderr raises `UnicodeDecodeError` *inside the error handler*, replacing a clear "clone failed" with a decode traceback. | Pass `encoding="utf8", errors="replace"` on every call. |
| ISS-18 | fetch.py:43, 50-53, 58 | Med | bug | No `timeout` on `git clone` / `remote set-url` / `rev-parse`. A hung network call blocks the CLI (and a CI job) forever; `walker` and `add_cochange` both already set one. | Add a `timeout=` (clone generous, e.g. 900s) and convert `TimeoutExpired` into the same `RuntimeError`. |
| ISS-19 | fetch.py:15-26, 36 | Med | security | `parse_spec` accepts path-traversal and option-like components: verified `parse_spec("owner/..") -> ("owner","..")`, so `target = dest/".."` clones *over the parent of the temp dir*; `parse_spec("-x/-y")` is also accepted. | Reject any component equal to `.`/`..`, starting with `-`, or empty, before building the URL or the target path. |
| ISS-20 | fetch.py:45 | Low | simplify | `proc.stderr.strip().replace(token or "\0", "***")` uses a NUL sentinel to mean "no token", and misses a URL-encoded token. | `if token: msg = msg.replace(token, "***")`. |
| ISS-21 | fetch.py:76-79 | Low | bug | With `--keep-clone DIR` pointing at a directory that already holds the clone, `git clone` fails with "already exists and is not an empty directory" and the user gets that raw. | Detect an existing checkout and either reuse or say so plainly. |
| **ISS-22** | chunks.py:67, 117-125 | **High** | bug/encoding | `src.splitlines()` breaks on U+2028, U+2029, U+0085, \x0b and \x0c; tree-sitter's row numbers do not. Any file containing one of those characters has every later symbol's chunk text sliced from the wrong lines. **Reproduced:** a file whose line 1 contains U+2028 yields `"\ndef f():"` as the body of `f` instead of the function. The residual-chunk `keep` computation is mis-sliced the same way. `query.read_jsonl` already carries a comment about exactly this character class, so the hazard was known but not fixed here. | Replace both `splitlines()` calls with `src.split("\n")` (dropping a trailing `"\r"` per line) so text indexing matches the parser's row numbering. |
| ISS-23 | chunks.py:141-145 | Low | bug | File and residual chunks always report `start_line: 1` and `end_line: <file length>`, even though a residual chunk holds only the lines no symbol claimed, and even for part 2+ of a split. | Emit the real span, or set them to `None` and say so in `chunk_fields`. |
| ISS-24 | chunks.py:99 vs 141 | Low | simplify | Symbol chunk 0 gets id `nid`; file chunk 0 gets `nid#0`. Two id schemes for one field. | Use `f"{nid}#{i}"` uniformly, or `nid` for i==0 uniformly — pick one and state it in the manifest. |
| ISS-25 | chunks.py:35, 108-109 | Low | dead-code | `include_files` is never passed `False` by any caller or test. | Remove the parameter (or expose it as `--no-file-chunks`; not this loop). |
| ISS-26 | chunks.py:70-75, 132-133 | Low | simplify | Caps 12/12/12/6/20/40 are inline magic numbers repeated across two blocks. | Hoist to named module constants next to `MAX_CHARS`. |
| ISS-27 | export.py:256-260, 283 | Med | bug | GraphML data values are written verbatim. A source file containing a C0 control character (\x0b, \x0c, \x00) inside a docstring or signature produces a GraphML file that **no XML parser can read back** — verified: `ElementTree` writes `<a>bad \x0c char</a>` and `ET.fromstring` on it raises `ParseError: not well-formed`. The two GraphML tests skip when networkx is absent, so CI would not catch it. | Sanitise in `_flat`/`add_data`: drop characters outside the XML 1.0 legal set (tab, LF, CR, >=0x20, minus surrogates). |
| ISS-28 | export.py:21-24 | Low | bug/encoding | `write_jsonl` opens without `newline=`, so on Windows every record is terminated `\r\n` while `query.read_jsonl` deliberately opens `newline="\n"` — each parsed line then carries a stray trailing `\r`. Tolerated by `json.loads` today; a byte-offset or checksum reader would not be. | `open(path, "w", encoding="utf8", newline="\n")`, mirroring the read side. |
| ISS-29 | export.py:217-220 vs viz.py:37-44 | Low | simplify | `_graphml_label` and `viz._trim`/`node_label` are the same function with different limits, duplicated across modules. | One `trim(text, limit)` helper; both callers pass their constant. |
| ISS-30 | export.py:459-460 | Low | bug | `chunks.jsonl` is written whenever `chunks is not None`, regardless of `--formats`; `written`/`manifest.files` then advertise a file the requested format list never asked for. (A test even pins this quirk for `--formats overview`.) | Keep the behaviour but document it in `FILE_NOTES`, or gate on `"jsonl" in formats` and update the two tests — decide in IMPLEMENT, default to documenting. |
| ISS-31 | export.py:47, 68-69, 112, 224-227, 338 | Low | simplify | `defaultdict`, `math`, `random`, `Counter` and `xml.etree` are imported *inside* functions, several of them per call in the layout hot path. | Move to module scope. |
| ISS-32 | export.py:333 | Low | bug | `graph.cypher` is written with no trailing newline; some `cypher-shell -f` versions drop the final statement. | Append `"\n"`. |
| ISS-33 | viz.py:108-110 | Low | bug | The title is substituted before the data blob, so a repo name containing the literal string `__R2G_DATA__` is replaced by the whole JSON payload inside `<title>` and `<h1>`. Repo names are attacker-influenced in `repo2graph github`. | Substitute the data placeholder first, or do a single-pass `re.sub` with a mapping. |
| ISS-34 | viz.py:47, 58 | Low | bug | `--viz-nodes 0` silently disables the cap and draws every node (a 20k-node page). Undocumented in `--help` and the README. | Treat `<=0` as "no cap" explicitly in the help text, or reject it. |
| ISS-35 | viz.py:435-443 | Low | bug | `relayout()` runs the O(n^2) settle loop (~300 iterations) synchronously on load; at `--viz-nodes 2000` the page is frozen for seconds with no indication. | Cap the settle iterations by node count, or yield to a frame after N steps. |
| ISS-36 | viz.py:601-605 | Low | bug | `pointerup` never calls `releasePointerCapture`, so the capture taken in `startDrag`/pan persists on the svg. | Release it in the `pointerup`/`pointercancel` handlers. |
| ISS-37 | query.py:103-123 | Low | bug | The char budget is tested *after* a chunk is appended, so `retrieve` can overshoot `budget_chars` by one whole chunk; the expansion loop below has no `k` bound at all and can append far more chunks than the caller asked for. | Check the budget before appending; bound the expansion pass too. |
| ISS-38 | query.py:105-110 | Low | simplify | `seen_nodes` is a list used for `in` membership. | Keep an ordered list plus a set. |
| ISS-39 | query.py:76 | Low | simplify | `1.5`, `0.25`, `0.75`, `400` are unnamed BM25 constants in the scoring expression. | Name them (`K1`, `B`, `AVG_LEN`) with a one-line comment. |
| ISS-40 | cli.py:40 | Low | bug | `len(chunks) if chunks else 0` cannot distinguish `--no-chunks` from a repo that produced zero chunks. | `len(chunks) if chunks is not None else 0`. |
| ISS-41 | cli.py:110-146 | Low | simplify | The `build` and `github` parsers repeat eight identical arguments verbatim. | Extract a shared `parent=` parser. |
| ISS-42 | __init__.py:11 vs pyproject.toml:8 | Low | simplify | `__version__ = "0.1.0"` duplicates the packaging version with nothing keeping them in step. | `__version__ = importlib.metadata.version("repo2graph")` with a fallback. |
| ISS-43 | pyproject.toml:29-30 | Low | test-gap | No lint or typecheck configuration at all, so the VERIFY phase has nothing to run beyond pytest. | Backlog: add ruff config; out of scope this loop (non-goal). |
| **ISS-44** | .github/workflows/index-repo.yml:48 | **High** | security | `${{ inputs.repo }}` is interpolated straight into a `run:` script — GitHub substitutes it *before* bash parses the line, so a value containing `"; <cmd>; #` executes with `contents: write` and `secrets.TARGET_REPO_TOKEN` in scope. This is precisely the pattern `action.yml:100-102` documents as forbidden, so the repo's own convention is violated. | Move the input into `env:` and reference `"$R2G_REPO"` inside the script, exactly as `action.yml` does. |
| ISS-45 | .github/workflows/ci.yml:21-25 | Med | test-gap | The matrix is `ubuntu-latest` only, yet every historical regression in this repo (and ISS-06/22/28 above) is a Windows encoding bug. CI structurally cannot catch the bug class it keeps shipping. | Add `windows-latest` to the `os` matrix for the `tests` job. |
| ISS-46 | action.yml:132-133 | Low | bug | `summary.json` is written *inside* `$R2G_OUT`, so it ends up in the uploaded artifact and is force-pushed to the `graph` branch, while `manifest.json` never mentions it. | Write it to `$RUNNER_TEMP/summary.json`. |
| ISS-47 | action.yml:81, 157 | Low | security | The composite action uses floating tags (`actions/setup-python@v7`, `actions/upload-artifact@v7`) while every workflow in `.github/workflows` pins a full SHA — inconsistent supply-chain posture in the file consumers actually run. | Pin both to SHAs with a version comment. |
| ISS-48 | README.md:425-429 vs self-index.yml | Low | bug | The README prints a `schedule: cron "0 4 * * 1"` block as "the copy this project runs on itself"; the real `self-index.yml` has no `schedule:` trigger. | Make the README block match the file (or add the schedule). |
| ISS-49 | .github/workflows/dependabot-automerge.yml:21 | Low | security | `${{ github.event.pull_request.html_url }}` interpolated into `run:`. Low real risk (the field is GitHub-generated and the job is gated on the dependabot actor) but it is the same anti-pattern as ISS-44. | Pass through `env:`. |
| ISS-50 | tests/test_repo2graph.py:318-329 | Med | test-gap | `test_index_survives_unicode_line_separators` asserts only that *a* chunk exists for the U+2028 file — it never inspects the chunk text, which is why ISS-22 has been silently shipping. | Extend it to assert the chunk body of `uses_sep` actually contains `return MSG`. |
| ISS-51 | tests/test_repo2graph.py:240-252, 442-459 | Med | test-gap | No CO_CHANGE test with a non-ASCII filename (ISS-06); the two GraphML tests are `importorskip("networkx")` so a GraphML regression (ISS-27) passes CI whenever networkx is absent — it is not in the `dev` extra, so it is absent by default. | Add a non-ASCII CO_CHANGE test; add a stdlib-only `ET.parse` round-trip test that does not skip. |
| ISS-52 | (no file) | Low | test-gap | `repo2graph/fetch.py` has zero tests: `parse_spec`, the clone argv construction and the token redaction are entirely uncovered. | Unit-test `parse_spec` (incl. the ISS-19 rejections) and the argv builder with a fake `subprocess.run`. |
| ISS-53 | graph.py:192-209 | Low | test-gap | The parallel parse path (>=64 files) never runs in the suite, so the README's "the result is exactly the same either way" claim is unverified. | Add a test that builds a 70-file tmp repo with `jobs=1` and `jobs=2` and compares node ids and edge triples. |

**Counts:** 53 issues — 3 High, 12 Med, 38 Low.

---

### Partition (scope for this loop)

The surface is too large to land 53 fixes in one reviewable pass, so:

**In scope (High + Med, 15 items):** ISS-01, ISS-02, ISS-06, ISS-07, ISS-13,
ISS-16, ISS-17, ISS-18, ISS-19, ISS-22, ISS-27, ISS-44, ISS-45, ISS-50, ISS-51.

**Backlog (all 38 Low):** ISS-03, 04, 05, 08, 09, 10, 11, 12, 14, 15, 20, 21,
23, 24, 25, 26, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
46, 47, 48, 49, 52, 53. IMPROVE may pull in the trivially safe one-liners
(ISS-08, ISS-10, ISS-20, ISS-32, ISS-40) if the loop is green and quiet.

---

### Ordered tasks, grouped by file

**T1 — `repo2graph/chunks.py` (ISS-22)** — highest value, do first.
1. Add a module-level `_lines(src)` helper returning `src.split("\n")` with a
   trailing `"\r"` stripped from each line.
2. Use it at line 67 (symbol body) and line 118 (residual `keep` computation).
   Nothing else in the file may change.

**T2 — `repo2graph/graph.py` (ISS-06, ISS-07)**
1. `add_cochange`: drop `text=True`, add `-c core.quotepath=false` before `log`,
   decode `out.stdout` with `.decode("utf8", "surrogateescape")`. Keep the
   existing `returncode`/exception guards and the 120s timeout.
2. `parse_all`: widen the fallback `except` to also catch `BrokenProcessPool`
   and pickling failures, keeping the serial fallback.

**T3 — `repo2graph/export.py` (ISS-27)**
1. Add `_xml_safe(text)` dropping characters illegal in XML 1.0.
2. Apply it in `add_data` (values) and to `NodeLabel` text. Ids are already
   constrained; leave them unless a test proves otherwise.

**T4 — `repo2graph/fetch.py` (ISS-16, ISS-17, ISS-18, ISS-19)**
1. Harden `parse_spec`: reject components that are empty, `.`, `..`, or begin
   with `-`; raise the existing `ValueError` with the same message shape.
2. Clone the token-free URL; supply the credential via an `http.extraheader`
   config passed through the environment, not argv. Keep the post-clone
   `remote set-url` (now a no-op safety net) or drop it if the URL is clean.
3. Add `encoding="utf8", errors="replace"` and a `timeout=` to all three
   subprocess calls; map `TimeoutExpired` to `RuntimeError("git clone timed out")`.

**T5 — `repo2graph/parse.py` (ISS-01, ISS-02)**
1. Remove `Symbol.start_byte` / `end_byte` and their constructor args.
2. Remove `ParsedFile.file_calls`; append a callee only when `owner is not None`.

**T6 — `repo2graph/walker.py` (ISS-13)**
1. Move the dot-directory / `DEFAULT_SKIP_DIRS` decision into `discover()` so the
   git and `os.walk` sources yield the same set. Keep today's git behaviour
   (dot-dirs are indexed) as the single answer — `test_resolve_import_keeps_dot_directories`
   documents that `.github/...` files are expected to be present.

**T7 — `.github/workflows/index-repo.yml` (ISS-44)**
1. Add `env: R2G_REPO: ${{ inputs.repo }}` to the "Compute slug" step and use
   `"$R2G_REPO"` in the `printf`. No other step changes.

**T8 — `.github/workflows/ci.yml` (ISS-45)**
1. Add an `os: [ubuntu-latest, windows-latest]` dimension to the `tests` matrix
   and `runs-on: ${{ matrix.os }}`. Leave the `action` job ubuntu-only
   (composite action, bash-specific).

**T9 — `tests/test_repo2graph.py` (ISS-50, ISS-51)** — owned by TEST, listed for
ordering only.

**T10 — authormark re-stamp**
Run `node .authormark/authormark.mjs stamp <file>` for every file touched by
T1-T8. Never delete or reorder a header block.

---

### Acceptance criteria

Each is a checkable statement. AC-1..AC-11 are behaviour changes; AC-12..AC-16
are preservation and hygiene.

1. **(ISS-22)** For a Python file whose first line contains U+2028, the chunk
   whose `node_id` is the symbol defined after it contains that symbol's body:
   given `'MSG = "a b"\n\ndef uses_sep():\n    return MSG\n'`, the chunk for
   `sym:<path>::uses_sep` has `"return MSG"` in its `text` and does not contain
   the string `MSG = "a`.
2. **(ISS-22)** The same file's `file_residual` chunk, when one is produced,
   contains the `MSG = ` assignment line and not the body of `uses_sep`.
3. **(ISS-06)** `build(repo, git_history=10)` on a git repo containing two files
   named with non-ASCII characters (e.g. `café.py`, `naïve.py`) committed
   together three times yields a `CO_CHANGE` edge between
   `file:café.py` and `file:naïve.py`.
4. **(ISS-06)** `add_cochange` never raises `UnicodeDecodeError`: the same test
   passes with the process locale left at its platform default (asserted by the
   test simply completing on the Windows CI leg).
5. **(ISS-27)** After `build --formats graphml` on a repo whose source contains a
   `\x0c` inside a docstring, `xml.etree.ElementTree.parse(graph.graphml)`
   succeeds (no `importorskip`, stdlib only) and the file contains no character
   in the ranges illegal for XML 1.0.
6. **(ISS-19)** `parse_spec("owner/..")`, `parse_spec("../evil")`,
   `parse_spec("-x/-y")` and `parse_spec("owner/")` each raise `ValueError`;
   `parse_spec("owner/repo")`, `parse_spec("https://github.com/owner/repo")` and
   `parse_spec("git@github.com:owner/repo.git")` all still return
   `("owner", "repo")`.
7. **(ISS-16)** No element of the argv list passed to `subprocess.run` by
   `fetch.clone` contains the token string when a token is supplied (asserted by
   monkeypatching `subprocess.run` and inspecting the recorded call).
8. **(ISS-18)** Every `subprocess.run` call in `fetch.py` is invoked with a
   `timeout` keyword (asserted by the same recording fixture).
9. **(ISS-13)** `discover()` returns the identical set of relative paths for the
   same directory tree whether or not it is a git checkout (build the tree, run
   `discover`, `git init`+`git add -A`, run `discover` again, compare sets).
10. **(ISS-07)** `parse_all` returns the serial result when the process pool
    raises `BrokenProcessPool` (monkeypatch `ProcessPoolExecutor` to raise), and
    the returned list equals the `jobs=1` result.
11. **(ISS-01, ISS-02)** `repo2graph.parse.Symbol` has no `start_byte`/`end_byte`
    field and `ParsedFile` has no `file_calls` field
    (`dataclasses.fields(...)` name check), and the full suite is still green.
12. **(preservation)** `python -m pytest -q` reports **at least 53 passed** with
    **0 failed**; no pre-existing test is modified except
    `test_index_survives_unicode_line_separators` (ISS-50, strengthened only).
13. **(preservation)** For the `sample_repo` fixture, the set of node ids, the
    set of `(src, dst, type)` edge triples and the set of chunk ids produced by
    `build` + `build_chunks` are byte-identical before and after the change
    (characterization snapshot, see test strategy).
14. **(ISS-44)** `.github/workflows/index-repo.yml` contains no `${{ inputs.` or
    `${{ github.event.` expression inside any `run:` block; the repo slug is
    computed from `"$R2G_REPO"`.
15. **(ISS-45)** `.github/workflows/ci.yml` `tests` job runs on both
    `ubuntu-latest` and `windows-latest` across both Python versions.
16. **(repo rule)** Every source file modified this loop still carries its
    `@authormark v1` block with a fingerprint refreshed via
    `node .authormark/authormark.mjs stamp <file>`; `authormark check` passes.

---

### Test strategy

**Runner:** `python -m pytest -q` from the repo root (config lives in
`pyproject.toml`, `testpaths = ["tests"]`). Single file: `tests/test_repo2graph.py`.
No new dependencies — stdlib + pytest only. `networkx` must stay optional, so no
new test may depend on it.

**Level 1 — characterization (behaviour preservation, write first).**
A new `test_refactor_preserves_graph_shape` using the existing `sample_repo`
fixture: build the graph, and assert against literal expected collections
committed in the test — the sorted node-id list, the sorted `(src, dst, type)`
triple list, and the sorted chunk-id list. Generate those literals from the
current `main` build before any source change, so the test passes at HEAD and
locks AC-13. This is the net for T5 (dataclass field removal), T6 (walker
unification) and T2's decode change.

**Level 2 — targeted regression, one per fixed bug.** Each new test names its
issue id in a comment.
- ISS-22 -> two tests, AC-1 and AC-2, driving `build` + `build_chunks` on a
  tmp repo with a U+2028 file. Must fail at HEAD with the body text sliced from
  the wrong lines (the observed failure is `"\ndef f():"` in place of the body),
  not with an import or fixture error.
- ISS-50 -> strengthen the existing U+2028 query test with the body assertion.
- ISS-06 -> a git-fixture test mirroring `test_cochange_edges_from_git_history`
  but with `café.py` / `naïve.py`, written with `encoding="utf-8"`. Must fail at
  HEAD by the edge being absent (or by `UnicodeDecodeError` on Windows).
- ISS-27 -> build a repo whose docstring holds `\x0c`, export graphml, then
  `ET.parse` it. Stdlib only, never skipped. Fails at HEAD with `ParseError`.
- ISS-19 -> a table-driven `parse_spec` test (AC-6). Fails at HEAD because the
  traversal specs are accepted.
- ISS-16/ISS-18 -> a `monkeypatch`-based fixture that replaces
  `repo2graph.fetch.subprocess.run` with a recorder returning `returncode=0`,
  then asserts on the recorded argv and kwargs (AC-7, AC-8). Never touches the
  network. This is also the first coverage `fetch.py` has ever had.
- ISS-13 -> the git-vs-walk equality test (AC-9). Skips if `git` is unavailable.
- ISS-07 -> monkeypatch `concurrent.futures.ProcessPoolExecutor` to raise
  `BrokenProcessPool` on `__enter__`, assert `parse_all` still returns the
  serial result (AC-10).
- ISS-01/02 -> a `dataclasses.fields` assertion (AC-11).

**Level 3 — config assertions.** AC-14 and AC-15 read the two workflow YAML
files as text and assert on their content (no PyYAML dependency: a regex for
`${{` inside `run:` blocks and a substring check for `windows-latest`). Cheap,
and they keep the injection fix from silently regressing.

**What to mock:** only `subprocess.run` inside `fetch.py`, and
`ProcessPoolExecutor`. Everything else runs for real against `tmp_path` — git
is already a hard test dependency in this suite (`test_discover_finds_non_ascii_filenames`,
`test_cochange_edges_from_git_history`), so real git invocations are in keeping
with the conventions here.

**"Fails for the right reason":** every new test must be run at HEAD before the
fix and produce an *assertion* failure (wrong chunk text, missing CO_CHANGE
edge, `ET.ParseError`, `parse_spec` returning a tuple instead of raising) — not
`ImportError`, `AttributeError`, `fixture not found`, or a skip. TEST must paste
that output. The characterization test (Level 1) is the exception: it must
**pass** at HEAD, and its value is that it must keep passing afterwards.

---

### Risks

1. **Characterization snapshots are brittle across environments.** Node ids
   include `repo:<root.name>`, and `tmp_path` names differ per run — the
   snapshot must be normalised (compare relative ids only, drop the `repo:` node
   or compare its shape) or the test will fail for the wrong reason on CI.
2. **ISS-13 (walker unification) can change what a non-git build indexes.**
   Unifying on the git behaviour means a plain-folder build starts including
   `.github/**` and other dot-dirs, which shifts node counts for that path.
   Nothing in the suite pins it, but it is a real observable change — call it
   out in `## Implement`, and if the characterization snapshot moves, stop and
   loop back rather than re-baselining silently.
3. **ISS-16 (token out of argv)** is the riskiest fix: `http.extraheader` via
   env differs across git versions, and there is no integration test that can
   actually authenticate. Mitigation: keep the change minimal, assert only on
   argv (AC-7), and if the mechanism proves fragile, fall back to
   `GIT_ASKPASS` — but do not ship a version that stops cloning public repos
   (`test`-covered only indirectly, so verify manually in VERIFY with
   `repo2graph github psf/requests --max-files 5`).
4. **ISS-45 (Windows CI) may go red immediately** on issues this loop does not
   fix — that is the point of adding it, but it could block the merge. If a
   Windows-only failure appears outside the in-scope list, record it as a new
   ISS-xx and mark the leg `continue-on-error` for one release rather than
   expanding scope mid-loop.
5. **ISS-27 sanitisation could alter existing GraphML output** if any current
   value contains a stripped character; the characterization test does not cover
   graphml text, so add an explicit "unchanged for the sample repo" check.
6. **Authormark staleness** is guaranteed on every file touched; forgetting the
   re-stamp fails CI (`authormark.yml`) after everything else is green.
7. **Scope creep.** 38 Low issues are catalogued and tempting. They are backlog.
   IMPLEMENT must not touch them beyond the five one-liners IMPROVE may pick up.

### Open questions (non-blocking)

- ISS-30: does `chunks.jsonl` outside `--formats jsonl` stay (documented) or go
  (gated)? Two tests pin the current behaviour; defaulting to "document it".
- ISS-24: which chunk-id scheme wins? Deferred to backlog; either choice is a
  breaking change for a consumer holding `node_id`, so it needs its own loop.

## Tests

### Runner

`python -m pytest -q` from the repo root (`pyproject.toml` -> `testpaths = ["tests"]`).
Single file, single suite: `tests/test_repo2graph.py`. No new dependency; no new
test imports `networkx`. Runner was already wired — nothing to add.

### File touched

- `tests/test_repo2graph.py` — added imports (`shutil`, `pathlib.Path`,
  `parse_all`), a `REPO_ROOT` constant, one characterization test, ten regression
  tests (some parametrized), and strengthened the one pre-existing ISS-50 test.
  The `@authormark v1` block is byte-identical to HEAD except the `Fingerprint:`
  line (stale after any edit — see "Authormark" below).

### Tests added, mapped to acceptance criteria

| Test (in `tests/test_repo2graph.py`) | AC | ISS | Kind |
|---|---|---|---|
| `test_refactor_preserves_graph_shape` | AC-13 | — | Level-1 characterization; **passes at HEAD**, must keep passing |
| `test_iss22_symbol_chunk_body_survives_unicode_line_separator` | AC-1 | ISS-22 | regression |
| `test_iss22_file_residual_excludes_symbol_body` | AC-2 | ISS-22 | regression |
| `test_index_survives_unicode_line_separators` (strengthened only) | AC-1 / test-gap | ISS-50 | regression |
| `test_iss06_cochange_survives_non_ascii_filenames` | AC-3, AC-4 | ISS-06 | regression (git fixture; skips if no `git`) |
| `test_iss27_graphml_roundtrips_with_a_control_char` | AC-5 | ISS-27 | regression (stdlib `ET.parse`, never skipped) |
| `test_iss19_parse_spec_rejects_traversal_and_option_specs[owner/.. , ../evil , -x/-y , owner/]` | AC-6 | ISS-19 | regression (table) |
| `test_iss19_parse_spec_still_accepts_valid_specs[owner/repo , URL , ssh]` | AC-6 | ISS-19 | regression (positive; passes at HEAD) |
| `test_iss16_token_never_appears_in_clone_argv` | AC-7 | ISS-16 | regression (monkeypatched `fetch.subprocess.run` recorder) |
| `test_iss18_every_fetch_subprocess_call_passes_timeout` | AC-8 | ISS-18 | regression (same recorder) |
| `test_iss13_discover_matches_between_git_and_walk` | AC-9 | ISS-13 | regression (git-vs-walk set equality; skips if no `git`) |
| `test_iss07_parse_all_falls_back_when_the_pool_breaks` | AC-10 | ISS-07 | regression (monkeypatched `ProcessPoolExecutor`) |
| `test_iss01_iss02_dead_dataclass_fields_are_gone` | AC-11 | ISS-01, ISS-02 | regression (`dataclasses.fields`) |
| `test_iss44_index_repo_workflow_has_no_run_interpolation` | AC-14 | ISS-44 | Level-3 YAML text assertion |
| `test_iss45_ci_workflow_tests_job_covers_windows` | AC-15 | ISS-45 | Level-3 YAML text assertion |

AC-12 (>= 53 passed / 0 failed) and AC-16 (authormark re-stamp) are verified in
VERIFY, not encoded as new tests. AC-13 also has the running full-suite count as
its second half.

### Notes / decisions

- **Characterization normalisation (Risk 1).** `sample_repo` is itself a
  per-run `tmp_path`, so the `repo:<root.name>` node id and every edge triple
  touching it are normalised to `repo:<ROOT>` before comparison. All other ids
  (`dir:`, `file:`, `sym:`, `module:`, `external:`) are already root-relative.
  The literal collections (`CHAR_NODES`, `CHAR_TRIPLES`, `CHAR_CHUNK_IDS`) were
  generated from a fresh HEAD build of the exact fixture content.
- **U+2028 in the fixtures** is written as the Python escape `" "` inside
  the test source (never a raw code point), matching the existing ISS-50 test.
- **ISS-07 "right reason".** At HEAD the test fails with
  `concurrent.futures.process.BrokenProcessPool: boom` propagating straight out
  of `parse_all` — exactly the abort the plan predicts, not an import/collection
  error. After the `except` is widened, the operative check becomes the
  `digest(got) == digest(serial)` equality (parsed-file structure per file).
- **ISS-19 table.** `parse_spec("owner/")` already raises at HEAD (repo needs
  >=1 char), so that one parametrized case passes at HEAD; the three traversal /
  option specs (`owner/..`, `../evil`, `-x/-y`) fail at HEAD with
  `DID NOT RAISE ValueError`, which is the intended reason.
- **No mocks beyond the two sanctioned ones:** `repo2graph.fetch.subprocess.run`
  (via `monkeypatch.setattr(fetch.subprocess, "run", ...)`) and
  `concurrent.futures.ProcessPoolExecutor`. Everything else runs for real
  against `tmp_path`; git is invoked for real where the existing suite already
  does so.

### Run command

```
python -m pytest -q
```

### Baseline (HEAD, before this section) — behaviour-preservation net

```
53 passed, 2 skipped in 1.67s
```

### After adding the tests (still at HEAD, no implementation) — expected red

```
15 failed, 57 passed, 2 skipped in 2.60s
```

The 4 extra passes vs the 53 baseline are `test_refactor_preserves_graph_shape`
(characterization — passes at HEAD by design), the 3
`test_iss19_parse_spec_still_accepts_valid_specs` cases and
`...parse_spec_rejects...[owner/]`; the pre-existing ISS-50 test flips from pass
to fail once strengthened, netting 53 - 1 + 5 = 57.

Every one of the 15 failures is an assertion / behaviour failure for its
intended reason — no `ImportError`, no missing fixture, no skip:

```
_________________ test_index_survives_unicode_line_separators _________________  (ISS-50 / AC-1)
>       assert "return MSG" in sep_chunk["text"]
E       assert 'return MSG' in '# file: pkg/sep.py\n# function: uses_sep  (lines 4-5, python)\n...\nd"\n'
        -- chunk body mis-sliced by splitlines() on U+2028; body is 'd"\n'

________ test_iss22_symbol_chunk_body_survives_unicode_line_separator ________  (ISS-22 / AC-1)
>       assert "return MSG" in chunk["text"]
E       assert 'return MSG' in '# file: sep.py\n# function: uses_sep  (lines 3-4, python)\n...\n\ndef uses_sep():'
        -- body sliced as "\ndef uses_sep():" instead of the function body

________________ test_iss22_file_residual_excludes_symbol_body ________________  (ISS-22 / AC-2)
>       assert "return MSG" not in text
E       assert 'return MSG' not in '# file: sep...  return MSG'
        -- residual wrongly pulls in the body of uses_sep

______________ test_iss06_cochange_survives_non_ascii_filenames _______________  (ISS-06 / AC-3, AC-4)
>       assert (f"file:{a}", f"file:{b}") in edges_of(g, "CO_CHANGE")
E       AssertionError: assert ('file:caf\xe9.py', 'file:na\xefve.py') in []
        -- git log quotes non-ASCII paths (core.quotepath); no CO_CHANGE edge produced

______________ test_iss27_graphml_roundtrips_with_a_control_char ______________  (ISS-27 / AC-5)
>       ET.parse(gml)  # must not raise
E       xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 68, column 31
        -- raw \x0c written verbatim into GraphML data value

_____ test_iss19_parse_spec_rejects_traversal_and_option_specs[owner/..] ______  (ISS-19 / AC-6)
_____ test_iss19_parse_spec_rejects_traversal_and_option_specs[../evil] _______
_____ test_iss19_parse_spec_rejects_traversal_and_option_specs[-x/-y] ________
>       with pytest.raises(ValueError):
E       Failed: DID NOT RAISE ValueError
        -- parse_spec accepts "owner/..", "../evil", "-x/-y" and returns a tuple

________________ test_iss16_token_never_appears_in_clone_argv _________________  (ISS-16 / AC-7)
>       assert token not in str(part), cmd
E       AssertionError: [... 'https://x-access-token:s3cr3t-CLONE-token-value@github.com/owner/repo.git' ...]
        -- token interpolated into the clone URL argv element

____________ test_iss18_every_fetch_subprocess_call_passes_timeout ____________  (ISS-18 / AC-8)
>       assert "timeout" in kwargs, cmd
E       assert 'timeout' in {'capture_output': True, 'text': True}
        -- no subprocess.run call in fetch.py sets a timeout

______________ test_iss13_discover_matches_between_git_and_walk _______________  (ISS-13 / AC-9)
>       assert walk_set == git_set
E       AssertionError: assert {'README.md', 'pkg/mod.py'} == {..., 'pkg/mod.py'}
E         Extra items in the right set:  '.github/workflows/ci.py'
        -- os.walk fallback drops dot-directories; git path keeps them

____________ test_iss07_parse_all_falls_back_when_the_pool_breaks _____________  (ISS-07 / AC-10)
>       got = gmod.parse_all(files, jobs=4)
E       concurrent.futures.process.BrokenProcessPool: boom
        -- fallback except only catches (OSError, ValueError); BrokenProcessPool aborts the build

_______________ test_iss01_iss02_dead_dataclass_fields_are_gone _______________  (ISS-01, ISS-02 / AC-11)
>       assert "start_byte" not in sym_fields
E       AssertionError: assert 'start_byte' not in {'bases', 'calls', 'docstring', 'end_byte', 'end_line', 'kind', ...}
        -- Symbol still carries start_byte/end_byte; ParsedFile still carries file_calls

___________ test_iss44_index_repo_workflow_has_no_run_interpolation ___________  (ISS-44 / AC-14)
>       assert "${{ inputs." not in block, block
E       AssertionError:  slug="$(printf '%s' "${{ inputs.repo }}" | sed -E '...')"
        -- ${{ inputs.repo }} interpolated straight into a run: block

_______________ test_iss45_ci_workflow_tests_job_covers_windows _______________  (ISS-45 / AC-15)
>       assert "windows-latest" in tests_job
E       assert 'windows-latest' in '\n    runs-on: ubuntu-latest\n    strategy:\n ...'
        -- tests matrix is ubuntu-latest only
```

### Authormark

Editing `tests/test_repo2graph.py` makes its `Fingerprint:` line stale (expected
per `AGENTS.md`). The vendored CLI was removed from the repo in `b14ce2e`
(de-vendor); CI now runs `Srinivasan-78/authormark-watch@main`. I restored line 1
(the zero-width watermark payload) byte-for-byte from HEAD and refreshed only the
`Fingerprint:` line with the recovered pre-de-vendor `stamp` tool as a best
effort. **IMPLEMENT (task T10) must re-stamp this file with the canonical tool**
alongside the source files it touches, then confirm `authormark check` is green
(AC-16). The `@authormark v1` block was never deleted, reordered or relocated.

## Implement

### Iteration 1

**Result:** `python -m pytest -q` -> **72 passed, 2 skipped** (0 failed). Baseline
was 53 passed / 2 skipped; the strengthened ISS-50 test plus the 18 new
regression/characterization tests (parametrize expands the 15 named tests to 19)
are all green. `test_refactor_preserves_graph_shape` (AC-13) passed at HEAD and
stayed green through every task — the node-id set, `(src,dst,type)` triple set
and chunk-id set for `sample_repo` are byte-identical to baseline.

**Files changed (one line each):**

| File | Task / ISS | Change |
|---|---|---|
| `repo2graph/chunks.py` | T1 / ISS-22 | New module-level `_lines(src)` = `src.split("\n")` with a trailing `"\r"` stripped per line; used for the symbol body slice and the residual `keep` slice in place of `str.splitlines()`, so text indexing matches tree-sitter row numbers on files containing U+2028/U+2029/U+0085/\x0b/\x0c. |
| `repo2graph/graph.py` | T2 / ISS-06 | `add_cochange`: dropped `text=True`, prepended `-c core.quotepath=false`, decode `out.stdout` with `.decode("utf8","surrogateescape")`; 120s timeout and the returncode / `SubprocessError` guards kept. |
| `repo2graph/graph.py` | T2 / ISS-07 | `parse_all`: the pool fallback now `import concurrent.futures` + `concurrent.futures.ProcessPoolExecutor(...)` and `except Exception:` -> serial path, so `BrokenProcessPool`, a worker `ImportError` and a pickling `TypeError`/`PicklingError` all fall back instead of aborting the build. |
| `repo2graph/export.py` | T3 / ISS-27 | New `_xml_safe(text)` dropping every character outside the XML 1.0 legal set (tab/LF/CR, 0x20-0xD7FF, 0xE000-0xFFFD, >=0x10000); applied to every GraphML `<data>` value in `add_data` and to the `NodeLabel` text. |
| `repo2graph/fetch.py` | T4 / ISS-19 | `parse_spec`: after the regex match, reject any component that is empty, `.`, `..`, or starts with `-`, raising the same `ValueError(f"not a GitHub repo spec: {spec!r}")`. |
| `repo2graph/fetch.py` | T4 / ISS-16 | Clone URL is now token-free (`https://github.com/{owner}/{repo}.git`); the credential is carried out of band by new `_auth_env(token)` which sets `GIT_CONFIG_COUNT/KEY_0/VALUE_0` for `http.https://github.com/.extraheader: AUTHORIZATION: basic <b64 of x-access-token:token>` plus `GIT_TERMINAL_PROMPT=0`, passed as `env=`. The post-clone `remote set-url` is deleted (URL was already clean, nothing on disk to scrub). |
| `repo2graph/fetch.py` | T4 / ISS-17,ISS-18 | Both remaining `subprocess.run` calls (`git clone`, `git rev-parse`) now pass `encoding="utf8", errors="replace"` and `timeout=` (`CLONE_TIMEOUT=900`, `GIT_TIMEOUT=120`); `TimeoutExpired` -> `RuntimeError("git clone timed out")` for clone, `"unknown"` for `head_sha`. Redaction is now `if token: msg = msg.replace(token, "***")`. |
| `repo2graph/parse.py` | T5 / ISS-01 | Removed `Symbol.start_byte` / `Symbol.end_byte` fields and the two constructor args at the `Symbol(...)` call site. (`node.start_byte`/`node.end_byte` in `_text`/`_signature` are tree-sitter node attributes, untouched.) |
| `repo2graph/parse.py` | T5 / ISS-02 | Removed `ParsedFile.file_calls` (field + 3 constructor sites); the call-collection line now appends only `if callee and owner is not None`. |
| `repo2graph/walker.py` | T6 / ISS-13 | `_walk_files` no longer drops dot-directories (`not d.startswith(".")` removed); it still prunes `DEFAULT_SKIP_DIRS` for speed, and `discover()`'s existing single `DEFAULT_SKIP_DIRS` filter over `rel.parts` is now the one authority for both the git and `os.walk` sources. |
| `.github/workflows/index-repo.yml` | T7 / ISS-44 | "Compute slug" step gains `env: R2G_REPO: ${{ inputs.repo }}` and the `printf` reads `"$R2G_REPO"`; no `${{ ... }}` left inside any `run:` block. |
| `.github/workflows/ci.yml` | T8 / ISS-45 | `tests` job: added `os: [ubuntu-latest, windows-latest]` to the matrix and `runs-on: ${{ matrix.os }}`; the `action` job stays ubuntu-only. |

**Deviations / notes:**

- **ISS-07 fallback breadth.** Plan T2 said "also catch `BrokenProcessPool` and
  pickling failures". Implemented as bare `except Exception` (the plan's stated
  first option) with a comment — a worker `ImportError` surfaces as
  `BrokenProcessPool`, a bad result surfaces as `PicklingError`/`TypeError`, and
  the existing comment already promises an unconditional serial fallback. No
  behaviour change on the happy path (pool still used for >=64 files).
- **ISS-16 mechanism (Risk 3).** Used `GIT_CONFIG_*` env, not `-c` argv and not
  `GIT_ASKPASS`. Needs git >= 2.31 (GA runners and any 2021+ git have it). The
  base64 of `x-access-token:<token>` never contains the raw token substring, and
  it is only in the child env, never argv. `remote set-url` was dropped rather
  than kept as a no-op because the URL is now clean from the start. Not
  integration-tested against real auth here — VERIFY should smoke
  `repo2graph github psf/requests --max-files 5` per the plan.
- **ISS-13 observable change (Risk 2).** A plain-folder (non-git) build now
  indexes `.github/**` and other dot-dir sources that the `os.walk` path used to
  hide, matching what a git checkout already did. This is the intended
  unification. The characterization snapshot (`sample_repo` has no dot-dirs) did
  **not** move, so no loop-back was triggered.
- **ISS-27 "unchanged for the sample repo" (Risk 5).** `test_iss27_...` also
  asserts the emitted GraphML contains no XML-illegal character; the two
  networkx GraphML tests still pass (skipped, networkx absent) and
  `test_graphml_carries_yfiles_layout` is unaffected — no sample-repo value
  contains a stripped character, so `_xml_safe` is a no-op there.
- **AUTHORMARK (T10) — NOT completed, manual follow-up required.** The pinned
  stamp tool at
  `...\scratchpad\authormark-ci.mjs` runs, but it **rewrites header line 1
  without the zero-width watermark payload** (verified: `git diff` after
  `stamp` showed `-# @authormark v1 ... <U+200B/U+200C run> ...` ->
  `+# @authormark v1 -- do not remove (authorship watermark)` with the payload
  gone). That is the watermark AGENTS.md forbids removing, so I reverted every
  header block (lines 1-5) to its byte-exact HEAD content and kept only the
  code changes below it. Consequence: the `Fingerprint:` line on all six
  touched `.py` files is now **stale** (expected after any edit) and must be
  refreshed with the *canonical* tool (`Srinivasan-78/authormark-watch@main`,
  the CI action) — which does preserve the payload — before merge:
  `authormark stamp repo2graph/chunks.py repo2graph/graph.py repo2graph/export.py repo2graph/fetch.py repo2graph/parse.py repo2graph/walker.py tests/test_repo2graph.py`.
  `.github/**` YAML is on the authormark ignore list, so `ci.yml` /
  `index-repo.yml` need no stamp. No `@authormark` block was deleted, edited,
  reordered or relocated. (The local tool also reports pre-existing staleness on
  ~11 files this loop never touched — e.g. `cli.py`, unchanged from HEAD — which
  confirms its key differs from the repo's canonical key; ignore that noise.)

**Full pytest output (final):**

```
72 passed, 2 skipped in 2.35s
```

Task-by-task green progression: T1 -> 12 failed / 60 passed; T2 -> 10 failed /
62 passed; T3 -> 9 failed / 63 passed; T4+T5+T6 -> 2 failed / 70 passed (only
the two YAML-text tests left); T7 -> 1 failed; T8 -> 0 failed, 72 passed /
2 skipped.

## Review

### Iteration 1

**Verdict: PASS — 0 unresolved BLOCKING findings.** 7 SHOULD, 5 NICE.

Reviewed the real `git diff 81519d6a` (9 files, +468/-39), not the `## Implement`
summary. Independently re-ran `python -m pytest -q` on this win32 host:
**72 passed, 2 skipped, 0 failed** — matches the claim, and incidentally is
direct evidence for Risk 4 (the new `windows-latest` CI leg) being green today.

#### Focus-area findings (each question answered against the code)

**ISS-22 / `chunks.py:13-20,78,129` — correct.** `_lines` = `src.split("\n")`
with a per-line trailing `"\r"` drop. tree-sitter increments `Point.row` on
`\n` only, so the indexing now matches the parser for U+2028/U+2029/U+0085/
`\x0b`/`\x0c`. CRLF behaviour is unchanged vs `splitlines()`. Both call sites
(symbol body line 78, residual `keep` line 129) were converted; `grep splitlines
repo2graph/` leaves only `graph.py:162` (go.mod, `\n`-only content — fine),
`graph.py:380` (see SH-1) and doc comments. **No residual `keep` mis-slice:**
`keep += lines[cur-1:s-1]` / `lines[cur-1:]` are 1-based-line indices,
consistent with `_lines`. Bonus consistency win nobody claimed: `_read_and_parse`
computes `lines = raw.count(b"\n") + 1` (`graph.py:181`), which now equals
`len(_lines(src))`; under `splitlines()` the file-chunk `end_line` could disagree
with the actual slice.

**ISS-06 / `graph.py:365-380` — correct.** `-c core.quotepath=false` precedes
`-C` (git accepts global options in any order), `text=True` is gone,
`out.stdout.decode("utf8","surrogateescape")` mirrors `walker._git_files`
exactly. The 120s timeout and the `returncode` / `(OSError, SubprocessError)`
guards are intact. The `1 < len(current) <= 25` skip is byte-identical to
baseline (still uncounted — that is ISS-12, correctly left in backlog).

**ISS-07 / `graph.py:203-212` — genuine serial fallback, not masking.** Verified
the failure mode: `list(pool.map(...))` is *inside* the `with`, so no partial
result can escape; on any `Exception` the fallback re-runs **all** items through
`[_read_and_parse(i) for i in items]`. If the underlying cause is a real defect
in `_read_and_parse` (not a pool problem), the serial pass raises it again — so
the widening cannot swallow a real error or return a truncated file list. Only
cost is duplicated work on a transient pool failure. `BaseException`
(KeyboardInterrupt/SystemExit) still propagates.

**ISS-27 / `export.py:217-231,277,301` — correct but incomplete (SH-4).** The
predicate is the right XML-1.0 legal set: `\t\n\r`, `0x20-0xD7FF` (surrogates
`D800-DFFF` excluded), `0xE000-0xFFFD` (`FFFE/FFFF` excluded), `>= 0x10000`.
Applied to every `<data>` text via `add_data` and to the `y:NodeLabel` text.
Not applied to the `id`/`source`/`target` **attributes** — see SH-4. Risk 5
("no GraphML output change for the sample repo") is argued in `## Implement` but
**not asserted by any test**; `test_iss27_...` only checks the control-char repo.

**ISS-16/17/18/19 / `fetch.py`** — token is absent from argv (URL is now
`https://github.com/{owner}/{repo}.git`, credential rides in `GIT_CONFIG_*`
env, which is `0400`-owner-only in `/proc` unlike world-readable `cmdline`).
Error string: `if token: msg = msg.replace(token, "***")` covers the raw token;
see SH-3 for the base64 form. `parse_spec` rejections are **not** over-broad —
re-derived by hand: `owner/repo`, `https://github.com/owner/repo`,
`git@github.com:owner/repo.git` all still return `("owner","repo")` (three
parametrized cases assert it); `owner/..`, `../evil`, `-x/-y`, `owner/` all
raise. `owner/..git` also now raises correctly (non-greedy `repo` group matched
`"."` + the `.git` suffix). Both surviving `subprocess.run` calls carry
`timeout=` and `encoding="utf8", errors="replace"`; `TimeoutExpired` ->
`RuntimeError("git clone timed out")` for clone and `"unknown"` for `head_sha`.
`remote set-url` deletion is right — the URL is clean from the start, so there is
nothing on disk to scrub with `--keep-clone`.

**ISS-13 / `walker.py:41-45`** — git and walk sets are now identical for the
tested tree, and `discover()`'s single `any(part in DEFAULT_SKIP_DIRS ...)`
filter (`walker.py:113`) is the one authority for both sources. `.git` is in
`DEFAULT_SKIP_DIRS`, so `os.walk` still does not descend into it. The
`.github`-now-indexed shift is **not** the only behaviour change, however — see
SH-5.

**ISS-01/02 / `parse.py`** — `grep -rn "start_byte\|end_byte\|file_calls"` over
the whole tree returns only `node.start_byte`/`node.end_byte` (tree-sitter node
attributes in `_text`/`_signature`, correctly untouched) plus this state file
and the new test. All three `ParsedFile(...)` / `Symbol(...)` construction sites
(`parse.py:183, 220, 235`) were updated. **The `file_calls` removal drops no
callee that had an owner:** the guard is `if callee and owner is not None:
owner.calls.append(callee)` — the `owner is not None` branch is the exact branch
that previously fed `owner.calls`; only the `owner is None` branch (which fed the
never-read `file_calls`) is gone. Characterization test confirms the CALLS /
CALLS_EXTERNAL triple set is unmoved.

**ISS-44 / `index-repo.yml`** — `grep '\${{'` over the file shows 12 hits, all in
`env:`, `with:` or `if:` positions; **zero inside any `run:` block**. The env
indirection is correct (`R2G_REPO` quoted as `"$R2G_REPO"` inside the `printf`).
The later "Attach graph to a release" step was already using `R2G_REPO`/`R2G_SLUG`
env indirection. Slug is `/`-stripped by the `sed`, so no traversal via `out/$slug`.

**ISS-45 / `ci.yml:21-26`** — valid YAML, `runs-on: ${{ matrix.os }}` wired to the
new `os: [ubuntu-latest, windows-latest]` dimension, 2x2 legs, `fail-fast: false`
retained, `action` job left ubuntu-only as planned.

**Test quality** — the 19 collected new/parametrized tests are real assertions,
not tautologies: chunk *body text* content, an actual `CO_CHANGE` triple, a real
`ET.parse()` round-trip plus an independent illegal-char scan of the emitted
bytes, `pytest.raises` tables, argv/kwargs inspection of a recorded call, and a
`dataclasses.fields` name check. Nothing is skipped unconditionally; the two
`shutil.which("git")` skips match the suite's existing convention.
**Risk 1 is handled:** `test_refactor_preserves_graph_shape` normalises
`repo:{sample_repo.name}` -> `repo:<ROOT>` before comparing, and every other id
(`dir:`/`file:`/`sym:`/`module:`/`external:`) is already root-relative, so the
literals are environment-independent. Weak spots are NC-1..NC-3 below.

#### Findings

| # | Rank | File:line | Finding | Concrete scenario |
|---|---|---|---|---|
| SH-1 | SHOULD | `repo2graph/graph.py:380` | The line the diff *edited* still ends in `.splitlines()` — the exact bug class ISS-22 just fixed in `chunks.py`. With `core.quotepath=false` git emits a path containing U+2028 raw (it only C-quotes bytes >0x7F when quotepath is on), and `str.splitlines()` cuts it in two. | A repo with `a<U+2028>b.py` (legal on Linux/macOS) committed alongside `c.py` 3+ times: the log line splits into `"a"` / `"b.py"`, neither is in `file_index`, the file is dropped from `current`, and its CO_CHANGE edges silently vanish — ISS-06 fixed only the non-ASCII half of the same defect. Fix: `.split("\n")` (fragments are then filtered by the existing `elif line in file_index`, so it is a safe one-word change). |
| SH-2 | SHOULD | `repo2graph/fetch.py:39-55` | `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0` require **git >= 2.31** (Mar 2021). Older git ignores the vars entirely, so the credential is silently not sent. | Debian 11 (bullseye, still on LTS) ships git 2.30.2. `repo2graph github owner/private-repo` with a valid `GH_TOKEN` used to work; after this diff git clones anonymously and the user gets `remote: Repository not found` — a misleading error for a correct token. Not silent-success, so not blocking, but Risk 3 in the plan explicitly anticipated this. Mitigate with a `git --version` preflight that errors clearly, or fall back to `GIT_ASKPASS` (works on every git). |
| SH-3 | SHOULD | `repo2graph/fetch.py:76-79` | Redaction covers only the raw token. The credential now also exists as `base64("x-access-token:" + token)`. | A malformed `GIT_CONFIG_VALUE_0` makes git print the offending config value into stderr; that stderr goes verbatim into `RuntimeError(f"git clone failed: {msg}")` and then into CLI output / CI logs with the base64 credential intact. Also redact `basic` (compute it once and `msg.replace(basic, "***")`). |
| SH-4 | SHOULD | `repo2graph/export.py:281, 307, 269` | `_xml_safe` is applied to `<data>` text and the NodeLabel but **not** to the `id=` / `source=` / `target=` attributes or `graph id=str(g.name)`. `walker._git_files` deliberately decodes with `surrogateescape`, so lone surrogates *do* reach node ids. | On Linux, a file whose name is invalid UTF-8 yields node id `file:caf\udce9.py`; `ET.ElementTree.write(..., encoding="utf-8")` then raises `UnicodeEncodeError` and no graphml is produced. Pre-existing at baseline (so not a regression), but ISS-27's stated goal was "GraphML a conforming parser can read back", and the ids are the one remaining hole. |
| SH-5 | SHOULD | `repo2graph/walker.py:14-18, 45` | The `## Implement` note claims "`.github/**` is the only behaviour change" for non-git builds. It is not: dropping `not d.startswith(".")` un-hides **every** dot-dir absent from `DEFAULT_SKIP_DIRS`, and unlike the git path there is no `--exclude-standard` to catch them. | `repo2graph build ./my-python-project` on a plain (non-git) folder now walks and indexes `.ruff_cache/**`, `.eggs/**`, `.cache/**`, `.gradle/**`, `.direnv/**`, `.yarn/**` (none are in `DEFAULT_SKIP_DIRS`; `.mypy_cache`/`.pytest_cache`/`.tox` are). A project with a populated `.ruff_cache` gains hundreds of junk `file:` nodes. Fix: add those names to `DEFAULT_SKIP_DIRS`. |
| SH-6 | SHOULD | `tests/test_repo2graph.py:719-731` (`_RunRecorder` tests) | **ISS-17 is in the in-scope 15 but has no acceptance criterion and no test.** The recorder already captures `kwargs`; asserting `encoding == "utf8"` and `errors == "replace"` is one line and would pin the fix that exists only to prevent a Windows `UnicodeDecodeError` — precisely the class of bug this repo keeps shipping. | Someone reinstates `text=True` in a later refactor: every test still passes. |
| SH-7 | SHOULD | (process) `## Implement` "AUTHORMARK (T10) — NOT completed" | **AC-16 is unmet.** The seven touched files carry stale `Fingerprint:` lines. `.github/workflows/authormark.yml` runs `authormark check` on every PR. | Opening the PR turns CI red on `authormark check` for `chunks.py`, `graph.py`, `export.py`, `fetch.py`, `parse.py`, `walker.py`, `tests/test_repo2graph.py`. **Not ranked BLOCKING deliberately:** IMPLEMENT correctly refused to use the local tool because it strips the zero-width watermark payload (AGENTS.md forbids that), so looping back to IMPLEMENT cannot fix it — this needs the canonical `Srinivasan-78/authormark-watch@main` stamp and is a merge gate for VERIFY / a human, not a code defect. |
| NC-1 | NICE | `tests/test_repo2graph.py:820-838` | `test_iss07_...`'s `digest(got) == digest(serial)` is near-tautological post-fix: both sides come from the identical `[_read_and_parse(i) for i in items]` comprehension. The load-bearing assertion is really "`parse_all` did not raise". Harmless, but the docstring oversells it. |
| NC-2 | NICE | `tests/test_repo2graph.py:800-816` | `test_iss13_...` asserts only set *equality*; it would pass vacuously if both sets were empty. One extra `assert ".github/workflows/ci.py" in git_set` makes the intent explicit and would survive a future filter change that hides the dot-dir from both paths. |
| NC-3 | NICE | `tests/test_repo2graph.py` (Risk 5) | The plan asked for an explicit "GraphML unchanged for the sample repo" check to bound `_xml_safe`. It was argued in prose but never encoded. A `write_graphml(sample_graph)`-before/after byte compare is not possible now, but asserting the sample repo's graphml contains the expected labels verbatim would cover it cheaply. |
| NC-4 | NICE | `repo2graph/fetch.py:47-54` | `GIT_TERMINAL_PROMPT=0` is set only when a token exists. Without one, a private-repo clone blocks on the credential prompt for the full `CLONE_TIMEOUT` (15 min) before failing. Set it unconditionally (build the env even for the no-token case). |
| NC-5 | NICE | `repo2graph/fetch.py:47-54` | `_auth_env` hard-codes `GIT_CONFIG_COUNT="1"` / `KEY_0` / `VALUE_0` over whatever the caller's environment already had. A user who legitimately sets `GIT_CONFIG_COUNT=2` for their own overrides gets `KEY_0` clobbered and `KEY_1` ignored. Read the inherited count and append at index N. |
| NC-6 | NICE | `repo2graph/graph.py:203` | `from concurrent.futures import ProcessPoolExecutor` was changed to `import concurrent.futures` + attribute access *solely* so the test can monkeypatch it. Legitimate, but it is production code shaped by a test; worth a one-line comment so a future cleanup does not "simplify" it back and silently disable the ISS-07 test. |

#### Cross-cutting checks

- **No dead code introduced.** `base64` is used; no orphaned imports left by the
  `parse.py` / `fetch.py` deletions; no unreferenced helper added.
- **No duplication introduced** beyond the deliberate re-statement of the
  XML-legal predicate inside `test_iss27_...` (correct — a test that imports the
  implementation's predicate cannot detect a wrong predicate).
- **No new security surface.** The two security fixes (ISS-16 argv, ISS-44 shell
  injection) both hold; SH-3 is the only residual leak path and it is
  second-order.
- **Behaviour preservation** is genuinely locked: `test_refactor_preserves_graph_shape`
  pins 13 node ids, 15 edge triples and 6 chunk ids, and it passes.

**Unresolved BLOCKING findings: 0 -> Status: VERIFY.**

## Verify

### Iteration 1

**RESULT: PASS (with AC-16 caveat).** Host: win32 (Windows 11, Python 3.13,
cp1252 locale). Working tree carries the 9-file diff uncommitted; HEAD ==
baseline `81519d6a`.

#### Commands run

| # | Command | Result |
|---|---|---|
| 1 | `python -m pytest -q` | **72 passed, 2 skipped, 0 failed** in 2.51s. The 2 skips are the networkx-gated GraphML tests (`test_repo2graph.py:455`, `:463` — `No module named 'networkx'`), unchanged from baseline. |
| 2 | lint / typecheck | **None configured** — no `ruff`/`mypy`/`flake8`/`pylint` config anywhere; `pyproject.toml` has only `[tool.pytest.ini_options]`. This is ISS-43 (backlog, explicit non-goal this loop). Nothing to run. |
| 3 | `python -m pip install -e . --no-deps -q` | exit 0 |
| 3 | `python -c "import repo2graph"` | `import ok 0.1.0` |
| 3 | `repo2graph --help` | usage printed: `{build,github,gh,query,map,stats}` |

#### Smoke evidence (affected paths)

- **ISS-27 / real repo.** `repo2graph build . --out <scr> --formats overview,jsonl,graphml`
  -> build ok (files 32, parsed 13, nodes 357, edges 1044, chunks 253).
  `python -c "import xml.etree.ElementTree as ET; ET.parse('<scr>/human/graph.graphml')"`
  -> parsed OK, root tag `{http://graphml.graphdrawing.org/xmlns}graphml`;
  independent scan of the emitted bytes -> **0 characters outside the XML 1.0
  legal set**.
- **ISS-27 / form-feed docstring.** Built a tmp repo whose only file has `\x0c`
  inside a docstring, `--formats graphml`; `ET.parse` succeeds, 0 illegal chars.
- **ISS-06 / non-ASCII CO_CHANGE.** Tmp git repo, `café.py` + `naïve.py`
  committed together 3x, `build(repo, git_history=10)` at the platform-default
  (cp1252) locale -> `CO_CHANGE` triple `("file:café.py","file:naïve.py")`
  present; **no `UnicodeDecodeError`** (script ran to completion).
- **ISS-22 / U+2028.** Tmp repo, `sep.py` line 1 = `MSG = "a b"`. The chunk
  for `sym:sep.py::uses_sep` has `text` == ``…\ndef uses_sep():\n    return MSG``
  — contains `"return MSG"`, does **not** contain `MSG = "a`. The
  `file_residual` chunk contains the `MSG = ` line and **not** `return MSG`.
- **ISS-19 / parse_spec table.** `parse_spec` of `owner/..`, `../evil`, `-x/-y`,
  `owner/` each raise `ValueError`; `owner/repo`,
  `https://github.com/owner/repo`, `git@github.com:owner/repo.git` each return
  `("owner","repo")`.
- **ISS-13 / discover parity.** Same tree (`README.md`, `pkg/mod.py`,
  `.github/workflows/ci.py`) walked before and after `git init && git add -A`:
  both yield `{'.github/workflows/ci.py', 'README.md', 'pkg/mod.py'}` — identical.
- **ISS-16 / public clone still works.** `repo2graph github psf/requests
  --max-files 5 --out <scr>` -> exit 0, 10 artifacts written, commit
  `dae7ef63b4df` — the token-free clone URL + `GIT_CONFIG_*` credential path did
  not break anonymous cloning.

#### Behaviour preservation (refactor)

`test_refactor_preserves_graph_shape` (AC-13) pins the sorted node-id set (13),
`(src,dst,type)` triple set (15) and chunk-id set (6) for the `sample_repo`
fixture and is **green** — the observable graph/chunk contract is byte-identical
before and after the 9-file diff. Full suite green corroborates.

#### Acceptance criteria

| AC | Verdict | Proof |
|---|---|---|
| AC-1 (ISS-22 symbol body) | **Met** | smoke: `uses_sep` chunk text = `def uses_sep():\n    return MSG`; `test_iss22_symbol_chunk_body_survives_unicode_line_separator` + strengthened `test_index_survives_unicode_line_separators` green |
| AC-2 (ISS-22 residual) | **Met** | smoke: residual = `…MSG = "a b"\nEXTRA = …`, no `return MSG`; `test_iss22_file_residual_excludes_symbol_body` green |
| AC-3 (ISS-06 CO_CHANGE non-ASCII) | **Met** | smoke: `("file:café.py","file:naïve.py")` in CO_CHANGE triples; `test_iss06_cochange_survives_non_ascii_filenames` green |
| AC-4 (ISS-06 no UnicodeDecodeError) | **Met** | smoke ran to completion at cp1252; same test green on this win32 host |
| AC-5 (ISS-27 stdlib ET.parse, no skip) | **Met** | two smokes `ET.parse` OK + 0 XML-illegal chars; `test_iss27_graphml_roundtrips_with_a_control_char` green (not `importorskip`) |
| AC-6 (ISS-19 parse_spec) | **Met** | smoke table; 4 reject + 3 accept parametrized cases green |
| AC-7 (ISS-16 token absent from argv) | **Met** | `test_iss16_token_never_appears_in_clone_argv` green; `fetch.py:73` clone cmd URL is `https://github.com/{owner}/{repo}.git`, credential in `GIT_CONFIG_VALUE_0` env |
| AC-8 (ISS-18 timeout kwarg) | **Met** | `test_iss18_every_fetch_subprocess_call_passes_timeout` green; `fetch.py:74` `timeout=CLONE_TIMEOUT`, `:90` `timeout=GIT_TIMEOUT` |
| AC-9 (ISS-13 discover parity) | **Met** | smoke: identical sets; `test_iss13_discover_matches_between_git_and_walk` green (not skipped) |
| AC-10 (ISS-07 pool fallback) | **Met** | `graph.py:208` `except Exception:` -> serial `[_read_and_parse(i) for i in items]`; `test_iss07_parse_all_falls_back_when_the_pool_breaks` green |
| AC-11 (ISS-01/02 dead fields gone) | **Met** | `dataclasses.fields(Symbol)` = bases/calls/docstring/end_line/kind/name/parent/qualname/signature/start_line (no `start_byte`/`end_byte`); `ParsedFile` = imports/lang/parse_errors/symbols (no `file_calls`); `test_iss01_iss02_dead_dataclass_fields_are_gone` green |
| AC-12 (>=53 passed / 0 failed) | **Met** | 72 passed, 2 skipped, 0 failed; only pre-existing test changed is the strengthened ISS-50 one |
| AC-13 (behaviour snapshot) | **Met** | `test_refactor_preserves_graph_shape` green — 13 node ids / 15 triples / 6 chunk ids unmoved for `sample_repo`; full suite green |
| AC-14 (ISS-44 no run: interpolation) | **Met** | `index-repo.yml`: `${{ inputs.* }}` / `${{ github.* }}` appear only in `env:` / `with:` / `if:` positions; the `run: \|` block reads `"$R2G_REPO"` (line 50); `test_iss44_index_repo_workflow_has_no_run_interpolation` green |
| AC-15 (ISS-45 Windows CI) | **Met** | `ci.yml` `tests`: `runs-on: ${{ matrix.os }}`, `os: [ubuntu-latest, windows-latest]`, `python-version: ["3.10","3.12"]` = 4 legs; `action` job stays ubuntu-only; `test_iss45_ci_workflow_tests_job_covers_windows` green |
| AC-16 (authormark re-stamp) | **Not met — caveat** | The 6 modified source files (`chunks.py`, `graph.py`, `export.py`, `fetch.py`, `parse.py`, `walker.py`) carry **stale `Fingerprint:` lines** (content changed below the header, fingerprint not refreshed); `tests/test_repo2graph.py` was stamped with a non-canonical best-effort tool. The `@authormark v1` block — including the 178-char zero-width watermark payload on line 1 — is **intact and unmoved** on every file (`git diff` shows no `+`/`-` on any `@authormark` / `Copyright` / `Author:` / `SPDX-License-Identifier` line). No canonical stamp tool is vendored (`.authormark/` absent; CI runs `Srinivasan-78/authormark-watch@main`). A canonical re-stamp of all 7 files is required before merge or `authormark check` in CI will go red. Per the VERIFY contract this known-outstanding hygiene item does not fail the phase on its own. |

#### Conclusion

Every functional/preservation criterion (AC-1..AC-15) is **Met**, every check is
green (full suite, install, CLI, five behaviour smokes, public clone), and the
refactor preserves the pinned graph/chunk contract. The single gap is AC-16
(authormark fingerprints), which is a merge-gate hygiene task needing the
canonical `authormark-watch` stamp — not a code defect, and explicitly carved
out of the fail condition.

**RESULT: PASS (with AC-16 caveat)**

## Remember

### Iteration 1

Durable knowledge from this loop went into `AGENTS.md` — the repo's single "Repo
rules" file (included verbatim by `CLAUDE.md`). No `docs/adr/` or memory dir
exists; `README.md` is user-facing and `.github/CONTRIBUTING.md` is a 4-line
checklist, so neither fits engineering rules. Four notes added, one existing
section corrected:

| Note | Landed in | Why it's non-obvious / will matter |
|---|---|---|
| **`split("\n")` not `splitlines()`** — tree-sitter counts rows by `\n` only; `splitlines()` also breaks on U+2028/U+2029/U+0085/`\x0b`/`\x0c`, desyncing line slices from parser rows. Reuse `chunks._lines`. Flags `graph.py:380` as still-open (git-log output still `.splitlines()`). | `AGENTS.md` new section "Text slicing — use `split("\n")`, never `splitlines()`" | Recurring bug class: ISS-22 (`chunks.py`), the `query.py` comment, and SH-1 still open at `graph.py:380`. A one-word change on the wrong side re-introduces silently-mis-sliced chunk text. |
| **Never `text=True` on git subprocess output.** Decode bytes as `utf8`/`surrogateescape`, pass `-c core.quotepath=false`, always set `timeout=`. `fetch.py` variant: `encoding="utf8", errors="replace"`. | `AGENTS.md` new section "Decoding git subprocess output (Windows / non-UTF-8 locales)" | Every historical regression here is a Windows cp1252 encoding bug (ISS-06/17/22/27). Pattern started in `walker._git_files`, now also in `graph.add_cochange` + `fetch.py`; without it, non-ASCII paths crash or `CO_CHANGE`/import edges vanish. New `windows-latest` CI leg exists to catch this. |
| **authormark stamp tool is not vendored** (de-vendored `b14ce2e`; no `.authormark/`). It lives in `Srinivasan-78/authormark-watch` and runs as CI `authormark check`. Locally-recovered `authormark.mjs` copies strip the zero-width watermark payload from header line 1 — do not use them. Any `.py`/`.md`/test edit needs a canonical re-stamp (pre-merge gate). | `AGENTS.md` — corrected the stale `node .authormark/authormark.mjs stamp <file>` line in the existing "Authorship headers" section + added a bullet | The old path in `AGENTS.md` no longer exists, so an agent following it literally fails. This is exactly why AC-16 is unmet this loop (see `## Verify`). |
| **Non-git builds now index dot-directories.** `walker.discover()` uses one `DEFAULT_SKIP_DIRS` filter for both `git ls-files` and `os.walk`; the walk path no longer drops all dot-dirs. `.github/**` is now indexed on plain folders, matching git checkouts; caches like `.ruff_cache/`/`.eggs/`/`.cache/` leak in unless added to `DEFAULT_SKIP_DIRS` (SH-5). | `AGENTS.md` new section "Discovery indexes dot-directories, git or not" | Observable behaviour change (ISS-13) that nothing in the test suite pins beyond the git-vs-walk parity test; a future dev debugging "why is `.ruff_cache` in my graph" needs this. |

Not recorded (git/code already show it, or already in-code): the per-issue fix
diffs (in `## Implement` + git), the `_xml_safe` XML-1.0 predicate (self-evident
in `export.py`), the `ProcessPoolExecutor` attribute-access-for-monkeypatch shape
(NC-6 asks for an inline comment, not a durable note).

**Side effect:** editing `AGENTS.md` staled its own `Fingerprint:` line. It joins
the 7 files already awaiting a canonical `authormark-watch` re-stamp before merge
(see `## Verify` AC-16 caveat). The `@authormark` block itself was not touched.

## Improve

### Iteration 1

#### Loop retro

- **Iterations / thrash.** One clean pass through every phase, **zero loop-backs**
  (PLAN -> TEST -> IMPLEMENT -> REVIEW -> VERIFY -> REMEMBER -> IMPROVE). No phase
  bounced its predecessor. The build loop itself did not thrash.
- **Near-miss inside IMPROVE (this phase).** A debug command I ran,
  `git checkout -- repo2graph/graph.py`, silently reverted IMPLEMENT's ISS-06 /
  ISS-07 edits *and* my in-progress quick wins on that file. Caught immediately
  via `git diff` (graph.py showed empty), reconstructed from the pre-edit `Read`
  plus the `## Implement` notes, full suite re-run green (73 passed). **Skill fix:
  an IMPROVE/REVIEW agent must never run destructive git ops (`checkout --`,
  `restore`, `reset`, `clean`) against an uncommitted tree that holds the whole
  loop's output — do experiments on a copy.** Worth adding to the phase-7 contract.
- **Which phase caught the most.** PLAN's up-front audit found all 53 issues and
  did the heavy lifting. REVIEW added 7 SHOULD + 6 NICE; of those, **SH-1 is a
  genuine PLAN miss**: PLAN audited `graph.py`, fixed the non-ASCII half of the
  `git log` decode bug (ISS-06) but did not notice the *same line* still had the
  `str.splitlines()` line-separator bug class (ISS-22). "Same bug class on a line
  you are already editing this loop" should be a standard audit checklist item —
  this is a PLAN thoroughness gap, not a contract gap.
- **Plan accuracy: high.** The 15-item in-scope partition landed exactly as
  scoped; the AC-13 characterization snapshot never moved; all five named risks
  materialised as predicted (Risk 2 dot-dir shift, Risk 3 git>=2.31, Risk 5
  `_xml_safe` scope) and were each either accepted or pushed to backlog. Risk 4
  (new `windows-latest` CI leg) went green first try.
- **Contract gap -> concrete fix.** **AC-16 (authormark re-stamp) is an
  acceptance criterion no agent can satisfy**: the canonical stamp tool was
  de-vendored (`b14ce2e`, no `.authormark/`), and the recovered local copy strips
  the zero-width watermark payload that `AGENTS.md` forbids removing. VERIFY had
  to pass it as "Not met — caveat". Fixes: (1) PLAN must not write ACs that depend
  on tooling absent from the repo; (2) the VERIFY contract should have an explicit
  "deferred to merge gate" verdict for hygiene items a code loop structurally
  cannot close, distinct from "Not met".

#### Code retro

- **Remaining debt:** 33 Low + 6 SHOULD (SH-2..SH-7) + 6 NICE (NC-1..NC-6),
  grouped into 10 backlog issues below.
- **Test gaps VERIFY tolerated:** no lint/typecheck exists (ISS-43) so VERIFY ran
  pytest only; the >=64-file parallel-parse path (ISS-53) still never executes in
  the suite; `fetch.py`'s `encoding=`/`errors=` kwargs (ISS-17 / SH-6) have no
  assertion; NC-1/2/3 are near-tautological new tests.
- **Perf concerns (none on a hot path):** `viz.relayout()` runs an O(n^2) settle
  loop synchronously on page load (ISS-35); `walker.is_binary` re-reads every
  candidate file (ISS-14).
- **Regressions this loop introduced, now backlogged:** SH-2 (private clone falls
  back to anonymous on git < 2.31), SH-5 (non-git builds now index `.ruff_cache/`,
  `.eggs/`, `.cache/` after the ISS-13 unification), SH-4 (a lone surrogate in a
  node id still makes GraphML unwritable — `_xml_safe` covers values, not id
  attributes).

#### Quick wins applied

| ISS | File:line | Diff summary |
|---|---|---|
| ISS-08 | `graph.py:133` | `(ctx or {}).get("go_module")` -> `ctx.get("go_module")` (`ctx` is already a dict from line 93) |
| ISS-10 | `graph.py:345,355` | `out[nid]` / `out[stack.pop()]` on a `defaultdict(list)` -> `out.get(nid, ())` / `out.get(stack.pop(), ())`, so the entrypoint ranking + `_reach` passes stop inserting empty lists into the dict while iterating it |
| ISS-32 | `export.py:351` | `write_cypher`: `"\n".join(lines)` -> `"\n".join(lines) + "\n"` (trailing newline so `cypher-shell -f` does not drop the final statement) |
| ISS-40 | `cli.py:40` | `len(chunks) if chunks else 0` -> `len(chunks) if chunks is not None else 0` (distinguishes `--no-chunks` from a zero-chunk repo) |
| SH-1 | `graph.py:371` | `out.stdout.splitlines()` -> `out.stdout.decode("utf8","surrogateescape").split("\n")` + a 3-line comment. Same bug class as ISS-22, on the line ISS-06 already edited: with `core.quotepath=false` git emits raw U+2028/U+2029/U+0085 in paths and `splitlines()` would cut a path in two so it never matches `file_index` and its CO_CHANGE edges vanish. |

- **ISS-20 needed no work** — IMPLEMENT already replaced the NUL-sentinel
  redaction with `if token: msg = msg.replace(token, "***")`.
- **New regression test:**
  `tests/test_repo2graph.py::test_sh1_add_cochange_splits_git_log_on_newline_only`
  — monkeypatches `repo2graph.graph.subprocess.run` to return a `git log` blob
  whose filename contains a raw U+2028; asserts the `CO_CHANGE` edge is still
  produced. Verified it **fails on `splitlines()`** (`('file:pkg/a x.py', ...)
  in []`) and **passes on `split("\n")`**.
- **Suite after quick wins:** `python -m pytest -q` -> **73 passed, 2 skipped**
  (baseline this loop was 72 passed / 2 skipped; +1 is the new SH-1 test).
- **Authormark:** editing `graph.py` / `export.py` / `cli.py` /
  `tests/test_repo2graph.py` re-stales their `Fingerprint:` lines — they were
  already on the pre-merge canonical `authormark-watch` re-stamp list from
  `## Verify` (AC-16). No `@authormark` block was touched.

#### Backlog — ready for GitHub issue creation

33 Low (38 Low minus quick wins ISS-08/10/20/32/40) + SH-2..SH-7 + NC-1..NC-6,
grouped into 10 issues. Orchestrator to create these (and the branches); not done
here.

| Proposed-issue-title | Issue-group | Issue-IDs covered | Rough size | Proposed labels | One-line problem | One-line fix approach | Priority |
|---|---|---|---|---|---|---|---|
| parse.py & cross-module string/correctness one-liners | parse/graph/cli/init | ISS-03, ISS-04, ISS-05, ISS-09, ISS-11, ISS-12, ISS-41, ISS-42, NC-6 | M | bug, type/refactor, backlog, priority/P2 | Over-eager `str.strip` of quote/pointer chars in `_text` and callee names, bare `except` hiding grammar-load failures, `add_node` discarding a legit `0`/`False` on re-add, uncounted cochange skips, plus dead-branch/dup-arg cruft | Strip only the detected delimiter run; narrow the except / count it; filter `v is not None and v != ""` + explicit empty-list check; add `stats["cochange_commits_skipped"]`; make `ctx` required; extract a shared argparse parent; derive `__version__` from `importlib.metadata`; comment the ProcessPoolExecutor attr-access | P2 |
| chunks.py: chunk line-span accuracy, id scheme & tidy | chunks | ISS-23, ISS-24, ISS-25, ISS-26 | M | bug, enhancement, backlog, priority/P3 | File/residual chunks always report `start_line:1` / `end_line:<file len>`; two id schemes (`nid` vs `nid#0`) for one field; dead `include_files` param; repeated inline magic caps | Emit the real span or `None` + document; pick one id scheme and pin it in the manifest (breaking — own sub-task, its own loop); drop the param; hoist caps to named module constants | P3 |
| export.py: correctness & GraphML hardening round 2 | export | ISS-28, ISS-29, ISS-30, ISS-31, SH-4 | M | bug, type/refactor, backlog, priority/P2 | `write_jsonl` opens without `newline=` so Windows writes `\r\n` vs the `\n`-only reader; `_xml_safe` not applied to `id`/`source`/`target` attributes so a lone surrogate in a node id still makes GraphML unwritable; `trim` duplicated with viz; hot-path imports inside functions; `chunks.jsonl` written outside `--formats` | `open(..., newline="\n")`; sanitise/escape id attributes too; one shared `trim(text, limit)`; move imports to module scope; gate on `"jsonl" in formats` or document the quirk in `FILE_NOTES` | P2 |
| viz.py: UX + safety | viz | ISS-33, ISS-34, ISS-35, ISS-36 | M | bug, enhancement, security, backlog, priority/P2 | A repo name containing `__R2G_DATA__` injects the whole JSON payload into `<title>`/`<h1>` (repo name is attacker-influenced in `repo2graph github`); `--viz-nodes 0` silently draws every node; `relayout()` freezes the page synchronously on load; pointer capture never released | Single-pass `re.sub` with a placeholder->value mapping; treat `<=0` as explicit "no cap" in `--help` or reject it; cap settle iterations by node count / yield a frame every N; call `releasePointerCapture` in `pointerup`/`pointercancel` | P2 |
| query.py: retrieval budget + scoring hygiene | query | ISS-37, ISS-38, ISS-39 | S | bug, type/refactor, backlog, priority/P2 | `retrieve` checks `budget_chars` *after* appending so it overshoots by a whole chunk, and the expansion loop has no `k` bound at all; `seen_nodes` is a list used for `in` membership; BM25 constants unnamed | Check the budget before appending and bound the expansion pass; keep an ordered list plus a set; name `K1`/`B`/`AVG_LEN` with a one-line comment | P2 |
| fetch.py: hardening round 2 | fetch | ISS-21, SH-2, SH-3, NC-4, NC-5 | M | bug, security, backlog, priority/P1 | `GIT_CONFIG_*` credential path needs git >= 2.31, so a private clone silently falls back to anonymous on Debian 11 (git 2.30) — regression from ISS-16; error redaction misses the base64 credential form; `--keep-clone` onto an existing checkout surfaces a raw git error; `GIT_TERMINAL_PROMPT=0` only set when a token exists; `GIT_CONFIG_COUNT` clobbers a caller's own value | `git --version` preflight with a clear error, or fall back to `GIT_ASKPASS` (works on every git); also `msg.replace(basic, "***")`; detect + reuse an existing checkout; set `GIT_TERMINAL_PROMPT=0` unconditionally; read the inherited count and append at index N | P1 |
| walker.py: discovery hygiene | walker | ISS-14, ISS-15, SH-5 | S | bug, enhancement, backlog, priority/P2 | After the ISS-13 unification a non-git build now walks `.ruff_cache/`, `.eggs/`, `.cache/`, `.gradle/`, `.direnv/`, `.yarn/` (not in `DEFAULT_SKIP_DIRS`); `is_binary` re-reads every candidate; a `git ls-files` timeout degrades to `os.walk` with no signal | Add those cache dirs to `DEFAULT_SKIP_DIRS`; fold the NUL check into the single read in `_read_and_parse`; record `stats["discovery"] = "git"\|"walk"` and print it in the build report | P2 |
| Test coverage round 2 | tests | ISS-52, ISS-53, SH-6, NC-1, NC-2, NC-3 | M | help wanted, backlog, priority/P2 | `fetch.py` has no `parse_spec` / argv-builder unit coverage beyond the two argv asserts; the >=64-file parallel parse path never runs in the suite; nothing asserts fetch's `subprocess.run` calls pass `encoding`/`errors`; three new tests are near-tautological | Table-test `parse_spec` (incl. ISS-19 rejections) + a fake-`subprocess.run` argv test; a 70-file `jobs=1` vs `jobs=2` node/edge-triple equality test; assert `encoding=="utf8"`/`errors=="replace"` in the recorder; give NC-1/2/3 explicit membership asserts | P2 |
| CI, supply-chain & workflow/doc hygiene | ci/action/docs | ISS-43, ISS-46, ISS-47, ISS-48, ISS-49 | M | type/ci, security, documentation, backlog, priority/P2 | No lint/typecheck config, so VERIFY runs pytest only; `summary.json` is written inside `$R2G_OUT` and force-pushed to the `graph` branch; `action.yml` uses floating `@v7` tags while workflows pin SHAs; README shows a `schedule:` block the real `self-index.yml` lacks; `dependabot-automerge.yml` interpolates `${{ github.event.pull_request.html_url }}` into `run:` | Add a ruff (and optionally mypy) config + a CI step; write `summary.json` to `$RUNNER_TEMP`; pin the two actions to SHAs with a version comment; reconcile the README block with the file; pass the URL via `env:` | P2 |
| Canonical authormark re-stamp of files touched this loop | hygiene | AC-16, SH-7 | XS | type/chore, backlog, priority/P1 | The 7 files edited this loop (`chunks.py`, `graph.py`, `export.py`, `fetch.py`, `parse.py`, `walker.py`, `tests/test_repo2graph.py`) plus `AGENTS.md` carry stale `Fingerprint:` lines; `.github/workflows/authormark.yml` runs `authormark check` and will fail the PR | Run the canonical `Srinivasan-78/authormark-watch@main` stamp over the 8 files before merge — the recovered local `authormark.mjs` strips the zero-width watermark payload and must not be used | P1 |

#### Labels to create (do not yet exist in the repo)

Existing set: `accessibility`, `bug`, `documentation`, `duplicate`, `enhancement`,
`good first issue`, `help wanted`, `invalid`, `question`, `wontfix`,
`automated-pr`, `bot`, `needs-review`, `type/chore`, `type/ci`,
`type/dependencies`, `dependencies`, `github_actions`, `size/XS`, `size/S`,
`size/M`.

Missing — create these:

| Label | Purpose |
|---|---|
| `security` | referenced by the fetch / viz / CI issues; no security label exists today |
| `type/refactor` | simplify / dedupe / dead-code issues (only `type/chore` and `type/ci` exist) |
| `backlog` | the persistent route back — tag every issue above so they stay findable as one set |
| `priority/P1` | ranked priority (none exist); P1 = fetch hardening + authormark re-stamp |
| `priority/P2` | ranked priority; the bulk of the backlog |
| `priority/P3` | ranked priority; chunks tidy + id-scheme decision |

## Loop log

- 2026-09-09 START scenario=refactor baseline=81519d6a
- 2026-09-09 PLAN iter=1 PASS — audited 12 modules + tests + action.yml + 6 workflows; 53 issues (3 High / 12 Med / 38 Low); 3 bugs reproduced at the interpreter (ISS-06, ISS-22, ISS-27); High+Med partitioned in scope, 38 Low to backlog; baseline suite 53 passed / 2 skipped -> Status: TEST
- 2026-09-09 TEST iter=1 PASS — added 1 characterization test (passes at HEAD, locks AC-13) + 10 regression tests (AC-1..AC-11, AC-14, AC-15) and strengthened the ISS-50 test; only file touched is tests/test_repo2graph.py. At HEAD: 15 failed, 57 passed, 2 skipped — every new/strengthened test fails for its intended assertion reason (mis-sliced chunk text, missing CO_CHANGE edge, ET.ParseError, parse_spec not raising, token in clone argv, no timeout kwarg, git!=walk discover sets, BrokenProcessPool abort, dataclass fields present, ${{ inputs }} in run:, no windows-latest); no ImportError / missing fixture / skip. networkx still unused by any test. Authormark fingerprint on the test file is stale (unavoidable) — IMPLEMENT/T10 to re-stamp with the canonical tool. -> Status: IMPLEMENT
- 2026-09-09 IMPLEMENT iter=1 PASS — landed T1-T8 (15 in-scope issues: ISS-01/02/06/07/13/16/17/18/19/22/27/44/45/50/51). Full suite 72 passed / 2 skipped / 0 failed (baseline 53+2). Characterization test green at HEAD and throughout — sample_repo graph shape byte-identical. ISS-13 does change one thing nothing pins: non-git builds now index dot-dirs (.github/**) like git builds already did — intended, snapshot unmoved. T10 authormark NOT done: the scratchpad stamp tool strips the zero-width watermark payload from header line 1, so all six .py header blocks were reverted to byte-exact HEAD and their Fingerprint lines left stale — canonical tool (authormark-watch@main) must re-stamp the 6 .py files + tests/test_repo2graph.py before merge; .github/** YAML is ignore-listed. -> Status: REVIEW
- 2026-09-09 REVIEW iter=1 PASS — reviewed the real diff (9 files, +468/-39); re-ran the suite independently on win32: 72 passed / 2 skipped / 0 failed (also first evidence the new windows-latest CI leg is green, Risk 4). 0 BLOCKING, 7 SHOULD, 6 NICE. All 15 in-scope fixes verified correct against the code, not the summary: _lines matches tree-sitter row counting and no residual keep mis-slice; add_cochange decode/quotepath/timeout correct; the widened except genuinely re-runs everything serially so it cannot mask an error or return partial results; _xml_safe uses the right XML-1.0 set incl. surrogates but is not applied to id/source/target attributes (SH-4); token absent from argv and from the raw-token error path; parse_spec rejections not over-broad (https/git@/owner-repo all still accepted); git-vs-walk discover sets equal; removed parse.py fields unreferenced tree-wide and no owned callee dropped; zero ${{ }} in any run: block; ci.yml matrix valid. Top SHOULDs: graph.py:380 still .splitlines() on git output (same ISS-22 bug class on a line this diff edited); fetch GIT_CONFIG_* needs git>=2.31 so private clones break on Debian 11 (Risk 3); walker unification un-hides more dot-dirs than .github (.ruff_cache/.eggs/.cache) for non-git builds; ISS-17 has no test; AC-16 authormark re-stamp still outstanding and will fail CI (deliberately not BLOCKING — IMPLEMENT cannot fix it without the canonical tool). -> Status: VERIFY
- 2026-09-09 VERIFY iter=1 PASS (AC-16 caveat) — full suite 72 passed / 2 skipped / 0 failed on win32; pip install -e + import + `repo2graph --help` green; lint/typecheck none configured (ISS-43). Smoked all affected paths: ISS-27 (graphml ET.parse OK + 0 XML-illegal chars on this repo and on a form-feed-docstring repo), ISS-06 (non-ASCII café.py/naïve.py CO_CHANGE edge present, no UnicodeDecodeError at cp1252), ISS-22 (U+2028 file — symbol chunk body and residual sliced from the right lines), ISS-19 (parse_spec accept/reject table), ISS-13 (git == walk discover sets), public `repo2graph github psf/requests` clone still works. AC-1..AC-15 all Met; AC-13 characterization test green so the sample_repo graph/chunk contract is unmoved. AC-16 Not met — 6 modified source files have stale Fingerprint lines and the test file was stamped non-canonically; the @authormark block incl. the zero-width payload is intact/unmoved on all; needs a canonical authormark-watch re-stamp before merge (hygiene, not a code defect, carved out of the fail condition). -> Status: REMEMBER
- 2026-09-09 REMEMBER iter=1 PASS — 4 durable notes added to AGENTS.md (repo's "Repo rules" file, included by CLAUDE.md; no docs/adr or memory dir exists): (1) split("\n") not splitlines() — tree-sitter row-count desync on U+2028/29/85/0b/0c, reuse chunks._lines, graph.py:380 still open; (2) never text=True on git subprocess output — decode utf8/surrogateescape + core.quotepath=false + timeout, pattern from walker._git_files now in add_cochange + fetch.py, motivated by the Windows-only regression history; (3) corrected the stale `.authormark/authormark.mjs` path — tool is de-vendored, lives in Srinivasan-78/authormark-watch, local copies strip the watermark payload; (4) non-git builds now index dot-dirs (.github/**, and cache dirs unless in DEFAULT_SKIP_DIRS) after ISS-13. Editing AGENTS.md staled its own Fingerprint — joins the pre-merge re-stamp list, @authormark block untouched. -> Status: IMPROVE
- 2026-09-09 IMPROVE iter=1 PASS — retro: single clean pass, 0 loop-backs; PLAN's audit did the heavy lifting (all 53 issues), REVIEW's SH-1 (graph.py git-log still .splitlines(), same class as the ISS-06 line it edited) is the one real PLAN miss; AC-16 authormark is a genuine contract gap (canonical stamp tool not in repo — PLAN should not write ACs needing absent tooling, VERIFY needs a "deferred to merge gate" verdict). Applied 5 quick wins: ISS-08/10/32/40 one-liners + SH-1 (add_cochange now decodes + split("\n"), new regression test test_sh1_add_cochange_splits_git_log_on_newline_only — verified fails on splitlines, passes on split). ISS-20 already done by IMPLEMENT. Full suite 73 passed / 2 skipped (was 72/2). Near-miss: a debug `git checkout -- repo2graph/graph.py` reverted IMPLEMENT's ISS-06/07 + my edits on that file; caught via git diff, reconstructed, re-verified green — skill fix: IMPROVE must not run destructive git ops on the uncommitted loop tree. 33 Low + SH-2..SH-7 + NC-1..NC-6 grouped into 10 backlog issues (table in ## Improve, ready for `gh issue create`); labels to create: security, type/refactor, backlog, priority/P1..P3. -> Status: DONE
