# Security Assessment

Consolidated from `AGENT-FINDINGS/04-security.md`. This was a defensive review of
the maintainer's own public repository. No exploitation was attempted against any
third party, and no external network testing was performed.

## Verdict

**No exploitable CRITICAL or HIGH severity vulnerabilities were found.** The
security posture is the strongest dimension of this project. Nearly every
sensitive call site carries an explicit adversarial-review comment naming the
specific attack it defends against — `fetch.py`'s git invocations,
`mcp/retrieval.py` path handling, `answer.py`'s SSRF guard, `viz.py`'s HTML/JS
generation, and the `prod-igy.yml` workflow among them.

Five findings, all LOW or INFO: four hardening opportunities and one
defence-in-depth residual the maintainer has already scoped.

## Trust boundaries enumerated

| Boundary | Untrusted input | Reviewed in |
|---|---|---|
| Indexed source trees | Arbitrary file content, names, symlinks | `discovery.py`, `exclusions.py`, `parse.py` |
| Remote repo specs | URLs, refs, branch names | `fetch.py` |
| MCP tool arguments | Supplied by an LLM, so effectively attacker-influenceable | `mcp/guardrails.py`, `mcp/tools.py`, `mcp/schemas.py` |
| Target-repo config files | TOML in the scanned repo | `config.py` |
| Generated HTML viz | Repo content rendered in a browser | `viz.py` |
| Export sinks | Neo4j/Cypher, JSON, GraphML | `export.py`, `neo4j.py` |
| CI workflow inputs | PR titles, branch names, event payloads | `.github/workflows/` |

## Findings

| ID | Severity | Confidence | Location | Finding |
|---|---|---|---|---|
| SEC-01 | LOW | High | `export.py` | Index artifacts get default OS permissions even when `--secret-policy off/warn-only` leaves credentials unredacted on disk |
| SEC-02 | LOW | High | `mcp/server.py:491-595` | SSE bearer-token check has no rate limiting if bound beyond loopback |
| SEC-03 | INFO | Medium | `mcp/tools.py` | Build-task ids are not session-scoped; moot under stdio, theoretical under multi-tenant SSE |
| SEC-04 | LOW | Medium | `export.py` (`_cy()`) | Cypher escaping relies on JSON escaping being Cypher-compatible rather than a dedicated escaper |
| SEC-05 | LOW | High | `answer.py:392-420` | Self-documented DNS-rebinding TOCTOU gap in the opt-in `rag --answer` SSRF check |

### SEC-01 — Unredacted secrets inherit default file permissions

When a user explicitly sets `--secret-policy off` or `warn-only`, discovered
credentials are written into the `.r2g` index in clear. The artifacts are created
with default OS permissions, so on a shared or multi-user host the index is
readable by other local users.

This requires the user to have deliberately disabled redaction, which is why it
is LOW rather than higher. But the whole point of those flags is that someone
will use them, and the index is a derived artifact they may not think of as
sensitive.

**Recommendation:** `chmod 0600` index artifacts when redaction is disabled.
Effort: **S**.

### SEC-05 — SSRF guard TOCTOU residual

`answer.py` resolves the target host via `socket.getaddrinfo` and rejects
private/loopback addresses before connecting, and `_SameOriginRedirect` closes
the redirect half. The residual gap is classic DNS rebinding: the name can
resolve differently between the check and the connect.

The code comments this limitation explicitly and the maintainer has already
identified the fix (pin the validated IP and connect to it directly). Scope is
narrow — `rag --answer` is opt-in and sends data to a user-specified LLM
endpoint. Worth prioritising if `rag --answer` adoption grows. Effort: **M**.

### SEC-02 / SEC-03 — SSE transport hardening

The bearer-token comparison correctly uses `hmac.compare_digest` on bytes, and
`require_sse_auth` refuses to serve a non-loopback bind without a token — both
good. What is missing is rate limiting, so a non-loopback deployment is subject
to online token guessing, and build-task ids are global rather than
session-scoped. Both only matter in a multi-tenant SSE deployment, which is not
the documented primary use (stdio, container-local).

**Recommendation:** document a minimum token entropy and add simple rate
limiting. Effort: **S–M**.

### SEC-04 — Pin the Cypher-escaping assumption with a test

`_cy()` depends on JSON string escaping being valid Cypher escaping. The usage is
structurally confined to property values, so no injection path was found. The
risk is that the assumption is implicit and could be invalidated by a future
refactor without any test noticing.

**Recommendation:** add an explicit test asserting the escaping contract.
Effort: **S**.

## Security claims verified

Each of these was checked rather than taken on trust, and each held:

| Claim | Verdict |
|---|---|
| "Building, querying and the MCP server make no network calls" | **Holds.** Outbound HTTP exists only in `answer.py` (opt-in `rag --answer`); `fetch.py` shells out to `git`. Orchestrator independently grepped `urllib`/`requests`/`httpx`/`socket`/`http.client` across the package and found no other call sites. |
| Grammars ship compiled in the wheel, not downloaded | **Holds.** The `tree-sitter-language-pack<1.0` cap is enforced and Dependabot is configured to block major bumps of it. See `DEPENDENCY-AND-COMPATIBILITY.md` for the residual-risk analysis. |
| MCP path confinement | **Holds.** No traversal path found via node ids, chunk ids, file params or resource URIs. |
| `viz.py` XSS mitigations | **Holds.** Escaping of file names, symbol names and code snippets reviewed by hand (the template is an inline string invisible to tooling — see CQ-04). |
| GitHub Actions hygiene | **Holds.** All third-party actions pinned to full commit SHAs; every workflow carries a `permissions:` block; no `pull_request_target` misuse found. |
| `.github/zizmor.yml` suppressions | **Legitimate.** Narrowly pinned per-line with written justification, not blanket suppressions. |

Prompt injection deserves a specific note, since it is the characteristic risk of
this product class: indexed repository content flows into MCP tool output that an
agent will read as context. Output is delimited and `tests/test_prompt_isolation.py`
exists to guard it. This is the right structural mitigation; no bypass was found.
It remains an inherent risk of the category rather than a defect here.

## Limitations

- **`bandit`, `pip-audit` and `zizmor` are not installed** in this environment,
  and installing tooling was out of scope by rule. The review was therefore
  **manual** (targeted grep plus reading). A tool-assisted pass could surface
  findings this review missed, and running those three in CI is recommended
  regardless.
- Dependency CVE analysis is delegated to `DEPENDENCY-AND-COMPATIBILITY.md`.
- No dynamic/fuzz security testing was performed beyond the project's own
  `tests/test_fuzz_inputs.py`.
- The `viz.py` template review was manual because the template is an inline
  string; a dedicated JS linter on an extracted template would be more reliable.
