# repo2graph

Point it at a codebase, and it reads the code, works out how the pieces connect, and writes that
out as a graph you can query — plus text chunks ready to drop into a RAG pipeline.

## What it actually does

Imagine reading an unfamiliar repo and drawing a map on a whiteboard: these files live in these
folders, this function calls that one, this class inherits from that one, this file imports that
package. repo2graph draws that map for you, automatically.

It does this in three steps:

1. **Read the code.** It parses every source file with tree-sitter, the same parsing engine
   editors use for syntax highlighting. That means it understands real code structure — not
   regexes — and it works on a repo it has never seen before with no configuration.
2. **Build the map.** Files, folders, functions, classes and imports become *nodes*. The
   relationships between them — "defines", "calls", "imports", "inherits" — become *edges*.
   The result is a graph you can load into Neo4j, Gephi, or NetworkX.
3. **Cut the code into chunks.** Roughly one chunk per function or class, and here is the useful
   part: each chunk gets a short header describing its neighbourhood in the graph — who calls it,
   what it calls, its docstring. When you embed those chunks, a question like *"how does login
   work?"* matches the code that actually handles login, instead of matching whatever happens to
   share a few words with the question.

Why bother? Plain text search over a repo finds files that *mention* a thing. A graph finds the
files that *do* the thing, and then hands you their neighbours too. That extra context is usually
what an LLM was missing.

## Install

You need Python 3.10 or newer.

```bash
git clone https://github.com/srinivasan-78/repo2graph
cd repo2graph
python3 -m venv .venv
.venv/bin/pip install -e .
```

The `repo2graph` command now lives at `.venv/bin/repo2graph`. Activate the venv
(`source .venv/bin/activate`) if you would rather just type `repo2graph`.

## Run it

### 1. Index a repo on your machine

```bash
repo2graph build /path/to/your/repo -o .r2g --git-history 200
```

That is the whole thing. It walks the repo, parses it, and drops everything into a `.r2g`
directory. On a medium repo this takes seconds; large ones take a minute or two.

`--git-history 200` is optional — it reads the last 200 commits and adds "these files keep
changing together" links, which are surprisingly good at revealing hidden coupling.

### 2. Look at what you got

```bash
cat .r2g/overview.md      # human-readable repo map: languages, hub files, hot symbols
repo2graph stats -o .r2g  # counts of nodes, edges, symbols, parse errors
```

Start with `overview.md`. It is written for a person to read and it is the fastest way to get
oriented in a codebase you do not know.

### 3. Ask it questions

There is a retriever built in, so you can search straight away — no embedding model, no vector
database, no API key:

```bash
repo2graph query "how does routing match a path" -o .r2g -k 8 --hops 1
```

It scores chunks lexically, then walks one hop out across the graph so the callers and callees of
each hit come along for the ride. Add `--json` to pipe the results somewhere:

```bash
repo2graph query "auth middleware" -o .r2g --json | jq '.[].path'
```

### 4. Index a repo you do not have locally

```bash
repo2graph github psf/requests -o out/requests --git-history 200
```

It clones to a temp directory, builds the graph, cleans up after itself, and writes an extra
`index.json` recording the repo slug and the exact commit it indexed. `gh` works as a shorthand
for `github`, and full URLs are fine too. For a private repo, pass `--token` or set `$GH_TOKEN` /
`$GITHUB_TOKEN`. Use `--ref` to pin a branch or tag, and `--keep-clone DIR` if you want the clone
kept around.

### Running the tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## What lands in the output directory

| File | What it is |
|---|---|
| `overview.md` | the repo map, written for humans — read this first |
| `chunks.jsonl` | retrieval chunks: code text with a graph-context header |
| `nodes.jsonl` | one JSON object per node |
| `edges.jsonl` | one JSON object per edge |
| `graph.graphml` | load into NetworkX, Gephi or igraph |
| `graph.cypher` | idempotent `MERGE` script for Neo4j / Memgraph |
| `stats.json` | node / edge / symbol counts and parse errors |

Handy flags for `build`: `--include '**/*.py'`, `--exclude '**/test/**'`, `--max-files N`,
`--formats jsonl,cypher` (skip the formats you do not want), `--no-chunks`.

## Using it in a RAG stack

`chunks.jsonl` is your embedding input. Keep each chunk's `node_id` as vector metadata — that is
the handle you need to jump back into the graph after retrieval.

The pattern that works well: vector search to find seed chunks, then one hop of graph expansion to
pull in the surrounding code, with `overview.md` prepended as repo-level system context.

