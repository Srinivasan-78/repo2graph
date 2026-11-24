# Agent 9 — CI/CD, GitHub Actions & Release Engineering Audit

Scope: `.github/workflows/*`, `.github/dependabot.yml`, `.github/zizmor.yml`,
`.github/scripts/{summary.py,prod-igy.js}`, `.pre-commit-config.yaml`,
`Makefile`, `Dockerfile`, `.dockerignore`, `action.yml`, `scripts/{bump_version,
check_version, check_workflow_summaries, commit_release_via_api,
version_surfaces}.py` and their tests. Read-only: no workflow was triggered, no
repo state was changed, no file outside this report was written.

## Summary

The pipeline is unusually well-documented (every workflow carries inline
history of the incident that shaped it) and the release automation
(`publish.yml`) has clearly been hardened twice already — once for the
unsigned-commit/admin-bypass incident, once for the "MERGED is not proof of
green" re-check. Run history over the last 100 runs is mostly green; the only
real failures are legitimate (`Dependency Review` catching real vulnerable
packages, two solo Dependabot update PRs that failed on `certifi`/`filelock`).

The two systemic problems are:

1. **The branch-protection ruleset and the workflow YAML have already drifted
   once, silently.** The required-status-checks list on `main`
   (`default-branch-protection`, ruleset 22778968) enumerates Python
   3.11/3.12/3.13 for each OS but **not 3.14**, and does not require the
   `container` (Docker) job at all, even though both exist in `ci.yml` and gate
   nothing is said about a reason to exclude them. A red 3.14 cell or a broken
   Dockerfile can merge to `main` today.
2. **The project ships (per its own README/CHANGELOG) to PyPI, npm, a GitHub
   Action, a VS Code extension, and "Docker" — but `publish.yml` only publishes
   to PyPI, the MCP Registry and npm.** There is no Docker image push to any
   registry anywhere in `.github/workflows/`, and no VS Code Marketplace /
   open-vsx publish step anywhere in the repo. `ci.yml`'s `container` job only
   builds and smoke-tests the image locally to the runner; it is never pushed.
   If a published Docker image or VS Code extension exists, it is not produced
   by anything in this repository's CI.

Beyond that, quality gates (ruff, ruff format, mypy, pytest, coverage) are
genuinely enforced on every PR with no `continue-on-error` disguising a
failure, the Python-3.10 drop was applied consistently (no workflow still
installs or tests 3.10), and the release pipeline's idempotency (tag
re-use checks, `skip-existing`, npm/mcp-publisher "already published" guards)
is well thought through. The two live gaps that most need attention are the
required-checks/matrix drift above and the fact that `enforce_admins` is
still off and ruleset bypass is still granted to `RepositoryRole 5` — i.e. the
exact mechanism that let 3.0.0 ship on a red test suite is still live,
mitigated now only by `publish.yml`'s own in-run re-check of required checks
(added after that incident) rather than by GitHub's own protection.

## Pipeline map

