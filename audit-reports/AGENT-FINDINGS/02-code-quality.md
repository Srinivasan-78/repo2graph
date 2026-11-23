# Agent 2 — Code Quality & Maintainability Audit

Scope: `repo2graph` package (~32k LOC across `repo2graph/*.py` and `repo2graph/mcp/*.py`).
Security and correctness-bug analysis are explicitly out of scope (owned by other agents);
any suspected correctness bug noticed in passing is flagged briefly for Agent 3 and not
analysed further.

## Summary

Overall verdict: **this is an unusually disciplined codebase for its size** — lint and
strict-mypy are both fully clean, naming/comment conventions are consistent, magic numbers
are named constants, resource handling (files, sqlite connections, locks) is deliberately
guarded, and the long prose comments are (on every sample checked) accurate explanations of
non-obvious decisions rather than stale filler. The principal maintainability risk is **function
size and cyclomatic complexity concentrated in a handful of "god functions"** in the hottest
modules (`graph.py:build`, `query.py:pack_context`, `parse.py:parse_source`,
`graphdiff.py:find_affected_tests`), plus one module (`viz.py`) that is two-thirds an inline
HTML/JS string with no tooling coverage. These are real, measurable maintainability costs even
though the code inside them is individually careful.

### Metrics

- Package size: ~31,900 lines across 60 source files (`repo2graph/*.py` + `repo2graph/mcp/*.py`).
- Largest modules: `parse.py` 2300, `graph.py` 2172, `query.py` 2123, `cli.py` 2110,
  `export.py` 1698, `viz.py` 1328, `security.py` 1181, `mcp/server.py` 932, `answer.py` 880,
  `scip.py` 850, `graphdiff.py` 766, `integrity.py` 757, `discovery.py` 727.