```python
import json
from repo2graph.query import Index

chunks = [json.loads(l) for l in open(".r2g/chunks.jsonl")]
# embed chunk["text"], store chunk["node_id"] and chunk["path"] as metadata

idx = Index(".r2g")                       # graph + chunks, no embeddings needed
hits = ["sym:app/auth.py::login"]         # node_ids your vector store returned
for node_id, edge_type, direction, src in idx.expand(hits, hops=1):
    for extra in idx.by_node.get(node_id, [])[:1]:
        print(edge_type, direction, extra["path"], extra["qualname"])
```

### Into Neo4j

```bash
repo2graph build /path/to/repo -o .r2g --formats jsonl,cypher
cypher-shell -u neo4j -p password -f .r2g/graph.cypher
```

## The graph model

**Nodes:** `repo`, `dir`, `file`, `symbol` (function / method / class / struct / trait /
interface / type / module), `module` (an external import target), `external` (a call target that
could not be resolved in-repo).

**Edges:**

- `CONTAINS` — repo → dir → file
- `DEFINES` — file → symbol, and symbol → nested symbol
- `IMPORTS` — file → file (`internal: true`) or file → external module
- `CALLS` — symbol → symbol, carrying `count` and `confidence`
- `CALLS_EXTERNAL` — symbol → an external name (stdlib or third-party)
- `INHERITS` — symbol → base class or interface
- `CO_CHANGE` — file ↔ file, from `--git-history`, when files were edited together in 3 or more
  commits

Node ids are stable and readable, so you can construct them by hand: `file:pkg/mod.py`,
`sym:pkg/mod.py::Class.method`, `module:requests`, `dir:pkg`.

## What a chunk looks like

One chunk per symbol (split at roughly 4000 characters with 8 lines of overlap), plus a residual
chunk per file covering code that no symbol claimed, plus whole-file chunks for docs and config.
Every chunk's text opens with its graph neighbourhood:

```
# file: repo2graph/graph.py
# function: resolve_import  (lines 66-99, python)
# called by: repo2graph/graph.py::build
# calls: Path, replace, str, list, startswith, sub, append
# doc: Map an import target to an in-repo file path when possible.
def resolve_import(...):
    ...
```

Fields on each chunk: `id`, `node_id`, `type`, `kind`, `path`, `lang`, `name`, `qualname`,
`start_line`, `end_line`, `callers`, `callees`, `text`.

## Languages

Python, JavaScript, TypeScript/TSX, Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala
and Bash get full symbol and call extraction.

Files in any other language still show up as `file` nodes with their directory structure and
doc/config chunks, so nothing disappears from the map. Adding a language means adding one entry to
`LANG_CFG` in `repo2graph/langs.py`.

## Where it is approximate

Worth knowing before you trust the output:

- **Call resolution is name-based, not type-based.** If a name is overloaded or shadowed,
  repo2graph emits up to 5 candidate edges, each with `1/n` confidence. Local definitions win ties.
  Filter on `confidence == 1.0` when you need precision.
- **Import resolution is path-based, per language** — Python packages and relative imports, JS/TS
  relative specifiers (including `.js` → `.ts`), Go via the `go.mod` module path, Java package
  paths, C/C++ include basenames. Anything it cannot resolve becomes an external `module` node.
- **Some files are skipped**: binaries, anything over 1.5 MB, and the usual vendor and build
  directories. `.gitignore` is honoured when the repo is a git checkout.

## Automating it with GitHub Actions

### Index any repo from the Actions tab

`.github/workflows/index-repo.yml` is a `workflow_dispatch` job: type a repo name, get a graph
back. It clones the target, builds the graph, prints the repo map into the job summary, uploads
`graph-<owner>__<repo>` as a downloadable artifact, and — if you set `publish_release: true` —
attaches a zip to a GitHub Release.

Inputs: `repo`, `ref`, `git_history`, `formats`, `exclude`, `publish_release`. For private
targets, add a `TARGET_REPO_TOKEN` secret with repo read scope; otherwise the job token is used.

All from the terminal:

```bash
gh workflow run index-repo.yml -f repo=psf/requests
gh run watch
gh run download --name graph-psf__requests --dir ./graph
```

### Keep a graph next to your own code

`action.yml` is a composite action you can drop into any repository:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: srinivasan-78/repo2graph@main
  with:
    path: .              # or: repo: some-org/other-repo
    git-history: "500"
    artifact-name: repo-graph
    commit-branch: graph # optional: force-push the graph to an orphan `graph` branch
```

Outputs: `out`, `nodes`, `edges`, `chunks`.

`.github/workflows/self-index.yml` wires this up on every push to `main` plus a weekly cron, which
means a RAG pipeline can always pull a fresh `chunks.jsonl`:

```bash
curl -sL https://raw.githubusercontent.com/srinivasan-78/repo2graph/graph/chunks.jsonl -o chunks.jsonl
```
