# Launch post drafts

> ## DRAFTS — NOT FOR PUBLICATION
>
> Nothing in this file has been posted. Every post needs a human to read it,
> check the claims, and publish it under their own name. These are drafts to
> edit, not copy to paste.
>
> **Before any of it goes out:**
>
> - [ ] A maintainer has read the post and agrees with every claim in it.
> - [ ] Every number traces to `benchmarks/results.json` or an
>       `examples/*/stats.json`. No exceptions, no rounding up.
> - [ ] The post is published by a human account, not automation.
> - [ ] The community's self-promotion rules have been read *for that
>       community, that week* — they change, and the notes below will go stale.
> - [ ] The demo video, if linked, is recorded from a tagged release.
>
> **One further rule, worth stating plainly:** do not post the same text to
> several communities on the same day. Cross-posting identical copy reads as a
> campaign, gets filtered as one, and is the fastest way to burn the launch.

---

## Positioning constraints

Every draft below is written inside [POSITIONING.md §1](../../POSITIONING.md).
When editing, the five things no surface may say:

| Never | Because |
|---|---|
| "understands your codebase" | it parses it — the difference is the entire limitations page |
| "complete call graph" | resolution is name-based; an absent edge is not proof of an absent call |
| "replaces grep" | `query` runs BM25 as its first step |
| "AI-powered" | the default path makes zero network calls and loads no model |
| any unmeasured number | everything numeric traces to a committed artifact |

The temptation in a launch post is the second one. Resist it: the audience that
matters will test it within ten minutes, and being caught overclaiming on the
completeness of a call graph costs more than the post gains.

---

## 1. Hacker News — Show HN

**Rules that apply:** Show HN is for something people can try. No marketing
language, no "revolutionary", title is plain. The author must be present in the
thread to answer. Do not ask for upvotes anywhere, ever.

**Title** (80 char limit, this is 63):

```
Show HN: repo2graph – cited answers about a codebase, no LLM call
```

**Body:**

```
I kept hitting the same failure with coding agents: ask about an unfamiliar
repo, and the agent either greps and floods its context with whole files, or
answers from training data and invents a function that does not exist. Either
way you cannot tell which happened.

repo2graph parses a repo with tree-sitter into a graph — who calls whom, who
imports what, which class extends which — and answers questions with the
repo's own source, every block headed [cite: path:start-end]. It runs as a
stdio MCP server for Claude Code / Cursor, or as a CLI that prints a
markdown context with a hard token ceiling.

  uvx repo2graph demo

builds a bundled example repo and answers five questions against it, so you
can see the output shape without pointing it at anything.

What I think is the interesting part: every edge carries the line where the
relationship is written, plus a confidence and how it was extracted. So an
ambiguous call — a .get() that matched three methods named get — comes back
marked "AMBIGUOUS 0.5 of 3 candidates" instead of being presented as fact.
The tool tells you when it is guessing, which for this class of tool seemed
more useful than pretending it never does.

Honest limits, since they matter for whether it is useful to you: call
resolution is name-based, not type-based. 4.6%–21.3% of CALLS edges are
ambiguous across the five repos I benchmarked. Dynamic dispatch, reflection
and DI containers produce no edges at all — an absent edge is not proof of an
absent call. It indexes generated code the same as hand-written code. The
index is a snapshot; nothing watches the filesystem.

No network calls in the default path and no model loaded — it is tree-sitter
and BM25, with optional embeddings. There is an opt-in flag that sends the
assembled context to an LLM, and it prints the provider and hostname to
stderr before the first byte.

Python 3.10+, MIT. Build times on the repos I measured (benchmarks/results.json):
Django 5,629 files in 34s; VS Code 6,000 files in 71s; Linux kernel 3,660
files in 78s.

https://github.com/Srinivasan-78/repo2graph
```

**Anticipate in the thread:**

| Likely comment | Honest answer |
|---|---|
| "This is just ctags / LSP" | An LSP is more accurate and needs a working build per language; this needs neither and runs on 17 grammars uncritically. Different trade-off, and say which one loses. |
| "Name-based resolution is useless" | Partly fair. Give the real ambiguity numbers and point at `confidence`/`candidate_count` — the design answer is marking it, not hiding it. |
| "Why not embeddings?" | It supports them (`repo2graph embed`) and BM25 is the floor because vectors are optional. The graph expansion is the part embeddings do not give you. |
| "Does it send my code anywhere?" | Not on the default path. One opt-in flag does, and discloses before sending. Point at `docs/PRIVACY.md`. |

**Timing:** weekday, 08:00–11:00 ET. Be free to sit in the thread for the next
six hours — an unanswered Show HN thread does worse than no post.

---

## 2. Lobsters

**Rules that apply:** authored-by-you submissions must be tagged `show` and you
must disclose authorship. Lobsters is smaller and more critical than HN; the
technical detail should go up, the framing down.

**Title:**

```
repo2graph: tree-sitter code graph with per-edge evidence and confidence
```

**Tags:** `show`, `python`, `devtools`

**Comment (required, as author):**