| Workflow | Trigger(s) | Purpose | Gates what | Required status check? |
|---|---|---|---|---|
| `ci.yml` | `push:main`, `pull_request`, `workflow_dispatch`, weekly cron (drift only) | lint/format/mypy/version/summary checks, test matrix (3 OS × 4 Python), packaging/MCP smoke, Docker build smoke, Windows CP1252 smoke, composite-action smoke, weekly unlocked drift canary | PR merge | Yes, for `code-quality`→"Code Quality & Static Analysis", `tests`→9 of 12 matrix cells (3.14 excluded), `packaging`, `windows-cp1252-pipe`, `action`. **Not** `container` or `drift`. |
| `provenance.yml` | `push:main`, `pull_request` | REUSE/SPDX license lint, zizmor workflow-security audit (SARIF to Code Scanning) | Nothing (not in required list) | No |
| `codeql.yml` | `push:main`, `pull_request`, `workflow_dispatch`, weekly cron | CodeQL `security-extended` over python/js-ts/actions | Nothing | No |
| `dependency-review.yml` | `pull_request` | Blocks newly-introduced vulnerable/incompatible deps in a diff | Nothing (not required) — real failures observed, see below | No |
| `dependency-audit.yml` | `pull_request:main`, weekly cron, `workflow_dispatch` | `pip-audit --strict` over the whole installed tree incl. extras; generates CycloneDX SBOM | Nothing, deliberately (comment: "turns the run red for a reviewer, not the merge button") | No |
| `dependabot-triage.yml` | `pull_request: [opened, reopened]` from dependabot only | Labels + manual-merge comment (auto-merge removed) | N/A | No |
| `lockfile.yml` | `pull_request` (paths: pyproject.toml/uv.lock), `workflow_dispatch` | `uv lock --check`, uploads/optionally commits a refreshed lock | Overlaps with `ci.yml`'s own `uv export --locked` | No, but overlaps `packaging`'s `uv lock --check` |
| `benchmark.yml` | `push:main,develop`, `pull_request`, `workflow_dispatch` | Runs `benchmark_runner.py --ci`, regression-gates task accuracy ≥80% | PR merge | Yes (both OS cells) |
| `perf.yml` | weekly cron, `workflow_dispatch` | `perf_bench.py --check` vs CPU-calibrated baseline | Nothing | No |
| `examples.yml` | `workflow_dispatch` only | Regenerates `examples/`, uploads artifact for human PR | N/A | No |
| `index-repo.yml` | `workflow_dispatch` | Index an arbitrary external repo via the composite action; optional release attach | N/A (admin tool) | No |
| `self-index.yml` | `push:main`, weekly cron, `workflow_dispatch` | Re-index this repo, push to `graph` branch | N/A | No |
| `prod-igy.yml` | `pull_request_target: [opened,synchronize,reopened]`, `issue_comment`, `workflow_dispatch` | AI/deterministic PR triage, labels, rebase/conflict nudges | N/A | No |
| `vscode.yml` | `push:main`/`pull_request` (paths: editors/vscode, repo2graph, self), `workflow_dispatch` | Compiles & tests the VS Code extension against the real MCP server (2 OS) | Nothing in the required list (extension has no CI gate on `main`) | No |
| `publish.yml` | `workflow_dispatch` only | **The entire release**: version bump → signed commit → release PR → re-verify checks → tag → PyPI → MCP Registry → GitHub Release → floating major tag → npm | The release itself | N/A (not a PR check) |

### Lifecycle diagram

```
 PR opened  ──► ci.yml (code-quality, tests×12, packaging, container,
                        windows-cp1252-pipe, action)
            ──► benchmark.yml (2 OS)
            ──► provenance.yml (reuse, zizmor)      \
            ──► codeql.yml                            > NOT required to merge
            ──► dependency-review.yml                /  (informational only)
            ──► dependency-audit.yml (if touches deps)
            ──► prod-igy.yml (triage/labels, pull_request_target)
            ──► lockfile.yml (if pyproject/uv.lock touched)
            ──► vscode.yml (if editors/vscode or repo2graph touched)
                 │
                 ▼  (required checks green per ruleset 22778968;
                      3.14 cells and `container` are NOT gating)
            merge to main (squash/merge/rebase; RepositoryRole-5 bypass always available,
                            enforce_admins=false)
                 │
                 ▼
            ci.yml / self-index.yml / vscode.yml / codeql.yml etc. re-run on main
                 │
     (human) workflow_dispatch → publish.yml
                 │
   prepare-release: bump_version.py auto → check_version.py + full pytest on
   bumped tree → commit via GraphQL (signed) → release PR → poll up to 60 min
   for CLEAN/merge → merge → RE-CHECK required checks before tagging → tag
                 │
        ┌────────┼─────────────┐
        ▼        ▼             ▼
      pypi     npm (after    mcp-registry (after pypi)
   (OIDC,      pypi)              │
   attest)        \               ▼
        \          \          release (GitHub Release + floating vN tag,
         \          \          needs pypi+mcp-registry)
          \          └──────────────┘
           ▼
      (no docker publish step anywhere)
      (no VS Code Marketplace / open-vsx publish step anywhere)
```

## Quality gate coverage

- **ruff check**: enforced, `ci.yml` `code-quality` job, `--output-format=github`
  (inline PR annotations). No `continue-on-error`.
- **ruff format --check**: enforced in the same job, separate step id (`format`).
- **mypy**: enforced, and run three times (`--platform linux/darwin/win32`) on
  one Linux runner specifically so Windows-only stdlib stubs (e.g.
  `resource.getrusage`) are checked without needing a second runner —
  documented against issue #484. Pre-commit's `mypy-strict` hook scopes to
  `pyproject.toml`'s `[[tool.mypy.overrides]]`, so passing pre-commit locally
  is a reasonable predictor of the CI mypy step (same scope, same strictness).
