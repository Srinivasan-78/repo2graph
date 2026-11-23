# Audit Reports

Comprehensive engineering audit of `repo2graph`, conducted 2026-11-01 against
branch `develop` at HEAD `f43b1369`, on Windows 11 / Python 3.13.15.

**Start here:** [`EXECUTIVE-SUMMARY.md`](EXECUTIVE-SUMMARY.md)

## Headline

| | |
|---|---|
| **Overall health** | **7.3 / 10** |
| **Maturity** | Functional open-source project, trending production-ready. Not enterprise-ready. |
| **Findings** | **50** — 1 CRITICAL, 7 HIGH, 17 MEDIUM, 22 LOW, 3 INFO (+1 rejected, 3 deduplicated) |
| **Test suite** | 1 failed, 2763 passed, 18 skipped, 1 xfailed (66.02s) |
| **Lint / format / types** | All clean (`ruff`, `ruff format`, strict `mypy` on 60 files) |
| **Most urgent** | A dirty working tree reverts fixes released in v5.0.0, and the test that would catch it was disabled |

## Reading order

Depending on why you're here:

**If you are about to commit** → [`BUGS-AND-DEFECTS.md`](BUGS-AND-DEFECTS.md),
then P0-1 in [`RECOMMENDED-ACTIONS.md`](RECOMMENDED-ACTIONS.md). Nothing else
matters until that is resolved.

**If you want the whole picture** →
[`EXECUTIVE-SUMMARY.md`](EXECUTIVE-SUMMARY.md) →
[`FINDINGS-REGISTER.md`](FINDINGS-REGISTER.md) →
[`RECOMMENDED-ACTIONS.md`](RECOMMENDED-ACTIONS.md).

