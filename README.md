<div align="center">

# repo2graph

**Give coding agents trustworthy, cited answers about unfamiliar codebases.**

[![PyPI](https://img.shields.io/pypi/v/repo2graph.svg?color=blue&label=PyPI)](https://pypi.org/project/repo2graph/)
[![CI](https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml/badge.svg)](https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

<img src="docs/images/demo.gif" alt="repo2graph building a map of a repository, then answering a question about it, in a terminal" width="800" />

</div>

<!-- mcp-name: io.github.Srinivasan-78/repo2graph -->

Ask a repository a question and get back the source that answers it. Every block is headed
`[cite: path:start-end]`, and the whole reply fits under a token ceiling that the tool enforces.
One tree-sitter pass records who calls whom, who imports what, and which files keep changing
together in git. Retrieval starts from BM25 matches and follows those links.

It needs no model, no API key, no language server and no database, and it makes no network
calls. Use it from the CLI, as an MCP server in Claude Code or Cursor, or as a GitHub Action.

## Try it

```bash
uvx repo2graph demo                                   # bundled example repo, five questions answered
uvx repo2graph build . -o .r2g                        # index your own code (seconds, not minutes)
uvx repo2graph rag "how does routing work" -o .r2g    # a cited, budget-bounded context pack
```

Give an agent the same thing over MCP:

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .
```

Cursor, Claude Desktop and other clients: [docs/mcp.md](docs/mcp.md). Step-by-step with expected
output: [docs/quickstart.md](docs/quickstart.md).

## Is it better than grep?

**Not at finding code, yet.** We measured it on 35 questions about Flask, requests, FastAPI and
Hono, scored against the definitions that answer them, with both tools held to the same token
budget ([method, per-question results, reproduction](docs/retrieval-benchmark.md)):

| Budget | repo2graph | grep, then read around the hits |
|---:|---:|---:|
| 2,000 tokens | 40% | 38% |
| 4,000 tokens | 46% | **62%** |
| 8,000 tokens | 60% | **73%** |

The causes are ranking problems. Whole-file and whole-class chunks win the seed ranking and use
up the budget, and graph expansion doesn't yet follow the edge direction the question asks for.
They're diagnosed in the benchmark write-up and are the next thing to fix.

What it does do that grep doesn't:

- **Relationships in one hop.** `repo_neighbours` returns a symbol's callers, callees, base
  classes and defining file, each with a line number. Every edge carries a `confidence`: a name
  that could mean several definitions is marked `ambiguous` and priced at `1/n`, not guessed.
- **A hard ceiling.** The pack is measured, clamped and re-measured before it's returned
  (12k tokens max over MCP), so an agent can't flood its own context through this tool.
- **PR blast radius in CI.** `repo2graph impact` reports what a diff touches: callers,
  importers, subclasses, and the files git history says usually change alongside it
  (`CO_CHANGE`).

How it compares with Serena, Aider's repo map, CodeGraphContext, code-graph-rag, Sourcegraph,
Cursor's index and Claude Code's own search, including when to use those instead:
**[docs/comparison.md](docs/comparison.md)**.

## Five questions to start with

| Ask your repo | What comes back that grep cannot give you |
|---|---|
| `Where is authentication enforced?` | the guard itself, plus every route that calls it |
| `What calls <function>?` | CALLS edges in, so callers come back even when the name is shadowed |
| `What tests cover <module>?` | IMPORTS edges from the test module back to the code under test |
| `What would be affected by changing <api>?` | the blast radius: direct callers and what they are called from |
| `Trace <a request> from route to persistence.` | a whole path across modules, each block cited to file and line |

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful

- uses: Srinivasan-78/repo2graph@v2
  with:
    git-history: "500"
    commit-branch: graph     # optional: publish graph.html to a browsable branch
```

`@v2` follows every 2.x release; pin an exact tag (`@v2.1.0`) to upgrade by hand. The Action never
calls an LLM. Inputs, outputs and the PR-impact workflow: [docs/github-action.md](docs/github-action.md),
[docs/pr-impact.md](docs/pr-impact.md).

## Commands

| Command | Does |
|---|---|
| `repo2graph build <path> -o .r2g` | Parse a repo into a graph and chunks (`--incremental`, `--git-history N`) |
| `repo2graph query "<q>" -o .r2g` | BM25 search plus one graph hop |
| `repo2graph rag "<q>" -o .r2g` | Budget-bounded, cited context pack (`--answer` sends it to an LLM: opt-in, the only network path) |
| `repo2graph impact -i .r2g --base main` | Blast radius of a diff |
| `repo2graph explain <edge\|node\|retrieval>` | Why an edge exists, or why a block was retrieved |
| `repo2graph github <owner/repo> -o <dir>` | Fetch, build and clean up without a local clone |
| `repo2graph demo` | Index a bundled example and answer the five questions above |
| `repo2graph map`, `repo2graph stats`, `repo2graph index-status`, `repo2graph embed` | Re-render `graph.html`, report counts and freshness, add optional dense vectors |
| `repo2graph doctor`, `repo2graph bug-report`, `repo2graph explain-path`, `repo2graph completion` | Diagnose setup, build a privacy-safe bug bundle, say why a path is (not) indexed, shell completion |
| `repo2graph-mcp <path>` | stdio MCP server: `repo_map`, `repo_search`, `repo_neighbours`, `repo_impact`, and two status tools |

Full flags: [docs/cli.md](docs/cli.md). Python API: [docs/python-api.md](docs/python-api.md).

## What it can't do

- **Resolve calls by type.** Calls are matched by name, scoped by class, file, imports and
  directory. `x.get()` on a receiver of unknown type is recorded as a low-confidence guess, not a
  fact. For exact references, use a language-server tool.
- **See dynamic dispatch, reflection or computed imports.** A missing edge doesn't prove that no
  call exists.
- **Cross language boundaries** (Python calling C++ through bindings), or notice that your files
  changed since the last build.

Symbols, calls and classes are extracted for Python, JS, TS, TSX, Go, Rust, Java, Ruby, C, C++,
C#, PHP, Kotlin, Swift, Scala, Bash and Lua. Every other file is still indexed as text. Measured
rates for each limitation: [docs/limitations.md](docs/limitations.md).

## Status

2.x is stable. The CLI, the MCP tools and the output schema follow semver, and breaking changes
wait for 3.0. Default paths run locally, send no telemetry and exclude secrets from agent replies
unconditionally ([privacy](docs/PRIVACY.md), [threat model](docs/THREAT_MODEL.md),
[security policy](.github/SECURITY.md)). A Docker image for read-only, non-root deployments is
described in [docs/ENTERPRISE_DEPLOYMENT.md](docs/ENTERPRISE_DEPLOYMENT.md).

## Contributing

```bash
git clone https://github.com/Srinivasan-78/repo2graph && cd repo2graph
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make lint format-check typecheck test
```

Branch from `develop`. Start with [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md),
[docs/good-first-issues.md](docs/good-first-issues.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The most useful contribution right now is new
questions for the [retrieval benchmark](docs/retrieval-benchmark.md), especially on repositories
you know well. All docs: [docs/README.md](docs/README.md).

MIT licensed.
