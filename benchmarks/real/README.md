# Retrieval benchmark: real repositories

Does repo2graph put the code that answers a question into an agent's context, at a fixed token
budget, more often than grep does? This page measures that on six third-party repositories and
reports the result as it came out, including where repo2graph loses.

**Short version, on 40 structural and 40 lexical questions it was not tuned against:** on
*structural* questions — the ones whose evidence provably spans a dependency edge — repo2graph
beats a grep-then-read baseline at every budget, by 41 pp at 8,000 tokens, while using *fewer*
tokens than grep to do it. On *lexical* questions it still loses, by 11 to 19 pp. That gap
narrowed this round and did not close; the diagnosis is
[below](#why-repo2graph-loses-on-the-lexical-set), and the honest reading is that the remainder is
vocabulary mismatch rather than ranking, which [dense retrieval addresses](#the-dense-result) and
BM25 tuning has not.

## Three question sets, and which one to believe

| | Published | Held-out questions | Held-out repositories |
|---|---|---|---|
| Lexical | 35, [`tasks.json`](tasks.json) | 40, [`tasks_holdout.json`](tasks_holdout.json) | 22, [`tasks_holdout_repos.json`](tasks_holdout_repos.json) |
| Structural | 10, [`tasks_structural.json`](tasks_structural.json) | 40, [`tasks_holdout_structural_40.json`](tasks_holdout_structural_40.json) | — |
| Repositories | the four below | the same four | click, axios |
| Role | regression set | accept/reject | generalisation |

The published set has been visible since the first version of this page and several fixes were
diagnosed against it. It is therefore a *regression* set and nothing more: it can show that a
change broke something, never that a change is good.

The two held-out sets hold out different things, which is why both exist.
[`tasks_holdout.json`](tasks_holdout.json) holds out the **questions** while reusing the four
repositories, so it asks whether a change generalises to questions nobody tuned on.
[`tasks_holdout_repos.json`](tasks_holdout_repos.json) holds out the **repositories**, so it asks
the harder question of whether a change generalises to code the change never saw — and it carries
the suite's only JavaScript. Both were authored without running repo2graph and validated against a
built index by [`../../scripts/validate_tasks.py`](../../scripts/validate_tasks.py) before any
retrieval was measured.

**Quote the held-out numbers.** They are uniformly harder — for every retriever, ripgrep included
— so they are the conservative estimate.

Targets for this round were deliberately *relative* (gap to grep) rather than absolute recall.
Absolute targets were abandoned when the held-out set came in harder than the published one: the
same fix would "pass" or "fail" depending only on which set it was scored against, whereas
gap-to-grep survives a change of set.

## Setup

| | |
|---|---|
| **Repositories** | [Flask](https://github.com/pallets/flask) 3.1.2, [requests](https://github.com/psf/requests) 2.32.5, [FastAPI](https://github.com/fastapi/fastapi) 0.118.0 (Python); [Hono](https://github.com/honojs/hono) 4.9.0 (TypeScript). Each pinned to the commit in [`repos.json`](repos.json). None was written by this project. |
| **Held-out repositories** | [click](https://github.com/pallets/click) 8.1.8 (Python), [axios](https://github.com/axios/axios) 1.7.9 (JavaScript), pinned in [`repos_holdout.json`](repos_holdout.json). Disjoint from the four above, and `test_answerability.py` asserts that they stay disjoint. |
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

Every table below carries a `<!-- bench-table: ... -->` marker naming the artifact it was read
from, and [`../../tests/test_bench_tables_match_artifacts.py`](../../tests/test_bench_tables_match_artifacts.py)
fails if any cell disagrees with that artifact or if the artifact was generated from a dirty tree.
That test exists because this page shipped a stale table once; see [Corrections](#corrections).

## Results: the held-out questions

From [`results_holdout_final.json`](results_holdout_final.json). 40 structural questions first,
because they are what the graph exists for:

<!-- bench-table: results_holdout_final.json structural_summary -->

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | **29%** | **11 / 40** | **12 / 40** | 1,965 |
| 2,000 | repo2graph-bm25 | 24% | 10 / 40 | 10 / 40 | 1,810 |
| 2,000 | ripgrep | 14% | 6 / 40 | 6 / 40 | 1,972 |
| 4,000 | repo2graph | **60%** | **24 / 40** | **25 / 40** | 3,968 |
| 4,000 | repo2graph-bm25 | 48% | 20 / 40 | 20 / 40 | 3,419 |
| 4,000 | ripgrep | 21% | 9 / 40 | 9 / 40 | 3,975 |
| 8,000 | repo2graph | **79%** | **31 / 40** | **33 / 40** | 7,580 |
| 8,000 | repo2graph-bm25 | 55% | 23 / 40 | 23 / 40 | 4,349 |
| 8,000 | ripgrep | 38% | 16 / 40 | 16 / 40 | 7,978 |

40 lexical questions, where the evidence sits in a single file:

<!-- bench-table: results_holdout_final.json summary -->

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | 17% | 7 / 40 | 9 / 40 | 1,969 |
| 2,000 | repo2graph-bm25 | 17% | 7 / 40 | 9 / 40 | 1,864 |
| 2,000 | ripgrep | **28%** | **12 / 40** | **15 / 40** | 1,966 |
| 4,000 | repo2graph | 29% | 10 / 40 | 15 / 40 | 3,961 |
| 4,000 | repo2graph-bm25 | 28% | 10 / 40 | 14 / 40 | 3,371 |
| 4,000 | ripgrep | **48%** | **19 / 40** | **23 / 40** | 3,945 |
| 8,000 | repo2graph | 45% | 16 / 40 | 23 / 40 | 7,842 |
| 8,000 | repo2graph-bm25 | 36% | 12 / 40 | 19 / 40 | 4,324 |
| 8,000 | ripgrep | **59%** | **24 / 40** | **26 / 40** | 7,806 |

Four things these say:

- **Graph expansion is the product, and it is measurable.** It adds +5 / +12 / +24 pp over BM25
  alone on structural questions. On lexical questions it adds nothing until 4k and +9 pp at 8k.
- **repo2graph beats grep on structural questions at every budget** (+15 / +39 / +41 pp), and the
  margin widens with budget rather than closing, because grep has no edge to follow however much
  room it is given.
- **It does so for fewer tokens than grep**, by 7, 7 and 398 mean tokens. Both retrievers are
  given the same ceiling; repo2graph stops sooner because a cited definition is a smaller thing
  to return than a window around every textual hit.
- **It still loses on lexical questions at every budget** (−11 / −19 / −14 pp). This is the
  unresolved result, and 4,000 tokens is the worst of it.

Per repository, to show that neither result is uniform (evidence recall, 2k / 4k / 8k):

| | structural, repo2graph | structural, ripgrep | lexical, repo2graph | lexical, ripgrep |
|---|---|---|---|---|
| flask | **36 / 91 / 91%** | 36 / 45 / 73% | 29 / 57 / **86%** | **57 / 79** / 79% |
| requests | **20 / 40 / 80%** | 0 / 20 / 50% | **29** / 29 / 43% | 21 / **43 / 64%** |
| hono | **27 / 64 / 73%** | 9 / 9 / 18% | 13 / 27 / 33% | **20** / 27 / 33% |
| fastapi | **30 / 40 / 70%** | 10 / 10 / 10% | 0 / 7 / 20% | **13 / 47 / 60%** |

The structural lead holds on all four. The lexical loss is concentrated: fastapi is where
repo2graph is beaten worst, and flask at 8,000 tokens is the one lexical cell it wins.

## Results: the held-out repositories

From [`results_holdout_repos.json`](results_holdout_repos.json). 22 questions on click and axios,
repositories no change on this page was developed against:

<!-- bench-table: results_holdout_repos.json summary -->

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | 50% | 11 / 22 | 11 / 22 | 1,970 |
| 2,000 | repo2graph-bm25 | 50% | 11 / 22 | 11 / 22 | 1,839 |
| 2,000 | ripgrep | 50% | 11 / 22 | 11 / 22 | 1,970 |
| 4,000 | repo2graph | **79%** | **18 / 22** | **18 / 22** | 3,952 |
| 4,000 | repo2graph-bm25 | **79%** | **18 / 22** | **18 / 22** | 3,056 |
| 4,000 | ripgrep | 58% | 12 / 22 | 13 / 22 | 3,866 |
| 8,000 | repo2graph | **79%** | **18 / 22** | 18 / 22 | 7,270 |
| 8,000 | repo2graph-bm25 | **79%** | **18 / 22** | 18 / 22 | 3,264 |
| 8,000 | ripgrep | 67% | 14 / 22 | 15 / 22 | 7,501 |

repo2graph wins the lexical comparison here, which it loses on the other held-out set — 22
questions on two repositories is a small sample and a tie at 2,000 tokens is three questions
either way, so do not read too much into the margin.

**Read the `-bm25` column instead, because it is the uncomfortable one: the graph adds exactly
zero on this set, at every budget, while costing up to 4,000 mean tokens.** These are lexical
questions, and the lexical rows on the other held-out set agree that expansion does little for
them below 8k. But it is worth stating plainly that the mechanism this project is built around
contributed nothing measurable to its best-looking table. There is no structural set for click or
axios yet; writing one is the most valuable open contribution this benchmark can take, because it
is the only way to find out whether the structural lead generalises to repositories as well as it
does to questions.

## Results: the published set (regression only)

From [`results.json`](results.json). These 35 + 10 questions were visible while fixes were being
made, so they bound how bad a regression can be and prove nothing about quality.

<!-- bench-table: results.json structural_summary -->

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | **60%** | **6 / 10** | **6 / 10** | 1,970 |
| 2,000 | repo2graph-bm25 | **60%** | **6 / 10** | **6 / 10** | 1,847 |
| 2,000 | ripgrep | 20% | 2 / 10 | 2 / 10 | 1,978 |
| 4,000 | repo2graph | **80%** | **8 / 10** | **8 / 10** | 3,908 |
| 4,000 | repo2graph-bm25 | 60% | 6 / 10 | 6 / 10 | 3,302 |
| 4,000 | ripgrep | 20% | 2 / 10 | 2 / 10 | 3,969 |
| 8,000 | repo2graph | **100%** | **10 / 10** | **10 / 10** | 7,358 |
| 8,000 | repo2graph-bm25 | 60% | 6 / 10 | 6 / 10 | 4,502 |
| 8,000 | ripgrep | 70% | 7 / 10 | 7 / 10 | 7,978 |

<!-- bench-table: results.json summary -->

| Budget | Retriever | Evidence found | Fully answered | Any evidence | Mean tokens |
|---:|---|---:|---:|---:|---:|
| 2,000 | repo2graph | **44%** | **15 / 35** | **17 / 35** | 1,959 |
| 2,000 | repo2graph-bm25 | **44%** | **15 / 35** | **17 / 35** | 1,814 |
| 2,000 | ripgrep | 35% | 9 / 35 | 13 / 35 | 1,962 |
| 4,000 | repo2graph | **63%** | **20 / 35** | **24 / 35** | 3,956 |
| 4,000 | repo2graph-bm25 | 61% | 19 / 35 | 24 / 35 | 3,259 |
| 4,000 | ripgrep | 61% | 18 / 35 | 23 / 35 | 3,873 |
| 8,000 | repo2graph | **80%** | **26 / 35** | **28 / 35** | 7,659 |
| 8,000 | repo2graph-bm25 | 70% | 21 / 35 | 26 / 35 | 4,137 |
| 8,000 | ripgrep | 72% | 22 / 35 | 26 / 35 | 7,652 |

The 10-question structural set reaching 100% is the clearest sign that it is too small and too
long-visible to carry weight. Compare it with 79% on the 40 held-out structural questions, and
believe the second.

## Scorecard against this round's targets

Measured on the held-out questions. "Before" is where this round started, in 2.2.

| | Target | Before | After | |
|---|---|---|---|---|
| **T1** lexical gap to grep, every budget | ≥ −5 pp | −14 / −24 / −19 | −11 / −19 / −14 | ❌ |
| **T2** structural lead over grep, 4k and 8k | ≥ +15 pp | +13 / +31 | **+39 / +41** | ✅ |
| **T3** mean tokens vs grep, matched budget | ≤ grep | +110 @8k | +3 / +16 / +36 lexical, **−7 / −7 / −398** structural | ◐ |
| **T4** agent-loop token ratio | ≤ 2× | 7.6× / 2.1× | 1.16× / 2.46× / 2.80× / 4.42× | ❌ except published structural |
| **T5** retrieval mode flags on default path | 0 | 4 | **0** | ✅ |

T3 is marked partial rather than passed because the target did not anticipate that the answer
would differ by question type. On structural questions repo2graph now returns *less* than grep and
finds more; on lexical ones it still spends slightly more to find less. The lexical overage is
down from +110 at 8,000 tokens to +36, which is under half a percent of the budget.

**T1 remains the open result**, and this round's written stop condition applies to it: *if the
lexical gap is still worse than −10 pp after the ranking and packing fixes, stop, because the
remainder is likely vocabulary mismatch rather than ranking.* At −11 / −19 / −14 pp that condition
is met. Do not open another BM25 ranking phase on the strength of this round; the next experiment
is [the dense path](#the-dense-result).

## Results: simulated agent loops

`search` → `read` → `answer`, via [`../../scripts/agent_eval.py`](../../scripts/agent_eval.py).
**No model is in the loop.** The "agent" is a deterministic policy running real ripgrep and a real
index, so this measures what each surface makes reachable in a few turns, not what a model would
do with it.

| Task set | Method | Success | Mean turns | Mean tokens | Precision per read |
|---|---|---:|---:|---:|---:|
| held-out structural, 40 | repo2graph | **16 / 40 (40%)** | 1.9 | 2,590 | 12.0% |
| | ripgrep | 4 / 40 (10%) | 2.5 | **586** | **29.0%** |
| held-out lexical, 40 | repo2graph | **8 / 40 (20%)** | **1.4** | 2,070 | 21.7% |
| | ripgrep | 7 / 40 (18%) | 3.0 | **740** | **89.1%** |
| published structural, 10 | repo2graph | **8 / 10 (80%)** | **1.5** | 2,189 | **12.4%** |
| | ripgrep | 1 / 10 (10%) | 6.0 | **1,879** | 11.0% |
| published lexical, 35 | repo2graph | **17 / 35 (49%)** | **1.3** | 2,314 | 24.7% |
| | ripgrep | 7 / 35 (20%) | 3.6 | **939** | 37.8% |

Four times grep's structural success rate, and it reaches an answer in one or two turns where grep
needs three to six. The turn counts moved most this round — held-out structural went 3.1 to 1.9 —
because sharper seed ranking puts the answer in the *first* pack more often, and a turn saved is
worth more to an agent than a token saved.

It is not cheap: 4.4× grep's tokens on the held-out structural set, and **grep wins precision per
read on three of the four sets**. repo2graph buys recall and turns with tokens. The one set where
it wins on every axis at once is the published structural one, which is also the least
trustworthy.

`agent_eval.py` reads [`repos.json`](repos.json) directly, so the held-out *repository* set cannot
go through the agent loop without teaching it `--repos`. That is a gap, not a result.

## Why repo2graph loses on the lexical set

Read off the packs for the losing questions, not guessed. Eight defects were diagnosed. Six were
fixed, one was fixed and found to be worth nothing, and one was both: **misdiagnosed on this page
for a month, and then fixed by a mechanism the corrected diagnosis did not predict.**

### D1 — container chunks and the budget

This is the most instructive entry on the page, so it is first, and it is told in order.

**The original claim**, which this page led with for a month: whole-file and whole-class chunks
"rank as seeds, cost 1,000+ tokens each, and usually contain a class header or an arbitrary slice
of the class rather than the method that answers the question."

**The correction.** Measurement found two of those three claims wrong:

1. **Ranking is not the defect.** On a fixture where a class holds the one method that answers the
   question, the method scores 3.413 and the class 2.565 — correctly. `Index.score` already
   length-normalises (`BM25_B * length / avgdl`), so long chunks are discounted. The real
   mechanism was never "long chunks win"; it is that a class matches the *union* of its members'
   vocabulary, which length normalisation does not address.
2. **The container's slice often *is* the answer.** An attempted fix compressed a container to a
   signature whenever it held a seed's span. Both structural tasks that regressed did so because
   the class chunk was *the only source* of the evidence lines — `ho-struct-hono-01` wants
   `Hono.constructor`, which was never retrieved as its own chunk at all. Span containment does
   not mean the member is separately present.
3. **The waste is real, but it is partial overlap rather than duplication.** Containers took 30%
   of seed slots and 44% of the budget at 4k, with a class mean of 794 tokens against 208 for a
   method, and in 20 of 40 packs a class sat beside one of its own members — but only the lines
   shared with the member were paid for twice.

That first fix attempt was reverted (held-out structural 25/62% → 31/50% at 4k/8k; net negative),
and this page then said the only correct version emits the container *minus* the member's lines,
needing multi-range citations and surgery in the packer, and that nothing should be attempted
until the diagnosis was rewritten.

**What actually fixed it was none of that.** Four further measurements on the published set at
8,000 tokens reframed the problem:

- every one of the 46 evidence definitions was **present in some chunk** — nothing was lost to
  parsing or chunking;
- an **oracle packer fit 100% of them** inside the budget, so the budget was never the bound;
- **54% of real pack tokens went to container chunks**;
- the chunk carrying the answer ranked top-8 for 22 of 46, and 8th–24th for a further 15.

So the index already held every answer and the budget could already afford it. The loss was
entirely in which chunks won the seed slots — which is a *selection* problem, not a packing one,
and selection is reachable with a multiplier. Two corrections, applied as a re-rank over
`score_rrf`'s output rather than as a change to BM25:

- a **penalty on container chunks** (`CONTAINER_SEED_PENALTY`), applied only above a token floor,
  because the cost is the problem and a cheap container does not have one;
- a **boost when a chunk's declared name shares content words with the question**
  (`NAME_TERM_BOOST`), capped at two terms.

Held-out structural recall went 38% → 60% at 4k and 71% → 79% at 8k on these two changes alone.

**What the corrected diagnosis got right and wrong.** Right: ranking order was not the defect, and
containers are sometimes the only source of the answer — which is why the fix is a penalty with a
cost floor rather than exclusion, and why `repo2graph-cite`-style compression lost. Wrong: the
conclusion that packer surgery was the *only* correct version. Reframing from "the container is
wasteful to pack" to "the container is occupying a seat" made a one-line multiplier sufficient.
The lesson is not that the correction was wasted — it is that *a diagnosis which rules out the
cheap fix deserves one more measurement before it rules it out.*

### Fixed, and uncontroversially

- **D6 — a parse gap hid an entire library's public API.** Class fields holding functions were
  never extracted: `LANG_CFG` mapped `method_definition` but neither `public_field_definition`
  (TS) nor `field_definition` (JS). That is 22 missing symbols in `hono/src`, and they were Hono's
  whole public surface — `Context.json`, `.text`, `.html`, `.body`, `.redirect`, `.header`,
  `.status`, `.get`, `.set`, `.newResponse`, `.notFound`, `Hono.fetch`. `context.ts` indexed its
  constructor, its getters and one private method, and nothing a caller actually invokes. Fixing
  it moved held-out lexical recall 14/24/40% → 16/28/43%, all of it on hono, with the three Python
  repositories byte-identical — the signature a TypeScript-only parser change should have.
- **D2 — test files crowded out source files in seeds.** Three of the top seeds for *"how are
  middleware chained together"* on Hono were test files exercising middleware rather than
  `compose.ts`. On the pinned hono checkout, whose indexed `src/` is 49% test chunks because the
  suite sits beside the source as `*.test.ts`, test paths took 45% of seed slots and 37% of the
  token budget at 4,000 tokens. Test paths are now *demoted* as seeds and stay reachable as
  neighbours, so *"what tests cover X"* still works. **Demoting is load-bearing: dropping them
  outright was measured separately and was harmful on held-out questions.**
- **D3 — expansion ignored the direction the question asked for.** *"What calls prepare_request"*
  filled the pack with `prepare_request`'s callees (`merge_setting`, `merge_cookies`) rather than
  its caller `Session.request`, across a `CALLS` edge of confidence 1.0. Expansion now reads the
  direction from the question's shape.
- **D5 — `conditional_expansion` as a default.** Settled on 40 questions: never enable. See
  [the knobs that lost](#the-knobs-that-lost).

### Fixed, and worth nothing to recall — which was the point

- **D8 — a split chunk cited a range it did not contain.** Asking fastapi *"how are things like
  datetimes and uuids made json safe before sending"* at 4,000 tokens emitted a block headed
  `[cite: encoders.py:102-344]` whose first line was from around line 160: `_split` cut a long
  symbol into parts and **every part was emitted with the parent's line span**, so the agent was
  told where to look and shown something else. File residuals had the same bug. `_split_spans` now
  returns each part's own span. **It moved recall by zero, exactly as predicted** — chunk text is
  unchanged, so ranking is unchanged — and it was still worth doing, because a wrong citation is
  worse than a miss.

### D7 — export aliases, and the limit of a recall benchmark

`utils/url.ts` declared `const _getQueryParam` (indexed) and `export const getQueryParam: (...) =
_getQueryParam as (...)` (not indexed), so the name consumers import had no node. Same shape in
`hono-base.ts`: `class Hono` indexed, its `export { Hono as HonoBase }` alias — which `hono.ts`
imports — not. This affects any library with a public façade over private implementations.

**The second half is the one worth reading, because the recall tables could not see it.** An alias
node is a rename: one line whose *name* is an exact match for the question. Indexing it put it into
seed selection, and all eighteen held-out recall rows stayed byte-identical while the simulated
agent loop went 15/40 → 14/40 — it lost *"how does the serveStatic middleware decide the
Content-Type header"*, whose answer is the 92-line `middleware/serve-static/index.ts`. Scoring
aliases down the way test paths are scored down was tried first and changed not one number,
because the cost was never the alias's *rank*: seeds are packed in order while `fits()` holds, so
at a small budget the bigger, better-scoring seeds are rejected one by one and the one-line alias
fits in exactly what they could not use. So aliases are skipped as seeds outright — but
deliberately left reachable by expansion, which is where the recall gain arrives:
`utils/url.ts:295-301` enters the pack as `CALLS out of query`.

The transferable lesson: **a retrieval change can be invisible to a single-shot recall benchmark
and still cost a real answer.** Any change touching what *seeds* needs both tables.

### Three defects that only existed in combination

D1's re-rank and the D2/D7 seed rules were developed on separate branches and each was green on
its own. Composing them produced three defects that neither branch could have caught, because each
needs one mechanism from each side. They are recorded because "both changes were measured" is not
the same as "the combination was measured":

1. **The name boost fires hardest on test definitions.** A test names the behaviour in the
   question's own words, so it collects the boost more reliably than the implementation does:
   `test_middleware_chaining_runs_handlers_in_order` overlaps *"how are middleware handlers
   chained together"* on two terms and took the full 2.0×, while `compose_middleware` — the answer
   — overlaps on one and took 1.5×. Boosting by name and demoting by path then fought, and the
   boost won, because 2.0 × `TEST_SEED_PENALTY` = 1.2 still beats an unboosted source chunk. That
   reintroduced the exact defect D2 exists to remove. The boost is now withheld from test paths
   unless the question is about tests.
2. **An alias is a guaranteed maximal name match and a guaranteed non-answer.** It can no longer
   be seeded, but a boosted alias still pushes real candidates out of the `k * 3` seed window.
   Withheld too.
3. **Sparing a chunk the penalty while denying it the boost is not protection.** The container
   token floor spared `app/store.py`'s 91-token residual the penalty, but withholding the boost
   from all containers unconditionally left it to lose its slot to every `*_order` function that
   could collect 1.5× — and the demo's *"trace an order request from route to persistence"*
   stopped citing the module that holds the persistence. One size test now governs both halves of
   the container rule.

All three are pinned by tests in
[`../../tests/test_answerability.py`](../../tests/test_answerability.py) and
[`../../tests/test_alias_seeds.py`](../../tests/test_alias_seeds.py).

### Still open

- **D4 — neighbours arrive signature-compressed once seeds have eaten the budget**, so they locate
  the answer without containing it, and the scorer requires containing it.
- **Six of the 46 published evidence definitions rank below 100** even after the re-rank, so no
  selection change can reach them. Those need better scoring, not better selection.

**An earlier version of this page claimed "none of these is a parse error."** That was false: D6
and D7 are both parse gaps, and D6 alone was hiding twelve of the methods Hono's users call. It
was written from the assumption that the graph had the right nodes, and the assumption was never
checked. Correcting it is among the most useful things this round produced, because a ranking fix
on top of a missing node cannot work.

## What was tried and rejected

Recorded because the negative results were as informative as the fixes, and because each looked
obviously right beforehand.

| Idea | Result |
|---|---|
| Boost chunks whose **path** matches the question (`openapi/utils.py` for an OpenAPI question) | Nothing at any budget, slightly negative at 4,000. The strongest-seeming intuition of the four. |
| **Drop test files** from seed candidates outright | Neutral on the published set, actively harmful on held-out questions. Demoting them (D2) works; removing them does not. |
| Raise **`k` from 8 to 40** so the budget is actually filled (the old default left 4,847 of 8,000 tokens unspent) | +2 on the lexical set, but the published structural set falls 100% → 80% at 8,000: extra lexical seeds crowd out the graph neighbours those questions are answered by. Reverted. |
| Penalise containers **by kind alone**, with no cost floor | Dropped the demo fixture's 91-token `app/store.py` residual for no budget saved. Caught by `test_demo.py`, not by this benchmark. |
| Let the name boost apply to **containers** | Promoted a test helper class literally named `Request` to rank 1 on *"trace an order request from route to persistence"*, ahead of every route body. |
| Scale the name boost by the **fraction of query terms** a name covers, so one generic term out of five is a nudge rather than half the cap | Fixes the cheap-container interaction on its own, and loses 9 of 12 held-out cells doing it — structural −2 / −2 / −5 pp. The strong boost earns its keep; gating *which chunks are eligible* is the fix, not weakening it. |
| Compress a container to a **signature** whenever it holds a seed's span (the first D1 attempt) | Net negative; see [D1](#d1--container-chunks-and-the-budget). |
| **LSA over the repository's own chunks** as an offline embedder | Captured part of the dense gain and damaged structural retrieval; see [the dense result](#the-dense-result). |

## The dense result

`repo2graph embed` had shipped in the `rag` extra since it was written and had never been
benchmarked. **It used to be the largest single lever on this page. It is now a trade, and the
reversal is this round's most interesting result.**

Held-out, from [`results_holdout_dense.json`](results_holdout_dense.json). Lexical first, because
that is where dense retrieval still wins:

<!-- bench-table: results_holdout_dense.json summary -->

| Budget | Retriever | Evidence found | Fully answered | Mean tokens |
|---:|---|---:|---:|---:|
| 2,000 | repo2graph | 17% | 7 / 40 | 1,969 |
| 2,000 | repo2graph-vec | 17% | 5 / 40 | 1,963 |
| 2,000 | repo2graph-vec-bm25 | 17% | 5 / 40 | 1,811 |
| 4,000 | repo2graph | 29% | 10 / 40 | 3,961 |
| 4,000 | repo2graph-vec | **36%** | **12 / 40** | 3,960 |
| 4,000 | repo2graph-vec-bm25 | 33% | 11 / 40 | 3,350 |
| 8,000 | repo2graph | 45% | 16 / 40 | 7,842 |
| 8,000 | repo2graph-vec | **57%** | **20 / 40** | 7,814 |
| 8,000 | repo2graph-vec-bm25 | 36% | 12 / 40 | 4,091 |

At 8,000 tokens dense fusion takes the lexical gap to grep from −14 pp to **−2 pp**, for
essentially the same tokens. That is the strongest evidence on this page that the residual lexical
gap is vocabulary mismatch rather than ranking.

And now the structural set, which is the reversal:

<!-- bench-table: results_holdout_dense.json structural_summary -->

| Budget | Retriever | Evidence found | Fully answered | Mean tokens |
|---:|---|---:|---:|---:|
| 2,000 | repo2graph | 29% | 11 / 40 | 1,965 |
| 2,000 | repo2graph-vec | **31%** | **12 / 40** | 1,974 |
| 2,000 | repo2graph-vec-bm25 | 24% | 10 / 40 | 1,791 |
| 4,000 | repo2graph | **60%** | **24 / 40** | 3,968 |
| 4,000 | repo2graph-vec | 50% | 19 / 40 | 3,947 |
| 4,000 | repo2graph-vec-bm25 | 36% | 15 / 40 | 3,422 |
| 8,000 | repo2graph | **79%** | **31 / 40** | 7,580 |
| 8,000 | repo2graph-vec | 76% | 30 / 40 | 7,627 |
| 8,000 | repo2graph-vec-bm25 | 43% | 18 / 40 | 4,439 |

**Dense vectors now cost structural recall: −10 pp at 4,000 tokens and −3 pp at 8,000.** Before
this round's seed-ranking work they *added* 7 pp at 8k. Nothing about the embedder changed; the
BM25-plus-graph default got better, and a diffuse topical signal fused at equal weight now dilutes
a ranking that structural questions — which name their symbol — had already got right. The same
mechanism that made LSA fail, arriving at a signal strong enough that it used to be worth it.

So the recommendation is narrower than it was: **turn vectors on if your questions are lexical,
leave them off if they are structural.** Which is unsatisfying, and points at the fix below.

**The graph is not made redundant by dense retrieval — it matters more with vectors on, not
less.** Expansion adds +21 pp lexical (57% against `vec-bm25`'s 36%) and +33 pp structural (76%
against 43%) at 8k, both larger than the corresponding gaps without vectors. The two signals are
complementary, which is the result that most surprised us.

**This is not the default, by decision.** It needs `sentence-transformers`, torch and a downloaded
model, and repo2graph's stated guarantee is that the default path needs no model, no API key and
no network. We kept the guarantee and publish the numbers for the other choice rather than quietly
leading with a configuration most users do not have. If you want the recall and can afford the
dependency:

```bash
pip install "repo2graph[rag]"
repo2graph build /path/to/repo -o .r2g && repo2graph embed -o .r2g
```

One alternative was tested and failed: **LSA over the repository's own chunks** (a TF-IDF
term-document matrix truncated by SVD, fitted per repo at build time, nothing downloaded). At 64,
256 and 512 dimensions it captured part of the lexical gain and *damaged* structural retrieval.
Dimension was not the problem: LSA has no knowledge outside the repository, so it can learn that
two words co-occur here but not that "datetime" and "timestamp" are related in general — and
fusing a diffuse topical signal at equal weight dilutes a BM25 ranking that structural questions,
which name their symbol, had already got right.

**The follow-up is now obvious, where it used to be speculative.** `score_rrf` accepts
`weights=(w_bm25, w_vec)` and `pack_context` never passes them, so every fusion ever measured here
is 50/50. A structural question that names its symbol should weight BM25 heavily; a lexical
question phrased in prose should weight the vectors. The page already has a working
question-shape classifier driving expansion direction (D3), so the input to that decision exists.
Earlier this looked like a parameter search on a weak mechanism; the reversal above makes it the
difference between "dense is a trade" and "dense is a win", which is worth measuring properly.

### The knobs that lost

Three flags let a caller pick a retrieval configuration until 3.0.0 retired them. Held-out, 8,000
tokens, read off [`results_holdout_final.json`](results_holdout_final.json):

| | lexical | structural | lexical tokens |
|---|---|---|---|
| default | **45%** | **79%** | 7,842 |
| `--neighbours=cite` | 33% | 55% | 4,656 |
| `--conditional-expansion` | 43% | 60% | 7,227 |
| both together | 33% | 55% | 4,643 |
| expansion off entirely | 36% | 55% | 4,324 |

`--neighbours=cite` was advertised as the opt-in that wins the lexical table, which it did win —
on the published 35 it was diagnosed against. On held-out questions it is worse than the default
everywhere and, decisively, it is *dominated* by simply turning expansion off: 33% lexical for
4,656 mean tokens against 36% for 4,324. Its only claim was token economy and it does not hold it.

`--conditional-expansion` gates expansion on BM25 confidence, and a structural question names its
symbol, so BM25 looks confident and the graph is skipped on exactly the questions it exists for —
19 pp of structural recall at 8k. Useless or harmful, never right. Its verdict flipped twice on
underpowered question sets (30 pp worse on the published 10, free on a held-out 15) before the set
was grown to 40 and settled it.

## A measurement limitation, recorded not fixed

16% of held-out and 20% of published lexical evidence items live in a symbol long enough to be
split into parts (fastapi: 7 of 15). Among *missed* items at 8k the share is higher, so split
symbols are over-represented in the failures.

This matters because the scorer credits a definition only when its **first line and the next
nine** are returned. For a 243-line function, returning the 100 lines that actually discuss the
question is arguably better retrieval than returning the signature, and the metric scores it zero.

It is a real bias, and it accounts for a minority of the lexical gap. **The scorer was left alone
deliberately.** Changing it to credit mid-body slices would raise repo2graph's numbers without
improving retrieval, which is the exact failure this page exists to avoid. Any future change here
needs a second metric, not a loosened one.

## Corrections

- **2026-10-05 — a published table was stale, and a test now prevents it.** The held-out
  structural table on this page was read off `results_holdout_knobs.json`, an intermediate
  artifact two phases old, while naming `results_holdout_final.json`. Two cells understated recall
  by 2.4 pp each, and the scorecard built on them recorded **T2 as missed by 1 pp when it had
  passed.** Nothing caught it: the doc-consistency tests check that links resolve and command
  lines parse, not that a number matches the JSON beside it. Every table on this page now carries a
  machine-checked marker naming its artifact, and
  [`../../tests/test_bench_tables_match_artifacts.py`](../../tests/test_bench_tables_match_artifacts.py)
  fails on a mismatch, on a missing marker, or on an artifact generated from a dirty tree. In the
  same pass the dense rows were regenerated from a clean tree; the previously published ones came
  from a dirty one and could not be reproduced from any commit.
- **2026-10-05 — the D1 diagnosis was corrected, then the correction was partly superseded.** See
  [D1](#d1--container-chunks-and-the-budget) for both halves. The page also claimed "none of these
  is a parse error", which was false.
- **2026-09-30 — structural agent numbers regenerated.** The published figures (50% success, 2.2
  turns, 2,282 tokens) came from a run that predated citation-mode neighbours and weighted RRF and
  had no committed artifact. `agent_eval.py` previously defaulted `--out` to `agent_results.json`
  regardless of which task file it read, so a structural run silently overwrote the general one;
  the two results now live in separate files.
- **2026-09-28 — rerun after indexing fixes.** repo2graph stopped silently dropping definitions
  that share a name within a file (Go methods on different types, overloads, nested closures such
  as Flask's two `View.as_view.view` functions) and began indexing Kotlin functions. The index is
  now more complete, and repo2graph's numbers moved from 35/39/54% to 30/39/52%: the newly separate
  definitions compete for the same eight seed slots.
- **2026-09-28 — scorer.** The first published version overstated repo2graph by 5–7 points
  (40/46/60%, against a corrected 35/39/54%). Its scorer credited a returned chunk with the
  symbol's whole line range, so a later part of a split function, or a file chunk with its symbols
  cut out, counted as containing the definition's first lines when it did not. It also listed the
  queried function as an answer to its own "what calls X" question. Both are fixed; grep's numbers
  were unaffected by the first bug.

## What this does not measure

- **End-to-end agent success.** No model reads any of the output. An agent choosing its own grep
  terms would likely beat the `ripgrep` rows, and an agent using `repo_neighbours` to walk from a
  hit to its callers is not modelled at all.
- **Latency.** Every retriever here answers in milliseconds; it does not separate them.
- **14 of the 17 supported languages.** This is the sharpest limit on everything above. Only
  Python (×4), TypeScript (×1) and JavaScript (×1) are benchmarked. Both D6 and D7 — a parse gap
  that hid a whole public API, and one that hides every export alias — were found in the *single*
  TypeScript repository, the moment anyone looked. That is evidence about the languages nobody has
  looked at, and it points one way.
- **Structural questions outside the original four repositories.** There is no structural set for
  click or axios, so the structural lead is measured on held-out *questions* only, never on
  held-out *code*. Given that the graph added exactly zero on the held-out repositories' lexical
  questions, this is the gap most likely to change a conclusion on this page.
- **Repositories outside this corpus.** Six repositories, 97 lexical and 50 structural questions is
  a small sample. Adding questions, especially on repositories you know well, is the most useful
  contribution this benchmark can take. Open a PR against `tasks_holdout.json`.

## Reproduce

Needs `git` and [ripgrep](https://github.com/BurntSushi/ripgrep). `--rg` takes a different
ripgrep command, `--cache` a directory for the clones, `--budgets` a comma-separated list.

```bash
pip install -e .

# published set (regression), writes results.json
python scripts/bench_real_repos.py

# held-out questions (accept/reject), writes results_holdout_final.json
python scripts/bench_real_repos.py \
    --tasks benchmarks/real/tasks_holdout.json \
    --structural-tasks benchmarks/real/tasks_holdout_structural_40.json \
    --out benchmarks/real/results_holdout_final.json

# held-out repositories: click and axios. The structural sets name symbols in the
# other four repositories, so they are skipped automatically rather than scored
# against an index that cannot contain their evidence.
python scripts/bench_real_repos.py \
    --repos benchmarks/real/repos_holdout.json \
    --tasks benchmarks/real/tasks_holdout_repos.json \
    --out benchmarks/real/results_holdout_repos.json

# add the dense rows -- needs `pip install -e ".[rag]"`
python scripts/bench_real_repos.py --embed \
    --tasks benchmarks/real/tasks_holdout.json \
    --structural-tasks benchmarks/real/tasks_holdout_structural_40.json \
    --out benchmarks/real/results_holdout_dense.json

# simulated agent loop, one task set at a time -- always pass --out
python scripts/agent_eval.py --tasks benchmarks/real/tasks_holdout.json \
    --out benchmarks/real/agent_results_holdout.json
```

**Run each one from a committed tree, and commit its artifact before starting the next.**
`bench_real_repos.py` samples `git status --porcelain --untracked-files=no` at the *end* of a run,
so an artifact written by an earlier command in the same batch stamps `repo2graph_dirty: true` on
every artifact after it. A dirty artifact cannot be reproduced from any commit, and
`test_bench_tables_match_artifacts.py` refuses to let one back a published table.

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