**If you are planning work** → [`RECOMMENDED-ACTIONS.md`](RECOMMENDED-ACTIONS.md)
(P0–P3, with effort estimates and a "what not to do" section) and
[`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md) (conditions to reach the
next maturity level).

**If you are verifying this audit's claims** →
[`TEST-EXECUTION-LOG.md`](TEST-EXECUTION-LOG.md) has every command, exit code and
blocked check, and [`AGENT-FINDINGS/`](AGENT-FINDINGS/) has the raw specialist
reports behind each consolidated one.

## Contents

| Report | Contents |
|---|---|
| [`EXECUTIVE-SUMMARY.md`](EXECUTIVE-SUMMARY.md) | Health score, maturity, top findings, risks, recommendations |
| [`REPOSITORY-OVERVIEW.md`](REPOSITORY-OVERVIEW.md) | What the project is, architecture diagram, scale, release topology |
| [`FINDINGS-REGISTER.md`](FINDINGS-REGISTER.md) | All 50 findings in one table, with dedupes and the rejected finding |
| [`BUGS-AND-DEFECTS.md`](BUGS-AND-DEFECTS.md) | The working-tree regression in full, with three reproductions |
| [`ARCHITECTURE-ASSESSMENT.md`](ARCHITECTURE-ASSESSMENT.md) | Layering, coupling, circular imports, extensibility |
| [`CODE-QUALITY-REPORT.md`](CODE-QUALITY-REPORT.md) | Complexity, maintainability, tooling baseline |
| [`SECURITY-ASSESSMENT.md`](SECURITY-ASSESSMENT.md) | Trust boundaries, findings, security claims verified |
| [`DOCUMENTATION-AUDIT.md`](DOCUMENTATION-AUDIT.md) | Doc-vs-implementation, links, claim verification |
| [`GITHUB-ISSUE-ANALYSIS.md`](GITHUB-ISSUE-ANALYSIS.md) | 272 issues, regression verdict, 16 recommended issues (not filed) |
| [`TESTING-AND-COVERAGE.md`](TESTING-AND-COVERAGE.md) | Suite composition, coverage, scenario gaps |
| [`CI-CD-ASSESSMENT.md`](CI-CD-ASSESSMENT.md) | Pipeline map, merge gating, release engineering |
| [`PERFORMANCE-ASSESSMENT.md`](PERFORMANCE-ASSESSMENT.md) | Measured results kept separate from theory |
| [`DEPENDENCY-AND-COMPATIBILITY.md`](DEPENDENCY-AND-COMPATIBILITY.md) | Dependencies, licensing, platform support |
| [`PRODUCTION-READINESS.md`](PRODUCTION-READINESS.md) | Twelve scored dimensions with justification |
| [`RECOMMENDED-ACTIONS.md`](RECOMMENDED-ACTIONS.md) | P0–P3 roadmap and explicit non-recommendations |
| [`TEST-EXECUTION-LOG.md`](TEST-EXECUTION-LOG.md) | Every command executed; blocked checks named |
| [`AGENT-FINDINGS/`](AGENT-FINDINGS/) | The 11 raw specialist reports |

## Method

Eleven specialist subagents ran in parallel, each scoped to one assessment area
and constrained read-only. A lead orchestrator executed the test suite and static
analysis centrally, independently verified every CRITICAL and HIGH finding,
deduplicated overlaps, adjudicated severity, and rejected one unsupported claim.

| Agent | Area | Raw report |
|---|---|---|
| 1 | Architecture & structure | `AGENT-FINDINGS/01-architecture.md` |
| 2 | Code quality & maintainability | `AGENT-FINDINGS/02-code-quality.md` |
| 3 | Bug detection & regressions | `AGENT-FINDINGS/03-bugs.md` |
| 4 | Security & vulnerabilities | `AGENT-FINDINGS/04-security.md` |
| 5 | Performance & scalability | `AGENT-FINDINGS/05-performance.md` |
| 6 | Documentation accuracy | `AGENT-FINDINGS/06-documentation.md` |
| 7 | GitHub issues, PRs, advisories | `AGENT-FINDINGS/07-github-issues.md` |
| 8 | Testing strategy & coverage | `AGENT-FINDINGS/08-testing.md` |
| 9 | CI/CD & release engineering | `AGENT-FINDINGS/09-cicd.md` |
| 10 | Dependencies & supply chain | `AGENT-FINDINGS/10-dependencies.md` |
| 11 | Config, compatibility, CLI/UX/DX | `AGENT-FINDINGS/11-config-compat-ux.md` |

### Epistemic rules applied

- **Confirmed** means reproduced by executing code. **Verified** means checked
  against source or API by the orchestrator. **Reported** means accepted on a
  specialist's evidence without independent re-execution. Every finding carries
  one of these.
- No test result is estimated and no coverage percentage is claimed that was not
  measured. Checks that could not run are named, with the reason, in
  `TEST-EXECUTION-LOG.md`.
- Measured performance findings are kept strictly separate from theoretical ones.
- Severity was not inflated. Issues requiring the attacker to already control the
  machine are rated LOW or INFO and say so.
- One specialist's HIGH finding was **rejected** because its premise was false,
  and one specialist's reasoning was **experimentally refuted**. Both corrections
  are documented rather than quietly dropped.

## What was not modified

- **No production code.** The four pre-existing uncommitted modifications were
  left exactly as found — analysed, never corrected.
- **No tests.** The one pre-existing test modification is reported as a finding.
- **No GitHub state.** No issue, PR, label, release or setting was created,
  closed or changed; no workflow was triggered.
- **No git history.** Nothing committed, pushed, stashed or checked out.
- **No formatters were run**, deliberately: this repository has a recorded
  incident in which a whole-tree `ruff format` silently reverted a fix while all
  checks stayed green.

Temporary artifacts (reproduction scripts, benchmark output, coverage subsets)
were written to the session scratchpad outside the repository. The only files
added here are these reports.

## Limitations

- **Single Windows host.** Linux and macOS behaviour is inspection-based.
  Absolute performance numbers do not transfer — see PERF-03.
- **`bandit`, `pip-audit` and `zizmor` are not installed** and installing was out
  of scope, so security and CVE review were manual.
- **Committed `HEAD` was never tested in isolation**, because stashing or
  checking out would have modified the working tree.
- **Full-suite coverage was not re-measured**; only a labelled subset, reported as
  methodology rather than as a project figure.
- **No workflow was executed**; CI assessment used YAML plus read-only run history.
- `CHANGELOG.md` (226 KB) was spot-checked, not read end to end; 272 issue bodies
  were sampled, not all read in full.
