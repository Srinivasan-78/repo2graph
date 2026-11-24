# Agent 4 — Security & Vulnerability Assessment

Scope: `repo2graph/security.py`, `repo2graph/mcp/*`, `fetch.py`, `discovery.py`,
`exclusions.py`, `export.py`, `viz.py`, `answer.py`, `neo4j.py`, `store.py`,
`config.py`, `.github/workflows/*`, `.github/scripts/*`. Read-only review; no
file was modified, no test suite run, no external network activity performed.

## Summary & risk posture

repo2graph is, on this read, an unusually mature codebase from a security
engineering standpoint. The maintainer has clearly already run multiple
rounds of adversarial self-review: nearly every sensitive call site carries a
comment naming the specific attack it defends against (argv injection, TOCTOU,
DNS rebinding, redirect-based SSRF, timing side channels, CRLF/control-char
smuggling, Cypher/GraphML/XML injection, prompt injection). I found **no
exploitable CRITICAL or HIGH-severity vulnerability** in the areas reviewed.
Findings below are MEDIUM/LOW hardening gaps, a few design observations worth
tracking, and one cross-reference item for the dependency-auditing agent.

Overall posture: **good**. The secret scanner, MCP path confinement, git
invocation hardening, SSRF guard in `answer.py`, and GitHub Actions trigger
hygiene (`prod-igy.yml`, `zizmor.yml`) are all built to a standard well above
typical open-source norms for a tool with this threat surface.

## Trust boundaries

1. **Untrusted source trees being indexed** (`discovery.py`, `parse.py`,
   `exclusions.py`) — arbitrary file names, symlinks, content, encodings.
2. **Untrusted repo URL/ref input** (`fetch.py`) — a caller-supplied
   `owner/repo`, URL, or ref string reaching `git clone/fetch/checkout` argv.
3. **MCP tool arguments from an LLM caller** (`repo2graph/mcp/*`) — numeric
   and string parameters, resource URIs, node/chunk ids, paths in
   `repo_read`.
4. **Config files inside the target repo** (`config.py`, TOML via
   `tomllib` — no arbitrary code execution path).
5. **CI workflow inputs** (`.github/workflows/*`, especially `pull_request_target`
   in `prod-igy.yml`, and `workflow_dispatch`/`issue_comment` inputs).
6. **Generated HTML viz** (`viz.py`) consumed in a browser — file/symbol
   names and snippets from an untrusted repo rendered client-side.
7. **Neo4j/export sinks** (`export.py` — Cypher, GraphML, JSONL) — node/edge
   property values originating from untrusted source identifiers.
8. **Outbound network call in `rag --answer`** (`answer.py`, `neo4j.py`) — the
   one documented exception to the "no network calls" claim; SSRF surface
   against attacker-influenced LLM/Neo4j endpoint configuration.

## Findings

### SEC-01 — Index artifacts written with default OS permissions regardless of secret policy
- **Severity:** LOW
- **Confidence:** Confirmed
- **CWE:** CWE-732 (Incorrect Permission Assignment)
- **Location:** `repo2graph/export.py:57` (`atomic_write`), used by every
  artifact writer (`jsonl`, `graphml`, `cypher`, `index.json`, etc.)
- **Attack scenario:** An operator builds an index with `--secret-policy off`
  or `warn-only` (both are documented, explicit opt-ins — see
  `repo2graph/security.py:973`, `redact_content`). On a multi-user Linux host
  with a permissive umask, the resulting `.r2g/` artifacts — which may now
  contain unredacted credentials pulled straight from source — are created
  with whatever the process umask allows (typically `644`), readable by any
  local user with access to the directory. This requires the operator to have
  already opted out of redaction; it is not exploitable against the default
  configuration.
- **Evidence:** `atomic_write()` calls `open(tmp, mode, **open_kw)` with no
  `os.chmod`/`os.fchmod` tightening before or after `os.replace`.
- **Recommendation:** When `secret_filter_policy in ("off", "warn-only")`,
  `chmod 0600` the output directory (or at minimum the JSONL/chunk artifacts)
  after writing. Low effort, defence-in-depth only — the policy is already an
  explicit opt-out the user requested.
- **Effort:** Small.

