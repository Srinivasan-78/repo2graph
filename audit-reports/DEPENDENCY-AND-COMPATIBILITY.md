# Dependency, Licensing and Compatibility Assessment

Consolidated from `AGENT-FINDINGS/10-dependencies.md` and the compatibility half
of `AGENT-FINDINGS/11-config-compat-ux.md`, with orchestrator verification.

## Verdict

**The dependency posture is excellent and the licensing position is clean.** Two
runtime dependencies, both genuinely used; 37 historical Dependabot alerts all
verified fixed with zero open; MIT consistent across LICENSE, LICENSES/,
CITATION.cff and pyproject; no dependency blocking Python 3.14.

Two findings deserve attention: the deliberate `tree-sitter-language-pack` cap now
carries unpatchable security debt, and Dependabot covers only half the
repository's package ecosystems.

## Dependency footprint

| Property | Value |
|---|---|
| Runtime dependencies | **2** (`tree-sitter>=0.23`, `tree-sitter-language-pack>=0.7,<1.0`) |
| Both actually imported | **Yes**, verified by grep |
| Extras | `dev`, `rag`, `mcp`, `model2vec` |
| Declared-but-unimported | `numpy` only — **deliberate and documented** (consumer-side declaration so `vectors.npy` is `numpy.load`-able; `embed.py` writes NPY by hand with `struct` specifically to avoid needing it at runtime) |
| Open Dependabot alerts | **0** of 37 historical, all verified `fixed` via live API |
| npm audit (VS Code lockfile) | **0 vulnerabilities** |
| Licence consistency | MIT across all surfaces — consistent |

A two-dependency runtime for a 32k-line static-analysis tool is the structural
foundation of the project's offline, reproducible and read-only-container
guarantees. It should be defended.

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| DEP-01 | **MEDIUM** | High | `pyproject.toml:33-44` | The `<1.0` language-pack cap pins to an apparently frozen branch — correct decision, but it now carries unpatchable-in-range security debt |
| DEP-02 | **MEDIUM** | Confirmed | `.github/dependabot.yml` | Only `pip` + `github-actions` covered; **npm (2 manifests) and docker are uncovered** |
| COMPAT-01 | **MEDIUM** | Confirmed (orchestrator-verified) | `repo2graph/doctor.py:89-96` | Tuple-length comparison bug makes `doctor` falsely warn on nearly every Python 3.13 install — and its support matrix is itself stale |
| DEP-03 | LOW | Confirmed | `npm/package.json` | npm launcher's version is cosmetic; `uvx --from "repo2graph[mcp]"` has no version pin, so it always runs PyPI-latest |
| DEP-04 | LOW | Confirmed | `Dockerfile` | Base image is tag-pinned, not digest-pinned |
| COMPAT-02 | LOW | Confirmed | `repo2graph/__init__.py:20-44`, `scripts/bump_version.py:24-29`, `scripts/check_version.py:25-30` | Dead `tomli` fallback and stale "supports 3.10" docstrings survive the 3.10 drop |

### DEP-01 — The language-pack cap: correct, and now carrying debt

The `tree-sitter-language-pack>=0.7,<1.0` cap is a **security boundary**, not
version lag, and it is documented at length in `pyproject.toml`. 1.x ships a
2.5MB loader that fetches ~21MB of unsigned native grammar code from the network
on first `get_parser()` — into the process holding the user's source and, in CI,
their tokens. That would break the no-network guarantee, make the lockfile and
SBOM stop describing what actually executes, and render the read-only container
and egress-restricted CI unworkable. 1.x exposes no offline switch.

**The cap should not be raised**, and this audit does not recommend raising it.
The rationale was verified and holds.

The finding is the *residual*: 0.13.0 appears to be the last pre-1.0 release
before upstream's Rust-core rewrite, so the pinned line looks frozen. Any future
vulnerability in a bundled grammar cannot be fixed by a version bump — there is
nowhere to bump to. The project is therefore carrying a security dependency it
cannot patch, in exchange for a security guarantee it values more. That is a
defensible trade, but it should be a *tracked* one rather than an implicit one.

**Recommendation:** record this explicitly as accepted risk in `SECURITY.md`, and
scope the vendoring path (ship grammars as repo assets, build them in CI) as the
eventual exit. Vendoring is credible but multi-week — **L**. Also verify
periodically whether upstream backports security fixes to 0.x.

Positively: the Dependabot ignore rule correctly blocks major bumps of this
package, so the cap is enforced by process and not just by the constraint string.

### DEP-02 — Half the repository's ecosystems are unwatched

`.github/dependabot.yml` configures `pip` and `github-actions`. The repository
also contains:

- `npm/package.json` — the published MCP launcher
- `editors/vscode/package.json` + `package-lock.json` — a real lockfile with a
  real dev-dependency tree
- `Dockerfile` — a base image

None of these receives automated vulnerability or update PRs. `npm audit` on the
VS Code lockfile is currently clean, so there is no live vulnerability — but
nothing is watching it, and the VS Code extension's build tooling is exactly the
kind of dependency tree that accumulates advisories quietly.

