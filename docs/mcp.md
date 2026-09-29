# MCP server

`repo2graph-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server over an
existing `.r2g` index, so an agent can ask the map questions itself instead of you
pasting a pack into a chat window. This page is its full contract: the ten tools,
their argument bounds, the server's own flags, and a config block per client.

It is an *additional* surface, not a replacement: every tool is a thin call into
`repo2graph.query.Index`, the same object the CLI and the GitHub Action use, over
the same artifacts.

> **Before exposing the HTTP transport**, read
> [docs/THREAT_MODEL.md §3.5](THREAT_MODEL.md#35-the-http-mcp-surface) — it names what the server
> defends against, what it does not yet (TLS enforcement, rate limiting), and why the intended
> shape is a loopback bind behind a reverse proxy. The hardened invocations are in
> [docs/secure-configuration.md](secure-configuration.md). Stdio mode, the default, has no
> listening socket and none of this applies to it.

> **Note**: `repo2graph-mcp` needs the `mcp` SDK (`mcp>=2.0,<3.0`), which ships as
> an optional extra: `pip install "repo2graph[mcp]"`. The CLI, the Action and the
> Python API never import it. **1.x is not supported** (#407): it hangs on the
> server's first tool call. `repo2graph-mcp` checks the installed version at
> startup and refuses to run under 1.x, naming the installed version and what
> to install instead, rather than hanging silently.

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

Prefer `npx`? [`repo2graph-mcp` on npm](https://www.npmjs.com/package/repo2graph-mcp) is a thin
launcher that resolves `uvx` (falling back to an installed `repo2graph-mcp`, then `pipx`) and hands
off to it — the server itself is still this same Python package, not a port. See
[`npm/README.md`](../npm/README.md) for the resolution order and what it does when none of those is
on `PATH`.

```json
{
  "mcpServers": {
    "repo2graph": {
      "command": "npx",
      "args": ["-y", "repo2graph-mcp", "/path/to/project"]
    }
  }
}
```

The extra pins `mcp>=2.0,<3.0`. `serve()` speaks only the 2.x `Server`
registration API (`on_list_tools=`/`on_call_tool=`) -- the earlier 1.x
decorator API (`@server.list_tools()`/`@server.call_tool()`) is not supported:
under mcp 1.30.0 the server hung on the very first `tools/call` (#407), and
that path was never exercised by CI, which always resolved 2.x fresh from
PyPI. If what is installed is not `mcp>=2.0,<3.0`, `repo2graph-mcp` names the
installed version and what to install instead at startup, rather than hanging
or raising a bare traceback.

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
| `--allow-auto-build` | off | Allow auto-building a missing index on tool calls in HTTP mode. (In HTTP mode, auto-build is disabled by default to prevent read-only network tool requests from initiating background builds without explicit authorization). |
| `--async-build` | off | Build a missing index on a background thread and return a `task_id` immediately instead of blocking the first tool call. Poll it with `repo_build_status`. |
| `--cache-size` | `256` | Cached tool results before the least recently used is evicted. `0` disables the cache. |
| `--cache-ttl` | `60` | Seconds a cached result is served before it is recomputed. |

> **Auto-build in stdio vs HTTP mode:** In local stdio mode, a missing index is automatically built on the first tool call for developer convenience. In HTTP mode, auto-build is disabled by default — tool calls against an unindexed directory return a 503 error with build instructions unless `--allow-auto-build` is explicitly enabled.

**HTTP transport** — stdio carries no headers, so authentication requires this.

| Flag | Default | What it does |
| --- | --- | --- |
| `--http-port` | off | Serve JSON-RPC on this port in addition to stdio. |
| `--http-host` | `127.0.0.1` | Bind address. Binding beyond loopback with no authentication is **refused at startup**, not merely discouraged. |
| `--http-only` | off | Serve HTTP without the stdio transport. |
| `--well-known-port` | off | Alias for `--http-port`; the discovery documents are served by the same transport. |
| `--http-allow-hosts` | none | Extra hostnames accepted in the `Host`/`Origin` headers on `POST /mcp`, for a deliberate deployment behind a reverse proxy. Loopback and `--http-host` are always accepted; anything else is refused with 403. |
| `--http-insecure-ok` | off | Acknowledge that this server does not terminate TLS (#267) and silence the startup warning emitted when `--http-host` is non-loopback with authentication configured. Pass only when a TLS-terminating reverse proxy already sits in front. |
| `--trust-proxy` | off | Honour `X-Forwarded-For` for rate-limit client identity (#267), but only from a peer also named in `--trusted-proxies` — both must hold. Never affects authentication. |
| `--trusted-proxies` | none | Comma-separated peer addresses allowed to set `X-Forwarded-For` when `--trust-proxy` is also set. |

**Rate limiting** — #264. Always on for HTTP; these flags retune it, none disable it.

| Flag | Default | What it does |
| --- | --- | --- |
| `--rate-limit-requests` | `300` | Requests one client identity may make per `--rate-limit-window` before being throttled. |
| `--rate-limit-window` | `60` | Width of the rate-limit sliding window, in seconds. |
| `--max-concurrent-requests` | `64` | Server-wide in-flight tool calls admitted at once. |
| `--max-queue-size` | `128` | Requests allowed to wait for a concurrency slot once `--max-concurrent-requests` is saturated, before being refused immediately as overloaded. |
| `--max-concurrent-builds` | `4` | Server-wide concurrent auto-builds (index opens that may trigger a build) admitted at once. |
| `--max-response-bytes` | `8388608` (8 MiB) | A JSON-RPC success response larger than this is replaced with a bounded error rather than sent. |

Naming none of these keeps `RateLimitConfig()`'s own defaults from
`http_server.py`; naming one does not require naming the rest — the others
still default.

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
| `repo_map` | none | Languages, hub files and top entry points, prefixed with a staleness note (#383) if the working tree has changed since the index was built. Stable across calls, so it caches. Read this first. |
| `repo_search` | `query`, optional `k`, `hops`, `budget_tokens` | Seed chunks plus their graph neighbours, each block headed `[cite: path:start-end]`. |
| `repo_neighbours` | `node_id`, optional `hops`, `limit` | One graph hop from a node: callers, callees, base classes and the defining file, with edge direction. |
| `repo_find_symbol` | `name`, optional `kind`, `path_prefix`, `limit` | Name -> `node_id`(s): JSON array of `{node_id, name, qualname, kind, path, start_line, end_line, lang}`. |
| `repo_read` | `path`, optional `start_line`, `end_line`, `context` | A widened `[cite: path:start-end]` citation window, read from the index rather than the filesystem. |
| `repo_path_between` | `from_id`, `to_id`, optional `max_hops`, `edge_types`, `max_paths` | Bounded, bidirectional path(s) between two node_ids, with per-edge and minimum confidence. |
| `repo_impact` | optional `base`, `head`, `diff`, `max_depth`, `format` | PR and git diff impact analysis: changed symbols, affected public APIs, callers, tests, and blast radius. |
| `repo_blast_radius` | `node_id`, optional `max_hops`, `include_cochange`, `limit` | Reverse reachability from a node_id: callers, subclasses, importers and co-changed files, by hop distance. |
| `repo_cache_stats` | none | JSON object with cache metrics (hits, misses, size, etc.). |
| `repo_build_status` | `task_id` | JSON object with build task status, progress, and error details. |

The first eight answer questions about the code and exclude secrets
unconditionally. The last two report on the server itself, never read a chunk,
and are never served from the cache — a cached cache-stats or progress reading is
the one answer guaranteed to be out of date.

> **Naming note:** an earlier design for `repo_blast_radius` called it
> `repo_impact` — but that name already belongs to the PR/diff-impact tool
> above, a fully built, different, existing feature. It ships as
> `repo_blast_radius` instead so neither tool breaks the other; do not
> confuse the two when reading an older issue or draft that used the old name.

### Tool annotations are honest about auto-build (#292)

MCP's `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` are
answered once, in `tools/list`, for the server's whole lifetime — there is no
per-call variant. So they can only be as honest as *this server instance*
allows, and that differs by mode:

- **An already-built index, any transport, or `--no-auto-build`.** No tool
  call can write anything. Every tool is genuinely read-only, and the stdio
  server (`get_tools(auto_build=False)`, the default) says so.
- **stdio with a repo to build from, auto-build not disabled.** The first
  call to any tool but `repo_build_status` may run `graph.build()` — parse
  every file, run git, write `.r2g/**` to disk — before it answers
  (`run_tool` routes every other tool through `open_index_or_task`, which
  builds when nothing exists yet). `serve()` passes `auto_build=(repo is not
  None)` to `get_tools()`, so a server started this way reports
  `readOnlyHint: false` for those tools, not the same flat annotation an
  already-indexed server sends.
- **HTTP.** Auto-build defaults to **off** regardless of this flag (#265):
  `main()` only sets `build_from` from `--allow-auto-build`, and without it
  a missing index is a plain error, not a build. An HTTP deployment that has
  not opted into `--allow-auto-build` is in the first, fully-read-only case
  above. One residual gap: `http_server.py`'s own `tools/list` handler
  builds its response from the flat `TOOL_ANNOTATIONS` constant directly
  rather than calling `get_tools(auto_build=...)`, so an operator who *does*
  pass `--allow-auto-build` for HTTP currently gets the same
  always-read-only annotations regardless — this doc and `--allow-auto-build`
  itself are the prominent disclosure of that side effect in the meantime.

### Argument bounds

Every numeric argument is coerced and clamped **in the handler**, so `dispatch()`,
a direct Python caller and the stdio server all inherit the same bounds. Nothing
here raises on a bad value; a value below the minimum is raised to it, one above
the maximum is lowered to it, and a non-number (`"abc"`, `null` excepted,
non-finite) takes the default **and the reply starts with a one-line
`_note: k='abc' is not an integer; used the default 8._`** so the substitution is
visible (for `repo_impact`, only in the `markdown` and `pr-comment` formats -- a
prefix would stop `json`/`sarif` parsing). `budget_tokens` also treats zero or
negative as "not a budget anyone means": it takes the **default** with a note.

| Argument | Tool | Default | Minimum | Maximum |
| --- | --- | ---: | ---: | ---: |
| `k` | `repo_search` | 8 | 1 | 50 |
| `hops` | `repo_search`, `repo_neighbours` | 1 | 0 | 4 |
| `budget_tokens` | `repo_search` | 6 000 | 1 (≤ 0 → default) | 12 000 |
| `limit` | `repo_neighbours` | 20 | 1 | 50 |
| `limit` | `repo_find_symbol` | 20 | 1 | 50 |
| `limit` | `repo_blast_radius` | 40 | 1 | 50 |
| `context` | `repo_read` | 0 | 0 | 500 lines |
| output (chars) | `repo_read` | — | — | 20 000 |
| `max_hops` | `repo_path_between` | 6 | 1 | 8 |
| `max_paths` | `repo_path_between` | 3 | 1 | 10 |
| nodes visited | `repo_path_between` | — | — | 4 000 (both frontiers combined) |
| `max_hops` | `repo_blast_radius` | 3 | 1 | 6 |
| nodes visited | `repo_blast_radius` | — | — | 4 000 per section (callers/subclasses/importers) |
| `max_depth` | `repo_impact` | 2 | 1 | 4 |
| `diff` (length) | `repo_impact` | — | — | 1 000 000 chars |
| output (tokens) | `repo_impact` | — | — | 12 000 |
| `query` (length) | `repo_search` | — | — | 4 000 chars |
| `node_id` (length) | `repo_neighbours`, `repo_path_between`, `repo_blast_radius` | — | — | 2 000 chars |
| `path` (length) | `repo_read` | — | — | 2 000 chars |
| `task_id` (length) | `repo_build_status` | — | — | 200 chars |

`repo_neighbours`, `repo_find_symbol`'s results, `repo_path_between` and
`repo_blast_radius` all take/return ids in the same shape the rest of the
project uses: `file:<path>`, `sym:<path>::<qualname>`, `dir:<path>`. Hand any
of them something else and it says so instead of returning nothing.

`repo_path_between`'s hop and visited-node ceilings are deliberately their own
constants, not a reuse of `repo_search`/`repo_neighbours`/`repo_impact`'s
`MCP_MAX_HOPS` (4) — a bidirectional path search does roughly `max_hops / 2`
layers of real work on each side, not `max_hops` deep on one, so the same
number does not mean the same cost.

### Errors are flagged, not just worded

A call the server cannot answer — a missing `query` or `node_id`, a `node_id`
that is not in the graph, an unknown tool name, a `repo_impact` whose `git diff`
failed, whose `diff` text is not a unified diff or whose `format` is not one of
`markdown`/`json`/`sarif`/`pr-comment`, a `repo_build_status` with no or an
unknown `task_id` (or on a server without `--async-build`, whose error body is
still the JSON object below) — still comes back with a sentence saying what went wrong and how to fix
it, but the result carries **`isError: true`** so a client can tell it from a
real (possibly short) answer. This holds on the stdio server and on the HTTP
transport. An empty result is never
used to mean "error".

### `repo_find_symbol`

**Purpose:** Look up a symbol or file's `node_id` by name -- the lookup an agent
that already knows a name (from a traceback, a grep, a review comment) needs
before it can call `repo_neighbours`, `repo_read`, `repo_path_between` or
`repo_blast_radius`, with no `repo_search` round trip.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `name` | string (required) | Symbol or file name to look up (e.g. `"validate_token"`). |
| `kind` | string (optional) | Exact node `kind` filter (e.g. `"function"`, `"class"`, `"method"`). |
| `path_prefix` | string (optional) | Only node ids whose path starts with this prefix. |
| `limit` | integer (optional) | Maximum candidates returned (default 20, clamped 1-50). |

**Matching order** (the same precedence `_boost_identifiers` uses for BM25
identifier boosting): an exact name match first; if none, a case-insensitive
match; if still none, a match on the last `::`/`.`-separated segment of a
symbol's qualname. Whichever tier produces a hit is the tier returned -- an
ambiguous name returns *every* candidate in that tier, with its path, so the
caller disambiguates rather than the server guessing. A name matching nothing
returns `[]`, not an error. Secret-excluded paths never appear.

**Output:** JSON array of `{node_id, name, qualname, kind, path, start_line, end_line, lang}`.

### `repo_read`

**Purpose:** Widen a `[cite: path:start-end]` citation -- the anchor every
other tool's output is built around -- with no filesystem access. Read from
`chunks.jsonl`, not disk: chunks have already passed secret-path exclusion and
content redaction, the filesystem has not, and a served index may have no
source tree beside it at all (the `graph` branch / GitHub Action artifact
case). This is what makes the tool safe and correct over the HTTP transport,
from a different machine, with nothing shared but the index.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `path` | string (required) | Repo-relative path, exactly as it appears in a `[cite: path:start-end]` header. Absolute paths and any `..` segment are refused outright, not normalised. |
| `start_line` | integer (optional) | First line to read (default 1). |
| `end_line` | integer (optional) | Last line to read (default: `start_line`). |
| `context` | integer (optional) | Extra lines of context on each side of `[start_line, end_line]` (default 0, clamped 0-500). |

**Output:** a `### [cite: path:start-end]` header (matching the rest of the
surface) plus the text, bounded to 20 000 characters and cut at a line
boundary if it would exceed that.

A span not covered by any chunk answers **"not indexed"** rather than falling
back to disk -- this includes the `file_residual` under-40-character case
(AGENTS.md) and, more generally, any `file_residual` chunk at all: such a
chunk concatenates the *non-contiguous* spans left over after every symbol was
carved out of a file, so there is no reliable way to map a requested line
range onto it without risking a wrong line. `repo_read` refuses that trade
rather than making it silently; it works exactly against `symbol` chunks and
whole, symbol-free `file` chunks, which are both contiguous by construction.

### `repo_path_between`

**Purpose:** Answer "how does X reach Y" -- the one question a graph is
uniquely better at than grep, and the gap the project's own README comparison
table used to concede to other tools. Runs a bounded, **bidirectional** BFS
over the same `index.adj` structure `repo_neighbours` reads, so the real work
is roughly `max_hops / 2` layers deep on each side rather than `max_hops` deep
on one.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `from_id` | string (required) | Starting graph node id. |
| `to_id` | string (required) | Target graph node id. |
| `max_hops` | integer (optional) | Maximum path length in hops (default 6, clamped 1-8). |
| `edge_types` | array of string (optional) | Edge types to traverse (default `["CALLS", "DEFINES", "IMPORTS"]`). `CO_CHANGE` is a statistical correlation, not a call or definition path, and is only followed when named here explicitly. |
| `max_paths` | integer (optional) | Maximum distinct paths returned (default 3, clamped 1-10). |

**Output:** JSON with `from`, `to`, `max_hops`, `edge_types`, `truncated`, and
`paths` -- an array of paths, each an ordered array of
`{node_id, path, start_line, end_line, via_edge, confidence}` (the first step
of every path carries `via_edge: null, confidence: null`; every step after it
names the edge that reached it, e.g. `"CALLS out"`, and that edge's
`confidence`). A top-level `min_confidence` array gives the minimum confidence
along each returned path, in the same order -- a chain resting on a
1-of-3 ambiguous call reads visibly weaker than an exact one.

Two ids that exist but share no path within `max_hops` return `paths: []` and
a `message` explaining that, distinctly from an id that is not in the graph at
all (an `isError` result naming which one). `truncated: true` means the
bidirectional search gave up after visiting 4 000 nodes across both frontiers
before finding a meeting point -- distinct from simply running out of hops,
which is the ordinary "no path" case.

### `repo_blast_radius`

**Purpose:** Reverse reachability from a `node_id` -- what would break if it
changed. This is the tool the shipped Kubernetes example flow *"What is the
impact radius of changing the scheduler?"* deserved and did not have: the
reverse of `repo_neighbours`' one hop in both directions, specifically the
reverse *closure*, by hop distance.

> Ships under this name rather than `repo_impact`, which is already the
> PR/diff-impact tool documented above -- see the naming note near the top of
> this page.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `node_id` | string (required) | Graph node id to analyze (e.g. from `repo_find_symbol` or a `repo_search` citation). |
| `max_hops` | integer (optional) | Reverse-closure depth (default 3, clamped 1-6). |
| `include_cochange` | boolean (optional) | Include historically co-edited files (default `true`). |
| `limit` | integer (optional) | Maximum rows per section (default 40, clamped 1-50). |

**Output fields:**

| Field | Meaning |
|---|---|
| `callers` | Reverse `CALLS` closure -- callers, callers of callers, etc. -- each `{node_id, path, start_line, end_line, hop}`. |
| `subclasses` | Reverse `INHERITS` closure: subclasses, and their subclasses. |
| `importers` | Reverse `IMPORTS` closure on the node's *containing file*: files that import it, directly or transitively. |
| `cochange` | Files historically edited alongside this one, `{path, count}`, sorted by count. Omitted (`[]`) when `include_cochange` is `false`. Kept visually separate from the sections above because it is correlation the AST cannot see, never a call/definition/import edge. |
| `summary` | `{files_affected, symbols_affected, max_hop_reached, truncated}`. |

Each of `callers`/`subclasses`/`importers` is its own bounded reverse walk (up
to 4 000 visited nodes and `limit` rows), so a hub symbol that would reach
most of the graph reports `summary.truncated: true` rather than a partial set
presented as complete. `exclude_secrets` is unconditional, as everywhere else.

### `repo_impact`

**Purpose:** Analyze PR or git diff impact against a base branch using the code graph. Detects changed symbols, affected public APIs, impacted callers across depth hops, test coverage, and blast radius with grounded citations.

**Input parameters:**

| Parameter | Type | Meaning |
|---|---|---|
| `base` | string (optional) | Base branch or commit ref to compare against (default `"main"`). |
| `head` | string (optional) | Head branch or commit ref. Omitted: the **working tree** (committed and uncommitted changes) is compared against `base`, exactly like `repo2graph impact`. Given: the three-dot `base...head` comparison of two refs. |
| `diff` | string (optional) | Raw unified diff text. If provided, overrides git diff. |
| `max_depth` | integer (optional) | Caller traversal depth (default 2, clamped to 1-4). |
| `format` | string (optional) | Output format: `"markdown"` (default), `"pr-comment"`, `"json"` or `"sarif"` (SARIF v2.1.0). Anything else is an `isError` result listing these. |

`git diff` runs in the server's indexed repository — the `--repo` it was started
for, else the source root recorded in the index's `manifest.json`, else the index
directory's parent — never in the server process's working directory. When git
fails, git's own message is relayed (with every absolute path replaced by
`<repo>`/`<path>`) as an `isError` result; a repository whose default branch is
not `main` needs `base`.

Unconditionally filters secrets (`exclude_secrets=True`) and clamps numeric inputs. Detailed schemas, CLI flags, and CI recipes are documented in [PR_IMPACT.md](pr-impact.md).

**Output is bounded too, not just the inputs.** The report grows with the number
of impacted symbols rather than with `max_depth`, so a wide diff could render far
past the 12 000-token ceiling `repo_search` holds itself to. Over that ceiling:

- `markdown` and `pr-comment` are cut on a line boundary and end with a
  `_[truncated to 12000 tokens…]_` note.
- `json` and `sarif` are **not** cut — a line-boundary cut would stop being parseable. It is
  replaced by a valid document carrying `"truncated": true`, a `reason`, and the
  scalar summary (`risk_level`, `blast_radius_score`, `metrics`), with the
  per-symbol lists omitted.

Run `repo2graph impact` for the full, unbounded report; the ceiling exists
because this tool's output lands directly in an agent's context window.

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

A missing or unknown `task_id`, or a server started without `--async-build`,
returns the same JSON shape (`status: "unknown"` and an `error` sentence) with
`isError: true` -- there is no build to report, which is not a status.

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
  a chunk from a path that looks like a credential store. On the CLI, `query` and
  `rag` exclude them by default too, but `--include-secrets` turns that off; the
  MCP server has no such switch.
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

### What it does not promise: freshness

A long-running server does not watch the filesystem. `repo_map` compares the
index against the working tree (per-file sha256 from `index.state.json`,
same signal `repo2graph index-status`/`doctor` use) and prepends a `_note:`
line when they have diverged -- but that check only *runs* when `repo_map` is
called, and answers from a stale graph are still served in the meantime: a
`[cite: path:start-end]` citation from a stale index names real lines that
may hold different code. Treat a staleness note as "rebuild before trusting
citations," and `repo2graph index-status -o <out>` for the full diff of what
changed.