### SEC-02 — SSE transport auth bypasses persistent-storage review but relies entirely on one bearer token with no rate limiting
- **Severity:** LOW
- **Confidence:** Medium
- **CWE:** CWE-307 (Improper Restriction of Excessive Authentication Attempts)
- **Location:** `repo2graph/mcp/server.py:567-605` (`AuthMiddleware.__call__`)
- **Attack scenario:** `--transport sse --host 0.0.0.0 --auth-token <t>` is a
  documented, intentional way to expose the index over HTTP. The bearer
  check uses `hmac.compare_digest`, which correctly defeats timing attacks on
  the comparison, but there is no attempt limiting or backoff on `/messages`
  or `/sse` — a network attacker who can reach the port can brute-force the
  token at whatever rate uvicorn will sustain. For a short/weak
  operator-chosen token this is a real (if deliberately opted-into) exposure.
  Preconditions: operator explicitly binds past loopback without reducing
  this by firewall, and picks a low-entropy token.
- **Recommendation:** Document a minimum token entropy/length in the
  `--auth-token`/`REPO2GRAPH_MCP_AUTH_TOKEN` help text, and/or add a basic
  per-IP rate limit in `AuthMiddleware`. Hardening suggestion, not a code
  defect — the threat model already correctly treats loopback as the safe
  default and warns loudly (`require_sse_auth`, `create_sse_app`) the moment
  a non-loopback bind lacks a token.
- **Effort:** Small–Medium.

### SEC-03 — `repo_build_status`/task ids are not bound to the caller; any client on the same server instance can poll any task id
- **Severity:** INFO
- **Confidence:** Medium
- **CWE:** CWE-862 (Missing Authorization) — informational, not exploitable in the stdio model
- **Location:** `repo2graph/mcp/tools.py:137-177` (`tool_build_status`)
- **Attack scenario:** Under `--async-build`, `TaskManager` issues task ids
  that `repo_build_status` will return the snapshot for given only the id,
  with no session/caller binding. In the stdio transport (the default and
  only fully-supported one) this is moot — one client, one process. Under
  `--transport sse` with multiple authenticated-but-distinct SSE clients
  sharing one server process, one client could in principle guess/learn
  another's task id and read its build status (file counts, repo path,
  timing) — low-value information, not source content. Flagged as
  informational because the SSE multi-tenant scenario is not a documented
  deployment mode and task ids are server-generated (not attacker-chosen),
  so there's no realistic enumeration path demonstrated here.
- **Recommendation:** If multi-tenant SSE use is ever intended, scope task
  ids per authenticated session. Not actionable as a standalone fix today.
- **Effort:** N/A (design note).

