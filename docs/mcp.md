# MCP server

`repo2graph-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server over an
existing `.r2g` index, so an agent can ask the map questions itself instead of you
pasting a pack into a chat window. This page is its full contract: the nine tools,
their argument bounds, the server's own flags, and a config block per client.

It is an *additional* surface, not a replacement: every tool is a thin call into
`repo2graph.query.Index`, the same object the CLI and the GitHub Action use, over
the same artifacts.

> **Note**: Stdio mode is the primary transport for local AI developer tooling.
> Stdio mode has no listening socket.

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

Prefer `npx`? A thin `repo2graph-mcp` launcher package exists at
[`npm/`](../npm/README.md) in this repository — it resolves `uvx` (falling back to an installed
`repo2graph-mcp`, then `pipx`) and hands off to it, so the server itself stays this same Python
package, not a port. **It is not yet published to the npm registry**, so the `npx` form below does
not work yet; use the `uvx` command above until it is. See [`npm/README.md`](../npm/README.md) for
the resolution order and what it does when none of `uvx`/`repo2graph-mcp`/`pipx` is on `PATH`.

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
| `--async-build` | off | Build a missing index on a background thread and return a `task_id` immediately instead of blocking the first tool call. Poll it with `repo_build_status`. |
| `--cache-size` | `256` | Cached tool results before the least recently used is evicted. `0` disables the cache. |
| `--cache-ttl` | `60` | Seconds a cached result is served before it is recomputed. |

**Audit logging**

| Flag | Default | What it does |
| --- | --- | --- |
| `--audit-log` | stderr only | Append audit records to this file as well as stderr. |
| `--audit-log-level` | `errors` | `none`, `errors` or `all` — which tool calls produce a record. `all` also records every query's text. |
| `--audit-log-fsync` | off | Sync each record to disk before returning. Slower; stderr already carries every record, so this only hardens the file copy against a crash. |

## Tools

| Tool | Arguments | What comes back |
|---|---|---|
| `repo_map` | none | Languages, hub files and top entry points, prefixed with a staleness note (#383) if the working tree has changed since the index was built. Stable across calls, so it caches. Read this first. |
| `repo_search` | `query`, optional `k`, `hops`, `budget_tokens` | Seed chunks plus their graph neighbours, each block headed `[cite: path:start-end]`. |
| `repo_neighbours` | `node_id`, optional `hops`, `limit` | One graph hop from a node: callers, callees, base classes and the defining file, with edge direction. |
| `repo_find_symbol` | `name`, optional `kind`, `path_prefix`, `limit` | Name -> `node_id`(s): JSON array of `{node_id, name, qualname, kind, path, start_line, end_line, lang}`. |
| `repo_read` | `path`, optional `start_line`, `end_line`, `context` | A widened `[cite: path:start-end]` citation window, read from the index rather than the filesystem. |
| `repo_path_between` | `from_id`, `to_id`, optional `max_hops`, `edge_types`, `max_paths` | Bounded, bidirectional path(s) between two node_ids, with per-edge and minimum confidence. |
| `repo_blast_radius` | `node_id`, optional `max_hops`, `include_cochange`, `limit` | Reverse reachability from a node_id: callers, subclasses, importers and co-changed files, by hop distance. |
| `repo_cache_stats` | none | JSON object with cache metrics (hits, misses, size, etc.). |
| `repo_build_status` | `task_id` | JSON object with build task status, progress, and error details. |

The first eight answer questions about the code and exclude secrets
unconditionally. The last two report on the server itself, never read a chunk,
and are never served from the cache — a cached cache-stats or progress reading is
the one answer guaranteed to be out of date.

> **Naming note:** an earlier design for `repo_blast_radius` called it
> `repo_impact`, which at the time was taken by the PR/diff-impact tool. That
> tool has since been removed, but `repo_blast_radius` keeps its name: it
> answers a graph question ("what depends on this symbol") rather than a diff
> question, and renaming it now would break every client that already calls it.

### Tool annotations are honest about auto-build (#292)

MCP's `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` are
answered once, in `tools/list`, for the server's whole lifetime — there is no
per-call variant. So they can only be as honest as *this server instance*
allows:

- **An already-built index or `--no-auto-build`.** No tool
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

### Argument bounds

Every numeric argument is coerced and clamped **in the handler**, so `dispatch()`,
a direct Python caller and the stdio server all inherit the same bounds. Nothing
here raises on a bad value; a value below the minimum is raised to it, one above
the maximum is lowered to it, and a non-number (`"abc"`, `null` excepted,
non-finite) takes the default **and the reply starts with a one-line
`_note: k='abc' is not an integer; used the default 8._`** so the substitution is
visible. `budget_tokens` also treats zero or
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
| `query` (length) | `repo_search` | — | — | 4 000 chars |
| `node_id` (length) | `repo_neighbours`, `repo_path_between`, `repo_blast_radius` | — | — | 2 000 chars |
| `path` (length) | `repo_read` | — | — | 2 000 chars |
| `task_id` (length) | `repo_build_status` | — | — | 200 chars |

`repo_neighbours`, `repo_find_symbol`'s results, `repo_path_between` and
`repo_blast_radius` all take/return ids in the same shape the rest of the
project uses: `file:<path>`, `sym:<path>::<qualname>`, `dir:<path>`. Hand any
of them something else and it says so instead of returning nothing.

`repo_path_between`'s hop and visited-node ceilings are deliberately their own
constants, not a reuse of `repo_search`/`repo_neighbours`'s
`MCP_MAX_HOPS` (4) — a bidirectional path search does roughly `max_hops / 2`
layers of real work on each side, not `max_hops` deep on one, so the same
number does not mean the same cost.

### Errors are flagged, not just worded

A call the server cannot answer — a missing `query` or `node_id`, a `node_id`
that is not in the graph, an unknown tool name, a traversal whose `git diff`
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
`chunks.jsonl`, not disk: those chunks have passed secret-path exclusion, the
filesystem has not, and a served index may have no source tree beside it at all
(the `graph` branch / GitHub Action artifact case). This is what makes the tool
correct when the index is all you have.

Content redaction is applied **at serve time**, to the lines actually returned,
exactly as `repo_search` gets it from `Index._served`. Build-time redaction
cannot be relied on here: `--secret-policy off` and `warn-only` deliberately
store the text unredacted, and this tool previously handed that straight back
while `repo_search` over the same bytes redacted it. If the index manifest
cannot be read, the tool redacts rather than guessing.

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
([the invariants](../.github/CONTRIBUTING.md#architecture--os-compatibility-invariants)) and, more generally, any `file_residual` chunk at all: such a
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

> Named `repo_blast_radius` rather than `repo_impact` because that name
> belonged to the PR/diff-impact tool, since removed -- see the naming note
> near the top of this page.

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

## Per-client setup

### Claude Code

The first-supported client. Everything here is verified against Claude Code's
`claude mcp` command and the stdio server in `repo2graph/mcp/`.

- [Install](#install)
- [Verify it worked](#verify-it-worked)
- [The nine tools](#the-nine-tools)
- [First questions worth asking](#first-questions-worth-asking)
- [Telling the agent how to use it](#telling-the-agent-how-to-use-it)
- [Troubleshooting](#troubleshooting)
- [What not to rely on](#what-not-to-rely-on)

---

#### Install

One command. `--` separates Claude Code's flags from the server's.

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /absolute/path/to/project
```

