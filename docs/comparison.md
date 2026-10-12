# How repo2graph compares

Building a graph out of a codebase is not a new idea, and repo2graph is not the only tool doing it
for AI agents. This page covers what each tool is for, where repo2graph is the wrong choice, and
the one axis that separates them. The agent-context tools come first; further down are the tools
that draw a codebase for a person to look at — [Sourcetrail](#repo2graph-as-a-sourcetrail-alternative)
and the [dependency-graph tools](#repo2graph-vs-the-dependency-graph-tools-madge-dependency-cruiser-codesee)
Madge, dependency-cruiser and CodeSee.

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
| **repo2graph** | tree-sitter, name-based call resolution, 19 languages, plus `CO_CHANGE` from git history | Source blocks, each headed `[cite: path:start-end]`, packed under a hard token ceiling | `uvx repo2graph build .`; no model, no DB, no language server, no network | See below. |

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
  network call on the default path. This is a deliberate trade, and its measured price is now
  smaller than it was and no longer one-directional: the optional dense-vector path
  (`pip install "repo2graph[rag]"`, which does download a model) is worth **+12 pp of lexical
  recall at 8,000 tokens and costs 3 pp of structural recall**, because fusing a diffuse topical
  signal at equal weight dilutes a BM25 ranking that structural questions had already got right
  ([the dense result](../benchmarks/real/README.md#the-dense-result)). So the offline default is
  not merely the cheaper choice any more; on structural questions it is the better one.

**Where it is not different:** retrieval quality on lexical questions. On code it did not write,
and on 40 questions it was not tuned against, repo2graph's cited packs find *less* of the answer
than grep at the same budget — 17/29/45% against 28/48/59% at 2k/4k/8k tokens
([numbers and diagnosis](../benchmarks/real/README.md)). Graph expansion earns its keep on
cross-file structural questions, where it leads grep by +15/+39/+41 pp over the same budgets, by
24 pp over its own lexical-only ablation at 8,000 tokens, and does it for fewer tokens than grep
spends. If raw recall on ordinary questions
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
| Languages parsed for symbols | 19 | ~40 |
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

## repo2graph as a Sourcetrail alternative

[Sourcetrail](https://github.com/CoatiSoftware/Sourcetrail) was a free, open-source (GPL-3.0)
desktop source explorer for C, C++, Java and Python, made by Coati Software. You indexed a project
once — C and C++ through Clang, usually from a `compile_commands.json` compilation database; Java
through Eclipse JDT; Python through its own indexer — and then explored it in three linked views: a
fuzzy search field, a graph centred on the selected symbol showing all of its incoming and outgoing
dependencies, and a code view listing every source location of that symbol as snippets. Clicking a
node moved all three; clicking an edge jumped to the line that created it. "Custom trails" drew a
whole call graph, inheritance chain or include tree to any depth. Editor plugins — VS Code, Vim,
Emacs, Visual Studio, IntelliJ IDEA, Eclipse, Sublime Text, Atom, Qt Creator — moved the cursor
between Sourcetrail and the editor in both directions.

Its original authors archived it at the end of 2021; the GitHub repository has been read-only since
2021-12-14. A community fork, [petermost/Sourcetrail](https://github.com/petermost/Sourcetrail), is
maintained with the stated aim of keeping it buildable; as of October 2026 its README lists C/C++
and Java, Python support having been removed. (Everything above about Sourcetrail is from its own
`README.md` and `DOCUMENTATION.md` at the archived commit, and the fork's README.)

**repo2graph is not a drop-in replacement.** It is a different answer to the same question — "how
does this unfamiliar code fit together?" — for a different reader. Sourcetrail put a person in front
of an interactive graph. repo2graph hands a coding agent the source that answers a question.

| | Sourcetrail | repo2graph |
|---|---|---|
| Who it is for | a developer, in a desktop application | a coding agent over MCP; also a terminal CLI and a GitHub Action |
| How you ask | search a symbol, then click through the graph and the code | ask a question in words: `repo_search`, `repo_neighbours`, `repo_blast_radius` over MCP, `repo2graph rag` in a terminal |
| What comes back | a symbol-centred graph, plus the source locations to read | the source blocks that answer, each headed `[cite: path:start-end]`, packed under a hard token ceiling |
| How it understands code | compiler front ends: Clang (C, C++), Eclipse JDT (Java), plus a Python indexer | tree-sitter for 19 languages; calls matched by name, scoped by class, file and imports |
| Setup | a project per codebase; a compilation database or Visual Studio project for C/C++, Gradle or Maven for Java | `uvx repo2graph build .` — no build configuration, compiler or language server |
| Precision | compiler-backed for C, C++ and Java; an optional post-processing pass resolves leftover references by name and marks them "ambiguous" | name-based throughout; a name that could mean several definitions is marked `ambiguous` and priced `1/n`, never guessed |
| Version-control signal | none | `CO_CHANGE`: files that keep changing together in git |
| Headless / CI | no | yes, as a GitHub Action |
| Status | archived 2021; community fork kept buildable | maintained |

**What Sourcetrail did better, plainly:**

- **The interactive UI.** Click a symbol, see its callers and callees, click an edge, land on the
  line, expand a class into its members, follow a trail to any depth. That loop was the product,
  and repo2graph does not have it. `graph.html` is a static, self-contained page written at build
  time: you can pan, zoom, search, and click a node to list its connections, path, line range and
  signature, but it draws the 300 best-connected nodes by default (`--viz-nodes`, see
  [`map`](cli.md#map-and-stats)), shows no source code, and does not follow your editor. It is a map
  to orient by, not a navigator. Against Sourcetrail's desktop UI that is a real deficit.
- **Precision on C, C++ and Java.** A compiler front end resolves overloads, templates and virtual
  dispatch that name matching cannot. If you need the exact call graph of a large C++ or Java
  system, a compiler-backed tool is the right answer — see
  [when repo2graph is the wrong choice](#when-repo2graph-is-the-wrong-choice).
- **Editor round-tripping.** Sourcetrail's plugins moved the cursor both ways. repo2graph's editor
  integration is the MCP client — Claude Code, Cursor, Claude Desktop ([docs/mcp.md](mcp.md)) — which
  asks it questions; there is no cursor sync.

**What repo2graph does differently:**

- **It is agent-native.** Sourcetrail's "select a symbol, see its neighbours" is a tool call here:
  `repo_neighbours` returns a symbol's callers, callees, base classes and defining file with line
  numbers, `repo_blast_radius` walks what depends on it, and `repo_search` returns the cited source
  for a question, inside a budget the server enforces (`MCP_MAX_BUDGET_TOKENS = 12000` in
  `repo2graph/mcp/guardrails.py`).
- **It lives in the terminal and the editor you already use**, not in a separate application: a
  one-line `claude mcp add`, a CLI, or a step in CI.
- **It needs no build setup**, for 19 languages, at the price of name-based rather than type-based
  resolution.
- **It sees history.** `CO_CHANGE` edges come from git, which no parser — Sourcetrail's included —
  can see.

What that buys is measured, and the measurement is mixed: on cross-file structural questions
repo2graph's cited packs find 79% of the evidence at 8,000 tokens against 38% for grep, and on
single-file lexical questions grep finds more — 59% against 45%
([method and tables](../benchmarks/real/README.md)). If you want to *browse* a C, C++ or Java
codebase by hand, the Sourcetrail fork or your IDE's call hierarchy is the better tool. If you want
your coding agent to answer cross-file questions with citations and a hard token ceiling, that is
what repo2graph is for.

## repo2graph vs the dependency-graph tools: Madge, dependency-cruiser, CodeSee

These are what people find first when they search for a way to visualize a codebase, and the first
two answer a narrower, well-defined question very well: *which module imports which*.

| | [Madge](https://github.com/pahen/madge) | [dependency-cruiser](https://github.com/sverweij/dependency-cruiser) | repo2graph |
|---|---|---|---|
| What an edge means | file A imports file B | file A imports file B | a call, an import, inheritance, a definition, or co-change in git — down to functions and methods |
| Languages | JavaScript modules (CommonJS, AMD, ES6); TypeScript via a `tsConfig` option; Sass, Stylus, Less | JavaScript, TypeScript, CoffeeScript, LiveScript (ES6, CommonJS, AMD), including `.jsx`, `.tsx`, `.vue`, `.svelte` | 19 with symbols; every other file as text |
| Main output | circular dependencies, orphans, leaves, dependents of a module; images through Graphviz | violations of rules you write (circular imports, orphans, undeclared or dev-only dependencies in production code, your own boundaries), as text, an eslint-like format for builds, or graphs (DOT, Mermaid, HTML, JSON, CSV) | cited source packs for an agent, a JSON-lines graph, `graph.html` |
| Strongest at | finding circular imports in a JS/TS project, quickly | enforcing architecture rules on every pull request | answering cross-file questions inside an agent's token budget |
| License | MIT | MIT | MIT |

Facts about Madge and dependency-cruiser are from their own READMEs, checked on 2026-10-11.

[CodeSee](https://www.codesee.io) was a hosted service that drew maps of a codebase for teams to
look at and annotate. GitKraken acquired the company in May 2024. We could not confirm from a
primary source what is still offered under that name, so check before evaluating it; the comparison
that holds regardless is the category — a hosted map for people — against a local retrieval tool for
agents that needs no account and makes no network call on its default path.

**The real axis.** Madge and dependency-cruiser graph *module imports*. That is a clean,
well-defined graph, and they draw it and police it better than repo2graph does: repo2graph has no
rule language and no circular-import report. repo2graph graphs *calls*, *imports*, *inheritance*
and *co-change*, at the level of individual functions and methods, and its main output is not a
picture — it is the source an agent needs, packed under a hard token ceiling with a citation on
every block. An import graph answers "what does this file depend on?"; repo2graph is built for "what
calls this, what does it call, and show me the code."

They are complementary. If you want circular imports caught or a layering rule enforced in CI on a
JS/TS repository, use dependency-cruiser (or Madge, for the quick check) — alongside repo2graph if
you also want an agent to answer questions about the same code.

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
- **Your language is outside the 19 with symbol support.** The files still appear on the map and
  are still retrievable as text, but there are no function-level nodes or `CALLS` edges for them.

## Reproducing any of this

Retrieval-quality numbers come from `benchmarks/real/results.json`
([method and diagnosis](../benchmarks/real/README.md), `scripts/bench_real_repos.py`).
Build-scale numbers come from `benchmarks/results.json`, generated against thirteen pinned public
repositories; [`benchmarks/methodology.md`](../benchmarks/methodology.md) has the methodology and
[`examples/README.md`](../examples/README.md) the exact reproduction command per repository.
Nothing on this page is estimated: where a figure is not measured, it is not given.
