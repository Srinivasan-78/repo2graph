# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 390 |
| Functions | 3374 |
| Classes | 223 |
| Total edges | 42089 |
| Languages | python=219, md=54, json=41, typescript=25, yml=23, tsx=8, toml=3, javascript=2, yaml=2, txt=2, cff=1 |
| Built at | 48afbeb |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 76 | IMPORTS |
| 2 | repo2graph/parse.py | 65 | IMPORTS |
| 3 | repo2graph/export.py | 62 | IMPORTS |
| 4 | repo2graph/graph.py | 60 | IMPORTS |
| 5 | benchmarks/corpus/python_backend/app/main.py | 53 | IMPORTS |
| 6 | repo2graph/chunks.py | 28 | IMPORTS |
| 7 | tests/test_doc_consistency.py | 26 | CO_CHANGE |
| 8 | repo2graph/cli.py | 24 | CO_CHANGE |
| 9 | tests/test_mcp.py | 24 | CO_CHANGE |
| 10 | tests/test_repo2graph.py | 24 | CO_CHANGE |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/cli.py | repo2graph/export.py | 26 |
| repo2graph/export.py | tests/test_repo2graph.py | 26 |
| repo2graph/graph.py | tests/test_repo2graph.py | 26 |
| repo2graph/cli.py | repo2graph/graph.py | 25 |
| repo2graph/graph.py | repo2graph/parse.py | 25 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| TESTS | 17799 | 42.3% |
| CALLS | 9300 | 22.1% |
| CALLS_EXTERNAL | 8369 | 19.9% |
| DEFINES | 3706 | 8.8% |
| IMPORTS | 1853 | 4.4% |
| CO_CHANGE | 572 | 1.4% |
| CONTAINS | 473 | 1.1% |
| INHERITS | 17 | 0.0% |

## What was skipped

- binary files: 9
- dotfiles: 2
- .gitignore entries: 4

## How to explore

```
open .r2g/human/graph.html        # interactive picture
repo2graph query -o .r2g "your question here"   # ask a question
repo2graph stats -o .r2g          # full stats
```
