<div align="center">

# repo2graph

**Interactive code-graph maps & zero-dependency GraphRAG for AI agents and humans**

<p align="center">
  <a href="https://glama.ai/mcp/servers/Srinivasan-78/repo2graph"><img src="https://glama.ai/mcp/servers/Srinivasan-78/repo2graph/badges/score.svg" alt="Glama MCP server score" /></a>
  <a href="https://pypi.org/project/repo2graph/"><img src="https://img.shields.io/pypi/v/repo2graph.svg?color=blue" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/repo2graph/"><img src="https://img.shields.io/pypi/pyversions/repo2graph.svg" alt="Python versions" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml"><img src="https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <a href="https://github.com/Srinivasan-78/repo2graph/stargazers"><img src="https://img.shields.io/github/stars/Srinivasan-78/repo2graph?style=social" alt="GitHub stars" /></a>
</p>

<p align="center">
  <img src="docs/images/demo.gif" alt="repo2graph building a map of a repository, then answering a question about it, in a terminal" width="850" />
</p>

<p align="center">
  If repo2graph is useful to you, a ⭐ on <a href="https://github.com/Srinivasan-78/repo2graph">GitHub</a> helps others find it.
</p>

*`repo2graph build` then `repo2graph query`, on this repo's own source — real output, not staged.*

<p align="center">
  <img src="docs/images/graph-overview.png" alt="Interactive code graph of a project mapped by repo2graph" width="850" />
</p>

*The picture that build also produces: each dot is a folder, file, function, or library; each arrow is a real code connection.*

</div>

<!-- mcp-name: io.github.Srinivasan-78/repo2graph -->

repo2graph reads a folder full of code and draws you a map of it — then uses that map to answer
questions about the code, with citations. Agents can ask it questions directly over MCP.

**Want to know how it actually works under the hood** — the tree-sitter pipeline, the graph model,
confidence scoring, budget accounting, the Python API? That's in
**[TECHNICAL.md](TECHNICAL.md)**. This page stays to what it does and how to run it.

## The idea

Imagine you get handed a big box of Lego that someone else already built things with. You want to
know what connects to what. You could look at every brick one at a time, or someone could hand you
a map.

Code is like that box. A project has hundreds of files, and the files use each other in ways you
cannot see by looking at one file at a time.

repo2graph makes the map. On the map:

- Every **thing** is a dot. A folder is a dot. A file is a dot. A function (a small named piece of
  code that does a job) is a dot. We call these dots **nodes**.
- Every **connection** is an arrow. "This file is inside that folder." "This function uses that
  function." "This file borrows code from that library." We call these arrows **edges**.

Dots joined by arrows are called a **graph**. That is the whole idea.

## Why a map helps

If you search a project for the word "login", you get every file that happens to say "login",
including comments and typos.

The map is better, because it knows which function actually does the login work, and it also knows
which functions call it and which functions it calls. So you get the real answer plus its
neighbours.

That matters most when a chatbot or AI helper is reading the code for you. Giving it the right
piece of code plus the pieces around it is usually what it was missing.

## Install

You need Python 3.10 or newer.

```bash
pip install repo2graph
```

Two optional extras, neither needed for the core:

```bash
pip install "repo2graph[rag]"   # sentence-transformers + numpy, for meaning-based search
pip install "repo2graph[mcp]"   # the MCP SDK, for serving the map to an agent
```

