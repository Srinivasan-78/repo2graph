# Findings Register

Consolidated, deduplicated and severity-adjudicated by the lead orchestrator.
**50 findings** across 11 specialist assessments plus orchestrator verification.

**Status** values: `Confirmed` = reproduced by executing code. `Verified` =
checked against source/API by the orchestrator. `Reported` = specialist finding
accepted on evidence but not independently re-executed. `Rejected` = withdrawn.

## Severity totals

| Severity | Count |
|---|---|
| **CRITICAL** | 1 |
| **HIGH** | 7 |
| MEDIUM | 17 |
| LOW | 22 |
| INFO | 3 |
| **Total** | **50** |
| *Rejected* | *1* |
| *Deduplicated* | *3* |

## CRITICAL

| ID | Category | Finding | Confidence | Location | Status |
|---|---|---|---|---|---|
| BUG-01 | Correctness / concurrency | Unsynchronised shared SQLite connection returns **silently wrong data** under concurrent queries; 15/16 threads failed, 0 with lock restored | Confirmed ×3 | `repo2graph/store.py:146-287` | Confirmed |

## HIGH

| ID | Category | Finding | Confidence | Location | Status |
|---|---|---|---|---|---|
| BUG-02 | Correctness | Nested classes sharing an inner name collapse to one key; fixture scope bleeds between unrelated test classes | Confirmed | `repo2graph/frameworks.py:454-466` | Confirmed |
| BUG-03 | Correctness | A shadowing fixture's edge to the fixture it overrides vanishes silently | Confirmed | `repo2graph/graph.py:1333-1338` | Confirmed |
| BUG-04 | Test integrity | A failing test was silenced by moving the lock into the test; original body detected the defect in 32/40 trials | Confirmed | `tests/test_store.py:93-115` | Confirmed |
| REL-01 | Release engineering | Default branch `main` is 240 commits and 3 major versions behind the published release | Confirmed | branch/tag topology | Verified |
| CI-01 | CI gating | Required checks omit all 3 Python-3.14 cells and the container job; admin bypass is `always` | Confirmed | ruleset 22778968 vs `ci.yml:144` | Verified |
| CQ-01 | Maintainability | `graph.build()` is 792 lines / ~155 branches / 6 state-capturing closures; no resolution tier is unit-testable | Confirmed | `repo2graph/graph.py:1380-2172` | Verified |
| TEST-02 | Test coverage | No backward-compatibility test for the on-disk `.r2g` format; a schema change breaks every user's cache with no CI signal | High | suite-wide | Reported |

## MEDIUM

| ID | Category | Finding | Confidence | Location | Status |
|---|---|---|---|---|---|
| CQ-02 | Maintainability | `pack_context()` 384 lines / ~89 branches on the budget-and-citation critical path | Confirmed | `repo2graph/query.py:1537-1923` | Verified |
| CQ-03 | Maintainability | 10 further functions of 160–350 lines, 17–104 branches, across 8 hot modules | High | `parse.py`, `graphdiff.py`, `chunks.py`, `discovery.py`, `graph.py`, `integrity.py`, `export.py`, `mcp/server.py` | Reported |
| CQ-04 | Maintainability | 65% of `viz.py` is an inline HTML/CSS/JS string, invisible to ruff and mypy | Confirmed | `repo2graph/viz.py:459-1328` | Reported |
| ARCH-01 | Architecture | Core module imports from the MCP interface layer for the project's central operation | Confirmed | `repo2graph/tasks.py:246` → `mcp/indexes.py:59` | Verified |
| ARCH-03 | Architecture | `cli.py:main()` is a ~959-line argparse construction function | Confirmed | `repo2graph/cli.py:1119-2078` | Verified |
| ARCH-05 | Extensibility | Language dispatch scattered across 6+ modules with no adapter interface | High | `parse.py`, `graph.py`, `imports.py`, `refcapture.py`, `frameworks.py` | Reported |
| DOC-01 | Documentation | `SECURITY.md` states CI tests Python 3.10–3.13; actual matrix is 3.11–3.14 | Confirmed | `.github/SECURITY.md:210,219` | Verified |
| DOC-02 | Documentation | README's "no network calls" exception list omits `--neo4j-uri` and `rag --tokenizer tiktoken` | Confirmed | `README.md:22-27` | Reported |
| DEP-01 | Supply chain | `tree-sitter-language-pack<1.0` cap (correct decision) pins to an apparently frozen branch — unpatchable-in-range security debt | High | `pyproject.toml:33-44` | Reported |
| DEP-02 | Supply chain | Dependabot covers only `pip` + `github-actions`; npm (2 manifests) and docker unwatched | Confirmed | `.github/dependabot.yml` | Reported |
| COMPAT-01 | Correctness / UX | Tuple-length comparison makes `doctor` falsely warn on nearly every Python 3.13 install; its support matrix is also stale (warns off CI-tested 3.14) | Confirmed | `repo2graph/doctor.py:89-96` | Verified |
| CI-02 | Release engineering | npm publish silently no-ops without `NPM_TOKEN`, and `release` does not depend on the npm job | Confirmed | `publish.yml:597-638`, `:486` | Verified |
| CI-03 | Release engineering | `provenance.yml` generates no build attestation despite its name | Medium | `.github/workflows/provenance.yml` | Reported |
| PERF-01 | Performance | Each file read ~3× per build — 15,043 `open()` calls for 3,000 files | Confirmed-measured | `discovery.py:161`, `graph.py:715`, `chunks.py:226` | Reported |
| TEST-03 | Test reliability | Fixed `asyncio.sleep(0.4)` as server-readiness wait — flakiness risk | High | `tests/test_mcp_sse.py:131,170,221` | Reported |
| TEST-04 | Test coverage | `fail_under = 80` branch gate enforced on 1 of 12 CI cells; no Windows cell measured | Confirmed | `pyproject.toml` coverage config, `ci.yml` | Reported |
| TEST-05 | Test quality | Concurrency tests assert "didn't crash", never "results were correct" — blind to BUG-01's real failure mode | High | suite-wide | Reported |

