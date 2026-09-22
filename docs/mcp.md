# MCP server

`repo2graph-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server over an
existing `.r2g` index, so an agent can ask the map questions itself instead of you
pasting a pack into a chat window. This page is its full contract: the five tools,
their argument bounds, the server's own flags, and a config block per client.

It is an *additional* surface, not a replacement: every tool is a thin call into
`repo2graph.query.Index`, the same object the CLI and the GitHub Action use, over
the same artifacts.

> **Note**: `repo2graph-mcp` needs the `mcp` SDK (`mcp>=1.0,<3.0`), which ships as
> an optional extra: `pip install "repo2graph[mcp]"`. The CLI, the Action and the
> Python API never import it.

## Install

Nothing, if you use [uv](https://docs.astral.sh/uv/) — `uvx` fetches and runs the server on demand,
which is what the config blocks below do:

```bash
uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project
```

Otherwise install it once:

```bash
pip install "repo2graph[mcp]"
```

Or from a checkout, if you want to change it: `pip install -e ".[mcp]"`.

The extra pins `mcp>=1.0,<3.0`: `serve()` supports both the 1.x `Server`
decorator API and the 2.x registration API it was replaced with. If neither is
what you have installed, `repo2graph-mcp` says so and names what to install
instead rather than raising.

## The index builds itself

Point the server at a repository and it serves it. If no index exists yet, the
first tool call builds one into `<repo>/.r2g` and answers from it; every call
after that reads the index already on disk.

```bash
repo2graph-mcp /path/to/project      # no prior `repo2graph build` needed
```

The build happens on that first call rather than at startup on purpose. A client
spawns the server and waits for the `initialize` response, so blocking the
handshake for the minute a large repo takes to parse makes the server look dead.
Doing it on first call keeps the handshake instant, and the index is written to
disk either way — so even if that one slow call times out in your client, the
work is not lost and retrying is instant.

Build ahead of time if you would rather the first question be fast:

```bash
repo2graph build /path/to/project -o /path/to/project/.r2g
```

For vector-enhanced retrieval (hybrid BM25 + dense semantic search), which
auto-build does not do for you:

```bash
pip install "repo2graph[rag]"
repo2graph embed -o /path/to/project/.r2g
```

Auto-build writes only what the tools read — `chunks.jsonl`, `nodes.jsonl`,
`edges.jsonl` and `overview.md`. It skips `graph.html`, `graph.graphml` and
`graph.cypher`, which cost real time and which no tool reads. Run
`repo2graph build` yourself if you want the picture too.

### Turning it off

`--no-auto-build` restores the old strict behaviour: the index must already
exist, and the server exits at startup naming the command that creates one.

```
error: no repo2graph index found at '.r2g'. Build one first with: repo2graph build <path> -o .r2g
```

### Which directory gets indexed

Only one convention is trusted, so the server never parses a tree you did not
point it at:

| You pass | Index | Built from |
|---|---|---|
| `repo2graph-mcp /path/to/project` | `/path/to/project/.r2g` | `/path/to/project` |
| `repo2graph-mcp --out /path/to/project/.r2g` | as given | `/path/to/project` (the parent of a dir named `.r2g`) |
| `repo2graph-mcp --out /var/cache/idx/proj` | as given | nothing — auto-build is off, because that path is not a repo hint |
| `repo2graph-mcp /path/to/project --out /var/cache/idx/proj` | as given | `/path/to/project` |

## Client configuration

Every block below uses `uvx`, so there is nothing to install first and the server
stays current. If you would rather install it once, drop the `uvx --from
"repo2graph[mcp]"` prefix and use `repo2graph-mcp` as the command directly.

### Claude Code

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project
```

### Claude Desktop (`claude_desktop_config.json`)