- **pytest full suite**: enforced on 12 matrix cells (`ubuntu/macos/windows` ×
  `3.11/3.12/3.13/3.14`), `fail-fast: false`, `timeout-minutes: 35`. Windows
  runs without `--cov` and with `PYTEST_XDIST_AUTO_NUM_WORKERS=2` — a
  deliberate, well-documented resource-exhaustion mitigation (`WinError 1455`,
  commit-limit overrun from `cc1` during `test_cpp_parse.py`), not a coverage
  gap in enforcement, since coverage's `fail_under` is enforced on the 8
  Linux/macOS cells.
- **coverage**: `[tool.coverage.run]` sets `fail_under = 80` (not independently
  re-verified in this read, taken from workflow comments — confirm in
  `pyproject.toml` if not already covered by Agent 2). Reported in the step
  summary only from the Ubuntu/3.13 cell.
- **Python version matrix vs. classifiers**: `pyproject.toml` declares
  `requires-python = ">=3.11"` and classifiers for 3.11–3.14. `ci.yml`'s `tests`
  matrix covers exactly 3.11/3.12/3.13/3.14 — complete. **However** the
  required-status-checks list on the `main` ruleset only names the
  3.11/3.12/3.13 cells (see CI-01) — a real coverage/enforcement mismatch
  between the YAML and what GitHub will actually block a merge on.
- **OS matrix**: `ubuntu-latest`, `macos-latest`, `windows-latest`, all three
  required. Good.