Three details account for most failed setups, and `repo2graph doctor` checks
all three for you:

| Detail | Why it matters |
|---|---|
| `--from "repo2graph[mcp]"` | Without the `[mcp]` extra the server has no SDK to start against. `uvx --from repo2graph repo2graph-mcp` installs and then exits telling the client to install an SDK, which the client reports only as "server failed to start". |
| An **absolute** path | MCP clients launch servers from an unspecified working directory. A relative path resolves somewhere you did not mean. |
| Valid JSON, if editing a config file by hand | A trailing comma also surfaces as "server failed to start", with no mention of JSON. |

### Scope

`claude mcp add` defaults to local (this project only). To make it available
across projects:

```bash
claude mcp add --scope user repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /absolute/path/to/project
```

A user-scope server is pinned to **one** repository path. If you work across
several repositories, add one server per repo with distinct names
(`repo2graph-api`, `repo2graph-web`) rather than trying to make one serve all
of them — the server indexes the path it was given.

### If you installed with pip instead of uv

```bash
pip install "repo2graph[mcp]"
claude mcp add repo2graph -- repo2graph-mcp /absolute/path/to/project
```

### No pre-build needed

The server builds its own index on the first tool call if one does not exist.
On a large repository that first call takes as long as a build would
(see [benchmarks](architecture.md): 34s for Django's 5,629 files), and the
client may time it out. Two ways to avoid that:

```bash
### Build ahead of time
repo2graph build /absolute/path/to/project -o /absolute/path/to/project/.r2g

### Or let the server build in the background and poll
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path --async-build
```

With `--async-build` the first call returns a task id; `repo_build_status`
reports progress. Use `--no-auto-build` if you would rather the server refuse
than build unasked.

#### Verify it worked

```bash
claude mcp list
```

Then, in Claude Code:

> Use repo_map to summarise this repository.

If that returns languages and hub files, the wiring is good. If anything is
off, one command tells you which of the three failure modes you hit:

```bash
repo2graph doctor .
```

Its **MCP Client Configuration** check reads Claude Code's own config, finds
the repo2graph entry, and names the problem — command not on `PATH`, missing
`[mcp]` extra, relative or non-existent path, or unparseable JSON. It never
echoes an entry's `env` values.

#### The nine tools

| Tool | Use it for | Bounds |
|---|---|---|
| `repo_map` | orientation: languages, hub files, entry points | — |
| `repo_search` | "where is X handled?" — BM25 seeds expanded one hop through the graph | `k` ≤ 50, `hops` ≤ 4, budget ≤ 12,000 tokens (default 6,000) |
| `repo_neighbours` | "what calls this?" — from a known `node_id` | `hops` ≤ 4, `limit` ≤ 50 |
| `repo_find_symbol` | name → `node_id`, when you already know what you're looking for | `limit` ≤ 50 |
| `repo_read` | widen a `[cite: path:start-end]` citation into more source lines | `context` ≤ 500 lines each side |
| `repo_path_between` | "how does X reach Y" — a bounded, bidirectional path search | `max_hops` ≤ 8, `max_paths` ≤ 10 |
| `repo_blast_radius` | reverse reachability from a `node_id` — what depends on it | `max_hops` ≤ 6, `limit` ≤ 50 |
| `repo_cache_stats` | diagnostics: cache hits/misses | — |
| `repo_build_status` | progress of an `--async-build` index | — |

Every numeric argument is clamped **in the handler**, so a model that asks for
`hops: 99` gets 4 rather than an error or a 200,000-character reply. Every tool
that reads repository content passes `exclude_secrets=True` unconditionally: a
human running the CLI can choose to see a `.env`, an agent tool returning one
is a different class of problem.

`repo_find_symbol`, `repo_read`, `repo_path_between` and `repo_blast_radius`
were added after the original six to close round-trips the first set left an
agent doing by hand: looking up a `node_id` by name instead of a `repo_search`
detour, widening a citation without touching the filesystem, tracing a call
chain between two known symbols, and reverse-closure "what breaks if I change
this" in one call instead of walking `repo_neighbours` repeatedly.

### The output is markdown, not JSON

Every tool returns a string. There is no per-result `path` field to parse — the
citation is in the text, in a stable form:

```
- CALLS in: `check_index_freshness` (repo2graph/doctor.py:1051) [sym:repo2graph/doctor.py::check_index_freshness]  -- at repo2graph/doctor.py:1075
```

Three things in that line, and the distinction matters:

- `(repo2graph/doctor.py:1051)` — where the **neighbour** is defined.
- `[sym:...]` — the node id, which is the argument for the next
  `repo_neighbours` call.
- `-- at repo2graph/doctor.py:1075` — where the **call is written**. For "what
  calls this", this is the line you actually want to open.

An edge repo2graph is unsure of says so:

```
- CALLS out: `ResultCache.get` (repo2graph/cache.py:128) [sym:...]  -- at repo2graph/status.py:124, AMBIGUOUS 0.5 of 3 candidates
```

That is a `.get()` on a dictionary that matched three methods named `get`.
Treat an `AMBIGUOUS` edge as a lead, not a fact.

#### First questions worth asking

Paste any of these into Claude Code once the server is connected. They are the
same five the bundled `repo2graph demo` answers, so you can see each one
working on a fixture before trying it on your own code.

| Ask | What the graph adds over a text search |
|---|---|
| `Where is authentication enforced?` | the guard itself, plus the routes that call it |
| `What calls <function>?` | CALLS edges into it, each with a confidence score |
| `What tests cover <module>?` | IMPORTS edges from the test module back to the code under test |
| `What would be affected by changing <api>?` | the definition, then its direct callers from the CALLS edges into it (`repo_neighbours`) |
| `Trace <a request> from route to persistence.` | a path across modules, each block cited to file and line |

```bash
uvx repo2graph demo    # watch all five answered, no repo needed
```

#### Telling the agent how to use it

Adding the server makes the tools available; it does not tell the agent when to
prefer them. A short `CLAUDE.md` block does, and it is the difference between
an agent that uses the graph and one that keeps grepping:

```markdown
#### Code navigation

Use grep to locate code; use the repo2graph MCP tools for relationships:

- `repo_search` for a cited, budget-bounded pack when a question spans several files.
- `repo_neighbours` for "what calls this" / "what would this break" — pass the
  `[sym:...]` node id from a previous result.
- Cite the `path:line` from the tool output in your answer. If a tool marks an
  edge `AMBIGUOUS`, say so rather than asserting the target.
- An absent edge is not proof of an absent call: dynamic dispatch, reflection
  and DI containers produce no edges. Fall back to grep for those.
```

That last line matters more than it looks. Without it, an agent that trusts the
graph completely will confidently report "nothing calls this" about a function
reached through a plugin registry.

#### Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| "server failed to start" | missing `[mcp]` extra, or invalid JSON in the config | `repo2graph doctor .` → MCP Client Configuration |
| Tools listed but return nothing | the server's path argument does not exist, or is relative | same check; it validates the path |
| First call times out | auto-build on a large repo | pre-build, or add `--async-build` |
| Answers cite code you deleted | stale index | `repo2graph index-status -o .r2g` |
| Answers are vague and repetitive | generated code dominating retrieval | `repo2graph doctor .` → Generated / Vendored Code prints the `--exclude-group` flags |
| A whole language returns no symbols | grammar not loading | `repo2graph doctor .` → Parser Coverage |

Still stuck? `repo2graph bug-report --category answer-unhelpful` assembles a
bundle that is safe to paste into a public issue: no file content, no
environment variable values, no absolute paths, and no file paths at all unless
you pass `--include-paths`.

#### What not to rely on

- **Completeness of callers.** Call resolution is name-based. Across the five
  benchmark repositories, 4.6%–21.3% of `CALLS` edges are ambiguous
  ([limitations](architecture.md)). No edge does not prove no call.
- **Freshness.** The index is a snapshot and nothing watches the filesystem.
  The server rebuilds when the index is missing, not when it is stale.
- **Generated code being marked.** It is indexed exactly like hand-written
  code.
- **The graph as a substitute for running the code.** An edge says a name
  resolves; it does not say the line executes.

Full list: [architecture.md](architecture.md).

#### See also

- [Cursor](#cursor) — the same server, different config file
- [docs/architecture.md](architecture.md) — what `confidence` and `evidence` mean

### Cursor

The second-supported client. Same server, same nine tools, same output — the
differences are the config file, the scope model, and how Cursor decides to call
a tool.

Read [Claude Code](#claude-code) first if you have not: the tool reference,
the five starter questions and the "what not to rely on" list are there and are
not repeated here.

- [Install](#install)
- [Verify it worked](#verify-it-worked)
- [What differs from Claude Code](#what-differs-from-claude-code)
- [Telling Cursor when to use it](#telling-cursor-when-to-use-it)
- [Troubleshooting](#troubleshooting)

---

#### Install

Cursor reads MCP servers from a JSON file. Project scope:

**`.cursor/mcp.json`** in the repository root

```json
{
  "mcpServers": {
    "repo2graph": {
      "command": "uvx",
      "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", "/absolute/path/to/project"]
    }
  }
}
```

For every project, use `~/.cursor/mcp.json` with the same block — but note that
the path argument pins the server to one repository, so a global entry is only
useful if you mostly work in one codebase. Otherwise prefer project scope, one
entry per repo.

The same three failure modes as any MCP client apply, and are worth repeating
because Cursor surfaces all of them as the same red dot:

- `--from "repo2graph[mcp]"`, not `--from repo2graph` — without the extra the
  server exits asking for an SDK.
- An **absolute** path. Cursor launches servers from an unspecified working
  directory.
- Valid JSON. A trailing comma in `.cursor/mcp.json` is the single most common
  cause of "server failed to start".

`repo2graph doctor .` checks all three, and it reads `.cursor/mcp.json` at both
project and user scope.

### If you committed `.cursor/mcp.json`

An absolute path in a committed config is wrong for every other contributor —
and on a public repo it leaks your directory layout. Either:

- add `.cursor/mcp.json` to `.gitignore` and let each contributor point it at
  their own checkout, or
- commit it with a placeholder path and a line in `CONTRIBUTING.md` telling
  people to substitute theirs.

There is no repo-relative form that works here; the server needs a real path at
launch.

#### Verify it worked

Cursor Settings → MCP should list `repo2graph` with its tools. Then in the chat
panel, with Agent mode on:

> Use repo_map to summarise this repository.

If the tool list is empty, the server did not start. If the tools are listed but
calls return nothing, the path argument is the usual cause — `repo2graph doctor .`
validates it.

#### What differs from Claude Code

| | Claude Code | Cursor |
|---|---|---|
| Config | `claude mcp add` (CLI) | `.cursor/mcp.json` (hand-edited) |
| Scope | `--scope local` / `user` / `project` | project file, or `~/.cursor/mcp.json` |
| Tool invocation | reliably calls tools described in `CLAUDE.md` | needs Agent mode; a plain chat turn may not call tools at all |
| Steering file | `CLAUDE.md` | `.cursor/rules/*.mdc` |
| Visible failure | names the server and error | a red dot in Settings → MCP |

The practical consequence: **Cursor needs more explicit steering.** Claude Code
will follow a prose instruction in `CLAUDE.md` to prefer a tool; Cursor's
built-in codebase search is good enough that, without a rule, it will often
answer from its own index and never call repo2graph at all. That is not a
defect in either tool — but it means the rules file below is load-bearing here
in a way the `CLAUDE.md` block is not.

#### Telling Cursor when to use it

**`.cursor/rules/repo2graph.mdc`**

```markdown
---
description: Prefer repo2graph's graph tools for call-relationship questions
alwaysApply: true
---

Cursor's own codebase search is good at "find text like X". It does not model
call relationships. For those, use the repo2graph MCP tools:

- "what calls this" / "what would break if I change this" -> `repo_neighbours`,
  passing the `[sym:...]` node id from a previous result.
- "where is X handled" -> `repo_search`. It returns cited source, not file paths.
- Blast radius of a symbol -> `repo_blast_radius`, which walks the reverse
  closure of a node. There is no diff-level tool: that surface was removed.

Quote the `path:line` from the tool output. If the output marks an edge
`AMBIGUOUS`, report it as uncertain rather than asserting the target.

An absent edge is not proof of an absent call: dynamic dispatch, reflection and
DI containers produce no edges. Use Cursor's own search for those.
```

The division of labour that works: **Cursor's semantic search for "find me
something like this", repo2graph for "what is connected to this".** Framing the
rule as a split rather than a replacement gets it followed more often, and it is
also the honest description — `repo_search` runs BM25 as its first step and does
not claim to beat an embedding index at fuzzy recall.

#### Troubleshooting

Everything in the [Claude Code troubleshooting table](#troubleshooting)
applies. Cursor-specific:

| Symptom | Cause | Fix |
|---|---|---|
| Tools listed, never called | no rules file, or Agent mode off | add `.cursor/rules/repo2graph.mdc`; enable Agent mode |
| Works for you, broken for a teammate | absolute path in a committed `.cursor/mcp.json` | gitignore it, or use a documented placeholder |
| Red dot, no detail | Cursor does not surface the server's stderr | run the command by hand: `uvx --from "repo2graph[mcp]" repo2graph-mcp /path` — the error appears there |
| Server starts, first call hangs | auto-build on a large repo | pre-build, or add `--async-build` to `args` |

Running the server by hand is the fastest diagnostic Cursor gives you, because
it shows the stderr the UI hides. A healthy server starts and waits silently on
stdin; press Ctrl-C.

#### See also

- [Claude Code](#claude-code) — the tool reference and the starter questions
- [docs/architecture.md](architecture.md) — what the citations and confidence values mean
