# How repo2graph compares

Building a graph out of a codebase is not a new idea, and repo2graph is not the only tool doing it
for AI agents. This page is the honest version of the comparison: what each tool is actually for,
where repo2graph is the wrong choice, and the one axis that separates them.

Everything said here about another project is taken from its own documentation, linked inline.
Where a claim about repo2graph is checkable in this repository, the file is named.

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

[Graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0, Y Combinator S26) parses code
locally with tree-sitter across roughly 40 languages, detects communities, tags every edge
`EXTRACTED` or `INFERRED`, and exposes `query`, `path` and `explain` over the resulting
`graph.json`. Docs, PDFs, images and video go into the same graph through a semantic pass that
uses your assistant's model or a configured backend.

It is a bigger project than repo2graph by every community measure, and for "I want to understand
how this system hangs together, including the design docs", it is the better tool.

| | repo2graph | Graphify |
|---|---|---|
| Returns | packed source, cited per block, inside a token budget | subgraph / path / concept explanation over `graph.json` |
| Ranking | BM25 seeds + k-hop expansion, optional dense fusion | graph traversal; explicitly not a vector index |
| Non-code corpus | indexed as text, never sent anywhere | docs, PDFs, images, video via a model-backed semantic pass |
| Languages parsed for symbols | 17 | ~40 |
| Edges from version control | `CO_CHANGE`, files that keep changing together | — |
| Ambiguity marking | `confidence` on `CALLS`; ambiguous names fan out at `1/n` | `EXTRACTED` / `INFERRED` tags |
| Runs headless in CI | yes — published GitHub Action | built around a `/graphify` skill in an assistant |
| Hosted platform | none | app.graphify.com |

**Where Graphify wins:** breadth. More grammars, more file types, community detection, and
path-between-two-concepts queries repo2graph has no equivalent for. Also mindshare, by four orders
of magnitude.

**Where repo2graph wins:** it is a retrieval layer, not a map. The budget is enforced on the whole
rendered pack rather than advisory (`MCP_MAX_BUDGET_TOKENS = 12000` in `repo2graph/mcp.py`, clamped
in the handler and re-measured before returning), `CO_CHANGE` adds a signal no AST can produce, and
the entire path — build, query, pack, MCP — makes zero network calls with no account anywhere. The
one exception, `rag --answer`, is opt-in, names its provider and hostname on stderr before sending
a byte, and drops secret-ish paths from the pack.

## repo2graph vs the Obsidian Code Graph plugin

[Code Graph](https://community.obsidian.md/plugins/code-graph) renders your codebase as an
interactive force-directed graph *beside your notes*, with TODO/FIXME highlighting and
comment-links (`@see`, `@adr`, `@tested-by`) as typed edges. It parses TypeScript, TSX, JavaScript
and Python with tree-sitter, and extracts imports only, by regex, for eight more languages. It
requires Obsidian 1.7.2+ on desktop.

This is barely the same category. It is a reading tool for a human inside a note-taking app;
repo2graph is a retrieval tool for an agent, with `graph.html` as a side artifact rather than the
point. If your knowledge lives in an Obsidian vault and you want the code visible next to it,
install the plugin — the two do not compete for the same slot.

## repo2graph vs plain grep or an embeddings index

This is the comparison that actually comes up in practice, because it is what most agents do today.
The side-by-side table is in the README
([Why repo2graph instead of grep or vector search?](../README.md#vs-grep)); what follows is the
reasoning behind it.

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
need numpy (`repo2graph/embed.py`).

## When repo2graph is the wrong choice

Stated plainly, because a comparison page that concludes "we win everything" is worth nothing:

- **You need type-accurate call resolution.** Call resolution here is name-based, not type-based —
  a deliberate trade for being language-agnostic and setup-free. Ambiguous names fan out to up to
  five candidates at `confidence = 1/n`. If you need the real call graph of a large C++ or Java
  system, use a compiler-backed tool.
- **Your codebase is mostly dynamic dispatch, reflection or codegen.** A parser cannot see those
  edges, and absence of an edge is not proof of absence of a call (`docs/limitations.md`).
- **You want the graph itself as the deliverable** — communities, layout, path queries between
  arbitrary concepts. That is Graphify's shape, not this one.
- **Your language is outside the 17 with symbol support.** The files still appear on the map and
  are still retrievable as text, but there are no function-level nodes or `CALLS` edges for them.

## Reproducing any of this

The numbers in the README's benchmark table come from `benchmarks/results.json`, generated against
five pinned public repositories; `docs/benchmarks.md` has the methodology and
`examples/README.md` the exact reproduction command per repository. Nothing on this page is
estimated — where a figure is not measured, it is not given.