To run it without installing anything — which is how most people wire up the MCP server — use
[uv](https://docs.astral.sh/uv/):

```bash
uvx repo2graph build . -o .r2g
uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project
```

Or from a checkout, if you want to change it:

```bash
git clone https://github.com/Srinivasan-78/repo2graph
cd repo2graph
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Use it

### 1. Make the map

```bash
repo2graph build /path/to/your/project -o .r2g --git-history 200
```

That is it. It walks the project, reads it, and puts everything in a folder called `.r2g`. A medium
project takes seconds. A very big one takes a minute or two.

`--git-history 200` is optional. It looks at the last 200 saves (commits) in the project's history
and adds links between files that keep getting changed together. Those links are a good clue about
which files secretly depend on each other.

No copy on your machine? Point it at GitHub instead — it downloads, maps, and tidies up after
itself:

```bash
repo2graph github psf/requests -o out/requests --git-history 200
```

### 2. Look at the map

```bash
open .r2g/human/graph.html   # the picture
cat .r2g/human/overview.md   # the same thing written out in words
repo2graph stats -o .r2g     # how many dots, arrows and functions there are
```

`graph.html` is one single file. No internet needed, nothing to install. Open it in a browser and
you get the picture: drag to move around, scroll to zoom, drag a dot to pin it in place, click a
dot to see what that function looks like and everything it is connected to.

| Interactive Canvas (Zoomed) | Filter & Inspector Controls |
| :---: | :---: |
| <img src="docs/images/graph-zoom.png" alt="Zoomed into the map: named functions, files and libraries joined by arrows" /> | <img src="docs/images/graph-sidebar.png" alt="Side panel with search box, node kinds and relationship kinds" /> |
| *Zoom in to inspect symbol call paths, imports, and definitions* | *Toggle node types, relationships, and filter on screen* |

By default the picture shows the 300 busiest dots, and hides calls that go out to other people's
code, because those triple the number of arrows and tell you little about your own project. Tick
`external` and `CALLS_EXTERNAL` in the side panel to show them. Want a simpler picture? Redraw it
with fewer dots: `repo2graph map -o .r2g --viz-nodes 80`.

### 3. Ask it questions

A search tool and a GraphRAG context packer are built in. Neither needs an AI account.

```bash
repo2graph query "how does routing match a path" -o .r2g       # find the code
repo2graph rag "how does the pack stay inside its budget" -o .r2g   # pack it for an LLM
```

`query` finds the best matching pieces and follows the arrows one step out, so the functions around
each answer come along too. `rag` does the same and then assembles a budget-bounded markdown pack,
repo map on top, every block stamped with an exact citation header:

```
### [cite: repo2graph/cli.py:22-28] `parse_formats` (CALLS out of cmd_build)
# file: repo2graph/cli.py
# function: parse_formats  (lines 22-28, python)
# called by: repo2graph/cli.py::cmd_build, repo2graph/cli.py::cmd_github
def parse_formats(spec: str) -> set[str]:
...
```

The `(CALLS out of cmd_build)` part is the *reason* the block is in the pack: either `seed` (the
search found it) or the arrow that dragged it in.

Word matching misses code that says the same thing in different words, so you can add meaning-based
search on top — vectors are computed once, then blended into every ranking:

```bash
pip install "repo2graph[rag]"                   # sentence-transformers + numpy
repo2graph embed -o .r2g                        # compute vectors, once
repo2graph rag "how is a request routed" -o .r2g --vectors
```

`embed` writes `agent/vectors.npy` and `agent/vectors.meta.json` next to the rest of the index, and
reuses every vector whose chunk text is unchanged, so re-running it after a rebuild is cheap. The
model is `--embed-model` (default: a small MiniLM); `rag --model` is a different thing entirely, the
*LLM* used by `--answer`.

Dense search is opt-in on purpose. Without `--vectors` nothing is loaded and no model is downloaded,
because the only promised dependency of `build`/`rag` is tree-sitter and a 90 MB model fetch has no
business happening unasked.

**Check it is actually on.** Dense retrieval has a failure mode where every surface reports success
and the ranking is still purely lexical — the index carries vectors, but a `chunks.jsonl` rebuilt
without re-running `embed` leaves some chunks unvectorised, and fusion is all-or-nothing:

```bash
repo2graph embed -o .r2g --verify-rag
```

```json
{
  "vectors_present": true,
  "model_id": "sentence-transformers/all-MiniLM-L6-v2",
  "dim": 384,
  "chunks": 812,
  "unvectorised_chunks": 0,
  "embedder_model_id": "sentence-transformers/all-MiniLM-L6-v2",
  "embedder_dim": 384,
  "ok": true,
  "error": null
}
```

It exits non-zero with an actionable `error` if vectors are missing, if the active embedder's model
id or width disagrees with the index's, or if any chunk lacks a vector. If fusion ever does switch
itself off mid-query, that is no longer silent either — a `rag_fusion_disabled` JSON line goes to
stderr naming the reason, and stdout still carries a usable lexical answer.

`repo2graph rag --answer` will also send the pack to an LLM and stream back a grounded answer. It is
the one command that puts your source code on the network — read
[the warning](docs/cli.md#-answer-sends-your-code-to-someone-elses-computer) first.

**Full flag tables, budget accounting and how retrieval works: [docs/cli.md](docs/cli.md).**

### 4. Hand the map to an agent over MCP

`repo2graph-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server, so an agent can ask the
map questions itself instead of you pasting a pack into a chat window.

Point it at a project and it serves it. Nothing to install and no setup step: if no map exists yet,
the first question builds one and answers from it.

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project
```

For Claude Desktop, Cursor and generic clients, the JSON block is the same four lines:

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

Three tools, deliberately:

| Tool | Arguments | What comes back |
|---|---|---|
| `repo_map` | none | Languages, hub files and top entry points. Stable across calls, so it caches. |
| `repo_search` | `query`, optional `k`, `hops`, `budget_tokens` | Seed chunks plus their graph neighbours, each headed `[cite: path:start-end]`. |
| `repo_neighbours` | `node_id`, optional `hops`, `limit` | One graph hop from a symbol: callers, callees, base classes, defining file. The thing grep cannot do. |

The server keeps three promises the CLI leaves to you: secrets are **always** excluded, output is
hard-capped at 12 000 tokens and re-measured before it is returned, and `k`/`hops` are clamped so no
single call can wedge the event loop every client shares. It never calls an LLM itself.

Auto-build writes only what the tools read, and only into a directory you pointed it at. Build ahead
with `repo2graph build` if you want the first question to be fast or want the picture too, and pass
`--no-auto-build` to require an index that already exists.

**Client configs, which directory gets indexed, and the full contract: [docs/mcp.md](docs/mcp.md).**

### 5. Or run it in CI

repo2graph is on the GitHub Marketplace, so a fresh map can live next to your code:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }   # full history, so CO_CHANGE edges are meaningful
- uses: Srinivasan-78/repo2graph@v1
  with:
    path: .
    git-history: "500"
    artifact-name: repo-graph
```

**All inputs and outputs: [docs/github-action.md](docs/github-action.md).**

## What you get in `.r2g`

The output is split in two, because people and programs want different things.

```
.r2g/
├── human/   overview.md   graph.html   graph.graphml
└── agent/   overview.md   manifest.json   chunks.jsonl
              nodes.jsonl   edges.jsonl   graph.cypher   stats.json
```

`agent/manifest.json` is the instruction sheet: what every other file is, what the dots and arrows
mean, how names are built, and where the code starts. A program needs nothing else to make sense of
the folder.

`chunks.jsonl` is the file you hand to an AI system. Each piece already carries its neighbours in
the header, which is what makes the answers good. If you use a vector database, keep each piece's
`node_id` — that is the handle that lets you jump back onto the map after a search.

**Every file, every node and edge kind, the chunk format: [docs/reference.md](docs/reference.md).**

## Using it from Python

Everything the CLI does is also a plain Python call — `build()`, `dump_all()`, and the same `Index`
class the CLI, the Action and the MCP server all use internally. Code sample, streaming exports,
and loading into Neo4j: **[TECHNICAL.md](TECHNICAL.md#using-it-from-python)**.

## Languages

Python, JavaScript, TypeScript and TSX, Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala
and Bash get the full treatment: functions, classes and calls. Files in any other language still
appear on the map as files in their folders, so nothing goes missing. Teaching it a new language
means adding one entry to `LANG_CFG` in `repo2graph/langs.py`.

## Where it guesses

The map is very good, but it is not perfect. Two things worth knowing before you trust it:

- **It matches calls by name, not by type.** If two functions share a name, repo2graph draws up to
  5 possible arrows and marks each one `1/n` sure. If you need certainty, keep only the arrows where
  `confidence` is `1.0`.
- **Some files are skipped:** pictures and other non-text files, anything bigger than 1.5 MB, and
  the usual vendor and build folders. In a git checkout, `.gitignore` is respected.
- **No arrow does not prove no call.** Code that decides while running which function to call is
  invisible to a reader like this one.

How the matching and resolution actually work, per language: **[TECHNICAL.md](TECHNICAL.md#where-it-guesses-and-why)**.

## Security

`build`, `query`, `rag`, and the MCP server make no network calls — everything reads and writes
locally under `.r2g`. The one exception is opt-in: `rag --answer` sends the assembled pack to an
LLM provider, and prints the provider + hostname before it does. The MCP server excludes
credential-shaped files unconditionally, with no flag to turn that off. Details and the full
reasoning: [SECURITY.md](SECURITY.md).

## Contributing

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

See [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md). Source files carry an `@authormark`
watermark header — read [AGENTS.md](AGENTS.md) before editing one.

## Licence

MIT. See [LICENSE](LICENSE).

---

<div align="center">

Found repo2graph useful? [Star the repo](https://github.com/Srinivasan-78/repo2graph) — it's the
easiest way to help other people find it.

</div>