Config file location:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "repo2graph": {
      "command": "uvx",
      "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", "/path/to/project"]
    }
  }
}
```

On Windows use a forward-slash path: `"C:/path/to/project"`.

### Cursor (`.cursor/mcp.json`)

Same block, in your workspace or global `.cursor/mcp.json`.

### Generic MCP clients

Any stdio client can reference it directly:

- **Command:** `uvx`
- **Args:** `["--from", "repo2graph[mcp]", "repo2graph-mcp", "/path/to/project"]`

The registry entry declares exactly this, as `runtimeHint: uvx` plus a `--from`
runtime argument — see [`server.json`](../server.json).

> **Tip:** installed rather than `uvx`-ed, the command is `repo2graph-mcp` and
> the args are just `["/path/to/project"]`. Inside a virtualenv, give the full
> path as `command` — `.venv/bin/repo2graph-mcp`, or
> `.venv\Scripts\repo2graph-mcp.exe` on Windows.

## Server options

`repo2graph-mcp --help` is the full list. Grouped by what they change:

**Index and cache**

| Flag | Default | What it does |
| --- | --- | --- |
| `-o`, `--out` | `<repo>/.r2g` | Index directory to serve. |
| `--no-auto-build` | off | Never build. Exit at startup unless the index already exists. |
| `--async-build` | off | Build a missing index on a background thread and return a `task_id` immediately instead of blocking the first tool call. Poll it with `repo_build_status`. |
| `--cache-size` | `256` | Cached tool results before the least recently used is evicted. `0` disables the cache. |
| `--cache-ttl` | `60` | Seconds a cached result is served before it is recomputed. |

**HTTP transport** — stdio carries no headers, so authentication requires this.

| Flag | Default | What it does |
| --- | --- | --- |
| `--http-port` | off | Serve JSON-RPC on this port in addition to stdio. |
| `--http-host` | `127.0.0.1` | Bind address. Binding beyond loopback with no authentication is **refused at startup**, not merely discouraged. |
| `--http-only` | off | Serve HTTP without the stdio transport. |
| `--well-known-port` | off | Alias for `--http-port`; the discovery documents are served by the same transport. |
| `--http-allow-hosts` | none | Extra hostnames accepted in the `Host`/`Origin` headers on `POST /mcp`, for a deliberate deployment behind a reverse proxy. Loopback and `--http-host` are always accepted; anything else is refused with 403. |

**Authentication**

| Flag | Default | What it does |
| --- | --- | --- |
| `--auth-token` | no auth | Require `Authorization: Bearer <TOKEN>` on every HTTP tool call. Prefer the `R2G_AUTH_TOKEN` environment variable — this flag's value is visible to other local users through `ps`/procfs. The flag wins when both are set. |
| `--auth-oidc-issuer` | off | Validate bearer tokens as JWTs against this OIDC issuer's JWKS, enforcing `iss`, `aud` and `exp`. |
| `--auth-audience` | unchecked | Expected `aud` claim for `--auth-oidc-issuer` tokens. |
| `--auth-jwks-ttl` | `300` | Seconds a fetched JWKS is trusted before refetch. |
| `--auth-cimd` | off | Publish an RFC 7591 client metadata document at `/.well-known/oauth-client-metadata`. |

**Audit logging**

| Flag | Default | What it does |
| --- | --- | --- |
| `--audit-log` | stderr only | Append audit records to this file as well as stderr. |
| `--audit-log-level` | `all` | `none`, `errors` or `all` — which tool calls produce a record. |
| `--audit-log-fsync` | off | Sync each record to disk before returning. Slower; stderr already carries every record, so this only hardens the file copy against a crash. |

`--auth-oidc-issuer` is the only one of these that makes a network call, and only
to that one issuer's JWKS endpoint. Container hardening, TLS termination and the
deployment checklist that goes with the HTTP shape:
[docs/ENTERPRISE_DEPLOYMENT.md](ENTERPRISE_DEPLOYMENT.md).

## Tools

| Tool | Arguments | What comes back |
|---|---|---|
| `repo_map` | none | Languages, hub files and top entry points. Stable across calls, so it caches. Read this first. |
| `repo_search` | `query`, optional `k`, `hops`, `budget_tokens` | Seed chunks plus their graph neighbours, each block headed `[cite: path:start-end]`. |
| `repo_neighbours` | `node_id`, optional `hops`, `limit` | One graph hop from a node: callers, callees, base classes and the defining file, with edge direction. |
| `repo_cache_stats` | none | JSON object with cache metrics (hits, misses, size, etc.). |
| `repo_build_status` | `task_id` | JSON object with build task status, progress, and error details. |

The first three answer questions about the code and exclude secrets
unconditionally. The last two report on the server itself, never read a chunk,
and are never served from the cache — a cached cache-stats or progress reading is
the one answer guaranteed to be out of date.

### Argument bounds

Every numeric argument is coerced and clamped **in the handler**, so `dispatch()`,
a direct Python caller and the stdio server all inherit the same ceiling. Nothing
here raises on a bad value; it is clamped and answered.

| Argument | Tool | Default | Maximum |
| --- | --- | ---: | ---: |
| `k` | `repo_search` | 8 | 50 |
| `hops` | `repo_search`, `repo_neighbours` | 1 | 4 |
| `budget_tokens` | `repo_search` | 6 000 | 12 000 |
| `limit` | `repo_neighbours` | 20 | 50 |
| `query` (length) | `repo_search` | — | 4 000 chars |
| `node_id` (length) | `repo_neighbours` | — | 2 000 chars |
| `task_id` (length) | `repo_build_status` | — | 200 chars |

`repo_neighbours` takes ids in the same shape the rest of the project uses:
`file:<path>`, `sym:<path>::<qualname>`, `dir:<path>`. Hand it something else and
it says so instead of returning nothing.

### `repo_cache_stats`

**Purpose:** Retrieve runtime diagnostic counters for the tool result cache. An agent calls this to inspect cache efficiency or debug server performance.

**Input parameters:** none (empty object).

**Output fields:**

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean | Whether the cache is active (`max_size > 0` and `ttl > 0`). |
| `hits` | integer | Number of successful cache lookups. |
| `misses` | integer | Number of lookups for items not in the cache or expired. |
| `size` | integer | Current number of entries in the cache. |
| `max_size` | integer | Maximum number of entries before eviction. |
| `ttl_s` | integer | Time-to-live for a cached entry, in seconds. |
| `evictions` | integer | Number of entries removed to make room for new ones. |
| `hit_rate` | float | Ratio of hits to total lookups (e.g. `0.75`). |

**Example:**

Call: `repo_cache_stats()`

Result:
```json
{
  "hits": 12,
  "misses": 4,
  "size": 4,
  "max_size": 256,
  "ttl_s": 60,
  "evictions": 0,
  "hit_rate": 0.75,
  "enabled": true
}
```

### `repo_build_status`

**Purpose:** Query progress and status of an asynchronous background index build started with the `--async-build` flag.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `task_id` | string (required) | The opaque handle returned by a previous tool call that initiated the background build. |

**Output fields:**

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string | The requested task ID. |
| `status` | string | Current state: `"building"`, `"ready"`, `"failed"`, or `"unknown"`. |
| `progress_pct` | integer | Estimated completion percentage (1-100). |
| `eta_s` | integer | Estimated seconds remaining. |
| `error` | string \| null | Human-readable failure message if status is `"failed"`. |
| `progress_is_estimated` | boolean | Always `true`, indicating progress is an estimate based on file count. |

**Example sequence:**

1. Call `repo_map()` while the server is running with `--async-build` and no index exists.
2. The server responds with a message indicating a build started:
   ```text
   the index for this repository is still being built. Call repo_build_status with task_id '8d7f3e2a-...' to check; roughly 12s remaining (15% done, estimated).
   ```
3. Call `repo_build_status(task_id="8d7f3e2a-...")`.
4. Result:
   ```json
   {
     "task_id": "8d7f3e2a-...",
     "status": "building",
     "progress_pct": 45,
     "eta_s": 8,
     "error": null,
     "progress_is_estimated": true
   }
   ```

## Three promises the server keeps that the CLI leaves to you

- **Secrets are excluded, always.** A tool an agent calls unattended never returns
  a chunk from a path that looks like a credential store. On the CLI that is
  opt-in (`--answer`).
- **Output is hard-bounded.** `repo_search` clamps whatever budget it is given to
  at most 12 000 tokens and re-measures the rendered result before returning it,
  and `repo_neighbours` lists at most 50 rows however large a `limit` it is
  handed — appending `... (truncated at <limit> neighbours)` when it cuts. No
  single call can eat a context window.
- **Work is hard-bounded.** `k` is capped at 50 and `hops` at 4. One call sits on
  the server's only event loop, so an argument that costs minutes would freeze
  every client, not just the one that sent it.

No LLM call is made by the server itself, and the `mcp` package is an optional
extra: without it the CLI, the Action and the Python API are all unaffected.
