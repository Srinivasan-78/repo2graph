# Production Readiness Assessment

## Which codebase is being scored

This matters, because three versions are live at once.

The scores below assess the **committed / released code** (`HEAD` = `f43b1369`,
whose content ships as v5.0.0). The **uncommitted working tree is not scored as
the product**, because it has never been released — but it is treated as a
**release blocker**, and that is reflected in the CI/CD and Testing scores rather
than spread across every dimension.

If the working tree were committed as-is, Reliability would fall to **4** and
Testing to **4**, and the project would not be releasable at all.

## Scores

| Dimension | Score | Justification |
|---|---|---|
| **Architecture** | **7** / 10 | One-way pipeline, no genuine circular imports, a clean storage abstraction, and `docs/architecture.md` that matches reality. Held back by one layering inversion (`tasks.py` → `mcp.tools`) and language dispatch scattered across 6+ modules with no adapter point, which makes the main growth axis expensive. |
| **Code quality** | **7** / 10 | `ruff` clean, `ruff format` clean on 280 files, **strict mypy clean on 60 files**, zero TODO/FIXME, no duplicated logic, accurate prose comments, disciplined resource handling. Held back squarely by function size on the hottest paths: `graph.build()` at 792 lines/~155 branches, `pack_context()` at 384 lines on the citation-critical path, plus ~10 more 160–350 line functions and no complexity gate to stop recurrence. |
| **Security** | **9** / 10 | No exploitable CRITICAL or HIGH findings. Sensitive call sites carry adversarial-review comments naming the specific attack defended. Every security claim tested held: no-network verified, grammar-download boundary enforced, MCP path confinement intact, actions SHA-pinned, zizmor suppressions narrowly justified. Five LOW/INFO hardening items. Not a 10 only because `bandit`/`pip-audit`/`zizmor` are absent from this environment **and from CI**, so the review was manual. |
| **Testing** | **7** / 10 | 2,782 tests with genuinely impressive breadth — MCP protocol conformance against an official client, property-based, fuzz, determinism, prompt-isolation, packaging-drift and golden-file tests with opt-in regeneration. A `fail_under = 80` branch gate exists. Held back by: the gate running on **1 of 12 CI cells** (no Windows coverage at all), **no on-disk format backward-compatibility test** despite five majors in two months, and concurrency tests that assert absence-of-crash rather than correctness-of-result — the exact blind spot BUG-01 exploits. |
| **Documentation** | **8** / 10 | Zero broken links across 45 markdown files. API signatures, CLI tables, action inputs/outputs and MCP tool names verified byte-accurate against source. README benchmark numbers independently recomputed and matched exactly. Held back by staleness that a version bump should have swept — `SECURITY.md`'s Python matrix, README's "3.x CLI" semver line, an incomplete network-exception list, and three undocumented env vars including a security bypass. |
| **Performance** | **7** / 10 | Real benchmark infrastructure with committed baselines and two required CI regression gates. Resource bounding applied consistently and deliberately (co-change caps, viz output ceiling with shrink loop, parse budgets, worker memory caps). `neighbours()` uses one indexed query, not N+1. Held back by each file being read ~3× per build (measured: 15,043 `open()` calls for 3,000 files) and a ~120ms cold-start import tax that the MCP server and editor extension pay every time. |
| **Reliability** | **7** / 10 | Error handling is deliberate throughout — every broad `except` sampled is a commented resilience boundary, none swallowing errors into wrong results. Degradation paths (stale cache rebuild, parse-error tolerance, partial results) are designed rather than accidental. Held back by the SQLite store's history: **five issues in six weeks** in one module, all concurrency or lifecycle, indicating an under-specified concurrency model. |
| **CI/CD** | **6** / 10 | Strong implementation — 15 workflows, a 12-cell matrix, SHA-pinned actions, test-before-tag, signed release commits via GraphQL, version derived rather than hand-bumped, and a clean run history (2 failures in ~90 runs, both legitimate catches). But the **configuration around it undercuts it**: required checks omit all three Python-3.14 cells and the container job, admin bypass is permanently `always`, npm publish silently no-ops, `provenance.yml` produces no provenance, and the default branch is 240 commits behind the release. The lowest score, and the one with the cheapest fixes. |
| **Dependencies** | **8** / 10 | **Two** runtime dependencies for a 32k-line static-analysis tool, both genuinely imported. 37/37 historical Dependabot alerts fixed, 0 open. Clean MIT licensing, REUSE compliant. No dependency blocks Python 3.14. Held back by Dependabot covering only half the repository's ecosystems (npm ×2 and docker unwatched) and the language-pack cap now pinning to an apparently frozen branch — correct decision, unpatchable residual. |
| **Developer experience** | **8** / 10 | Make targets that faithfully predict CI, pre-commit consistent with CI, a 66-second parallel suite, `doctor`/`bug-report`/`demo`/`explain` affordances, clear contribution docs, and accurate architecture documentation. Held back by the `doctor` defect, and by a contributor who clones the default branch getting 2.2.0. |
| **User experience** | **7** / 10 | `demo` is a genuinely good first-run affordance. Config precedence (file < pyproject < CLI) was tested directly and works as documented; malformed TOML, wrong types and unknown keys all produce clear, actionable, non-traceback errors — tested, not assumed. Windows behaviour (locking, memory caps, stdout encoding, BOM/CRLF/unicode) verified correct live. Held back by `doctor` emitting a false incompatibility warning to most users and three undocumented env vars. |
| **Production readiness** | **7** / 10 | Operationally mature for its documented use cases, with named gaps below. |