- `ruff check .` (select `E,F,W,BLE,B,S,I,UP,RUF`, no `--fix`): **0 findings**.
- `python -m mypy repo2graph/` (strict, applies to all 59 own-package modules, no
  per-module overrides except `numpy`/`numpy.*` which only relaxes stub-following, not
  type checking of this package's code): **0 errors, 60 source files**.
- No `TODO`/`FIXME`/`XXX` markers anywhere in the package.
- `except Exception`/bare-except sites: 28, all narrowed with either a `# noqa: BLE001 -
  <rationale>` comment or an inline comment explaining the resilience boundary and the
  re-raise/fallback path taken. No swallow-to-silent-empty-result pattern found (see CQ-06
  for the one caveat).
- `type: ignore` comments: 8 in the whole package, each with the specific error code and a
  one-line reason.

## Findings

### CQ-01: `graph.build()` is a 792-line function with ~155 branch points

- **Severity**: High
- **Confidence**: High
- **Location**: `repo2graph/graph.py:1380`–`2172` (function `build`, runs to end of file)
- **Description**: `build()` is the single entry point that turns a parsed repo into a
  `Graph`. It is 792 lines long and contains roughly 155 `if`/`for`/`while`/`except`/`with`
  branch points (counted via AST walk), plus six nested closures defined inline
  (`scope_of` @1731, `named_in` @1757, `match_bases` @1763, `base_methods` @1785,
  `names_home` @1809, `tier3_imported` @1825) that implement successive call-resolution
  tiers (same-file, same-class-hierarchy, imported, same-module, …). Each closure captures
  a large amount of enclosing state (`by_name`, `by_name_path`, `members`, `imported_files`,
  `import_aliases`, `class_bases`, …), so none of them can be tested, reasoned about, or
  reused independently of the rest of `build()`.
- **Why it matters**: This is the core graph-construction logic for the whole tool. A
  function this size cannot be unit-tested at a granularity finer than "run the whole
  build," which is exactly the situation the existing comments describe fixing bugs around
  (e.g. the `#456 P2` quadratic-blowup comment at `graph.py:1744`). Every future change to
  name resolution has to be made inside this one function, with full knowledge of all of its
  state, raising the risk of an unintended interaction between tiers.
- **Recommendation**: Extract the six resolution-tier closures (and the sections that feed
  them: `by_name`/`by_name_path`/`by_name_dir`/`members` construction) into a small
  `_NameResolver` class or module-level functions taking their dependencies as explicit
  parameters instead of captured closure state. This does not need to change behavior — it
  is a pure decomposition — but it would let each tier be unit-tested and documented on its
  own.
- **Effort**: L

### CQ-02: `query.pack_context()` is 384 lines / ~89 branches; `retrieve()` is 116 lines

- **Severity**: Medium-High
- **Confidence**: High
- **Location**: `repo2graph/query.py:1537`–`1923` (`pack_context`), `1397`–`1517` (`retrieve`)
- **Description**: `pack_context` (the function that turns ranked chunks into the final
  cited context pack returned to an agent) is 384 lines with ~89 branch points, handling
  token-vs-character budgeting, map prepending, expansion, citation formatting, and
  truncation all in one body. `retrieve` (116 lines) is comparatively mild but still mixes
  BM25/vector fusion, reranking, lexical-weakness fallback, and test-seed demotion inline.
- **Why it matters**: `query.py` is on the critical path for every answer the tool
  produces; a bug here produces a wrong or truncated context pack silently (the kind of
  retrieval-correctness risk this audit was asked to flag). The function's size makes it
  hard to verify that every exit path still respects both the character and token budgets.
- **Recommendation**: Split `pack_context` along its already-visible phase boundaries
  (budget resolution → map prepend → per-chunk excerpting/citation → final fit) into
  private helpers with clear pre/postconditions, similar to the existing `_fit_lines`,
  `_compress`, `_excerpt_record`, `_cite_block` helpers already factored out below it —
  the pattern is already present, just not applied all the way through the main function.
- **Effort**: M

### CQ-03: Several other functions in hot-path modules exceed ~250 lines / ~45 branches

- **Severity**: Medium
- **Confidence**: High
- **Location** (lines = body length, branches = AST-counted `if/for/while/except/with` + bool-op joins):
  - `repo2graph/parse.py:1926` `parse_source` — 347 lines, ~104 branches
  - `repo2graph/graphdiff.py:343` `find_affected_tests` — 346 lines, ~83 branches
  - `repo2graph/chunks.py:201` `iter_chunks` — 293 lines, ~47 branches
  - `repo2graph/discovery.py:449` `explain_path` — 278 lines, ~28 branches
  - `repo2graph/parse.py:1654` `parse_import_details` — 256 lines, ~46 branches
  - `repo2graph/graph.py:1189` `add_pytest_fixture_edges` — 187 lines, ~51 branches
  - `repo2graph/graph.py:534` `_chunk_and_parse` — 178 lines, ~17 branches
  - `repo2graph/integrity.py:527` `verify_artifacts` — 173 lines, ~42 branches
  - `repo2graph/export.py:944` `write_manifest` — 176 lines
  - `repo2graph/mcp/server.py:766` `main` — 162 lines
- **Description**: These are long, multi-purpose functions in the modules this audit was
  asked to focus on (parse, graph, query, export, mcp/server, graphdiff, integrity,
  discovery, chunks). None individually is as extreme as CQ-01/CQ-02, but collectively they
  show the same pattern: a function handles discovery/validation/aggregation/formatting in
  one body instead of composed smaller units.
- **Why it matters**: Same class of risk as CQ-01/CQ-02 at smaller scale — harder to review
  a diff against, harder to unit test one concern in isolation, higher chance a change to
  one branch has an unintended effect on a sibling branch 200 lines away.
- **Recommendation**: No urgent action needed; treat as backlog cleanup opportunistically
  when next touching each function. `find_affected_tests` and `parse_source` would benefit
  most given their branch density.
- **Effort**: M (per function, if tackled)

### CQ-04: `viz.py` is two-thirds an inline HTML/CSS/JS string with no tooling coverage

- **Severity**: Medium
- **Confidence**: High
- **Location**: `repo2graph/viz.py:459`–`1328` (`TEMPLATE = r"""..."""`, to end of file; the
  module is 1328 lines total, so the template is ~870 of them, i.e. ~65%)
- **Description**: The interactive HTML visualization is authored as one large raw Python
  string literal containing a full HTML document with embedded `<style>` and `<script>`
  (JS) blocks. The `<script>` content is not run through any JS linter/formatter and is
  invisible to `ruff`/`mypy` entirely — it is just a string as far as Python tooling is
  concerned.
- **Why it matters**: Most of the module's substance (DOM logic, rendering, interaction
  handling for the viz output) has zero static analysis, no syntax highlighting in most
  editors' Python-mode, and cannot be tested except by rendering the page and exercising it
  manually/E2E. A typo in the JS fails silently in the browser console, not at build or
  lint time.
