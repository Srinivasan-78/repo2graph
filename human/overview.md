# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 438 |
| Functions | 3734 |
| Classes | 240 |
| Total edges | 46537 |
| Languages | python=230, md=83, json=41, typescript=25, yml=24, tsx=8, toml=4, rust=3, javascript=2, yaml=2, txt=2 |
| Built at | f1f8005 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 85 | IMPORTS |
| 2 | repo2graph/export.py | 70 | IMPORTS |
| 3 | repo2graph/parse.py | 67 | IMPORTS |
| 4 | repo2graph/graph.py | 63 | IMPORTS |
| 5 | benchmarks/corpus/python_backend/app/main.py | 59 | IMPORTS |
| 6 | repo2graph/chunks.py | 30 | IMPORTS |
| 7 | repo2graph/security.py | 27 | IMPORTS |
| 8 | tests/test_doc_consistency.py | 27 | CO_CHANGE |
| 9 | repo2graph/events.py | 26 | IMPORTS |
| 10 | tests/conftest.py | 26 | IMPORTS |

## CO_CHANGE hotspots

These files are frequently edited together — treat as implicit dependencies even if no CALLS edge exists.

| File A | File B | Co-change count |
| --- | --- | --- |
| repo2graph/graph.py | tests/test_repo2graph.py | 27 |
| repo2graph/export.py | tests/test_repo2graph.py | 27 |
| repo2graph/cli.py | repo2graph/export.py | 27 |
| repo2graph/graph.py | repo2graph/parse.py | 26 |
| repo2graph/export.py | repo2graph/graph.py | 25 |

## Edge type breakdown

| Edge type | Count | % of total |
| --- | --- | --- |
| TESTS | 19565 | 42.0% |
| CALLS | 10357 | 22.3% |
| CALLS_EXTERNAL | 9330 | 20.0% |
| DEFINES | 4089 | 8.8% |
| IMPORTS | 2027 | 4.4% |
| CO_CHANGE | 625 | 1.3% |
| CONTAINS | 525 | 1.1% |
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