### Overall: **7.3 / 10**

Unweighted mean of the twelve dimensions. The spread is narrow (6–9), which is
itself informative: this is not a project with one catastrophic weakness and
otherwise polish, nor the reverse. It is consistently solid, with release
process as the weakest link and security as the strongest.

## Maturity classification

> **Functional open-source project, trending production-ready.**
> **Not enterprise-ready.**

### Why not "Production-ready"

Three reasons, none about code quality:

1. **Release hygiene.** The default branch is three major versions and 240
   commits behind the published release. A production-ready project's default
   branch reflects what it ships.
2. **Version churn.** Five major versions in under two months (v1.2.0 → v5.0.0).
   Semantic versioning is being followed honestly — these are real breaking
   changes — but a consumer cannot plan against this cadence, and README still
   describes the compatibility promise in terms of "the 3.x CLI".
3. **The merge gate does not enforce what CI tests.** Python 3.14 is advertised
   in classifiers, tested in CI, and cannot block a merge. Admin bypass is
   permanently enabled, which is the exact mechanism that previously tagged a
   release on failing tests.

### Why not "Early development" either

The engineering practice here is well above that line, and it should be said
plainly: strict mypy clean across 60 files, 2,782 tests, SHA-pinned actions with
per-line justified suppressions, REUSE compliance, a benchmark corpus with
published methodology and independently reproducible numbers, SBOM and lockfile
enforcement, adversarial security comments at sensitive call sites, and a
documented security boundary maintained against an upgrade that would be easier
to accept. Much of this exceeds what commercially-maintained projects do.

### Why not "Enterprise-ready"

Beyond the three items above: zero external issue reports means no independent
validation of fitness; `bandit`/`pip-audit`/`zizmor` are not in CI; there is no
real build provenance despite a workflow named for it; and there is no
backward-compatibility guarantee or test for the on-disk index format, which an
enterprise consumer caching indexes across upgrades would require.

## Conditions to reach the next level

### → Production-ready

| # | Condition | Finding | Effort |
|---|---|---|---|
| 1 | Restore the `516fe12d` hunks; never commit the current working tree | BUG-01…BUG-05 | S |
| 2 | Make the default branch reflect the released state (fast-forward `main`, or make `develop` default) | REL-01 | S + decision |
| 3 | Add the three Python-3.14 cells and the container job to required checks; stop permanent admin bypass | CI-01 | S |
| 4 | Make concurrency tests assert result correctness, not absence of exceptions | TEST-05 | S–M |
| 5 | Add an on-disk format backward-compatibility test | TEST-02 | M |
| 6 | Fix `doctor`'s version check and support matrix | COMPAT-01 | S |
| 7 | Correct `SECURITY.md`'s CI matrix claim and complete README's network-exception list | DOC-01, DOC-02 | S |
| 8 | Extend Dependabot to npm and docker | DEP-02 | S |

Seven of the eight are **S** effort. This is a close milestone, not a distant one.

### → Enterprise-ready

| # | Condition | Finding | Effort |
|---|---|---|---|
| 1 | All production-ready conditions above | — | — |
| 2 | Real build provenance / attestation on published artifacts | CI-03 | M |
| 3 | `bandit` + `pip-audit` (+ `zizmor`) running in CI as required checks | SECURITY limitations, CI-05 | S |
| 4 | Coverage gate on more than one cell, including a Windows cell | TEST-04 | S |
| 5 | A stated API/CLI/index-format stability policy and a slower major cadence | DOC-03, release churn | Process |
| 6 | Record the language-pack cap as accepted, tracked, unpatchable risk; scope grammar vendoring | DEP-01 | S to document, L to execute |
| 7 | Reduce `graph.build()` and `pack_context()` to independently testable units; add a complexity gate | CQ-01, CQ-02, CQ-06 | L |
| 8 | Some external validation — users, contributors, or independent review | — | Time |

## What is genuinely strong

An audit that only enumerates deficits misrepresents this repository. These were
each verified, not assumed:

- **Security is the best-executed dimension** — not merely free of findings, but
  visibly *designed* against named attacks, with its own claims holding under test.
- **A two-dependency runtime** is the structural basis of the offline,
  reproducible and read-only-container guarantees, and it is defended
  deliberately — including by refusing a dependency upgrade that would be easier
  to take.
- **Documentation accuracy is measurable and measured.** Benchmark numbers
  recompute. Zero broken links. The project tests its own docs for drift.
- **Windows is a first-class platform**, verified live rather than claimed.
- **The release pipeline's response to past incidents was substantive** —
  test-before-tag and re-checking required checks before tagging directly
  address a real prior failure.

The gap between this project's engineering practice and its release hygiene is
the single most actionable observation in this audit: the hard part is already
done well, and the cheap part is what is holding the score down.
