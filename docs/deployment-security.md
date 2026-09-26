# Deployment security guide

This is the single place that answers, per deployment shape, *who is trusted, what they can reach,
and whether that shape is recommended at all*. It does not repeat what
[`docs/THREAT_MODEL.md`](THREAT_MODEL.md), [`.github/SECURITY.md`](../.github/SECURITY.md),
[`docs/PRIVACY.md`](PRIVACY.md) and [`docs/ENTERPRISE_DEPLOYMENT.md`](ENTERPRISE_DEPLOYMENT.md)
already establish — it cites them and adds the piece none of them state explicitly: a trust
boundary per mode, and a verdict on each.

[`docs/THREAT_MODEL.md`](THREAT_MODEL.md) is the companion to this page and the one to read first:
it enumerates the assets, the trust boundaries and the attacks against each surface. This page
takes those boundaries as given and asks the operator's question instead — *given how I intend to
run it, is this shape supported?*

Every claim below is checked against the code cited beside it, not against what the flag names
imply. Where the code does less than a reasonable reader would assume, that gap is stated plainly
rather than smoothed over — see "What this document does not claim" at the end.

## The six deployment modes

| # | Mode | Who can reach it | Recommended? |
|---|---|---|---|
| 1 | Trusted-local CLI (`build`/`query`/`rag`/`doctor`) | Whoever has a shell on the machine | **Yes** — this is the default, unauthenticated-by-design shape |
| 2 | CI indexing (the GitHub Action) | The workflow's own runner, scoped by its job permissions | **Yes** — see [`docs/ACTION_SECURITY.md`](ACTION_SECURITY.md) for the Action-specific trust boundary (`pull_request_target`, token scoping) |
| 3 | Stdio MCP, single developer | The process that spawned it (an AI client on the same machine) | **Yes** — the common case; no network listener exists at all |
| 4 | HTTP MCP on loopback, no auth | Any local process/browser tab that can reach `127.0.0.1` | **Conditionally** — fine on a single-user workstation; not for a shared or multi-user host (see Mode 4 below) |
| 5 | HTTP MCP behind a reverse proxy, authenticated | Whoever the proxy + `--auth-token`/`--auth-oidc-issuer` admits | **Yes, with TLS at the proxy** — the supported shape for a shared deployment |
| 6 | Multi-tenant / non-trusted callers sharing one server | Multiple mutually-distrusting principals | **Not recommended** — see Mode 6; the server has no per-caller data scoping |

### Mode 1 — Trusted-local CLI

**Trust boundary:** none inside the tool. Anyone who can run `repo2graph` already has the same
filesystem access the tool would use — there is no privilege the CLI holds that its caller doesn't
already have.

`build`, `query`, `rag` (without `--answer`) and `doctor` open no socket — verified by a real
socket-level test, not just by reading the code
(`tests/test_rag_path.py::*opens_no_socket*`). The one opt-in exception is `rag --answer`, covered
in its own section below. Nothing here needs authentication because there is no boundary to
authenticate across.

**Verdict: supported, and the intended default.**

### Mode 2 — CI indexing (the GitHub Action)

**Trust boundary:** the workflow's own permission scoping — `contents: read` at the top level,
narrower job-level grants where writes are needed (`index-repo.yml`, `self-index.yml`), and a
sparse, `--ignore-scripts` checkout on the one `pull_request_target` job that has secrets in scope.
This is a different trust model from the other five modes (it's about what the *workflow* can do to
the *repository*, not about who can reach a running server) — full detail, including the specific
CodeQL findings it closes, lives in [`docs/ACTION_SECURITY.md`](ACTION_SECURITY.md); this document
only places it on the map.

**Verdict: supported**, as configured by this repository's own workflows — a fork PR does not
receive write-scoped secrets, by construction of `pull_request_target`'s permission model.

### Mode 3 — Stdio MCP, single developer

