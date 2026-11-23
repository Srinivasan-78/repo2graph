# Agent 10 — Dependencies, Licensing & Supply-Chain Security

Scope: `repo2graph` Python package, `npm/` launcher, `editors/vscode/` extension, Docker image.
Read-only audit. No file other than this report was modified. No installs, locks, or syncs were run.

## Summary & dependency footprint

- **Runtime dependency count: 2.** `tree-sitter>=0.23` and `tree-sitter-language-pack>=0.7,<1.0`. Both are
  imported and used (verified by grep, see below) — no undeclared-but-imported or declared-but-unused
  runtime dependency.
- **uv.lock: 120 distinct `[[package]]` entries** (122 rows counting the two version-marker splits for
  `numpy` and `scipy` — see DEP-09), spanning the base install plus every extra (`dev`, `rag`, `rag-static`,
  `mcp`, `completion`).
- **Extras:** `dev` (pytest/ruff/mypy/pre-commit tooling), `rag` (sentence-transformers + numpy, pulls torch
  and the full NVIDIA CUDA stack on Linux), `rag-static` (model2vec, numpy — torch-free alternative),
  `mcp` (the `mcp` SDK, needed only by `repo2graph-mcp`), `completion` (argcomplete).
- **Dependabot alerts: 37 total, all `state: fixed`, 0 open, 0 dismissed** (verified live via
  `gh api repos/Srinivasan-78/repo2graph/dependabot/alerts --paginate`). This matches the two recent
  remediation commits and is a genuinely clean state right now — see "Known vulnerabilities" for caveats.
- **npm (`npm/`):** zero runtime dependencies — a 150-line launcher script, nothing to audit for CVEs.
- **VS Code extension (`editors/vscode/`):** 4 packages total, all dev-only (`@types/node`,
  `@types/vscode`, `typescript`, `undici-types`), 0 runtime deps shipped to users. `npm audit
  --package-lock-only` (ran live, no install) reports 0 vulnerabilities.
- **Docker:** `python:3.12-slim`, tag-pinned not digest-pinned, non-root (10000:10000) runtime user,
  multi-stage build, installs only `.[mcp]` (rag/torch deliberately excluded from the image).

## Declared vs imported dependency reconciliation