## LOW

| ID | Category | Finding | Confidence | Location | Status |
|---|---|---|---|---|---|
| BUG-05 | API contract | `GraphStore` Protocol lost `__enter__`/`__exit__`; dormant type-contract gap | Confirmed | `repo2graph/store.py:54-69` | Confirmed |
| SEC-01 | Security | Unredacted secrets inherit default file permissions when `--secret-policy off/warn-only` | High | `repo2graph/export.py` | Reported |
| SEC-02 | Security | SSE bearer-token check has no rate limiting if bound beyond loopback | High | `repo2graph/mcp/server.py:491-595` | Reported |
| SEC-04 | Security | Cypher escaping relies on JSON escaping being Cypher-compatible | Medium | `repo2graph/export.py` (`_cy()`) | Reported |
| SEC-05 | Security | Self-documented DNS-rebinding TOCTOU in the opt-in `rag --answer` SSRF guard | High | `repo2graph/answer.py:392-420` | Reported |
| CQ-05 | Maintainability | `cli.py:main()` size is a navigability issue (~182 `add_argument`, ~10 real branches) | Confirmed | `repo2graph/cli.py:1119-2078` | Verified |
| CQ-06 | Process | No McCabe/complexity lint gate, so CQ-01…CQ-03 can grow unchecked | Confirmed | `pyproject.toml:184-205` | Reported |
| ARCH-02 | Architecture | `export.py` calls back into `query.py` for BM25 writing (self-acknowledged) | Confirmed | `export.py` → `query.py` | Reported |
| ARCH-04 | Architecture | `cli_inspect.py`/`cli_embed.py` import private helpers from `cli.py` | Confirmed | `repo2graph/cli_inspect.py`, `cli_embed.py` | Reported |
| DOC-03 | Documentation | "The 3.x CLI… follows semver" while the project is at 4.1.0 with 5.0.0 released | Confirmed | `README.md:231` | Reported |
| DOC-04 | Documentation | "first eight" tools should read "first seven" | Confirmed | `docs/mcp.md:240` | Reported |
| CFG-01 | Documentation | `REPO2GRAPH_CACHE_DIR`, `REPO2GRAPH_ALLOW_INSECURE_OLLAMA`, `GITHUB_SERVER_URL` read by code but undocumented | Confirmed | README/docs | Reported |
| DEP-03 | Packaging | npm launcher version is cosmetic; `uvx --from` has no version pin, always runs PyPI-latest | Confirmed | `npm/package.json`, `npm/bin/repo2graph-mcp.js` | Reported |
| DEP-04 | Supply chain | Docker base image tag-pinned, not digest-pinned | Confirmed | `Dockerfile` | Reported |
| COMPAT-02 | Dead code | Dead `tomli` fallback and stale "supports 3.10" docstrings survive the 3.10 drop | Confirmed | `repo2graph/__init__.py:20-44`, `scripts/bump_version.py:24-29`, `scripts/check_version.py:25-30` | Reported |
| PERF-02 | Performance | Eager `importlib.metadata.version()` costs ~40ms of a ~120ms cold-start import tax | Confirmed-measured | `repo2graph/__init__.py:73-99` | Reported |
| PERF-05 | Performance | Row-by-row NPY struct pack/unpack rather than bulk ops | Low (inspection) | `repo2graph/embed.py:84-150` | Reported |
| PERF-06 | Performance | `Index.nodes/.edges/.adj` silently fully materialise a sqlite-backed graph | Medium (inspection) | `repo2graph/query.py:686-701` | Reported |
| PERF-07 | Performance | BM25 rebuild is pure-Python with per-token dict overhead on cache miss | Low (inspection) | `repo2graph/query.py:703-722` | Reported |
| CI-04 | CI hygiene | Duplicate `uv lock --check` across `lockfile.yml` and `ci.yml` on overlapping triggers | High | `.github/workflows/lockfile.yml` | Reported |
| CI-05 | CI gating | `dependency-audit.yml` / `dependency-review.yml` are not required checks; a CVE-introducing PR can merge green | Medium | `.github/workflows/` | Reported |
| TEST-06 | Test quality | `assert x is not None` as sole/terminal assertion at 43 sites | Medium | `tests/*.py` | Reported |