**Trust boundary:** the parent process. A stdio server is spawned as a child process by its client
and speaks over an anonymous pipe. There is no header to carry a credential, and — this is the
point `repo2graph/auth.py`'s module docstring makes explicitly — anyone able to write to that pipe
already has the parent process's privileges, so a token check here "would have looked like security
without being any." Authentication is therefore *not implemented* for this transport, on purpose,
not by oversight.

**Verdict: supported**, and the documented default in [`docs/mcp.md`](mcp.md). Appropriate exactly
when the client and the server run as the same user on the same machine — which is what every
`uvx`/`npx`-launched config block in `docs/mcp.md` sets up.

### Mode 4 — HTTP MCP on loopback, no auth

**Trust boundary:** every local process and every browser tab on the same machine. `--http-port`
with no `--auth-token`/`--auth-oidc-issuer` and the default `--http-host 127.0.0.1` starts a server
any local principal can reach — including a browser page, guarded only by the `Host`/`Origin`
allowlist described in `http_server.py`'s module docstring (loopback values and DNS-rebinding
protection, not authentication).

**Is this safe without TLS?** On loopback, yes for confidentiality — traffic never leaves the
machine's own network stack, so there is no wire to eavesdrop on. It is **not** an access-control
boundary: no credential is required, so any other local user or local process can call every tool.

**Verdict: conditionally supported.** Fine for a single-user workstation where "anyone who can open
a socket to `127.0.0.1`" and "anyone who could just run the CLI directly" are the same trust level.
**Not recommended** on a shared or multi-user host (a CI runner shared across jobs, a devbox with
several logged-in users) — there, `127.0.0.1` is not a private boundary, and Mode 5 is the
supported shape.

### Mode 5 — HTTP MCP behind a reverse proxy, authenticated

**Trust boundary:** the proxy's TLS termination plus `repo2graph-mcp`'s own bearer/OIDC check.
Binding beyond loopback with no credential configured is **refused at startup**, not merely
discouraged — `http_server.py` raises before the socket opens:

```
refusing to bind {host}:{port} with no authentication: a code index is
the whole repository in searchable form. Pass --auth-token or
--auth-oidc-issuer, or bind 127.0.0.1.
```

This is enforced at startup, not a runtime toggle, so there is no window where the process is
listening non-locally without a credential requirement in place.

**Is HTTP safe without TLS here?** **No.** The built-in HTTP server does not terminate TLS itself —
[`docs/ENTERPRISE_DEPLOYMENT.md`](ENTERPRISE_DEPLOYMENT.md) says so directly. A bearer token or a
JWT sent to a plaintext, non-loopback endpoint is a credential on the wire in the clear. **Always
put a TLS-terminating reverse proxy in front of any non-loopback bind** — see the worked nginx
example below.

**Authenticated vs. authorized — the distinction this document is required to make explicit.**
`--auth-token` and `--auth-oidc-issuer` answer *who is this caller* (authentication) — constant-time
comparison for the static token, full RS256 JWT verification with `iss`/`aud`/`exp` enforcement for
OIDC (`repo2graph/auth.py`). Neither answers *what is this caller allowed to do*
(authorization): every authenticated caller gets the same three content tools, the same argument
bounds, and the same secret-exclusion behavior. There is no per-user scoping, no read/write split,
no role concept anywhere in `repo2graph/mcp.py` or `repo2graph/http_server.py`. If your deployment
needs to give different callers different views of the same index, that layer has to live in your
proxy or gateway, not in `repo2graph-mcp` — it does not exist here.

**Worked example — a hardened nginx reverse proxy:**