- **Python 3.10 drop**: verified clean. Grepped the drop commit's surface —
  `ci.yml`'s matrix is `["3.11","3.12","3.13","3.14"]` with an explicit comment
  ("3.10 was dropped once it reached end of life... that was a
  `requires-python` bump, a breaking change, not a matrix edit"); `drift`'s
  matrix is `["3.11","3.14"]`; no other workflow (`benchmark.yml`: "3.13" only,
  `perf.yml`: "3.13", `examples.yml`/`self-index.yml`/`index-repo.yml`: "3.12",
  `lockfile.yml`: "3.12", `vscode.yml`: "3.12", `publish.yml`: "3.12",
  `dependency-audit.yml`: "3.12") references 3.10 anywhere. No workflow
  installs or tests 3.10.

## Release & publish pipeline analysis

`publish.yml` is `workflow_dispatch`-only by design, explicitly to avoid a
self-retriggering loop from its own `push:tags:` / `release:published` side
effects (documented in the file header, confirmed against the mcp-publisher
"cannot publish duplicate version" hard-failure case). The `branch` input
(default `main`) exists specifically because an earlier version of this
workflow bumped whatever branch was dispatched from but opened the PR against
a hardcoded `main`, letting the tag and the published artifact disagree about
content — now fixed by reading `inputs.branch` everywhere.

**Version derivation**: `bump_version.py auto` scans commits since the last
`vX.Y.Z` tag plus the `[Unreleased]` CHANGELOG section for `!:`/`BREAKING
CHANGE:`/`### Breaking`/`### Removed` (major), `feat:` (ignoring
`ci/test/docs/chore` scopes) or `### Added` (minor), else patch. **Malformed
commit header**: a header that doesn't match any of these patterns falls
through to the patch-bump default — safe but silently wrong if a contributor
actually meant a feature and used a non-conventional message; there's no hard
failure, only a possibly-too-small bump. **Missing CHANGELOG entry**: if there
are zero commits since the tag *and* an empty `[Unreleased]` section,
`detect_bump_type()` raises `RuntimeError` and the run fails before anything
is touched — correct fail-closed behaviour. If commits exist but
`[Unreleased]` is empty, `bump_changelog()` still synthesizes a minimal
`### Changed` section so the `release` job's CHANGELOG-section extractor
doesn't later fail on a tagged, merged commit — a deliberate design choice
to avoid repeating the exact 3.0.0 failure mode (see below) for a different
missing-input case.

**The exact 3.0.0 incident** is still documented in the workflow itself: the
bump used to be gated only *after* commit/tag; `tests/test_export_hardening.py`
asserted a static fallback version string that only the bump itself could
falsify, so the first run able to see the failure was the already-tagged
`pypi` job (`1 failed, 2073 passed`). `prepare-release` now runs
`check_version.py` and the full `pytest -q -n auto` against the bumped tree
*before* anything is committed, pushed, PR'd or tagged — a real fix, not just
a comment.

**Idempotency / resumability**:
- Tag creation is guarded (`git rev-parse -q --verify refs/tags/$tag`) — a
  re-run against an already-tagged commit won't double-tag.
- PR creation checks for an existing PR by head/base before creating.
- PyPI upload uses `skip-existing: true` specifically because the `release`
  job's `gh release create` re-fires this same workflow's `release:
  types:[published]`... wait, no — the workflow is explicitly
  `workflow_dispatch`-only and the header comment says this exact scenario
  ("every successful release re-triggered itself") was the *reason* the
  `push:tags:`/`release:published` triggers were removed. `skip-existing`
  now exists as defense-in-depth against a manual re-dispatch of an
  already-published tag, not because of a live self-trigger.
- `mcp-registry` hard-fails on a duplicate version (no skip-existing
  equivalent) — the one non-idempotent leg. A re-dispatch after a partial
  failure where PyPI succeeded but MCP registry did not would need the
  publish step itself to already be idempotent on GitHub's side; worth
  confirming `mcp-publisher publish` behaves the same way (not inspected here,
  recommend Agent verifying mcp-publisher's CLI docs).
- `npm` publish checks `npm view ...@$VERSION` first and exits 0 cleanly if
  already published; also exits 0 (with a warning, not a failure) if
  `NPM_TOKEN` is unset — meaning an npm publish can *silently no-op* and the
  overall workflow still reports green (see CI-05).
- The floating major tag (`vN`) is force-pushed every run (`git tag -f`,
  `push -f`), so that leg is naturally idempotent/resumable.

**Can PyPI/npm/MCP-Registry/GitHub-Release drift out of sync?** Yes, in two
concrete ways:
1. `npm`'s silent no-op on missing `NPM_TOKEN` (and `release`'s job dependency
   graph: `release` depends on `[prepare-release, pypi, mcp-registry]` but
   **not** on `npm`) means a release can complete successfully — tag pushed,
   GitHub Release created, PyPI and MCP Registry published — while npm quietly
   never got the new version, and nothing in the run fails to signal this.
2. There is no workflow anywhere that publishes a Docker image or a VS Code
   Marketplace release, so whatever process (if any) keeps those in sync with
   the PyPI/npm version is entirely outside this repository's CI and
   unverifiable from here.

**`commit_release_via_api.py`**: exists because `git commit` on an Actions
runner is never marked "Verified" even with an authenticated bot/App token,
and `main`'s current *classic* branch protection and the active ruleset both
require... (checked: the ruleset's `pull_request` rule does not list signed
commits as a requirement at the API level I could read, but the script's own
docstring states the release branch requires signed commits and that an
unsigned commit blocks every auto-merge attempt). It switched from the Git
Data API (blobs+trees+commits, never signed) to GraphQL
`createCommitOnBranch` (always signed) after discovering every release commit
produced before 2026-09-21 was unsigned and only ever merged via admin bypass
— this is the same incident the user's memory notes reference
(admin-bypass-merged-red-release-pr). The staging-branch dance
(`{branch}-signing-staging`) exists solely so an *existing* open PR's head
branch is never reset to empty mid-flight (which would auto-close the PR) —
a subtle, correct piece of engineering.

## Artifact integrity & provenance

- **`provenance.yml`** is misleadingly named for what it does: it runs REUSE
  license/SPDX linting and `zizmor` (workflow security static analysis,
  SARIF → Code Scanning). It does **not** produce SLSA provenance/attestation
  for any published artifact. The only real attestation in the pipeline is
  `pypa/gh-action-pypi-publish ... attestations: true` (PyPI Trusted
  Publishing OIDC attestation) and npm's `--provenance` flag (npm's own OIDC
  provenance, not SLSA). There is no `actions/attest-build-provenance` or
  equivalent SLSA generator anywhere in `.github/workflows/`.