- **Recommendation**: Not urgent to change given it's a self-contained, rarely-touched
  template, but if the JS grows further, consider extracting it to a sibling `.js`/`.html`
  asset file embedded via `importlib.resources` at build time, which would let a JS
  linter (or at least basic syntax checking) run over it in CI.
- **Effort**: M

### CQ-05: `cli.py:main()` is 959 lines, almost entirely argparse wiring

- **Severity**: Low
- **Confidence**: High
- **Location**: `repo2graph/cli.py:1119`–`2078` (`main`)
- **Description**: `main()` is by line count the largest function in the package, but
  AST analysis shows ~182 `add_argument`/`add_parser` calls against only ~10 real branch
  points — it is overwhelmingly declarative parser construction, not complex control flow.
  Dispatch (`args.func(args)`) and top-level exception handling (`KeyboardInterrupt`,
  `BrokenPipeError`, `_GraphLimitExceeded`) sit at the very end.
- **Why it matters**: Despite low complexity, the sheer length makes `main()` slow to
  navigate (finding "where is the `--jobs` flag defined" means scrolling ~900 lines), and
  it is the one function in the file that cannot be tested without invoking the full CLI
  parser, since the parser-building and the dispatch logic are not separated.
  This is primarily a size/navigability issue, not a complexity one — tagging it `CQ`
  (not a style-only note) because it affects how easy the CLI surface is to extend safely.
- **Recommendation**: Factor parser construction (the `add_argument`/`add_parser` calls)
  into a `_build_parser() -> argparse.ArgumentParser` helper, leaving `main()` itself as
  just: build parser → parse args → dispatch → handle top-level exceptions. Purely
  mechanical, low risk.
- **Effort**: S

### CQ-06: Lint config has no complexity/length gate, so CQ-01..CQ-03 can grow unchecked

- **Severity**: Low
- **Confidence**: High
- **Location**: `pyproject.toml:184`–`205` (`[tool.ruff.lint]` `select = ["E", "F", "W",
  "BLE", "B", "S", "I", "UP", "RUF"]`)
- **Description**: The ruff rule selection is deliberate and well-justified by the adjacent
  comment (bug classes, security, import order, syntax upgrades), but it does not include
  McCabe complexity (`C90`) or pylint's `too-many-*`/`PLR09xx` rules. Nothing in CI would
  flag a function growing from 792 to 1,200 lines.
- **Why it matters**: Given CQ-01 through CQ-03 already exist, there is no automated
  backstop preventing further growth of the largest functions, which is how `build()`
  likely got to 792 lines in the first place (incremental additions, each individually
  reasonable).
