# Retrieval benchmark: real repositories

Does repo2graph put the code that answers a question into an agent's context, at a fixed token
budget, more often than grep does? This page measures that on four third-party repositories and
reports the result as it came out, including where repo2graph loses.

**Short version:** yes, now, at every budget on every set -- but two of the nine cells are ties,
not wins, and the margin on the lexical set is 4-6 points, which is one or two questions. It did
not used to: through 2.2 a grep-then-read baseline beat it clearly at 4,000 and 8,000 tokens. The
cause was never parsing or retrieval -- every answer was already in the index -- but which chunks
won the budget, and it is [fixed below](#what-was-wrong-and-what-fixed-it). Because that fix was
developed against these 35 questions, the number that matters most is the
[held-out set](#results-the-22-held-out-questions): 22 questions on two repositories the change
never saw.

## Setup

| | |
|---|---|
| **Repositories** | [Flask](https://github.com/pallets/flask) 3.1.2, [requests](https://github.com/psf/requests) 2.32.5, [FastAPI](https://github.com/fastapi/fastapi) 0.118.0 (Python); [Hono](https://github.com/honojs/hono) 4.9.0 (TypeScript). Each pinned to the commit in [`repos.json`](repos.json). None was written by this project. |
| **Held-out repositories** | [click](https://github.com/pallets/click) 8.1.8 (Python), [axios](https://github.com/axios/axios) 1.7.9 (JavaScript), pinned in [`repos_holdout.json`](repos_holdout.json). Deliberately disjoint from the four above. |
| **Questions** | 35 lexical questions in [`tasks.json`](tasks.json), phrased the way someone new to the codebase asks: *"how are HTTP redirects followed"*, *"what calls dispatch_request"*. 10 cross-file structural questions in [`tasks_structural.json`](tasks_structural.json). 22 held-out questions in [`tasks_holdout.json`](tasks_holdout.json). Each names the one to three definitions that answer it, with line ranges read off the pinned commit before any tool was run. |
| **Scoring** | A definition is *found* when its first line and the next nine (or all of it, if shorter) are in the returned text. Mentioning the file, or returning only a signature, does not count. For repo2graph, each returned line is aligned to the source file, so only code actually present in the pack earns credit. For "what calls X" questions, only the callers count, not X itself. |
| **Budgets** | 2,000, 4,000 and 8,000 tokens (`len(text) // 4` for every retriever). |

The four retrievers:

- **`repo2graph`**: `Index.pack_context` with the MCP server's `repo_search` defaults
  (`k=8`, `hops=1`, secrets excluded). The repo-map header it prepends counts against the budget.
- **`repo2graph-cite`**: the same call with `--neighbours=cite`. Graph neighbours arrive as ~15
  tokens of signature metadata instead of full chunk bodies, so they act as a navigation index
  rather than spending the budget.
- **`repo2graph-bm25`**: the same call with `expand_graph=False`. The difference between this row
  and the first two is what the graph adds.
- **`ripgrep`**: `rg -i -F` for the question's words (stop words dropped), then read ±15 lines
  around the hits in order of how many distinct question words each window contains, until the
  budget is spent. This is the grep-then-read loop a coding agent runs, minus the model's
  judgement in choosing search terms. Our untested expectation is that a real agent choosing its
  own terms would do better than this row.

## Results: the 35 lexical questions

From [`results.json`](results.json), produced by
[`../../scripts/bench_real_repos.py`](../../scripts/bench_real_repos.py):

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | **repo2graph** | **41%** | 14 / 35 | 16 / 35 | 1,966 |
| 2,000 | repo2graph-cite | 41% | 14 / 35 | 16 / 35 | 1,896 |
| 2,000 | repo2graph-bm25 | 41% | 14 / 35 | 16 / 35 | 1,816 |
| 2,000 | ripgrep | 35% | 9 / 35 | 13 / 35 | 1,962 |
| 4,000 | repo2graph | 61% | **19 / 35** | **24 / 35** | 3,966 |
| 4,000 | repo2graph-cite | 59% | 18 / 35 | 23 / 35 | 3,525 |
| 4,000 | repo2graph-bm25 | 59% | 18 / 35 | 23 / 35 | 3,232 |
| 4,000 | ripgrep | 61% | 18 / 35 | 23 / 35 | 3,873 |
| 8,000 | **repo2graph** | **76%** | **24 / 35** | 26 / 35 | 7,695 |
| 8,000 | repo2graph-cite | 67% | 20 / 35 | 25 / 35 | 4,721 |
| 8,000 | repo2graph-bm25 | 67% | 20 / 35 | 25 / 35 | 4,137 |
| 8,000 | ripgrep | 72% | 22 / 35 | 26 / 35 | 7,652 |

**4,000 tokens is a tie, not a win** -- 61% each, and repo2graph is ahead on fully-answered
questions (19 vs 18) only by one. At 2,000 and 8,000 it leads by 6 and 4 points, which on 46
evidence definitions is three and two definitions. Treat all three as "no longer losing", not as
a rout.

Default full-body expansion is now the best variant at every budget, which reverses the earlier
finding that it added nothing over BM25 alone. Citation mode no longer leads: compressing a
neighbour to a signature line was a way to stop containers eating the budget, and the seed
re-rank addresses that cause directly instead.

At 4,000 tokens, by question type (definitions found): *concept* (28 questions) 23/35 for
repo2graph against ripgrep's 24/35 -- grep is still marginally ahead on the plain
"how does X work" question; *trace* (5 questions) 4/9 against 3/9; *relationship*
(2 questions, "what calls X") 1/2 for both. Neither finds `Session.request` as the caller of
`prepare_request`.

Head-to-head, task by task:

| Budget | repo2graph finds more | ripgrep finds more | tie |
|---:|---:|---:|---:|
| 4,000 | 6 | 6 | 23 |
| 8,000 | 5 | 3 | 27 |

At 4,000 the aggregate tie is a real tie and not an average over a lopsided split: six questions
each. The three grep still wins at 8,000 are `fastapi-08`, `hono-01` and `hono-03`. Every row,
with the definitions each retriever missed, is in `results.json`.

## Results: the 10 cross-file structural questions

[`tasks_structural.json`](tasks_structural.json) holds questions whose evidence provably spans
cross-file dependency edges — a caller in file A delegating to a helper in file B, or a
transitive dependency path. These are the questions graph expansion exists for.

| Budget | repo2graph | repo2graph-cite | repo2graph-bm25 | ripgrep |
|---:|---:|---:|---:|---:|
| 2,000 | **60%** (1,970 tok) | 60% (1,984 tok) | 60% (1,872 tok) | 20% (1,978 tok) |
| 4,000 | **80%** (3,959 tok) | 60% (3,539 tok) | 60% (3,341 tok) | 20% (3,969 tok) |
| 8,000 | **100%** (7,808 tok) | 60% (4,621 tok) | 60% (4,462 tok) | 70% (7,978 tok) |

This is the set graph expansion exists for, and it is where the margin is wide: +40, +60 and
+30 points. Two things the table says that the headline does not:

- **100% at 8,000 tokens is 10 questions out of 10.** On a 10-task set that is one task from
  90%, and the set is small enough that a single badly-chosen question would move it.
- **The graph is doing the work, not the re-rank.** `repo2graph-bm25` -- the same ranking with
  expansion switched off -- sits at 60% at every budget. The gap between that row and the first
  is what one hop of CALLS/IMPORTS/INHERITS buys.

With 10 tasks, one task is 10 percentage points. Treat every number on this table as ±1 task.

## Results: the 22 held-out questions

Everything above was used to *develop* the ranking fix, so none of it can judge it. This set
exists to: 22 questions across [click](https://github.com/pallets/click) 8.1.8 and
[axios](https://github.com/axios/axios) 1.7.9, two repositories that appear in no other task
file here, written by reading their source and never run against a retriever before the
questions and line ranges were fixed.

| Budget | repo2graph | repo2graph (2.2) | ripgrep |
|---:|---:|---:|---:|
| 2,000 | 50% | 50% | 50% |
| 4,000 | **79%** | 67% | 58% |
| 8,000 | **79%** | 71% | 67% |

The fix moves this set by +12 and +8 points at 4,000 and 8,000 tokens, having never seen it.
That is the evidence that it is a retrieval improvement and not a fit to 35 questions.

Three caveats, because this set is the load-bearing one:

- **2,000 tokens is a three-way tie at 50%**, unchanged by the fix. At that budget the pack
  holds three or four chunks and which ones they are matters less than how many fit.
- **The questions were written by this project**, like the other two sets. They were written
  from the source rather than from any tool's output, and before any retriever ran, but a
  question phrased by someone who has just read the function tends to share vocabulary with it.
  That favours every lexical retriever here, grep included, and it is the main reason to read
  the *difference* between columns rather than the absolute numbers.
- **Two repositories and 22 questions is small.** One question is 4-5 points.

Questions on repositories you know well are the most useful contribution this benchmark can
take, and held-out ones most of all. Open a PR against `tasks_holdout.json`.

## Results: simulated agent loops

`search` → `read` → `answer`, via [`../../scripts/agent_eval.py`](../../scripts/agent_eval.py).
**No model is in the loop.** The "agent" is a deterministic policy running real ripgrep and a
real repo2graph index against real checked-out repositories. It measures what the retrieval
surface makes reachable in a few turns, not what a model would do with it.

Structural tasks ([`agent_results_structural.json`](agent_results_structural.json), 10 tasks):

| Method | Success | Mean turns | Mean tokens | Precision per read |
|---|---:|---:|---:|---:|
| repo2graph | **7 / 10 (70%)** | **3.3** | 4,006 | 2.4% |
| ripgrep | 1 / 10 (10%) | 6.0 | **1,879** | **11.0%** |

General tasks ([`agent_results.json`](agent_results.json), the same 35 lexical questions):

| Method | Success | Mean turns | Mean tokens | Precision per read |
|---|---:|---:|---:|---:|
| repo2graph | **19 / 35 (54%)** | **1.9** | 7,133 | 2.4% |
| ripgrep | 7 / 35 (20%) | 3.6 | **939** | **37.8%** |

repo2graph answers more of these in fewer turns, and it is not cheap: 4,006 tokens against
ripgrep's 1,879 on the structural set, and 7,133 against 939 on the general set. Precision per
read — the fraction of read tokens that belong to an evidence definition — is where the cost
shows up: 2.4% against ripgrep's 11.0% and 37.8%. repo2graph is buying recall with context, and
the single-shot tables above are the budget-matched comparison that this one deliberately is not.

## What was wrong, and what fixed it

The 2.2 diagnosis listed four causes, read off the losing packs. Measuring them first settled
which one mattered. On the 35-question set at an 8,000-token budget:

- every one of the 46 evidence definitions was **present in some chunk** -- nothing was lost to
  parsing or chunking;
- an **oracle packer fit 100% of them** inside the budget, so the budget was never the bound;
- **54% of real pack tokens went to container chunks**;
- the chunk carrying the answer ranked in the top 8 for 22 of 46, and 8th-24th for a further 15.

So the index already held every answer and the budget could already afford it. The loss was
entirely in which chunks won the seed slots.

### The container problem

The chunker emits every method as its own chunk and cuts it out of the parent. Flask's
`class Flask` chunk therefore spans lines 81-1536, costs ~1,150 tokens, and contains **no method
body at all** -- it is a header, a docstring and metadata. BM25 ranks it highly on almost any
question about Flask, because a long class docstring names everything the class does. It then
spends an eighth of an 8,000-token budget saying nothing the reader can act on.

Two corrections, both applied as a re-rank over `score_rrf`'s output rather than as a change to
BM25 (RRF maps scores onto `1/(60 + rank)`, nearly flat across the top of the list, so a modest
multiplier separates candidates BM25 could not -- without disturbing the lexical scoring that
`repo2graph query` and the golden tests pin):

- a **penalty on container chunks**, applied only above a token floor, because the cost is the
  problem and a small container does not have one;
- a **boost when a chunk's declared name shares content words with the question**, capped at two
  terms, and withheld from containers.

### What was tried and rejected

Recorded because the negative results were as informative as the fix, and because each looked
obviously right beforehand:

| Idea | Result |
|---|---|
| Boost chunks whose **path** matches the question (`openapi/utils.py` for an OpenAPI question) | Nothing at any budget, slightly negative at 4,000. The strongest-seeming intuition of the four. |
| **Drop test files** from seed candidates | Neutral on this set, actively harmful on the held-out set |
| Raise **`k` from 8 to 40** so the budget is actually filled (the old default left 4,847 of 8,000 tokens unspent) | +2 on the lexical set, but the structural set falls 100% → 80% at 8,000: extra lexical seeds crowd out the graph neighbours those questions are answered by. Reverted. |
| Penalise containers **by kind alone**, with no cost floor | Dropped the demo fixture's 91-token `app/store.py` residual -- the chunk answering "trace a request to persistence" -- for no budget saved. Caught by `test_demo.py`, not by this benchmark. |
| Let the name boost apply to **containers** | Promoted a test helper class literally named `Request` to rank 1 on "trace an order request from route to persistence", ahead of every route body. |

### Still unfixed

- **Graph expansion does not read the direction a question asks for.** For *"what calls
  prepare_request"* the pack still fills with `prepare_request`'s callees rather than its caller
  `Session.request`, which is a confidence-1.0 `CALLS` edge the graph already holds. Both
  retrievers miss it.
- **Six of the 46 definitions rank below 100** even after the re-rank, so no packing change can
  reach them; those need better scoring, not better selection.
- **Grep still wins three questions at 8,000 tokens** (`fastapi-08`, `hono-01`, `hono-03`).

## Corrections

- **2026-10-05 — the ranking fix, and a clean regeneration.** `results.json` now records
  `"repo2graph_dirty": false`, so the commit it names reproduces it; the previous publication
  was generated from a dirty tree and said so. repo2graph's lexical numbers moved from
  30/37/48% to 41/61/76% and its structural numbers from 20/70/80% to 60/80/100%. The cause is
  [documented above](#what-was-wrong-and-what-fixed-it) and is a seed-ranking change, not a new
  index: no artifact format changed and the graph is identical. Because that fix was developed
  against the 35 questions on this page, `tasks_holdout.json` was added at the same time so the
  claim rests on repositories the change never saw.

- **2026-09-28 — scorer.** The first published version overstated repo2graph by 5–7 points
  (40/46/60%, against a corrected 35/39/54%). Its scorer credited a returned chunk with the
  symbol's whole line range, so a later part of a split function, or a file chunk with its
  symbols cut out, counted as containing the definition's first lines when it did not. It also
  listed the queried function as an answer to its own "what calls X" question. Both are fixed;
  grep's numbers were unaffected by the first bug.
- **2026-09-28 — rerun after indexing fixes.** repo2graph stopped silently dropping definitions
  that share a name within a file (Go methods on different types, overloads, nested closures such
  as Flask's two `View.as_view.view` functions) and began indexing Kotlin functions. The index is
  now more complete, and repo2graph's numbers moved from 35/39/54% to 30/39/52%. The newly
  separate definitions compete for the same eight seed slots, which is the seed-ranking weakness
  above.
- **2026-09-30 — structural agent numbers regenerated.** The published figures (50% success,
  2.2 turns, 2,282 tokens) came from a run that predated citation-mode neighbours and weighted
  RRF and had no committed artifact. Regenerated on the current tree: 70% success, 3.3 turns,
  4,006 tokens. `agent_eval.py` previously defaulted `--out` to `agent_results.json` regardless
  of which task file it read, so a structural run silently overwrote the general one; the two
  results now live in separate files.

## What this does not measure

- **End-to-end agent success.** No model reads any of the output. An agent choosing its own grep
  terms would likely beat the `ripgrep` rows, and an agent using `repo_neighbours` to walk from a
  hit to its callers is not modelled at all.
- **Latency.** Every retriever here answers in milliseconds; it does not separate them.
- **Other repositories or languages.** Four repositories, 35 lexical and 10 structural questions
  is a small sample. Adding questions, especially on repositories you know well, is the most
  useful contribution this benchmark can take. Open a PR against `tasks.json`.

## Reproduce

Needs `git` and [ripgrep](https://github.com/BurntSushi/ripgrep). `--rg` takes a different
ripgrep command, `--cache` a directory for the clones, `--budgets` a comma-separated list.

```bash
pip install -e .

# single-shot retrieval, the four dev repositories, writes results.json
python scripts/bench_real_repos.py

# the held-out set: click + axios, writes results_holdout.json
python scripts/bench_real_repos.py     --repos benchmarks/real/repos_holdout.json     --tasks benchmarks/real/tasks_holdout.json     --out   benchmarks/real/results_holdout.json

# simulated agent loop, one task set at a time -- always pass --out
python scripts/agent_eval.py --tasks benchmarks/real/tasks.json \
    --out benchmarks/real/agent_results.json
python scripts/agent_eval.py --tasks benchmarks/real/tasks_structural.json \
    --out benchmarks/real/agent_results_structural.json
```

`bench_real_repos.py` refuses to run if a tag no longer resolves to the commit the questions were
written against, so the source under test cannot drift silently. Which definitions are found does
not depend on the platform. Token counts can differ by a few tokens between a CRLF checkout (the
committed results were produced on Windows with `core.autocrlf=true`) and an LF one.

## The synthetic regression suite

[`../corpus/`](../corpus/README.md) is a separate, smaller suite written by this project to
exercise specific parser patterns. It is a regression gate, not evidence of how repo2graph
compares with anything.
