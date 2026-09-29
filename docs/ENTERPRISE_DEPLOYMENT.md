# Enterprise deployment

How to run repo2graph — the CLI, the GitHub Action, or the MCP server — inside an organization
where the repositories being indexed contain proprietary code. This is guidance for the operator
who deploys the tool, not a claim that any single configuration makes it "fully secure" — see
`docs/SECURITY-AUDIT.md`'s closing section for what the tool does and does not protect against.

Read **[docs/THREAT_MODEL.md](THREAT_MODEL.md)** first if you are making a deployment decision: it
states the trust boundaries this page's architecture is designed around, and names every open
security-relevant gap against the surface it belongs to. **[docs/secure-configuration.md](secure-configuration.md)**
has the copy-paste version of the configurations below, and
**[docs/PRIVACY.md](PRIVACY.md)** covers what is written where and how to delete it.

## Recommended architecture

```mermaid
flowchart TD
    Dev[Developer / AI client] -->|stdio, local process| MCP[repo2graph-mcp]
    Dev2[Remote / shared client] -->|HTTPS + bearer or OIDC| Proxy[TLS-terminating reverse proxy<br/>e.g. nginx, envoy]
    Proxy -->|127.0.0.1 only| MCP2[repo2graph-mcp --http-port]

    subgraph Sandbox[Container / sandbox]
        MCP
        MCP2
    end

    Sandbox -->|read-only mount| RepoRO[(Repository — read-only)]
    Sandbox -->|read-write, tmpfs or scoped volume| IndexDir[(.r2g index directory)]
    Sandbox -.->|network: none, unless --answer or --auth-oidc-issuer| Net{{No egress by default}}
```

Two supported shapes, both real:

1. **Local, stdio, single developer.** The most common case — `uvx --from "repo2graph[mcp]"
   repo2graph-mcp /path/to/project`, spawned by the AI client as a child process. No network
   listener exists at all; the client owns the pipe. This is the shape `docs/mcp.md` documents.
2. **Shared, HTTP, multiple callers.** `--http-port` with `--auth-token` or `--auth-oidc-issuer`.
   The server refuses to bind beyond loopback with no auth configured
   (`http_server.py:426-430`) — this is enforced at startup, not a configuration you have to
   remember to set. Put a TLS-terminating reverse proxy in front for anything crossing a network
   boundary; the built-in HTTP server does not terminate TLS itself.

   **The server does not know whether a reverse proxy is actually there.** Binding to a
   non-loopback address with authentication configured is allowed to start (refusing it would
   break the exact reverse-proxy shape this page recommends), but `HTTPTransport` emits a
   high-visibility startup warning — a plain-text banner on stderr plus a structured
   `http_transport_no_tls_termination` event — every time it does, unless the operator passes
   `insecure_transport_ack=True` as their explicit acknowledgement that TLS termination is already
   handled upstream. A bearer or OIDC credential sent to a bind with no TLS in front of it travels
   in clear text to anyone who can observe the network between the caller and this process.

## Rate limiting, concurrency quotas and the trusted-proxy allowlist

`HTTPTransport` enforces four independent ceilings by default (`RateLimitConfig` in
`http_server.py`), so a shared deployment cannot be starved by one caller or one burst:

- **Per-client rate limit** — a sliding window (`requests_per_window` / `window_seconds`) keyed on
  the *verified* identity: the OIDC `sub` claim when one exists, a fixed bucket for the shared
  static bearer token (it names no one in particular, so it cannot be split further), or the
  caller's network address when no auth is configured. A caller over the limit gets `429` with a
  `Retry-After` header and JSON-RPC error code `-32000`.
- **Global concurrent-request limit** (`max_concurrent_requests`) plus a **bounded wait queue**
  (`max_queue_size`, `queue_wait_seconds`) — a request arriving once every slot is busy waits up to
  `queue_wait_seconds` for one to free, and is refused with `503` (`Retry-After`, JSON-RPC error
  code `-32001`) rather than queued without limit if the queue itself is already full.
- **Index build concurrency limit** (`max_concurrent_builds`) — a separate, non-blocking gate
  specifically around the code path that may trigger an auto-build, independent of the general
  concurrency slot above. Several callers hitting an unbuilt index at once cannot each start their
  own build; the first proceeds, the rest are told to retry.
