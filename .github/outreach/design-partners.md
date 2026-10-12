# DRAFT — design-partner call (GitHub Discussions)

**Status: draft for the maintainer to post in the repository's Discussions. Not posted.**

## Title

Looking for design partners: point repo2graph at one real repository and tell us where it misled you

## Body

> repo2graph's numbers come from six repositories we did not write — Flask, requests, FastAPI, Hono,
> click and axios — and only Python, TypeScript and JavaScript among the 19 languages it parses.
> Every time someone asked it questions about a new repository, they found something: a parse gap
> that hid [Hono's whole public API](https://github.com/Srinivasan-78/repo2graph/blob/main/benchmarks/real/README.md#fixed-and-uncontroversially),
> an alias that cost a real answer. We want more of that, on code we have never seen.
>
> **What we are asking for — about 30 minutes:**
>
> 1. **One real repository** you know well. Public is best, because then the questions can go into
>    the benchmark; a private one is fine if you only report what happened.
> 2. **Three repository-understanding questions** you would actually ask a colleague about it — "what
>    calls X", "where is Y enforced", "what breaks if I change Z". Write them down *before* running
>    anything, with the file and lines you know answer each one.
> 3. **Run them:**
>
>        uvx repo2graph build . -o .r2g
>        uvx repo2graph rag "your question" -o .r2g
>
>    or through your agent over MCP (`docs/mcp.md`).
> 4. **Reply here** with, for each question: did the cited blocks contain the answer; where did it
>    help; and — most useful of all — where did it mislead you, with the citation it gave.
>
> **What you get:** your questions and answer locations added to the held-out benchmark (credited,
> if you want), a fix if it was a parser gap, and a direct line to the maintainer while we work on
> it.
>
> **What we will not do:** collect anything automatically. repo2graph has no telemetry and makes
> no network calls on the default path; the only thing we learn is what you write here. If you
> want to report on a private repository, `repo2graph bug-report` builds a bundle with no file
> content at any setting, and no file paths unless you pass `--include-paths`.
>
> Results so far, honestly: on cross-file structural questions it finds more of the answer than
> grep at every budget we measured; on single-file lexical questions grep still wins. Details:
> `benchmarks/real/README.md`. We would rather hear that it failed on your repository than not hear.

## Notes for the maintainer

- `repo2graph bug-report`'s privacy behaviour is described in `docs/cli.md`; re-read it before
  posting so the promise above matches the current flags.
- Each answer that comes back is a candidate row for `benchmarks/real/tasks_holdout*.json`; run
  `scripts/validate_tasks.py` on it before trusting it.