```nginx
# TLS-terminating proxy in front of `repo2graph-mcp --http-port 8719 --http-host 127.0.0.1
# --auth-token ... --http-allow-hosts mcp.internal.example.com`
server {
    listen 443 ssl;
    server_name mcp.internal.example.com;

    ssl_certificate     /etc/nginx/tls/mcp.internal.example.com.crt;
    ssl_certificate_key /etc/nginx/tls/mcp.internal.example.com.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # The bearer/JWT itself is repo2graph-mcp's job to check (Authorization
    # header passed through untouched below) -- this proxy's job is TLS,
    # reachability, and not doing anything clever with the body.
    location /mcp {
        proxy_pass http://127.0.0.1:8719;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_http_version 1.1;
        proxy_read_timeout 35s;   # a little over the server's own 30s request timeout
        client_max_body_size 2m; # generous over the server's 1 MiB MAX_BODY_BYTES
    }

    location = /.well-known/oauth-client-metadata {
        proxy_pass http://127.0.0.1:8719;
        proxy_set_header Host $host;
    }
}

# Anything that isn't HTTPS gets redirected, never served.
server {
    listen 80;
    server_name mcp.internal.example.com;
    return 301 https://$host$request_uri;
}
```

`--http-allow-hosts mcp.internal.example.com` must name the hostname the proxy presents in `Host` —
otherwise the server's own DNS-rebinding guard refuses the proxied request with 403 before the body
is even read (`http_server.py`'s `Host`/`Origin` check runs regardless of what is in front of it).

### Mode 6 — Multi-tenant / non-trusted usage

**Trust boundary:** as Mode 5, plus the assumption that every authenticated caller is equally
trusted with the whole index. That assumption is the thing to interrogate before choosing this
shape.

**What is shared across every caller of one server process:**

- **The whole index**, unscoped. There is no per-caller filter on which nodes, files or chunks a
  tool call can return — `repo_search`/`repo_neighbours`/`repo_map` answer identically for every
  authenticated identity.
- **The result cache** (`--cache-size`). `ResultCache` keys on the canonical JSON of arguments, not
  on caller identity — two different callers issuing the same query can be served the same cached
  render. Not a confidentiality problem *within* one deployment (every caller is already entitled to
  the same content, per the point above), but worth knowing if you were assuming per-caller
  isolation that does not exist.
- **The audit log**, if enabled — every call from every caller lands in one file, distinguished only
  by the identity field (`--audit-log`, see `docs/PRIVACY.md`).
- **Rate and connection limits: none.** `ThreadingHTTPServer` caps neither open connections nor
  threads (`http_server.py`'s own module comment says this outright), and there is no per-caller
  rate limit anywhere in the request path. One authenticated caller issuing requests as fast as
  possible affects every other caller's latency on the same process.

**Verdict: not recommended** as a way to serve mutually-distrusting tenants from one process. It is
fine for multiple *trusted* callers who are all meant to see the same repository (a team sharing one
deployment) — that is Mode 5 with more than one credential issued. It is not a substitute for
per-tenant isolation; if you need that, run one process (and one index) per tenant, each behind its
own credential, rather than one process serving several.

## The `--answer` egress path

`repo2graph rag --answer` is the one path in the entire tool that sends real repository content to a
third party, and it gets a threat-model entry of its own because of what it sends and what decides
where.

**What leaves the machine:** the assembled context pack — real file content pulled out of
`chunks.jsonl` — POSTed to whichever LLM provider wins a precedence race:
`GEMINI_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `OLLAMA_HOST`, in that order, based on
which environment variable is set (override with `--provider` to force one). Because non-source
files are chunked whole, a `.env` sitting in the indexed repository can be seeded by an ordinary
query and shipped to the provider verbatim — this is exactly why `--answer` also gates
`pack_context(exclude_secrets=True)` (see "Closed" below).

**What never runs without it:** a plain `rag` (no `--answer`) makes no DNS lookup and opens no
socket. `answer.py` is imported lazily, only under `if args.answer:` — the module that can make a
network call is not even loaded into the process unless the flag is present. This is the same
socket-level-tested guarantee Mode 1 above relies on.

**What is disclosed before the request goes out:** `answer._disclose()` prints the provider name
and **hostname only** to stderr before the first byte of the request is sent — never a credential,
never a full URL with query parameters, so you see exactly where your code is headed before it
leaves.

