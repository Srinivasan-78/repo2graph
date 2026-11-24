# Agent 11 — Configuration, Compatibility, Platform Support, CLI/API/UX/DX Audit

Environment: Windows 11 Pro, Python 3.13.15 (CPython), repo checkout `E:\Github\repo2graph`
(uncommitted changes in `frameworks.py`/`graph.py`/`store.py`/`tests/test_store.py` left untouched).
All CLI/API exercises ran against throwaway repos created under the scratchpad, invoked as
`PYTHONPATH="E:\Github\repo2graph" python -m repo2graph.cli <args>` (the `repo2graph` entry point
on this machine's PATH resolves to an unrelated stale editable install at
`C:\Users\Srini\AppData\Local\Temp\merge-test2`, version 1.6.0 — a local-environment quirk, not a
repo defect; noted under Limitations).

## Summary

Overall this codebase is unusually hardened already for the things this audit brief asks about:
config precedence is implemented exactly as documented and was confirmed by test; error messages
for malformed config, unknown config keys, bad CLI usage and missing indexes are all specific and
actionable (no stack traces); Windows file locking, Windows memory-capping, Windows stdout
encoding (cp1252 console vs UTF‑8 redirection), BOM/CRLF handling and unicode filenames all behaved
correctly under direct testing; the MCP tool surface returns typed `ToolError` results rather than
raising; output paths in the emitted index are POSIX-normalized consistently even when built from
a Windows working tree.

The two confirmed defects worth fixing:

