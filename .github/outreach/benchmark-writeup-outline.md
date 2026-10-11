# DRAFT — outline for a token-budget benchmark write-up

**Status: outline for the maintainer to write up for Dev.to / Hashnode or a blog. Not published.**

Working title: *"We benchmarked our code-graph tool against grep at a fixed token budget. Grep won
half of it."* The honest headline is the hook; it is also the claims discipline in
`.github/distribution.md`.

Source of truth for every number: `benchmarks/real/README.md` and the JSON files it names. Quote
the **held-out** sets; the published 35 + 10 set was visible during tuning and is a regression set.

## 1. The question (150 words)

Does a code graph put the code that answers a question into an agent's context more often than
grep does, *when both are held to the same token budget*? Why the budget matters: an agent's
context is the scarce resource, and "found it eventually" hides the cost.

## 2. Setup (250 words)

- Four repositories not written by the project (Flask, requests, FastAPI, Hono), pinned in
  `benchmarks/real/repos.json`; held-out repositories click and axios.
- 40 lexical + 40 structural held-out questions, answer locations written down before any tool ran,
  validated by `scripts/validate_tasks.py`.
- Scoring: a definition counts only if its first ten lines are in the returned text.
- Retrievers: repo2graph, repo2graph with graph expansion off (`-bm25`), and a grep-then-read loop
  (`rg -i -F`, ±15 lines around hits). Budgets 2k / 4k / 8k tokens.

## 3. Results — lead with both halves (300 words)

- Structural: repo2graph 29 / 60 / 79% vs grep 14 / 21 / 38% (`results_holdout_final.json`,
  `structural_summary`). The graph is what does it: expansion adds +5 / +12 / +24 pp over the same
  retriever without it. Uses *fewer* tokens than grep at every budget.
- Lexical: grep 28 / 48 / 59% vs repo2graph 17 / 29 / 45% (`summary`). Unclosed gap.
- Held-out repositories (lexical only, 22 questions): 79% vs 58% at 4k — but the graph added
  exactly zero there. Say so.

## 4. The agent loop (250 words)

Deterministic search→read→answer policy, no model (`scripts/agent_eval.py`):
held-out structural 16/40 vs 4/40 in 1.9 vs 2.5 turns at 4.4× grep's tokens; held-out lexical 8/40
vs 7/40 in 1.4 vs 3.0 turns at 2.8×; grep wins precision per read on three of four sets
(`agent_results_holdout*.json`). The trade: recall and turns, paid for in tokens.

## 5. What we got wrong along the way (400 words) — the part people will share

From `benchmarks/real/README.md`: the D1 diagnosis that was wrong for a month; the parse gap that
hid Hono's whole public API (D6); the alias fix that was invisible to the recall table but cost a
real agent answer (D7); the stale table that a test now prevents (Corrections, 2026-10-05).

## 6. Dense vectors: a trade, not a win (200 words)

`results_holdout_dense.json`: +12 pp lexical at 8k, −10 pp structural at 4k. Why it is opt-in.

## 7. Limits (150 words)

No model in the loop; six repositories; two of nineteen languages benchmarked; name-based call
resolution, ambiguous names priced 1/n.

## 8. Reproduce it (100 words)

The commands from `benchmarks/real/README.md`, "Reproduce", and an invitation to add questions
(link the design-partner discussion, `.github/outreach/design-partners.md`).

## Before publishing

Re-run `pytest tests/test_bench_tables_match_artifacts.py` and re-read each number from its JSON;
if a newer artifact exists, update the post rather than this outline.
