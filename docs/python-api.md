<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌‌​​​​​‌​​‌‌​​​‌​​‌​‌‌​‌​‌​‌​‌​‌​​‌‌‌‌​‌​‌​​​​​‌​​​​‌‌​‌​​​‌​​​‌‌​‌​​‌​‌​‌‌‌‌‌​‌‌​‌‌​‌​‌‌​​​‌​​‌​‌‌‌‌‌​‌​​​‌​‌​‌‌‌‌​‌​​‌​‌‌‌‌‌​‌​​‌​​‌​‌​‌‌‌‌‌​‌​‌‌​‌​​‌​​​​​‌​‌​‌​​​‌​‌​‌​​‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.pLKUOPCDi_mb_Ez_I_ZAQR
-->
# Python API

Everything the CLI does is available as a library.

## Build, export, fetch, draw

```python
from pathlib import Path
from repo2graph import build, iter_chunks, write_html
from repo2graph.export import dump_all
from repo2graph.fetch import index_github
from repo2graph.viz import LoadedGraph

# 1. Build the repository graph in memory
g = build(Path("."), git_history=200, jobs=0)
print(f"Graph ready: {len(g.nodes)} nodes, {len(g.edges)} edges")

# 2. Stream retrieval chunks to disk and dump artifacts.
#    Chunks are yielded one at a time, keeping memory bounded.
written, count = dump_all(
    g,
    chunks=iter_chunks(g),
    outdir=Path(".r2g"),
    formats={"jsonl", "graphml", "cypher", "overview", "html"},
    viz_nodes=300,
)
print(f"Wrote {len(written)} artifacts ({count} chunks) to .r2g")

# 3. Or clone and index a remote GitHub repository in one call
meta = index_github("psf/requests", outdir=Path("out/requests"), git_history=200)
print(f"Indexed {meta['repo']} @ {meta['commit']}: {meta['nodes']} nodes, {meta['chunks']} chunks")

# 4. Redraw the interactive HTML map from an existing index with a custom node cap
write_html(LoadedGraph(Path(".r2g")), Path(".r2g/human/graph.html"), viz_nodes=80)
```

## Graph-aware retrieval

`repo2graph.query.Index` combines BM25 lexical search with graph traversal and
budget-bounded packing out of the box. It is the same object the CLI, the Action
and the [MCP server](mcp.md) all call.

```python
from repo2graph.query import Index, format_pack, read_jsonl

# Read chunks safely across operating systems (lossless newline & encoding handling)
chunks = read_jsonl(".r2g/agent/chunks.jsonl")

# 1. GraphRAG pack: bounded total markdown, citations, and repo map prepend
idx = Index(".r2g")
pack = idx.pack_context(
    "how does session auth work?",
    k=8,
    hops=1,
    budget_chars=24000,  # bounds the WHOLE markdown; 0 means unbounded
    min_confidence=1.0,  # drop ambiguous CALLS edges (CALLS only)
    expand_graph=True,  # False = lexical seeds only
    exclude_secrets=False,  # True drops dotfiles/.env/.pem/... from the pack
)
print(pack["markdown"], pack["used_chars"], pack["truncated"])

# 2. Best matching chunks + their 1-hop graph neighbours (what they call/inherit/import).
#    budget_chars here bounds the chunk text only — see "The two --budget flags"
#    in docs/cli.md.
results = idx.retrieve("how does authentication verify tokens", k=8, hops=1, budget_chars=24000)

for r in results:
    print(f"[{r['why']}] {r['path']}::{r['qualname']} (score: {r['score']})")

# Format retrieved chunks into a clean prompt context for an LLM
prompt_context = format_pack(results)

# 3. Or expand existing vector search hits across the code graph
hits = ["sym:app/auth.py::login"]
for node_id, edge_type, direction, src in idx.expand(hits, hops=1):
    for extra in idx.by_node.get(node_id, [])[:1]:
        print(edge_type, direction, extra["path"], extra["qualname"])
```

That third pattern is the one to reach for if you already have a vector database.
Keep each chunk's `node_id` when you store it; that is the handle that lets you
jump back onto the map after a search, and turn a flat list of hits into hits plus
the code around them.

## Loading it into Neo4j

```bash
repo2graph build /path/to/project -o .r2g --formats jsonl,cypher
cypher-shell -u neo4j -p password -f .r2g/agent/graph.cypher
```

The script is idempotent — running it twice is safe. It works against Memgraph
too.

`human/graph.graphml` opens in yEd and Gephi already laid out (so it does not look
like a hairball), and reads fine in NetworkX and igraph.