- **Response size limit** (`max_response_bytes`) — a tool result larger than this is replaced with
  a bounded error rather than sent, independent of whatever budget `repo2graph query`'s own
  `pack_context` already applied.

Every rate-limit or overload event is recorded in the audit log and emitted as a structured
`rate_limited` event, keyed on the same client identity described above — never on the token or
claims themselves, so the record is safe to ship to a SIEM.

**Forwarded headers are ignored by default.** `X-Forwarded-For` — the header a reverse proxy sets
to carry the original caller's address — is never consulted unless *both* `trust_proxy` is enabled
*and* the directly-connecting peer is itself in the `trusted_proxies` allowlist. Enabling
`trust_proxy` without also naming which peers may set the header would let any direct, untrusted
caller claim to be someone else purely by sending it. Neither setting ever affects
*authentication* — the Authorization header is the only credential this server ever reads; forwarded
headers only change which network identity a rate-limit bucket is keyed on. If you run behind a
reverse proxy and want per-real-caller rate limiting rather than one bucket for the whole proxy,
enable `trust_proxy` and list the proxy's address (or addresses, for a pool) in `trusted_proxies`.

## Container hardening

If you run `repo2graph-mcp` in a container (recommended for the HTTP-shared shape), you can build the official `Dockerfile` provided in this repository, which is already configured for these requirements:

```bash
docker build -t your-repo2graph-image .
docker run --rm \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --network=none \
  -v /path/to/repo:/repo:ro \
  -v repo2graph-index:/repo/.r2g \
  --user 10000:10000 \
  your-repo2graph-image \
  repo2graph-mcp /repo --no-auto-build
```

Notes on each flag, specific to what this codebase actually needs:

- **`--read-only` + a writable volume for `.r2g` only.** repo2graph writes exactly one directory —
  the index output directory — and nothing else. Mount the repository itself read-only
  (`-v ...:ro`); the container's root filesystem can be fully read-only as long as `.r2g` (or
  wherever `-o` points) has a writable mount.
- **`--network=none`** is correct for the default configuration (no `--answer`, no
  `--auth-oidc-issuer`). If you need `--answer`, you need egress to whichever LLM provider you
  configure — scope that with an explicit allowlist at the network layer (this codebase makes no
  attempt at SSRF protection or destination allowlisting itself; see "What this does not do"
  below), not by opening `--network=none` off. If you use `--auth-oidc-issuer`, you need egress to
  that one issuer's JWKS endpoint only.
- **`--cap-drop=ALL` / `--security-opt=no-new-privileges` / non-root user.** repo2graph needs no
  Linux capability — it does not bind privileged ports, does not need raw sockets, does not fork
  privileged subprocesses. Nothing in the codebase requires running as root.
- **No host Docker socket, no credential mounts.** Never mount `/var/run/docker.sock` or a
  credentials directory (`~/.aws`, `~/.ssh`) into this container — the tool has no legitimate use
  for either, and `docs/SECURITY-AUDIT.md`'s secret-exclusion guarantee only covers what's *inside*
  the repository tree it's told to index, not what an operator additionally mounts in.
- **`--no-auto-build`** if you'd rather control exactly when indexing happens rather than let the
  first tool call trigger it — useful in a shared deployment where you don't want an arbitrary
  caller's first request to be the one that pays the indexing cost.

Kubernetes is not required merely because this is an enterprise environment. A single container
per repository (or a small pool behind the reverse-proxy shape above) is the right size for what
this tool does; nothing here needs a scheduler, a service mesh, or a Kubernetes-specific security
context beyond the equivalent pod `securityContext` fields (`runAsNonRoot`, `readOnlyRootFilesystem`,
`allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`) matching the Docker flags above.

## Recommended package deployment model

Do not run production infrastructure on an unpinned `uvx repo2graph`, where "unpinned" means no
version constraint — `uvx` always resolves and fetches at invocation time, so an unpinned command
line can silently pick up a new release between one run and the next.

```
PyPI (repo2graph==X.Y.Z, verified via pip-audit / your SCA tool)
  ↓
Internal artifact repository (Artifactory, Nexus, a private PyPI mirror)
  ↓
Approved package, version-pinned
  ↓
Developer environment / CI runner
```

