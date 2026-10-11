# DRAFT — Show HN post

**Status: draft for the maintainer to post. Nothing here has been submitted anywhere.**

Every number below is copied from a committed artifact and the source is named beside it. If an
artifact is regenerated before posting, re-read the number from the file rather than this page.

## Before posting

1. **Make the image.** Lead with a picture of the graph, not the architecture. Build one of the
   gallery repositories at its pinned commit and screenshot `graph.html` — FastAPI or Gin are the
   best candidates, being recognisable, indexed whole with git history (so `CO_CHANGE` edges are
   present) and small enough to read on screen:

   ```bash
   git clone --filter=blob:none https://github.com/gin-gonic/gin /tmp/gin
   cd /tmp/gin && git checkout 0f09c3a9b4626d9fe9979cf4d0521d6ae32ab646
   uvx repo2graph build . -o .r2g --git-history 200
   # open .r2g/human/graph.html, click Context.Next, screenshot the focused view
   ```

   Do **not** use React for the image: Flow annotations defeat the JavaScript grammar there, and
   `examples/README.md` says so (241 of 386 parsed files report parse errors).
2. **Check the one-command path still works from PyPI:** `uvx repo2graph demo` (it exists in the
   CLI and is the first line of README's "Try it").
3. **Re-check the numbers** in the body against the files named next to them.
4. **Be there for the first two hours** to answer. The first skeptical comment will be "so is it
   better than grep?" — the honest answer is in the body; link `README.md#is-it-better-than-grep`.

## Title (HN limit is 80 characters)

Primary:

> Show HN: Repo2graph – cited, token-bounded answers about a codebase for coding agents

Alternative, if leading with the picture:

> Show HN: Repo2graph – a code graph your coding agent can query over MCP

## URL field

`https://github.com/Srinivasan-78/repo2graph`

## Body

> [image: graph.html for Gin at commit 0f09c3a, `Context.Next` focused]
>
> repo2graph parses a repository once with tree-sitter and records who calls whom, who imports
> what, which classes inherit from which, and which files keep changing together in git. An agent
> then asks it a question over MCP and gets back the source that answers it — every block headed
> `[cite: path:start-end]`, the whole reply under a token ceiling the server enforces (12k tokens
> max). No model, no API key, no database, no language server, and no network calls on the default
> path.
>
> Try it without your own repo:
>
>     uvx repo2graph demo
>
> or give Claude Code your repo:
>
>     claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp .
>
> The gallery has eleven well-known repositories indexed at pinned commits with the command to
> reproduce each — Django, FastAPI, Gin, ripgrep, Guava, Kubernetes, the Linux kernel and others:
> https://github.com/Srinivasan-78/repo2graph/tree/main/examples
>
> Is it better than grep? Measured, not argued, on 40 structural and 40 lexical questions about
> Flask, requests, FastAPI and Hono that it was not tuned against, every retriever held to the same
> token budget:
>
> - Cross-file structural questions ("what calls X", "trace a request from route to storage"): it
>   finds 29/60/79% of the evidence at 2k/4k/8k tokens; a grep-then-read loop finds 14/21/38%.
> - Single-file lexical questions: grep wins at every budget, 28/48/59% against 17/29/45%. We have
>   not closed that gap.
> - In a simulated agent loop (no model in the loop; a fixed search→read→answer policy), it solves
>   16/40 structural tasks against grep's 4/40, in 1.9 turns against 2.5 — and spends 4.4× grep's
>   tokens doing it. On the lexical set it is 8/40 against 7/40, in 1.4 turns against 3.0.
>
> Method, full tables and the defects we found in our own tool along the way:
> https://github.com/Srinivasan-78/repo2graph/blob/main/benchmarks/real/README.md
>
> What it can't do: call resolution is by name, scoped by class, file and imports — not by type. A
> name that could mean several definitions is marked ambiguous and priced 1/n rather than guessed.
> It cannot see dynamic dispatch or reflection. And `graph.html` is a static map you can pan,
> search and click through, not an interactive navigator like Sourcetrail was.
>
> It also runs as a GitHub Action, and the 3.x CLI, MCP tools and output schema follow semver. MIT
> licensed. I'd especially like to hear where it misled you on a real repository.

## Sources for every number

| Claim | Source |
|---|---|
| 29/60/79% vs 14/21/38%, structural | `benchmarks/real/results_holdout_final.json`, `structural_summary`, methods `repo2graph` and `ripgrep` |
| 17/29/45% vs 28/48/59%, lexical | same file, `summary` |
| 16/40 vs 4/40, 1.94 vs 2.5 turns, 2,590 vs 586 tokens (4.4×) | `benchmarks/real/agent_results_holdout_structural.json` |
| 8/40 vs 7/40, 1.38 vs 3.0 turns | `benchmarks/real/agent_results_holdout.json` |
| 12k-token ceiling over MCP | `MCP_MAX_BUDGET_TOKENS = 12000`, `repo2graph/mcp/guardrails.py` |
| Eleven gallery repositories, Gin commit | `examples/README.md`, `benchmarks/results.json` |
| React parse errors | `examples/react/README.md`, `examples/README.md` |

## Do not write

- "Better than grep" without the lexical half. The general claim invites the first reader to run
  the project's own lexical table against it.
- "Sourcetrail replacement" or "drop-in". See `docs/comparison.md#repo2graph-as-a-sourcetrail-alternative`.
- The published-set numbers (44/63/80% lexical) — that set was visible during tuning and is a
  regression set, not evidence. Quote the held-out set.
- "Type-aware", "understands your code", "all languages benchmarked" — two of nineteen are.
