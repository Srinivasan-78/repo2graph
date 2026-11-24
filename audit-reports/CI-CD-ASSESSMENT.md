# CI/CD and Release Engineering Assessment

Consolidated from `AGENT-FINDINGS/09-cicd.md`, with orchestrator verification,
**one finding rejected**, and **one finding added** that no agent reported.

No workflow was triggered, re-run, cancelled or dispatched during this audit.
All run-history analysis was read-only.

## Verdict

The CI *implementation* is strong: 15 workflows, a 12-cell OS × Python matrix,
SHA-pinned actions, release gating that directly addresses two recorded past
incidents, and a clean observed run history (2 failures in ~90 runs, both
legitimate vulnerability catches, no flakiness).

The *configuration around it* is where the problems are. The gate that enforces
CI is narrower than CI itself, and the branch topology means the published
default branch is three major versions behind the latest release.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| REL-01 | **HIGH** | Confirmed (orchestrator-discovered) | `main` / tag topology | Default branch is 240 commits and 3 major versions behind the published release |
| CI-01 | **HIGH** | Confirmed (orchestrator-verified) | ruleset 22778968 vs `ci.yml:144` | Required checks omit all Python 3.14 cells and the container job; admin bypass is always-on |
| CI-02 | MEDIUM | Confirmed | `publish.yml:597-638`, `:486` | npm publish silently no-ops without `NPM_TOKEN`, and `release` does not depend on it |
| CI-03 | MEDIUM | Medium | `provenance.yml` | Named for provenance but performs only REUSE/zizmor linting; no build attestation generated |
| CI-04 | LOW | High | `lockfile.yml` vs `ci.yml` packaging job | Duplicate `uv lock --check` on overlapping triggers |
| CI-05 | LOW | Medium | `dependency-audit.yml`, `dependency-review.yml` | Neither is a required check, so a PR introducing a CVE can merge green |
| ~~CI-X~~ | **REJECTED** | — | — | "No Docker/VS Code publish despite shipping both" — premise false, see below |

### REL-01 — The default branch is three major versions stale

**Discovered and verified by the orchestrator.** No specialist agent reported
this, because each was scoped to the working tree rather than to branch topology.

| Fact | Value |
|---|---|
| GitHub default branch | `main` |
| `main` HEAD | `63a73503` "chore(release): bump version to 2.2.0", **2026-09-26** |
| `main` `pyproject.toml` version | **2.2.0** |
| `develop` `pyproject.toml` version | **4.1.0** |
| Latest published release | **v5.0.0**, 2026-11-01 |
| `v5.0.0` release commit on `develop`? | **No** (`git merge-base --is-ancestor` → false) |
| `develop` ahead of `main` | **240 commits** |
| `main` ahead of `develop` | 0 commits |

Verification commands and their output are in `TEST-EXECUTION-LOG.md`'s sibling
checks; the topology was established with `git merge-base --is-ancestor`,
`git show <tag>:pyproject.toml` and `gh release list`.

**Three different versions are live simultaneously** — the default branch says
2.2.0, the development branch says 4.1.0, and the published release is 5.0.0.

Consequences, in order of severity:

1. **Everyone who visits the repository sees 2.2.0.** The rendered README, the
   browsable source, and `git clone` without a `--branch` flag all resolve to a
   branch five weeks and 240 commits stale. Every documentation improvement
   audited in `DOCUMENTATION-AUDIT.md` is invisible to visitors.
2. **`pyproject.toml`'s own URLs point into it.** The `"MCP server"` project URL
   is `blob/main/docs/mcp.md`, so the PyPI listing for the current release links
   to 2.2.0-era documentation.
3. **The v5.0.0 bump commit (`2a30c17e`) was never merged back.** This is why
   `develop`'s CHANGELOG still files the #523/#524/#525 fixes under
   `## [Unreleased]` even though they shipped in v5.0.0, and why `develop`'s
   version is 4.1.0 after a 5.0.0 release. The release branch is a dead end.

This is rated above every individual code finding except the working-tree
regression, because it degrades what *every* user and contributor receives,
continuously, with no error message.

**Recommendation:** merge the release commit back into `develop`, then
fast-forward `main` to the released state and keep it there as part of
`publish.yml`. If `develop` is intended as the real trunk, make it the default
branch instead. Either resolves it; the current state resolves it neither way.
Effort: **S** mechanically, but it is a release-process decision, not just a
merge.

### CI-01 — The merge gate is narrower than CI

**Orchestrator-verified** against `gh api repos/.../rulesets/22778968`.

Required status checks (15 contexts):

```
Code Quality & Static Analysis
Test Suite ({ubuntu,macos,windows}-latest, Python {3.11,3.12,3.13})   ← 9 cells
Package Distribution & MCP Stdio Smoke Test
Windows CP1252 Non-UTF8 Pipeline Compatibility
GitHub Action Composite Integration Test
Benchmark Regression Gate ({ubuntu,windows}-latest, Python 3.13)
```

Against `ci.yml:144`, whose matrix is `["3.11","3.12","3.13","3.14"]` × 3 OSes:

- **The three Python 3.14 cells are not required.** A PR that breaks only on 3.14
  can merge green — and 3.14 is advertised in `pyproject.toml` classifiers.