## INFO

| ID | Category | Finding | Confidence | Location | Status |
|---|---|---|---|---|---|
| DOC-05 | Documentation | `tests/golden/cli_inventory.json` covers 9 of ~17 subcommands while appearing exhaustive | Confirmed | `tests/golden/cli_inventory.json` | Reported |
| SEC-03 | Security | Build-task ids not session-scoped; moot under stdio, theoretical under multi-tenant SSE | Medium | `repo2graph/mcp/tools.py` | Reported |
| PERF-03 | Environment | Windows build ~15× the committed Linux baseline, root-caused to `open()` latency — **not** a code regression | Confirmed-measured | environment | Verified |

## Deduplicated

Three specialist findings were merged rather than double-counted:

| Duplicate | Merged into | Reason |
|---|---|---|
| TEST-01 (testing) | **BUG-04** | Same defect — the neutered concurrency test — reported by two specialists |
| TEST-07 (testing) | **DOC-05** | Same artefact, `cli_inventory.json` incompleteness |
| ARCH-06 (architecture) | **CQ-01 / CQ-03** | Module size is the same observation as the function-size cluster |

## Rejected

| Claimed | Rated | Verdict |
|---|---|---|
| "No Docker registry push and no VS Code Marketplace publish exist in CI, despite the project reportedly shipping both" | HIGH | **REJECTED — premise false.** The absence is real, but the project does not claim to publish either: `README.md:234` offers a Dockerfile, not an image, and `editors/vscode/README.md:16` states "it is not on the Marketplace." The agent inherited the five-channel framing from the orchestrator's own briefing and did not verify it. |

## Findings by assessment area

| Area | CRITICAL | HIGH | MEDIUM | LOW | INFO |
|---|---|---|---|---|---|
| Correctness / bugs | 1 | 3 | 1 | 1 | — |
| Architecture | — | — | 3 | 2 | — |
| Code quality | — | 1 | 3 | 2 | — |
| Security | — | — | — | 4 | 1 |
| Testing | — | 1 | 3 | 1 | — |
| CI/CD & release | — | 2 | 2 | 2 | — |
| Documentation | — | — | 2 | 3 | 1 |
| Dependencies & compatibility | — | — | 3 | 3 | — |
| Performance | — | — | 1 | 4 | 1 |

## Note on concentration

Five of the eight CRITICAL/HIGH findings (BUG-01 through BUG-05, of which four
are CRITICAL/HIGH) originate in a **single uncommitted working-tree diff** and
are fixed by restoring the hunks from commit `516fe12d`. The underlying
committed codebase is considerably healthier than the raw severity counts
suggest — a point made precisely in `EXECUTIVE-SUMMARY.md` and
`PRODUCTION-READINESS.md`.