1. **`repo2graph doctor` always warns on real-world Python 3.13 installs** (`doctor.py:91,93`) —
   a tuple-length comparison bug makes every 3.13 patch release except exactly `3.13.0` report
   "newer than tested support matrix (<= 3.13)", which is false and will confuse almost every
   Python-3.13 user who runs `doctor` (this machine's 3.13.15 is a representative case).
2. **Residual Python-3.10 compatibility code and documentation survived the 3.10 drop** in
   `repo2graph/__init__.py`, `scripts/bump_version.py`, `scripts/check_version.py` — all three
   still carry a `tomllib`/`tomli` fallback and docstrings stating "this project supports 3.10",
   while `pyproject.toml` now declares `requires-python = ">=3.11"` (dropped in commit
   `af907bbe feat!: drop Python 3.10 to clear the filelock advisories`). The fallback branch is
   dead code under the declared support matrix.

Secondary findings: three environment variables the code reads (`REPO2GRAPH_CACHE_DIR`,
`REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL`) are not documented anywhere in
`README.md` or `docs/`.

## Configuration assessment

Reviewed: `repo2graph/config.py`, `repo2graph/exclusions.py`, `repo2graph/limits.py`,
`tests/test_config_file.py`.

- **Sources and precedence**: `.repo2graph.toml` (repo root, top-level keys) beats
  `pyproject.toml`'s `[tool.repo2graph]` table; a CLI flag beats both; built-in defaults lose to
  all three. This is exactly what `config.py`'s module docstring claims, and it is what the code
  does — `load()` records `sources` per-dest and the CLI layer (confirmed by test below) applies a
  config value only when the flag was not explicitly given.
- **Validation**: every key has a typed validator (`_str_list`, `_nonneg_int`, `_pos_int`,
  `_bool`, `_secret_policy`, `_max_file_mb`, `_repo_relative_path`, `_viz_nodes`). `bool` is
  explicitly excluded from the int validators (`isinstance(v, bool)` check) so `git-history = true`
  is correctly rejected rather than silently treated as `1`.
  `_repo_relative_path` rejects absolute paths, NUL bytes, and `..` segments for the `scip` key —
  this is explicitly a repo-escape guard (good).
- **Unknown-key handling**: an unknown key is a hard `ConfigError`, not a silent ignore — confirmed
  by test below. This is the correct and safer choice (the brief flagged silent-ignore as a
  usability risk; this codebase does not have that problem).
- **Malformed file handling**: an unparseable `.repo2graph.toml` raises `ConfigError` and aborts the
  build; an unparseable `pyproject.toml` only emits a diagnostic note and is otherwise ignored
  (`config.py:181-186`) — a deliberate, documented asymmetry ("it belongs to the project... did not
  need it before"), which is a reasonable design choice, not a bug.
- **Defaults**: "no config file" returns an empty `RepoConfig()` and every caller falls through to
  its pre-config default — confirmed no behavioural change for projects with no config.

### Test transcript (config precedence + validation)

```
$ cd <scratch>/cfg-test && cat > .repo2graph.toml <<EOF
bogus-key = true
EOF
$ python -m repo2graph.cli build . -o .r2g
error: .repo2graph.toml: unknown key 'bogus-key' (known keys: chunk-large-files, exclude,
exclude-dir, framework-edges, git-authors, git-history, include, include-vendor,
max-call-candidates, max-file-mb, reference-edges, scip, secret-dirs, secret-keywords,
secret-policy, viz-nodes)
exit 1

$ cat > .repo2graph.toml <<EOF
include = [1,2,3
EOF
$ python -m repo2graph.cli build . -o .r2g
error: .repo2graph.toml: not valid TOML: Unclosed array (at end of document)
exit 1

$ cat > .repo2graph.toml <<EOF
git-history = true
EOF
$ python -m repo2graph.cli build . -o .r2g
error: .repo2graph.toml: key 'git-history': expected an integer >= 0, got True
exit 1

$ cat > .repo2graph.toml <<EOF
max-call-candidates = 2
EOF
$ python -m repo2graph.cli build . -o .r2g --max-call-candidates 9
... (build succeeds)
$ python -c "import json; print(json.load(open('.r2g/agent/manifest.json'))['max_call_candidates'])"
9
```
Confirms: CLI flag (9) wins over config file (2) exactly as `config.py`'s docstring claims.

### Env-var surface (full grep of `os.environ`/`getenv` across `repo2graph/`)

| Variable | File:line | Purpose | Documented? |
|---|---|---|---|
| `REPO2GRAPH_DEBUG` | `events.py:31`, set by `cli.py:2063` (`--debug`) | re-raise internal errors instead of swallowing them | Yes — `docs/cli.md:108` |
| `REPO2GRAPH_MCP_AUTH_TOKEN` | `mcp/server.py:894` | bearer token for MCP SSE endpoints (fallback for `--auth-token`) | Yes — `docs/mcp.md:224,1081` |
| `GH_TOKEN` / `GITHUB_TOKEN` | `fetch.py:220` | token for private repo clone (`repo2graph github`) | Yes — `docs/cli.md:264` |
| `GITHUB_STEP_SUMMARY` | `cli_inspect.py:175` | where `--github-step-summary` appends its card | Yes — `docs/cli.md:675` |
| `OLLAMA_HOST` | `answer.py:~290` | `rag --answer`'s local LLM endpoint | Yes — `docs/cli.md:574-577` |
| `PYTHONIOENCODING` | `cli.py:1106` | if set, skip the UTF-8 stdout/stderr reconfigure on Windows | **No** — only explained in a code comment |
| `REPO2GRAPH_CACHE_DIR` | `store.py:495` | override the embedding/cache directory | **No** |
| `REPO2GRAPH_ALLOW_INSECURE_OLLAMA` | `answer.py:302` | bypass the SSRF block on `OLLAMA_HOST` pointing off-box | **No** — only surfaces at runtime in the error message itself |
| `GITHUB_SERVER_URL` | `fetch.py:62,204,221` | GitHub Enterprise server base URL for `repo2graph github` | **No** |
| `SystemRoot`, `ProgramFiles` | `integrity.py:109-110` | Windows path heuristics for a security check (not user-facing config) | N/A (internal) |

**CFG-01** (new finding) — three operator-relevant env vars (`REPO2GRAPH_CACHE_DIR`,
`REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL`) are read by the code but mentioned nowhere
in `README.md` or `docs/`. `REPO2GRAPH_ALLOW_INSECURE_OLLAMA` in particular is a security-relevant
override that a user will only discover by triggering the SSRF block it bypasses — it should be
documented next to `OLLAMA_HOST` in `docs/cli.md` so a user running their own Ollama on a
non-localhost address can find it without tripping the error first.
- Severity: Low. Confidence: Confirmed (grep + read).
- Impact: operators using GitHub Enterprise (`GITHUB_SERVER_URL`) or a custom cache location
  (`REPO2GRAPH_CACHE_DIR`) have no documented way to discover these knobs short of reading source.
- Recommendation: add a short "Environment variables" table to `docs/cli.md` (one already exists
  implicitly, scattered across sections) listing all of the above in one place.
- Effort: Trivial (documentation only).

## Compatibility & platform findings

### COMPAT-01 — `doctor`'s Python-version check misreads `sys.version_info` on virtually every real 3.13 install

- Severity: **Medium** (UX/trust defect in the project's own first-line diagnostic tool).
- Confidence: **Confirmed**, reproduced directly on this machine.
- Location: `repo2graph/doctor.py:87-105`.
- Description: `vi = sys.version_info` is a 5-tuple `(major, minor, micro, releaselevel, serial)`.
  The check does `(3, 11) <= vi <= (3, 13)` to decide "ok" vs. `vi > (3, 13)` to decide "warn".
  Because `vi` has more elements than the literal `(3, 13)`, Python's tuple comparison treats any
  `vi` whose first two elements are `(3, 13)` as **greater than** `(3, 13)` (a longer tuple with an
  equal, non-exhausted prefix compares greater), so `vi <= (3, 13)` is only ever true for the exact
  interpreter build `3.13.0` (rc/beta tags aside) and false for `3.13.1` through `3.13.x` for any
  `x > 0`. In practice this means almost every machine actually running Python 3.13 (patch versions
  are universal) gets a spurious warning telling them their "tested" interpreter is unsupported.
- Evidence:
  ```
  $ python -m repo2graph.cli doctor .
  [WARN]   Python Version: Python 3.13.15 is newer than tested support matrix (<= 3.13)
               * Remediation: Python 3.11 through 3.13 are verified. Python 3.14+ may encounter
                 grammar wheel compatibility issues.
  ```
  (This machine runs 3.13.15, squarely inside the declared `>=3.11` and classifier-advertised
  3.11–3.14 support matrix in `pyproject.toml`.)
- Impact: undermines trust in `doctor` — the tool the README and CLI's own first-run banner tell
  users to run when "something looks wrong" — by crying wolf on the single most common 3.13
  installation shape. A user debugging a real problem will spend time on this red herring.
- Recommendation: compare only the first two components, e.g. `vi[:2]`, or use
  `sys.version_info.releaselevel`-agnostic slicing: `(3, 11) <= vi[:2] <= (3, 13)` and
  `vi[:2] > (3, 13)`.
- Effort: Trivial (one-line fix, two comparisons).

### COMPAT-02 — Python-3.10 compatibility residue left behind after the 3.10 drop

- Severity: Low (dead code / stale documentation, not a functional bug — `tomllib` is guaranteed
  present under the declared `>=3.11` floor, so the fallback branch is simply unreachable).
- Confidence: Confirmed (read).
- Location:
  - `repo2graph/__init__.py:20-44` (`_parse_toml`, `_pyproject_version`) — docstrings read
    "tomllib is 3.11+ and this project supports 3.10" and import `tomllib` only inside a
    `try/except ModuleNotFoundError`, falling back to a hand-written regex TOML reader.
  - `scripts/bump_version.py:24-29,301-302` — identical `import tomllib / except ImportError: import
    tomli as tomllib / except ImportError: tomllib = None` pattern.
  - `scripts/check_version.py:25-30,40-41` — same pattern.
- Description: `pyproject.toml` declares `requires-python = ">=3.11"` (classifiers 3.11–3.14) as of
  commit `af907bbe feat!: drop Python 3.10 to clear the filelock advisories`. `tomllib` has been in
  the standard library since 3.11, so under the current support matrix the `ModuleNotFoundError`
  branch in all three files can never execute, and the `tomli` dependency it would fall back to
  (used only by these scripts, not declared as a project dependency) is dead weight. The task brief
  specifically flagged this class of residue (`tomllib` fallback to `tomli`) as a likely leftover
  from the 3.10 drop, and it is present in exactly the three places `scripts/version_surfaces.py`
  touches.
- Impact: Confusing to a contributor reading these files (the docstrings actively assert a support
  claim the project no longer makes), and a latent source of drift if `tomli` is ever quietly
  removed from a contributor's environment while someone is relying on the stale comment.
- Recommendation: Remove the `ImportError`/`ModuleNotFoundError` fallback branches and the regex
  TOML reader in all three files, import `tomllib` unconditionally, and update the docstrings. If
  there's a reason to keep the regex-only fallback for a non-tomllib context (e.g. running these
  scripts under a build tool using a different/older interpreter than the package's own floor),
  that reason should be stated in the docstring instead of "this project supports 3.10".
- Effort: Small (three files, mechanical).

### Windows-specific findings (direct platform exercise)

Everything below was reproduced live on this Windows 11 / Python 3.13.15 machine.

- **File locking** (`repo2graph/lock.py`): uses `msvcrt.locking` on `win32`, `fcntl.flock`
  elsewhere; explicitly keeps `EACCES`/`EPERM` in the retry loop on Windows rather than treating
  them as permanently-unwritable, with a code comment explaining why (OneDrive/AV/search-indexer
  transient sharing violations surface the same errno as "really read-only" on Windows). This is
  correct and already accounts for the exact WinError 5/32 class of problem the brief asked about.
  No defect found.
- **Memory capping** (`repo2graph/parsepool.py:113-138`): on Windows, delegates to
  `parse._win_cap_child_memory` (a Job Object–based cap) rather than `resource.setrlimit` (POSIX
  only); correctly guarded (`sys.platform == "win32"` branch, broad `except` on `ImportError,
  OSError, ValueError, AttributeError`). No defect found.
- **Peak RSS reporting** (`repo2graph/cli.py:432-453`): returns `None` on Windows rather than using
  `resource.getrusage` (not available there); confirmed live — `"peak_rss_mb": null` in this
  session's `build` output. A code comment explains this was deliberately changed from a bare
  `try/except ImportError` to an explicit `sys.platform == "win32"` guard specifically because mypy
  running on a Windows checkout (but not in Linux-only CI) could not narrow the `try/except` form —
  a previously-fixed CI-blind-spot (`#484`), not a currently open issue.
- **`os.kill`/`SIGKILL`** (`repo2graph/parsepool.py:344-348`): uses
  `getattr(signal, "SIGKILL", signal.SIGTERM)`, which is the correct idiom since `signal.SIGKILL`
  does not exist on Windows; `os.kill` itself is emulated by the CRT on Windows (terminates the
  process, closer to `TerminateProcess`), so the fallback to `SIGTERM` is effectively unreachable on
  win32 but harmless.
- **Path output normalization**: confirmed every path field written into `nodes.jsonl`,
  `chunks.jsonl` and `manifest.json` uses forward slashes consistently even though the build ran
  from a Windows working tree with native backslash paths (`app/main.py`, `app/café.py`) — no mixed
  separators observed in output.
- **Case-insensitive collisions / symlinks**: `discovery.py` already has dedicated handling
  (`stats["skipped_case_collision"]`, W17; a documented comment on W13 about case-insensitive glob
  matching; explicit symlink lstat-and-drop logic at `discovery.py:479-481`). Not independently
  re-verified end-to-end (would require creating real NTFS symlinks, which needs elevated
  privileges not available in this sandbox), but the code path exists and is commented with issue
  references, suggesting it was deliberately added rather than overlooked.

### Encoding findings (direct exercise)

- **BOM + CRLF + non-ASCII filename + non-ASCII content**: built a throwaway repo containing
  `app/café.py` (written with `encoding="utf-8-sig"`, `newline="\r\n"`, body containing
  `café_func`, `"héllo wörld 日本語"`). Build succeeded; the emitted `chunks.jsonl` correctly shows
  `qualname: café_func`, correct 1-based `start_line`/`end_line` despite CRLF line endings, and the
  function body round-trips the non-ASCII string intact (verified via raw UTF-8 bytes, not a
  terminal rendering, after an initial false alarm where a `python -c` default-encoding `print()`
  mangled the display — the underlying bytes were always correct:
  `b'app/caf\xc3\xa9.py"'`). No defect found; BOM is stripped and CRLF is normalized without
  corrupting content or symbol boundaries.
- **Windows console vs. redirected-stream encoding**: `repo2graph/cli.py:1096-1114`
  (`_utf8_stdio`) reconfigures stdout/stderr to UTF-8 only when not a TTY and no explicit
  `PYTHONIOENCODING` is set, with a code comment describing the exact failure this fixes
  (`repo2graph rag ... > pack.md` over a repo containing `こんにちは` previously wrote `?????` to a
  redirected cp1252 stream). `tests/test_encoding.py` exists and a "Windows CP1252" CI job is
  referenced in the comment as exercising the fallback path explicitly. This is the exact issue the
  brief asked about, and it is already fixed and tested — no open defect found.
- `doctor` reports `stdout: utf-8, default: utf-8, preferred encoding: cp1252` on this machine,
  confirming the console-vs-redirect distinction is being surfaced to the user, not just handled
  silently.

### Deployment environments

- `lock.py`'s module docstring explicitly documents the read-only-root-filesystem deployment shape
  (`-o /out` with `/` read-only, only `/out` writable) and the sibling-vs-in-target lock fallback
  behaviour for it (`lock.py:105-136`). Not independently re-tested under an actual read-only mount
  (not available in this Windows sandbox), but the logic and its boundary cases are documented
  in-line with issue-level detail, consistent with a deliberate, already-addressed design rather
  than an unexamined claim.
- The `tree-sitter-language-pack>=0.7,<1.0` pin is called out in `pyproject.toml`'s own comment
  block as a *security* boundary tied to the no-network-calls claim in README/SECURITY.md (closing
  a Dependabot PR that would raise this cap is a known, intentional action per prior fleet memory —
  not re-litigated here since it is explicitly Agent 4/Security territory, but it does bear on
  "Compatibility" in the sense that any future dependency bump here must preserve the offline
  guarantee this audit's Docker/CI section depends on).
- GitHub Action and VS Code extension host were not independently exercised (out of scope to spin
  up a GitHub Actions runner or VS Code extension host from this sandbox); no code-level defect
  found in the parts reviewed (`cli_inspect.py`'s `GITHUB_STEP_SUMMARY` handling is straightforward
  and guarded by `OSError` on write).

## CLI inventory & UX findings

Subcommands (from `repo2graph --help`): `version, build, github (gh), query, rag, embed, map,
stats, diff, explain-path, bug-report, index-status, demo, doctor, completion, explain`
(`explain` has subcommands `edge, node, retrieval`).

- Flag naming is consistent across subcommands: `-o/--out`, `-r/--repo`, `--include`, `--exclude`,
  `--exclude-dir`, `--exclude-group`, `--json` recur with the same spelling and semantics
  everywhere they apply (confirmed via `--help` on `build`, `explain-path`, `query`, `rag`,
  `doctor`).
- Error messages for bad usage are specific and actionable, never a raw traceback, in every case
  tried:
  ```
  $ python -m repo2graph.cli query "x" -o ./doesnotexist
  no index at doesnotexist: run `repo2graph build <repo> -o doesnotexist` first      [exit 1]

  $ python -m repo2graph.cli build ./nope -o .r2g2
  error: repository directory does not exist or is not a directory: nope            [exit 1]

  $ python -m repo2graph.cli explain-path
  usage: repo2graph explain-path [-h] ... path
  repo2graph explain-path: error: the following arguments are required: path        [exit 2]
  ```
  Exit codes are meaningfully differentiated: `1` for a runtime/usage-level failure the program
  detected itself, `2` for argparse-level missing/invalid arguments — a standard and sensible split,
  though it does not appear to be written down anywhere (`docs/cli.md` was not checked exhaustively
  for an exit-code table; worth adding one if it doesn't exist).
- Running `repo2graph` with no subcommand prints full top-level help and **exits 0** rather than a
  nonzero "usage error" code. This is a debatable minor convention (many CLIs — `git`, `cargo` —
  exit nonzero on no subcommand; argparse's default here is clearly intentional, since it also
  prints the friendly "first time here?" banner) — flagged as a very low-severity note rather than a
  finding, since it's arguably a deliberate choice to be welcoming rather than terse.
- **DX affordances**: `doctor`, `demo`, `bug-report`, `index-status`, `explain` were all run live
  and are genuinely useful:
  - `demo` indexes a bundled example repo and walks through five increasingly complex questions
    with real CLI invocations shown inline (`explain node 'sym:...'`, `rag "..."`), ending with a
    "Now run it on your own code" footer giving the exact next three commands. This is an excellent
    first-run experience — better than most CLIs' onboarding.
  - `doctor` surfaces platform, encoding, git, tree-sitter grammar, MCP SDK, write-permission and
    artifact-integrity checks in one pass with remediation text per failing/warning check (modulo
    COMPAT-01 above).
  - `bug-report` explicitly promises and delivers no source code, no env var values, no absolute
    paths, no remote URL in its output — a genuinely privacy-conscious diagnostic bundle design.
  - `index-status` gives a clean provenance/freshness/contents summary (commit, dirty-at-build,
    build age, artifact size, dense-vector presence) in one glance.

## Python API assessment

- `repo2graph/__init__.py` exposes a deliberately minimal `__all__`:
  `Graph, build, build_chunks, iter_chunks, write_html` plus `__version__`. `docs/python-api.md`'s
  "stable surface" table documents a larger set (`Index`, `dump_all`, `verify_artifacts`,
  `index_github`, `LoadedGraph`, `SqliteGraphStore`, `default_reranker`, `ChunkRecord`,
  `RetrievalResult`, `PackResult`) explicitly by their *submodule* paths
  (`repo2graph.query`, `repo2graph.export`, `repo2graph.integrity`, `repo2graph.fetch`,
  `repo2graph.store`, `repo2graph.rerank`, `repo2graph.schema`) rather than re-exporting them at the
  top level. This is a coherent, intentional design (top-level = the handful of calls almost every
  caller needs; everything else imported from its owning module) and matches the documentation
  exactly — no mismatch found between the documented surface and the actual code.
- `py.typed` ships (`repo2graph/py.typed` present, declared in `pyproject.toml`'s package-data), and
  `docs/python-api.md` references `TypedDict` return shapes (`ChunkRecord`, `RetrievalResult`,
  `PackResult`) from `repo2graph.schema`, giving callers usable static types without needing stubs.
- `_resolve_version()` (`__init__.py`) deliberately prefers a source checkout's own
  `pyproject.toml` version over `importlib.metadata`'s answer, specifically to defend against a
  stale globally-installed dist-info shadowing a source checkout (issue `#340`) — and this defense
  was confirmed working in practice on this machine: `repo2graph build`/`bug-report` run via
  `python -m repo2graph.cli` from the checkout correctly reported `"tool_version": "4.1.0"` even
  though this machine's PATH has an unrelated stale `1.6.0` editable install registered globally
  (see Limitations).

## MCP surface assessment

- `repo2graph/mcp/tools.py`'s `dispatch()` never raises for a bad tool name, bad/missing index, or
  bad arguments — it returns a `ToolError` (a `str` subclass with no behavioural difference except a
  transport-level `isError: true` flag), confirmed by reading the full dispatch path: unknown tool
  name, no open index, and `repo_build_status` with no task id or an unknown task id are all handled
  as returned error strings, not exceptions. This matches the brief's ask ("are errors returned as
  results rather than killing the server") exactly.
- Tool schemas (`schemas.py`) are declaration-only with no handler logic mixed in, so the surface
  can be read/diffed independently — a good separation for an LLM-consumer audit.
  `SERVER_INSTRUCTIONS` is sent once at MCP `initialize` and explicitly states the "notes lead a
  reply" convention once rather than repeating it in every one of the (800-char-capped)
  `TOOL_DESCRIPTIONS`, with an explicit code comment explaining *why* that split exists (a client's
  model reads server metadata, not the repo's own docs, so a bare `JSON.parse` of a reply would
  break the first time a field is defaulted or the index is stale) — this is a thought-through
  design for exactly the LLM-consumer concern the brief raises, not an oversight.
- Paging (`cursor`) and newer optional fields (`format`, `path`, `line`) are forwarded to handlers
  only when the caller actually sent them, preserving old call shapes — and paged calls are
  deliberately excluded from the result cache (a cached page would otherwise hand back an already-
  expired cursor). Bounded/paginated output is handled deliberately, not left to the caller to
  discover empirically.
- No schema-quality defect found in the portion read (`tools.py` in full, `schemas.py` header); a
  full per-tool wording review of all nine tool descriptions/parameter docs in `schemas.py` beyond
  what's shown above was not completed given time constraints — see Limitations.

## First-run walkthrough transcript

Followed the README quickstart (`uvx repo2graph build . -o .r2g` /
`uvx repo2graph rag "..." -o .r2g`) against a throwaway two-function sample repo, substituting
`python -m repo2graph.cli` for `uvx repo2graph` (network-touching `uvx` invocation was avoided per
task constraints; this is believed behaviourally equivalent to the quickstart's `uvx` path once a
package is resolved).

```
$ mkdir sample-repo/app && cd sample-repo
$ cat > app/main.py   # 6-line greet()/route_handler() sample
$ git init -q && git add -A && git commit -q -m init

$ python -m repo2graph.cli build . -o .r2g
{ ... "stats": {"files": 1, "parsed": 1, "parse_errors": 0, "nodes": 6, "edges": 6, ...},
  "chunks": 2, "performance": {"build_seconds": 0.04, "peak_rss_mb": null, ...} }
exit 0

$ python -m repo2graph.cli query "greet" -o .r2g
--- app/main.py::greet [lexical]
# file: app/main.py
# function: greet  (lines 1-2, python)
# called by: app/main.py::route_handler
def greet(name):
    return f"hello {name}"
...
exit 0

$ python -m repo2graph.cli doctor .
[WARN]   Python Version: Python 3.13.15 is newer than tested support matrix (<= 3.13)   <- COMPAT-01
[OK]     Platform Support / Platform Encoding / Git CLI / Tree-sitter Grammars (19/19)
[OK]     MCP Server / Permissions / Artifact Integrity
exit 0

$ python -m repo2graph.cli stats -o .r2g
{ "total_symbols": 2, "top_hub_nodes": [...], "languages": {"python": 1}, "has_vectors": false, ... }
exit 0
```

**Friction points found**:
1. The only real surprise was the `doctor` false-positive warning on an entirely normal,
   fully-supported Python installation (COMPAT-01) — everything else about the quickstart was
   smooth: `build` produced the exact `written` file list the manifest later documents field-by-
   field, `query` and the `demo` walkthrough are self-explanatory, and the "first time here?"
   banner at the bottom of bare `repo2graph --help` already tells a brand-new user the three
   commands to run in order (`demo`, then `build . -o .r2g`, then `doctor .` if something looks
   wrong) — which is precisely what this walkthrough did.
2. No undocumented knowledge was required at any step; no step failed outright.

## Findings table

| ID | Severity | Confidence | Location | One-line description |
|---|---|---|---|---|
| COMPAT-01 | Medium | Confirmed | `repo2graph/doctor.py:91,93` | Tuple-length comparison bug makes `doctor` warn on almost every real Python 3.13 patch release |
| COMPAT-02 | Low | Confirmed | `repo2graph/__init__.py:20-44`, `scripts/bump_version.py:24-29`, `scripts/check_version.py:25-30` | Dead `tomli` fallback + stale "supports 3.10" docstrings left over after the 3.10 drop |
| CFG-01 | Low | Confirmed | README.md / docs/ | `REPO2GRAPH_CACHE_DIR`, `REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL` read by code but undocumented |
| UX-01 (note, not scored) | Info | n/a | `repo2graph/cli.py` | Bare `repo2graph` with no subcommand exits 0 after printing help, rather than a nonzero usage code — likely deliberate, flagged only for completeness |

## Limitations

- Did not independently test actual NTFS symlinks/junctions, path-length-limit (>260 char) paths,
  or reserved device names (`CON`, `AUX`, `COM1`, ...) end-to-end — `discovery.py` has dedicated,
  issue-referenced code for symlinks and case-collisions that was read but not exercised live, since
  creating true Windows symlinks typically needs Developer Mode or elevated privileges not available
  in this sandbox.
- Did not spin up Docker, a GitHub Actions runner, or a VS Code extension host; reviewed only the
  code-level claims and comments bearing on those environments (`lock.py`'s read-only-root-fs
  design, `cli_inspect.py`'s `GITHUB_STEP_SUMMARY` handling).
- Did not exhaustively run `--help` on every subcommand/flag combination or review every one of the
  nine MCP tool descriptions word-for-word in `schemas.py`; focused on `tools.py`'s dispatch
  behaviour and the header/instruction text, which is where an LLM-consumer tool-selection failure
  would most likely originate.
- Did not run the pytest suite (per task instructions; the orchestrator already ran it: 1 failed /
  2763 passed, not investigated here since it falls outside this agent's remit).
- One environment artifact is worth flagging explicitly rather than silently working around: this
  machine's globally-installed `repo2graph` console script resolves to an unrelated stale editable
  install (`C:\Users\Srini\AppData\Local\Temp\merge-test2`, version 1.6.0), not this checkout. All
  testing in this report used `python -m repo2graph.cli` with `PYTHONPATH` pointed at
  `E:\Github\repo2graph` to guarantee the checkout under audit — not the PATH entry — was what ran.
  This is a leftover artifact of a prior session on this fleet machine, not a defect in the
  repo2graph project itself.