- **Recommendation**: Consider adding `C90` with a generous `max-complexity` threshold
  (e.g. 25–30, deliberately above today's average so it only fires on genuine outliers) as
  a tripwire for future growth, without requiring an immediate refactor of the existing
  offenders. This is a process recommendation, not a correctness issue — purely optional.
- **Effort**: S

## Style observations (non-defects)

- The codebase makes heavy, consistent use of long prose docstrings/comments explaining
  *why* a decision was made (architecture rationale, historical bug numbers like `#456`,
  `#398`, `#341`). Every sample checked during this audit (`chunks.py:iter_chunks`'s claim
  about source-cache eviction, `frameworks.py`'s module docstring, `cache.py`'s TTL
  rationale, `fetch.py`'s memoization caveat) was verified against the actual code and
  found accurate, not stale. This is unusual for a codebase this size and is a genuine
  strength, not a smell — flagged here only because the audit brief asked to judge rather
  than assume.
- Private module-level `global` mutable state (`fetch.py:_git_version_cache`,
  `parse.py:_cpp_available_cache`, `parsepool.py:_slot/_watch/_worker_job`) is present but
  in every case is a deliberate, documented memoization or per-worker-process state pattern
  required by the multiprocessing design, with explicit comments about why a failed probe
  is *not* cached. Not flagged as a defect.
- Magic numbers in `query.py` (`BM25_B`, `BM25_AVG_LEN`, `CONTAINER_SEED_PENALTY`,
  `STEM_VARIANT_WEIGHT`, `TEST_SEED_PENALTY`) are already named module-level constants, not
  inline literals — good practice already followed.
- `main` is defined in both `cli.py` and `mcp/server.py`, `build` in both `graph.py` and
  `store.py`, `retrieve`/`pack_context` in both `federated.py` and `query.py` — checked all
  of these; none are duplicated logic. `federated.py`'s versions are thin fan-out wrappers
  that call into `Index.retrieve`/`Index.pack_context` per-repo and merge results; the
  `main` pair are each a module's own CLI/server entry point. No action needed.
- No `eval`/`exec`/`pickle` misuse found; the one `ast.literal_eval` use (`embed.py:126`)
  is on a trusted, self-written header format, not untrusted input.

## Tooling output

```
$ ruff check . --no-fix
All checks passed!
```

```
$ python -m mypy repo2graph/
Success: no issues found in 60 source files
```

## Strengths

- Zero ruff findings and zero mypy findings under `strict = true` with no per-module
  escape hatches for the package's own code (only `numpy`/`numpy.*` stub-following is
  relaxed) — the pyproject.toml comment at `pyproject.toml:273`–`284` documents that this is
  a deliberate, hard-won state (the last three modules, `cli.py`/`graph.py`/`parse.py`, were
  only recently brought under strict mode, and doing so surfaced two real bugs elsewhere).
- Resource handling (file opens, sqlite connections, lock files, subprocess pipes) is
  consistently guarded with context managers, explicit `__enter__`/`__exit__`, or
  errno-aware fallback paths (`graph.py:_safe_open`, `lock.py`'s `acquire()`,
  `store.py:SqliteGraphStore`).
- Exception handling, while broad `except Exception` appears 28 times, is in every
  instance deliberately scoped to a described "resilience boundary" with a comment
  explaining what failure mode is being contained and why it must not crash the caller;
  none degrade to a silently-wrong result without logging/propagating a reason.
- No dead code, no unused imports/variables, no `TODO`/`FIXME` backlog markers anywhere —
  the codebase appears to be kept clean continuously rather than audited occasionally.

## Limitations

- This audit sampled function-level complexity via line counts and an AST branch-counter
  (not a calibrated cyclomatic-complexity tool), so branch counts are approximate relative
  orderings, not exact McCabe numbers.
- Given the size of the package, "sample the rest" modules (tasks.py, cache.py, events.py,
  lock.py, bugreport.py, doctor.py, status.py, embed.py, imports.py, refcapture.py,
  cochange.py, exclusions.py, schema.py, explain.py, edgemeta.py, changelog.py, config.py,
  testpaths.py, tokenizers.py, rerank.py, watch.py, cli_embed.py, cli_inspect.py,
  neo4j.py, federated.py, audit.py, limits.py, and the remaining mcp/* modules) received a
  lighter pass (grep-based pattern search, not line-by-line reading) and could hide
  function-size or duplication issues not surfaced by the broad searches run here.
- Per the task's constraints, the four files with uncommitted changes
  (`frameworks.py`, `graph.py`, `store.py`, `tests/test_store.py`) were read in their
  current (modified) on-disk state but not diffed against HEAD for quality commentary,
  since those changes are explicitly another agent's/the user's in-flight work and were
  left untouched.
