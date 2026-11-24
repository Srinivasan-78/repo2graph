# Repo overview: repo2graph

## At a glance

| Metric | Value |
| --- | --- |
| Files indexed | 430 |
| Functions | 3682 |
| Classes | 239 |
| Total edges | 45756 |
| Languages | python=228, md=83, json=41, typescript=25, yml=24, tsx=8, toml=3, javascript=2, yaml=2, txt=2, cff=1 |
| Built at | 9265748 |

## Top 10 most-connected files (by in-degree)

| Rank | File | In-degree | Dominant edge type |
| --- | --- | --- | --- |
| 1 | repo2graph/query.py | 82 | IMPORTS |
| 2 | repo2graph/export.py | 69 | IMPORTS |
| 3 | repo2graph/parse.py | 66 | IMPORTS |
| 4 | repo2graph/graph.py | 61 | IMPORTS |
| 5 | benchmarks/corpus/python_backend/app/main.py | 59 | IMPORTS |
| 6 | repo2graph/chunks.py | 28 | IMPORTS |
| 7 | tests/test_doc_consistency.py | 27 | CO_CHANGE |
| 8 | repo2graph/events.py | 26 | IMPORTS |
| 9 | repo2graph/security.py | 26 | IMPORTS |
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
| TESTS | 19290 | 42.2% |
| CALLS | 10175 | 22.2% |
| CALLS_EXTERNAL | 9122 | 19.9% |
| DEFINES | 4030 | 8.8% |
| IMPORTS | 1987 | 4.3% |
| CO_CHANGE | 618 | 1.4% |
| CONTAINS | 515 | 1.1% |
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
