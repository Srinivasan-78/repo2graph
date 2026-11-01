# Agent 6 — Documentation Audit

Scope: README.md, docs/{architecture,cli,mcp,python-api,comparison}.md, CHANGELOG.md, CITATION.cff,
CODE_OF_CONDUCT.md, LICENSE/LICENSES/REUSE.toml, .github/{CONTRIBUTING,SECURITY,distribution,
remediation-tracking}.md, .github/ISSUE_TEMPLATE/*, pull_request_template.md, outreach/*,
benchmarks/**/README.md, examples/**/README.md, editors/vscode/README.md, npm/README.md,
action.yml, server.json, glama.json, Dockerfile, Makefile. Read-only; no repo files other than
this report were modified. Verified against repo2graph/cli.py, cli_embed.py, cli_inspect.py,
__init__.py, graph.py, query.py, export.py, integrity.py, mcp/*, action.yml (parsed), and the
benchmark/perf JSON artifacts.

## Summary

This documentation set is unusually well-guarded by `tests/test_doc_consistency.py` (CLI command
presence, language list, action.yml inputs/outputs, MCP tool names, CITATION/npm/version
surfaces, exclusion groups, doctor-check names in both directions, module-reference table
completeness). Everything that test file enforces checked out clean by direct inspection. The
findings below are concentrated in what it does **not** enforce: free prose claims (network-call
exceptions, semver line number, CI Python matrix) and one internal arithmetic slip in docs/mcp.md.
No broken relative links or images were found across all 45 markdown files in the repository.
Benchmark percentages quoted in README.md were independently recomputed from
`benchmarks/real/results_holdout_final.json` and matched to the point of agreeing with
derived figures (e.g. "+7/+7/+398 mean tokens") that aren't literal fields in the JSON.

## Doc-vs-implementation discrepancies

### DOC-01 — docs/mcp.md miscounts "the first eight" tools (HIGH confidence, LOW severity)
- Location: `docs/mcp.md:240`
- What the doc says: "The first eight answer questions about the code and exclude secrets
  unconditionally... The last two report on the server itself..."
- What's true: the tools table (`docs/mcp.md:230-238`) lists 9 tools total:
  `repo_map, repo_search, repo_neighbours, repo_find_symbol, repo_read, repo_path_between,
  repo_blast_radius` (7 question-answering tools) then `repo_cache_stats, repo_build_status`
  (2 server-introspection tools) — confirmed against
  `repo2graph.mcp.TOOL_DESCRIPTIONS` (9 keys, same names, same split). "Last two" is correct;
  "first eight" should read "first seven" (7 + 2 = 9, not 8 + 2 = 10).
- Recommendation: change "first eight" to "first seven".
- Effort: trivial (one word).

### DOC-02 — README's "no network calls" exception list is narrower than SECURITY.md's (MEDIUM confidence HIGH severity on the claim, since it's a trust/security statement)
- Location: `README.md:22-27`
- What README says: "Building, querying and the MCP server make no network calls; the only
  exceptions are `rag --answer`..., `repo2graph github` and a `rag`/`query` target given as a
  GitHub spec..., and `--vectors`/`embed`..."
- What the code/SECURITY.md documents as additional outbound-network paths, absent from README's
  list: `build --neo4j-uri URL` (pushes the graph to a Neo4j/Memgraph server, `.github/SECURITY.md:53-55`),
  `rag --tokenizer tiktoken[:ENCODING]` (downloads a BPE table on first use, confirmed also in
  `docs/cli.md:346` and `.github/SECURITY.md:58-59`), and `repo2graph-mcp --transport sse`
  (opens a listening socket — inbound rather than outbound, so arguably outside README's claim,
  but SECURITY.md itself treats it as one of "every exception" in the same list README's prose
  echoes).
- Recommendation: either shorten README's claim to "see SECURITY.md for the full list" (it
  already links there once, at line 28, but only for the tree-sitter-pack rationale, not for this
  enumeration) or add `--neo4j-uri` and `--tokenizer tiktoken` to the parenthetical.
- Effort: small (one sentence).

### DOC-03 — README's "Status" section names the wrong major version line (HIGH confidence, LOW-MEDIUM severity)
- Location: `README.md:231`
- What it says: "The 3.x CLI, MCP tools and output schema follow semver."
- What's true: `pyproject.toml` is at `version = "4.1.0"`; `CHANGELOG.md` shows `## [4.0.0] —
  2026-10-12` and `## [4.1.0] — 2026-10-23` as released versions, i.e. the project shipped a
  major (breaking) bump to the 4.x line three weeks before the stated audit date. The sentence is
  stale by one major version.
- Recommendation: change "3.x" to "4.x".
- Effort: trivial.

### DOC-04 — `.github/SECURITY.md` describes a CI Python matrix that no longer exists (HIGH confidence, HIGH severity — it's the security/trust document)
- Location: `.github/SECURITY.md:210`, `.github/SECURITY.md:219`
- What it says: "...multi-platform test matrices across Python 3.10-3.13..." and "...`Test Suite`
  across `ubuntu-latest`, `windows-latest` and `macos-latest` (Python 3.10, 3.11, 3.12, 3.13)..."
- What the code actually does: `.github/workflows/ci.yml:144` sets
  `python-version: ["3.11", "3.12", "3.13", "3.14"]`, with a comment at `ci.yml:140-144`
  explicitly noting "3.10 was dropped". `pyproject.toml` floors at `requires-python = ">=3.11"`.
  `README.md:241` already states the correct, current matrix ("3.11 – 3.14 ... Python 3.10 was
  supported through 4.1.0 and dropped in the next release"). SECURITY.md was not updated when
  `af907bbe` ("feat!: drop Python 3.10 to clear the filelock advisories") landed.
- Recommendation: update both lines to "Python 3.11-3.14".
- Effort: trivial, but worth doing promptly since this is the document maintainers point
  enterprise reviewers at for the CI gate list.

## Broken links & paths

None found. Scanned all 45 `*.md` files in the repository for markdown link targets
(`](...)`) and `<img src="...">` references, resolved every non-`http(s)`/`mailto`/anchor-only
target relative to its containing file, and confirmed every resolved path exists on disk. This
includes README.md, every docs/*.md, every benchmarks/**/README.md, every examples/*/README.md
(14 repos) and overview.md pages, editors/vscode/README.md, npm/README.md, and the .github tree.

## Version/compatibility inconsistencies

- See DOC-03 and DOC-04 above (both are version-line staleness, not raw version-string mismatches).
- All machine-checked version surfaces (`pyproject.toml`, `repo2graph/__init__.py`,
  `server.json`, `npm/package.json`, `CITATION.cff`, `uv.lock`) read **4.1.0**, consistent, and
  match what `scripts/version_surfaces.py`/`tests/test_version_surfaces.py` enforce.
  `editors/vscode/package.json` is at `0.1.0` — this is **correct and intentional**: the VS Code
  extension is explicitly out of `version_surfaces.SURFACES` (it's a separate, unpublished preview
  artifact, not released alongside the PyPI package) and `editors/vscode/README.md` itself says
  "It is a preview, built from source; it is not on the Marketplace."
- Stale internal code-comment references to "this project supports 3.10" remain in
  `repo2graph/__init__.py` (around `_parse_toml`/`_pyproject_version`, e.g. "tomllib is 3.11+ and
  this project supports 3.10"). These are code comments, not part of the audited doc set, but
  they will mislead the next contributor reading that module, and should be cleaned up alongside
  SECURITY.md (DOC-04) in the same follow-up.
- `.github/remediation-tracking.md:27` records `requires-python | >=3.10 (classifiers
  3.10–3.13)` — this is a point-in-time remediation log entry (dated, historical), not a live
  claim, so it is lower priority than DOC-04, but flagging it in case the file is ever read as
  current status rather than history.

## Unsupported or stale claims

- **Benchmark numbers (verified correct):** every percentage in README's structural and lexical
  comparison tables (`README.md:69-73`, `86-90`) was recomputed from
  `benchmarks/real/results_holdout_final.json`'s `summary`/`structural_summary` arrays and
  matches to the nearest percentage point (e.g. structural @ 4,000 tokens: repo2graph 0.595 →
  "60%", bm25-only 0.476 → "48%", ripgrep 0.214 → "21%", all exact). The "+7/+7/+398 mean tokens"
  comparison (`README.md:78-79`) was likewise independently recomputed from
  `mean_tokens_used` fields and matches exactly (1972-1965=7, 3975-3968=7, 7978-7580=398).
- **Benchmark provenance note (INFO, not a defect):** `results_holdout_final.json` itself records
  `"repo2graph_version": "2.2.0"` — i.e. the held-out structural/lexical numbers README leans on
  most heavily were produced two major releases ago (current: 4.1.0), including before
  `--framework-edges` (4.0.0) and the dense-retrieval backend split existed. The numbers are
  faithfully transcribed from that artifact (see above), so this is not a doc-vs-code
  discrepancy, but it is a staleness risk worth a maintainer's attention: retrieval ranking
  changes since 2.2.0 are not reflected in the headline comparison table. The examples/README.md
  gallery is explicit and careful about exactly this kind of cross-version comparability problem
  (2.2.0 vs 3.0.0+ batches) — the real-world retrieval benchmark doesn't carry the same caveat.
- **`.github/distribution.md`** (an internal planning/claims-discipline doc, not user-facing)
  still describes the benchmark suite as "35 general + 10 structural tasks ... budget-matched at
  2k/4k/8k" (its "Already shipped" table). README.md now reports a different, larger held-out set
  ("40 lexical and 40 structural questions ... **not** tuned against") and explicitly calls the
  35+10 set "a regression set, not evidence" (`README.md:130-133`). distribution.md was not
  updated to match; low priority since it's an internal tracking doc, but it would mislead a
  contributor using it as a map of what currently backs the comparison claims.
- **Language-support claim ("nineteen" languages, README.md:212-220):** verified exactly —
  `repo2graph.parse.LANG_CFG` has 19 keys, matching the enumerated list (Python, JS, TS, TSX, Go,
  Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala, Bash, Lua, Objective-C, HCL). Correct.
- **"Two of those nineteen are benchmarked on real repositories" (README.md:222-223):** consistent
  with `benchmarks/real/README.md`'s stated corpus (Flask, requests, FastAPI = Python; Hono =
  TypeScript).

## Completeness gaps

1. **`tests/golden/cli_inventory.json` does not cover most of the CLI surface it claims to
   inventory.** It records flags only for `<root>`, `build`, `embed`, `gh`/`github`, `map`,
   `query`, `rag`, `stats`, `version` — it has **no entries at all** for `diff`, `explain-path`,
   `bug-report`, `index-status`, `demo`, `doctor`, `completion`, or `explain` (edge/node/retrieval),
   even though all eight are real subcommands in `repo2graph/cli.py` (`cli_inspect.py`'s
   `cmd_diff`, `cmd_explain_path`, `cmd_bug_report`, `cmd_index_status`, `cmd_demo`, `cmd_doctor`,
   `cmd_completion`, `cmd_explain`) and all eight are documented in both README.md's command table
   and docs/cli.md. This isn't a doc bug per se — docs/cli.md's prose for these commands was
   checked by hand against cli.py's argparse definitions and matches precisely — but it means a
   generated "golden" cross-check that a reviewer might trust as exhaustive silently is not, for
   roughly half the CLI. Worth flagging to whichever agent/test owns that fixture.
2. **Newcomer path (build → understand output → troubleshoot) is well covered**: `demo` →
   `README.md` starter questions → `docs/cli.md`'s citation-header walkthrough → `doctor`/
   `bug-report`/`index-status` for troubleshooting are all cross-linked and consistent. No gap
   found here.
3. **MCP integration docs (docs/mcp.md) are thorough** (per-client config blocks, server flags
   grouped by concern, pagination, JSON output shapes) but the "first eight" slip (DOC-01) is the
   one place internal cross-referencing within that single page breaks down.
4. **Contributing path** (`.github/CONTRIBUTING.md`) correctly routes a new contributor to
   `docs/architecture.md`'s module map; `test_architecture_module_reference_names_every_module`
   (in test_doc_consistency.py) keeps that table from silently missing new modules, so this axis
   is actively guarded and was not independently re-verified beyond confirming the test exists
   and reads as described.
5. **No README/docs page explains how to interpret a `confidence < 1.0` CALLS edge for someone
   who has never read docs/architecture.md's resolution section** — the term is used correctly and
   consistently everywhere (`cli.md`, `python-api.md`, `mcp.md`) but always assumes the reader has
   already read the "What it can't do" section of README or architecture.md §ambiguous calls. Not
   a defect, but a newcomer jumping straight to `docs/mcp.md`'s `repo_neighbours` row will see
   "confidence" used with no inline definition and no link on that occurrence specifically.

## Verified-accurate claims

- `docs/python-api.md`'s "stable surface" table and all code examples: `build()`, `Index()`,
  `Index.retrieve()`, `Index.pack_context()`, `dump_all()`, `verify_artifacts()` — every
  parameter name shown in the docs' examples (`budget_chars`, `min_confidence`, `expand_graph`,
  `exclude_secrets`, `rerank`, `g, chunks=, outdir=, formats=, viz_nodes=`) matches the live
  `inspect.signature()` of each function exactly.
- `docs/cli.md`'s `build` flag table (every flag in the shared `common`/`path_rules`/`build_out`
  argparse parent parsers plus `build`'s own, ~35 flags) matches `repo2graph/cli.py` line-for-line,
  including defaults (`--parse-timeout 10`, `--cochange-min 3`, `--max-file-mb 1.5`, etc.).
- `docs/cli.md`'s `query`/`rag`/`embed`/`diff`/`explain-path`/`bug-report`/`index-status`/`demo`/
  `doctor`/`completion`/`explain` sections all match their respective argparse definitions in
  `cli.py` and `cli_inspect.py`/`cli_embed.py`, including retired-no-op flags
  (`--neighbours`/`--conditional-expansion`/`--precision-first`) and flag aliases
  (`--retrieve-budget-chars`, `--context-budget-chars`, `--min-confidence`).
- `action.yml`'s full set of 37 inputs and 6 outputs are all documented in `docs/cli.md`'s Action
  section, with matching defaults (spot-checked `limit-policy: fail`, `max-chunks`/`max-nodes`/
  `max-total-bytes`/`max-build-seconds` all blank-default).
- `docs/mcp.md`'s tool table names exactly the 9 tools in `repo2graph.mcp.TOOL_DESCRIPTIONS`
  (`repo_map, repo_search, repo_neighbours, repo_find_symbol, repo_read, repo_path_between,
  repo_blast_radius, repo_cache_stats, repo_build_status`) — no extra, no missing.
- All relative links and images across all 45 tracked markdown files resolve to real files.
- README's benchmark tables and the derived mean-token deltas are numerically exact against
  `benchmarks/real/results_holdout_final.json`.
- `benchmarks/perf/README.md`'s "252,801 nodes, 602,800 edges" 50,000-file figure, echoed
  verbatim in `docs/cli.md`'s `--store` flag description (both occurrences), is internally
  consistent between the two files.
- Version surfaces (`pyproject.toml`, `__init__.py`, `server.json`, `npm/package.json`,
  `CITATION.cff`, `uv.lock`) are all `4.1.0`, consistent with each other and with the automated
  `test_version_surfaces.py`/`test_doc_consistency.py` guards.
- npm/README.md and docs/mcp.md agree, word-for-word in substance, that the npm package is not
  yet published to the registry and that `npx repo2graph-mcp` is not yet a working install path.
- The GitHub Action security guidance (fork-PR push refusal, `commit-force` restricted to branch
  names containing "graph", least-privilege permission blocks) in `docs/cli.md`'s Action section
  reads as a coherent, specific set of guardrails, consistent with the composite action's stated
  behavior throughout the same file.

## Recommended doc actions (create/update/consolidate/remove)

1. **Update** `.github/SECURITY.md:210,219` — Python CI matrix (DOC-04). High priority: this file
   is explicitly the document enterprise reviewers are pointed at for CI-gate claims.
2. **Update** `README.md:231` — "3.x" → "4.x" (DOC-03).
3. **Update** `docs/mcp.md:240` — "first eight" → "first seven" (DOC-01).
4. **Update** `README.md:22-27` — either broaden the exception enumeration to include
   `--neo4j-uri` and `--tokenizer tiktoken`, or soften the claim to point to SECURITY.md's fuller
   list explicitly for the exception enumeration, not just the tree-sitter-pack rationale
   (DOC-02).
5. **Clean up** stale "this project supports 3.10" comments in `repo2graph/__init__.py` while
   someone is already touching version/3.10 cleanup for DOC-04 — not a doc file but will confuse
   the next contributor who reads it as current.
6. **Consider** tightening or dating `tests/golden/cli_inventory.json`'s scope (completeness gap
   #1) so it isn't mistaken for an exhaustive cross-check; this is a test-fixture issue more than
   a documentation issue, flagged here because the audit brief pointed at it as a comparison
   target.
7. **No removal or consolidation needed.** `test_the_shipped_doc_set_is_the_documented_one`
   already keeps `docs/` capped at five reference pages, and nothing found in this audit
   duplicates content in a way that has drifted (README/docs/action.yml all restate overlapping
   material — e.g. the Action's inputs appear in both README's short example and docs/cli.md's
   full table — but every restatement checked is accurate and the two serve different levels of
   detail intentionally).

## Limitations

- Per the task's constraints, I did not fetch any external URLs (competitor project links in
  docs/comparison.md, badge endpoints, PyPI/npm/Marketplace links) — those are listed below as
  present but their liveness/accuracy was not independently verified over the network, only
  inspected for obvious staleness by reading.
- I did not run the pytest suite; all "matches code" conclusions above come from direct reading
  of `repo2graph/cli.py`, `cli_embed.py`, `cli_inspect.py`, `__init__.py`, and from
  `inspect.signature()` run via a read-only `python -c` invocation, plus reading
  `tests/test_doc_consistency.py` to understand what is already machine-enforced (and trusting
  that CI keeps it green, since the task forbids running tests).
- `docs/comparison.md`'s claims about third-party tools (Serena, Aider, CodeGraphContext,
  Sourcegraph, Cursor, Claude Code's search, Madge, dependency-cruiser, CodeSee, Sourcetrail) were
  read for internal consistency and plausibility but not independently fact-checked against those
  projects' current behavior, since that would require network research outside this audit's
  rules.
- `docs/architecture.md` (686 lines) was sampled via the consistency-test content (module table,
  generated-scorecard markers, git-metadata field names, determinism-test names) rather than read
  end-to-end line-by-line; no discrepancy surfaced in the portions inspected, but a full manual
  read of its remaining prose (resolution algorithm internals, SCIP integration details, reference/
  framework-edge precision limits) was not completed given the breadth of the rest of the
  documentation set in scope.
- The uncommitted changes in `repo2graph/frameworks.py`, `graph.py`, `store.py`, and
  `tests/test_store.py` were left untouched as instructed and were not used as a basis for any
  finding above — all comparisons were against the working tree as it stands, which may not
  reflect where those in-flight edits are heading.