### SEC-04 — `graph.cypher` export uses `json.dumps` as a Cypher string-literal escaper rather than a Cypher-native escaper
- **Severity:** LOW
- **Confidence:** Medium
- **CWE:** CWE-89 (Injection) — analogous construct, not SQL
- **Location:** `repo2graph/export.py:529-531` (`_cy`)
- **Attack scenario:** `_cy(v)` for a string value returns `json.dumps(v)`,
  relying on JSON's `\"`, `\\`, control-char escaping being a syntactic
  subset Cypher also accepts inside a double-quoted string literal. This
  holds for the Neo4j/openCypher grammar in practice (both escape `"`, `\`,
  and control characters compatibly), but it is an implicit assumption
  rather than a Cypher-specific escaper, and any future divergence (e.g. a
  Cypher dialect that treats `\/` or a Unicode escape differently, or a
  non-BMP surrogate pair edge case) would reopen injection through a
  maliciously-named symbol or file path that becomes a node property. Labels
  and relationship types (`_cy_label`) are correctly allow-listed against
  `[A-Za-z_][A-Za-z0-9_]*`, so the injection surface is limited to *property
  values*, which can only add/overwrite properties on an already-`MERGE`'d
  node/edge — not arbitrary Cypher execution — because the value always sits
  inside a `{key: <value>}` map literal, never concatenated as raw Cypher.
- **Recommendation:** Add a comment/test pinning the JSON-escaping assumption
  against the Cypher grammar (string literal rules in the openCypher spec),
  or switch to an explicit Cypher string escaper. Low urgency: worst case is
  malformed output (parse error on import), not code execution, given the
  map-literal structural confinement.
- **Effort:** Small.

### SEC-05 — `_blocked_ip_reason`/`_is_blocked_ssrf_target` DNS-rebinding guard has a documented, acknowledged TOCTOU residual
- **Severity:** LOW
- **Confidence:** Confirmed (self-documented by the maintainer)
- **CWE:** CWE-367 (TOCTOU)
- **Location:** `repo2graph/answer.py:373-412`
- **Attack scenario:** `_is_blocked_ssrf_target` resolves DNS at
  check time and rejects any resolved address that is loopback/link-local/
  metadata/etc. `urllib.request` re-resolves at actual connect time, so a
  short-TTL DNS record that answers differently between the check and the
  connect (classic rebinding) can still reach a blocked target. This only
  matters for `rag --answer` pointed at an attacker-controlled or
  attacker-influenced Ollama/LLM endpoint hostname — a narrow, already
  opt-in, already-documented feature. The code comment at line 389 already
  states this residual gap explicitly and correctly scopes the fix
  (connection pinning, which `urllib` cannot express) as future work.
- **Recommendation:** Acknowledge as accepted risk, or move to `httpx`/a
  custom transport that supports pinning the resolved IP for the actual
  connection. Cross-reference: no new work needed beyond what the maintainer
  has already scoped in-code.
- **Effort:** Medium (would require a transport change).

## Hardening opportunities (non-vulnerabilities)

- **`AuthMiddleware` scope:** only `/sse` and `/messages` are gated;
  `/healthz`/`/readyz` are deliberately open (reasonable — they leak nothing).
  No change needed, noted for completeness.
- **`graph.html` size cap / halving loop** (`viz.py:406-416`) is a good
  resource-exhaustion mitigation (`MAX_PAGE_BYTES`) for the browser-rendered
  viz; no gap found here.
- **MCP guardrail constants** (`mcp/guardrails.py`) consistently clamp every
  numeric argument (hops, k, limit, budget_tokens, read window) server-side
  before use, which closes off the obvious resource-exhaustion vectors from
  a malicious/careless LLM caller (huge `max_hops`, huge `budget_tokens`,
  etc.). This is good defense-in-depth and matches what `tests/` appear to
  cover.
- **`repo_read` never touches the filesystem at call time** — it serves from
  pre-built, pre-redacted chunk data keyed by indexed relative path
  (`mcp/retrieval.py:655`), so even though it also independently rejects
  absolute paths, drive letters, UNC prefixes and `..` segments
  (`mcp/retrieval.py:634-644`), the real confinement is structural (dict
  lookup against a closed set of indexed paths) rather than purely
  string-validation-based. Defense-in-depth done right — two independent
  layers, neither alone load-bearing.
- **Symlinks are dropped, not followed**, during discovery via `lstat` +
  `S_ISREG` checks (`discovery.py:376-388`, `discovery.py:618-630`) before
  any content is read — this closes the most common "indexed repo contains a
  symlink pointing outside the repo root" class of issue by construction.
- Consider documenting the `_is_secret_path`/`scan_content_secrets` coverage
  boundary (keyword + regex + entropy heuristics) explicitly as "best-effort,
  not a guarantee" somewhere user-facing, if it isn't already — the function
  names and comments make clear the authors already know this; a short
  README/SECURITY.md caveat would set the right expectation for anyone
  relying on it to scrub a repo before sharing index artifacts externally.

## Security claims verified/refuted

- **"No network calls except `rag --answer`" (README.md:23).** VERIFIED.
  `grep -rln "urllib\.request\|requests\.|httpx\.|socket\.|http\.client"` over
  `repo2graph/` returns exactly `answer.py`, `neo4j.py`, `query.py` (the
  latter only as a doc-comment match, no actual socket code), and their
  compiled `.pyc` caches. `neo4j.py`'s `urllib.request` use is likewise an
  explicit, user-configured export sink (`repo2graph export --neo4j-url`),
  not a build/index/query-time call. No other module imports a network
  primitive. Claim holds.
- **"fetch.py clones are hardened against argv/env injection."** VERIFIED by
  inspection — `GIT_HARDENING_ARGS`, `GIT_REF` allowlist regex, `parse_spec`/
  `parse_ref` validation before any subprocess spawns, token delivered via
  `GIT_CONFIG_KEY_N`/`GIT_CONFIG_VALUE_N` env (never argv), `GIT_TERMINAL_PROMPT=0`,
  ambient `GIT_DIR`/`GIT_WORK_TREE`/etc. stripped from the child environment.
  No gap found in the paths reviewed.
- **GitHub Actions: `pull_request_target` usage is scoped and documented.**
  VERIFIED — `prod-igy.yml` is the only workflow using it; sparse-checkout
  pulls only the trusted `.github/scripts/prod-igy.js` from the base ref
  (never the PR head), the AI-assist npm install uses `--ignore-scripts`,
  the issue-comment trigger path gates on `author_association` (OWNER/
  MEMBER/COLLABORATOR only), and `.github/zizmor.yml` documents exactly why
  each zizmor finding (`dangerous-triggers`, `artipacked`, `self-repository`,
  `adhoc-packages`) is suppressed, each pinned to a specific `file:line`
  rather than a blanket rule-level ignore. No undisclosed suppression found.
- **All third-party Actions pinned to commit SHA, not a mutable tag.**
  VERIFIED for every workflow grepped (`uses: ...@<40-hex>  # vN.N.N` pattern
  throughout; no bare `@v[0-9]` tag reference found).
