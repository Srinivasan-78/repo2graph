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
| "Build a gallery of famous repos" | `examples/` — Django, Kubernetes, Linux, TensorFlow, VS Code, each with a pinned revision, `overview.md` and build metrics |
| "Publish token benchmarks vs the agent workflow" | `benchmarks/real/` — 35 general + 10 structural tasks across Flask, requests, FastAPI and Hono, budget-matched at 2k/4k/8k, plus a multi-turn agent eval (`scripts/agent_eval.py`). Raw rows in `benchmarks/real/results.json`, method in `benchmarks/methodology.md` |
| "Add a vs-competitors table" | `docs/comparison.md` — Serena, Aider's repo map, CodeGraphContext, code-graph-rag, Sourcegraph, Cursor's index, Claude Code's own search |
| "Emphasise it works in the terminal and inside Claude/Cursor" | `README.md` "Try it" section; per-client guides in `docs/mcp.md` for Claude Code, Claude Desktop, Cursor and generic clients |
| "Zero external graph dependencies" | True and already documented: `.github/SECURITY.md` §supply chain, and `export.py`'s pure-Python Fruchterman-Reingold ("networkx's needs numpy, we do not"). Runtime deps are `tree-sitter` and `tree-sitter-language-pack` only |

## Genuinely open

### 1. Own the "Sourcetrail alternative" search

**Status: not started.** `sourcetrail` appears in zero files in this repository.

Sourcetrail was the first tool that made interactive code visualization accessible to individual
developers, and its discontinuation left a gap that search traffic still reflects. This is the
cheapest high-intent audience available: people searching for a replacement have already decided
they want the category.

What to write: a comparison page covering what Sourcetrail did, what repo2graph does differently
(agent-native via MCP rather than a desktop GUI; terminal and editor rather than a separate app),
and — honestly — what Sourcetrail did better, which is the interactive desktop UI. repo2graph's
`graph.html` is a static map, not a navigable IDE-grade view.

Do not claim to be a drop-in replacement. Claim to be the modern answer to the same question.

### 2. Extend `docs/comparison.md` to the dependency-graph tools

**Status: not started.** Madge, dependency-cruiser and CodeSee appear nowhere.

`docs/comparison.md` currently compares against agent-context tools. It does not cover the
JS/TS dependency-graph tools that people actually find first when they search for code
visualization. Add rows for Madge, dependency-cruiser and CodeSee, and state the real axis:
those tools graph *module imports*; repo2graph graphs calls, imports, inheritance and git
co-change, and serves the result to an agent under a token ceiling.

### 3. Show HN with a live demo on a well-known repo

**Status: not started.** The `examples/` gallery already supplies the material — Django and
Flask-scale repos with pinned revisions and real build numbers, so the post can link to
reproducible output rather than a screenshot.

Lead with the graph image, not the architecture. Pair it with the one-command path:
`uvx repo2graph demo`.

### 4. MCP directory and awesome-list coverage

**Status: partial.** `server.json` and the `mcp-name:` marker in `README.md` exist, and the
project is listed on Glama (`glama.json`, and the Quality Score URL in `pyproject.toml`).
Not yet in the broader `awesome-mcp-servers` lists.

### 5. Extend the gallery from 5 repos to 10–15

**Status: partial** — 5 of the asked-for 10–15. `scripts/generate_examples.py` and
`examples/repositories.yaml` already parameterise this, so each addition is a config entry plus
a run, not new code. React and FastAPI are the obvious next two for recognisability.

### 6. Tutorials and roundups

**Status: not started.** Dev.to / Hashnode write-ups ("onboard to an unfamiliar repo in five
minutes"), and outreach to people writing "best MCP tools for coding agents" roundups.

## Priority order

1. Show HN with a visual demo on a famous repo — the gallery material already exists
2. "Sourcetrail alternative" page — highest-intent search traffic, zero current coverage
3. MCP directory and awesome-list listings — cheap, mechanical
4. `docs/comparison.md` rows for Madge / dependency-cruiser / CodeSee
5. A write-up of the token-budget benchmark that already exists

## Claims discipline

The benchmarks in `benchmarks/real/` do not support a blanket "better than grep" or "better than
Cursor" claim, and marketing copy must not make one. What they actually show:

- **Single-shot lexical questions: grep wins at every budget** — 35% vs 30% at 2k, 61% vs 37% at
  4k, 72% vs 48% at 8k. On purely lexical questions whose evidence sits in one file, text search
  is the better tool, and `README.md` says so in its own voice.
- **Cross-file structural questions: repo2graph wins** — 70% vs 20% at 4k, 80% vs 70% at 8k.
  grep cannot traverse a dependency edge or compute a reverse call closure.
- **Inside a multi-turn agent loop: repo2graph wins both sets** — 70% vs 10% structural and
  54% vs 20% general, in roughly half the turns, at 2.1×–7.6× the tokens.

So the defensible pitch is the agent loop and the structural question, not repo understanding in
general: *your coding agent answers cross-file questions in fewer turns, with citations and a
hard token ceiling.* The broader claim invites the first skeptical reader to run the project's
own general table against it.

Two further limits worth stating plainly rather than being caught on:

- Call resolution is **name-based, scoped by class, file and imports — not type-based**. Ambiguous
  names are marked `ambiguous` and priced `1/n` rather than guessed. `README.md`'s "What it can't
  do" section is the canonical wording; do not soften it.
- `graph.html` is a **static map**, not an interactive navigator. Against Sourcetrail's desktop UI
  that is a real deficit, and the Sourcetrail page should say so.

## Measurement

No telemetry, by design — local-first and no-network is part of the product promise, and adding
default telemetry to measure a marketing push would trade the thing being marketed. Proxy
signals instead: PyPI downloads, GitHub Action usage, stars and forks, MCP directory referrals,
issues and PRs from outside accounts, and third-party repositories referencing the Action or an
MCP config.