Concretely:

```bash
# Pin explicitly, everywhere this matters:
uvx --from "repo2graph[mcp]==2.2.0" repo2graph-mcp /path/to/project

# Or, mirrored internally:
pip install --index-url https://pypi.internal.example.com/simple/ "repo2graph[mcp]==2.2.0"
```

`uv.lock` is committed in this repository specifically so that anyone building from source gets the
exact dependency graph that was reviewed — mirror that discipline in your own deployment: pin
`repo2graph` itself, and let your SCA tool (see `docs/SECURITY-AUDIT.md`'s CI/CD findings — this
repo's own `dependency-audit.yml` runs `pip-audit --strict`) gate upgrades rather than floating on
whatever the latest release happens to be.

## Protocol version and transport honesty

`initialize` advertises `protocolVersion: "2024-11-05"`. That is a deliberate statement about what
this transport actually implements, not the newest revision available: plain JSON-RPC
request/response over `POST /mcp`, `Content-Length` framing only (chunked bodies are refused),
`HTTP/1.0` with the connection closed after every response, and no session concept. It is **not**
Streamable HTTP (the `2025-06-18`/`2025-03-26` transport, which adds a `GET` SSE stream,
`Mcp-Session-Id`, and `Last-Event-ID` resumability) — a client that specifically requires that
transport should not connect expecting it to work. If your client negotiates protocol versions and
insists on a newer one, use the stdio transport instead, which goes through the `mcp` SDK and
negotiates independently of this HTTP server's own advertised value.

## What this does not do

Being explicit about the boundary, per §53 of the brief:

- **Sandboxing the MCP server does not protect against a compromised host.** If the machine running
  the container is already compromised, no application-layer or container-layer control here
  changes that — this is a statement about defense in depth, not a claim that containment is total.
- **No general SSRF protection or destination allowlisting** on the `--answer` or
  `--auth-oidc-issuer` network paths. Both constrain redirects, which is narrower than an
  allowlist and not a substitute for one. `--auth-oidc-issuer` requires `https` on every hop,
  caps redirects at 3 (`auth.py:411`), and requires `jwks_uri` to share the configured issuer's
  scheme and host (`auth.py:250-258`), so the JWKS fetch cannot be downgraded or relocated.
  `--answer` keeps a provider redirect on the same origin and refuses an https-to-http
  downgrade (`answer.py:407`), because the request carries your API key in a header that
  CPython would otherwise forward to whatever host the redirect names. Neither path restricts
  where *you* point it. If your threat model requires egress allowlisting, enforce it at the
  network layer (an egress proxy, a Kubernetes `NetworkPolicy`), not by assuming the
  application does it.
- **No secret-scanning of arbitrary repository content beyond the path-shape and audit-log-value
  denylists documented in `docs/SECURITY-AUDIT.md`.** A `.env` file is excluded by path; a
  credential accidentally committed inside `app_config.py` with an unremarkable variable name is
  not caught by anything repo2graph does — this is inherent to path/shape-based detection, not a
  bug to fix, and is exactly why `docs/PRIVACY.md` and `.github/SECURITY.md` avoid claiming complete
  secret protection.

## Enterprise deployment checklist

- [ ] `repo2graph` version pinned in every environment that runs it (see above).
- [ ] If using `--http-port`: TLS-terminating proxy in front, `--auth-token` or
      `--auth-oidc-issuer` configured (the server refuses an unsafe bind otherwise, but verify your
      proxy doesn't accidentally expose the unauthenticated loopback port).
- [ ] Container runs `--read-only --cap-drop=ALL --security-opt=no-new-privileges`, non-root,
      with only the index-output directory writable.
- [ ] `--network=none` unless `--answer` or `--auth-oidc-issuer` is in use; if either is, egress is
      scoped at the network layer to the specific destination, not left open.
- [ ] `--audit-log` enabled and shipped to your SIEM if you need a record of who queried what,
      per `docs/PRIVACY.md`'s description of what that log does and doesn't contain.
- [ ] Your own SCA/dependency-scanning pipeline gates upgrades of `repo2graph` and its extras,
      rather than floating `uvx repo2graph` with no version pin.