- **SBOM**: `dependency-audit.yml` generates a CycloneDX SBOM via
  `pip-audit --format cyclonedx-json`, uploaded as a 14-day CI artifact. It is
  **not** attached to the GitHub Release, not published alongside the PyPI
  package, and only covers the Python dependency tree with `[dev,rag,mcp]`
  extras — it does not cover the npm launcher's dependency tree or the VS Code
  extension's.
- **Lockfile enforcement**: `uv.lock` currency is checked in three independent
  places — `ci.yml`'s `packaging` job (`uv lock --check`, hard fail),
  `lockfile.yml` (dedicated PR-triggered check with an auto-fix artifact and
  optional dispatch auto-commit), and implicitly by every `uv export --locked`
  call across `ci.yml`/`publish.yml`/`vscode.yml` (fails rather than
  re-resolving on drift). This is solid, if slightly redundant (CI-06).
- **Docker image**: built and smoke-tested in `ci.yml`'s `container` job only.
  Never pushed anywhere, and (CI-01) not even a required status check on
  `main`'s ruleset.

## Observed run reliability

Pulled via `gh run list --limit 100 --json name,conclusion,status,createdAt,event,headBranch` (last 100 runs across the repo as of 2026-11-01):

| Workflow | Runs | Failures | Failure rate | Notes |
|---|---:|---:|---:|---|
| Benchmark and Regression Corpus | 16 | 0 | 0% | Clean |
| CI | 11 | 0 (2 cancelled) | 0% | Cancellations are concurrency-group supersessions, not failures |
| Dependabot triage | 4 | 0 | 0% | 3 skipped (non-dependabot PRs correctly filtered) |
| **Dependency Review** | 7 | **2** | **29%** | Both failures are real: `##[error]Dependency review detected vulnerable packages` on `develop` (run 41227670020, 41227366473) — correct behaviour, not flakiness |
| Dependency audit | 7 | 0 | 0% | |
| Index this repository | 4 | 0 | 0% | |
| Lockfile Sync | 4 | 0 | 0% | |
| Publish | 1 (of last 100 mixed runs); 5 of last 5 `publish.yml`-only runs | 0 | 0% | All 5 most recent dispatches (`main`, 2026-09-26 → 2026-11-01) succeeded |
| VS Code extension | 6 | 0 | 0% | |
| codeql | 10 | 0 | 0% | |
| prod-igy | 11 | 0 (4 skipped) | 0% | Skips are the author-association gate correctly declining non-maintainer `issue_comment` triggers |
| provenance | 10 | 0 | 0% | |
| Dependabot's own per-ecosystem update workflows (`uv in /. for certifi`, `uv in /. for filelock`) | 1 each | 1 each | 100% | Dependabot's internal update-PR generation failing for those two packages specifically — outside this repo's workflow files, but worth a maintainer glance since it may indicate a resolution conflict for those two deps |

**Overall**: of the ~90 runs belonging to workflows in this repo's own
`.github/workflows/`, only 2 failures were observed, and both are the
dependency-review gate doing its job (catching genuinely vulnerable
transitive packages on `develop`), not flakiness. No workflow in the recent
window shows a pattern of intermittent/non-deterministic failure. This is
materially better reliability than the historical incidents on record (the
3.0.0 abort, the aborted-release recovery) would suggest — those appear to
predate the `prepare-release` gate-before-tag and signed-commit fixes
currently in `publish.yml`.

## Findings

