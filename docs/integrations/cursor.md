# Cursor + repo2graph

The second-supported client. Same server, same six tools, same output — the
differences are the config file, the scope model, and how Cursor decides to call
a tool.

Read [Claude Code](claude-code.md) first if you have not: the tool reference,
the five starter questions and the "what not to rely on" list are there and are
not repeated here.

- [Install](#install)
- [Verify it worked](#verify-it-worked)
- [What differs from Claude Code](#what-differs-from-claude-code)
- [Telling Cursor when to use it](#telling-cursor-when-to-use-it)
- [Troubleshooting](#troubleshooting)

---

## Install

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

## Verify it worked

Cursor Settings → MCP should list `repo2graph` with its tools. Then in the chat
panel, with Agent mode on:

> Use repo_map to summarise this repository.

If the tool list is empty, the server did not start. If the tools are listed but
calls return nothing, the path argument is the usual cause — `repo2graph doctor .`
validates it.

## What differs from Claude Code

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

## Telling Cursor when to use it

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
- Blast radius of the current diff -> `repo_impact`.

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

## Troubleshooting

Everything in the [Claude Code troubleshooting table](claude-code.md#troubleshooting)
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

## See also

- [Claude Code](claude-code.md) — the tool reference and the starter questions
- [docs/mcp.md](../mcp.md) — per-platform config paths, including Claude Desktop and Windsurf
- [docs/OUTPUT_SCHEMA.md](../OUTPUT_SCHEMA.md) — what the citations and confidence values mean
