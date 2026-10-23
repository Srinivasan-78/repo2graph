# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 399 |
| Functions | 3604 |
| Classes | 234 |
| Total edges | 44741 |
| Languages | python=226, md=54, json=41, typescript=25, yml=24, tsx=8, toml=3, javascript=2, yaml=2, txt=2, cff=1 |
| Built at | be8a483 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 80 | IMPORTS |
| 2 | repo2graph/export.py | 66 | IMPORTS |
| 3 | repo2graph/parse.py | 66 | IMPORTS |
| 4 | repo2graph/graph.py | 60 | IMPORTS |
| 5 | benchmarks/corpus/python_backend/app/main.py | 58 | IMPORTS |
| 6 | repo2graph/chunks.py | 28 | IMPORTS |
| 7 | tests/test_doc_consistency.py | 27 | CO_CHANGE |
| 8 | tests/conftest.py | 25 | IMPORTS |
| 9 | repo2graph/cli.py | 24 | CO_CHANGE |
| 10 | repo2graph/events.py | 24 | IMPORTS |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/cli.py | repo2graph/export.py | 27 |
| repo2graph/export.py | tests/test_repo2graph.py | 26 |
| repo2graph/graph.py | tests/test_repo2graph.py | 26 |
| repo2graph/cli.py | repo2graph/graph.py | 25 |
| repo2graph/graph.py | repo2graph/parse.py | 25 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| TESTS | 18941 | 42.3% |
| CALLS | 9870 | 22.1% |
| CALLS_EXTERNAL | 8933 | 20.0% |
| DEFINES | 3947 | 8.8% |
| IMPORTS | 1971 | 4.4% |
| CO_CHANGE | 578 | 1.3% |
| CONTAINS | 482 | 1.1% |
| INHERITS | 19 | 0.0% |

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