| Dependency | Declared | Imported where | Verdict |
|---|---|---|---|
| `tree-sitter` | `>=0.23` | `repo2graph/frameworks.py:35`, `repo2graph/parse.py:13`, `repo2graph/refcapture.py:7` | Used, floor plausible (no version-gated API cited against it) |
| `tree-sitter-language-pack` | `>=0.7,<1.0` | `repo2graph/frameworks.py:421`, `repo2graph/parse.py:564` (lazy `get_parser` import) | Used; cap is deliberate (see below) |
| `numpy` (`rag` extra) | `>=1.24` | **Never imported by `repo2graph`** — `embed.py` writes/reads `vectors.npy` by hand with `struct` | Declared-but-unused **by design**, documented in a pyproject.toml comment: it exists so `[rag]` installs something that can open the output with `numpy.load`. Not a finding — correctly justified and already called out in the source comment. |
| `sentence-transformers` (`rag`) | `>=3.0` | consumed via subprocess/import path for dense embedding backend | Used |
| `model2vec` (`rag-static`) | `>=0.10,<1.0` | dense embedding backend, torch-free path | Used; cap matches the "major bump is untested" pattern used elsewhere in this file |
| `mcp` | `>=2.0,<3.0` | `repo2graph/mcp/server.py` (`SDK_SPEC`) | Used; floor raised from 1.x after a documented 2.2.0 hang bug (#407) |
| `pytest-xdist`, `hypothesis`, `pyyaml`, etc. (`dev`) | various | test suite / tooling only | Used, each has a one-line justification comment in pyproject.toml |
| `argcomplete` (`completion`) | `>=3.0` | shell completion wiring | Used |

No undeclared import of a third-party package was found in `repo2graph/*.py` outside the declared set
(checked `tree_sitter`, `tree_sitter_language_pack`, `numpy`, `mcp`, `sentence_transformers`, `model2vec`,
`argcomplete` import sites).

## The tree-sitter-language-pack cap: verification & residual risk

**The stated rationale checks out against current upstream reality** (verified via web search, since this
cannot be checked purely offline):

- The pre-1.0 line (0.x, up to and including **0.13.0**, the version actually locked here) ships every
  grammar compiled into the wheel — no network calls at `get_parser()` time. This matches the pyproject.toml
  comment exactly.
- The 1.x line is a from-scratch Rust-core rewrite (project since moved between `Goldziher`/`kreuzberg-dev`/
  `xberg-io` org names) that ships a small wheel and fetches grammars on demand from the network on first
  use — independently corroborated by a real downstream bug report (a project's "offline first run: every
  file retries the grammar download and gets no symbols, silently") and by the Rust ecosystem's own
  parallel package explicitly needing a vendoring workaround for offline/reproducible builds. `PackConfig`
  in 1.x indeed exposes only pre-download cache/selection knobs, no offline switch — confirming the
  pyproject.toml claim that there is no configuration making 1.x satisfy the no-network guarantee.
- **DEP-T1 (finding, see below): the pinned 0.x line appears to be dead, not just capped.** Available
  evidence indicates 0.13.0 was the *last* pre-1.0 release before the project moved to 1.0.0 and the
  Rust-core architecture; there is no sign of any 0.x release since. That means the cap does not pin to "a
  slower-moving stable line" — it pins to a **frozen branch that receives no further commits of any kind**,
  security or otherwise. Any future CVE discovered in a bundled grammar's parser code, or in the 0.x
  Python/C binding layer itself, cannot be fixed by bumping within the cap; the only paths are (a) accept
  the parked version indefinitely, (b) vendor grammars and move to 1.x's architecture, or (c) fork/patch 0.x
  directly. This is a real, if currently unrealized, security debt the cap carries — not a reason to lift
  it, but something the project should track explicitly (e.g. a periodic check for 0.x grammar CVEs, since
  pip-audit/OSV coverage of a frozen minor-ecosystem package may be thin).
- **Vendoring assessment:** credible but nontrivial. Evidence from analogous projects (Rust's
  `ae-tree-sitter-bundle`, a downstream Arch Linux package of 1.15.0) shows the pattern is: run the fetch
  script once online, commit the resulting grammar sources/binaries into the repo, and build/load
  statically against the vendored copy with network access disabled at runtime. For repo2graph this would
  mean: pick the subset of 1.x grammars actually used (likely far fewer than its 300+ bundled languages),
  vendor them under something like `third_party/grammars/`, verify their licences (grammars are themselves
  separately licensed, mostly MIT/Apache-2.0/BSD per language, so a vendoring pass would need its own
  per-grammar licence audit feeding into `LICENSES/`), and adjust `grammar_fingerprint()` in `parse.py`
  (already keys the parse cache on grammar versions) to key on the vendored commit/hash instead. This is a
  multi-week engineering effort, not a one-line fix, and is reasonably deferred — but it is the only path
  that moves off a frozen 0.x line while keeping the no-network guarantee, so it is worth a tracked issue
  rather than leaving the comment as the final word.

**Dependabot enforcement — verified correct, not a gap.** `.github/dependabot.yml`'s `ignore` block for
`tree-sitter-language-pack` scopes to `update-types: ["version-update:semver-major"]`. Since there is no
further 0.x release to propose, and a 0.x→1.x bump is unambiguously a semver-major jump, this rule does
correctly block Dependabot from ever opening a PR that raises the cap. This is **not** a process finding —
the enforcement matches the documented intent.

## Known vulnerabilities

**Verified via `gh api` (live, read-only):**
- `repos/Srinivasan-78/repo2graph/dependabot/alerts --paginate`: **37 alerts, all `state: fixed`.**
  `?state=open` and `?state=dismissed` both return `[]`. Breakdown: 23 `filelock` (1 high `GHSA-v4x8-3wr5-3wq8`,
  rest medium/low), 12 `virtualenv` (several high), 2 `PyJWT` (medium), 1 `setuptools` (low), 1 `certifi`
  (critical, `GHSA-6q48-6c7w-9gw2`), 1 `opentelemetry-api` (medium).
- Cross-checked against `.github/remediation-tracking.md` and the two recent commits
  (`4c9baeff` "bump filelock and setuptools to clear Dependabot alerts", `af907bbe` "drop Python 3.10 to
  clear the filelock advisories"): the commit messages' claimed alert numbers (#17, #18, #26–#38) match the
  alert numbers the API now reports as fixed. `uv.lock` locks `filelock==4.2.6` and `setuptools==84.2.1`,
  consistent with the commits' stated bump targets. **The remediation is genuinely reflected in both the
  lockfile and the alert API as of this check** — this is the rare case where the alert feed and the lock
  agree.
- **Caveat (from project history, not contradicted here but worth restating):** this repo has a documented
  prior pattern of alerts lagging a clean dependency graph. The fact that all 37 are `fixed` right now is a
  point-in-time result, not a standing guarantee — the next lockfile bump should re-run this same
  `gh api ... /dependabot/alerts` check rather than assume the graph self-updates promptly.
- `certifi`'s critical alert (`GHSA-6q48-6c7w-9gw2`) is worth flagging on its own: `certifi` is a
  trust-store/CA-bundle package, so a critical advisory there is unusual to see marked `fixed` via a
  `filelock`/`setuptools` bump commit that never names `certifi`. It is plausible this was closed by the
  lockfile's independent resolution of a newer `certifi` (`2026.10.23` is current in `uv.lock`) rather than
  by either named commit — the remediation-tracking.md text itself says "17 of the 31 open alerts (certifi,
  virtualenv, opentelemetry-api) named versions the lock had already moved past... closed by the rescan
  rather than by a bump," which matches.

**Could not verify offline / without installing tooling:**
- `pip-audit` is **not installed** in this environment and was not installed per the read-only constraint.
  No independent OSV/PyPI-advisory cross-check of the full resolved tree (including `rag`/`mcp` extras) was
  possible beyond what the Dependabot alert history already covers. `.github/workflows/dependency-audit.yml`
  already runs this in CI with `--strict` on `[dev,rag,mcp]`, so CI coverage exists; this audit could not
  reproduce it locally.
- `npm audit --package-lock-only` **was** run (npm 11.17.0 is installed) against
  `editors/vscode/package-lock.json`: **0 vulnerabilities** across 4 dev-only packages. `npm/` has no
  lockfile and no dependencies, so there is nothing to audit there.
- No independent CVE lookup was done for the pinned `tree-sitter-language-pack 0.13.0`/`tree-sitter 0.26.0`
  beyond what the Dependabot feed (pip ecosystem) already covers.

## Transitive & maintenance risk

- **Total packages locked: 120** (`grep -c '^name = ' uv.lock`, two of which — `numpy`, `scipy` — appear
  twice under disjoint Python-version resolution markers, which is a normal uv.lock splitting pattern, not
  a duplicate-dependency bug).
- **Single largest blast radius: the `rag` extra.** `sentence-transformers` → `torch` → the full NVIDIA
  CUDA 13 toolkit (`nvidia-cublas`, `nvidia-cudnn-cu13`, `nvidia-cufft`, `nvidia-cusolver`, `nvidia-nccl-cu13`,
  `triton`, etc. — 14 NVIDIA packages alone, Linux-only via `sys_platform == 'linux'` markers) plus
  `transformers`, `tokenizers`, `huggingface-hub`, `safetensors`, `scikit-learn`, `scipy`. This is by far
  the largest dependency subtree and the one `.github/workflows/dependency-audit.yml` explicitly calls out
  as "the attack surface most worth watching." `rag-static` (model2vec) is a deliberately smaller
  alternative (~60 MB vs several GB) and does not pull torch — this trade-off is well-documented in
  pyproject.toml and is sound.
- **Native code / compiled extensions present in the lock:** `tree-sitter`, `tree-sitter-language-pack`
  (plus three standalone grammar packages — `tree-sitter-c-sharp`, `tree-sitter-embedded-template`,
  `tree-sitter-yaml` — pulled in transitively, presumably by the language pack or a dev/test path), `cffi`,
  `cryptography`, `rpds-py`, `pydantic-core`, `regex`, `safetensors`, `scipy`, `torch`/`triton`/CUDA
  libraries, `pywin32` (Windows-only, via `mcp`'s `sys_platform == 'win32'` marker).
- **mcp SDK's own transitive footprint is broad for a stdio-only consumer.** `mcp` 2.2.0 pulls `httpx2`,
  `starlette`, `sse-starlette`, `uvicorn`, `python-multipart`, `pyjwt[crypto]` (→ `cryptography`),
  `opentelemetry-api`, `jsonschema` — an HTTP-server-and-auth stack — even though repo2graph's documented
  contract is stdio-only (the HTTP/auth code was deliberately deleted from repo2graph itself per
  `.github/remediation-tracking.md`'s "Not applicable" section). This is upstream SDK packaging, not
  something repo2graph controls, but it means `pip install "repo2graph[mcp]"` installs a JWT/crypto stack
  that is never exercised by any code path repo2graph itself ships. Worth a one-line note in docs if not
  already present; not a vulnerability by itself.
- **Maintenance status, determinable offline from `uv.lock` dates:** most core pins (`pydantic 2.13.5`,
  `httpx 0.28.1`, `starlette 1.6.0`, `rich 15.0.0`, `ruff 0.16.7`) carry upload timestamps within the last
  few months of the lock's generation and read as actively maintained. The one package with a credible
  maintenance concern, verified via web search rather than offline data, is `tree-sitter-language-pack`'s
  pinned 0.x line itself (DEP-T1 above) — frozen since the 1.0 rewrite.
- **No single point of failure beyond the two runtime deps themselves** — if `tree-sitter-language-pack`
  became fully unavailable there is no parser fallback, but that is an accepted, documented trade-off of
  the project's core architecture, not an oversight.

## Licensing & REUSE compliance

- `pyproject.toml`: `license = "MIT"`, `license-files = ["LICENSE"]`.
- `LICENSE`: MIT License, copyright "Srinivasan-78", 2026.
- `LICENSES/MIT.txt`: present, standard SPDX MIT text — consistent with the above.
- `CITATION.cff`: `license: MIT`, `version: 4.1.0` (matches `pyproject.toml`'s `version = "4.1.0"`) — no
  drift between these two version surfaces at the time of this audit.
- `REUSE.toml`: a single blanket `[[annotations]]` with `path = "**"`, `precedence = "aggregate"`,
  `SPDX-License-Identifier = "MIT"` — this licenses every file (including `.github/`) as MIT by default,
  with any per-file SPDX header (seen for example in `npm/bin/repo2graph-mcp.js`) taking precedence. This is
  a valid and simple REUSE compliance strategy; nothing here is a violation.
- **Dependency licence compatibility with MIT:** not independently re-verified per-package (no offline
  licence database was available and installing `pip-licenses` or similar was out of scope for a read-only
  audit). Based on the packages' well-known public licences from general knowledge: `tree-sitter` and
  `tree-sitter-language-pack` are MIT; `numpy`, `sentence-transformers`, `scikit-learn`, `scipy`, `torch`
  (BSD-style), `pydantic`, `httpx`, `starlette`, `rich`, `mcp`-family, `pytest`, `ruff`, `mypy` are
  MIT/Apache-2.0/BSD — all permissive and MIT-compatible for this project's distribution model (none of
  these dependencies are statically linked/redistributed by repo2graph itself; they are installed
  alongside it). **This is not a verified finding, only a plausibility check** — a proper SPDX/licence scan
  of the full resolved tree (e.g. via `pip-licenses` or the CycloneDX SBOM that `dependency-audit.yml`
  already generates) is the correct way to confirm this and was not performed here.

## npm / VS Code / Docker surfaces

- **`npm/package.json`** is a pure launcher: zero `dependencies`, `files: ["bin/"]`, `engines.node: >=18`.
  `npm/bin/repo2graph-mcp.js` resolves `uvx` → `repo2graph-mcp` on PATH → `pipx`, in that order, and on the
  `uvx` path runs `uvx --from "repo2graph[mcp]" repo2graph-mcp`.
  - **DEP-11 (finding): the npm package's own version (4.1.0, kept in step with the Python package per its
    `description`) is not actually used to pin what gets run.** `--from "repo2graph[mcp]"` carries no
    version specifier, so `npx repo2graph-mcp@4.1.0` (or any other npm-side version) will still resolve and
    run whatever `repo2graph[mcp]` is *currently latest on PyPI* through `uvx`, not version 4.1.0. If a
    future Python release ships a breaking MCP protocol or CLI-flag change, every existing `npx
    repo2graph-mcp` invocation silently picks it up with no way to pin via the npm package version alone.
    This is a reasonable trade-off for a thin launcher (matches the stated design: "this package ships no
    server code of its own") but is worth documenting explicitly as a known behavior, since the version
    number on the npm package currently implies a correspondence that does not exist in the invocation path.
  - The script's own `.cmd`/`.bat` shell-quoting hardening (CVE-2024-27980 Node.js fix, explicit
    `quoteForCmd`/`CMD_UNSAFE_RE` handling) is well done and specifically defends against a real,
    numbered CVE class — noted as a positive, not a finding.
- **`editors/vscode/package.json`:** `engines.vscode: ^1.85.0`, devDependencies only
  (`@types/node 20.17.6`, `@types/vscode 1.85.0`, `typescript 5.6.3`), no runtime dependencies shipped to
  end users. `package-lock.json` resolves to exactly 4 packages (`@types/node`, `@types/vscode`,
  `typescript`, `undici-types` as a transitive dep of `@types/node`). `npm audit --package-lock-only`
  (ran live): 0 vulnerabilities. This is a minimal, low-risk footprint — nothing to flag.
- **Dockerfile:**
  - **DEP-12 (finding): base image is tag-pinned (`python:3.12-slim`), not digest-pinned.** A tag can move
    (a rebuild of the `3.12-slim` tag changes the underlying image without changing the Dockerfile), so the
    build is not fully reproducible and is not protected by Dependabot's own SHA-pinning pattern that the
    project already applies carefully to GitHub Actions (`dependabot.yml`'s own comment: "Dependabot
    rewrites the pinned SHA... which is what keeps SHA pinning maintainable"). The same philosophy is not
    applied to the Docker base image.
  - **DEP-13 (finding/gap): Docker is not a Dependabot ecosystem in `.github/dependabot.yml`.** Even if the
    tag were digest-pinned, nothing currently proposes updates to it. See "Dependabot process assessment."
  - Everything else in the Dockerfile is well-reasoned and documented: multi-stage build (builder/runtime
    split), `pip install --no-cache-dir ".[mcp]"` (not `[rag]`, deliberately, per its own comment, to avoid
    shipping torch/CUDA by default), non-root `10000:10000` user, `git` installed via apt with
    `--no-install-recommends`, `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED` set for a read-only rootfs, and
    a scoped `git config --system --add safe.directory /repo` done as root then dropped back to the
    unprivileged user. Dependencies are installed via `pip install ".[mcp]"` against the repo's own
    `pyproject.toml`/`uv.lock`-resolved state, not an ad hoc separate requirements list — it does not bypass
    the lockfile's version resolution in spirit, though `pip install` (vs. `pip install --require-hashes` or
    a `uv pip sync` from the lock) means the image build itself does not enforce the exact hashes `uv.lock`
    pins; it re-resolves within the same constraints. This is a minor reproducibility gap, not a security
    one, since `pyproject.toml`'s version ranges are tight.

## Dependabot process assessment

- **Ecosystems covered: 2 of 4** (`pip`, `github-actions`). **Not covered: `npm` and `docker`** — this
  repo has two npm manifests (`npm/package.json`, `editors/vscode/package.json`, the latter with a real
  lockfile and devDependencies that could theoretically go stale/vulnerable) and one `Dockerfile` with a
  base image, and none of the three has an automated update path. This is the clearest process gap found.
- **What is configured is well-built:** weekly Monday schedule, `open-pull-requests-limit: 5`,
  minor/patch grouping (so a major always gets its own reviewable PR), a 7-day cooldown, assignee set, and
  — specifically verified above — the `tree-sitter-language-pack` major-version ignore rule that correctly
  enforces the security-boundary cap.
- **Automated security fixing:** could not be directly verified (requires a repo-admin-level API scope /
  UI check not exercised here), but the fact that all 37 Dependabot alerts currently read `fixed` and the
  two recent commits explicitly reference clearing specific alert numbers is consistent with either manual
  remediation or functioning automated security PRs; this audit cannot distinguish which, only that the
  outcome (`fixed`) is real.
- `.github/workflows/dependency-review.yml` (on every PR) and `.github/workflows/dependency-audit.yml`
  (weekly + on PR, `pip-audit --strict` against `[dev,rag,mcp]`, SBOM generation) both exist and are
  reasonably configured, and are a second line of defence independent of Dependabot's own alerting.
  `.github/workflows/lockfile.yml` enforces `uv.lock` freshness against `pyproject.toml` on every PR that
  touches either file — this closes the "drifted lockfile" failure mode the Dependabot `pip` ecosystem
  update would otherwise depend on being merged promptly.

## Python version compatibility

- `requires-python = ">=3.11"`, classifiers for 3.11–3.14.
- **No dependency in `uv.lock` was found to block 3.14** — `numpy` and `scipy` both carry a resolution
  marker branch for `python_full_version == '3.14.*'` and `>= '3.15'` resolving to the newer `2.5.3`/`1.18.1`
  builds respectively, meaning the lock already has 3.14-compatible wheels selected. `torch`'s own
  dependency markers use `python_full_version < '3.15'` guards for CUDA-bindings/triton, consistent with
  torch itself supporting up to 3.14 at the locked version. Nothing in the lock pins a ceiling below 3.14.
- **DEP-14 (finding): dead code and stale comments from the just-completed Python 3.10 drop remain in
  `repo2graph/__init__.py`.** The commit `af907bbe` ("drop Python 3.10 to clear the filelock advisories")
  removed the `tomli` fallback from `repo2graph/config.py` but **missed an equivalent fallback in
  `repo2graph/__init__.py`**:
  - `repo2graph/__init__.py:28` and `:52`: comments literally state "tomllib is 3.11+ and this project
    supports 3.10" — false as of this commit; `requires-python` is now `>=3.11`.
  - `repo2graph/__init__.py:21-44` (`_parse_toml`): the `try: import tomllib / except
    ModuleNotFoundError: return False, None` branch is now unreachable dead code — `tomllib` is guaranteed
    present on every supported interpreter (3.11 is stdlib-tomllib's floor). The `_pyproject_version`
    regex-fallback path this unreachable branch exists to feed is therefore also now permanently unused in
    practice.
  - `repo2graph/security.py:261`: a comment ("Python 3.10 is the floor here, so possessive `*+` is not
    available") is similarly stale — harmless (it doesn't affect correctness, since the regex still works
    fine on 3.11+; possessive quantifiers remain unavailable in Python's `re` module at 3.11 too, so the
    *constraint* described is still technically true, just for a different reason than stated), but still
    describes a floor the project no longer has.
  - This is cosmetic/hygiene, not a bug — the fallback path being dead does not cause incorrect behavior,
    it is just unreachable code the 3.10-removal pass should have caught (it did catch the equivalent code
    in `config.py` and the doctor check, documented in the commit; `__init__.py` was simply missed).

## Findings

| ID | Severity | Confidence | Location | Description | Risk | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| DEP-01 | Info | High | `pyproject.toml` deps block | `tree-sitter`/`tree-sitter-language-pack` are the only runtime deps; both verified imported in `repo2graph/frameworks.py`, `parse.py`, `refcapture.py`. No undeclared or unused runtime dep. | None — confirms clean state | No action | — |
| DEP-02 | Info | High | `pyproject.toml` `[rag]` extra, `numpy` | Declared but never imported by `repo2graph` itself; deliberately documented as a consumer-side declaration so output `.npy` files are openable. | None, by design | No action; the existing comment already explains it | — |
| DEP-T1 | Medium | Medium | `pyproject.toml` tree-sitter-language-pack cap; verified via web search, not locally checkable | The pinned 0.x line (locked at 0.13.0) appears to be the last release before the project moved to the 1.x Rust-core rewrite — the branch repo2graph depends on receives no further releases of any kind, security or otherwise. | A future grammar/binding CVE in the 0.x line has no in-range fix available; the only remedies are accepting it, forking/patching, or vendoring grammars to move to 1.x's architecture. | Do not raise the cap. Open a tracked issue to (a) periodically check for 0.x-line advisories via OSV/GHSA directly (not just Dependabot, which may have thin coverage of a frozen minor package) and (b) scope the vendoring effort (grammar subset, per-grammar licence audit, `grammar_fingerprint()` rework) as a deliberate future migration, not an emergency reaction. | Tracking: small. Vendoring itself: large (multi-week). |
| DEP-03 | Info | High | `.github/dependabot.yml` ignore rule for `tree-sitter-language-pack` | The `version-update:semver-major` ignore correctly blocks any Dependabot PR that would raise the cap. | None — confirms correct enforcement | No action | — |
| DEP-04 | Low | High | `gh api .../dependabot/alerts` | All 37 historical alerts are `state: fixed`; 0 open, 0 dismissed, matching the two recent remediation commits' claimed alert numbers and `uv.lock`'s `filelock==4.2.6`/`setuptools==84.2.1`. | None currently — informational | Re-run this exact check after any future lockfile bump; do not assume the alert feed stays in sync automatically given this repo's documented history of alert lag. | Trivial, recurring |
| DEP-05 | Info | Medium | `certifi` alert `GHSA-6q48-6c7w-9gw2` (critical, now fixed) | Closed by lockfile rescan picking up a newer `certifi`, not by either of the two named remediation commits, per `.github/remediation-tracking.md`'s own note. | None — just a provenance clarification | None needed; noted for audit trail completeness | — |
| DEP-06 | Medium | Low (unverifiable offline) | whole resolved tree, incl. `[rag]`/`[mcp]` | `pip-audit` is not installed; no independent OSV cross-check of the full tree was possible in this read-only audit beyond the Dependabot alert history (pip ecosystem only). | Unknown — could be hiding something Dependabot's own scanning doesn't catch | Run `.github/workflows/dependency-audit.yml` (it already does this in CI) and review its artifact; do not rely solely on Dependabot alerts for extras-level coverage. | Already automated in CI; this is a "trust the existing workflow" note |
| DEP-07 | Low | High | `uv.lock`, `rag` extra | `sentence-transformers`→`torch` pulls 14 NVIDIA CUDA packages plus `transformers`/`tokenizers`/`scikit-learn`/`scipy` — by far the largest and most volatile subtree, already called out by the project's own CI comments as the surface most worth watching. | Elevated attack surface for anyone installing `[rag]`; mitigated by `rag-static` existing as an alternative and by the Docker image deliberately excluding `[rag]`. | No change needed; this is already well-mitigated and documented. Consider periodically re-confirming `rag-static` remains a viable drop-in for the common case. | None required now |
| DEP-08 | Info | Medium | `mcp` 2.2.0 transitive deps (`pyjwt[crypto]`→`cryptography`, `starlette`, `uvicorn`, `sse-starlette`, `python-multipart`, `opentelemetry-api`) | `pip install "repo2graph[mcp]"` installs a full HTTP-server/JWT/crypto stack even though repo2graph's own HTTP/auth code was deliberately deleted and the server is stdio-only. | Upstream SDK packaging choice, not under repo2graph's control; slightly larger attack surface than strictly necessary for stdio-only use. | Document the discrepancy (one line in docs/mcp.md or SECURITY.md: "the mcp SDK installs an HTTP/auth stack repo2graph does not use") so it isn't mistaken for repo2graph's own design. Consider filing upstream if the SDK ever supports an extras split. | Small (docs only) |
| DEP-09 | Info | High | `uv.lock` `numpy`/`scipy` duplicate `name =` entries | Two entries each are disjoint Python-version resolution-marker splits (`<3.12` vs `>=3.12`), not duplicate/conflicting pins. | None — confirms normal uv.lock behavior | No action | — |
| DEP-10 | Info | Medium | general dependency licences | No per-package licence re-verification was performed beyond general knowledge of well-known permissive licences (MIT/BSD/Apache-2.0 across the board); all appear MIT-compatible. | Low — unlikely to surface a copyleft dependency given the ecosystem, but not independently confirmed | Run `pip-licenses` or inspect the CycloneDX SBOM `dependency-audit.yml` already generates to get a verified per-package licence table. | Small, and tooling already exists in CI |
| DEP-11 | Low | High | `npm/bin/repo2graph-mcp.js`, `uvx --from "repo2graph[mcp]"` (no version pin) | The npm package's own version number (kept at 4.1.0, matching the Python package) is not actually used to select what `uvx`/`pipx` runs — it always resolves PyPI's current latest `repo2graph[mcp]`. A version mismatch between the npm package a user thinks they installed and what actually executes is possible and currently undocumented. | Low-moderate: a future breaking Python-side release changes behavior for existing `npx repo2graph-mcp` users with no npm-side signal. | Document this explicitly in `npm/README.md` / `docs/mcp.md` ("this launcher always runs the latest published `repo2graph[mcp]`; pin via `uvx --from 'repo2graph[mcp]==X.Y.Z'` directly if you need a fixed version"). | Small (docs only) |
| DEP-12 | Low | High | `Dockerfile:2,19` (`FROM python:3.12-slim`) | Base image is tag-pinned, not digest-pinned, unlike the project's careful SHA-pinning discipline for GitHub Actions. | Build is not byte-for-byte reproducible; a tag rebuild silently changes the base layer. | Pin `python:3.12-slim` to a digest (`@sha256:...`), ideally with a renovate/Dependabot docker-ecosystem job to keep it current (see DEP-13). | Small |
| DEP-13 | Medium | High | `.github/dependabot.yml` (no `docker` or `npm` ecosystem entries) | Only 2 of this repo's 4 real package ecosystems (pip, github-actions, npm×2 manifests, docker) have an automated update path. `npm/package.json` has zero deps so is low-urgency, but `editors/vscode/package.json` has a real lockfile and the `Dockerfile` has a base image — neither gets automated version-bump PRs. | A stale/vulnerable devDependency in the VS Code extension or an aging Docker base image can go unnoticed indefinitely; nothing currently surfaces either. | Add `package-ecosystem: "npm"` entries for both `npm/` and `editors/vscode/` directories, and `package-ecosystem: "docker"` for the root `Dockerfile`, to `.github/dependabot.yml`, following the same grouping/schedule/cooldown pattern already used for `pip`. | Small (config only) |
| DEP-14 | Low | High | `repo2graph/__init__.py:21-44,28,52`; `repo2graph/security.py:261` | Dead `tomllib`-availability fallback code and stale "this project supports 3.10" comments survive the Python-3.10-drop commit (`af907bbe`), which removed the equivalent fallback in `config.py` but missed `__init__.py`. | None functionally (the dead branch is simply unreachable, not wrong); misleading to future readers/contributors. | Remove the `except ModuleNotFoundError` branch and simplify `_parse_toml`/`_pyproject_version` to assume `tomllib` is always present; update or remove the stale 3.10 comments in both files. | Small |

## Limitations

- `pip-audit` is not installed and was not installed, per the read-only/no-install constraint; vulnerability
  coverage of the full resolved tree (beyond what Dependabot's `pip`-ecosystem alert history already covers)
  relies on the existing `dependency-audit.yml` CI workflow, which this audit did not and could not execute.
- No per-package SPDX licence re-verification was performed (e.g. via `pip-licenses`); the licence
  compatibility assessment in this report is a plausibility check against general knowledge of well-known
  package licences, not a verified scan.
- Upstream `tree-sitter-language-pack` maintenance-status claims (last 0.x release, current org ownership,
  1.x network-fetch behavior) are based on live web search, not on anything checkable fully offline from
  this repo's own files — stated as "verified online" rather than "verified offline" throughout, per the
  task's own distinction.
- Dependabot's "automated security fixes" setting itself (the repo-level toggle, distinct from alerts and
  from `dependabot.yml`) was not and could not be directly inspected via the tools available in this
  session; only its observable outcome (37/37 alerts `fixed`) was checked.
- No attempt was made to verify whether `npm/` or `editors/vscode/` have ever had a Dependabot or other
  automated scan run against them historically (there is no ecosystem entry for either, so no alert history
  would exist to check even if the API were queried).