**Closed, not open:**

- `--provider` forces a specific provider instead of letting environment-variable presence pick one
  for you — useful when you need a deterministic destination rather than whichever key happens to be
  set.
- Dotfile and secret-shaped paths are automatically excluded from the pack via
  `pack_context(exclude_secrets=True)`, applied unconditionally whenever `--answer` is on — this is
  not a flag you have to remember to also pass.

**What is not closed:** no SSRF protection or destination allowlisting beyond redirect handling — see
[`docs/ENTERPRISE_DEPLOYMENT.md`](ENTERPRISE_DEPLOYMENT.md#what-this-does-not-do) for exactly what
the redirect guards do and do not cover. If your threat model requires egress allowlisting to a
specific set of provider hostnames, enforce it at the network layer (an egress proxy, a Kubernetes
`NetworkPolicy`), not by assuming `--answer` does it.

## MCP tool exposure: every argument is caller-hostile

The MCP server hands its output straight into whatever agent called it, and the caller — not the
operator — picks every argument. `repo2graph/mcp.py` treats that as the threat model for the tool
layer specifically:

- **Every numeric argument is clamped in the handler**, not in `serve()` — so a direct Python
  caller, `dispatch()`, the stdio server and the HTTP transport all inherit the same ceiling with no
  way to route around it by picking a different entry point. `k` tops out at 50, `hops` at 4,
  `budget_tokens` at 12 000, `repo_neighbours`' `limit` at 50 — the full table is in
  [`docs/mcp.md`](mcp.md#argument-bounds). This was missed twice in one development run before the
  discipline settled (`hops`/`k` first, then `limit`), each time producing a tool call that could
  return tens of thousands of characters to whatever asked for it — worth stating so a future
  numeric argument gets the same clamp on day one rather than after the fact.
- **All three content-returning handlers pass `exclude_secrets=True` unconditionally.** `repo_map`,
  `repo_search` and `repo_neighbours` can never return a chunk from a dotfile or secret-shaped path,
  with no flag to turn that off. This is stricter than the CLI, deliberately: a human running
  `repo2graph query` chose to see `.env` in local output; an agent calling the MCP server unattended
  did not make that choice, so the server does not offer it. `repo_cache_stats` and
  `repo_build_status` never read a chunk at all, so this does not apply to them.
- **Work is hard-bounded the same way output is.** One call runs on the server's only event loop
  (stdio) or one handler thread (HTTP); an argument that costs minutes of CPU would freeze every
  other caller, not just the one that sent it — which is why the ceilings above bound *cost*, not
  only *response size*.
- **Resources and prompts are not implemented** (`tools` only) — a client requesting `resources/list`
  or `prompts/list` gets `METHOD_NOT_FOUND`, not a partial or unsafe answer. Nothing here is a gap in
  the tool surface's own security posture; it means there is one fewer surface to reason about
  today.

## Token/OIDC configuration and key rotation

**Static bearer token** (`--auth-token` / `R2G_AUTH_TOKEN`): one shared secret, compared in constant
time (`hmac.compare_digest`) so a `==` timing side-channel can't leak it byte by byte. Prefer the
environment variable over the flag — a flag's value is visible to other local users through
`ps`/procfs on the same host; the flag wins if both are set, so removing it from your process
supervisor's command line (leaving only the environment variable) is enough to close that.
**Rotation is manual and requires a restart:** there is no live-reload of the token, so rotating it
means generating a new value, restarting `repo2graph-mcp` with it, and updating every configured
caller before or immediately after — there is a window during rotation where old and new callers
cannot both be served by the same running process. If your deployment cannot tolerate that window,
prefer OIDC.

**OIDC** (`--auth-oidc-issuer`, `--auth-audience`, `--auth-jwks-ttl`): bearer tokens are verified as
JWTs against the issuer's published JWKS, with `iss`, `aud` and `exp` all enforced — a signature
check alone proves the issuer minted *a* token, not one for this server, which is why `aud` matters.
**Key rotation here is largely automatic by design:** a token signed with a `kid` the cached JWKS
doesn't recognize triggers a refetch (rate-limited to `DEFAULT_MIN_REFRESH_INTERVAL` = 5 seconds
between unknown-kid refetches, independent of the `--auth-jwks-ttl` cache lifetime, so a flood of
tokens carrying random `kid`s cannot force one fetch per token). Rotating a signing key at your IdP
is therefore usually a non-event for this server: the next token signed with the new key causes one
JWKS refetch and verification proceeds. The one operational thing to get right is the `aud` claim
staying stable across your own key rotations — `--auth-audience` is checked against the token, not
against the JWKS, so it is unaffected by which key signed the token.

Algorithm confusion is closed by construction, not by convention: `repo2graph/auth.py`'s
`ALGORITHMS` table accepts RS256/RS384/RS512 only, a JWK's `kty` must be `"RSA"`, and a key's
declared `use`/`key_ops` (when the issuer publishes them) is honored — a key marked for encryption
is never accepted as a signature verifier. None of this is configurable per deployment; it is a
property of the module, not a setting you could accidentally weaken.

## Artifact sensitivity and retention

A `.r2g` index is your repository's source, chunked and denormalized for search — treat it with (at
minimum) the same access control you'd give the repository itself. The full table of what's cached,
where, and for how long lives in [`docs/PRIVACY.md`](PRIVACY.md#what-gets-cached-and-where); the
points that matter for a deployment decision specifically:

- **Every artifact writes only inside the output directory you named.** `export.py` resolves every
  writer's path as `outdir / <fixed relative name>` — never outside the tree you pointed the tool
  at, and never a caller-supplied absolute path. This holds for both the CLI and the MCP server's
  auto-build.
- **Nothing rotates or expires on its own.** `parse.cache.json`, `vectors.npy`/`.meta.json` and
  `index.state.json` persist until the *next build* overwrites them — there is no TTL, no automatic
  pruning. If you're shipping `.r2g/` as a CI artifact (as `action.yml` supports) or committing it to
  a `graph` branch, that copy's retention is entirely governed by your CI provider's artifact
  retention settings or your git history — repo2graph applies none of its own.
- **The audit log, if you turn it on, is append-only forever.** `--audit-log <path>` never rotates or
  deletes; retention is entirely up to you. It's already redacted on shape as well as field name
  (credential-looking strings, secret-path patterns) before it's written, and it never contains raw
  chunk text — but it is still an unbounded, growing file you own the lifecycle of.
- **The in-memory `ResultCache` is the one thing that isn't persisted.** Process memory only,
  cleared on any index rebuild or process exit — nothing here needs a retention policy because
  nothing here survives the process.

## What this document does not claim

Same caveat as every other security document in this repository: this describes what the code does
today, verifiable by reading `repo2graph/http_server.py`, `repo2graph/auth.py` and
`repo2graph/mcp.py` directly, not a claim of formal audit, penetration test, or compliance
certification. Two gaps worth naming rather than glossing over, both tracked separately rather than
fixed in this document:

- **No rate limiting or connection cap on the HTTP transport.** `ThreadingHTTPServer` bounds
  neither. A deployment that needs this today has to add it at the reverse proxy (nginx's
  `limit_req`/`limit_conn`, or an equivalent at your load balancer).
- **Some error paths still echo `str(exc))` toward the client** rather than a sanitized message —
  worth knowing if your threat model treats exception text as potentially sensitive (a stack frame
  naming an internal path, for instance). This is a known, separately-tracked gap, not something
  this document is claiming is closed.

If your organization requires a formal audit, penetration test, or a compliance attestation (SOC 2,
ISO 27001, or similar), that requires organizational and technical controls beyond this repository's
scope — this document is an input to that process, not a substitute for it.
