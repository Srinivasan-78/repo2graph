# Retrieval benchmark: real repositories

Does repo2graph put the code that answers a question into an agent's context, at a fixed token
budget, more often than grep does? This page measures that on four third-party repositories and
reports the result as it came out, including where repo2graph loses.

**Short version, measured on 40 held-out questions it was not tuned against:** on *structural*
questions — the ones whose answer provably spans a dependency edge — repo2graph beats a
grep-then-read baseline at every budget, by 31 pp at 8,000 tokens, and graph expansion is what
does it. On *lexical* questions it still loses, by 12 to 22 pp. That gap narrowed by 2–4 pp over
this round of fixes and did not close; the diagnosis is [below](#why-repo2graph-loses-on-the-lexical-set),
and the honest reading is that the remaining cause is vocabulary mismatch rather than ranking,
which [dense retrieval addresses](#the-dense-result) and BM25 tuning has not.

## Two question sets, and which one to believe

| | Published | Held-out |
|---|---|---|
| Lexical | 35, [`tasks.json`](tasks.json) | 40, [`tasks_holdout.json`](tasks_holdout.json) |
| Structural | 10, [`tasks_structural.json`](tasks_structural.json) | 40, [`tasks_holdout_structural_40.json`](tasks_holdout_structural_40.json) |
| Role | regression set | accept/reject |

The published set has been visible since the first version of this page, and several fixes were
diagnosed against it. It is therefore a *regression* set and nothing more: it can show that a
change broke something, never that a change is good.

Every accept/reject decision in this round was made on the held-out set, which was authored
without running repo2graph and validated against the built index by
[`../../scripts/validate_tasks.py`](../../scripts/validate_tasks.py) before any retrieval was
measured. **Quote the held-out numbers.** They are uniformly harder — for every retriever,
ripgrep included — so they are the conservative estimate.

Targets for this round were deliberately *relative* (gap to grep) rather than absolute recall.
Absolute targets were abandoned when the held-out set came in harder than the published one:
the same fix would "pass" or "fail" depending only on which set it was scored against, whereas
gap-to-grep survives a change of set.

## Setup

| | |
|---|---|
| **Repositories** | [Flask](https://github.com/pallets/flask) 3.1.2, [requests](https://github.com/psf/requests) 2.32.5, [FastAPI](https://github.com/fastapi/fastapi) 0.118.0 (Python); [Hono](https://github.com/honojs/hono) 4.9.0 (TypeScript). Each pinned to the commit in [`repos.json`](repos.json). None was written by this project. |
| **Questions** | Phrased the way someone new to the codebase asks: *"how are HTTP redirects followed"*, *"what calls dispatch_request"*. Each names the one to three definitions that answer it, with line ranges read off the pinned commit before any tool was run. |
| **Scoring** | A definition is *found* when its first line and the next nine (or all of it, if shorter) are in the returned text. Mentioning the file, or returning only a signature, does not count. For repo2graph, each returned line is aligned to the source file, so only code actually present in the pack earns credit. For "what calls X" questions, only the callers count, not X itself. |
| **Budgets** | 2,000, 4,000 and 8,000 tokens (`len(text) // 4` for every retriever). |

The retrievers. **`repo2graph` is the product**; the rest are ablations, measured to attribute
where the result comes from:

- **`repo2graph`** — `Index.pack_context` with the MCP server's `repo_search` defaults
  (`k=8`, `hops=1`, secrets excluded). The repo-map header it prepends counts against the budget.
  This is the only configuration a caller can select, and the one every headline number uses.
- **`repo2graph-bm25`** — `expand_graph=False`. The difference from the row above is what the
  graph adds, and it is the single most important comparison on this page.
- **`repo2graph-cite`**, **`repo2graph-cond`**, **`repo2graph-cond-cite`** — signature-compressed
  neighbours, confidence-gated expansion, and both together. These were once user-facing flags
  (`--neighbours=cite`, `--conditional-expansion`, `--precision-first`). **These measurements
  rejected all three, and 3.0.0 retired them**: the CLI flags still parse but do nothing, and the
  MCP parameters are gone. They survive as internal keyword arguments so these rows stay
  reproducible. See [the knobs that lost](#the-knobs-that-lost).
- **`repo2graph-vec`**, **`repo2graph-vec-bm25`** — dense-vector fusion via `repo2graph embed`,
  which needs the `rag` extra. Off by default. See [the dense result](#the-dense-result).
- **`ripgrep`** — `rg -i -F` for the question's words (stop words dropped), then read ±15 lines
  around the hits in order of how many distinct question words each window contains, until the
  budget is spent. This is the grep-then-read loop a coding agent runs, minus the model's
  judgement in choosing search terms. Our untested expectation is that a real agent choosing its
  own terms would do better than this row.

## Results: the held-out set

From [`results_holdout_final.json`](results_holdout_final.json). 40 lexical questions:

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | 16% | 7 / 40 | 8 / 40 | 1,979 |
| 2,000 | repo2graph-bm25 | 16% | 7 / 40 | 8 / 40 | 1,863 |
| 2,000 | ripgrep | **28%** | **12 / 40** | **15 / 40** | 1,966 |
| 4,000 | repo2graph | 26% | 11 / 40 | 13 / 40 | 3,971 |
| 4,000 | repo2graph-bm25 | 26% | 11 / 40 | 13 / 40 | 3,679 |
| 4,000 | ripgrep | **48%** | **19 / 40** | **23 / 40** | 3,945 |
| 8,000 | repo2graph | 43% | 17 / 40 | 20 / 40 | 7,958 |
| 8,000 | repo2graph-bm25 | 38% | 16 / 40 | 19 / 40 | 5,550 |
| 8,000 | ripgrep | **59%** | **24 / 40** | **26 / 40** | 7,806 |

40 structural questions — evidence that provably spans cross-file dependency edges, which is
what graph expansion exists for:

| Budget | Retriever | Evidence found | Fully answered | Mean tokens |
|---:|---|---:|---:|---:|
| 2,000 | repo2graph | **17%** | **6 / 40** | 1,962 |
| 2,000 | repo2graph-bm25 | 14% | 6 / 40 | 1,813 |
| 2,000 | ripgrep | 14% | 6 / 40 | 1,972 |
| 4,000 | repo2graph | **38%** | **14 / 40** | 3,864 |
| 4,000 | repo2graph-bm25 | 29% | 12 / 40 | 3,487 |
| 4,000 | ripgrep | 21% | 9 / 40 | 3,975 |
| 8,000 | repo2graph | **71%** | **28 / 40** | 7,210 |
| 8,000 | repo2graph-bm25 | 43% | 18 / 40 | 4,800 |
| 8,000 | ripgrep | 38% | 16 / 40 | 7,978 |

Three things this says:

- **Graph expansion is the product, and it is measurable.** It adds +3 / +9 / +28 pp over BM25
  alone on structural questions, and the 8,000-token row is the clearest result on this page:
  71% against 43%. On lexical questions it adds nothing until 8k, where it adds 5 pp.
- **repo2graph beats grep on structural questions at every budget** (+3 / +17 / +33 pp). No
  earlier version of this page could say that; at 2,000 tokens the two used to tie.
- **It still loses on lexical questions at every budget** (−12 / −22 / −15 pp). This is the
  unresolved result, and 4,000 tokens is the worst of it.

Per repository, to show that neither result is uniform (evidence recall, 2k / 4k / 8k):

| | lexical, repo2graph | lexical, ripgrep | structural, repo2graph | structural, ripgrep |
|---|---|---|---|---|
| flask | 36 / 36 / 64% | **57 / 79 / 79%** | 18 / 27 / 73% | **36 / 45** / 73% |
| requests | 21 / 50 / **71%** | 21 / **43** / 64% | 10 / 20 / **90%** | 0 / 20 / 50% |
| hono | 7 / 20 / 27% | **20 / 27 / 33%** | **36 / 73 / 73%** | 9 / 9 / 18% |
| fastapi | 0 / 0 / 13% | **13 / 47 / 60%** | 0 / **30 / 50%** | 10 / 10 / 10% |

**fastapi is the worst repository in the corpus by a wide margin** — 13% lexical recall at 8,000
tokens against grep's 60%. Its answering definitions are huge (median function 13.5 lines, p90
204, max 928), so they are truncated most often, and until 3.0.0 truncation removed precisely the
part that identifies a definition. See [D8](#fixed-this-round).

By question type at 4,000 tokens (definitions found): *concept* (32 questions) 13/37 for
repo2graph against 20/37 for ripgrep; *trace* (5 questions) 1/14 against 6/14; *relationship*
(3 questions, "what calls X") 1/7 against 2/7. **Trace questions are the weakest category** and
the one a dependency graph ought to own.

## Results: the published set (regression only)

From [`results.json`](results.json). These 35 lexical and 10 structural questions were visible
while fixes were being designed, so they are reported to show nothing regressed — not as
evidence that anything works.

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | **39%** | **13 / 35** | **17 / 35** | 1,976 |
| 2,000 | repo2graph-bm25 | **39%** | **13 / 35** | **17 / 35** | 1,821 |
| 2,000 | ripgrep | 35% | 9 / 35 | 13 / 35 | 1,962 |
| 4,000 | repo2graph | 44% | 15 / 35 | 19 / 35 | 3,972 |
| 4,000 | repo2graph-bm25 | 44% | 15 / 35 | 19 / 35 | 3,643 |
| 4,000 | ripgrep | **61%** | **18 / 35** | **23 / 35** | 3,873 |
| 8,000 | repo2graph | 56% | 17 / 35 | 24 / 35 | 7,771 |
| 8,000 | repo2graph-bm25 | 54% | 16 / 35 | 23 / 35 | 5,359 |
| 8,000 | ripgrep | **72%** | **22 / 35** | **26 / 35** | 7,652 |

Structural, 10 questions (one task is 10 pp — treat every number as ±1 task):

| Budget | repo2graph | repo2graph-bm25 | repo2graph-cite | ripgrep |
|---:|---:|---:|---:|---:|
| 2,000 | 20% (1,925 tok) | 20% (1,828 tok) | 10% (1,808 tok) | 20% (1,978 tok) |
| 4,000 | **70%** (3,656 tok) | 50% (3,224 tok) | 30% (3,216 tok) | 20% (3,969 tok) |
| 8,000 | **80%** (6,771 tok) | 50% (4,146 tok) | 30% (3,538 tok) | 70% (7,978 tok) |

At 2,000 tokens on the lexical set repo2graph now leads ripgrep, 39% against 35% — the first
budget at which it has ever done so on either set. Read that as a regression check that came out
well, not as a headline: the held-out set says −12 pp at the same budget, and the held-out set is
the one that was not tuned against.

## Scorecard against this round's targets

Measured on the held-out set.

| | Target | Before | After | |
|---|---|---|---|---|
| **T1** lexical gap to grep, every budget | ≥ −5 pp | −14 / −24 / −19 | −12 / −22 / −15 | ❌ |
| **T2** structural lead over grep, 4k and 8k | ≥ +15 pp | +13 / +31 | **+17 / +33** | ✅ |
| **T3** mean tokens vs grep, matched budget | ≤ grep | +110 @8k | +13 / +26 / +152 | ❌ |
| **T4** agent-loop token ratio | ≤ 2× | 7.6× / 2.1× | see below | ❌ except one set |
| **T5** retrieval mode flags on default path | 0 | 4 | **0** | ✅ |

Three of five, and one of the three is T5, which was a deletion rather than a measurement. T2
passed only on the round's last fix — the export-alias one, which was worth +2.4 pp of structural
recall at 4k and carried the target over the line. This is the honest summary of the round:
**the structural thesis is now solid and the lexical gap is not closed.**

The plan this round followed had a written stop condition — *if the lexical gap is still worse
than −10 pp after the ranking and packing fixes, stop, because the remainder is likely vocabulary
mismatch rather than ranking.* At −12 / −22 / −15 pp **that condition is met**, and the next
section is what it points to.

## The dense result

`repo2graph embed` had shipped in the `rag` extra since it was written and had never been
benchmarked. Turning it on, with no change to repo2graph itself, is the largest lever found in
this round.

Held-out, from [`results_holdout_knobs.json`](results_holdout_knobs.json). That run predates the
export-alias fix below, so its default structural row reads 36% / 69% rather than the 38% / 71%
in the headline table — the dense and ablation rows were measured against *that* default, and
re-reading one row off a later artifact would compare two different builds:

| | 2k | 4k | 8k |
|---|---|---|---|
| **lexical** — BM25 + graph (default) | 16% | 26% | 43% |
| **lexical** — + dense vectors | **22%** | **41%** | **57%** |
| lexical — ripgrep | 28% | 48% | 59% |
| **structural** — BM25 + graph (default) | 17% | 36% | 69% |
| **structural** — + dense vectors | **29%** | **50%** | **76%** |
| structural — ripgrep | 14% | 21% | 38% |

At 8,000 tokens dense fusion takes the lexical gap from −15 pp to −2 pp and lifts structural
recall to 76%. Mean token counts are slightly *lower* than the BM25 rows at every budget.

**The graph is not made redundant by dense retrieval.** With vectors on, expansion still adds
17 pp lexical (57% against `vec-bm25`'s 40%) and 26 pp structural (76% against 50%) at 8k. The two
signals are complementary, which is the result that most surprised us.

**This is not the default, by decision.** It needs `sentence-transformers`, torch and a downloaded
model, and repo2graph's stated guarantee is that the default path needs no model, no API key and
no network. We kept the guarantee and are publishing the numbers rather than quietly leading with
a configuration most users do not have. If you want the recall and can afford the dependency:

```bash
pip install "repo2graph[rag]"
repo2graph build /path/to/repo -o .r2g && repo2graph embed -o .r2g
```

One alternative was tested and failed: **LSA over the repository's own chunks** (a TF-IDF
term-document matrix truncated by SVD, fitted per repo at build time, nothing downloaded). At 64,
256 and 512 dimensions it captured part of the lexical gain and *damaged* structural retrieval
(44–50% at 8k against the default's 69%). Dimension was not the problem: LSA has no knowledge
outside the repository, so it can learn that two words co-occur here but not that "datetime" and
"timestamp" are related in general — and fusing a diffuse topical signal at equal weight dilutes
a BM25 ranking that structural questions, which name their symbol, had already got right.

A real follow-up remains: `score_rrf` accepts `weights=(w_bm25, w_vec)` and `pack_context` never
passes them, so every fusion measured here is 50/50. Down-weighting a weak dense signal might let
it add without diluting. That is a parameter search on a mechanism already shown to be weak,
though, and should not be mistaken for a way to rescue the offline embedder.

### The knobs that lost

Three flags let a caller pick a retrieval configuration until 3.0.0 retired them. Held-out, 8,000
tokens, from the same file:

| | lexical | structural | lexical tokens |
|---|---|---|---|
| default | **43%** | **69%** | 7,962 |
| `--neighbours=cite` | 36% | 31% | 5,677 |
| `--conditional-expansion` | 41% | 52% | 7,616 |
| expansion off entirely | 38% | 43% | 5,551 |

`--neighbours=cite` was advertised as the opt-in that wins the lexical table, which it did win —
on the published 35 it was diagnosed against. On held-out questions it is worse than the default
everywhere and, decisively, it is *dominated* by simply turning expansion off: with vectors on at
8k it returns 38% lexical for 5,307 mean tokens against 40% for 5,083. Its only claim was token
economy and it does not hold it.

`--conditional-expansion` gates expansion on BM25 confidence, and a structural question names its
symbol, so BM25 looks confident and the graph is skipped on exactly the questions it exists for —
17 pp of structural recall at 8k. With dense vectors on it is an exact no-op: identical recall
*and* identical mean token counts at all three budgets. Useless or harmful, never right. Its
verdict flipped twice on underpowered question sets (30 pp worse on the published 10, free on a
held-out 15) before the set was grown to 40 and settled it.

## Results: simulated agent loops

`search` → `read` → `answer`, via [`../../scripts/agent_eval.py`](../../scripts/agent_eval.py).
**No model is in the loop.** The "agent" is a deterministic policy running real ripgrep and a
real repo2graph index against real checked-out repositories. It measures what the retrieval
surface makes reachable in a few turns, not what a model would do with it.

| Task set | Method | Success | Mean turns | Mean tokens | Precision per read |
|---|---|---:|---:|---:|---:|
| held-out structural, 40 | repo2graph | **15 / 40 (38%)** | 3.1 | 2,550 | 15.3% |
| | ripgrep | 4 / 40 (10%) | **2.5** | **586** | **29.0%** |
| held-out lexical, 40 | repo2graph | 7 / 40 (18%) | **1.0** | 1,962 | 21.5% |
| | ripgrep | 7 / 40 (18%) | 3.0 | **740** | **89.1%** |
| published structural, 10 | repo2graph | **7 / 10 (70%)** | **3.3** | 3,099 | 3.1% |
| | ripgrep | 1 / 10 (10%) | 6.0 | **1,879** | **11.0%** |
| published lexical, 35 | repo2graph | **15 / 35 (43%)** | **1.5** | 2,447 | 28.0% |
| | ripgrep | 7 / 35 (20%) | 3.6 | **939** | **37.8%** |

repo2graph answers more in fewer turns on three of the four sets, and ties grep's success rate on
held-out lexical while using one turn against three. It is not cheap: the token ratio is 4.3× on
held-out structural, 2.6× on both lexical sets, and 1.65× on published structural — the only set
that meets T4's ≤ 2×. Grep wins precision per read on every set.

The honest reading: **repo2graph buys recall and turns with context**, and the budget-matched
single-shot tables above are the comparison this one deliberately is not.

## Why repo2graph loses on the lexical set

Read off the packs for the losing questions, not guessed. Four defects were diagnosed; two were
fixed, one was fixed and found to be worth nothing, and **the one this section used to lead with
turned out to be misdiagnosed.**

### Fixed this round

- **D6 — a parse gap hid an entire library's public API.** Class fields holding functions were
  never extracted: `LANG_CFG` mapped `method_definition` but neither `public_field_definition`
  (TS) nor `field_definition` (JS). That is 22 missing symbols in `hono/src`, and they were
  Hono's whole public surface — `Context.json`, `.text`, `.html`, `.body`, `.redirect`,
  `.header`, `.status`, `.get`, `.set`, `.newResponse`, `.notFound`, `Hono.fetch`. `context.ts`
  indexed its constructor, its getters and one private method, and nothing a caller actually
  invokes. Fixing it moved held-out lexical recall 14/24/40% → 16/28/43%, all of it on hono, with
  the three Python repositories byte-identical — the signature a TypeScript-only parser change
  should have.
- **D8 — a split chunk cited a range it did not contain.** This one hit the headline promise
  directly. Asking fastapi *"how are things like datetimes and uuids made json safe before
  sending"* at 4,000 tokens emitted a block headed `[cite: encoders.py:102-344]` whose first line
  was from around line 160: `_split` cut a long symbol into parts and **every part was emitted
  with the parent's line span**, so the agent was told where to look and shown something else.
  File residuals had the same bug. `_split_spans` now returns each part's own span.
  **It moved recall by zero, exactly as predicted** — chunk text is unchanged, so ranking is
  unchanged — and it was still worth doing, because a wrong citation is worse than a miss.
- **D2 — test files crowded out source files in seeds.** Three of the top seeds for *"how are
  middleware chained together"* on Hono were test files exercising middleware rather than
  `compose.ts`. Test paths are now demoted as seeds and stay reachable as neighbours, so *"what
  tests cover X"* still works.
- **D3 — expansion ignored the direction the question asked for.** *"What calls
  prepare_request"* filled the pack with `prepare_request`'s callees (`merge_setting`,
  `merge_cookies`) rather than its caller `Session.request`, across a `CALLS` edge of confidence
  1.0. Expansion now reads the direction. **Partially fixed:** `requests-08` now finds
  `Session.request` at 8,000 tokens, where grep still does not, and still misses it at 2k and 4k.

### Misdiagnosed — the correction this page owes most

- **D1 — "container chunks win the seed ranking."** Earlier versions of this page said whole-file
  and whole-class chunks "rank as seeds, cost 1,000+ tokens each, and usually contain a class
  header or an arbitrary slice of the class rather than the method that answers the question."
  **Two of those three claims are wrong**, and three measurements say so:

  1. **Ranking is not the defect.** On a fixture where a class holds the one method that answers
     the question, the method scores 3.413 and the class 2.565 — correctly. `Index.score` already
     length-normalises (`BM25_B * length / avgdl`), so long chunks are discounted. The real
     mechanism was never "long chunks win"; it is that a class matches the *union* of its
     members' vocabulary, which length normalisation does not address.
  2. **The container's slice often *is* the answer.** An attempted fix compressed a container to
     a signature whenever it held a seed's span. Both structural tasks that regressed did so
     because the class chunk was *the only source* of the evidence lines — `ho-struct-hono-01`
     wants `Hono.constructor`, which was never retrieved as its own chunk at all. Span
     containment does not mean the member is separately present.
  3. **The waste is real but it is partial overlap, not duplication.** Containers do take 30% of
     seed slots and 44% of the budget at 4k on the held-out set (class mean 794 tokens against
     208 for a method), and in 20 of 40 packs a class sits beside one of its own members. But
     only the lines shared with the member are paid for twice.

  That fix attempt was reverted: held-out lexical 16/28/43% → 17/24/40%, held-out structural
  25/62% → 31/50% at 4k/8k, published structural 70/80% → 60/70%. Net negative.

  The only correct version emits the container *minus* the member's lines, which means
  multi-range citations and real surgery in the packer. Since the premise is now known to be
  partly false, that work should not start until the diagnosis is rewritten from measurement.

- **D7 — export aliases were not symbols, and fixing it took two changes.** `utils/url.ts`
  declared `const _getQueryParam` (indexed) and `export const getQueryParam: (...) =
  _getQueryParam as (...)` (not indexed), so the name consumers import had no node. Same shape in
  `hono-base.ts`: `class Hono` indexed, its `export { Hono as HonoBase }` alias — which `hono.ts`
  imports — not. This affects any library with a public façade over private implementations.
  Indexing both forms moved held-out structural recall 36% → 38% at 4k and 69% → 71% at 8k.

  **The second change is the one worth reading, because the recall tables could not see it.**
  An alias node is a rename: one line whose *name* is an exact match for the question. Putting it
  in the index put it into seed selection, and all eighteen held-out recall rows stayed
  byte-identical while the simulated agent loop went 15/40 → 14/40 — it lost *"how does the
  serveStatic middleware decide the Content-Type header"*, whose answer is the 92-line
  `middleware/serve-static/index.ts`. Scoring aliases down the way test paths are scored down was
  tried first and changed not one number, because the cost was never the alias's *rank*: seeds are
  packed in order while `fits()` holds, so at a small budget the bigger, better-scoring seeds are
  rejected one by one and the one-line alias fits in exactly what they could not use. So aliases
  are skipped as seeds outright — but deliberately left reachable by expansion, which is where the
  recall gain above actually arrives: `utils/url.ts:295-301` enters the pack as
  `CALLS out of query`. Loop back to 15/40.

  The transferable lesson: **a retrieval change can be invisible to a single-shot recall
  benchmark and still cost a real answer.** Any change touching what *seeds* needs both tables.

### Still open

- **D4 — neighbours arrive signature-compressed once seeds have eaten the budget**, so they
  locate the answer without containing it, and the scorer requires containing it.

**An earlier version of this page claimed "none of these is a parse error."** That was false:
D6 and D7 are both parse gaps, and D6 alone was hiding twelve of the methods Hono's users call.
It was written from the assumption that the graph had the right nodes, and the assumption was
never checked. Correcting it is the single most useful thing this round produced, because a
ranking fix on top of a missing node cannot work.

## A measurement limitation, recorded not fixed

16% of held-out and 20% of published lexical evidence items live in a symbol long enough to be
split into parts (fastapi: 7 of 15). Among *missed* items at 8k the share is 24%, so split
symbols are modestly over-represented in the failures.

This matters because the scorer credits a definition only when its **first line and the next
nine** are returned. For a 243-line function, returning the 100 lines that actually discuss the
question is arguably better retrieval than returning the signature, and the metric scores it
zero.

It is a real bias, and it accounts for at most a quarter of the lexical gap — 25 of 33 held-out
misses at 8k are not split symbols. **The scorer was left alone deliberately.** Changing it to
credit mid-body slices would raise repo2graph's numbers without improving retrieval, which is the
exact failure this page exists to avoid. Any future change here needs a second metric, not a
loosened one.

## Corrections

- **2026-10-05 — the published table was stale, and the diagnosis was wrong.** The committed
  `results.json` reported 30/37/48% lexical against a tree that was by then returning 39/44/56%,
  because it had not been regenerated since 2026-09-30. Both sets are now regenerated from a
  clean tree on every row on this page. In the same pass, the D1 container-chunk diagnosis that
  this page led with for a month was measured and found to be substantially wrong (above), and
  the claim "none of these is a parse error" was found to be false. The `repo2graph-cite` rows
  were also presented as a shipping mode that wins the lexical set; it was retired in 3.0.0 for
  losing on held-out questions.
- **2026-09-30 — structural agent numbers regenerated.** The published figures (50% success,
  2.2 turns, 2,282 tokens) came from a run that predated citation-mode neighbours and weighted
  RRF and had no committed artifact. `agent_eval.py` previously defaulted `--out` to
  `agent_results.json` regardless of which task file it read, so a structural run silently
  overwrote the general one; the two results now live in separate files.
- **2026-09-28 — rerun after indexing fixes.** repo2graph stopped silently dropping definitions
  that share a name within a file (Go methods on different types, overloads, nested closures such
  as Flask's two `View.as_view.view` functions) and began indexing Kotlin functions. The index is
  now more complete, and repo2graph's numbers moved from 35/39/54% to 30/39/52%: the newly
  separate definitions compete for the same eight seed slots.
- **2026-09-28 — scorer.** The first published version overstated repo2graph by 5–7 points
  (40/46/60%, against a corrected 35/39/54%). Its scorer credited a returned chunk with the
  symbol's whole line range, so a later part of a split function, or a file chunk with its
  symbols cut out, counted as containing the definition's first lines when it did not. It also
  listed the queried function as an answer to its own "what calls X" question. Both are fixed;
  grep's numbers were unaffected by the first bug.

## What this does not measure

- **End-to-end agent success.** No model reads any of the output. An agent choosing its own grep
  terms would likely beat the `ripgrep` rows, and an agent using `repo_neighbours` to walk from a
  hit to its callers is not modelled at all.
- **Latency.** Every retriever here answers in milliseconds; it does not separate them.
- **15 of the 17 supported languages.** This is the sharpest limit on everything above. Only
  Python (×3) and TypeScript (×1) are benchmarked. Both D6 and D7 — a parse gap that hid a whole
  public API, and one that hides every export alias — were found in the *single* TypeScript
  repository, the moment anyone looked. That is evidence about the fourteen languages nobody has
  looked at, and it points one way.
- **Repositories outside this corpus.** Four repositories, 75 lexical and 50 structural questions
  is a small sample. Adding questions, especially on repositories you know well, is the most
  useful contribution this benchmark can take. Open a PR against `tasks_holdout.json`.

## Reproduce

Needs `git` and [ripgrep](https://github.com/BurntSushi/ripgrep). `--rg` takes a different
ripgrep command, `--cache` a directory for the clones, `--budgets` a comma-separated list.

```bash
pip install -e .

# published set (regression), writes results.json
python scripts/bench_real_repos.py

# held-out set (accept/reject), writes results_holdout_final.json
python scripts/bench_real_repos.py \
    --tasks benchmarks/real/tasks_holdout.json \
    --structural-tasks benchmarks/real/tasks_holdout_structural_40.json \
    --out benchmarks/real/results_holdout_final.json

# add the dense rows -- needs `pip install -e ".[rag]"`
python scripts/bench_real_repos.py --embed --out benchmarks/real/results_dense.json

# simulated agent loop, one task set at a time -- always pass --out
python scripts/agent_eval.py --tasks benchmarks/real/tasks_holdout.json \
    --out benchmarks/real/agent_results_holdout.json
```

Before trusting a new question, run the validator:

```bash
# --cache is the same clones-and-indexes directory bench_real_repos.py uses,
# so run the bench once first; it defaults to <tempdir>/r2g-bench-real.
python scripts/validate_tasks.py benchmarks/real/tasks_holdout.json \
    --cache "${TMPDIR:-/tmp}/r2g-bench-real"
```

It verifies every evidence entry against a built index — path under the right root, range inside
the file, symbol real, indexed start inside the evidence span — and separates *task defects*
(a wrong symbol name, which fails) from *parser gaps* (a symbol repo2graph cannot see, which is
recorded, kept and measured). **A question is never dropped because repo2graph cannot answer
it**; that rule is what makes D6 and D7 visible on this page rather than quietly absent from it.

`bench_real_repos.py` refuses to run if a tag no longer resolves to the commit the questions were
written against, so the source under test cannot drift silently. Which definitions are found does
not depend on the platform. Token counts can differ by a few tokens between a CRLF checkout (the
committed results were produced on Windows with `core.autocrlf=true`) and an LF one.

## The synthetic regression suite

[`../corpus/`](../corpus/README.md) is a separate, smaller suite written by this project to
exercise specific parser patterns. It is a regression gate, not evidence of how repo2graph
compares with anything.
