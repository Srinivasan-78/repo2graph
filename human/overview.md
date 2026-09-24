# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 204 |
| Functions | 1875 |
| Classes | 111 |
| Total edges | 12324 |
| Languages | python=73, md=53, json=41, yml=17, html=5, yaml=2, txt=2, toml=2, javascript=1, lock=1 |
| Built at | 1134e3e |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/parse.py | 35 | IMPORTS |
| 2 | repo2graph/export.py | 33 | IMPORTS |
| 3 | repo2graph/query.py | 33 | IMPORTS |
| 4 | repo2graph/graph.py | 32 | IMPORTS |
| 5 | repo2graph/cli.py | 31 | IMPORTS |
| 6 | tests/test_repo2graph.py | 26 | CO_CHANGE |
| 7 | repo2graph/mcp.py | 22 | IMPORTS |
| 8 | repo2graph/viz.py | 19 | CO_CHANGE |
| 9 | repo2graph/walker.py | 18 | CO_CHANGE |
| 10 | repo2graph/chunks.py | 15 | IMPORTS |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/graph.py | tests/test_repo2graph.py | 24 |
| repo2graph/export.py | tests/test_repo2graph.py | 22 |
| repo2graph/cli.py | repo2graph/export.py | 21 |
| repo2graph/cli.py | tests/test_repo2graph.py | 20 |
| repo2graph/cli.py | repo2graph/graph.py | 19 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| CALLS | 4596 | 37.3% |
| CALLS_EXTERNAL | 4472 | 36.3% |
| DEFINES | 1984 | 16.1% |
| IMPORTS | 760 | 6.2% |
| CO_CHANGE | 281 | 2.3% |
| CONTAINS | 227 | 1.8% |
| INHERITS | 4 | 0.0% |

## What was skipped

- binary files: 12
- files over 1.5 MB: 6
- dotfiles: 1
- .gitignore entries: 21

## How to explore

```
open .r2g/human/graph.html        # interactive picture
repo2graph query -o .r2g "your question here"   # ask a question
repo2graph stats -o .r2g          # full stats
```
