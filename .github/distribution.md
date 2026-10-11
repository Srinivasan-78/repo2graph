# Distribution plan

The gap between what repo2graph does and how visible it is is a distribution problem, not a
product problem. This page records the plan and — more importantly — what is already shipped,
because the first pass at this plan asked for several things that exist.

Every claim here is checked against the repository. Where a claim could overstate what the
benchmarks support, the honest version is written down instead; see
[Claims discipline](#claims-discipline).

## Already shipped — do not rebuild

| Asked for | Already in the repo |
|---|---|
| "Add a GIF or short video of the graph being generated and queried" | `docs/images/demo.gif`, embedded at the top of `README.md` |
| "Build a gallery of famous repos" | `examples/` — eleven repositories (Django, Kubernetes, Linux, TensorFlow, VS Code, React, FastAPI, Gin, ripgrep, Guava, Jekyll), each with a pinned revision, `overview.md` and build metrics |
| "Publish token benchmarks vs the agent workflow" | `benchmarks/real/` — 35 general + 10 structural tasks across Flask, requests, FastAPI and Hono, budget-matched at 2k/4k/8k, plus a multi-turn agent eval (`scripts/agent_eval.py`). Raw rows in `benchmarks/real/results.json`, method in `benchmarks/methodology.md` |
| "Add a vs-competitors table" | `docs/comparison.md` — Serena, Aider's repo map, CodeGraphContext, code-graph-rag, Sourcegraph, Cursor's index, Claude Code's own search, Graphify, Sourcetrail, Madge, dependency-cruiser, CodeSee |
| "Emphasise it works in the terminal and inside Claude/Cursor" | `README.md` "Try it" section; per-client guides in `docs/mcp.md` for Claude Code, Claude Desktop, Cursor and generic clients |
| "Zero external graph dependencies" | True and already documented: `.github/SECURITY.md` §supply chain, and `export.py`'s pure-Python Fruchterman-Reingold ("networkx's needs numpy, we do not"). Runtime deps are `tree-sitter` and `tree-sitter-language-pack` only |

## Status of the open items (#462)

The repository-side work is done. What remains needs the maintainer's own account: posting,
submitting, recruiting. Every draft below follows [Claims discipline](#claims-discipline) and
names the file each number comes from.

| # | Item | In the repository | Left for the maintainer |
|---|---|---|---|
| 1 | "Sourcetrail alternative" page | **Done.** [`docs/comparison.md#repo2graph-as-a-sourcetrail-alternative`](../docs/comparison.md#repo2graph-as-a-sourcetrail-alternative), linked from `README.md` | Nothing. If a standalone URL is wanted for search, that is a sixth page under `docs/` and a deliberate change to `test_the_shipped_doc_set_is_the_documented_one` |
| 2 | `docs/comparison.md` rows for Madge, dependency-cruiser, CodeSee | **Done.** [`docs/comparison.md#repo2graph-vs-the-dependency-graph-tools-madge-dependency-cruiser-codesee`](../docs/comparison.md#repo2graph-vs-the-dependency-graph-tools-madge-dependency-cruiser-codesee) | Re-check CodeSee's status before quoting it: GitKraken acquired the company in May 2024, and what is offered under the name today could not be confirmed from a primary source |
| 3 | Show HN | **Drafted:** [`outreach/show-hn.md`](outreach/show-hn.md) — title, body, the image to make and how, and a source for every number | Make the screenshot, re-check the numbers, post, and be there to answer |
| 4 | MCP directories and awesome lists | `server.json`, the `mcp-name:` marker and the Glama listing already existed. **Drafted:** [`outreach/directory-listings.md`](outreach/directory-listings.md) — one-line entries, form fields, category | Open the pull requests / fill in the forms, matching each list's own format |
| 5 | Gallery from 5 to 10–15 | **Done: 11.** React, FastAPI, Gin, ripgrep, Guava and Jekyll added ([`examples/README.md`](../examples/README.md)), covering JavaScript, Rust, Java and Ruby for the first time | Optional: Rails and Spring Framework were tried and are not in. Their docstrings contain `/my/rails/root/...` and `</root>`, which `validate_example`'s home-directory leak check flags when the generator runs with `HOME=/root` (a container). They should pass on a normal machine or the `examples.yml` runner; making the check match only a path boundary would fix it everywhere. Optional too: regenerate all eleven with one version (`--all`) so the gallery is one batch again |
| 6 | Tutorials and roundups | **Drafted:** [`outreach/benchmark-writeup-outline.md`](outreach/benchmark-writeup-outline.md) | Write and publish; contact authors of "best MCP tools for coding agents" roundups |
| 7 | Design partners | **Drafted:** [`outreach/design-partners.md`](outreach/design-partners.md) — one real repository, three questions, where it helped or misled | Post in GitHub Discussions and follow up |

## Priority order

1. Show HN with a visual demo on a famous repo — [draft](outreach/show-hn.md); lead with Gin or
   FastAPI, not React (Flow annotations defeat the parser there; see `examples/README.md`)
2. MCP directory and awesome-list listings — [draft entries](outreach/directory-listings.md)
3. A write-up of the token-budget benchmark — [outline](outreach/benchmark-writeup-outline.md)
4. Design partners — [Discussions draft](outreach/design-partners.md)

## Claims discipline

The benchmarks in `benchmarks/real/` do not support a blanket "better than grep" or "better than
Cursor" claim, and marketing copy must not make one. What they actually show:

Quote the **held-out** sets. The published 35 + 10 questions were visible while fixes were made,
so they are a regression set; an earlier version of this page quoted figures from them that have
since been superseded (repo2graph now *leads* grep on the published lexical set, 44/63/80% against
35/61/72%, which is exactly why that set must not be the headline).

- **Single-shot lexical questions: grep wins at every budget** — 28% vs 17% at 2k, 48% vs 29% at
  4k, 59% vs 45% at 8k (`benchmarks/real/results_holdout_final.json`, `summary`). On purely
  lexical questions whose evidence sits in one file, text search is the better tool, and
  `README.md` says so in its own voice.
- **Cross-file structural questions: repo2graph wins at every budget** — 29% vs 14% at 2k, 60% vs
  21% at 4k, 79% vs 38% at 8k (same file, `structural_summary`), and for fewer tokens than grep.
  grep cannot traverse a dependency edge or compute a reverse call closure.
- **Inside a simulated multi-turn agent loop** (no model; a fixed search→read→answer policy):
  structural 16/40 vs 4/40 in 1.9 turns vs 2.5, at 4.4× grep's tokens
  (`agent_results_holdout_structural.json`); lexical 8/40 vs 7/40 — one task, effectively a tie —
  in 1.4 turns vs 3.0, at 2.8× (`agent_results_holdout.json`). grep wins precision per read on
  three of the four agent sets.

So the defensible pitch is the agent loop and the structural question, not repo understanding in
general: *your coding agent answers cross-file questions in fewer turns, with citations and a
hard token ceiling.* The broader claim invites the first skeptical reader to run the project's
own general table against it.

Two further limits worth stating plainly rather than being caught on:

- Call resolution is **name-based, scoped by class, file and imports — not type-based**. Ambiguous
  names are marked `ambiguous` and priced `1/n` rather than guessed. `README.md`'s "What it can't
  do" section is the canonical wording; do not soften it.
- `graph.html` is a **static map**, not an interactive navigator. Against Sourcetrail's desktop UI
  that is a real deficit, and the Sourcetrail section of `docs/comparison.md` says so.

## Measurement

No telemetry, by design — local-first and no-network is part of the product promise, and adding
default telemetry to measure a marketing push would trade the thing being marketed. Proxy
signals instead: PyPI downloads, GitHub Action usage, stars and forks, MCP directory referrals,
issues and PRs from outside accounts, and third-party repositories referencing the Action or an
MCP config.
