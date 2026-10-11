# DRAFT — MCP directory and awesome-list entries

**Status: draft for the maintainer to submit. No pull request has been opened against any other
repository, and no directory form has been filled in.**

Already done in this repository: `server.json` (official MCP registry metadata, name
`io.github.Srinivasan-78/repo2graph`), the `mcp-name:` marker in `README.md`, and the Glama listing
(`glama.json`; Quality Score link in `pyproject.toml`).

Every claim in the entries below is checkable: nine MCP tools (`docs/mcp.md`, "The nine tools"),
the 12,000-token ceiling (`repo2graph/mcp/guardrails.py`), no network on the default path
(`README.md`), MIT licence (`LICENSE`).

## awesome-mcp-servers style lists

Most of these lists use one line per server, grouped by category, often with language and scope
emoji legends. Read the target list's CONTRIBUTING file first and match its exact format; the
category is usually **"Coding Agents"**, **"Developer Tools"** or **"Code Analysis"**, whichever
the list has.

Plain one-liner:

```markdown
- [Srinivasan-78/repo2graph](https://github.com/Srinivasan-78/repo2graph) - Code graph over MCP: ask a repository a question and get back the source that answers it, every block cited to file:line, under a hard token ceiling. tree-sitter for 19 languages, git co-change edges, no model, no API key, no network.
```

With the common legend (🐍 Python implementation, 🏠 local service, 🍎 🪟 🐧 platforms):

```markdown
- [Srinivasan-78/repo2graph](https://github.com/Srinivasan-78/repo2graph) 🐍 🏠 🍎 🪟 🐧 - Code graph over MCP: cited, token-bounded answers about a codebase (callers, callees, blast radius, cited source packs). tree-sitter, 19 languages, no model or network on the default path.
```

## Directories with a form or a JSON entry

| Field | Value |
|---|---|
| Name | repo2graph |
| Short description (≤ 100 chars) | Ask a codebase a question, get back the source that answers it — every block cited to file:line. |
| Long description | repo2graph parses a repository with tree-sitter into a graph of calls, imports, inheritance and git co-change, and serves it to coding agents over MCP. `repo_search` returns cited source packed under a hard token ceiling; `repo_neighbours` lists a symbol's callers, callees and base classes; `repo_blast_radius` walks what depends on a symbol. Local, no model, no API key, no database, no network calls on the default path. Call resolution is name-based, not type-based. |
| Category | Developer tools / Coding agents / Code analysis |
| Install | `claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .` |
| Transport | stdio |
| Language | Python (3.10–3.13) |
| Licence | MIT |
| Repository | https://github.com/Srinivasan-78/repo2graph |
| Docs | https://github.com/Srinivasan-78/repo2graph/blob/main/docs/mcp.md |
| Registry name | `io.github.Srinivasan-78/repo2graph` |

## Do not write

- "Better than grep" or "more accurate than X" — see `.github/distribution.md`, Claims discipline.
- "Supports every language" — 19 have symbol extraction; two are benchmarked on real repositories.
