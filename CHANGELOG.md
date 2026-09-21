# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries for v1.0.0 through v1.4.0 were reconstructed from git history and the
published GitHub Releases after the fact, so they summarise what shipped rather
than being contemporaneous notes. `.github/workflows/publish.yml` now reads the
section for a version out of this file and uses it as the Release body, which
makes keeping it current a release-blocking step rather than a good intention.

## [Unreleased]

### Removed

- `claude-code-review.yml` and `claude.yml`, and with them all automated code
  review. `claude.yml` had no author gate of any kind: any commenter, including
  a first-time contributor, could start a 30-minute model run by typing
  `@claude`. `claude-code-review.yml` excluded fork PRs entirely to keep its
  token away from untrusted heads, so it never reviewed the contributions most
  worth reviewing. prod-igy is now the only place a model is invoked.

### Added

- `repo2graph doctor [path]` command diagnosing Python version, package version,
  tree-sitter & grammar availability, Git integration, directory permissions,
  existing artifact integrity, vector correspondence, MCP SDK compatibility,
  and platform encoding. Safely probes LLM provider configuration without ever
  disclosing secret values or environment variables. Supports `--json` output
  (Issue #308).
- Automated documentation consistency test suite (`tests/test_doc_consistency.py`)
  guaranteeing zero drift between code and documentation across CLI subcommands,
  parser language configurations, GitHub Action inputs/outputs, and MCP registered
  tools (Issue #319).
- prod-igy reports check-run results for the PR head — counts, plus the names
  of failing checks. Read from `checks.listForRef`, which needs the new
  `checks: read` scope; a re-run supersedes the earlier result for that name,
  and an in-flight run counts as running rather than failing.
- prod-igy writes a plain-language description of the diff and, when the title
  is not Conventional Commits, suggests one. The suggestion is displayed only;
  prod-igy still calls `pulls.update` exactly once, to retarget the base branch,
  and never rewrites a contributor's title or description.

  Scope is deliberately narrow. The step is shown one diff and nothing else —
  no repository contents, no `AGENTS.md`, no tools — so it cannot review the
  change against code it has not seen. Labels remain entirely path- and
  size-derived. Off by default (`PRODIGY_AI` repository variable); model,
  token ceiling, effort, timeout, per-PR run and output caps are all
  `vars.PRODIGY_AI_*`.

  Contributors have no way to reach it: it fires only on `pull_request_target`,
  `workflow_dispatch`, and a `@prod-igy` comment from an OWNER / MEMBER /
  COLLABORATOR, and it takes no instruction from the pull request. Per-PR spend
  is tracked in a hidden ledger inside prod-igy's own comment, so a force-push
  loop cannot run it more than `PRODIGY_AI_MAX_RUNS_PER_PR` times.

  Every failure — absent SDK, absent or revoked key, unreachable endpoint,
  rate limit, timeout, unparseable response — omits the section and posts the
  same comment prod-igy posted before this existed. The reason is recorded in
  the run log, never in the comment.

## [1.6.0] — 2026-09-20

### Changed

- `publish.yml` takes a `branch` input, defaulting to `main`, and every stage
  reads it instead of assuming the dispatch ref. `workflow_dispatch` runs
  against whatever ref the operator picks, so dispatching from `develop`
  previously bumped *develop's* content while opening the release PR against a
  hardcoded `main` base. The checkout is now pinned to the named branch, and
  the four hardcoded `main` references (`--base`, `gh pr list`, `gh pr create`,
  and the fetch/checkout/pull that tags the merge) follow it.

- Every workflow job now carries `timeout-minutes`, and every workflow a
  `concurrency` group. All 19 jobs previously inherited GitHub's 6-hour
  default, so the worst-case ceiling across the fleet drops from 6,840 minutes
  to 510. Bounds are set well above measured run times — CI completes in
  ~4 minutes — because the point is to catch a hang, not to police a slow run.
  `cancel-in-progress` is decided per workflow rather than uniformly: releases,
  the two bots that answer humans, and the long dispatch-only jobs are never
  cancelled; pure checks on a pull request are.

### Fixed

- **Bug sweep: every open `bug`-labelled issue.** Twenty-six issues, grouped
  below by the module they land in. Each carries a regression test proven to
  fail against the unfixed code.

- Indexing correctness. `_chunk_and_parse` sliced a large file at raw byte
  offsets, so a multi-byte character straddling a boundary lost **two** slices
  — up to 3 MB of source — with `parse_errors` still `0` and the file node
  still written as healthy, which is why only non-ASCII trees were affected
  and no test noticed. Slices are now carried to a character boundary and
  genuinely undecodable ones are counted into `stats`. The same function
  buffered the whole file for its digest and line count, so the path
  `max_file_bytes` exists to bound had no bound at all; both are now streamed.
  `PARSE_CACHE_FORMAT` goes 1 → 2 for the entry-shape change. `discover()`'s
  `S_ISREG` guard could be waived by `--chunk-large-files`, letting a
  non-regular file reach a blocking `read()`. (#196, #248)

- MCP server robustness. `open_index` did a check-then-act on module-level
  caches with no lock while the HTTP transport serves on `ThreadingHTTPServer`,
  so two concurrent first tool calls both ran the whole build and raced through
  `atomic_write`; a per-directory lock now spans the check-build-cache
  sequence. `--http-only` without `--http-port` fell through to the stdio
  transport — the exact thing the flag disables — and its early return jumped
  over `audit.close()`. (#235, #203)

- MCP auto-build no longer pins `jobs=1`. Workers inherited fd 0 and fd 1,
  which under the stdio server are the client's JSON-RPC pipes, so a repo above
  64 files hung on its first tool call. Pool workers are now detached onto
  devnull at the fd level, so a large repo's first call costs what
  `repo2graph build` costs. Also adds the >64-file fixture whose absence let
  the parallel path go untested. (#90, #67)

- HTTP transport hardening. No socket timeout, so a client that sent
  `Content-Length` and withheld the body pinned a handler thread forever. A
  deeply nested JSON body raised `RecursionError` past `except ValueError` and
  killed the handler thread **before** authentication. A non-ASCII bearer
  credential raised `TypeError` out of `authenticate()` the same way, and a JWK
  with `kty: RSA` but no `n`/`e` raised `KeyError`. Nested tool arguments took
  the thread down through the audit logger's unbounded recursion. A
  `Transfer-Encoding: chunked` body was never read, leaving bytes in the socket
  to desync the next request. An ordinary client disconnect injected a
  multi-line Python traceback into the stderr stream this package promises is
  strict JSON-lines. (#197, #232, #233, #234, #238, #249, #199)

- Audit sink survivability. A typo'd `--audit-log` killed the server with a
  traceback before it started, and `flock` failing on NFS/FUSE/overlay silently
  dropped every record to the file sink on POSIX while the Windows branch
  handled it — the "no lock available: still write" fallthrough was
  unreachable. The per-record `os.fsync`, taken while holding both the thread
  lock and the file lock, is now opt-in via `--audit-log-fsync`. (#201, #200,
  #244)

- Input validation and bounds. `--ref` reached the git argv without the
  leading-dash check `parse_spec` applies to owner/repo, so a value like
  `--upload-pack=…` was parsed as a flag. `decode_jwt` ignored a JWK's RFC 7517
  `use`/`key_ops`, accepting an encryption-only key for signature
  verification. `_challenge()` interpolated `oidc_issuer` into
  `WWW-Authenticate` without the module's own header sanitiser — now applied
  structurally to every `extra_headers` value. The `rag --answer` provider
  response was read unbounded on both axes, per line and in total. (#237,
  #246, #243, #241)

- Artifact and packaging truthfulness. `manifest.json` hardcoded "up to 5"
  CALLS edges, so an index built with `--max-call-candidates 2` shipped a
  manifest telling an agent to calibrate against a number the build never used;
  the effective value is now formatted into the prose and emitted as a
  top-level key. `load_vectors` never checked the `format` marker
  `write_vectors` has always stamped, so an unrecognised pair would have
  produced plausible, wrong rankings rather than degrading to BM25. (#245,
  #242)

- Build cost and subprocess hygiene. `cpp --version` was re-probed for every
  C/C++ file with a parse error — two spawns per file on a macro-heavy tree —
  and neither `cpp` invocation passed `stdin=subprocess.DEVNULL`, so under the
  stdio server they inherited the client's pipe. `MAX_COCHANGE_BYTES` promised
  in its own comment to be enforced during the read and was applied after
  `capture_output()` had already buffered the whole git log. (#239, #240, #236)

- The composite action's `version` input reached pip for the first time. It
  gated on `[ -f $GITHUB_ACTION_PATH/pyproject.toml ]`, which is always true
  for a composite action, so the input was accepted and ignored and the Action
  never installed the signed PyPI artifact `publish.yml` produces. (#204)

- Audit-log `high_entropy` redaction no longer treats ordinary snake_case
  identifier queries (`test_iss25_…`, `resolve_import_python3_relative`) as
  credentials. Vendor shapes (`ghp_…`, `AKIA…`, JWTs, …) are unchanged.

- `dependency-review.yml` ends with a newline, which the repo's own
  `end-of-file-fixer` pre-commit hook requires.

### Security

- `prod-igy` no longer starts privileged triage from an outsider `issue_comment`.
  The workflow `if:` and the script both require `author_association` in
  (`OWNER`, `MEMBER`, `COLLABORATOR`). Bot comments also strip backticks from
  `headRef` / `baseRef` so a fork branch name cannot break a markdown code span.

## [1.5.4] — 2026-09-17

### Changed

- Release version 1.5.4.

## [1.5.3] — 2026-09-17

### Changed

- Release version 1.5.3.

## [1.5.2] — 2026-09-17

### Security

- The audit log's `error` field is now redacted the same way every other
  value is. A downstream exception's `str()` can echo caller input verbatim
  (a malformed request, an OS error including a path with an embedded
  token), and that field previously bypassed `sanitize_value`.
- Every MCP string argument (`query`, `node_id`, `task_id`) is now
  length-capped in its handler, matching the existing numeric clamps on
  `k`/`hops`/`limit`/`budget_tokens`. Nothing downstream crashed on an
  unbounded string, but tokenising or scoring against an arbitrarily long
  one was wasted CPU no real query or node id needs.
- `claude-code-review.yml` now skips forked-repo pull requests explicitly
  (`if: github.event.pull_request.head.repo.full_name == github.repository`)
  rather than relying implicitly on GitHub's default secret redaction for
  `pull_request`-from-fork runs.
- A whole-repository security audit — architecture, threat model, trust
  boundaries and a prioritized findings list with evidence — is at
  [docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md). See also
  [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md),
  [docs/PERFORMANCE.md](docs/PERFORMANCE.md),
  [docs/PRIVACY.md](docs/PRIVACY.md) and
  [docs/ENTERPRISE_DEPLOYMENT.md](docs/ENTERPRISE_DEPLOYMENT.md) (all new).

## [1.5.1] — 2026-09-16

### Added

- MCP `ToolAnnotations` across stdio and HTTP transports: `readOnlyHint=True`,
  `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=False`.
- Informative parameter descriptions documenting formats (`sym:pkg/mod.py::func`,
  `file:path`, `dir:path`), bounds, and default values across all tool schemas.
- macOS-style framed browser window containers, rounded corners, and soft
  ambient drop shadows for all documentation screenshots (`graph-overview.png`,
  `graph-zoom.png`, `graph-sidebar.png`).

### Changed

- Enhanced all 5 MCP tool descriptions (`repo_map`, `repo_search`,
  `repo_neighbours`, `repo_cache_stats`, `repo_build_status`) to meet top-tier
  Glama Tool Definition Quality Score (TDQS) standards: active purpose verbs,
  explicit sibling disambiguation, concrete "When to use" / "When NOT to use"
  guidelines, and exact return shape specifications.
- Modernized `README.md` hero section with center-aligned branding, single-row
  badge bar, centered overview map, and balanced side-by-side canvas/controls table.
- Expanded tool description character budget test in `test_mcp.py` to 2,500 chars.

### Fixed

- Restored AuthorMark watermark fingerprints across modified files and `README.md`,
  resolving CI provenance verification.

## [1.5.0] — 2026-09-16

### Added

- `repo2graph build --incremental` reuses parse results for files whose content
  hash and language are both unchanged, via a new `agent/parse.cache.json`
  artifact. The whole resolution phase — the global name index, `CALLS`
  confidences, `INHERITS`, entrypoints and reach — is recomputed on every build,
  so an incremental index is byte-for-byte identical to a full rebuild rather
  than merely close. The build report gains an `incremental` block.
- `repo2graph embed --verify-rag` self-tests the dense-retrieval path: vectors
  present, model id, dimension, active-embedder agreement and per-chunk
  coverage. Exits non-zero with an actionable message when anything is broken.
- An HTTP transport for `repo2graph-mcp` (`--http-port`, `--http-host`,
  `--http-only`), serving JSON-RPC at `POST /mcp`. Every answer still comes from
  the same `dispatch()` the stdio transport uses.
- Bearer-token and OIDC authentication for that transport (`--auth-token`,
  `--auth-oidc-issuer`, `--auth-audience`, `--auth-jwks-ttl`), implemented with
  the standard library only — no new runtime dependency. `alg` is taken from the
  key rather than the token, the full PKCS#1 v1.5 block is compared rather than
  scanned, `iss`/`aud`/`exp`/`nbf` are enforced, and token comparison is
  constant time.
- `--auth-cimd` publishes an RFC 7591 client metadata document at
  `/.well-known/oauth-client-metadata`.
- `/.well-known/mcp-server-metadata` describes the server, its tools, its auth
  modes and whether an index exists, without needing a session. Unauthenticated
  by necessity, and therefore carrying no repository content.
- Structured audit logging: one JSON line per tool call on stderr, with
  `--audit-log <path>` and `--audit-log-level {none,errors,all}`. Values are
  redacted on shape as well as on field name, keeping a length and a short
  fingerprint so occurrences correlate without the log holding the secret.
- A bounded, expiring result cache for tool calls (`--cache-size`,
  `--cache-ttl`), dropped wholesale on any index rebuild, plus a
  `repo_cache_stats` tool.
- `ttlMs`/`cacheScope` cache metadata on `tools/list` over the HTTP transport.
- `--async-build` builds a missing index on a background thread and returns a
  task id immediately; `repo_build_status` polls it.
- A `rag_fusion_disabled` warning on stderr when dense fusion abandons itself
  because a shortlisted chunk has no vector, plus `Index.fusion_coverage`.
- `.github/workflows/dependency-audit.yml` runs `pip-audit --strict` over the
  full tree including the `rag` and `mcp` extras, on every PR to `main` and
  weekly.
- A `windows-latest` CI job that runs with `PYTHONIOENCODING=cp1252` and stdout
  redirected and piped, over a repository whose source contains U+2192, U+00E9
  and U+4E2D.
- `.pre-commit-config.yaml` running ruff and a version-consistency check.
- `glama.json`, and `uv.lock` so hosted builds are reproducible.
- A `packaging` CI job that installs the package the way a third-party host does
  — once without the `mcp` extra, asserting the refusal stays a legible sentence
  on stderr with nothing on stdout, and once with it, driving a real stdio round
  trip through the installed console script via `scripts/mcp_roundtrip.py`.

### Changed

- **Breaking:** `--viz-nodes 0` now draws an empty graph instead of meaning "no
  cap". `--viz-nodes all` is the no-cap spelling. One spelling for the two most
  opposite intentions a caller can have meant a mistyped or defaulted-to-zero
  argument silently rendered the largest possible page.
- `relayout()` in `graph.html` settles the force layout in batches across
  animation frames with a visible progress bar, instead of one synchronous loop
  that blocked the browser's main thread. The iteration cap defaults to 500 and
  is configurable via `data-max-iterations` on the graph container.
- Every CLI command writes through a single guarded `_emit`; six previously
  called `print()` directly and could raise `UnicodeEncodeError` on a redirected
  Windows stdout.
- Dependabot moved from monthly to weekly, with assignees.
- All `actions/checkout` pins unified on the verified v7.0.1 SHA.

### Fixed

- `answer._disclose()` no longer receives the provider dict that carries the
  resolved API key, closing CodeQL alert #1
  (`py/clear-text-logging-sensitive-data`).
- A URL check in the test suite compares a parsed hostname rather than a
  substring of an unparsed URL, closing CodeQL alert #2
  (`py/incomplete-url-substring-sanitization`).
- Four workflow pins whose comments named a different tag than the SHA they
  pinned.
- `events.encodable` no longer flattens ordinary characters to ASCII when a
  stream reports an encoding Python does not have.
- The stdio server reported the **MCP SDK's** version as its own in
  `serverInfo`, because `Server()` was constructed without `version=` and the
  SDK fills that field from its own package — so clients saw `1.30.0` against a
  1.4.0 release, and the two transports disagreed about what they were.

### Security

- Binding the HTTP transport beyond loopback with no authentication configured
  is refused at startup.
- Request bodies on the HTTP transport are bounded.
- `claude.yml` and `claude-code-review.yml` gained top-level `permissions`
  defaults.

## [1.4.0] — 2026-09-15

### Added

- An MCP server, `repo2graph-mcp`, serving an index over stdio with three tools:
  `repo_map`, `repo_search` and `repo_neighbours`. Behind the `mcp` extra.
- The index is built on the first tool call when one does not exist yet, so
  adding the server to a client needs no separate setup step.
- Dense retrieval became reachable: `repo2graph embed` persists vectors and
  `rag --vectors` fuses them with BM25.
- Token-denominated budgets (`--budget-tokens`) alongside character budgets.
- Publishing to PyPI and the MCP Registry from a single release workflow.

### Changed

- README cut to a landing page; the reference moved into `docs/`.

### Fixed

- `git` is kept off stdin in subprocess calls, so it cannot block on the MCP
  server's JSON-RPC pipe.

## [1.3.0] — 2026-09-11

### Added

- A unified GraphRAG engine: citation-carrying retrieval, graph expansion and
  optional LLM answer streaming (`rag --answer`).
- GraphRAG inputs on the GitHub Action, bringing it to parity with the CLI.
- A provenance workflow and a signed-commit gate.
- `CODEOWNERS`.

### Changed

- `langs` and `walker` merged into `parse.py`; `layout` merged into `export.py`.
- The version reported by `--version` was corrected; it had been stuck at 0.1.0
  through v1.0 to v1.2.

## [1.2.0] — 2026-09-09

### Fixed

A whole-repository audit landed as one batch:

- `chunks.py`: line-span accuracy, named constants.
- `viz.py`: UX fixes and safety against `__R2G_DATA__` placeholder injection.
- `export.py`: GraphML hardening and export correctness.
- `parse.py`: cross-module string and correctness fixes.
- `walker.py`: discovery hygiene and cache-directory skipping.
- `query.py`: retrieval budget and scoring hygiene.
- `fetch.py`: hardening round 2.
- Artifacts are written atomically through sibling temp files; chunks stream to
  disk rather than being materialised.

## [1.1.2] — 2026-09-09

### Fixed

- A release-workflow step referenced the wrong step output.
- A test wrote a line-separator fixture without an explicit encoding.

## [1.1.1] — 2026-09-06

### Changed

- The authormark tooling was de-vendored; `networkx` was dropped, leaving graph
  layout and GraphML generation as pure Python.
- Workflow permissions hardened and actions pinned to SHAs.

## [1.0.1] — 2026-08-30

### Fixed

- `query` against a partial index exits with a clear message instead of
  crashing.

### Added

- Authorship watermarks across the source tree.

## [1.0.0] — 2026-08-26

### Added

- First release: tree-sitter parsing into a code graph, JSONL/GraphML/Cypher
  exports, an interactive HTML map, retrieval chunks, and a GitHub Action.

[Unreleased]: https://github.com/Srinivasan-78/repo2graph/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Srinivasan-78/repo2graph/compare/v1.1.2...v1.2.0
[1.1.2]: https://github.com/Srinivasan-78/repo2graph/compare/v1.1.1...v1.1.2
[1.1.1]: https://github.com/Srinivasan-78/repo2graph/compare/v1.0.1...v1.1.1
[1.0.1]: https://github.com/Srinivasan-78/repo2graph/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Srinivasan-78/repo2graph/releases/tag/v1.0.0
