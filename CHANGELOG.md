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

### Added

- **Repository governance docs.** **`docs/ARCHITECTURE.md`** — the contributor-facing module map
  (what each of the 27 modules owns, which way dependencies run, the two import cycles
  `export`↔`viz` and `mcp`↔`http_server`↔`tasks`, that `langs`/`walker`/`layout` are re-export
  shims rather than modules, and where a change of each kind goes).
  **`docs/parser-development.md`** — adding a language end to end: `LangConfig`'s four keys, how to
  find real tree-sitter node types, `_BASE_NODES` for inheritance clauses, optional per-language
  import resolution, the two tests to write, and the six documents a test will fail without.
  **`docs/TRIAGE.md`** — what makes an issue workable, the triage buckets, what qualifies as
  `good first issue`, and the proposed label taxonomy. **`docs/COMMUNITY.md`** — issues vs.
  Discussions routing and the Discussions categories. **`docs/good-first-issues.md`** — seven
  starter tasks, each with a file-and-line pointer, acceptance criteria, and the specific catch
  that makes it harder than it reads.
- **`docs/issue-triage-2026-09-25.md`** — a full pass over all 84 open issues. Classifies every one
  into a single bucket (15 bug, 29 enhancement, 2 documentation, 8 chore/ci/refactor, 2 duplicate,
  21 needs-reproduction, 4 proposed out-of-scope, 3 epic), and records what the pass turned up:
  two duplicates (#295⊂#315, #291 superseded by #407), five issues describing behaviour that
  already exists or is documented as deliberate (#289's edge filters, #283's MCP ceiling, #263's
  deployment docs, #287's residual-chunk threshold, #349's documented import cycle), and four
  issues about the same budget vocabulary with none referencing another. Drafted replies included;
  **nothing was applied** — no issue was closed, relabelled or commented on.
- **Two issue templates.** `incorrect_edge.yml` for a wrong or missing `CALLS`/`IMPORTS`/`INHERITS`
  edge, which gates on the documented blind spots (dynamic dispatch, reflection, DI, generated
  bindings) and on index freshness before the report is filed; and `language_support.yml`, which
  asks for the tree-sitter node types and points at the parser guide.
- **`tests/test_i18n_consistency.py`** — the first guard on any relationship
  between the six READMEs, which is why all six had drifted together. 25 cases
  pin, across every language at once: that each translation is linked from the
  English switcher; that every `LANG_CFG` grammar appears in every README (the
  exact drift recorded under *Changed* below); that `GraphRAG` never appears
  above the `## 📐` architecture heading; that `GraphRAG`, `BM25`,
  `pack_context()`, `AST` and `RRF` never appear before the `## 👥` persona
  heading; and that each file still carries the positioning anchors
  (`explain retrieval`, `build --incremental`, `[cite:`, `CO_CHANGE`,
  `repo2graph-mcp`). Emoji section markers are the boundary because they are the
  only headings identical in all six files. No assertion compares one README's
  prose to another's — a translation legitimately differs in every sentence.
  `test_doc_consistency.py`'s language-token map was hoisted to a module-level
  `LANGUAGE_TOKENS` so both suites share one source of truth.
- **`repo2graph explain`** — three subcommands that answer "why did the graph say
  that?" without reading JSONL by hand. `explain edge <src> <dst>` reports every
  edge between two nodes in either direction, with each edge's own attributes and
  both endpoints' file locations, and says *which* node is missing when there is
  no edge. `explain node <id>` gives a node's metadata, in/out degree and chunks.
  `explain retrieval <query>` traces a query end to end: tokens, the BM25 short
  list with per-candidate matched terms and which of them became seeds, every
  graph hop walked out of those seeds, and the final chunks tagged `seed` or
  `expanded_neighbor`. All three take `--json` (Issue #309).
- **Lua**, the seventeenth grammar with full treatment — functions, classes and
  calls — bringing the extension count to 29 (Issue #77).
- **In-repo import resolution for eight more languages.** `IMPORTS` edges now
  resolve to files for Rust, C#, PHP, Kotlin, Scala, Swift, Ruby and Bash, where
  before only Python, JS/TS, Go, Java and C/C++ did. Rust understands `crate::`,
  `super::`, `self::` and the crate name read out of `Cargo.toml`'s `[package]`;
  PHP maps PSR-4-ish `App\` prefixes onto `app/` and `src/`; Ruby distinguishes
  `require_relative` from `require`; Bash resolves `source`/`.` against the
  sourcing file's directory (Issue #355).
- **Structured import extraction for Swift, Ruby and Bash**, so those languages
  contribute modules, names and aliases to `ImportDetail` rather than only a raw
  string (Issue #343).
- **`repo2graph completion [bash|zsh|fish]`**, printing the shell setup line for
  tab completion. Completion itself comes from `argcomplete`, a new optional
  extra: `pip install "repo2graph[completion]"`. Nothing is required for the CLI
  to work without it (Issue #356).
- **An official `Dockerfile`** — multi-stage, non-root (10000:10000), and
  compatible with a read-only root filesystem and `--cap-drop=ALL`, matching the
  hardening `docs/ENTERPRISE_DEPLOYMENT.md` already asked operators to apply
  (Issue #327).
- **`--cochange-min`**, and CO_CHANGE edges that carry their own provenance. The
  co-change threshold was a hardcoded 3; it is now a flag, and every emitted edge
  records `cochange_count`, `sampled_commits` and `min_pairs` so a reader can see
  what evidence produced it and at what setting. `stats.json` gains
  `cochange_sampled_commits` and `cochange_min_pairs`. The docstring on
  `add_cochange` now states the whole formula — depth cap, `--no-merges`, the
  25-file bulk-commit filter, path filtering and the threshold — in one place
  (Issue #280).
- **`--allow-auto-build`** for `repo2graph-mcp`, which re-enables auto-building a
  missing index in HTTP mode. See the note under Changed (Issue #265).
- **Four new GitHub Action inputs.** `incremental`, `parse-policy` and
  `max-call-candidates` expose build flags that were previously CLI-only (Issues
  #346, #311). `commit-force` makes the push to `commit-branch` a plain push
  instead of a force-push; alongside it, the action now refuses outright to push
  from a fork pull request and warns on `pull_request`/`pull_request_target`,
  with the reasoning written up in `docs/ACTION_SECURITY.md` (Issue #310).

### Changed

- **`.github/CONTRIBUTING.md` corrected on two points that would have cost a
  contributor a round trip.** It said to fork and branch from `main`; feature and
  fix PRs target **`develop`**, and `main` only moves via a promotion PR or the
  release bump. And it listed two lint commands where CI runs three —
  `ruff format --check .` is a gate separate from `ruff check .`, and
  `make lint` alone does not include it. Also added: a routing table to the new
  guides, the full CI gate list, the test house style (pin literal values; prove
  a new test is a detector), and a pointer to the starter tasks — the "Good first
  issues" section previously said none were filed.
- **`.github/SECURITY.md` leads with how to report.** Reporting was the last
  section of seven; it is now the first, with a direct private-advisory link,
  acknowledgement and disclosure expectations, what to include, an explicit
  in-scope list, and the three things that are documented behaviour rather than
  vulnerabilities (a missing edge, a `1/n` ambiguous `CALLS` edge, a stale index).
- **`docs/publishing.md` documents the branch model and a pre-release checklist.**
  The automated release flow was covered; what was missing was that `develop` is
  promoted to `main` first, that `develop` is protected from the repo-wide
  auto-delete because promotion PRs use it as the head branch, and the six things
  to verify before cutting.
- **The bug and feature templates.** Bug report now gates on index freshness and
  on `docs/limitations.md`, asks for `repo2graph doctor` output, and redirects
  edge reports to the new template; its version placeholder was stale at `1.5.1`.
  Feature request became **Feature proposal** and now asks which surface it lands
  on, for one checkable acceptance criterion, and whether the filer wants to
  implement it. The issue chooser's contact links now route questions, support,
  ideas and showcases to Discussions.
- **Positioning rewritten around one outcome: "give coding agents trustworthy,
  cited answers about unfamiliar codebases."** The README hero no longer opens on
  "AST-driven code graphs & zero-dependency GraphRAG"; `GraphRAG`, `AST-driven`,
  `BM25` and `pack_context()` now appear only in "Architecture & token economics"
  and below. Three sections are new: **Who it's for** (four personas — onboarding
  developer, coding-agent user, PR reviewer, maintainer — each with the first
  command to run), **Why repo2graph instead of grep or vector search?** (a
  three-way table that concedes grep is the right tool for a literal string), and
  **What it does — and what it does not** (eight limitations on the first-time
  reader's path, not only in `docs/limitations.md`). The same outcome sentence now
  drives the PyPI summary (`pyproject.toml`), the MCP registry entry
  (`server.json`) and the Action's Marketplace blurb (`action.yml`); eight
  audience-facing PyPI keywords were added. The rationale, the jargon policy, the
  per-surface before/after audit and ready-to-paste copy for the surfaces that are
  not files in this repository (GitHub description and topics, the landing page)
  are in the new root **`POSITIONING.md`**.
- **`docs/limitations.md` gained two limitations that were real but undocumented:**
  dependency injection (the `CALLS` edge lands on the interface declaration or
  fans out across every same-named implementation, never on the class the
  container injected) and **stale indexes** (the index is a snapshot, nothing
  watches the filesystem, and `repo2graph doctor` checks index integrity and
  vector drift — not whether your working tree moved on). `docs/why-graph.md`
  gained an embedding-search section; `docs/README.md`'s "when to use" bullets
  became a persona routing table.
- **The parsed-grammar count is 17 everywhere.** `docs/comparison.md` said 15 in
  two places, the README's comparison table said 16, and all five translated
  READMEs said "16 grammars / 28 extensions"; `LANG_CFG` has held 17 grammars
  and 29 extensions since Lua landed.
- **The five translated READMEs carry the new positioning.**
  `docs/i18n/README_{de,es,fr,ja,zh-CN}.md` were retranslated — not reduced to a
  stub link — for the hero, the intro, the persona table, the grep/vector
  comparison and the does/does-not table, at the same level of abridgement they
  already used.
- **HTTP mode no longer auto-builds a missing index.** Read-only network tool
  calls could previously trigger parser execution, file writes and git
  interactions on a server an operator had only pointed at a directory. A tool
  call against an unindexed directory now returns 503 with instructions instead.
  **Upgrade note:** an HTTP deployment that relied on the first tool call
  building the index must either pre-build it or pass `--allow-auto-build`.
  Local stdio mode is unchanged — it still builds on first use (Issue #265).

### Fixed

- **The version bump covered five files; the version was written in eleven.**
  `bump_version.py` rewrote `pyproject.toml`, `server.json`,
  `repo2graph/__init__.py`, `CHANGELOG.md` and `uv.lock`, and
  `check_version.py` verified the first three. Neither knew about the
  *documented* surfaces, so after 2.0.0 shipped the README still told users to
  write `uses: Srinivasan-78/repo2graph@v1` and described `@v1` as following
  "every 1.x release" — a live instruction to pin to a dead release line — while
  `docs/ENTERPRISE_DEPLOYMENT.md`, which tells operators to pin rather than
  float, showed `repo2graph[mcp]==1.6.0`. 29 stale sites across 7 files, now
  corrected to 2.0.0.
  The list is one table, `scripts/version_surfaces.py`, read by the bump, by the
  check, and by `publish.yml`'s `--files` (previously five hand-typed paths, one
  place for the same drift to recur). It is an allowlist, not a glob:
  `benchmarks/results.json` and `examples/*/manifest.json` record which version
  *produced* an artifact, and `docs/github-action.md`'s `repo2graph>=1.4,<2`
  illustrates the form of a range spec — a bump must leave all of those alone,
  which is asserted. A surface whose pattern matches nothing is now an error
  rather than a silent pass, since that is how the `@v1` drift went unnoticed.
  `check_version.py` also runs in CI now; it was a pre-commit hook and a
  release-time step and nothing in between, so it only fired for contributors
  who had installed pre-commit, and otherwise first fired at the point its own
  docstring calls "a burnt version number that PyPI will not let you reuse".
  `bump_version.py` now fails rather than warns when `uv` is missing, because
  the pypi job installs with `uv export --locked`.
- **`explain retrieval` traced a walk the retrieval never made.** It called
  `Index.expand()` without `edge_dirs`, so it inherited `DEFAULT_EDGE_DIRS`
  (`DEFINES: ("in",)`, `IMPORTS: ("out",)`, `INHERITS: ("out",)`) while the
  `Index.retrieve()` it printed twelve lines later passes `ALL_EDGE_DIRS` — the
  narrowing AGENTS.md already documents, in a new caller. The command reported
  `Runner.run → Runner DEFINES in` for a chunk that came back labelled
  `DEFINES out of main.py`, and dropped every DEFINES-out / IMPORTS-in /
  INHERITS-in hop from the trace entirely. Its seed list was also
  `list(a set)`, so the traced order — which is part of the traversal, since
  `expand()` walks its frontier in order under a per-hop cap — changed with
  `PYTHONHASHSEED`: three runs of one command, three answers. The seed loop now
  also honours `budget_chars` the way `retrieve()` does, so `selected_as_seed`
  cannot disagree with the seeds actually used on a large index.
- **The action's `parse-policy` input offered a value the CLI rejects.** It was
  documented and defaulted as `lenient`; `repo2graph build --parse-policy`
  accepts only `best-effort`, `warn`, `strict`. It survived because the step
  drops the value when it equals the default, so the one invalid value was also
  the one that never reached the CLI — any correction to that guard would have
  started forwarding it. Now `best-effort` throughout, with a test that reads
  the accepted values off the live parser rather than restating them.
- **Two builders could both hold the build lock.** `BuildLock` reclaimed a lock
  whose file was older than `stale_threshold` (1 hour) by unlinking it —
  regardless of whether the holder was alive. `_write_metadata` runs once, at
  acquire, so the file's mtime measures how long the holder has been *working*,
  not whether it is stuck: any build slower than the threshold was joined by a
  second one. Because `flock`/`msvcrt.locking` attach to an open file rather
  than a path, the second builder's lock on the freshly created file conflicted
  with nobody, and `dump_all`'s directory swap then ran twice over one index.
  A second, narrower instance of the same shape: `release()` unlinked the path
  unconditionally, so a waiter that opened the path just before that unlink
  locked a now-nameless file while the next process created and locked a new
  one. The OS lock is now the only authority on whether the lock is held — it
  is released by the kernel when its holder dies, so a lock file left by a
  crash is already acquirable and never needed reclaiming. `acquire()`
  additionally verifies that the descriptor it locked is still the file the
  path names, and `release()` only unlinks a name that still refers to its own
  file. `stale_threshold` now enriches the timeout diagnostic instead of
  licensing a takeover.
- **A failed index swap that also failed to roll back said nothing useful.**
  `_atomic_dir_swap` swallowed the restore error and re-raised the original, so
  an operator was left with a missing index and a `.<name>.backup.<pid>`
  directory they had no reason to look in. The raised error now names it.
- **The official image had no `git`.** `python:3.12-slim` does not ship it, and
  repo2graph shells out to git rather than reimplementing it: `walker.discover`
  prefers `git ls-files` and falls back to an `os.walk` that does not honour
  `.gitignore`, so the documented `docker run … repo2graph build /repo`
  indexed a different file set than every other way of running the same build,
  `--git-history` produced nothing, and `doctor` failed its own `check_git`.
  The runtime stage now installs git, and declares `safe.directory` through
  `GIT_CONFIG_*` rather than a config file, since the documented run is
  `--read-only` and mounts a host checkout owned by another uid.
- **`add_cochange`'s new formula docstring named the wrong cap.** It said
  `MAX_COCHANGE_COMMITS = 1000`; the constant is 5000, which is also what
  `docs/cli.md` tells operators.

### Security

- **Index files are read under a size ceiling.** `embed._npy_read` and
  `load_vectors`, `integrity.verify_artifacts` and `doctor` all read index
  files whole with `.read()`/`read_text()`. An index is routinely consumed from
  elsewhere — the `graph` branch, Action artifacts, `examples/` — and `doctor`
  on a received index is the documented way to check one, so those sizes are
  attacker-chosen and each read was an unbounded allocation driven by a file
  somebody else wrote. All five sites now go through `integrity.read_bounded`,
  which stats the open descriptor and refuses anything over its limit
  (`MAX_METADATA_BYTES` 256 MB for `manifest.json` and `vectors.meta.json`,
  `MAX_VECTORS_BYTES` 512 MB for `vectors.npy`). Bounding `vectors.npy` alone
  would not have closed this: `load_vectors` reads the sidecar first, so the
  whole allocation stayed reachable through that file.
- **`auth_modes` and `budget_tokens` are no longer redacted out of the audit
  log.** `SECRET_KEY_RE` matches by substring, so field names describing a
  *shape* — which auth is in force, how large a request was — were replaced
  with `[redacted:key:...]`, costing an operator the two fields most worth
  reading back and revealing nothing in exchange. A short explicit
  `NON_SECRET_KEYS` allowlist exempts them from the **name** test only; their
  values still go through the credential-shape, URL and path checks, so a
  token that turns up under one of those names is still redacted. The
  allowlist rather than a narrower regex: loosening the pattern would also stop
  matching names nobody has written yet, and that failure mode is a credential
  in a log.
- **The HTTP transport no longer tells a caller where its index lives.** Found
  by an audit of the transport, not reported. When no index exists,
  `open_index` raises `SystemExit` with a message written for an operator at a
  terminal that names the absolute index directory — twice — and `_call_tool`
  relayed `str(exc)` verbatim as the 503 body, so a caller (and the context
  window of any agent driving the server) received the host's filesystem
  layout. That is the disclosure `_public_repo_label` already refuses one
  endpoint over. The 503 stays actionable per #265 but uses placeholders
  (`repo2graph build <repo> -o <index-dir>`); the real directory goes to the
  audit log, which is server-side. The rest of the transport audit found no
  further issues: token comparison is constant-time, algorithm confusion is
  refused by an RSA-only table plus a `kty` check, `Host`/`Origin` are
  validated before the body is read, response headers are sanitised against
  splitting, bodies are `Content-Length`-only and size-capped with a
  `RecursionError` guard on deeply nested JSON, and the non-loopback bind
  guard fails closed.
- **Quadratic blowup scanning for PEM private keys (denial of service).** Found
  by a ReDoS pass over `secrets.py`, not reported. The `private_key` pattern was
  `BEGIN` followed by a lazy `[\s\S]*?` to an *optional* `END`, so every `BEGIN`
  whose `END` is missing re-scanned the entire remaining text before the
  optional group gave up — O(n²) in the number of `BEGIN` markers. Repository
  content is attacker-supplied on every build and `max_file_bytes` defaults to
  1.5 MB, so one committed file of repeated `-----BEGIN RSA PRIVATE KEY-----`
  lines cost roughly eight minutes of CPU per build (measured: 4× time per 2×
  input, 5.5s at 160 KB). `BEGIN` and `END` are now separate anchors paired by
  `_pem_spans` in one linear pass: the same 1.5 MB worst case takes 269 ms.
  Detection is unchanged — a complete block still spans `BEGIN` through `END`,
  an unterminated one still yields its header, and line-preserving redaction
  still holds. The other patterns were measured at the same sizes and are
  linear.
- **GHSA-mqm8-mc66-wjvj (high) — OIDC JWKS fetch could be downgraded to
  cleartext by redirect.** `auth._fetch_json` checked `https` on the URL it was
  handed, then called `urlopen`, which follows redirects using a handler that
  accepts `http`, `https` and `ftp`. An issuer's discovery document or
  `jwks_uri` could therefore `302` to `http://`, putting the signing keys on the
  wire in clear; an on-path attacker answering that request substitutes their
  own modulus and then satisfies every remaining check in the module — `alg`,
  `use`/`key_ops`, `iss`, `aud`, `exp` and a genuinely valid signature over
  their own key — to authenticate as any `sub`. Fetches now go through a
  module-level opener whose redirect handler refuses any non-`https` hop and
  caps redirects at 3. Affected deployments: `--auth-oidc-issuer` on the HTTP
  transport.
- **GHSA-f896-f643-87cf (medium) — `jwks_uri` was unconstrained.**
  `JWKSCache._resolve_jwks_uri` cross-checked the discovery document's `issuer`
  but took `jwks_uri` at face value, so one field in a document fetched over
  the network relocated the trust anchor for every authentication decision to
  any host. It must now share the issuer's scheme and host. The `issuer` field
  is also required rather than optional: the old `if declared and ...` skipped
  the mismatch check entirely when the field was absent, letting a document opt
  out of being compared by omitting it. RFC 8414 §3.2 makes it REQUIRED.
- **GHSA-6wrx-c2rg-mvm9 (medium) — `repo2graph doctor` was an arbitrary-file
  hash oracle.** `integrity.verify_artifacts` built each path to check as
  `out / rel_path` where `rel_path` is a key from the index's own
  `manifest.json` — untrusted input, since this project ships indexes on a
  `graph` branch, as Action artifacts and in `examples/`, with `doctor` as the
  documented way to check a received one. `pathlib` discards the left operand
  when the right is absolute, so an absolute key read any file the process
  could reach and echoed its real `sha256` into `report.errors`, while a
  missing file reported differently from a mismatching one, probing for
  existence. Keys that are absolute, or that resolve outside the index, are now
  reported as a corrupt manifest before anything is read. `checksums` keys were
  the only manifest-derived values used as paths; `written` and `entrypoints`
  were audited and are not.
- **`rag --answer` no longer lets a provider redirect walk off with the API key.**
  Found by sweeping the codebase for the class behind GHSA-mqm8-mc66-wjvj, not
  reported separately. `stream_answer` called `urlopen`, which follows
  redirects, and CPython's handler forwards every header except
  `content-length`/`content-type` to the new target whatever host or scheme it
  names — so a provider answering `302 http://elsewhere` was handed the
  `Authorization` / `x-api-key` / `x-goog-api-key` header in clear. It also made
  `_disclose` untrue, since the hostname printed to stderr before the first byte
  would not be where the request ended up. Provider redirects are now confined
  to the same origin, may not downgrade https to http, and are capped at 3; an
  http-to-https upgrade on the same host is still allowed, because `OLLAMA_HOST`
  is legitimately plain http. Sweep result for the three advisory classes:
  `auth.py` and `answer.py` were the only redirect-following calls (no others
  exist), `integrity.py` held the only untrusted path join, and there is no
  archive extraction, no `pickle`/`eval`/`yaml.load`, and no `shell=True`
  anywhere in the package.
- `secrets.scan_content_secrets` returns `(type, start, end)` spans instead of
  `(type, start, end, matched_text)`. No caller ever read the fourth element --
  `redact_content` wanted only its newline count and `chunks._process_chunk_content`
  reports `f[0]` -- so every scan built a list of live credentials that existed
  purely to be discarded, one `emit(..., findings=findings)` away from becoming
  the leak the module exists to prevent. Callers needing the bytes hold `text`
  and can slice the span. Internal API: not exported from `repo2graph.__all__`.

## [2.0.0] — 2026-09-22

### Removed

- `claude-code-review.yml` and `claude.yml`, and with them all automated code
  review. `claude.yml` had no author gate of any kind: any commenter, including
  a first-time contributor, could start a 30-minute model run by typing
  `@claude`. `claude-code-review.yml` excluded fork PRs entirely to keep its
  token away from untrusted heads, so it never reviewed the contributions most
  worth reviewing. prod-igy is now the only place a model is invoked.

### Added

- **Scoped call resolution and evidence transparency**: Call targets are resolved
  through 8 lexical proximity tiers, most specific first (`self_recursive` ->
  `same_class` -> `same_file` -> `import_alias`/`imported_symbol` -> `same_module`
  -> `unique_global_name` -> `ambiguous_global_name` -> `unresolved_external`)
  rather than blind global matching. `self_recursive` is a top-level function
  calling its own name, the one self-call with no second reading. A method's own
  name is never claimed outright -- the receiver is not recorded, so `self.f()`
  and `other.f()` are the same input here -- and resolves through `same_file`
  with the caller left in the candidate set.
  Each `CALLS` edge records `resolution_kind`, `candidate_count`, `scope_distance`,
  and `call_kind` (`static`, `dynamic`, `decorator`, `possible`); the
  `CALLS_EXTERNAL` edge used for `unresolved_external` carries `resolution_kind`
  and `candidate_count` but no `scope_distance`
  (Issues #270, #271, #273).
- **Import and alias resolution**: Multi-language AST import parsing extracts modules,
  imported symbols, and local aliases (`ImportDetail`), mapping calls to their aliased
  targets and tracking `imports_resolved` vs `imports_unresolved` in graph stats (Issue #272).
- **Graph quality metrics & reporting**: Added `quality_metrics` to `manifest.json`
  and enhanced `repo2graph stats` with a new `--format text` human-readable summary.
  `repo2graph stats` still prints the raw `stats.json` by default, unchanged from
  before this feature; `--format json` and `--json` are explicit spellings of that
  same default (Issue #274).
- **Parser strictness policies**: Added `--parse-policy best-effort|warn|strict` flag
  to `build` and `github` commands. In `strict` mode, tree-sitter AST syntax errors
  raise a typed `ParseError` and halt the build with clear diagnostics (Issue #275).
- **Subtyped inheritance & interface extraction**: Extracted class bases carry
  relationship subtypes (`INHERITS`, `IMPLEMENTS`, `EXTENDS`, `MIXES_IN`) alongside
  the primary `INHERITS` edge type for backwards compatibility. Built-in and framework
  base types without repo-local definitions are guarded against false cross-project
  linkages (Issue #276).
- **Path precedence hierarchy & explain-path**: Documented the real discovery
  precedence order `explain_path` evaluates (`outside_root` through
  `binary`/`included`, steps 0-10) and introduced `repo2graph explain-path <path>`,
  which checks one path against those rules and reports the single rule that
  decided it — precedence step, rule id, decision and reason — not a
  step-by-step trace (Issue #277).
- `repo2graph doctor [path]` command diagnosing Python version, package version,
  tree-sitter & grammar availability, Git integration, directory permissions,
  existing artifact integrity, vector correspondence, MCP SDK compatibility,
  and platform encoding. Safely probes LLM provider configuration without ever
  disclosing secret values or environment variables. Supports `--json` output
  (Issue #308). Fixed during review, before release: the provider-env probe
  was disclosing each key's last 2 characters and exact length; artifact
  integrity treated any top-level `agent/` directory or `chunks.jsonl` as a
  (possibly corrupt) repo2graph index, which false-positived on unrelated
  projects using those same generic names; the MCP SDK check only echoed the
  installed version instead of verifying `mcp.server.Server` is actually
  importable, the thing `repo2graph-mcp` needs; and a probe against a
  not-yet-created nested path left every directory it created behind instead
  of cleaning up.
- Automated documentation consistency test suite (`tests/test_doc_consistency.py`)
  guaranteeing zero drift between code and documentation across CLI subcommands,
  parser language configurations, GitHub Action inputs/outputs, and MCP registered
  tools (Issue #319).
- Secure-by-default secret exclusion across all artifact flows (`build`, `github`,
  auto-build `query`/`rag`, GitHub Action, and MCP). Sensitive files (`.env*`,
  private keys, certificates, credentials, `.ssh`, `.aws`, `.gnupg`) are excluded
  automatically. `--include-secrets` provides explicit opt-in (Issue #260).
- Content-aware secret scanning and line-preserving redaction in chunking. Detects
  AWS keys, GitHub tokens, Slack tokens, OpenAI keys, Google API keys, PEM private
  keys, JWTs, and database credentials with configurable policy (`--secret-policy
  redact-match|exclude-file|warn-only|off`) (Issue #261).
- Structured logging and audit log sanitization. Centralized sanitization with
  recursion depth ceiling (12), container size limits (128 items), cycle detection,
  and credential scrubbing for basic-auth URLs and sensitive headers (Issue #262).
- Dedicated secrets hardening test suite (`tests/test_secrets_hardening.py`) verifying
  path filtering, content redaction, false-positive resistance, log sanitization, and
  GitHub Action flag propagation.
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
