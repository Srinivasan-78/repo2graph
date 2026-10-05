<div align="center">

# repo2graph

**Cited, token-bounded answers about a codebase, for coding agents and CI.**

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

It needs no model, no API key, no language server and no database. Building, querying and the
MCP server make no network calls; the only exceptions are `rag --answer` (opt-in, sends the pack
to an LLM) and `repo2graph github` (clones a repository). Parsers are part of that promise:
`tree-sitter-language-pack` is pinned below 1.0 so every grammar arrives compiled into the
installed wheel, rather than being downloaded on first parse — see
[SECURITY.md](.github/SECURITY.md#why-this-is-safe-for-enterprise-use). Use it from the CLI, as an MCP server in Claude Code or Cursor, or as a GitHub Action.

## Try it

```bash
uvx repo2graph demo                                   # bundled example repo, five questions answered
uvx repo2graph build . -o .r2g                        # index your own code
uvx repo2graph rag "how does routing work" -o .r2g    # a cited, budget-bounded context pack
```

Give an agent the same thing over MCP:

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .
```

Cursor, Claude Desktop and other clients: [docs/mcp.md](docs/mcp.md). Step-by-step with expected
output: [`demo` in docs/cli.md](docs/cli.md#demo--the-first-command-to-run).

## Is it better than grep?

**On cross-file structural questions, yes. On single-file lexical queries, no — grep is the
better tool.** Both halves are measured on 40 lexical and 40 structural questions about Flask,
requests, FastAPI and Hono that repo2graph was **not** tuned against, scored against the
definitions that answer them, every retriever held to the same token budget
([method, full tables and diagnosis](benchmarks/real/README.md); raw rows in
[`results_holdout_final.json`](benchmarks/real/results_holdout_final.json)).

### Where repo2graph wins: cross-file structural questions

grep structurally cannot traverse dependency edges, compute reverse call closures, or follow
cross-module delegation. On questions whose evidence provably spans a cross-file graph edge:

| Budget | repo2graph | lexical search alone | grep, then read around hits |
|---:|---:|---:|---:|
| 2,000 tokens | **17%** | 14% | 14% |
| 4,000 tokens | **36%** | 29% | 21% |
| 8,000 tokens | **69%** | 43% | 38% |

**The graph is what does it**, and that is the comparison that matters: expansion adds +3/+7/+26 pp
over the same retriever with expansion switched off. Against grep the margin is +2/+14/+31 pp —
it widens with budget rather than closing, because grep has no edge to follow however much room
it is given.

### Where grep wins: single-file lexical questions

| Budget | repo2graph | grep, then read around hits |
|---:|---:|---:|
| 2,000 tokens | 16% | **28%** |
| 4,000 tokens | 26% | **48%** |
| 8,000 tokens | 43% | **59%** |

On purely lexical questions where the evidence sits in a single file, text search is grep's
optimum, and this gap is real: −12/−22/−15 pp. We have not closed it. The remaining cause looks
like vocabulary mismatch rather than ranking — a question that says "datetime" does not match
code that says "timestamp" — which is why the
[dense-vector path](benchmarks/real/README.md#the-dense-result) closes it to −2 pp at 8,000
tokens and more BM25 tuning has not. That path needs a downloaded model, so it is opt-in
(`pip install "repo2graph[rag]"`) and the zero-dependency default stays the default.

### Where repo2graph wins for agents: fewer turns

In simulated agent workflows (`search` → `read` → `answer`, via `scripts/agent_eval.py`). No
model is in the loop — the "agent" is a deterministic policy over real ripgrep and a real index:

| Task set | Method | Success | Mean turns | Mean tokens | Precision per read |
|---|---|---:|---:|---:|---:|
| Structural (40) | repo2graph | **38%** | 3.0 | 2,543 | 15.4% |
| Structural (40) | ripgrep | 10% | **2.5** | **586** | **29.0%** |
| Lexical (40) | repo2graph | 18% | **1.0** | 1,962 | 21.5% |
| Lexical (40) | ripgrep | 18% | 3.0 | **740** | **89.1%** |

Nearly four times the structural success rate, and it reaches a lexical answer in one turn where
grep needs three. It is not cheap: 4.3× grep's tokens on the structural set and 2.6× on the
lexical one, and **grep wins precision per read on both**. repo2graph buys recall and turns with
context; the budget-matched comparison is the single-shot tables above.

For completeness, the 35+10 question set this page used to report — visible since the first
version of these tables and therefore a regression set, not evidence — now reads 39/44/56%
lexical against grep's 35/61/72%, and 20/70/80% structural against grep's 20/20/70%. Those are
the better-looking numbers, which is exactly why the held-out set is the one quoted above.

What it does do that grep doesn't:

- **Relationships in one hop.** `repo_neighbours` returns a symbol's callers, callees, base
  classes and defining file, each with a line number. Every edge carries a `confidence`: a name
  that could mean several definitions is marked `ambiguous` and priced at `1/n`, not guessed.
- **A hard ceiling.** The pack is measured, clamped and re-measured before it's returned
  (12k tokens max over MCP), so an agent can't flood its own context through this tool.
- **Reverse closures from the graph.** `repo_blast_radius` walks what depends on a symbol,
  bounded by hop count and visit cap, with a citation per edge.

How it compares with Serena, Aider's repo map, CodeGraphContext, code-graph-rag, Sourcegraph,
Cursor's index and Claude Code's own search, including when to use those instead:
**[docs/comparison.md](docs/comparison.md)**.

## Five questions to start with

| Ask your repo | What the graph adds |
|---|---|
| `Where is authentication enforced?` | the guard itself, plus the routes that call it |
| `What calls <function>?` | CALLS edges into it, each with a confidence score |
| `What tests cover <module>?` | IMPORTS edges from the test module back to the code under test |
| `What would be affected by changing <api>?` | the definition, then its direct callers from the CALLS edges into it (explain node) |
| `Trace <a request> from route to persistence.` | the handler and its callees one hop at a time, each block cited to file and line |

## GitHub Action

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful

- uses: Srinivasan-78/repo2graph@v2
  with:
    git-history: "500"
    commit-branch: graph     # optional: publish graph.html to a browsable branch
```

`@v2` follows every 2.x release; pin an exact tag (`@v2.2.0`) to upgrade by hand. The Action never
calls an LLM. Inputs and outputs: [docs/cli.md](docs/cli.md).

## Commands

| Command | Does |
|---|---|
| `repo2graph build <path> -o .r2g` | Parse a repo into a graph and chunks (`--incremental`, `--git-history N`) |
| `repo2graph query "<q>" -o .r2g` | BM25 search plus one graph hop |
| `repo2graph rag "<q>" -o .r2g` | Budget-bounded, cited context pack (`--answer` sends it to an LLM: opt-in, the only path that sends code anywhere) |
| `repo2graph explain <edge\|node\|retrieval>` | Why an edge exists, or why a block was retrieved |
| `repo2graph github <owner/repo> -o <dir>` | Fetch, build and clean up without a local clone |
| `repo2graph demo` | Index a bundled example and answer the five questions above |
| `repo2graph map`, `repo2graph stats`, `repo2graph index-status`, `repo2graph embed` | Re-render `graph.html`, report counts and freshness, add optional dense vectors |
| `repo2graph doctor`, `repo2graph bug-report`, `repo2graph explain-path`, `repo2graph completion` | Diagnose setup, build a privacy-safe bug bundle, say why a path is (not) indexed, shell completion |
| `repo2graph-mcp <path>` | stdio MCP server: `repo_map`, `repo_search`, `repo_neighbours`, `repo_blast_radius`, and five more |

Full flags: [docs/cli.md](docs/cli.md). Python API: [docs/python-api.md](docs/python-api.md).

## What it can't do

- **Resolve calls by type.** Calls are matched by name, scoped by class, file, imports and
  directory. `x.get()` on a receiver of unknown type is recorded as a low-confidence guess, not a
  fact. For exact references, use a language-server tool.
- **See dynamic dispatch, reflection or computed imports.** A missing edge doesn't prove that no
  call exists.
- **Cross language boundaries** (Python calling C++ through bindings).
- **Rebuild itself when files change.** `index-status` reports staleness; rebuild with
  `build --incremental`.

<a id="languages"></a>Symbols, calls and classes are extracted for Python, JS, TS, TSX, Go, Rust, Java, Ruby, C, C++,
C#, PHP, Kotlin, Swift, Scala, Bash and Lua. Every other file is still indexed as text. The
specific cases that defeat it — reflection dispatch, string-keyed registries, barrel re-exports —
are pinned as known failures in the
[synthetic regression suite](benchmarks/corpus/README.md#known-failure-cases-it-pins), and
per-language coverage is scored, unevenly, in
[docs/architecture.md §4](docs/architecture.md#4-language-support).

**Two of those seventeen are benchmarked on real repositories**: Python (Flask, requests,
FastAPI) and TypeScript (Hono). Treat the rest as parsed-and-unmeasured. That is not a formality —
the retrieval benchmark found two parse gaps in the *one* TypeScript repository as soon as it
looked, one of which was hiding
[Hono's entire public API](benchmarks/real/README.md#fixed-this-round), and neither would have
been visible without a real repository to ask questions about.

## Status

The 2.x CLI, MCP tools and output schema follow semver: breaking changes wait for 3.0. Default paths run locally, send no telemetry and exclude secrets from agent replies
unconditionally ([what never leaves your machine](.github/SECURITY.md#what-never-leaves-your-machine),
[how credential files are excluded](.github/SECURITY.md#how-credential-files-are-excluded),
[reporting a vulnerability](.github/SECURITY.md#reporting-a-vulnerability)). A Docker image for
read-only, non-root deployments is described in
[.github/SECURITY.md](.github/SECURITY.md#container-deployment).

## Contributing

```bash
git clone https://github.com/Srinivasan-78/repo2graph && cd repo2graph
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make lint format-check typecheck test
```

Branch from `develop`. Start with [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md) and
[docs/architecture.md](docs/architecture.md). The most useful contribution right now is new
questions for the [retrieval benchmark](benchmarks/real/README.md), especially on repositories
you know well. All docs: [architecture](docs/architecture.md), [CLI](docs/cli.md),
[MCP](docs/mcp.md), [Python API](docs/python-api.md), [comparison](docs/comparison.md).

MIT licensed.
