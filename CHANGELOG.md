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

### Fixed

- POSIX `fcntl.flock` raising `OSError` (advisory locks unsupported on
  NFS/FUSE/overlay) no longer silently drops every `--audit-log` record.
  The file sink falls through to the unlocked write the code already
  promised, matching win32; one `audit_lock_unavailable` event is emitted
  the first time.

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
