# Backlog — deferred audit findings

Work that was found, understood, and deliberately not done — each entry with the
reason. This is the closest thing the project has to a roadmap, and the place to
look for a first contribution: an item here has already been scoped and argued
for, so picking one up starts from a decision rather than a blank page.

## Deferred by the 2026-09-17 enterprise-hardening audit

Full detail, evidence and severity reasoning: `docs/SECURITY-AUDIT.md`. Two genuine gaps found by
that audit were fixed directly (audit-log `error`-field redaction, MCP string-argument length
caps, a fork-PR secret-exposure guard on `claude-code-review.yml`); these are the rest, deliberately
not fixed in that pass because each needs its own scoped, reviewed change rather than a drive-by
edit alongside a security audit.

| Item | Size | Why deferred |
|---|---|---|
| **No SBOM generated in CI** *(Shipped)* | S | `dependency-audit.yml` now has a "Generate CycloneDX SBOM" step (`pip-audit --format cyclonedx-json`) and uploads `sbom.cyclonedx.json` as an artifact. |
| **No per-file tree-sitter parse timeout** | M | `MAX_BYTES` bounds file size, not parse time. `tree_sitter.Parser.set_timeout_micros` support varies across grammar bindings in `tree-sitter-language-pack`; a wrong per-language timeout risks truncated parses on legitimately large generated files with no fixture to prove the value is well-calibrated. |
| **No independent byte-size cap on `git log --name-only` cochange output** *(Shipped)* | XS | `graph.py` now has `MAX_COCHANGE_BYTES` (10 MB) and streams the pipe through `_read_capped`, so `add_cochange` bounds output bytes independently of `MAX_COCHANGE_COMMITS`/`COCHANGE_TIMEOUT`. |
| **Secret-path denylist (`query.py` `SECRET_KEYWORDS`/`SECRET_DIR_NAMES`) is not user-configurable** | S | Solid and independent of `.gitignore`, but a hardcoded `frozenset` — an org with nonstandard secret-file naming can't extend it without a code change. Needs a CLI flag / config file design, not a quick patch. |
| **HTTP transport returns `str(exc)` verbatim to the client** *(Shipped)* | S | The generic-`Exception` handlers already sent a fixed "Internal server error" message; the one remaining leak was the `SystemExit` branch in `_call_tool` (`http_server.py`, was line 617), which echoed `open_index`'s exit message — including the on-disk index path — straight to the caller. Now sends a fixed "Index unavailable"; the real message still reaches the (redacted) audit log via `error=str(exc)`. |
| **No enforced cap on total graph nodes/edges/files** | M | `graph.py`'s `max_files` is opt-in, defaults unbounded. `Graph.nodes`/`edges` are fully in-memory with no size guard, unlike the already-streamed chunk emission path. |
| **No explicit `attestations:` flag on the PyPI publish step** *(Shipped)* | XS | `publish.yml`'s `pypa/gh-action-pypi-publish` step now passes `attestations: true` explicitly. |
| **TOCTOU symlink race between `discover()`'s `lstat()` and the later `open()`** | — | Documented as a known limitation, not fixed: requires local code execution on the same host to exploit (a stronger position than repo2graph could additionally defend against), and `O_NOFOLLOW` is POSIX-only, so no fix closes it cross-platform. See `docs/SECURITY-AUDIT.md` P3.1. |
| **No benchmark above 3,000 files** | L | `docs/PERFORMANCE.md` has real measurements at 90 and 3,000 files; nothing was run at 50k/100k+ in this pass (time budget). Overlaps the pre-existing BACKLOG item below, "a fixture above `PARALLEL_MIN_FILES`." |

Note: the SBOM, byte-cap, and attestations rows above were already shipped by the time of a
2026-09-21 pass through this backlog — this file had drifted from the code. Only the `str(exc)`
leak in the `SystemExit` branch was still genuinely open; it's fixed now. Treat every row in this
file as a claim to verify against current code before acting on it, same as any other memory of
past state.

