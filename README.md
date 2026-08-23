# repo2graph

Turn any repository into a code **graph** plus **graph-aware chunks** that can be fed straight
into a RAG system. Language support comes from tree-sitter, so it works on unfamiliar repos
without per-repo configuration.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

Run the tests:

```bash
.venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest
```

## Build an index

```bash
repo2graph build /path/to/repo -o /path/to/repo/.r2g --git-history 200
```

Outputs in the `-o` directory:

| File | Contents |
|---|---|
| `nodes.jsonl` | one JSON object per node |
| `edges.jsonl` | one JSON object per edge |
| `chunks.jsonl` | retrieval chunks: code text prefixed with a graph-context header |
| `graph.graphml` | NetworkX/Gephi/igraph-loadable graph |
| `graph.cypher` | idempotent `MERGE` script for Neo4j / Memgraph |
| `overview.md` | repo map: languages, hub files, most-called symbols |
| `stats.json` | node/edge/symbol counts, parse errors |

Useful flags: `--include '**/*.py'`, `--exclude '**/test/**'`, `--max-files N`,
`--formats jsonl,cypher`, `--no-chunks`, `--git-history N`.

## Graph model

Nodes: `repo`, `dir`, `file`, `symbol` (function / method / class / struct / trait / interface /
type / module), `module` (external import target), `external` (unresolved call target).

Edges:

- `CONTAINS` — repo → dir → file
- `DEFINES` — file → symbol, symbol → nested symbol
- `IMPORTS` — file → file (`internal: true`) or file → external module
- `CALLS` — symbol → symbol, with `count` and `confidence` (name-based resolution;
  local definitions win, and ties emit up to 5 candidate edges each with `1/n` confidence)
- `CALLS_EXTERNAL` — symbol → external name (stdlib/third-party)
- `INHERITS` — symbol → base class / interface
- `CO_CHANGE` — file ↔ file, from `--git-history` (files edited together in ≥3 commits)

Node ids are stable and human-readable: `file:pkg/mod.py`, `sym:pkg/mod.py::Class.method`,
`module:requests`, `dir:pkg`.

## Chunks

One chunk per symbol (split at ~4000 chars with 8 lines of overlap), plus a residual chunk per
file covering the code no symbol claimed, plus whole-file chunks for docs and config. Every
chunk's text starts with a header carrying its graph neighborhood, which is what makes the
embedding actually match questions about behavior:

```
# file: repo2graph/graph.py
# function: resolve_import  (lines 66-99, python)
# called by: repo2graph/graph.py::build
# calls: Path, replace, str, list, startswith, sub, append
# doc: Map an import target to an in-repo file path when possible.
def resolve_import(...):
    ...
```

Chunk fields: `id`, `node_id`, `type`, `kind`, `path`, `lang`, `name`, `qualname`,
`start_line`, `end_line`, `callers`, `callees`, `text`.

## Query without embeddings

A built-in retriever does lexical scoring (BM25-ish, with identifier splitting) and then expands
the winners across the graph, so callers and callees of a hit come along:

```bash
repo2graph query "how does routing match a path" -o .r2g -k 8 --hops 1
repo2graph query "auth middleware" -o .r2g --json | jq '.[].path'
```

## Wiring into a vector RAG stack

`chunks.jsonl` is the embedding input; keep `node_id` as the vector's metadata key so the graph
can expand results after retrieval.

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

The recommended pattern is vector search for seeds, then one hop of `CALLS` / `IMPORTS` /
`DEFINES` / `INHERITS` expansion for context, with `overview.md` prepended as a repo-level system
prompt.

## Neo4j

```bash
repo2graph build /path/to/repo -o .r2g --formats jsonl,cypher
cypher-shell -u neo4j -p password -f .r2g/graph.cypher
```

## Languages

Python, JavaScript, TypeScript/TSX, Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala,
Bash. Files in other languages still become `file` nodes with directory and doc/config chunks;
adding a language means adding one entry to `LANG_CFG` in `repo2graph/langs.py`.

## Accuracy notes

- Call resolution is name-based, not type-based: a call to an overloaded or shadowed name emits
  up to 5 low-confidence edges. Filter on `confidence == 1.0` when precision matters.
- Import resolution is path-based per language (Python packages and relative imports, JS/TS
  relative specifiers including `.js` → `.ts`, Go via the `go.mod` module path, Java package
  paths, C/C++ include basenames). Unresolved targets become external `module` nodes.
- Binary files, files over 1.5 MB, and standard vendor/build directories are skipped;
  `.gitignore` is honored when the repo is a git checkout.

## GitHub automation

### One command, any GitHub repo

```bash
repo2graph github owner/repo -o out/owner__repo --git-history 200
repo2graph gh https://github.com/psf/requests --ref v2.32.3 --depth 50 -o out/requests
```

Clones to a temp directory (removed afterwards unless `--keep-clone DIR`), builds the graph, and
writes an extra `index.json` with the repo slug, indexed commit SHA and counts. Private repos work
with `--token` or `$GH_TOKEN` / `$GITHUB_TOKEN`. `--depth 0` (the default) keeps full history,
which `--git-history` needs for `CO_CHANGE` edges.

### Workflow: type a repo name, download the graph

`.github/workflows/index-repo.yml` is a `workflow_dispatch` job. Run it from the Actions tab (or
`gh workflow run index-repo.yml -f repo=psf/requests -f git_history=200`) and it:

1. clones the target repo,
2. builds the graph,
3. prints the repo map into the job summary,
4. uploads `graph-<owner>__<repo>` as a downloadable artifact,
5. optionally (`publish_release: true`) attaches a zip to a GitHub Release.

Inputs: `repo`, `ref`, `git_history`, `formats`, `exclude`, `publish_release`. For private
targets, add a `TARGET_REPO_TOKEN` secret with repo read scope; otherwise the job token is used.

Fetch the result without leaving the terminal:

```bash
gh workflow run index-repo.yml -f repo=psf/requests
gh run watch
gh run download --name graph-psf__requests --dir ./graph
```

### Composite action: keep a graph next to your code

`action.yml` is reusable from any repository:

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

Outputs: `out`, `nodes`, `edges`, `chunks`. `.github/workflows/self-index.yml` wires it up on
push to `main` plus a weekly cron, so a RAG pipeline can pull the latest `chunks.jsonl` from the
artifact or straight from the `graph` branch:

```bash
curl -sL https://raw.githubusercontent.com/srinivasan-78/repo2graph/graph/chunks.jsonl -o chunks.jsonl
```