**Recommendation:** add `npm` (both directories) and `docker` ecosystems to
`dependabot.yml`. Effort: **S**. This is the cheapest risk reduction in this
report.

### COMPAT-01 — `doctor`'s Python check is broken, twice over

**Orchestrator-verified.** `repo2graph/doctor.py:89` reads
`if (3, 11) <= vi <= (3, 13):` where `vi = sys.version_info`, a **5-tuple**.

```
version_info = (3, 13, 15, 'final', 0)
vi <= (3,13)     -> False
vi[:2] <= (3,13) -> True
```

Tuple comparison is lexicographic, so a 5-tuple is never `<=` a 2-tuple with
equal leading elements. On Python 3.13.15 — this machine, a fully supported
version — `doctor` falls through to the `vi > (3, 13)` branch and reports:

> `Python 3.13.15 is newer than tested support matrix (<= 3.13)`

Every 3.13 patch release except exactly 3.13.0 gets a false warning. Every 3.12.x
and 3.11.x install likewise fails the `ok` branch.

**There is a second defect in the same function**, which the specialist did not
report: the support matrix is stale. The docstring says "tested <= 3.13" and the
remediation text warns that "Python 3.14+ may encounter grammar wheel
compatibility issues" — but CI tests 3.14 across all three OSes and
`pyproject.toml` advertises a 3.14 classifier. So a user on 3.14, a fully
supported and CI-tested version, is told by the project's own diagnostic tool
that their Python may be incompatible.

This matters out of proportion to its size: `doctor` exists to build confidence
during onboarding, and it currently misfires for the large majority of users.
Same root cause family as DOC-01 — the 3.10-drop commit swept CI and README but
missed `SECURITY.md` and `doctor.py`.

**Recommendation:** use `vi[:2]` and update the matrix to 3.11–3.14. Effort:
**S** (one line plus the strings). Add a test asserting `doctor` reports `ok` on
the running interpreter — which would have caught this immediately.

### COMPAT-02 — Dead 3.10-era code

`tomllib` is stdlib from 3.11, and `requires-python` is now `>=3.11`, so the
`tomli` fallback branches in `repo2graph/__init__.py:20-44`,
`scripts/bump_version.py:24-29` and `scripts/check_version.py:25-30` are
unreachable. Accompanying docstrings still claim 3.10 support.

Low severity — dead code, not broken code — but it is unreachable-by-construction
code that will never be exercised and will mislead the next reader. Effort: **S**.

### DEP-03 — The npm launcher's version means nothing

`npm/package.json` carries a version, but the launcher invokes
`uvx --from "repo2graph[mcp]"` with **no version pin**, so it always runs whatever
is latest on PyPI. A user who deliberately installs `repo2graph-mcp@4.1.0` from
npm gets whatever PyPI currently serves. This is undocumented.

Combined with CI-02 in `CI-CD-ASSESSMENT.md` (npm publish silently no-ops without
`NPM_TOKEN`), the npm channel's version semantics are doubly unreliable.

**Recommendation:** either pin the Python package version in the launcher to
match, or document explicitly that the npm version is a launcher version and
tracks PyPI-latest. Effort: **S**.

## Platform compatibility — verified on Windows

This audit ran on Windows 11 / Python 3.13.15, which is the platform least
covered by typical CI attention. The compatibility specialist exercised the
Windows-specific paths live and found them **already correctly handled**:

| Concern | Result |
|---|---|
| File locking | Correct via `msvcrt` |
| Worker memory capping | Correct via Job Objects |
| `SIGKILL` absence | Correct fallback |
| stdout encoding on redirected/cp1252 streams | Correct UTF-8 reconfiguration |
| BOM, CRLF, unicode filenames | Handled |
| Output path separators | POSIX-normalised consistently |

This codebase has clearly been through prior hardening passes for exactly these
issues, and a dedicated required CI check
(`Windows CP1252 Non-UTF8 Pipeline Compatibility`) guards the encoding half.
COMPAT-01 is the one Windows-adjacent defect found, and it is platform-independent.

## Licensing

- MIT, consistent across `LICENSE`, `LICENSES/MIT.txt`, `CITATION.cff` and
  `pyproject.toml`'s `license` / `license-files`.
- REUSE compliant. The `.authormark.json` ignore entries for bot-generated config
  and `.github` are deliberate and were **not** counted as violations.
- No dependency licence incompatible with MIT was identified.

## Limitations

- **`pip-audit` is not installed** and installing was out of scope, so Python CVE
  checking relied on the GitHub Dependabot API plus manual version inspection of
  `uv.lock`. A `pip-audit` run in CI is recommended regardless.
- Per-package licence text was not re-scanned for all transitive dependencies;
  the assessment is based on declared metadata.
- Upstream maintenance status for `tree-sitter-language-pack` 0.x was established
  from online release information, not offline — treat DEP-01's "frozen branch"
  characterisation as High confidence rather than Confirmed.
- This repository has a **documented history of Dependabot alerts lagging behind
  a clean dependency graph**. "0 open alerts" reflects the API's current state;
  re-check after every lockfile bump rather than treating it as settled.