**CI-01 — Severity: High. Confidence: High.**
Location: `main`'s active ruleset (`gh api .../rulesets/22778968`, rule type
`required_status_checks`) vs. `.github/workflows/ci.yml:135-144,294-299,363-368`.
Description: The ruleset's required contexts list `Test Suite (ubuntu-latest,
Python 3.11|3.12|3.13)`, `(macos-latest, ...)`, `(windows-latest, ...)` — nine
entries, exactly the 3.11–3.13 cross-product — but `ci.yml`'s matrix runs
twelve cells including Python 3.14. It also omits `Container Image Build &
Read-Only Smoke Test` entirely. `enforce_admins` is `false` and
`bypass_actors` grants `RepositoryRole 5` (effectively any repo admin)
`bypass_mode: "always"`.
Failure scenario: A PR that breaks the package on Python 3.14 (a version the
project ships classifiers for) or that breaks the Dockerfile can be merged to
`main` by a normal contributor with a fully green merge button, because
neither failing job is a required check. This is exactly the shape of the
3.0.0 admin-bypass incident, minus the admin: no bypass is even needed for
these two specific cells.
Recommendation: Add the four missing contexts (`Test Suite (ubuntu-latest,
Python 3.14)`, `(macos-latest, 3.14)`, `(windows-latest, 3.14)`, `Container
Image Build & Read-Only Smoke Test`) to the ruleset's `required_status_checks`
list. Separately, evaluate whether `enforce_admins`/ruleset bypass should stay
"always" given the documented admin-bypass-merged-red-release-pr incident.
Effort: Low (an API/UI change), but needs explicit maintainer sign-off since
it changes who can merge what.

**CI-02 — Severity: High. Confidence: Medium.**
Location: entire `.github/workflows/` tree; no Docker-registry push step
exists anywhere (confirmed via `grep -rn "docker push\|docker/login\|docker/build-push\|ghcr.io" .github/`
returning nothing) and no VS Code Marketplace/open-vsx publish step exists
(`grep -rn "vsce publish\|ovsx" .github/ editors/vscode/package.json` returning
nothing).
Description: Per the audit brief, this project "ships to PyPI, npm, Docker, a
GitHub Action and a VS Code extension." `publish.yml` publishes to PyPI, the
MCP Registry, and npm, and implicitly "publishes" the GitHub Action by tagging
(consumers pin `@vN`/`@vX.Y.Z`). It does not publish a Docker image to any
registry, and no workflow publishes the VS Code extension to any marketplace.
Failure scenario: Either (a) these two surfaces are not actually published
anywhere, and documentation/README claims about them are stale or aspirational
(worth cross-checking against Agent 1/Agent 6's findings on README claims), or
(b) they are published through a process entirely outside version control —
unauditable, unreproducible, and with no guaranteed correspondence to the
PyPI/npm version tagged in the same release. Either way, `CHANGELOG.md:788`
itself documents a prior, related incident: "Clarified Docker container build
expectations in SECURITY.md and README.md, removing stale claims of PR CI
building images" — i.e. this exact category of claim has drifted from reality
before.
Recommendation: Either wire a `docker/build-push-action` + registry login step
into `publish.yml` (gated the same way `npm`/`pypi` are, with the same
`needs: pypi` ordering) and a `vsce publish`/`ovsx publish` step for the
extension, or update every doc/README claim to explicitly state these are not
CI-published artifacts.
Effort: Medium (new credentials/secrets, registry choice, and
`version_surfaces.py` would need both as new surfaces).

**CI-03 — Severity: Medium. Confidence: High.**
Location: `.github/workflows/publish.yml:597-645` (`npm` job),
`publish.yml:486-489` (`release` job's `needs:`).
Description: The `npm` job exits 0 with only a `::warning::` when `NPM_TOKEN`
is unset, and the `release` job (which creates the GitHub Release and advances
the floating major tag) depends on `[prepare-release, pypi, mcp-registry]` —
not on `npm`. 
Failure scenario: If `NPM_TOKEN` is ever unset, rotated-and-not-updated, or
expired, the overall `publish.yml` run reports fully green, a GitHub Release
is cut, PyPI and the MCP Registry get the new version, and npm silently does
not — with no run-level failure, and only a step-level `::warning::` that
nobody is required to read. `npm/package.json`'s version (checked against the
release version in the step above it) would still match the *intended*
version even though the publish never happened, so the drift is invisible
until a user tries `npx repo2graph-mcp@latest` and gets the old version.
Recommendation: Make the missing-token case a hard failure (or at minimum
surface it in `release`'s job summary / via a required `needs: npm` with
`if: always()` reconciliation step) rather than a silent warning-and-continue.
Effort: Low.

**CI-04 — Severity: Medium. Confidence: Medium.**
Location: `.github/workflows/provenance.yml` (whole file, both jobs);
no `actions/attest-build-provenance` or SLSA generator anywhere in
`.github/workflows/`.
Description: `provenance.yml` is named for SLSA-style supply-chain provenance
but contains REUSE/license linting and zizmor workflow-security scanning —
useful, but not provenance. The only genuine attestation signal in the whole
pipeline is PyPI's own OIDC Trusted-Publishing attestation
(`gh-action-pypi-publish ... attestations: true`) and npm's `--provenance`
flag, both of which are provider-side features rather than something this
repo's CI explicitly generates and can verify.
Failure scenario: A consumer or security reviewer searching for "where does
this project produce SLSA provenance" finds a workflow named exactly that and
concludes (incorrectly) that build provenance is covered, when in fact no
in-repo step attests to *how* the wheel/sdist/npm package was built beyond
what PyPI/npm infer from the OIDC-authenticated job identity.
Recommendation: Either rename `provenance.yml` to reflect what it actually
does (`workflow-compliance.yml` or similar) to avoid the misleading signal, or
add an explicit `actions/attest-build-provenance` step in the `pypi`/`npm`
jobs and surface the attestation URL in the release summary.
Effort: Low (rename) to Medium (add real attestation).

**CI-05 — Severity: Low. Confidence: High.**
Location: `.github/workflows/lockfile.yml` (whole file) vs.
`.github/workflows/ci.yml:294-319` (`packaging` job's `lock_check` step).
Description: Both workflows independently run `uv lock --check` against the
same `pyproject.toml`/`uv.lock` pair on every PR that touches either file —
`lockfile.yml` is path-filtered to exactly `pyproject.toml`/`uv.lock`, so it
fires on precisely the PRs where `ci.yml`'s `packaging` job's own lock check
would already catch the same drift.
Failure scenario: Not a correctness bug — both will correctly fail together —
but it's redundant CI minutes and two separate red checks a contributor has to
reconcile for the same root cause, and `lockfile.yml` is not a required
status check so it adds signal noise without adding gate coverage `ci.yml`
doesn't already have.
Recommendation: Keep `lockfile.yml` only for its unique value (the
auto-generated-lockfile artifact upload and the dispatch auto-commit path);
consider dropping the pull_request trigger's duplicate check, or clearly scope
one of the two to be purely informational.
Effort: Low.

**CI-06 — Severity: Low. Confidence: Medium.**
Location: `.github/workflows/dependency-audit.yml:1-172` vs.
`.github/workflows/dependency-review.yml:1-23`.
Description: Both workflows gate on dependency vulnerabilities from two
different angles (installed-tree vs. diff-only) by design (documented in
`dependency-audit.yml`'s header), but neither is a required status check —
confirmed against the ruleset's `required_status_checks` list, which contains
neither "Dependency Supply Chain Review" nor the `dependency-audit.yml` job
name.
Failure scenario: A PR that introduces a newly-published high/critical CVE in
a new dependency can still merge with a fully green *required* check set,
because `dependency-review.yml`'s finding is informational only (matches the
file's own stated intent for `dependency-audit.yml`, but `dependency-review.yml`
carries no such caveat in its own comments — its omission from required
checks looks accidental rather than deliberate).
Recommendation: Confirm with maintainers whether `dependency-review.yml` (diff
of *newly introduced* deps, not the whole tree) was intentionally left out of
required checks; if not, add it — it is cheaper to require than
`dependency-audit.yml` since it only runs on the PR's own diff.
Effort: Low.

**CI-07 — Severity: Informational. Confidence: Medium (cross-reference to Agent 4).**
Location: `gh api repos/Srinivasan-78/repo2graph/rulesets/22778968` —
`bypass_actors: [{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"always"}]`,
and classic branch protection (`gh api .../branches/main/protection`) shows
`enforce_admins.enabled: false` and a *separate*, much narrower
`required_status_checks.contexts: ["License & Copyright Compliance
(REUSE/SPDX)"]` (the classic API's stale view of the one check that used to
gate before the ruleset took over — note this is cross-referenced for context
only, not re-litigated here since branch-protection/bypass depth is Agent 4's
primary scope).
Description: The classic branch-protection API and the active ruleset
disagree about what's required — the classic endpoint still reports the
single pre-ruleset check as its `required_status_checks`, which is misleading
to anyone reading only that endpoint (e.g. third-party tooling, compliance
scanners) rather than the ruleset.
Recommendation: For CI/CD-reliability purposes specifically: any tooling or
documentation that asserts "these are the required checks" by querying the
classic protection endpoint instead of the ruleset will be wrong. Make sure
internal docs/scripts (if any exist) read rulesets, not classic protection.
Effort: Low.