- **`Container Image Build & Read-Only Smoke Test`** (`ci.yml:364`) is not
  required. The read-only-container guarantee is a documented enterprise
  selling point, and its test cannot block a merge.
- **`bypass_actors: [{actor_type: RepositoryRole, actor_id: 5, bypass_mode: "always"}]`**
  — admin bypass is permanently enabled. This is the exact mechanism behind the
  recorded incident in which a red release PR was merged and 3.0.0 was tagged on
  failing tests. The gates added since then are real, but this door is still open.

**Recommendation:** add the three 3.14 cells and the container job to the
required set; change admin bypass from `always` to pull-request-only or remove
it. Effort: **S** (settings change).

### CI-02 — npm can silently drift behind PyPI

**Orchestrator-verified.** `publish.yml:638` emits
`::warning::NPM_TOKEN is not set; repo2graph-mcp $VERSION was not published to npm`
— a warning, not a failure. And `publish.yml:486` reads
`needs: [prepare-release, pypi, mcp-registry]`, which does not include `npm`.

So a release can report fully green while the npm package stays at an older
version. Since the npm package is a launcher that users install to get the MCP
server, a version skew is user-visible.

**Recommendation:** either make the npm job's absence of a token a hard failure,
or add `npm` to the `release` job's `needs` so the release reflects reality.
Effort: **S**.

### Rejected finding — "no Docker or VS Code publishing"

The CI specialist reported as **HIGH** that no Docker registry push and no VS Code
Marketplace publish step exist, "despite the project reportedly shipping both."

The absence is real — `grep` for `docker/build-push-action`, `docker push`,
`ghcr.io`, `vsce`, `ovsx` across `.github/workflows/` returns nothing, and
`publish.yml`'s jobs are `prepare-release`, `pypi`, `mcp-registry`, `release`,
`npm`. **But the premise is false.** The project does not claim to publish either:

- `README.md:234` offers "A Dockerfile for …" — a Dockerfile, not a published image.
- `editors/vscode/README.md:16` states plainly: *"It is a preview, built from
  source; it is not on the Marketplace."*

**This finding is withdrawn.** It arose because the orchestrator's own task
briefing listed five "distribution channels" including Docker and VS Code, and
the agent took that framing as established fact rather than verifying it. Recorded
here as a correction, and as a reminder that an agent's inherited premises need
checking as much as its conclusions. `REPOSITORY-OVERVIEW.md`'s channel table is
accurate only if Docker and VS Code are read as *available artifacts*, not
published releases.

### CI-03 — `provenance.yml` does not produce provenance

The workflow runs REUSE and zizmor linting. It does not generate a SLSA build
attestation, and no `actions/attest-build-provenance` step exists anywhere. The
name creates a reasonable but false impression that published artifacts carry
verifiable provenance.

**Recommendation:** either add real attestation to `publish.yml` (GitHub's
`attest-build-provenance` action makes this cheap for PyPI artifacts) or rename
the workflow to describe what it does. Effort: **S** to rename, **M** to
implement.

## Observed reliability

From read-only `gh run list` history: **2 failures in approximately 90 recent
runs**, both legitimate vulnerability catches rather than infrastructure flakes.
No flaky workflow was identified. This is a genuinely healthy signal and is
consistent with the orchestrator's own full-suite run completing in 66s with one
explainable failure.

## What the release pipeline gets right

Recorded deliberately, because the project has a recorded history of release
incidents and the response to them was substantive:

- **Test-before-tag.** `publish.yml` runs the suite before tagging, then
  re-checks required status checks before pushing the tag — directly addressing
  the "red release PR got merged" incident.
- **Signed commits via the GraphQL API.** `commit_release_via_api.py` exists
  because required-signature rules cannot be satisfied by a local CI commit; the
  API path produces properly attributed signed commits. An unusual constraint,
  solved correctly.
- **Version derived, never hand-bumped.** The version comes from commit headers
  and the CHANGELOG, which removes a whole class of surface-drift errors — and
  `scripts/version_surfaces.py` enforces the remaining surfaces with an explicit
  allowlist rather than a glob, with a documented rationale for why globbing
  would rewrite history.
- **The Python 3.10 drop was applied consistently** across every workflow matrix.
  (It missed `SECURITY.md` and `doctor.py` — see `DOCUMENTATION-AUDIT.md` DOC-01
  and `DEPENDENCY-AND-COMPATIBILITY.md` COMPAT-01 — but the CI side was clean.)
- **Pre-commit faithfully predicts CI's code-quality job**, so local checks are
  trustworthy.

## Contributor workflow

`make install` / `lint` / `format-check` / `typecheck` / `test` all exist and
correspond to what CI runs. `make test` uses `-n auto` and completed in 66s here.
A contributor can clone, install, test and validate using only documented
procedures — verified, with the caveat that cloning the default branch gets them
2.2.0 (REL-01).

## Limitations

- No workflow was executed; assessment is from YAML plus read-only run history.
- Release behaviour under failure (partial publish, re-run, malformed commit
  header) was reasoned about from the YAML, not exercised.
- Classic branch-protection API and the active ruleset disagree about required
  checks; the ruleset was treated as authoritative since its enforcement is
  `active`.
