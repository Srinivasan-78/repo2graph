# Production readiness

Evaluated 2026-09-17, against the findings in `docs/SECURITY-AUDIT.md`. Classifications are
**Ready**, **Ready with controls**, **Requires remediation**, or **Not suitable** — no numeric
scores (a score implies false precision this audit doesn't have).

## Security

**Ready with controls.**

No P0 (critical security boundary) findings. One P1-severity claim from the audit was verified and
downgraded to P3 after checking GitHub's actual platform behavior (see `docs/SECURITY-AUDIT.md`).
Two genuine P2 gaps were found and fixed in this pass (audit-log redaction of the `error` field,
MCP string-argument length ceilings); the remainder are P3 hardening items, tracked in
`docs/BACKLOG.md`, none of which block a supervised deployment.

*Evidence:* `docs/SECURITY-AUDIT.md`'s findings section, each with `file:line` citations and — for
every "already handled" claim — a pointer to the test that proves it, not just the code that claims
it.

*Remaining risk:* the P3 items listed in the audit (TOCTOU symlink race requiring local code
execution to exploit, no hard cap on total graph nodes/edges, secret-path denylist not
user-configurable, no SBOM). None of these is exploitable by repository
content alone or by a remote, unprivileged MCP caller. Per-file parse time is now
bounded (`PARSE_TIMEOUT_MICROS`; see `docs/SECURITY-AUDIT.md` P2.3).

*Control:* run with `--no-auto-build` and a read-only repository mount in shared deployments (see
`docs/ENTERPRISE_DEPLOYMENT.md`); enable `--audit-log` where a record of tool calls matters.

## Privacy

**Ready.**

No network call in default mode, verified by a real socket-level test
(`tests/test_rag_path.py`, `tests/test_http_transport.py`), not just by reading the code. The two
opt-in network paths (`--answer`, `--auth-oidc-issuer`) are both gated behind explicit flags and
disclose their destination before sending anything.

*Evidence:* `docs/PRIVACY.md`, backed by the same test suite referenced there.

*Remaining risk:* none identified beyond what's already documented as inherent to the design
(secret exclusion is path/shape-based, not exhaustive content scanning — see
`docs/ENTERPRISE_DEPLOYMENT.md`'s "what this does not do").

## Reliability

**Ready with controls.**

A single malformed file cannot abort a build (`graph.py:203-209`, tested). Cache corruption,
concurrent audit-log writers, and Windows-specific encoding failures all have dedicated regression
tests (`tests/test_encoding.py` — 204 tests across a 5×7×5 matrix; `tests/test_audit.py`'s
file-locking tests). 838 tests pass on this branch as of this audit.

*Remaining risk:* no enforced ceiling on total nodes/edges for an extremely large/adversarial
repository (P3.5) — memory usage is unbounded by construction for that one dimension. A
pathological file can no longer pin a worker indefinitely: `parse_source` applies
`PARSE_TIMEOUT_MICROS` (5s) around every `parser.parse`.

*Control:* for untrusted or unusually large repositories, run the indexer with an external resource
limit (container memory/CPU limits, `ulimit`, or a Kubernetes pod resource request/limit) rather
than relying on an internal ceiling that doesn't yet exist.

## Performance

**Ready with controls, at measured scale; unverified beyond it.**

Real measurements exist at 90 files (this repo) and 3,000 files (synthetic): full builds in 4.3s
and 18.6s respectively, incremental rebuilds ~3x faster than a full rebuild, sub-1.2s query/rag
latency against a warm 3,000-file index. See `docs/PERFORMANCE.md` for the exact numbers and how to
reproduce them.

*Remaining risk:* nothing in this audit measured 50k/100k/500k-file repositories, and
`docs/PERFORMANCE.md` explicitly declines to extrapolate linearly — the resolution phase's cost is
not obviously linear in file count. `docs/BACKLOG.md` already tracked (before this audit) that no
test fixture crosses `PARALLEL_MIN_FILES` (64), so the process-pool code path has thin coverage.

*Control:* measure your own first data point at your actual repository's scale before treating this
tool as validated for it; see `docs/PERFORMANCE.md`'s recommendations.

## Maintainability

**Ready.**

Two prior whole-repository hardening runs are documented in `DONE.md` and `docs/BACKLOG.md` with
what shipped, what was deliberately deferred and why, and what regressed and got caught. `AGENTS.md`
encodes five recurring bug classes as standing repo rules specifically so they don't recur a third
time. `ruff` is clean; `mypy --strict` is clean on the modules not explicitly relaxed by name (a
deliberate allowlist-in-reverse, not a blanket exclusion).

*Remaining risk:* `docs/BACKLOG.md` item 2 (whole-repo `mypy --strict`, 324 errors across 10 legacy
modules) is real, tracked, and explicitly not attempted in a single mechanical sweep because of the
regression risk that would carry.

## Supply chain

**Ready.**

Every third-party GitHub Action pinned to a full commit SHA across all 10 workflows (verified, not
assumed, in this audit's CI/CD pass). PyPI publishing uses OIDC Trusted Publishing — no long-lived
token exists to leak. `pip-audit --strict` genuinely fails CI on a finding. `uv.lock` is committed.
REUSE/SPDX license compliance is a required CI status check, not a suggestion.

*Remaining risk:* no SBOM artifact is generated (P2.4 in the audit) — `pip-audit` output is a
vulnerability report, not a bill of materials a downstream consumer can ingest independently. The
`@v1` release tag is a moving convenience pointer, not an integrity pin (P3.7) — now documented in
`SECURITY.md`.

*Control:* enterprise consumers of the GitHub Action should pin to a commit SHA, not `@v1`, exactly
as this repo's own workflows pin their third-party dependencies.

## Deployment

**Ready with controls.**

Three surfaces (CLI, GitHub Action, MCP server) share one engine and one on-disk format, so there
is exactly one retrieval implementation to reason about regardless of which surface is deployed.
The HTTP transport refuses an unsafe bind at startup rather than at request time. Container
hardening guidance, with flags specific to what this codebase actually needs (not generic
boilerplate), is in `docs/ENTERPRISE_DEPLOYMENT.md`.

*Remaining risk:* no SSRF protection or destination allowlisting on the two opt-in network paths
beyond https-only + size/timeout caps — this is a network-layer control the deploying organization
must add if its threat model requires it (documented explicitly, not silently assumed away).

## Observability

**Ready.**

Structured audit logging (one JSON line per tool call) with shape-based redaction proven by tests
in both directions (secrets are redacted; ordinary arguments are not mangled). `repo_cache_stats`
and `repo_build_status` MCP tools expose operational counters without repository content.

*Remaining risk:* none identified in this audit beyond the now-fixed `error`-field redaction gap.

## Documentation

**Ready.**

`README.md`, `TECHNICAL.md`, `SECURITY.md`, `docs/mcp.md`, `docs/cli.md`, `docs/reference.md`,
`docs/python-api.md`, and — new in this audit — `docs/SECURITY-AUDIT.md`, `docs/PRIVACY.md`,
`docs/ENTERPRISE_DEPLOYMENT.md`, and `docs/PERFORMANCE.md` cover architecture, security posture,
privacy, deployment, and measured performance with evidence rather than assertion.

## Testing

**Ready.**

838 tests passing (up from 825 at the last recorded baseline in `DONE.md`, plus the regression
tests added by this audit). Negative security tests exist for path traversal, secret redaction (in
both directions), auth rejection (`alg:none`, forged signatures, expired/wrong-audience tokens),
and XSS in generated HTML — not only "valid input works" tests. `docs/BACKLOG.md` transparently
tracks the one known coverage gap this audit did not close: no test crosses `PARALLEL_MIN_FILES`
(64 files), so the process-pool build path's behavior at real scale is unverified by the suite,
though this audit's own 3,000-file benchmark exercised it manually (see `docs/PERFORMANCE.md`).

---

## Summary

| Area | Status |
|---|---|
| Security | Ready with controls |
| Privacy | Ready |
| Reliability | Ready with controls |
| Performance | Ready with controls, at measured scale; unverified beyond it |
| Maintainability | Ready |
| Supply chain | Ready |
| Deployment | Ready with controls |
| Observability | Ready |
| Documentation | Ready |
| Testing | Ready |

No P0 findings, and none of the P2/P3 findings in `docs/SECURITY-AUDIT.md` are exploitable by
repository content alone or by an unprivileged remote MCP caller. This is not a claim of formal
certification, and specific controls (network egress scoping, container hardening, version
pinning) are the deploying organization's responsibility, documented explicitly in
`docs/ENTERPRISE_DEPLOYMENT.md` rather than assumed.