## Contributor workflow verification

- `.github/CONTRIBUTING.md` exists (1155 lines) and `Makefile` defines
  `install` (`pip install -e ".[dev,rag,mcp]"`), `lint` (`ruff check .`),
  `format`/`format-check`, `typecheck` (`python -m mypy repo2graph/`, whole
  package — deliberately not a hand-maintained subset, per its own comment),
  `test` (`pytest -q -n auto`), `test-serial`, `test-cov`, and `clean`. Every
  target a contributor would need (`install`→`lint`→`format-check`→
  `typecheck`→`test`) exists and maps directly onto what `ci.yml`'s
  `code-quality` and `tests` jobs run — `ruff check .`, `ruff format --check
  .`, `mypy --platform <p> repo2graph` (CI iterates three platforms; `make
  typecheck` runs the host's own platform only — a predictable, documented gap
  noted in `ci.yml`'s own comment, not a bug), and `pytest -q -n auto
  --cov=repo2graph --cov-report=term-missing`. Running `make -n test` and
  `make -n typecheck` (not `make test` itself, per the read-only constraint)
  confirms the exact commands Make would issue match these.
- **CI vs. local install drift**: `ci.yml`'s `code-quality`/`tests` jobs install
  via `uv export --locked ... | pip install --require-hashes` (hashed, locked),
  while `Makefile install` and several lighter-weight workflows
  (`benchmark.yml`, `perf.yml`, `examples.yml`, the `drift` job in `ci.yml`,
  `index-repo.yml`) use plain `pip install -e ".[dev,rag,mcp]"` or similar —
  unlocked. This is a real, acknowledged, and *intentional* split: the locked
  path is what gates PRs, the unlocked path is specifically what `drift` exists
  to exercise (catch upstream breakage), and `Makefile`'s unlocked `install`
  is the fast local-dev path. Not a defect, but worth knowing that `make
  install && make test` does not reproduce byte-for-byte what the required
  `tests` job installs — a contributor chasing a CI-only failure should
  reproduce with `uv export --locked ...` + `pip install --require-hashes`,
  not plain `make install`.
- **Pre-commit vs. CI**: `.pre-commit-config.yaml` runs `ruff --fix
  --exit-non-zero-on-fix`, `ruff-format --check`, several file-hygiene hooks,
  a local `version-consistency` hook (`check_version.py`, triggered on the
  exact file set `version_surfaces.py` lists) and a local `mypy-strict` hook
  scoped to `^repo2graph/.*\.py$` using the same `pyproject.toml`
  `[[tool.mypy.overrides]]` CI uses. Passing `pre-commit run --all-files`
  locally is therefore a reasonably faithful predictor of the `code-quality`
  job (same tools, same scope, same config) — it does not run the test suite,
  the packaging smoke test, the Docker smoke test, or the multi-OS matrix, all
  of which are separate CI jobs by design, not gaps in pre-commit.

## Limitations

- Did not inspect `.github/scripts/summary.py` or `prod-igy.js` line-by-line
  (read their callers and the associated tests' existence/names only); a
  deeper logic review of those two scripts was out of time budget and they are
  lower-risk (summary rendering, PR triage) than the release pipeline.
- Did not independently verify `[tool.coverage.run] fail_under = 80` inside
  `pyproject.toml` — taken from `ci.yml`'s own comments; flagged for
  cross-check against Agent 2's code-quality report if not already covered.
- Could not verify whether a Docker image or VS Code extension is actually
  published through some out-of-repo process (a maintainer's local `docker
  push` / `vsce publish`, a separate private pipeline, a marketplace listing
  maintained by hand) — only that no workflow file in this repository does it.
  This should be confirmed directly with the maintainer rather than assumed
  either way.
- Did not run `pytest`, did not trigger/cancel/dispatch any workflow, did not
  modify branch protection, rulesets, or any repository setting — all `gh`
  calls used were read-only (`run list/view --log-failed`, `api ... ` GET
  only).
- Run-history statistics are drawn from the last 100 runs across *all*
  workflows in the repo as of 2026-11-01; this is a short window for
  workflows that run rarely (e.g. `publish.yml` — only 5 dispatches visible
  total) and may not capture older incidents referenced in project history
  (the 3.0.0 abort, the aborted-release tag-move) since those predate the
  current `prepare-release` gate-before-tag logic and are not reflected in
  current run conclusions.
