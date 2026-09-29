# Retrieval benchmark: real repositories

Does repo2graph put the code that answers a question into an agent's context, at a fixed token
budget, more often than grep does? This page measures that on four third-party repositories, and
reports the result as it came out, including where repo2graph loses.

**Short version:** on these 35 questions, it currently does not. At every budget measured, a
grep-then-read baseline finds at least as much of the answer, and at 4,000 and 8,000 tokens it
finds clearly more.
Graph expansion adds no recall over repo2graph's own BM25 seeds on this set. The causes are
diagnosed [below](#why-repo2graph-loses-here), and they are retrieval-ranking problems, not
parsing ones.

## Setup

| | |
|---|---|
| **Repositories** | [Flask](https://github.com/pallets/flask) 3.1.2, [requests](https://github.com/psf/requests) 2.32.5, [FastAPI](https://github.com/fastapi/fastapi) 0.118.0 (Python); [Hono](https://github.com/honojs/hono) 4.9.0 (TypeScript). Each pinned to the commit in [`benchmarks/real/repos.json`](../benchmarks/real/repos.json). None was written by this project. |
| **Questions** | 35, in [`benchmarks/real/tasks.json`](../benchmarks/real/tasks.json), phrased the way someone new to the codebase asks: *"how are HTTP redirects followed"*, *"what calls dispatch_request"*. Each names the one to three definitions that answer it, with line ranges read off the pinned commit before any tool was run. |
| **Scoring** | A definition is *found* when its first line and the next nine (or all of it, if shorter) are in the returned text. Mentioning the file, or returning only a signature, does not count. For repo2graph, each returned line is aligned to the source file, so only code actually present in the pack earns credit (a chunk's cite range describes the whole symbol, not the part returned). For "what calls X" questions, only the callers count, not X itself. |
| **Budgets** | 2,000, 4,000 and 8,000 tokens (`len(text) // 4` for every retriever). |

The three retrievers:

- **`repo2graph`**: `Index.pack_context` with the MCP server's `repo_search` defaults
  (`k=8`, `hops=1`, secrets excluded). The repo-map header it prepends counts against the budget.
- **`repo2graph-bm25`**: the same call with `expand_graph=False`. The difference between these
  two rows is what the graph adds.
- **`ripgrep`**: `rg -i -F` for the question's words (stop words dropped), then read ±15 lines
  around the hits in order of how many distinct question words each window contains, until the
  budget is spent. This is the grep-then-read loop a coding agent runs, minus the model's
  judgement in choosing search terms. Our untested expectation is that a real agent choosing its
  own terms would do better than this row. Numbers and hyphenated words are handled simply
  (numbers dropped, `access-control` kept as one term).

## Results

From [`benchmarks/real/results.json`](../benchmarks/real/results.json), produced by
[`scripts/bench_real_repos.py`](../scripts/bench_real_repos.py):

| Budget | Retriever | Evidence found | Questions fully answered | Questions with any evidence | Mean tokens used |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | 30% | 10 / 35 | 13 / 35 | 1,977 |
| 2,000 | repo2graph-bm25 | 30% | 10 / 35 | 13 / 35 | 1,824 |
| 2,000 | ripgrep | **35%** | 9 / 35 | 13 / 35 | 1,962 |
| 4,000 | repo2graph | 39% | 14 / 35 | 17 / 35 | 3,973 |
| 4,000 | repo2graph-bm25 | 39% | 14 / 35 | 17 / 35 | 3,670 |
| 4,000 | ripgrep | **61%** | **18 / 35** | **23 / 35** | 3,873 |
| 8,000 | repo2graph | 52% | 15 / 35 | 22 / 35 | 7,836 |
| 8,000 | repo2graph-bm25 | 52% | 15 / 35 | 22 / 35 | 5,588 |
| 8,000 | ripgrep | **72%** | **22 / 35** | **26 / 35** | 7,652 |

At 4,000 tokens, by question type (definitions found): *concept* questions (28) 17/35 for
repo2graph vs 24/35 for ripgrep; *trace* questions (5) 0/9 vs 3/9; *relationship* questions
(2, "what calls X") 1/2 each. Neither finds `Session.request` as the caller of
`prepare_request`.

At 8,000 tokens repo2graph finds more on 3 questions (`requests-01` redirects, `fastapi-07`
`jsonable_encoder`, `hono-06` CORS), grep finds more on 11 (`fastapi-08`, `flask-01`,
`flask-02`, `flask-04`, `flask-06`, `hono-01`, `hono-03`, `hono-04`, `hono-05`,
`requests-04`, `requests-07`), and 21 tie. Every row, with the definitions each retriever missed, is in
`results.json`.

**Correction, 2026-09-28.** The first published version of this page overstated repo2graph by
5–7 points (40% / 46% / 60%, against a corrected 35% / 39% / 54%). Its scorer credited a returned chunk with the symbol's whole line
range, so a later part of a split function, or a file chunk with its symbols cut out, counted as
containing the definition's first lines when it did not. It also listed the queried function as
an answer to its own "what calls X" question. Both are fixed; grep's numbers were unaffected by
the first bug.

**Rerun after indexing fixes, 2026-09-28.** The same day, repo2graph stopped silently dropping
definitions that share a name within a file (Go methods on different types, overloads, nested
closures such as Flask's two `View.as_view.view` functions) and began indexing Kotlin
functions. The index is now more complete, and repo2graph's numbers moved from 35/39/54% to
30/39/52%. The newly separate definitions compete for the same eight seed slots, which is the
seed-ranking weakness described below. The table above is the rerun.

## Why repo2graph loses here

Read off the packs for the losing questions, not guessed:

1. **Container chunks win the seed ranking and spend the budget.** Pieces of whole files
   (`file:hono.test.ts`, `file:context.ts`) and whole classes (`Flask`, `Session`, `Context`,
   `APIRouter`) match many question words because each covers a long span. They rank as seeds,
   cost 1,000+ tokens each, and usually contain a class header or an arbitrary slice of the
   class rather than the method that answers the question.
2. **Test files crowd out source files.** On Hono, three of the top seeds for *"how are
   middleware chained together"* are test files that exercise middleware, not `compose.ts`.
3. **Graph expansion follows the wrong edges for the question.** For *"what calls
   prepare_request"* the pack filled with `prepare_request`'s callees (`merge_setting`,
   `merge_cookies`) and not its caller `Session.request`. Expansion does not yet read the
   direction a question asks for.
4. **The neighbours that do arrive are often compressed** to a signature line once the seeds
   have spent the budget, so they locate the answer without containing it.

None of these is a parse error: the graph has the right nodes and edges (`Session.request` →
`Session.prepare_request` is a confidence-1.0 `CALLS` edge). They are ranking and packing
decisions, and they are the next thing to fix. A fix will be judged on a fresh set of questions,
not on these 35. Tuning to a benchmark and then reporting it is the failure this page exists to
avoid.

## What this does not measure

- **End-to-end agent success.** No model reads any of the output. An agent choosing its own grep
  terms would likely beat the `ripgrep` row, and an agent using `repo_neighbours` to walk from a
  hit to its callers is not modelled at all.
- **Latency.** Every retriever here answers in milliseconds; it does not separate them.
- **Other repositories or languages.** Four repositories and 35 questions is a small sample.
  Adding questions, especially on repositories you know well, is the most useful contribution
  this benchmark can take. Open a PR against `benchmarks/real/tasks.json`.

## Reproduce

Needs `git` and [ripgrep](https://github.com/BurntSushi/ripgrep). `--rg` takes a different
ripgrep command, `--cache` a directory for the clones, `--budgets` a comma-separated list.

```bash
pip install -e .
python scripts/bench_real_repos.py            # clones into a temp dir, writes benchmarks/real/results.json
python scripts/bench_real_repos.py --budgets 1000,16000 --out /tmp/results.json
```

The script refuses to run if a tag no longer resolves to the commit the questions were written
against, so the source under test cannot drift silently. Which definitions are found does not
depend on the platform. Token counts can differ by a few tokens between a CRLF checkout (the
committed results were produced on Windows with `core.autocrlf=true`) and an LF one.

## The synthetic regression suite

`benchmarks/corpus/` is a separate, smaller suite of five repositories written by this project to
exercise specific patterns (layered services, event buses, reflection, barrel re-exports). It is
a regression gate, not evidence of how repo2graph compares with anything. See
[regression-suite.md](regression-suite.md).