```
Author here. The part I would most like criticism of is the edge metadata
model.

Every edge carries the file and line where the relationship is written, plus
a confidence defined as P(dst is the correct target | the relationship at
evidence exists) — deliberately not P(the relationship exists), since that
second thing is a syntactic fact from the parse tree and is not uncertain.
That split is what lets an ambiguous name split 1/n across candidates while
the call site stays certain.

Structural edges (a file being in a directory; two files co-changing in git
history) carry evidence: null rather than a fabricated line number, which
felt like the right call but does make the schema less uniform.

Where I expect pushback: name-based resolution. 4.6%–21.3% of CALLS edges are
ambiguous across five benchmark repos. I chose to mark them rather than drop
them or guess harder. I am not certain that is right for every consumer.
```

---

## 3. r/ClaudeAI (or another MCP-focused subreddit)

**Rules that apply:** check the sidebar for a self-promotion policy and a
"Projects" flair or weekly thread. Several MCP-adjacent subs restrict
self-promotion to a specific day or thread — if so, use it rather than working
around it.

**Title:**

```
Built an MCP server that gives Claude Code cited answers about your repo (and marks its own uncertain edges)
```

**Body:**

```
One command:

  claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /absolute/path/to/project

It parses the repo with tree-sitter into a call/import/inheritance graph and
exposes six read-only tools. The two that earn their keep:

- repo_search — "where is auth enforced?" returns cited source blocks under a
  hard token ceiling (6k default, 12k max), not a list of file paths.
- repo_neighbours — "what calls this?" walks CALLS/IMPORTS/INHERITS edges from
  a node id, and cites the call site, not just where the caller is defined.

Every numeric argument is clamped in the handler, so a model asking for
hops: 99 gets 4 instead of a 200k-character reply. All retrieval tools
exclude secret-shaped paths unconditionally — a human running the CLI can
choose to see a .env; a tool handing one to an agent is different.

The bit I would want to know as a user: edges it is unsure of say so.

  - CALLS out: `ResultCache.get` (cache.py:128) [sym:...]
      -- at status.py:124, AMBIGUOUS 0.5 of 3 candidates

That is a .get() on a dict that matched three methods named get. It does not
know which runs.

Worth knowing before you install: dynamic dispatch, reflection and DI
containers produce no edges, so "nothing calls this" from the graph is not
proof. I put a CLAUDE.md snippet in the integration guide that tells the agent
exactly that, because otherwise it will over-trust the tool.

Setup guide: docs/integrations/claude-code.md
Repo: https://github.com/Srinivasan-78/repo2graph
```

---

## 4. dev.to / Hashnode long-form

**Working title:** "An absent edge is not proof of an absent call: building a
code graph that admits what it does not know"

**Outline** — write this one as an article about the *problem*, with the tool as
the worked example. A launch post disguised as an article gets read as a launch
post.

1. The failure: an agent confidently reporting "nothing calls this" about a
   function reached through a plugin registry. Real, reproducible, and the
   reader has seen it.
2. Why name-based resolution is the honest ceiling without a build per
   language — and what an LSP buys instead, and costs.
3. The design decision: mark ambiguity rather than hide it. What
   `confidence = P(dst correct | relationship exists)` means and why the split
   matters.
4. Why `evidence: null` on structural edges is right — a fabricated line number
   is worse than an absent one.
5. The measurement that surprised me: profiling `--incremental` showed the parse
   cache already removes 96% of parsing, and **74% of an incremental rebuild is
   secret scanning re-running over bytes it already cleared**. The intuitive
   optimisation (incremental resolution) targets 7%. Link the RFC.
6. What is still wrong. Real, not performative.

Item 5 is the strongest thing in the outline and the reason to write the article
at all: it is a specific, counter-intuitive, measured finding that stands on its
own whether or not the reader ever installs anything.

---

## 5. Short social (X / LinkedIn / Mastodon)

Written to be skippable. One idea, one command, one honest caveat.

**Option A — the differentiator:**

```
Most code-graph tools present every edge as fact.

repo2graph marks the ones it is guessing at:

  CALLS out: ResultCache.get -- at status.py:124,
    AMBIGUOUS 0.5 of 3 candidates

That's a .get() on a dict that matched three methods named get.
It doesn't know which runs, and says so.

  uvx repo2graph demo
```

**Option B — the measurement:**

```
Profiled our incremental indexing expecting parsing to be the bottleneck.

Parse cache already removes 96% of it.
74% of an incremental rebuild turned out to be the secret scanner,
re-scanning bytes it had already cleared.

The obvious optimisation targeted 7% of the problem.

Measure first. Writeup: <link>
```

B travels further with engineers and mentions the product almost incidentally,
which is why it works. Prefer it for a first post.

**Never in a social post:** a number without its source, a comparison to a named
competitor, or a screenshot of output from a private repository.

---

## Sequencing

Do not launch everywhere at once. Each step's job is to find the errors before a
larger audience does.

| Order | Where | Why here | Gate before the next step |
|---|---|---|---|
| 1 | dev.to article (item 4) | a durable link the other posts can point at, and the least costly place to be wrong | someone outside the project has read it |
| 2 | r/ClaudeAI or equivalent | the audience most likely to install it; smallest blast radius for a setup bug | at least one stranger got it working from the guide alone |
| 3 | Lobsters | technical scrutiny of the design before HN | the design criticism is answered, not deflected |
| 4 | Show HN | the widest reach; only worth spending once | someone is free to sit in the thread all day |
| 5 | Social | amplify what already landed | — |

Step 2's gate is the one people skip. If a stranger cannot get the MCP server
running from `docs/integrations/claude-code.md` without asking you a question,
the guide is wrong and Show HN will surface that at the worst possible moment.