- **Permissions blocks present on every workflow.** VERIFIED — every file in
  `.github/workflows/*.yml` declares a top-level `permissions:` block (none
  missing).

## Tooling run

- `bandit`, `pip-audit`, `zizmor` are **not installed** in this environment
  (`python -m pip show bandit zizmor pip-audit` returned "Package(s) not
  found"). Per instructions, no new tooling was installed. All analysis
  above is manual static review (targeted `grep`/`Read` over the modules in
  scope) rather than automated scanner output.
- `.github/zizmor.yml` was read and its suppressions manually checked against
  the workflows they apply to (see "Security claims verified/refuted" above)
  since the tool itself could not be run.

## Limitations

- No `bandit`/`pip-audit`/`zizmor` run — manual review only; a future pass
  with these tools installed may surface additional low-severity findings
  (e.g. bandit's broader B-rule coverage of weak-crypto/`tempfile` patterns)
  that a targeted grep-based review can miss.
- Given the size of the codebase (≈32k LOC in `repo2graph/`, plus
  `security.py` at 1181 lines and `viz.py` at 1328 lines), this review
  prioritized the explicitly named high-risk modules (`fetch.py`,
  `mcp/*`, `security.py`, `viz.py`, `export.py`, workflows) over an
  exhaustive line-by-line pass of every helper module (`cochange.py`,
  `parsepool.py`, `scip.py`, `graphdiff.py`, etc.). Their `subprocess`/
  `tempfile` call sites were grepped and spot-checked but not read in full.
- Did not attempt to independently fuzz the secret-scanner regexes in
  `security.py` for bypass strings (e.g. constructing a credential shape
  specifically crafted to miss `scan_content_secrets`); `tests/test_secrets_hardening.py`
  and `tests/test_secret_coverage_gaps.py` exist and appear to cover this
  class of concern already (not executed, per the no-pytest-run constraint).
- Dependency/supply-chain depth (transitive CVEs, lockfile integrity, SBOM)
  is explicitly out of scope here — cross-reference Agent 10.
- No live exploitation, no external network testing was performed, per
  instructions.

---

## Condensed findings table

| ID | Severity | Confidence | Area | One-line summary |
|---|---|---|---|---|
| SEC-01 | LOW | Confirmed | export.py / permissions | Index artifacts get default OS perms even under opt-out secret policies that leave credentials in clear |
| SEC-02 | LOW | Medium | mcp/server.py SSE auth | Bearer-token check has no rate limiting against brute force when bound non-loopback |
| SEC-03 | INFO | Medium | mcp/tools.py task ids | Build-task ids not session-scoped; moot under stdio, theoretical under multi-tenant SSE |
| SEC-04 | LOW | Medium | export.py Cypher escaping | `_cy()` relies on JSON escaping being Cypher-compatible rather than a dedicated Cypher escaper (structurally confined to property values, not full injection) |
| SEC-05 | LOW | Confirmed | answer.py SSRF guard | Self-documented DNS-rebinding TOCTOU residual in the opt-in `rag --answer` SSRF check |

## Top 5 (by what a reader should act on first)

1. **SEC-01** — chmod index artifacts when `--secret-policy off/warn-only` is used, so an explicit redaction opt-out doesn't also leave the artifact world-readable.
2. **SEC-05** — the SSRF/DNS-rebinding TOCTOU gap in `answer.py` is already well understood by the maintainer; worth prioritizing a transport-level fix (IP-pinned connect) if `rag --answer` usage against untrusted hostnames grows.
3. **SEC-04** — pin the JSON-as-Cypher-escaper assumption in `export.py:_cy` with an explicit test/comment against the openCypher grammar, since it's currently implicit.
4. **SEC-02** — document a minimum entropy bar for `--auth-token` and consider basic rate limiting on the SSE auth middleware.
5. No CRITICAL/HIGH findings surfaced. The codebase's git-invocation hardening (`fetch.py`), MCP path/argument confinement, secret-scanner design, viz.py XSS mitigations (escaped `<`/CRLS, `textContent`-only DOM writes), and GitHub Actions trigger hygiene (`prod-igy.yml`, SHA-pinned actions, scoped `permissions:`) are all solid and should be treated as the baseline other repos in this org are measured against.
