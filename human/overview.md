# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 325 |
| Functions | 2709 |
| Classes | 198 |
| Total edges | 18152 |
| Languages | python=137, md=68, json=50, yml=25, typescript=13, tsx=8, html=5, toml=3, javascript=2, yaml=2, txt=2 |
| Built at | ba559e8 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 50 | IMPORTS |
| 2 | repo2graph/parse.py | 47 | IMPORTS |
| 3 | repo2graph/export.py | 46 | IMPORTS |
| 4 | repo2graph/graph.py | 41 | IMPORTS |
| 5 | repo2graph/mcp.py | 34 | IMPORTS |
| 6 | benchmarks/corpus/python_backend/app/main.py | 26 | IMPORTS |
| 7 | tests/test_repo2graph.py | 26 | CO_CHANGE |
| 8 | repo2graph/cli.py | 25 | CO_CHANGE |
| 9 | repo2graph/viz.py | 21 | CO_CHANGE |
| 10 | repo2graph/chunks.py | 20 | IMPORTS |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/export.py | tests/test_repo2graph.py | 25 |
| repo2graph/graph.py | tests/test_repo2graph.py | 25 |
| repo2graph/cli.py | repo2graph/export.py | 23 |
| repo2graph/graph.py | repo2graph/parse.py | 21 |
| repo2graph/cli.py | repo2graph/graph.py | 21 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| CALLS | 6740 | 37.1% |
| CALLS_EXTERNAL | 6469 | 35.6% |
| DEFINES | 2939 | 16.2% |
| IMPORTS | 1194 | 6.6% |
| CO_CHANGE | 404 | 2.2% |
| CONTAINS | 396 | 2.2% |
| INHERITS | 10 | 0.1% |

## What was skipped

- binary files: 12
- files over 1.5 MB: 6
- .gitignore entries: 25

## How to explore

```
open .r2g/human/graph.html        # interactive picture
repo2graph query -o .r2g "your question here"   # ask a question
repo2graph stats -o .r2g          # full stats
```
