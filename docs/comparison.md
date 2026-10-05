# How repo2graph compares

Building a graph out of a codebase is not a new idea, and repo2graph is not the only tool doing it
for AI agents. This page covers what each tool is for, where repo2graph is the wrong choice, and
the one axis that separates them.

Everything said here about another project is taken from its own documentation, linked inline.
Where a claim about repo2graph is checkable in this repository, the file is named.

## The short answer: which tool for which job

These are the tools people actually ask about. Facts about each come from its own documentation,
checked on 2026-09-28; they move fast, so follow the links before relying on a detail.

| Tool | How it understands code | What a query returns | Setup | Where it beats repo2graph |
|---|---|---|---|---|
| **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)'s built-in search** | No index. The agent runs Glob / Grep / Read on demand. | Whatever files and line ranges the agent chooses to read | None | Always fresh, zero setup, and the model picks its own search terms. On our [real-repo benchmark](../benchmarks/real/README.md) a *mechanical* grep-then-read loop already finds more of the answer than repo2graph at 4k+ tokens on lexical questions. |
| **[Cursor](https://cursor.com/docs) codebase indexing** | Chunks embedded and stored by Cursor's service, synced incrementally | Nearest-neighbour chunks inside the Cursor agent | Automatic, inside Cursor | Handles vocabulary mismatch well; nothing to install. Only available inside Cursor, and code chunks leave the machine for embedding. |
| **[Serena](https://github.com/oraios/serena)** | Language servers (LSP) for 40+ languages, or a JetBrains backend | Symbols, type-aware references, and symbol-level **edits** (rename, replace body) over MCP | `uv` + a language server per language | **Precision.** References come from a real type checker, not name matching, and it can edit. If you want your agent to find *every* caller of a method correctly, use Serena. |
| **[Aider](https://aider.chat/docs/repomap.html)'s repo map** | tree-sitter + graph ranking of files by dependency | Signatures of the most relevant symbols repo-wide, ~1k tokens by default (`--map-tokens`) | Built into Aider | A compact whole-repo overview in very few tokens. Part of Aider's own workflow, not a separate service. |
| **[CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext)** | tree-sitter into a graph database (FalkorDB Lite, Kuzu, Neo4j, …), 23 languages | Graph query results: callers, callees, call chains, dead code, complexity | A graph DB backend | Richer graph queries (dead code, complexity, arbitrary call chains) and more languages. |
| **[code-graph-rag](https://github.com/vitali87/code-graph-rag)** | tree-sitter into Memgraph | Answers from an LLM that turns your question into Cypher; can also edit | Memgraph + an LLM | Natural-language questions over the full graph, plus editing. Needs a model on every query. |
| **[Sourcegraph](https://sourcegraph.com) (Code Search, Cody Enterprise)** | Precise code intelligence (SCIP indexers) across many repositories | Cross-repo search results; Cody Enterprise answers | Server deployment; Cody Free/Pro ended July 2025 | Organisation-scale, cross-repository, compiler-accurate navigation. A different weight class. |
| **repo2graph** | tree-sitter, name-based call resolution, 17 languages, plus `CO_CHANGE` from git history | Source blocks, each headed `[cite: path:start-end]`, packed under a hard token ceiling | `uvx repo2graph build .`; no model, no DB, no language server, no network | See below. |

**Where repo2graph is actually different**, and only where:

- **The token ceiling is enforced, not advisory.** The whole returned pack is measured, clamped
  and re-measured before it leaves the MCP server (`MCP_MAX_BUDGET_TOKENS = 12000` in
  `repo2graph/mcp/guardrails.py`, clamped in the handler). An agent cannot flood its own context
  through this tool.
- **Every block carries a citation**, so a wrong answer shows you where it went wrong.
- **It runs headless in CI.** The GitHub Action builds and publishes an index with no model
  and no account. (A `repo2graph impact` command used to report a PR's blast radius here; it was
  removed, having never been benchmarked and having drifted from this description -- it never
  traversed `INHERITS` or `CO_CHANGE`.)
- **`CO_CHANGE`**: files that keep changing together, mined from git. No parser can see this.
- **Zero-infrastructure.** No graph database, no language server, no embedding service, no
  network call on the default path. This is a deliberate trade and it has a measured price: the
  optional dense-vector path (`pip install "repo2graph[rag]"`, which does download a model) is
  worth 14 pp of lexical and 7 pp of structural recall at 8,000 tokens
  ([the dense result](../benchmarks/real/README.md#the-dense-result)). We keep the offline
  default and publish the numbers for the other choice rather than leading with a configuration
  most users do not have.

**Where it is not different:** retrieval quality on lexical questions. On code it did not write,
and on 40 questions it was not tuned against, repo2graph's cited packs find *less* of the answer
than grep at the same budget — 16/26/43% against 28/48/59% at 2k/4k/8k tokens
([numbers and diagnosis](../benchmarks/real/README.md)). Graph expansion earns its keep on
cross-file structural questions, where it leads grep by +2/+14/+31 pp over the same budgets and
by 26 pp over its own lexical-only ablation at 8,000 tokens. If raw recall on ordinary questions
is what you need today, a good agent with grep (or Serena, for exact references) is the better
choice.

## The axis that matters: what comes back from a query

Three tools can all parse the same file with the same tree-sitter grammar and still be different
products, because they return different things.

- **A picture.** The graph is rendered for a human to look at and navigate.
- **A subgraph.** The graph is queried and a structure comes back — nodes, edges, a path — which
  the caller then follows, opening files as it goes.
- **The code.** The graph is used to *decide which source to return*, and the source comes back
  already packed to a token budget with a citation on every block.

repo2graph is the third. `Index.pack_context()` (`repo2graph/query.py`) scores chunks
lexically, expands one hop across `CALLS` / `IMPORTS` / `DEFINES` / `INHERITS`, and renders the
result as markdown where each block is headed `[cite: path:start-end]`. An agent that receives
that has the answer in its context, not a map telling it where the answer might be.

That is a narrower goal than "map everything you own", and the trade-offs below follow from it.

## repo2graph vs Graphify

[Graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0) parses code
locally with tree-sitter across roughly 40 languages, detects communities, tags every edge
`EXTRACTED` or `INFERRED`, and exposes `query`, `path` and `explain` over the resulting
`graph.json`. Docs, PDFs, images and video go into the same graph through a semantic pass that
uses your assistant's model or a configured backend.

It is a bigger project than repo2graph by every community measure, and for "I want to understand
how this system hangs together, including the design docs", it is the better tool.

| | repo2graph | Graphify |
|---|---|---|
| Returns | packed source, cited per block, inside a token budget | subgraph / path / concept explanation over `graph.json` |
| Ranking | BM25 seeds + graph expansion (1 hop by default), optional dense fusion | graph traversal; explicitly not a vector index |
| Non-code corpus | indexed as text, never sent anywhere | docs, PDFs, images, video via a model-backed semantic pass |
| Languages parsed for symbols | 17 | ~40 |
| Edges from version control | `CO_CHANGE`, files that keep changing together | — |
| Ambiguity marking | `confidence` on `CALLS`; ambiguous names fan out at `1/n` | `EXTRACTED` / `INFERRED` tags |
| Runs headless in CI | yes — published GitHub Action | built around a `/graphify` skill in an assistant |
| Hosted platform | none | app.graphify.com |

**Where Graphify wins:** breadth. More grammars, more file types, community detection, and
path-between-two-concepts queries repo2graph has no equivalent for.

**Where repo2graph wins:** it is a retrieval layer, not a map. The budget is enforced on the whole
rendered pack rather than advisory (`MCP_MAX_BUDGET_TOKENS = 12000` in
`repo2graph/mcp/guardrails.py`, clamped in the handler and re-measured before returning),
`CO_CHANGE` adds a signal no AST can produce, and the entire path — build, query, pack, MCP —
makes zero network calls with no account anywhere. The one exception, `rag --answer`, is opt-in,
names its provider and hostname on stderr before sending a byte, and drops secret-ish paths from
the pack.

## repo2graph vs the Obsidian Code Graph plugin

[Code Graph](https://community.obsidian.md/plugins/code-graph) renders your codebase as an
interactive force-directed graph *beside your notes*, with TODO/FIXME highlighting and
comment-links (`@see`, `@adr`, `@tested-by`) as typed edges. It parses TypeScript, TSX, JavaScript
and Python with tree-sitter, and extracts imports only, by regex, for eight more languages. It
requires Obsidian 1.7.2+ on desktop.

This is a different category. It is a reading tool for a human inside a note-taking app;
repo2graph is a retrieval tool for an agent, with `graph.html` as a side artifact rather than the
point. If your knowledge lives in an Obsidian vault and you want the code visible next to it,
install the plugin — the two do not compete for the same slot.

## repo2graph vs plain grep or an embeddings index

This is what most agents do today. What follows is the design reasoning. Whether it pays off in
practice is measured, not argued, in
[the retrieval benchmark](../benchmarks/real/README.md) — and on lexical questions grep wins more
often than it loses.

- **grep** is exact and structureless. It finds the token, not the relationship: it cannot tell you
  who calls this function, and a match inside a comment ranks identically to the definition. When
  the query words differ from the code's words, it returns nothing at all.
- **An embeddings index** handles the vocabulary mismatch and loses the structure. Top-k
  nearest-neighbour chunks arrive without their callers, and nothing stops two chunks of the same
  file from consuming the budget while the function that actually implements the behaviour sits one
  call away, unretrieved.

repo2graph uses BM25 for the seeds — exact, cheap, no model — and then spends the remaining budget
on *graph neighbours of the seeds* rather than on more text that merely resembles the query. Dense
vectors are available (`repo2graph embed`) and fuse with the lexical score, but they are optional:
the query path is stdlib-only by design, so a machine that only queries a shipped index does not
need numpy (`repo2graph/embed.py`). On the lexical benchmark this expansion does not improve
recall over BM25 alone; on the structural benchmark it adds 20 to 30 points over BM25 at 4k and
8k tokens.

## When repo2graph is the wrong choice

- **You need type-accurate call resolution.** Call resolution here is name-based, not type-based —
  a deliberate trade for being language-agnostic and setup-free. Ambiguous names fan out to up to
  five candidates at `confidence = 1/n`. If you need the real call graph of a large C++ or Java
  system, use a compiler-backed tool.
- **Your codebase is mostly dynamic dispatch, reflection or codegen.** A parser cannot see those
  edges, and absence of an edge is not proof of absence of a call. The
  [synthetic regression suite](../benchmarks/corpus/README.md) pins the specific cases.
- **You need every reference to a symbol, exactly.** Use a language-server tool such as
  [Serena](https://github.com/oraios/serena).
- **You want the graph itself as the deliverable** — communities, layout, path queries between
  arbitrary concepts. That is Graphify's shape, not this one.
- **Your language is outside the 17 with symbol support.** The files still appear on the map and
  are still retrievable as text, but there are no function-level nodes or `CALLS` edges for them.

## Reproducing any of this

Retrieval-quality numbers come from `benchmarks/real/results.json`
([method and diagnosis](../benchmarks/real/README.md), `scripts/bench_real_repos.py`).
Build-scale numbers come from `benchmarks/results.json`, generated against five pinned public
repositories; [`benchmarks/methodology.md`](../benchmarks/methodology.md) has the methodology and
[`examples/README.md`](../examples/README.md) the exact reproduction command per repository.
Nothing on this page is estimated: where a figure is not measured, it is not given.
