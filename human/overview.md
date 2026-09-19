# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 178 |
| Functions | 1202 |
| Classes | 67 |
| Total edges | 7848 |
| Languages | python=51, md=50, json=41, yml=18, html=5, yaml=2, txt=2, toml=2, javascript=1, lock=1 |
| Built at | 2d0ffd9 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 30 | IMPORTS |
| 2 | repo2graph/parse.py | 28 | CO_CHANGE |
| 3 | repo2graph/export.py | 26 | IMPORTS |
| 4 | repo2graph/graph.py | 23 | IMPORTS |
| 5 | repo2graph/cli.py | 22 | IMPORTS |
| 6 | tests/test_repo2graph.py | 21 | CO_CHANGE |
| 7 | repo2graph/viz.py | 19 | CO_CHANGE |
| 8 | repo2graph/walker.py | 18 | CO_CHANGE |
| 9 | repo2graph/fetch.py | 14 | CO_CHANGE |
| 10 | repo2graph/mcp.py | 14 | IMPORTS |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/export.py | tests/test_repo2graph.py | 18 |
| repo2graph/graph.py | tests/test_repo2graph.py | 17 |
| repo2graph/cli.py | tests/test_repo2graph.py | 16 |
| repo2graph/graph.py | repo2graph/parse.py | 14 |
| repo2graph/cli.py | repo2graph/export.py | 14 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| CALLS_EXTERNAL | 2895 | 36.9% |
| CALLS | 2810 | 35.8% |
| DEFINES | 1267 | 16.1% |
| IMPORTS | 485 | 6.2% |
| CONTAINS | 201 | 2.6% |
| CO_CHANGE | 187 | 2.4% |
| INHERITS | 3 | 0.0% |

## What was skipped

- binary files: 8
- files over 1.5 MB: 6
- .gitignore entries: 18

## How to explore

```
open .r2g/human/graph.html        # interactive picture
repo2graph query -o .r2g "your question here"   # ask a question
repo2graph stats -o .r2g          # full stats
```
