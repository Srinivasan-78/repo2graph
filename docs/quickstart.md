# 2-minute quickstart

From nothing installed to a cited answer about your own code. No API key, no
configuration file, no network call.

- [Minute 1 — see it work](#minute-1--see-it-work)
- [Minute 2 — point it at your code](#minute-2--point-it-at-your-code)
- [The five starter questions](#the-five-starter-questions)
- [Wire it into an agent](#wire-it-into-an-agent)
- [When something goes wrong](#when-something-goes-wrong)

---

## Minute 1 — see it work

`repo2graph demo` writes a small bundled repository to a scratch directory,
indexes it, and answers the five starter questions against it. It needs
nothing from you but Python 3.10+.

```bash
uvx repo2graph demo
```

or, if you would rather install it first:

```bash
pip install repo2graph
repo2graph demo
```

Expected output — a build, then five answers, each one citing files and line
ranges you can open:

```
1/3  Writing the bundled demo repository to /tmp/r2g-demo-a1b2c3
     9 files: README.md, app/__init__.py, app/auth.py, app/billing.py, app/routes.py,
     app/service.py, app/store.py, tests/test_orders.py, web/client.js

2/3  Building the graph (no network, no API key, no config file)
     8 files parsed -> 67 nodes, 117 edges, 43 chunks

3/3  Five questions you can copy onto your own repository

  --- 1/5  Where is authentication enforced?
      (finds the guard itself, plus every route that calls it)
      $ repo2graph rag "Where is authentication enforced?" -o /tmp/r2g-demo-a1b2c3/.r2g

      6 cited blocks, 5929 chars:
        app/auth.py:1-43             app/auth.py                    seed
        app/routes.py:1-47           app/routes.py                  seed
        app/store.py:1-53            app/store.py                   seed
        ...

      [cite: app/auth.py:1-43]
      | # file: app/auth.py (python, 43 lines)
      | # defines: AuthError, parse_token, require_token, lookup_principal
      | """Authentication and authorisation for the orders API.
      |
      | This module is where authentication is enforced: `require_token` is the
      | single guard every route calls before any handler body runs.
      | """
```

The part worth slowing down on is the fifth answer, where the graph does
something grep cannot:

```
  --- 5/5  Trace an order request from route to persistence.

      [cite: app/auth.py:27-37]
      | # function: require_token  (lines 27-37, python)
      | # called by: app/routes.py::create_order, app/routes.py::get_order,
      |              app/routes.py::refund_order,
      |              tests/test_orders.py::test_require_token_rejects_a_missing_header
      | # calls: app/auth.py::parse_token, app/auth.py::lookup_principal
```

Those `called by` and `calls` lines are edges, not text matches. They are why
the answer knows the guard runs on three routes without anyone having written
that down.

The scratch repository is deleted on the way out. Keep it to poke at:

```bash
repo2graph demo --out ./r2g-demo    # writes there and keeps it
repo2graph demo --full              # print every answer in full, not the first lines
```

## Minute 2 — point it at your code

```bash
cd /path/to/your/project
repo2graph build . -o .r2g
```

Expected output — a JSON summary on stdout, and `.r2g/` next to your code:

```json
{
  "out": ".r2g",
  "written": ["agent/nodes.jsonl", "agent/edges.jsonl", "agent/chunks.jsonl",
              "human/graph.html", "human/overview.md", "agent/manifest.json"],
  "stats": {"files": 227, "parsed": 80, "nodes": 2552, "edges": 11118},
  "chunks": 3341
}
```

A few hundred files takes a few seconds. Then ask it something:

```bash
repo2graph rag "how does routing match a path" -o .r2g
```

You get markdown: a repo map, a `---`, then `### [cite: path:start-end]`
blocks of real source. Paste it into any model, or read it yourself.

Two things to know before you trust the output:

- **`repo2graph rag` never calls an LLM.** It assembles context and prints it.
  Only `--answer` sends anything anywhere, and it prints the provider and
  hostname to stderr before the first byte — see
  [the `--answer` section of docs/cli.md](cli.md#-answer-sends-your-code-to-someone-elses-computer).
- **`.r2g/` goes in `.gitignore`** unless you want the graph committed. The
  [GitHub Action](github-action.md) is the supported way to keep a fresh one
  in the repository without doing it by hand.

Open `.r2g/human/graph.html` in a browser for the map.

## The five starter questions

These are the five that show what a graph gives you over a text search. The
left column is the copy-paste form for your own repository; run
`repo2graph demo` to watch each one answered against the bundled fixture.

| # | Ask your repo | What comes back that grep cannot give you |
|---|---|---|
| 1 | `Where is authentication enforced?` | the guard itself, plus every route that calls it |
| 2 | `What calls <function>?` | CALLS edges in, so callers come back even when the name is shadowed |
| 3 | `What tests cover <module>?` | IMPORTS edges from the test module back to the code under test |
| 4 | `What would be affected by changing <api>?` | the blast radius: direct callers and what they are called from |
| 5 | `Trace <a request> from route to persistence.` | a whole path across modules, each block cited to file and line |

Run any of them like this:

```bash
repo2graph rag "What calls parse_formats?" -o .r2g
repo2graph rag "What would be affected by changing pack_context?" -o .r2g --budget 12000
```

Question 4 is the one to reach for before a refactor; question 5 is the one
to reach for on your first day in an unfamiliar codebase.

## Wire it into an agent

One command, and Claude Code can ask the graph directly:

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /absolute/path/to/project
```

For Claude Desktop (`claude_desktop_config.json`) or Cursor
(`.cursor/mcp.json`), the same thing as a block:

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

Three details that account for most failed MCP setups, all of which
`repo2graph doctor` checks for you:

- **`--from "repo2graph[mcp]"`, not `--from repo2graph`.** Without the `[mcp]`
  extra the server has no SDK to start against, and the client reports only
  "server failed to start".
- **An absolute path.** MCP clients launch servers from an unspecified
  working directory, so a relative path resolves somewhere you did not mean.
- **Valid JSON.** A trailing comma in `claude_desktop_config.json` also
  surfaces as "server failed to start", with no mention of JSON.

The server builds its own index on the first call if one does not exist yet.
Full per-client config file locations: [docs/mcp.md](mcp.md).

## When something goes wrong

Run the diagnostic first. It checks the environment, the index and the client
wiring, and prints a remediation line for anything it finds:

```bash
repo2graph doctor .
repo2graph doctor . --json     # same report, machine-readable, for CI
```

It exits `0` when every check passes or only warns, and `1` when a check
fails outright.

| Symptom | What `doctor` says | Fix |
|---|---|---|
| `uvx repo2graph` — command not found | `uv / pip Availability: neither uv nor pip is available` | install uv, or use `pip install repo2graph` |
| `no index at .r2g` | `Index Freshness: no index found` | `repo2graph build . -o .r2g` |
| Answers cite code you deleted | `Index Freshness: index is out of date: 3 modified` | `repo2graph build . -o .r2g --incremental` |
| Answers are vague and repetitive | `Generated / Vendored Code: 412 of 900 indexed files look generated` | rebuild with the `--exclude` globs it prints |
| A whole language returns nothing | `Parser Coverage: 91 file(s) produced parse errors` | `pip install --upgrade tree-sitter-language-pack` |
| Your source is missing from answers | `Ignored Paths: 1,204 of 1,300 candidate files were skipped` | `repo2graph explain-path <path> -r .` names the one rule that excluded it |
| MCP server "failed to start" | `MCP Client Configuration: ... without the [mcp] extra` | use `--from "repo2graph[mcp]"` |
| MCP tools return nothing | `MCP Client Configuration: ... target path does not exist` | use an absolute path to the repository |
| Windows `charmap` codec errors | `Platform & Encoding: cp1252` | already handled — report it if you still see one |

Three commands answer most "why did it do that" questions without reading any
source:

```bash
repo2graph explain-path src/generated/api.py -r .   # why a file is in or out
repo2graph explain node "sym:app/auth.py::require_token" -o .r2g
repo2graph stats -o .r2g                            # what the index actually contains
```

Still stuck? Open an issue with the output of `repo2graph doctor . --json`
attached — it names versions, grammars and artifact state, and it never
prints a secret value or an environment variable's contents.

## Where to go next

- [docs/cli.md](cli.md) — every flag, and the two `--budget` units
- [docs/mcp.md](mcp.md) — the MCP tools and per-client configuration
- [docs/github-action.md](github-action.md) — a fresh graph on every push
- [docs/limitations.md](limitations.md) — what the graph does not model
