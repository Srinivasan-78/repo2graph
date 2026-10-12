# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 294 |
| Functions | 2556 |
| Classes | 167 |
| Total edges | 17498 |
| Languages | python=167, json=36, md=31, yml=21, typescript=13, tsx=8, toml=3, javascript=2, yaml=2, txt=2, cff=1 |
| Built at | c933a93 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 60 | IMPORTS |
| 2 | repo2graph/export.py | 48 | IMPORTS |
| 3 | repo2graph/graph.py | 48 | IMPORTS |
| 4 | repo2graph/parse.py | 47 | IMPORTS |
| 5 | benchmarks/corpus/python_backend/app/main.py | 34 | IMPORTS |
| 6 | tests/test_doc_consistency.py | 25 | CO_CHANGE |
| 7 | repo2graph/chunks.py | 24 | IMPORTS |
| 8 | tests/test_repo2graph.py | 24 | CO_CHANGE |
| 9 | tests/test_mcp.py | 22 | CO_CHANGE |
| 10 | repo2graph/cli.py | 20 | CO_CHANGE |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/cli.py | repo2graph/export.py | 26 |
| repo2graph/export.py | tests/test_repo2graph.py | 26 |
| repo2graph/graph.py | tests/test_repo2graph.py | 26 |
| repo2graph/graph.py | repo2graph/parse.py | 25 |
| repo2graph/cli.py | repo2graph/graph.py | 24 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| CALLS_EXTERNAL | 6484 | 37.1% |
| CALLS | 6129 | 35.0% |
| DEFINES | 2755 | 15.7% |
| IMPORTS | 1231 | 7.0% |
| CO_CHANGE | 531 | 3.0% |
| CONTAINS | 358 | 2.0% |
| INHERITS | 10 | 0.1% |

## What was skipped

- binary files: 8
- .gitignore entries: 25

## How to explore

```
open .r2g/human/graph.html        # interactive picture
repo2graph query -o .r2g "your question here"   # ask a question
repo2graph stats -o .r2g          # full stats
```
