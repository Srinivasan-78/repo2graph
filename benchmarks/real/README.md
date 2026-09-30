# Retrieval benchmark: real repositories

Does repo2graph put the code that answers a question into an agent's context, at a fixed token
budget, more often than grep does? This page measures that on four third-party repositories and
reports the result as it came out, including where repo2graph loses.

**Short version:** on the 35 lexical questions it still does not. At every budget measured, a
grep-then-read baseline finds at least as much of the answer, and at 4,000 and 8,000 tokens it
finds clearly more. On the 10 cross-file structural questions the order reverses at 4,000 tokens
and above. The causes are diagnosed [below](#why-repo2graph-loses-on-the-lexical-set), and they
are retrieval-ranking problems, not parsing ones.

## Setup

| | |
|---|---|
| **Repositories** | [Flask](https://github.com/pallets/flask) 3.1.2, [requests](https://github.com/psf/requests) 2.32.5, [FastAPI](https://github.com/fastapi/fastapi) 0.118.0 (Python); [Hono](https://github.com/honojs/hono) 4.9.0 (TypeScript). Each pinned to the commit in [`repos.json`](repos.json). None was written by this project. |
| **Questions** | 35 lexical questions in [`tasks.json`](tasks.json), phrased the way someone new to the codebase asks: *"how are HTTP redirects followed"*, *"what calls dispatch_request"*. 10 cross-file structural questions in [`tasks_structural.json`](tasks_structural.json). Each names the one to three definitions that answer it, with line ranges read off the pinned commit before any tool was run. |
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
| 2,000 | repo2graph | 30% | 10 / 35 | 13 / 35 | 1,978 |
| 2,000 | repo2graph-cite | 30% | 10 / 35 | 13 / 35 | 1,856 |
| 2,000 | repo2graph-bm25 | 30% | 10 / 35 | 13 / 35 | 1,830 |
| 2,000 | ripgrep | **35%** | 9 / 35 | 13 / 35 | 1,962 |
| 4,000 | repo2graph | 37% | 13 / 35 | 16 / 35 | 3,973 |
| 4,000 | repo2graph-cite | 41% | 14 / 35 | 18 / 35 | 3,685 |
| 4,000 | repo2graph-bm25 | 37% | 13 / 35 | 16 / 35 | 3,649 |
| 4,000 | ripgrep | **61%** | **18 / 35** | **23 / 35** | 3,873 |
| 8,000 | repo2graph | 48% | 14 / 35 | 20 / 35 | 7,834 |
| 8,000 | repo2graph-cite | 52% | 16 / 35 | 22 / 35 | 5,460 |
| 8,000 | repo2graph-bm25 | 48% | 14 / 35 | 20 / 35 | 5,561 |
| 8,000 | ripgrep | **72%** | **22 / 35** | **26 / 35** | 7,652 |

Citation-mode neighbours are the only variant that improves on BM25 alone, and they do it at
lower token cost (5,460 vs 5,561 mean tokens at 8k). Default full-body expansion adds nothing
over BM25 at any budget on this set.

At 4,000 tokens, by question type (definitions found): *concept* (28 questions) 16/35 for
repo2graph, 17/35 for `-cite`, 24/35 for ripgrep; *trace* (5 questions) 0/9, 1/9, 3/9;
*relationship* (2 questions, "what calls X") 1/2 for all three. Neither finds `Session.request`
as the caller of `prepare_request`.

Head-to-head at 8,000 tokens, `repo2graph-cite` against ripgrep: repo2graph finds more on 3
questions (`requests-01`, `fastapi-07`, `hono-06`), grep finds more on 11 (`flask-02`,
`flask-04`, `flask-06`, `flask-09`, `requests-04`, `requests-07`, `fastapi-08`, `hono-01`,
`hono-03`, `hono-04`, `hono-05`), and 21 tie. Every row, with the definitions each retriever
missed, is in `results.json`.

## Results: the 10 cross-file structural questions

[`tasks_structural.json`](tasks_structural.json) holds questions whose evidence provably spans
cross-file dependency edges — a caller in file A delegating to a helper in file B, or a
transitive dependency path. These are the questions graph expansion exists for.

| Budget | repo2graph | repo2graph-cite | repo2graph-bm25 | ripgrep |
|---:|---:|---:|---:|---:|
| 2,000 | 20% (1,925 tok) | 10% (1,836 tok) | 20% (1,858 tok) | 20% (1,978 tok) |
| 4,000 | **70%** (3,648 tok) | 30% (3,214 tok) | 50% (3,220 tok) | 20% (3,969 tok) |
| 8,000 | **80%** (6,760 tok) | 30% (3,536 tok) | 50% (4,142 tok) | 70% (7,978 tok) |

Read the whole table, not the best row. Three things it says that a single headline number does
not:

- **At 2,000 tokens there is no advantage.** repo2graph ties ripgrep at 20%, and one hop of
  expansion has not paid for itself yet.
- **The margin over grep is 50 pp at 4,000 tokens and 10 pp at 8,000.** Given grep more budget
  and it closes most of the gap. The 4,000-token row is the best case, not the typical one.
- **Citation mode is the worst retriever here**, at 10/30/30%, below BM25 alone at every budget.
  Signature metadata locates a cross-file answer without containing it, and the scorer requires
  containing it. The variant that wins the lexical set loses this one; there is no single
  best setting.

With 10 tasks, one task is 10 percentage points. Treat every number on this table as ±1 task.

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

## Why repo2graph loses on the lexical set

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
   have spent the budget, so they locate the answer without containing it. This is also why
   citation mode, which compresses every neighbour by design, loses the structural set.

None of these is a parse error: the graph has the right nodes and edges (`Session.request` →
`Session.prepare_request` is a confidence-1.0 `CALLS` edge). They are ranking and packing
decisions. A fix will be judged on a fresh set of questions, not on these 35. Tuning to a
benchmark and then reporting it is the failure this page exists to avoid.

## Corrections

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

## Reproducibility caveat

`results.json` records `"repo2graph_dirty": true` — it was generated from a working tree with
uncommitted changes, so the `repo2graph_commit` it names does not reproduce it exactly. Treat the
recorded commit as "approximately this" until the next clean regeneration.

## Reproduce

Needs `git` and [ripgrep](https://github.com/BurntSushi/ripgrep). `--rg` takes a different
ripgrep command, `--cache` a directory for the clones, `--budgets` a comma-separated list.

```bash
pip install -e .

# single-shot retrieval, both task sets, writes results.json
python scripts/bench_real_repos.py

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
