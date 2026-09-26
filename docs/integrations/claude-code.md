# Claude Code + repo2graph

The first-supported client. Everything here is verified against Claude Code's
`claude mcp` command and the stdio server in `repo2graph/mcp.py`.

- [Install](#install)
- [Verify it worked](#verify-it-worked)
- [The six tools](#the-six-tools)
- [First questions worth asking](#first-questions-worth-asking)
- [Telling the agent how to use it](#telling-the-agent-how-to-use-it)
- [Troubleshooting](#troubleshooting)
- [What not to rely on](#what-not-to-rely-on)

---

## Install

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
(see [benchmarks](../PERFORMANCE.md): 34s for Django's 5,629 files), and the
client may time it out. Two ways to avoid that:

```bash
# Build ahead of time
repo2graph build /absolute/path/to/project -o /absolute/path/to/project/.r2g

# Or let the server build in the background and poll
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path --async-build
```

With `--async-build` the first call returns a task id; `repo_build_status`
reports progress. Use `--no-auto-build` if you would rather the server refuse
than build unasked.

## Verify it worked

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

## The six tools

| Tool | Use it for | Bounds |
|---|---|---|
| `repo_map` | orientation: languages, hub files, entry points | — |
| `repo_search` | "where is X handled?" — BM25 seeds expanded one hop through the graph | `k` ≤ 50, `hops` ≤ 4, budget ≤ 12,000 tokens (default 6,000) |
| `repo_neighbours` | "what calls this?" — from a known `node_id` | `hops` ≤ 4, `limit` ≤ 50 |
| `repo_impact` | blast radius of a diff against a base branch | — |
| `repo_cache_stats` | diagnostics: cache hits/misses | — |
| `repo_build_status` | progress of an `--async-build` index | — |

Every numeric argument is clamped **in the handler**, so a model that asks for
`hops: 99` gets 4 rather than an error or a 200,000-character reply. All three
retrieval tools pass `exclude_secrets=True` unconditionally: a human running
the CLI can choose to see a `.env`, an agent tool returning one is a different
class of problem.

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

## First questions worth asking

Paste any of these into Claude Code once the server is connected. They are the
same five the bundled `repo2graph demo` answers, so you can see each one
working on a fixture before trying it on your own code.

| Ask | What the graph adds over a text search |
|---|---|
| `Where is authentication enforced?` | the guard itself, plus every route that calls it |
| `What calls <function>?` | CALLS edges in, so callers come back even when the name is shadowed |
| `What tests cover <module>?` | IMPORTS edges from the test module back to the code under test |
| `What would be affected by changing <api>?` | direct callers and what they are called from |
| `Trace <a request> from route to persistence.` | a path across modules, each block cited to file and line |

```bash
uvx repo2graph demo    # watch all five answered, no repo needed
```

## Telling the agent how to use it

Adding the server makes the tools available; it does not tell the agent when to
prefer them. A short `CLAUDE.md` block does, and it is the difference between
an agent that uses the graph and one that keeps grepping:

```markdown
## Code navigation

Use the repo2graph MCP tools before grepping:

- `repo_search` for "where is X handled" — it returns cited source, not paths.
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

## Troubleshooting

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

## What not to rely on

- **Completeness of callers.** Call resolution is name-based. Across the five
  benchmark repositories, 4.6%–21.3% of `CALLS` edges are ambiguous
  ([limitations](../limitations.md)). No edge does not prove no call.
- **Freshness.** The index is a snapshot and nothing watches the filesystem.
  The server rebuilds when the index is missing, not when it is stale.
- **Generated code being marked.** It is indexed exactly like hand-written
  code.
- **The graph as a substitute for running the code.** An edge says a name
  resolves; it does not say the line executes.

Full list: [POSITIONING.md §5](../../POSITIONING.md).

## See also

- [Cursor](cursor.md) — the same server, different config file
- [docs/mcp.md](../mcp.md) — the tool reference and per-platform config paths
- [docs/OUTPUT_SCHEMA.md](../OUTPUT_SCHEMA.md) — what `confidence` and `evidence` mean