Route back to the work the 2026-09 whole-repo audit found but did not fix in the
first batch. Full detail lives in `docs/BUILD_STATE.graphrag-2026-09.md`, the
archived state file from that run (`## Plan` MASTER ISSUE TABLE = all 53
findings; `## Improve` = the grouped backlog and loop retro). `docs/BUILD_STATE.md`
always holds the *current* build-app run, not that one.

**Epic:** [#31 — Epic: post-audit backlog](https://github.com/Srinivasan-78/repo2graph/issues/31)

| Issue | Scope | Covers | Priority |
|-------|-------|--------|----------|
| [#26](https://github.com/Srinivasan-78/repo2graph/issues/26) | fetch.py hardening round 2 | ISS-21, SH-2, SH-3, NC-4, NC-5 | P1 |
| [#21](https://github.com/Srinivasan-78/repo2graph/issues/21) | parse.py & cross-module string/correctness one-liners | ISS-03/04/05/09/11/12/41/42, NC-6 | P2 |
| [#23](https://github.com/Srinivasan-78/repo2graph/issues/23) | export.py correctness & GraphML hardening round 2 | ISS-28/29/30/31, SH-4 | P2 |
| [#24](https://github.com/Srinivasan-78/repo2graph/issues/24) | viz.py UX + safety | ISS-33/34/35/36 | P2 |
| [#25](https://github.com/Srinivasan-78/repo2graph/issues/25) | query.py retrieval budget + scoring hygiene | ISS-37/38/39 | P2 |
| [#27](https://github.com/Srinivasan-78/repo2graph/issues/27) | walker.py discovery hygiene | ISS-14/15, SH-5 | P2 |
| [#28](https://github.com/Srinivasan-78/repo2graph/issues/28) | Test coverage round 2 | ISS-52/53, SH-6, NC-1/2/3 | P2 |
| [#29](https://github.com/Srinivasan-78/repo2graph/issues/29) | CI, supply-chain & workflow/doc hygiene | ISS-43/46/47/48/49 | P2 |
| [#30](https://github.com/Srinivasan-78/repo2graph/issues/30) | authormark: refresh stale Fingerprint lines on batch-1 files (not merge-blocking — CI checks presence only) | AC-16, SH-7 | P2 |
| [#22](https://github.com/Srinivasan-78/repo2graph/issues/22) | chunks.py line-span accuracy, id scheme & tidy | ISS-23/24/25/26 | P3 |

All child issues carry the `backlog` label. To work one, run a `/build-app`
refactor loop scoped to a single issue (or a single module group).

## Deferred by the MCP / vectors run (2026-09)

Ranked by the IMPROVE phase of that run. Size is rough effort, not risk.

| # | Item | Size | Why this rank |
|---|------|------|---------------|
| 1 | **CI job that installs the `[mcp]` extra and does one stdio round trip** *(Shipped)* | S | **Shipped in PR #54:** CI installs `[dev,mcp]` and runs `test_ac34_stdio_server_roundtrip`, exercising `serve()` end-to-end over stdio JSON-RPC. (Previously `serve()` had no automated test coverage). |
| 2 | **Say so when fusion silently switches itself off** *(Shipped)* | S | **Shipped:** `_vectors_for` now returns a reason, `score_rrf` emits a `rag_fusion_disabled` JSON line on stderr and records `Index.fusion_coverage`, and `repo2graph embed --verify-rag` self-tests the whole path. |
| 3 | **Port the MCP server to the 2.x SDK API** (detail below) | M | Deliberate deferral, not debt — but the `<2` pin ages, and 1.x will stop getting fixes. |
| 4 | **Graph-level incremental rebuild** *(Shipped)* | L | **Shipped as `repo2graph build --incremental`.** Resolved the way the analysis below predicted it had to be: cache `ParsedFile` per file, re-run the *whole* resolution phase every build. See "Incremental rebuild, as shipped". |
| 5 | **A real `sentence-transformers` smoke test, opt-in and network-gated** | S | Every embedder in the suite is `StubEmbedder`. `default_embedder()` is tested only for its *failure* message, so nothing proves the real wrapper's `model_id`/`dim` agree with what `vectors.meta.json` records — the exact pair `fuse_ok` compares. |
| 6 | **`docs/BACKLOG.md` has no `@authormark` header** | XS | Pre-existing at baseline `ff0e3ca`; not introduced by this run, and deliberately not fixed here (the stamper is not vendored). Fold into the next watermark sweep, with issue #30. |
| 7 | **No coverage measurement anywhere in the repo** | S | ~130 tests were added this run on judgement alone. Nobody can currently answer "which branch of `embed.py` never runs". |
| 8 | **Auto-build cannot use the process pool** (detail below) | M | Correct but slower than it needs to be on a large repo. A hang was traded for serial parsing; only the first tool call pays. |
| 9 | **Every MCP fixture is under `PARALLEL_MIN_FILES`** | S | The pool hang below survived a green 73-test suite because `mini_repo` is 5 files and `big_index` is ~20. No fixture crosses 64, so the parallel path in `build()` is never exercised from a test. |

**Auto-build cannot use the process pool.** `mcp._build_index` pins `jobs=1`. `graph.build()`
switches to a `ProcessPoolExecutor` above `PARALLEL_MIN_FILES` (64) files, and spawning one from
inside the running stdio server hangs indefinitely: the workers inherit the parent's stdin and
stdout, which are the client's JSON-RPC pipes. Reproduced on Windows against this repo at 65 files
— handshake fine, first `tools/call` never returned. Serial is *faster* at the threshold (0.21s vs
0.45s here, the pool costing more to start than it saves), so the pin costs nothing until a repo is
large, where the first tool call is now noticeably slower than `repo2graph build` on the same tree.

To pick this up: give the pool workers explicit handles instead of the inherited ones — a
`preexec`/initializer that reopens `sys.stdin`/`sys.stdout` on `os.devnull`, or an executor created
before the transport is bound — and confirm on Windows specifically, which is where spawn (not fork)
makes the inheritance bite. Then drop the `jobs=1` pin and the test asserting it
(`test_auto_build_never_spawns_a_process_pool`). Add a fixture above 64 files first (item 9) or the
fix cannot be tested; note that a regression there hangs rather than fails, so any end-to-end test
needs its own timeout.

**Graph-level incremental rebuild — `build(..., previous: Graph)`.** Deliberately cut, not
forgotten. Edge invalidation is the obvious hard part, but the real blocker is one level up:
`build()` resolves `CALLS` through a *global* name index and sets
`confidence = 1/len(candidates)`. Adding or deleting a symbol named `run` in file A therefore
changes the confidence — and the count — of `CALLS` edges emitted from files B and C that did not
change at all, and `mark_entrypoints()`/`reach` is a whole-graph BFS on top of that. A merge that
reparses only the changed paths and splices their nodes/edges produces an index that is *wrong in a
way nothing detects*: stale confidences and stale entrypoint flags flow straight into
`chunks.jsonl` headers and into `pack_context`'s `min_confidence` gate — the same failure mode the
vector model-mismatch guard exists to prevent. Doing it correctly means caching `ParsedFile` per
file and re-running the *whole* resolution phase on every build (cheap: tree-sitter parsing is the
expensive part), which is a different design from `build(..., previous=)` and a run of its own.

What shipped instead is the safe, self-contained half: per-file sha256 in `agent/index.state.json`
(the substrate any incremental build needs) and vector reuse keyed on chunk *text* hash, which is
correct by construction because a chunk's vector depends on its own text and nothing else.

**Incremental rebuild, as shipped.** The analysis above was right about the blocker and right about
the fix, and the fix is what shipped — *not* `build(..., previous: Graph)`. `build()` gained
`cache=`, a `{path: entry}` map read from a new `agent/parse.cache.json`, and reuses a file's
`ParsedFile` when its sha256 *and* its language both still match. Everything downstream of parsing
is then recomputed from the complete symbol set, exactly as a full build does: the global name
index, `CALLS` confidences, `INHERITS`, `mark_entrypoints()` and `reach`. Nothing is spliced, so
none of the staleness this entry warned about can arise — a repo-wide confidence shift caused by a
symbol added in *another* file lands on the unchanged caller's edge, because that caller's edges are
rebuilt from its cached symbols rather than carried over.

The cost model is what makes this worth doing rather than a compromise: parsing dominates a build,
resolution is O(edges) and negligible, so recomputing all of it buys exactness for no measurable
time. Every file is still *read* — the content hash is the bytes, there is no cheaper way to know a
file is unchanged — and reading is the small half.

The acceptance test is whole-artifact byte equality against a full rebuild, across an add, a modify,
a delete and a no-op (`tests/test_incremental.py`). That is also why the hit/miss tallies live on
`Graph.incremental` and not in `Graph.stats`: `stats.json` is one of the artifacts compared, so a
counter that differs between the two routes by construction would have had to be special-cased out
of the comparison, weakening the very test that makes the feature trustworthy.

Deliberately *not* implemented: partial confidence recalculation over "affected symbol namespaces",
and a reverse-edge index for a partial reach BFS. Both were considered and rejected — they compute
the same answer as the full re-resolution above, cost more code and more ways to be subtly wrong,
and save time that is already close to zero. Should resolution ever become the bottleneck on a very
large repo, that is when to revisit them, with a profile in hand.

**Say so when fusion silently switches itself off.** `Index` drops vectors whose chunk ids are no
longer in `chunks.jsonl`, so a `chunks.jsonl` rebuilt without re-running `embed` degrades instead of
mis-aligning. The degrade is all-or-nothing, not partial: `query._vectors_for` builds
`[vectors[i] for i in candidates]` inside a `try/except (KeyError, IndexError, TypeError)` and
returns `(None, [])` on the *first* candidate that has no vector, so if any one of the top
`RRF_CANDIDATES` BM25 candidates is unvectorised, `score_rrf` abandons the dense ranking entirely
and returns plain BM25. There is therefore no "fuses on the part it has" coverage risk to guard
against — the ranking is never half-dense.

What is missing is the *report*. `Index.fuse_ok` only compares model id and width, so after a
rebuild without a re-`embed` it can pass, `--vectors` can report success, and fusion can then turn
itself off inside `_vectors_for` with nothing printed either way. Wanted: carry the coverage
fraction out of `_vectors_for` and have `--vectors` say `fused 0/8 candidates — re-run
repo2graph embed` rather than quietly answering a lexical question. Small.

**Port the MCP server to the 2.x SDK API, as shipped.** `repo2graph/mcp.py::serve()` now branches
on `supports_decorators = hasattr(Server, "list_tools")`: the 1.x decorator API
(`@server.list_tools()` / `@server.call_tool()`) when present, and the 2.x registration API
(`list_tools_2x`/`call_tool_2x` handlers) otherwise. `dispatch()` — the only place any logic lives
— is untouched, so the three handlers and every bounds test apply unchanged to both SDK
generations. The `mcp` extra is `mcp>=1.0,<3.0` (`pyproject.toml`, `SDK_SPEC` in `mcp.py`), and
`_require_sdk()` still exits with a clear instruction rather than a traceback when neither API
shape is present.

## Shipped in batch 1

Branch `audit/batch-1-encoding-hardening` — 15 High/Med in-scope fixes
(ISS-01/02/06/07/13/16/17/18/19/22/27/44/45/50/51) + 5 one-liners
(ISS-08/10/20/32/40) + SH-1. Suite: 73 passed, 2 skipped.
